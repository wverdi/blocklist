# blocklist

Listas de bloqueio DNS consolidadas, geradas automaticamente e assinadas no
AdGuard Home.

Cada lista junta fontes públicas escolhidas, a curadoria própria e as
exceções da casa, remove duplicados e publica um único arquivo no formato
AdGuard. O gerador **extrai apenas o domínio** de cada linha das fontes e
**reescreve** a regra como `||dominio^`: nenhuma linha crua de terceiro chega
ao DNS, então uma fonte comprometida não consegue injetar `$dnsrewrite`
(redirecionar um domínio) nem `@@` (furar o bloqueio).

## Listas publicadas

| Lista | URL para assinar no AdGuard |
|---|---|
| Conteúdo adulto | `https://raw.githubusercontent.com/wverdi/blocklist/main/dist/adulto.txt` |

O relatório de cada rodada fica em `dist/relatorio-<lista>.md`.

Outras categorias entram depois, uma por arquivo em `listas/`.

> `kwai.txt`, na raiz, é anterior a este gerador e continua mantido à mão —
> não é gerado nem tocado por nada aqui.

## Como isso funciona

```
listas/<nome>.toml   ──┐
proprias/<nome>.txt  ──┼──► gerador/gerar.py ──► dist/<nome>.txt  ──► AdGuard
proprias/…-excecoes  ──┘         │                dist/relatorio-<nome>.md
fontes públicas ───────────────►─┘                estado/<nome>.json
```

A cada rodada o gerador:

1. baixa cada fonte por HTTPS, com limite de tempo e de tamanho;
2. extrai só o domínio; descarta `@@`, `$modificadores`, regex, curingas e
   qualquer linha que não saiba reescrever com segurança;
3. junta as fontes marcadas `publicar = true` com `proprias/<nome>.txt`;
4. remove as exceções de `proprias/<nome>-excecoes.txt`;
5. **compacta**: tira todo domínio cujo pai já está na lista, já que
   `||exemplo.com^` cobre `www.exemplo.com` e todos os outros subdomínios;
6. confere as travas de sanidade;
7. só reescreve o arquivo se alguma regra mudou de fato.

## Travas de sanidade

A rodada **não publica** e mantém o arquivo anterior quando:

- uma fonte marcada `publicar = true` falha, responde vazia ou só com
  comentários (um HTTP 200 com corpo truncado é o modo de falha mais
  perigoso: passaria despercebido e esvaziaria a lista);
- o total varia mais que `variacao_maxima` em relação à rodada anterior;
- o total cai abaixo de `minimo_dominios`.

Nunca existe uma janela com a lista vazia: o arquivo publicado só é
substituído depois de o novo passar por todas as travas.

Uma fonte em **modo observação** (`publicar = false`) que falhe não impede a
publicação — ela não entra no arquivo de qualquer forma.

## Adicionar uma categoria nova

Crie `listas/<nome>.toml`. O workflow encontra sozinho, sem editar YAML:

```toml
titulo = "Jogos de azar"
descricao = "..."
homepage = "https://github.com/wverdi/blocklist"
expires = "1 day"

saida = "dist/jogos.txt"
propria = "proprias/jogos.txt"
excecoes = "proprias/jogos-excecoes.txt"
permitir_regra_de_tld = false

[travas]
variacao_maxima = 0.20
minimo_dominios = 1000

[[fontes]]
nome = "alguma-fonte"
url = "https://exemplo.com/lista.txt"
licenca = "MIT"
publicar = true
```

Uma lista **sem nenhuma fonte externa** é válida: basta omitir os blocos
`[[fontes]]` e manter só a curadoria própria. O gerador ainda assim
normaliza, deduplica, compacta e aplica as travas.

## Modo observação

Uma fonte com `publicar = false` é baixada e medida a cada rodada, aparece no
relatório com o que acrescentaria, mas **não entra no arquivo**. Serve para
acompanhar uma fonte por algumas semanas antes de confiar nela. Promover é
trocar uma palavra no TOML.

Hoje `oisd-nsfw` e `blocklistproject-porn` estão em observação: a amostragem
de 18/09/2026 encontrou muito domínio fora da categoria nas duas (empresas
comerciais, blogs pessoais), e nenhuma das duas cobria os domínios que a
curadoria própria já tinha achado no query log.

## Regras de TLD inteiro

`permitir_regra_de_tld` controla se regras de um rótulo só (`||xxx^`) podem
entrar. Elas bloqueiam um domínio de topo completo.

Comportamento verificado no motor de filtragem da AdGuard: `||sex^` bloqueia
`site.sex` e `loja.sex`, mas **não** bloqueia `sexta.com`, `middlesex.ac.uk`
nem mesmo `sex.com` — o `^` exige um separador, e letra e ponto não são
separadores. São regras estreitas e seguras.

O risco não é casar texto parcial: é uma fonte bloquear um TLD legítimo (um
`||ad^` derrubaria toda Andorra). Por isso a opção é opt-in e o relatório
lista todas as regras de TLD que entraram, a cada rodada.

## Exceções

`proprias/<nome>-excecoes.txt` remove a linha exata do arquivo publicado.

**Isso não vence uma regra de domínio pai.** Se `||exemplo.com^` está na
lista, excetuar `img.exemplo.com` não libera nada. O relatório avisa toda vez
que isso acontece, na seção "Exceções sem efeito" — a única forma de saber
que a exceção não funcionou. Para liberar de verdade, exclua também a regra
do pai.

## Rodar na mão

Sem dependências: só Python 3.11 ou mais novo.

```bash
python -m unittest discover -s gerador   # 141 testes
python gerador/gerar.py                  # todas as listas
python gerador/gerar.py listas/adulto.toml
```

## Automação

`.github/workflows/atualizar.yml` roda todo dia às 09:00 UTC (06:00 em
Brasília) e no `workflow_dispatch`. Ele executa os testes antes de gerar, e
só commita quando alguma regra mudou — a mensagem de commit resume a rodada.

Dois avisos sobre o agendamento do GitHub Actions:

- o horário **não é pontual**: atrasos de minutos ou horas são normais.
  Irrelevante aqui, porque o AdGuard relê os filtros a cada 24 h;
- o GitHub **desativa workflows agendados** de repositórios sem atividade por
  um período longo. As rodadas que mudam algo geram commit e contam como
  atividade, mas vale conferir de tempos em tempos a data da última execução.

## Licença e isenção

Este repositório é licenciado sob a **GPL-3.0** (ver `LICENSE`). Qualquer
pessoa pode usar, redistribuir e modificar as listas; obras derivadas
permanecem sob a mesma licença.

A escolha não é arbitrária: a lista consolidada é obra derivada da NSFW do
**HaGeZi**, que é GPL-3.0. Uma licença permissiva seria incompatível.

**Sem garantia de espécie alguma.** A lista pode bloquear domínios legítimos
(falso positivo) e deixar passar conteúdo que você esperava que bloqueasse
(falso negativo). Ela **não substitui supervisão nem constitui controle
parental**. O uso é por sua conta e risco, e a responsabilidade por qualquer
consequência é de quem a utiliza. As seções 15 e 16 da GPL-3.0 tratam disso
formalmente; este aviso também vai no cabeçalho de cada arquivo publicado,
para quem assina a URL no AdGuard e nunca abre o repositório.

Domínio bloqueado indevidamente, ou faltando?
[Abra uma issue](https://github.com/wverdi/blocklist/issues).

### Créditos das fontes

O cabeçalho de cada arquivo publicado nomeia todas as fontes consolidadas,
com licença e URL de origem. Detalhamento em `docs/licencas.md`.

## O que nunca entra aqui

Consultas reais de usuários, nomes de máquinas internas, faixas de IP da
rede, credenciais ou qualquer coisa que identifique pessoas. O repositório é
público: `proprias/` e as exceções ficam visíveis para qualquer um.
