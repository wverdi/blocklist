"""Gerador de listas de bloqueio consolidadas.

Le listas publicas de terceiros, extrai apenas o dominio de cada linha e
reescreve tudo como ||dominio^. Nenhuma linha crua de terceiro chega ao
arquivo publicado: regras de excecao (@@), com modificadores ($) ou com
expressao regular sao descartadas, nao convertidas.
"""

import argparse
import datetime
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

TRAVAS_PADRAO = {"variacao_maxima": 0.20, "minimo_dominios": 1}


class ErroDeConfig(Exception):
    """Configuracao ausente, malformada ou insegura."""


def carregar_config(caminho):
    """Le listas/<nome>.toml, valida e preenche os padroes.

    Falha alto e cedo: uma config errada aqui vira bloqueio errado na rede,
    entao e' melhor a rodada nem comecar.
    """
    caminho = Path(caminho)
    try:
        with caminho.open("rb") as arquivo:
            config = tomllib.load(arquivo)
    except FileNotFoundError as erro:
        raise ErroDeConfig(f"config nao encontrada: {caminho}") from erro
    except tomllib.TOMLDecodeError as erro:
        raise ErroDeConfig(f"TOML invalido em {caminho}: {erro}") from erro

    for campo in ("titulo", "saida"):
        if not config.get(campo):
            raise ErroDeConfig(f"{caminho.name}: falta o campo obrigatorio '{campo}'")

    config["nome"] = caminho.stem
    config.setdefault("descricao", "")
    config.setdefault("homepage", "")
    config.setdefault("expires", "1 day")
    config.setdefault("permitir_regra_de_tld", False)
    config["travas"] = {**TRAVAS_PADRAO, **config.get("travas", {})}
    config.setdefault("fontes", [])

    vistos = set()
    for fonte in config["fontes"]:
        for campo in ("nome", "url"):
            if not fonte.get(campo):
                raise ErroDeConfig(
                    f"{caminho.name}: fonte sem o campo obrigatorio '{campo}'"
                )
        if fonte["nome"] in vistos:
            raise ErroDeConfig(
                f"{caminho.name}: fonte '{fonte['nome']}' declarada duas vezes"
            )
        vistos.add(fonte["nome"])
        if not fonte["url"].startswith("https://"):
            raise ErroDeConfig(
                f"{caminho.name}: a fonte '{fonte['nome']}' nao usa https - "
                "uma lista baixada em claro pode ser adulterada no caminho"
            )
        fonte.setdefault("publicar", True)
        fonte.setdefault("licenca", "")

    return config

# Um rotulo de dominio: letras, digitos, hifen ou sublinhado, sem comecar
# nem terminar com hifen. Aceita punycode (xn--...) como qualquer rotulo.
_ROTULO = r"[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?"
_DOMINIO = re.compile(rf"^{_ROTULO}(?:\.{_ROTULO})+$")
_TLD = re.compile(rf"^{_ROTULO}$")

# Linha de arquivo hosts: um IP seguido do dominio.
_IP = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$|^[0-9a-f:]+:[0-9a-f:]*$")

# Nomes que aparecem no cabecalho de todo arquivo hosts e nao sao bloqueios.
_HOSTS_LOCAIS = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback", "ip6-localnet", "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters", "ip6-allhosts", "0.0.0.0"}


def extrair_dominio(linha, permitir_tld=False):
    """Devolve o dominio bloqueado por `linha`, ou None.

    None significa "nao sei reescrever isto com seguranca como ||dominio^".
    E o caso de comentarios, excecoes @@, regras com modificadores ou regex,
    curingas, IPs e lixo. Descartar e' sempre preferivel a adivinhar.

    `permitir_tld` libera regras de um rotulo so (||xxx^), que bloqueiam um
    dominio de topo inteiro. Exige opt-in porque um ||ad^ numa fonte derrubaria
    todo o TLD de Andorra.
    """
    texto = linha.strip()
    if not texto:
        return None

    # Comentarios e cabecalho de lista Adblock.
    if texto[0] in "#!" or texto.startswith("["):
        return None

    # Regra cosmetica (##, #?#, #@#) - nao se aplica a filtragem DNS.
    if "##" in texto or "#@#" in texto or "#?#" in texto:
        return None

    # Expressao regular.
    if texto.startswith("/"):
        return None

    # Excecao: libera em vez de bloquear. Converter inverteria o sentido.
    if texto.startswith("@@"):
        return None

    # Modificadores mudam o efeito da regra ($dnsrewrite, $important, ...).
    if "$" in texto:
        return None

    # Comentario no fim da linha ("0.0.0.0 sexo.com # adulto").
    texto = texto.split("#")[0].split("!")[0].strip()
    if not texto:
        return None

    # Formato hosts: "<ip> <dominio>". Qualquer outra linha com espaco e' lixo.
    campos = texto.split()
    if len(campos) > 1:
        if not _IP.match(campos[0].lower()):
            return None
        texto = campos[-1]

    texto = texto.lower()

    # Ancora de dominio do Adblock. Guardamos se estava presente: so uma regra
    # com || expressa a intencao de bloquear um TLD inteiro.
    ancorado = texto.startswith("||")
    if ancorado:
        texto = texto[2:]
    texto = texto.rstrip("^").rstrip("|")

    if not texto or "*" in texto:
        return None
    if texto in _HOSTS_LOCAIS:
        return None
    if _IP.match(texto):
        return None

    if _DOMINIO.match(texto):
        return texto
    if permitir_tld and ancorado and _TLD.match(texto):
        return texto
    return None


def _ancestrais(dominio):
    """Todos os sufixos de dominio que, como regra, cobririam `dominio`.

    a.b.com -> b.com, com
    """
    partes = dominio.split(".")
    return [".".join(partes[i:]) for i in range(1, len(partes))]


def coletar(fontes, baixar, permitir_tld=False):
    """Baixa e interpreta cada fonte configurada.

    Devolve (resultados, falhas): um dict {nome: Lista} com o que deu certo e
    uma lista [(nome, motivo)] com o que nao deu. Uma fonte fora do ar nao
    derruba as outras - quem chamou decide se ainda pode publicar.

    Fonte que responde vazia (ou so com comentarios) conta como falha: e' o
    modo de falha mais perigoso, porque um 200 com corpo truncado passaria
    despercebido e esvaziaria a lista.
    """
    resultados, falhas = {}, []

    for fonte in fontes:
        nome = fonte["nome"]
        try:
            texto = baixar(fonte["url"])
        except Exception as erro:
            falhas.append((nome, f"{type(erro).__name__}: {erro}"))
            continue

        lista = parse_lista(texto, permitir_tld=permitir_tld)
        if not lista.dominios:
            falhas.append((nome, "resposta vazia ou sem nenhum dominio valido"))
            continue

        resultados[nome] = lista

    return resultados, falhas


class Consolidado(NamedTuple):
    """Resultado de juntar fontes, lista propria e excecoes."""

    dominios: set
    encobertas: list  # excecoes que nao funcionam por causa de um dominio pai
    compactados: int  # linhas poupadas por ja terem ancestral na lista
    propria_redundante: list  # suas regras que as fontes ja cobrem


def _coberto_por(conjunto, dominio):
    return dominio in conjunto or any(
        pai in conjunto for pai in _ancestrais(dominio)
    )


def consolidar(fontes, propria, excecoes):
    """Junta fontes publicadas + lista propria, tira excecoes e compacta.

    `fontes` e' um dict {nome: set}, ja filtrado para conter apenas as fontes
    marcadas com publicar=true. Uma lista sem fonte externa nenhuma funciona
    igual, so com a lista propria.
    """
    das_fontes = set().union(*fontes.values()) if fontes else set()

    uniao = das_fontes | set(propria)
    uniao, encobertas = aplicar_excecoes(uniao, excecoes)

    final = compactar(uniao)
    compactados = len(uniao) - len(final)

    redundantes = sorted(d for d in propria if _coberto_por(das_fontes, d))

    return Consolidado(final, encobertas, compactados, redundantes)


def ler_com_limite(stream, limite):
    """Le no maximo `limite` bytes; estoura se o conteudo for maior.

    Uma fonte que cresceu demais, ou um servidor que responde com um stream
    sem fim, travaria o job ate o timeout do Actions.
    """
    dados = stream.read(limite + 1)
    if len(dados) > limite:
        raise ValueError(f"resposta acima do limite de {limite:,} bytes")
    return dados


def baixar_http(url, tempo_limite=60, tamanho_maximo=64 * 1024 * 1024):
    """Baixa uma fonte por HTTPS, com limite de tempo e de tamanho."""
    import urllib.request

    pedido = urllib.request.Request(
        url, headers={"User-Agent": "wverdi/blocklist gerador"}
    )
    with urllib.request.urlopen(pedido, timeout=tempo_limite) as resposta:
        return ler_com_limite(resposta, tamanho_maximo).decode("utf-8", "replace")


def executar(config, baixar, propria, excecoes, anterior, data):
    """Roda uma lista inteira e decide se pode publicar.

    Devolve (texto, rodada). `texto` e' None quando a rodada nao pode
    publicar - nesse caso o chamador nao toca no arquivo existente, que
    continua sendo a ultima versao valida.
    """
    permitir_tld = config["permitir_regra_de_tld"]
    resultados, falhas = coletar(config["fontes"], baixar, permitir_tld)

    publicar = {f["nome"] for f in config["fontes"] if f.get("publicar", True)}
    das_publicadas = {
        nome: lista.dominios
        for nome, lista in resultados.items()
        if nome in publicar
    }

    cons = consolidar(das_publicadas, propria, excecoes)

    problemas = [
        f"fonte publicada '{nome}' falhou: {motivo}"
        for nome, motivo in falhas
        if nome in publicar
    ]
    problemas += verificar_travas(
        len(cons.dominios), anterior.get("total"), config["travas"]
    )

    rodada = Rodada(
        config=config,
        data=data,
        resultados=resultados,
        falhas=falhas,
        consolidado=cons,
        anterior=anterior,
        problemas=problemas,
        publicado=not problemas,
    )

    if problemas:
        return None, rodada

    fontes_creditadas = [f for f in config["fontes"] if f["nome"] in das_publicadas]
    texto = renderizar(cons.dominios, config, fontes_creditadas, data)
    return texto, rodada


class Rodada(NamedTuple):
    """Tudo o que aconteceu numa execucao, para virar relatorio."""

    config: dict
    data: str
    resultados: dict  # nome da fonte -> Lista
    falhas: list  # [(nome, motivo)]
    consolidado: Consolidado
    anterior: dict  # estado da rodada anterior
    problemas: list  # travas que impediram a publicacao
    publicado: bool


def linha_resumo(rodada):
    """Uma linha resumindo a rodada; vira a mensagem de commit."""
    nome = rodada.config["nome"]
    total = len(rodada.consolidado.dominios)
    anterior = rodada.anterior.get("total")

    texto = f"{nome}: {total:,} regras"
    if anterior:
        texto += f" ({total - anterior:+,})"

    publicar = {f["nome"] for f in rodada.config["fontes"] if f.get("publicar", True)}
    antes = rodada.anterior.get("fontes", {})
    detalhes = []
    for fonte, lista in sorted(rodada.resultados.items()):
        if fonte in publicar and fonte in antes:
            delta = len(lista.dominios) - antes[fonte]
            if delta:
                detalhes.append(f"{fonte} {delta:+,}")
    if detalhes:
        texto += " [" + ", ".join(detalhes) + "]"

    return texto


def montar_relatorio(rodada):
    """Escreve o relatorio da rodada em Markdown."""
    config, cons = rodada.config, rodada.consolidado
    publicar = {f["nome"] for f in config["fontes"] if f.get("publicar", True)}
    total = len(cons.dominios)
    anterior = rodada.anterior.get("total")

    linhas = [
        f"# {config['titulo']}",
        "",
        f"Rodada de {rodada.data}.",
        "",
    ]

    if rodada.publicado:
        delta = f" ({total - anterior:+,} desde a rodada anterior)" if anterior else ""
        linhas.append(f"**Publicado:** {total:,} regras{delta}.")
    else:
        linhas.append(f"**NAO PUBLICADO.** O arquivo anterior foi mantido.")
        linhas.append("")
        linhas.append("Motivo:")
        linhas += [f"- {p}" for p in rodada.problemas]

    linhas += ["", "## Fontes", ""]
    if rodada.resultados:
        linhas += [
            "| fonte | dominios | descartadas | papel |",
            "|---|---:|---:|---|",
        ]
        for nome, lista in sorted(rodada.resultados.items()):
            papel = "publicada" if nome in publicar else "observacao"
            linhas.append(
                f"| {nome} | {len(lista.dominios):,} | "
                f"{lista.descartadas:,} | {papel} |"
            )
    else:
        linhas.append("Nenhuma fonte externa: lista mantida so por voce.")

    if rodada.falhas:
        linhas += ["", "## Fontes que falharam", ""]
        linhas += [f"- **{nome}**: {motivo}" for nome, motivo in rodada.falhas]

    tlds = sorted(
        {t for nome, l in rodada.resultados.items() if nome in publicar for t in l.tlds}
    )
    if tlds:
        linhas += [
            "",
            "## Regras de TLD inteiro",
            "",
            "Cada uma bloqueia um dominio de topo completo. Confira se todas "
            "sao da categoria desta lista.",
            "",
        ]
        linhas += [f"- `||{t}^`" for t in tlds]

    if cons.compactados:
        linhas += [
            "",
            "## Compactacao",
            "",
            f"{cons.compactados:,} regras removidas por ja terem um dominio "
            "pai na lista (||pai^ cobre todos os subdominios).",
        ]

    if cons.encobertas:
        linhas += [
            "",
            "## Excecoes sem efeito",
            "",
            "Estas excecoes **nao funcionam**: um dominio pai continua "
            "bloqueado e o arquivo publicado nao usa regras `@@`. Para "
            "liberar de verdade, remova tambem a regra do dominio pai.",
            "",
        ]
        linhas += [f"- `{d}`" for d in cons.encobertas]

    if cons.propria_redundante:
        linhas += [
            "",
            "## Suas regras que as fontes ja cobrem",
            "",
            "Continuam valendo; se quiser enxugar proprias/, pode aposentar.",
            "",
        ]
        linhas += [f"- `{d}`" for d in cons.propria_redundante]

    return "\n".join(linhas) + "\n"


def renderizar(dominios, config, fontes, data):
    """Monta o arquivo final no formato AdGuard.

    Todo dominio e' reescrito como ||dominio^: nenhuma linha crua de terceiro
    sobrevive ate aqui. O cabecalho segue a convencao de metadados que o
    AdGuard Home le (Title, Description, Homepage, Expires).
    """
    regras = [f"||{d}^" for d in sorted(dominios)]

    cabecalho = [
        f"! Title: {config.get('titulo', 'Lista')}",
        f"! Description: {config.get('descricao', '')}",
        f"! Homepage: {config.get('homepage', '')}",
        f"! Last modified: {data}",
        f"! Expires: {config.get('expires', '1 day')}",
    ]
    if config.get("licenca"):
        cabecalho.append(f"! License: {config['licenca']}")
    cabecalho += [
        "!",
        f"! Regras: {len(regras)}",
        "! Gerado automaticamente - nao edite este arquivo a mao.",
        "! Bloqueios manuais ficam em proprias/, excecoes em proprias/*-excecoes.txt.",
        "!",
        "! Sintaxe AdBlock: ||dominio^ bloqueia o dominio e TODOS os subdominios.",
        "!",
    ]

    if fontes:
        cabecalho.append("! ---- Fontes consolidadas ----")
        for fonte in fontes:
            licenca = fonte.get("licenca") or "licenca nao declarada"
            cabecalho.append(f"!   {fonte['nome']} ({licenca}) - {fonte['url']}")
        cabecalho.append("!")

    if config.get("aviso"):
        # Toda linha recebe "!": uma linha solta seria lida como regra.
        for linha in config["aviso"].strip().splitlines():
            cabecalho.append(f"! {linha}".rstrip())
        cabecalho.append("!")

    return "\n".join(cabecalho + regras) + "\n"


def _regras(texto):
    """So as linhas de regra, sem cabecalho nem espacos."""
    if texto is None:
        return None
    return [l.strip() for l in texto.splitlines() if l.strip().startswith("||")]


def mesmo_conteudo(a, b):
    """Compara duas versoes ignorando o cabecalho.

    A data no cabecalho muda todo dia; sem esta comparacao o workflow
    commitaria diariamente mesmo sem nenhuma regra ter mudado.
    """
    if a is None or b is None:
        return False
    return _regras(a) == _regras(b)


def verificar_travas(quantidade, anterior, travas):
    """Diz se o resultado desta rodada pode ser publicado.

    Devolve a lista de problemas encontrados; vazia significa "pode publicar".
    Qualquer problema faz a rodada abortar mantendo a versao anterior - nunca
    substituimos uma lista boa por uma suspeita.
    """
    minimo = travas.get("minimo_dominios", 1)
    variacao_maxima = travas.get("variacao_maxima")
    problemas = []

    if quantidade < max(minimo, 1):
        problemas.append(
            f"abaixo do minimo: {quantidade:,} dominios (minimo {minimo:,})"
        )

    if variacao_maxima is not None and anterior:
        variacao = abs(quantidade - anterior) / anterior
        if variacao > variacao_maxima:
            sentido = "cresceu" if quantidade > anterior else "encolheu"
            problemas.append(
                f"variacao excessiva: {sentido} {variacao:.1%} "
                f"({anterior:,} -> {quantidade:,}, limite {variacao_maxima:.0%})"
            )

    return problemas


class Lista(NamedTuple):
    """Resultado da leitura de uma fonte."""

    dominios: set
    ignoradas: int  # comentarios e linhas em branco
    descartadas: int  # linhas de conteudo que nao soubemos converter
    tlds: list  # regras de TLD inteiro que entraram, para auditoria


def _e_ruido(linha):
    """Comentario, linha em branco ou cabecalho: ruido normal, nao descarte."""
    texto = linha.strip()
    return not texto or texto[0] in "#!" or texto.startswith("[")


def parse_lista(texto, permitir_tld=False):
    """Le uma fonte inteira e devolve os dominios mais as estatisticas.

    Distingue "ruido" (comentario, linha vazia, entrada de hosts local) de
    "descarte" (linha de conteudo que nao soubemos converter com seguranca).
    So a segunda contagem merece atencao no relatorio.
    """
    dominios, tlds = set(), set()
    ignoradas = descartadas = 0

    for linha in texto.splitlines():
        dominio = extrair_dominio(linha, permitir_tld=permitir_tld)
        if dominio is not None:
            dominios.add(dominio)
            if "." not in dominio:
                tlds.add(dominio)
        elif _e_ruido(linha) or _e_hosts_local(linha):
            ignoradas += 1
        else:
            descartadas += 1

    return Lista(dominios, ignoradas, descartadas, sorted(tlds))


def _e_hosts_local(linha):
    """"127.0.0.1 localhost" e afins: cabecalho de todo arquivo hosts."""
    campos = linha.split("#")[0].strip().split()
    return len(campos) > 1 and campos[-1].lower() in _HOSTS_LOCAIS


def aplicar_excecoes(dominios, excecoes):
    """Tira as excecoes do conjunto e aponta as que ficaram sem efeito.

    Devolve (dominios_restantes, excecoes_encobertas).

    Remover a linha exata nao basta quando um dominio pai continua na lista:
    ||exemplo.com^ segue bloqueando img.exemplo.com mesmo depois de a linha
    dele sair. Como o arquivo publicado nao usa regras @@, essas excecoes
    simplesmente nao funcionam - e precisam aparecer no relatorio.
    """
    restantes = set(dominios) - set(excecoes)
    encobertas = sorted(
        e for e in excecoes if any(pai in restantes for pai in _ancestrais(e))
    )
    return restantes, encobertas


def compactar(dominios):
    """Remove dominios ja cobertos por um ancestral presente no conjunto.

    ||exemplo.com^ bloqueia exemplo.com e todos os subdominios, entao
    publicar tambem ||www.exemplo.com^ e' desperdicio puro.
    """
    conjunto = set(dominios)
    return {
        d
        for d in conjunto
        if not any(pai in conjunto for pai in _ancestrais(d))
    }


# --------------------------------------------------------------------------
# Camada de arquivos e linha de comando
# --------------------------------------------------------------------------


def _ler_dominios(caminho, permitir_tld):
    """Le um arquivo de proprias/ (mesma sintaxe AdGuard que voce ja usa)."""
    if not caminho or not Path(caminho).exists():
        return set()
    texto = Path(caminho).read_text(encoding="utf-8")
    return parse_lista(texto, permitir_tld=permitir_tld).dominios


def _ler_texto(caminho):
    caminho = Path(caminho)
    return caminho.read_text(encoding="utf-8") if caminho.exists() else None


def _ler_estado(caminho):
    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def processar(caminho_config, raiz, data, baixar=baixar_http):
    """Roda uma lista de ponta a ponta.

    Devolve (ok, resumo): `ok` False significa que a rodada nao publicou e o
    arquivo anterior foi mantido; `resumo` e' None quando nada mudou.
    """
    config = carregar_config(caminho_config)
    nome = config["nome"]
    permitir_tld = config["permitir_regra_de_tld"]

    propria = _ler_dominios(raiz / config["propria"], permitir_tld) if config.get("propria") else set()
    excecoes = _ler_dominios(raiz / config["excecoes"], permitir_tld) if config.get("excecoes") else set()

    caminho_estado = raiz / "estado" / f"{nome}.json"
    anterior = _ler_estado(caminho_estado)

    texto, rodada = executar(config, baixar, propria, excecoes, anterior, data)

    caminho_saida = raiz / config["saida"]
    caminho_relatorio = raiz / "dist" / f"relatorio-{nome}.md"
    caminho_relatorio.parent.mkdir(parents=True, exist_ok=True)
    caminho_relatorio.write_text(
        montar_relatorio(rodada), encoding="utf-8", newline="\n"
    )

    if texto is None:
        print(f"[{nome}] NAO PUBLICADO - arquivo anterior mantido")
        for problema in rodada.problemas:
            print(f"   - {problema}")
        return False, None

    atual = _ler_texto(caminho_saida)
    if mesmo_conteudo(atual, texto):
        print(f"[{nome}] sem mudanca ({len(rodada.consolidado.dominios):,} regras)")
        return True, None

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    caminho_saida.write_text(texto, encoding="utf-8", newline="\n")

    caminho_estado.parent.mkdir(parents=True, exist_ok=True)
    caminho_estado.write_text(
        json.dumps(
            {
                "data": data,
                "total": len(rodada.consolidado.dominios),
                "fontes": {
                    n: len(l.dominios) for n, l in sorted(rodada.resultados.items())
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    resumo = linha_resumo(rodada)
    # O resumo ja comeca com "<nome>: " porque vira mensagem de commit de
    # todas as listas juntas; no console o nome ja esta entre colchetes.
    print(f"[{nome}] atualizado: {resumo.removeprefix(nome + ': ')}")
    return True, resumo


def main(argv=None):
    analisador = argparse.ArgumentParser(
        description="Gera as listas de bloqueio consolidadas."
    )
    analisador.add_argument(
        "listas",
        nargs="*",
        help="arquivos de config a processar (padrao: todos em listas/)",
    )
    analisador.add_argument(
        "--raiz",
        default=Path(__file__).resolve().parent.parent,
        type=Path,
        help="raiz do repositorio",
    )
    args = analisador.parse_args(argv)

    raiz = args.raiz.resolve()
    configs = [Path(c) for c in args.listas] or sorted((raiz / "listas").glob("*.toml"))
    if not configs:
        print("nenhuma config encontrada em listas/", file=sys.stderr)
        return 1

    data = datetime.date.today().isoformat()
    falhou = False
    resumos = []
    for caminho in configs:
        try:
            ok, resumo = processar(caminho, raiz, data)
            falhou = falhou or not ok
            if resumo:
                resumos.append(resumo)
        except ErroDeConfig as erro:
            print(f"ERRO DE CONFIG: {erro}", file=sys.stderr)
            falhou = True

    # Vira a mensagem de commit do workflow. Vazio = nada mudou = nao commita.
    caminho_resumo = raiz / "dist" / "resumo.txt"
    caminho_resumo.parent.mkdir(parents=True, exist_ok=True)
    caminho_resumo.write_text(
        "; ".join(resumos) + "\n" if resumos else "",
        encoding="utf-8",
        newline="\n",
    )

    return 1 if falhou else 0


if __name__ == "__main__":
    sys.exit(main())
