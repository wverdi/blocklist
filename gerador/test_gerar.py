"""Testes do gerador de listas."""

import io
import tempfile
import unittest
from pathlib import Path

from gerar import (
    ErroDeConfig,
    Rodada,
    processar,
    aplicar_excecoes,
    carregar_config,
    coletar,
    executar,
    ler_com_limite,
    linha_resumo,
    montar_relatorio,
    compactar,
    consolidar,
    extrair_dominio,
    mesmo_conteudo,
    parse_lista,
    renderizar,
    verificar_travas,
)


class TestExtrairDominio(unittest.TestCase):
    """extrair_dominio(linha, permitir_tld=False) -> str | None

    Devolve o dominio de uma linha de lista publica, ou None se a linha
    nao for um bloqueio de dominio simples que possamos reescrever com
    seguranca como ||dominio^.
    """

    # --- formatos que aceitamos ---

    def test_extrai_de_regra_adguard(self):
        self.assertEqual(extrair_dominio("||fatalfans.com^"), "fatalfans.com")

    def test_extrai_de_regra_adguard_sem_separador_final(self):
        self.assertEqual(extrair_dominio("||fatalfans.com"), "fatalfans.com")

    def test_extrai_de_linha_hosts_com_zero(self):
        self.assertEqual(extrair_dominio("0.0.0.0 pornhub.com"), "pornhub.com")

    def test_extrai_de_linha_hosts_com_loopback(self):
        self.assertEqual(extrair_dominio("127.0.0.1 pornhub.com"), "pornhub.com")

    def test_extrai_de_dominio_puro(self):
        self.assertEqual(extrair_dominio("pornhub.com"), "pornhub.com")

    def test_normaliza_maiusculas(self):
        self.assertEqual(extrair_dominio("||PornHub.COM^"), "pornhub.com")

    def test_ignora_espaco_em_volta(self):
        self.assertEqual(extrair_dominio("   ||pornhub.com^   "), "pornhub.com")

    def test_aceita_subdominio_profundo(self):
        self.assertEqual(
            extrair_dominio("0.0.0.0 fck-cl40.tnaflix.com"), "fck-cl40.tnaflix.com"
        )

    def test_aceita_punycode_no_tld(self):
        """Bug do estudo de 17/09: o regex [a-z]{2,} descartava estes."""
        self.assertEqual(
            extrair_dominio("||pornoizle.xn--6frz82g^"), "pornoizle.xn--6frz82g"
        )

    def test_aceita_punycode_no_rotulo(self):
        self.assertEqual(
            extrair_dominio("||xn--80a0apf2b.xn--80asehdb^"),
            "xn--80a0apf2b.xn--80asehdb",
        )

    def test_remove_comentario_no_fim_da_linha_hosts(self):
        self.assertEqual(extrair_dominio("0.0.0.0 sexo.com # adulto"), "sexo.com")

    # --- linhas que descartamos ---

    def test_descarta_linha_vazia(self):
        self.assertIsNone(extrair_dominio(""))
        self.assertIsNone(extrair_dominio("    "))

    def test_descarta_comentario_hash(self):
        self.assertIsNone(extrair_dominio("# Title: alguma lista"))

    def test_descarta_comentario_exclamacao(self):
        self.assertIsNone(extrair_dominio("! Title: alguma lista"))

    def test_descarta_cabecalho_adblock(self):
        self.assertIsNone(extrair_dominio("[Adblock Plus]"))

    def test_descarta_excecao_allowlist(self):
        """@@ libera em vez de bloquear: virar ||dominio^ inverteria o sentido."""
        self.assertIsNone(extrair_dominio("@@||exemplo.com^"))

    def test_descarta_regra_com_modificador(self):
        """$important, $dnsrewrite etc. mudam o efeito da regra."""
        self.assertIsNone(extrair_dominio("||exemplo.com^$important"))

    def test_descarta_dnsrewrite(self):
        self.assertIsNone(extrair_dominio("||exemplo.com^$dnsrewrite=1.2.3.4"))

    def test_descarta_regex(self):
        self.assertIsNone(extrair_dominio("/banner\\d+/"))

    def test_descarta_regra_cosmetica(self):
        self.assertIsNone(extrair_dominio("exemplo.com##.anuncio"))

    def test_descarta_entrada_de_hosts_local(self):
        self.assertIsNone(extrair_dominio("127.0.0.1 localhost"))
        self.assertIsNone(extrair_dominio("255.255.255.255 broadcasthost"))

    def test_descarta_endereco_ip(self):
        """Lista de dominios nao publica IP."""
        self.assertIsNone(extrair_dominio("1.2.3.4"))

    def test_descarta_curinga(self):
        self.assertIsNone(extrair_dominio("||*.exemplo.com^"))

    def test_descarta_dominio_invalido(self):
        self.assertIsNone(extrair_dominio("nao e um dominio"))
        self.assertIsNone(extrair_dominio("exemplo..com"))
        self.assertIsNone(extrair_dominio("-exemplo.com"))

    # --- regra de TLD inteiro: so quando explicitamente permitido ---

    def test_descarta_regra_de_tld_por_padrao(self):
        """||xxx^ bloqueia o TLD .xxx inteiro: exige opt-in da lista."""
        self.assertIsNone(extrair_dominio("||xxx^"))

    def test_aceita_regra_de_tld_quando_permitido(self):
        self.assertEqual(extrair_dominio("||xxx^", permitir_tld=True), "xxx")

    def test_dominio_puro_de_um_rotulo_nao_vira_tld(self):
        """Sem || nao ha intencao explicita de bloquear o TLD."""
        self.assertIsNone(extrair_dominio("xxx", permitir_tld=True))


class TestCompactar(unittest.TestCase):
    """compactar(dominios) -> set

    Remove todo dominio ja coberto por um ancestral presente no conjunto,
    porque ||exemplo.com^ bloqueia sozinho todos os subdominios. No
    StevenBlack isso corta 38% das linhas.
    """

    def test_remove_subdominio_coberto_pelo_pai(self):
        self.assertEqual(
            compactar({"exemplo.com", "www.exemplo.com"}), {"exemplo.com"}
        )

    def test_remove_subdominio_profundo_coberto_por_ancestral(self):
        self.assertEqual(
            compactar({"exemplo.com", "a.b.c.exemplo.com"}), {"exemplo.com"}
        )

    def test_mantem_irmaos_sem_pai_na_lista(self):
        self.assertEqual(
            compactar({"a.exemplo.com", "b.exemplo.com"}),
            {"a.exemplo.com", "b.exemplo.com"},
        )

    def test_nao_confunde_prefixo_colado_com_subdominio(self):
        """naoexemplo.com nao e' subdominio de exemplo.com."""
        self.assertEqual(
            compactar({"exemplo.com", "naoexemplo.com"}),
            {"exemplo.com", "naoexemplo.com"},
        )

    def test_nao_remove_por_causa_de_sufixo_publico(self):
        """com.br nao esta na lista, entao nada e' coberto por ele."""
        self.assertEqual(
            compactar({"a.com.br", "b.com.br"}), {"a.com.br", "b.com.br"}
        )

    def test_regra_de_tld_cobre_dominios_daquele_tld(self):
        self.assertEqual(compactar({"xxx", "site.xxx"}), {"xxx"})

    def test_conjunto_vazio(self):
        self.assertEqual(compactar(set()), set())

    def test_nao_altera_conjunto_ja_compacto(self):
        entrada = {"a.com", "b.org", "c.net"}
        self.assertEqual(compactar(entrada), entrada)

    def test_nao_modifica_o_conjunto_recebido(self):
        entrada = {"exemplo.com", "www.exemplo.com"}
        compactar(entrada)
        self.assertEqual(entrada, {"exemplo.com", "www.exemplo.com"})

    def test_cadeia_de_tres_niveis_colapsa_no_topo(self):
        self.assertEqual(
            compactar({"exemplo.com", "a.exemplo.com", "b.a.exemplo.com"}),
            {"exemplo.com"},
        )


class TestAplicarExcecoes(unittest.TestCase):
    """aplicar_excecoes(dominios, excecoes) -> (set, list)

    Remove a linha exata de cada excecao, conforme decidido: o arquivo
    publicado so contem regras de bloqueio, sem @@.

    A lista devolvida sao as excecoes que continuam sem efeito porque um
    dominio pai segue bloqueado. Elas vao para o relatorio - e a unica
    forma de saber que a excecao nao funcionou.
    """

    def test_remove_a_linha_exata(self):
        resultado, _ = aplicar_excecoes({"a.com", "b.com"}, {"a.com"})
        self.assertEqual(resultado, {"b.com"})

    def test_excecao_de_dominio_ausente_nao_faz_nada(self):
        resultado, encobertas = aplicar_excecoes({"a.com"}, {"z.com"})
        self.assertEqual(resultado, {"a.com"})
        self.assertEqual(encobertas, [])

    def test_avisa_quando_o_pai_continua_bloqueando(self):
        resultado, encobertas = aplicar_excecoes(
            {"exemplo.com"}, {"img.exemplo.com"}
        )
        self.assertEqual(resultado, {"exemplo.com"})
        self.assertEqual(encobertas, ["img.exemplo.com"])

    def test_remove_a_linha_e_ainda_assim_avisa_do_pai(self):
        """O caso perigoso: parece que funcionou, mas nao funcionou."""
        resultado, encobertas = aplicar_excecoes(
            {"exemplo.com", "img.exemplo.com"}, {"img.exemplo.com"}
        )
        self.assertEqual(resultado, {"exemplo.com"})
        self.assertEqual(encobertas, ["img.exemplo.com"])

    def test_nao_avisa_quando_a_excecao_realmente_funcionou(self):
        resultado, encobertas = aplicar_excecoes(
            {"img.exemplo.com"}, {"img.exemplo.com"}
        )
        self.assertEqual(resultado, set())
        self.assertEqual(encobertas, [])

    def test_avisa_quando_regra_de_tld_encobre(self):
        resultado, encobertas = aplicar_excecoes({"xxx"}, {"loja.xxx"})
        self.assertEqual(resultado, {"xxx"})
        self.assertEqual(encobertas, ["loja.xxx"])

    def test_encobertas_vem_ordenadas(self):
        _, encobertas = aplicar_excecoes(
            {"exemplo.com"}, {"z.exemplo.com", "a.exemplo.com"}
        )
        self.assertEqual(encobertas, ["a.exemplo.com", "z.exemplo.com"])

    def test_sem_excecoes_devolve_o_conjunto_intacto(self):
        resultado, encobertas = aplicar_excecoes({"a.com", "b.com"}, set())
        self.assertEqual(resultado, {"a.com", "b.com"})
        self.assertEqual(encobertas, [])

    def test_nao_modifica_o_conjunto_recebido(self):
        entrada = {"a.com", "b.com"}
        aplicar_excecoes(entrada, {"a.com"})
        self.assertEqual(entrada, {"a.com", "b.com"})


class TestParseLista(unittest.TestCase):
    """parse_lista(texto, permitir_tld=False) -> Lista

    Le uma fonte inteira e devolve os dominios mais as estatisticas que
    o relatorio precisa para voce auditar o que foi jogado fora.
    """

    HOSTS = """# Title: exemplo
127.0.0.1 localhost

0.0.0.0 pornhub.com
0.0.0.0 www.pornhub.com
0.0.0.0 xvideos.com
"""

    ADBLOCK = """[Adblock Plus]
! Title: exemplo
||pornhub.com^
||xvideos.com^
@@||liberado.com^
||comregra.com^$important
/regex\\d+/
"""

    def test_extrai_os_dominios_de_um_arquivo_hosts(self):
        lista = parse_lista(self.HOSTS)
        self.assertEqual(
            lista.dominios, {"pornhub.com", "www.pornhub.com", "xvideos.com"}
        )

    def test_conta_linhas_ignoradas_sem_inflar_descartadas(self):
        """Comentario e linha em branco nao sao 'descarte', sao ruido normal."""
        lista = parse_lista(self.HOSTS)
        self.assertEqual(lista.ignoradas, 3)  # titulo, linha vazia, localhost
        self.assertEqual(lista.descartadas, 0)

    def test_conta_as_linhas_que_nao_soubemos_converter(self):
        lista = parse_lista(self.ADBLOCK)
        self.assertEqual(lista.dominios, {"pornhub.com", "xvideos.com"})
        self.assertEqual(lista.descartadas, 3)  # @@, $important, regex

    def test_deduplica_dominio_repetido(self):
        lista = parse_lista("0.0.0.0 a.com\n0.0.0.0 a.com\n||a.com^\n")
        self.assertEqual(lista.dominios, {"a.com"})

    def test_nao_coleta_regra_de_tld_por_padrao(self):
        lista = parse_lista("||xxx^\n||a.com^\n")
        self.assertEqual(lista.dominios, {"a.com"})
        self.assertEqual(lista.tlds, [])

    def test_coleta_regras_de_tld_para_auditoria(self):
        """Toda regra de TLD inteiro tem que aparecer no relatorio."""
        lista = parse_lista("||xxx^\n||porn^\n||a.com^\n", permitir_tld=True)
        self.assertEqual(lista.dominios, {"xxx", "porn", "a.com"})
        self.assertEqual(lista.tlds, ["porn", "xxx"])

    def test_texto_vazio(self):
        lista = parse_lista("")
        self.assertEqual(lista.dominios, set())
        self.assertEqual(lista.descartadas, 0)

    def test_aceita_quebra_de_linha_do_windows(self):
        lista = parse_lista("0.0.0.0 a.com\r\n0.0.0.0 b.com\r\n")
        self.assertEqual(lista.dominios, {"a.com", "b.com"})


class TestVerificarTravas(unittest.TestCase):
    """verificar_travas(quantidade, anterior, travas) -> list[str]

    Lista vazia = pode publicar. Qualquer problema aborta a publicacao e
    mantem a versao anterior: nunca existe janela com lista vazia.
    """

    TRAVAS = {"minimo_dominios": 100, "variacao_maxima": 0.20}

    def test_sem_problemas_quando_esta_tudo_normal(self):
        self.assertEqual(verificar_travas(1000, 1000, self.TRAVAS), [])

    def test_variacao_pequena_passa(self):
        self.assertEqual(verificar_travas(1100, 1000, self.TRAVAS), [])
        self.assertEqual(verificar_travas(900, 1000, self.TRAVAS), [])

    def test_crescimento_acima_do_limite_bloqueia(self):
        problemas = verificar_travas(1300, 1000, self.TRAVAS)
        self.assertEqual(len(problemas), 1)
        self.assertIn("30.0%", problemas[0])

    def test_encolhimento_acima_do_limite_bloqueia(self):
        """O caso que mais importa: fonte truncada ou adulterada."""
        problemas = verificar_travas(500, 1000, self.TRAVAS)
        self.assertEqual(len(problemas), 1)
        self.assertIn("50.0%", problemas[0])

    def test_lista_vazia_sempre_bloqueia(self):
        self.assertTrue(verificar_travas(0, 1000, self.TRAVAS))

    def test_lista_vazia_bloqueia_mesmo_sem_rodada_anterior(self):
        self.assertTrue(verificar_travas(0, None, self.TRAVAS))

    def test_abaixo_do_minimo_bloqueia(self):
        problemas = verificar_travas(50, None, self.TRAVAS)
        self.assertEqual(len(problemas), 1)
        self.assertIn("minimo", problemas[0].lower())

    def test_primeira_rodada_nao_checa_variacao(self):
        """Sem rodada anterior nao ha com o que comparar."""
        self.assertEqual(verificar_travas(1000, None, self.TRAVAS), [])

    def test_acumula_varios_problemas(self):
        problemas = verificar_travas(10, 1000, self.TRAVAS)
        self.assertEqual(len(problemas), 2)

    def test_travas_ausentes_usam_padrao_seguro(self):
        """Config sem travas nao pode significar 'sem protecao'."""
        self.assertTrue(verificar_travas(0, None, {}))


class TestRenderizar(unittest.TestCase):
    """renderizar(dominios, config, fontes, data) -> str

    Produz o arquivo final no formato AdGuard, com o cabecalho de metadados
    que o wverdi/blocklist ja usa.
    """

    CONFIG = {
        "titulo": "Conteudo Adulto",
        "descricao": "Lista consolidada.",
        "homepage": "https://github.com/wverdi/blocklist",
        "expires": "1 day",
    }
    FONTES = [
        {"nome": "hagezi-nsfw", "url": "https://ex.com/a.txt", "licenca": "GPL-3.0"},
        {"nome": "stevenblack", "url": "https://ex.com/b.txt", "licenca": "MIT"},
    ]

    def render(self, dominios):
        return renderizar(dominios, self.CONFIG, self.FONTES, "2026-09-18")

    def test_traz_o_titulo_no_cabecalho(self):
        self.assertIn("! Title: Conteudo Adulto", self.render({"a.com"}))

    def test_traz_expires_para_o_adguard_saber_o_intervalo(self):
        self.assertIn("! Expires: 1 day", self.render({"a.com"}))

    def test_traz_a_data_informada(self):
        self.assertIn("! Last modified: 2026-09-18", self.render({"a.com"}))

    def test_traz_a_contagem_de_regras(self):
        self.assertIn("! Regras: 2", self.render({"a.com", "b.com"}))

    def test_escreve_cada_dominio_como_regra_adguard(self):
        saida = self.render({"pornhub.com"})
        self.assertIn("||pornhub.com^", saida)

    def test_ordena_os_dominios(self):
        saida = self.render({"c.com", "a.com", "b.com"})
        regras = [l for l in saida.splitlines() if l.startswith("||")]
        self.assertEqual(regras, ["||a.com^", "||b.com^", "||c.com^"])

    def test_credita_cada_fonte_com_a_licenca(self):
        saida = self.render({"a.com"})
        self.assertIn("hagezi-nsfw", saida)
        self.assertIn("GPL-3.0", saida)
        self.assertIn("stevenblack", saida)
        self.assertIn("MIT", saida)

    def test_todo_cabecalho_e_comentario(self):
        """Nenhuma linha de metadado pode ser lida como regra pelo AdGuard."""
        for linha in self.render({"a.com"}).splitlines():
            if linha and not linha.startswith("||"):
                self.assertTrue(
                    linha.startswith("!"), f"linha nao comentada: {linha!r}"
                )

    def test_termina_com_quebra_de_linha(self):
        self.assertTrue(self.render({"a.com"}).endswith("\n"))

    def test_lista_vazia_ainda_produz_cabecalho_valido(self):
        saida = self.render(set())
        self.assertIn("! Title:", saida)
        self.assertEqual([l for l in saida.splitlines() if l.startswith("||")], [])

    def test_traz_a_licenca_declarada_da_lista(self):
        config = {**self.CONFIG, "licenca": "GPL-3.0"}
        saida = renderizar({"a.com"}, config, self.FONTES, "2026-09-18")
        self.assertIn("! License: GPL-3.0", saida)

    def test_traz_o_aviso_de_isencao(self):
        """Quem assina a URL no AdGuard tem que ver o aviso sem abrir o repo."""
        config = {**self.CONFIG, "aviso": "Use por sua conta e risco."}
        saida = renderizar({"a.com"}, config, self.FONTES, "2026-09-18")
        self.assertIn("Use por sua conta e risco.", saida)

    def test_aviso_de_varias_linhas_vira_varios_comentarios(self):
        config = {**self.CONFIG, "aviso": "Primeira linha.\nSegunda linha."}
        saida = renderizar({"a.com"}, config, self.FONTES, "2026-09-18")
        self.assertIn("! Primeira linha.", saida)
        self.assertIn("! Segunda linha.", saida)

    def test_aviso_nunca_quebra_o_formato(self):
        """Uma linha de aviso sem '!' seria lida como regra pelo AdGuard."""
        config = {**self.CONFIG, "aviso": "linha um\nlinha dois\n\nlinha tres"}
        saida = renderizar({"a.com"}, config, self.FONTES, "2026-09-18")
        for linha in saida.splitlines():
            if linha and not linha.startswith("||"):
                self.assertTrue(linha.startswith("!"), f"solta: {linha!r}")

    def test_sem_aviso_nem_licenca_o_cabecalho_segue_valido(self):
        saida = self.render({"a.com"})
        self.assertIn("! Title:", saida)
        self.assertNotIn("! License:", saida)


class TestMesmoConteudo(unittest.TestCase):
    """mesmo_conteudo(a, b) -> bool

    Compara so as regras, ignorando o cabecalho. Sem isso, a data no
    cabecalho geraria um commit por dia mesmo sem nada ter mudado.
    """

    def test_ignora_diferenca_apenas_no_cabecalho(self):
        a = "! Last modified: 2026-09-18\n||a.com^\n"
        b = "! Last modified: 2026-09-19\n||a.com^\n"
        self.assertTrue(mesmo_conteudo(a, b))

    def test_detecta_regra_adicionada(self):
        a = "! x\n||a.com^\n"
        b = "! x\n||a.com^\n||b.com^\n"
        self.assertFalse(mesmo_conteudo(a, b))

    def test_detecta_regra_removida(self):
        a = "! x\n||a.com^\n||b.com^\n"
        b = "! x\n||a.com^\n"
        self.assertFalse(mesmo_conteudo(a, b))

    def test_ignora_diferenca_de_quebra_de_linha(self):
        self.assertTrue(mesmo_conteudo("||a.com^\n", "||a.com^\r\n"))

    def test_arquivo_inexistente_e_diferente_de_qualquer_coisa(self):
        self.assertFalse(mesmo_conteudo(None, "||a.com^\n"))


class TestConsolidar(unittest.TestCase):
    """consolidar(fontes, propria, excecoes) -> Consolidado

    fontes: dict {nome: set de dominios} - apenas as marcadas publicar=true.
    Junta tudo, tira as excecoes, compacta e apura o que o relatorio precisa.
    """

    def test_une_as_fontes_com_a_lista_propria(self):
        r = consolidar({"f1": {"a.com"}, "f2": {"b.com"}}, {"c.com"}, set())
        self.assertEqual(r.dominios, {"a.com", "b.com", "c.com"})

    def test_deduplica_entre_fontes(self):
        r = consolidar({"f1": {"a.com"}, "f2": {"a.com"}}, set(), set())
        self.assertEqual(r.dominios, {"a.com"})

    def test_aplica_as_excecoes(self):
        r = consolidar({"f1": {"a.com", "b.com"}}, set(), {"a.com"})
        self.assertEqual(r.dominios, {"b.com"})

    def test_relata_excecao_encoberta_pelo_pai(self):
        r = consolidar({"f1": {"exemplo.com"}}, set(), {"img.exemplo.com"})
        self.assertEqual(r.encobertas, ["img.exemplo.com"])

    def test_compacta_e_conta_quantas_linhas_isso_poupou(self):
        r = consolidar({"f1": {"exemplo.com", "www.exemplo.com"}}, set(), set())
        self.assertEqual(r.dominios, {"exemplo.com"})
        self.assertEqual(r.compactados, 1)

    def test_aponta_regra_propria_ja_coberta_por_fonte(self):
        """O caso real do fatalmodel.com: candidata a aposentar."""
        r = consolidar({"f1": {"fatalmodel.com"}}, {"fatalmodel.com"}, set())
        self.assertEqual(r.propria_redundante, ["fatalmodel.com"])

    def test_aponta_regra_propria_coberta_por_dominio_pai_da_fonte(self):
        r = consolidar({"f1": {"exemplo.com"}}, {"img.exemplo.com"}, set())
        self.assertEqual(r.propria_redundante, ["img.exemplo.com"])

    def test_nao_aponta_regra_propria_exclusiva(self):
        """fatalfans.com nao esta em nenhuma fonte: tem que continuar."""
        r = consolidar({"f1": {"outro.com"}}, {"fatalfans.com"}, set())
        self.assertEqual(r.propria_redundante, [])
        self.assertIn("fatalfans.com", r.dominios)

    def test_funciona_sem_nenhuma_fonte_externa(self):
        """Lista 100% manual, sem nenhuma fonte de terceiro."""
        r = consolidar({}, {"a.com", "b.com"}, set())
        self.assertEqual(r.dominios, {"a.com", "b.com"})
        self.assertEqual(r.propria_redundante, [])

    def test_excecao_vence_a_lista_propria(self):
        """Se voce se contradiz, a excecao manda - e o relatorio avisa."""
        r = consolidar({}, {"a.com"}, {"a.com"})
        self.assertEqual(r.dominios, set())


class TestColetar(unittest.TestCase):
    """coletar(fontes, baixar, permitir_tld=False) -> (resultados, falhas)

    `baixar` e' injetado para o teste nao tocar a rede. Uma fonte que falha
    nao derruba as outras: ela vira uma entrada em `falhas`, e quem decide o
    que fazer com isso e' quem chamou.
    """

    FONTES = [
        {"nome": "f1", "url": "https://ex.com/1.txt"},
        {"nome": "f2", "url": "https://ex.com/2.txt"},
    ]

    def test_baixa_e_interpreta_cada_fonte(self):
        def baixar(url):
            return {
                "https://ex.com/1.txt": "||a.com^\n",
                "https://ex.com/2.txt": "0.0.0.0 b.com\n",
            }[url]

        resultados, falhas = coletar(self.FONTES, baixar)
        self.assertEqual(falhas, [])
        self.assertEqual(resultados["f1"].dominios, {"a.com"})
        self.assertEqual(resultados["f2"].dominios, {"b.com"})

    def test_fonte_que_da_erro_nao_derruba_as_outras(self):
        def baixar(url):
            if url.endswith("1.txt"):
                raise OSError("connection reset")
            return "||b.com^\n"

        resultados, falhas = coletar(self.FONTES, baixar)
        self.assertEqual(set(resultados), {"f2"})
        self.assertEqual(len(falhas), 1)
        self.assertEqual(falhas[0][0], "f1")
        self.assertIn("connection reset", falhas[0][1])

    def test_fonte_vazia_conta_como_falha(self):
        """Resposta 200 com corpo vazio e' o modo de falha mais perigoso."""
        resultados, falhas = coletar([self.FONTES[0]], lambda url: "")
        self.assertEqual(resultados, {})
        self.assertEqual(len(falhas), 1)
        self.assertIn("vazia", falhas[0][1].lower())

    def test_fonte_so_com_comentarios_conta_como_falha(self):
        resultados, falhas = coletar(
            [self.FONTES[0]], lambda url: "! Title: x\n! nada aqui\n"
        )
        self.assertEqual(resultados, {})
        self.assertEqual(len(falhas), 1)

    def test_repassa_permitir_tld(self):
        resultados, _ = coletar(
            [self.FONTES[0]], lambda url: "||xxx^\n||a.com^\n", permitir_tld=True
        )
        self.assertEqual(resultados["f1"].dominios, {"xxx", "a.com"})

    def test_sem_fontes_devolve_vazio(self):
        self.assertEqual(coletar([], lambda url: ""), ({}, []))


class TestCarregarConfig(unittest.TestCase):
    """carregar_config(caminho) -> dict

    Le um listas/<nome>.toml, valida e preenche os padroes. Config invalida
    tem que falhar alto e cedo, com mensagem que diz o que corrigir.
    """

    def escrever(self, conteudo):
        pasta = Path(tempfile.mkdtemp())
        caminho = pasta / "teste.toml"
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    MINIMA = """
titulo = "Teste"
saida = "dist/teste.txt"
"""

    def test_le_os_campos_declarados(self):
        config = carregar_config(self.escrever(self.MINIMA))
        self.assertEqual(config["titulo"], "Teste")
        self.assertEqual(config["saida"], "dist/teste.txt")

    def test_usa_o_nome_do_arquivo_como_identificador(self):
        config = carregar_config(self.escrever(self.MINIMA))
        self.assertEqual(config["nome"], "teste")

    def test_expires_tem_padrao(self):
        config = carregar_config(self.escrever(self.MINIMA))
        self.assertEqual(config["expires"], "1 day")

    def test_regra_de_tld_vem_desligada_por_padrao(self):
        """Opt-in explicito: e' a opcao que pode derrubar um TLD inteiro."""
        config = carregar_config(self.escrever(self.MINIMA))
        self.assertFalse(config["permitir_regra_de_tld"])

    def test_travas_tem_padrao_mesmo_sem_secao(self):
        config = carregar_config(self.escrever(self.MINIMA))
        self.assertIn("variacao_maxima", config["travas"])
        self.assertIn("minimo_dominios", config["travas"])

    def test_lista_sem_fontes_e_valida(self):
        """Uma categoria mantida so por curadoria propria e' config valida."""
        config = carregar_config(self.escrever(self.MINIMA))
        self.assertEqual(config["fontes"], [])

    def test_fonte_publica_por_padrao(self):
        config = carregar_config(
            self.escrever(
                self.MINIMA
                + """
[[fontes]]
nome = "f1"
url = "https://ex.com/a.txt"
"""
            )
        )
        self.assertTrue(config["fontes"][0]["publicar"])

    def test_fonte_em_modo_observacao(self):
        config = carregar_config(
            self.escrever(
                self.MINIMA
                + """
[[fontes]]
nome = "f1"
url = "https://ex.com/a.txt"
publicar = false
"""
            )
        )
        self.assertFalse(config["fontes"][0]["publicar"])

    def test_exige_titulo(self):
        with self.assertRaises(ErroDeConfig) as ctx:
            carregar_config(self.escrever('saida = "dist/x.txt"\n'))
        self.assertIn("titulo", str(ctx.exception))

    def test_exige_saida(self):
        with self.assertRaises(ErroDeConfig) as ctx:
            carregar_config(self.escrever('titulo = "x"\n'))
        self.assertIn("saida", str(ctx.exception))

    def test_exige_nome_e_url_em_cada_fonte(self):
        with self.assertRaises(ErroDeConfig) as ctx:
            carregar_config(
                self.escrever(self.MINIMA + '\n[[fontes]]\nnome = "f1"\n')
            )
        self.assertIn("url", str(ctx.exception))

    def test_rejeita_fonte_com_nome_repetido(self):
        """Nome repetido sobrescreveria silenciosamente o resultado da outra."""
        with self.assertRaises(ErroDeConfig) as ctx:
            carregar_config(
                self.escrever(
                    self.MINIMA
                    + """
[[fontes]]
nome = "f1"
url = "https://ex.com/a.txt"

[[fontes]]
nome = "f1"
url = "https://ex.com/b.txt"
"""
                )
            )
        self.assertIn("f1", str(ctx.exception))

    def test_rejeita_url_que_nao_seja_https(self):
        """Lista baixada por http em claro e' adulteravel no caminho."""
        with self.assertRaises(ErroDeConfig) as ctx:
            carregar_config(
                self.escrever(
                    self.MINIMA
                    + '\n[[fontes]]\nnome = "f1"\nurl = "http://ex.com/a.txt"\n'
                )
            )
        self.assertIn("https", str(ctx.exception).lower())

    def test_erro_claro_em_toml_invalido(self):
        with self.assertRaises(ErroDeConfig):
            carregar_config(self.escrever("isto ][ nao e toml"))

    def test_erro_claro_em_arquivo_inexistente(self):
        with self.assertRaises(ErroDeConfig):
            carregar_config(Path(tempfile.mkdtemp()) / "nao-existe.toml")


class TestMontarRelatorio(unittest.TestCase):
    """montar_relatorio(rodada) -> str

    O relatorio e' a unica forma de voce auditar o que o gerador fez sem ler
    um arquivo de 100 mil linhas. Tudo que e' decisao automatica tem que
    aparecer aqui.
    """

    def rodada(self, **ajustes):
        base = dict(
            config={
                "nome": "adulto",
                "titulo": "Conteudo Adulto",
                "fontes": [
                    {"nome": "hagezi", "url": "u1", "publicar": True},
                    {"nome": "oisd", "url": "u2", "publicar": False},
                ],
            },
            data="2026-09-18",
            resultados={
                "hagezi": parse_lista("||a.com^\n||b.com^\n"),
                "oisd": parse_lista("||a.com^\n||z.com^\n"),
            },
            falhas=[],
            consolidado=consolidar({"hagezi": {"a.com", "b.com"}}, set(), set()),
            anterior={},
            problemas=[],
            publicado=True,
        )
        base.update(ajustes)
        return Rodada(**base)

    def test_informa_a_data_da_rodada(self):
        self.assertIn("2026-09-18", montar_relatorio(self.rodada()))

    def test_informa_o_total_de_regras_publicadas(self):
        self.assertIn("2", montar_relatorio(self.rodada()))

    def test_mostra_cada_fonte_com_sua_contagem(self):
        texto = montar_relatorio(self.rodada())
        self.assertIn("hagezi", texto)
        self.assertIn("oisd", texto)

    def test_marca_a_fonte_que_esta_so_em_observacao(self):
        """Tem que ficar obvio que o oisd nao entrou no arquivo."""
        texto = montar_relatorio(self.rodada())
        self.assertIn("observa", texto.lower())

    def test_mostra_a_variacao_em_relacao_a_rodada_anterior(self):
        texto = montar_relatorio(self.rodada(anterior={"total": 1}))
        self.assertIn("+1", texto)

    def test_destaca_excecoes_sem_efeito(self):
        consolidado = consolidar({"f": {"exemplo.com"}}, set(), {"img.exemplo.com"})
        texto = montar_relatorio(self.rodada(consolidado=consolidado))
        self.assertIn("img.exemplo.com", texto)

    def test_aponta_regras_proprias_ja_cobertas(self):
        consolidado = consolidar({"f": {"fatalmodel.com"}}, {"fatalmodel.com"}, set())
        texto = montar_relatorio(self.rodada(consolidado=consolidado))
        self.assertIn("fatalmodel.com", texto)

    def test_lista_as_regras_de_tld_que_entraram(self):
        resultados = {"hagezi": parse_lista("||xxx^\n", permitir_tld=True)}
        texto = montar_relatorio(self.rodada(resultados=resultados))
        self.assertIn("xxx", texto)

    def test_registra_as_fontes_que_falharam(self):
        texto = montar_relatorio(self.rodada(falhas=[("oisd", "HTTP 503")]))
        self.assertIn("503", texto)

    def test_deixa_claro_quando_nao_publicou_e_por_que(self):
        texto = montar_relatorio(
            self.rodada(publicado=False, problemas=["variacao excessiva: 50%"])
        )
        self.assertIn("variacao excessiva", texto)
        self.assertIn("nao publicad", texto.lower())

    def test_informa_quantas_linhas_a_compactacao_poupou(self):
        consolidado = consolidar(
            {"f": {"exemplo.com", "www.exemplo.com"}}, set(), set()
        )
        texto = montar_relatorio(self.rodada(consolidado=consolidado))
        self.assertIn("compact", texto.lower())


class TestLerComLimite(unittest.TestCase):
    """ler_com_limite(stream, limite) -> bytes

    Protege contra uma fonte que cresceu demais ou que responde com um
    stream sem fim. Sem isso, o job do Actions trava ate o timeout.
    """

    def test_le_conteudo_dentro_do_limite(self):
        self.assertEqual(ler_com_limite(io.BytesIO(b"abc"), 10), b"abc")

    def test_le_exatamente_no_limite(self):
        self.assertEqual(ler_com_limite(io.BytesIO(b"abcde"), 5), b"abcde")

    def test_recusa_conteudo_acima_do_limite(self):
        with self.assertRaises(ValueError) as ctx:
            ler_com_limite(io.BytesIO(b"abcdef"), 5)
        self.assertIn("limite", str(ctx.exception).lower())

    def test_stream_vazio(self):
        self.assertEqual(ler_com_limite(io.BytesIO(b""), 10), b"")


class TestExecutar(unittest.TestCase):
    """executar(config, baixar, propria, excecoes, anterior, data) -> (texto, rodada)

    O orquestrador. `texto` e' None quando a rodada nao pode publicar - e o
    chamador entao nao toca no arquivo existente.
    """

    CONFIG = {
        "nome": "adulto",
        "titulo": "Conteudo Adulto",
        "descricao": "",
        "homepage": "",
        "expires": "1 day",
        "permitir_regra_de_tld": False,
        "saida": "dist/adulto.txt",
        "travas": {"variacao_maxima": 0.20, "minimo_dominios": 1},
        "fontes": [{"nome": "f1", "url": "https://ex.com/a.txt", "publicar": True}],
    }

    def executar(self, baixar, **ajustes):
        args = dict(
            config=self.CONFIG,
            baixar=baixar,
            propria=set(),
            excecoes=set(),
            anterior={},
            data="2026-09-18",
        )
        args.update(ajustes)
        return executar(**args)

    def test_publica_quando_esta_tudo_bem(self):
        texto, rodada = self.executar(lambda url: "||a.com^\n||b.com^\n")
        self.assertTrue(rodada.publicado)
        self.assertIn("||a.com^", texto)
        self.assertIn("||b.com^", texto)

    def test_inclui_a_lista_propria_no_resultado(self):
        texto, _ = self.executar(
            lambda url: "||a.com^\n", propria={"fatalfans.com"}
        )
        self.assertIn("||fatalfans.com^", texto)

    def test_nao_publica_quando_a_fonte_publicada_falha(self):
        """Sem a fonte, o arquivo existente e' melhor do que um incompleto."""
        def baixar(url):
            raise OSError("timeout")

        texto, rodada = self.executar(baixar)
        self.assertIsNone(texto)
        self.assertFalse(rodada.publicado)
        self.assertTrue(rodada.problemas)

    def test_publica_mesmo_se_uma_fonte_de_observacao_falhar(self):
        config = {
            **self.CONFIG,
            "fontes": [
                {"nome": "f1", "url": "https://ex.com/a.txt", "publicar": True},
                {"nome": "obs", "url": "https://ex.com/b.txt", "publicar": False},
            ],
        }

        def baixar(url):
            if url.endswith("b.txt"):
                raise OSError("503")
            return "||a.com^\n"

        texto, rodada = self.executar(baixar, config=config)
        self.assertTrue(rodada.publicado)
        self.assertIn("||a.com^", texto)

    def test_fonte_em_observacao_nao_entra_no_arquivo(self):
        config = {
            **self.CONFIG,
            "fontes": [
                {"nome": "f1", "url": "https://ex.com/a.txt", "publicar": True},
                {"nome": "obs", "url": "https://ex.com/b.txt", "publicar": False},
            ],
        }
        baixar = lambda url: (
            "||a.com^\n" if url.endswith("a.txt") else "||naodeveentrar.com^\n"
        )
        texto, _ = self.executar(baixar, config=config)
        self.assertIn("||a.com^", texto)
        self.assertNotIn("naodeveentrar.com", texto)

    def test_nao_publica_quando_a_variacao_estoura(self):
        texto, rodada = self.executar(
            lambda url: "||a.com^\n", anterior={"total": 100}
        )
        self.assertIsNone(texto)
        self.assertFalse(rodada.publicado)

    def test_aplica_as_excecoes(self):
        texto, _ = self.executar(
            lambda url: "||a.com^\n||b.com^\n", excecoes={"a.com"}
        )
        self.assertNotIn("||a.com^", texto)
        self.assertIn("||b.com^", texto)

    def test_lista_sem_fontes_publica_so_a_propria(self):
        """Categoria mantida so por curadoria propria, sem fonte externa."""
        config = {**self.CONFIG, "fontes": []}
        texto, rodada = self.executar(
            lambda url: "", config=config, propria={"soqueminha.com"}
        )
        self.assertTrue(rodada.publicado)
        self.assertIn("||soqueminha.com^", texto)

    def test_lista_sem_fontes_e_sem_propria_nao_publica(self):
        """Publicar arquivo vazio apagaria o bloqueio que ja existe."""
        config = {**self.CONFIG, "fontes": []}
        texto, rodada = self.executar(lambda url: "", config=config)
        self.assertIsNone(texto)


class TestLinhaResumo(unittest.TestCase):
    """linha_resumo(rodada) -> str

    Vira a mensagem de commit. Sem isso o historico do repositorio fica so
    com "update" e voce nao consegue auditar nada olhando o git log.
    """

    def rodada(self, **ajustes):
        base = dict(
            config={
                "nome": "adulto",
                "titulo": "Conteudo Adulto",
                "fontes": [
                    {"nome": "hagezi", "url": "u", "publicar": True},
                    {"nome": "oisd", "url": "u", "publicar": False},
                ],
            },
            data="2026-09-18",
            resultados={
                "hagezi": parse_lista("||a.com^\n||b.com^\n"),
                "oisd": parse_lista("||z.com^\n"),
            },
            falhas=[],
            consolidado=consolidar({"hagezi": {"a.com", "b.com"}}, set(), set()),
            anterior={},
            problemas=[],
            publicado=True,
        )
        base.update(ajustes)
        return Rodada(**base)

    def test_traz_o_nome_da_lista_e_o_total(self):
        self.assertIn("adulto", linha_resumo(self.rodada()))
        self.assertIn("2", linha_resumo(self.rodada()))

    def test_e_uma_linha_so(self):
        self.assertNotIn("\n", linha_resumo(self.rodada()))

    def test_mostra_o_crescimento_do_total(self):
        self.assertIn("+1", linha_resumo(self.rodada(anterior={"total": 1})))

    def test_mostra_a_queda_do_total(self):
        self.assertIn("-1", linha_resumo(self.rodada(anterior={"total": 3})))

    def test_detalha_a_variacao_por_fonte_publicada(self):
        resumo = linha_resumo(
            self.rodada(anterior={"total": 1, "fontes": {"hagezi": 1}})
        )
        self.assertIn("hagezi", resumo)

    def test_nao_detalha_fonte_em_observacao(self):
        """O oisd nao entra no arquivo: nao polui a mensagem de commit."""
        resumo = linha_resumo(
            self.rodada(anterior={"total": 1, "fontes": {"hagezi": 1, "oisd": 99}})
        )
        self.assertNotIn("oisd", resumo)

    def test_primeira_rodada_nao_inventa_variacao(self):
        resumo = linha_resumo(self.rodada())
        self.assertNotIn("+", resumo)


class TestProcessarEscreveArquivos(unittest.TestCase):
    """processar(config, raiz, data, baixar) — a camada de arquivos.

    O gerador roda no Windows (na mao) e no Linux (no Actions). Se a saida
    dependesse da plataforma, a primeira rodada automatica reescreveria o
    arquivo inteiro so pela quebra de linha, produzindo um diff de 100 mil
    linhas sem nenhuma regra ter mudado.
    """

    def montar_repo(self, propria="||minha.com^\n"):
        raiz = Path(tempfile.mkdtemp())
        (raiz / "listas").mkdir()
        (raiz / "proprias").mkdir()
        (raiz / "listas" / "t.toml").write_text(
            """
titulo = "Teste"
saida = "dist/t.txt"
propria = "proprias/t.txt"

[[fontes]]
nome = "f1"
url = "https://ex.com/a.txt"
""",
            encoding="utf-8",
        )
        (raiz / "proprias" / "t.txt").write_text(propria, encoding="utf-8")
        return raiz

    def rodar(self, raiz, conteudo="||a.com^\n||b.com^\n"):
        return processar(
            raiz / "listas" / "t.toml", raiz, "2026-09-18", baixar=lambda url: conteudo
        )

    def test_lista_publicada_usa_quebra_de_linha_unix(self):
        raiz = self.montar_repo()
        self.rodar(raiz)
        self.assertNotIn(b"\r\n", (raiz / "dist" / "t.txt").read_bytes())

    def test_relatorio_usa_quebra_de_linha_unix(self):
        raiz = self.montar_repo()
        self.rodar(raiz)
        self.assertNotIn(b"\r\n", (raiz / "dist" / "relatorio-t.md").read_bytes())

    def test_estado_usa_quebra_de_linha_unix(self):
        raiz = self.montar_repo()
        self.rodar(raiz)
        self.assertNotIn(b"\r\n", (raiz / "estado" / "t.json").read_bytes())

    def test_junta_fontes_e_lista_propria_no_arquivo(self):
        raiz = self.montar_repo()
        ok, resumo = self.rodar(raiz)
        self.assertTrue(ok)
        conteudo = (raiz / "dist" / "t.txt").read_text(encoding="utf-8")
        self.assertIn("||a.com^", conteudo)
        self.assertIn("||minha.com^", conteudo)
        self.assertIn("t: 3 regras", resumo)

    def test_segunda_rodada_identica_nao_reescreve(self):
        raiz = self.montar_repo()
        self.rodar(raiz)
        antes = (raiz / "dist" / "t.txt").read_bytes()
        ok, resumo = self.rodar(raiz)
        self.assertTrue(ok)
        self.assertIsNone(resumo)  # None = nada a commitar
        self.assertEqual((raiz / "dist" / "t.txt").read_bytes(), antes)

    def test_fonte_fora_do_ar_preserva_o_arquivo_anterior(self):
        """A garantia central: nunca substituir uma lista boa por uma suspeita."""
        raiz = self.montar_repo()
        self.rodar(raiz)
        bom = (raiz / "dist" / "t.txt").read_bytes()

        def cai(url):
            raise OSError("connection reset")

        ok, _ = processar(
            raiz / "listas" / "t.toml", raiz, "2026-09-19", baixar=cai
        )
        self.assertFalse(ok)
        self.assertEqual((raiz / "dist" / "t.txt").read_bytes(), bom)


if __name__ == "__main__":
    unittest.main()
