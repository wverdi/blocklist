# Licenças

## Decisão

Este repositório é **GPL-3.0** (`LICENSE`, texto canônico da FSF).

Motivo: `dist/adulto.txt` é obra derivada da lista NSFW do HaGeZi, que é
GPL-3.0 — uma licença *copyleft*. Quem redistribui obra derivada precisa
distribuir sob a mesma licença e preservar os avisos de autoria. Uma licença
permissiva (MIT, Apache) seria incompatível com isso.

Efeito prático, que é o pretendido: **qualquer pessoa pode usar,
redistribuir e modificar** estas listas. Quem publicar uma versão derivada
mantém a GPL-3.0 e os créditos.

A GPL-3.0 cobre também o código do `gerador/`, que é original deste
repositório.

## Fontes

Verificado em 18/09/2026.

| Fonte | Licença | Situação |
|---|---|---|
| `hagezi/dns-blocklists` | **GPL-3.0** | publicada |
| `StevenBlack/hosts` | MIT | publicada |
| `blocklistproject/Lists` | Unlicense | observação |
| `oisd` (nsfw.oisd.nl) | não declarada no GitHub; ver termos em oisd.nl | observação |

A MIT do StevenBlack é permissiva e compatível: combinar as duas resulta em
GPL-3.0, a mais restritiva.

Se `oisd-nsfw` for promovido de observação para publicado, **confira os
termos de uso em oisd.nl antes** — é a única fonte configurada sem licença
declarada de forma legível por máquina.

## Créditos no arquivo publicado

O gerador emite, no cabeçalho de todo arquivo:

```
! License: GPL-3.0
!
! ---- Fontes consolidadas ----
!   hagezi-nsfw (GPL-3.0) - https://raw.githubusercontent.com/hagezi/...
!   stevenblack-porn-only (MIT) - https://raw.githubusercontent.com/StevenBlack/...
```

O campo `licenca` e as linhas de crédito vêm de `listas/<nome>.toml`. Ao
adicionar uma fonte nova, preencha `licenca` — o gerador escreve "licenca
nao declarada" quando o campo fica vazio, e isso aparece publicamente no
arquivo.

## Isenção de responsabilidade

Declarada em três lugares: as seções 15 e 16 da GPL-3.0, a seção
correspondente do `README.md`, e o campo `aviso` de `listas/adulto.toml`,
que o gerador copia para o cabeçalho de cada arquivo publicado.

O texto no cabeçalho existe porque quem assina a URL no AdGuard Home
normalmente nunca abre o repositório — o aviso precisa viajar junto com a
lista.
