# notas

Material de trabalho do Clip Warden: relatórios de auditoria, medições, briefings
e o critério de um teste. **Nada aqui é necessário para instalar ou usar o
agente** — quem quiser rodá-lo precisa apenas do `README.md` da raiz.

Estes arquivos ficam versionados de propósito. Quase toda regra deste projeto
existe porque alguma coisa quebrou de um jeito específico, e é aqui que está o
que foi medido: os números, as datas e as saídas de comando que sustentam as
decisões escritas na persona, nas skills e nos comentários do código. Apagá-los
deixaria o repositório com as conclusões e sem as evidências.

Quatro deles são citados de fora, e os ponteiros apontam para `notas/`:

| arquivo | citado por |
|---|---|
| `RELATORIO-AUDITORIA-13-09.md` | `compose.yml`, `docs/AMBIENTE.md` |
| `CRITERIO-TESTE-TIKTOK-INBOX.md` | `tests/test_tiktok.py`, `docs/POSTAGEM.md`, `warden-shared/scripts/warden_tiktok.py` |
| `ANALISE-POSTAGEM-REDES.md` | `warden-shared/scripts/warden_tiktok.py` |
| `evidencia-clipe-reprovado.jpg` | `docs/CONTACT-SHEET.md` |

Nenhum deles entra na imagem: o `Dockerfile` copia `runtime/persona.md`, as sete
pastas `warden-*/` e `SPECS/`, e mais nada. Regra que o agente precisa seguir
nunca mora aqui — mora na persona, numa `SKILL.md` ou em
`warden-<skill>/references/`, e `tests/test_ponteiros.py` reprova quem tentar o
contrário.
