# Linha de legenda suspeita: é aviso, não recusa

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

A regra curta mora dentro da imagem, em `warden-clip`. O que está aqui é a
medição que a mudou em 16/09/2026 — e que é o motivo de ninguém dever repor a
recusa por conta própria.

## O que a regra diz (cópia para leitura humana)

Um número, uma palavra repetida, um marcador `>>` de auto-legenda: a ferramenta
**nomeia a linha, imprime como ela aparece na tela, e queima a legenda assim
mesmo**. Você lê a linha na saída da ferramenta — não num mosaico que você abre —
e se ela estiver errada você diz isso em UMA oração ao entregar o clipe. Não
re-renderize por isso a menos que peçam.

`--keep "<a linha>"` continua existindo e agora significa só **"eu li esta,
pare de me avisar"**. Ele não decide mais se o clipe tem legenda.

## Quem assina, por estrada

Quando nenhuma assinatura existe, o `cut` renderiza o clipe **sem legenda
nenhuma** em vez de queimar palavras que ninguém leu:

| estrada | quem assina |
|---|---|
| `warden lote render` | a ferramenta assina as linhas limpas ela mesma, por janela. Você não roda `captions review`, e ninguém é perguntado |
| `warden cut` sozinho | VOCÊ assina, antes: `warden captions review <srt> --start <s> --end <s> --approve` |

## A medição que mudou isto, em 16/09/2026

Até 16/09 **uma** linha suspeita renderizava o clipe inteiro SEM legenda até a
linha ser repetida de volta em `--keep`. Medido naquele dia, numa live real do
YouTube: `>>` em **184 de 706 cues**, e **2 clipes de 2 saíram sem palavra
nenhuma na tela, duas vezes** — que é a reclamação do dono, palavra por palavra.

Uma verificação que dispara em todo clipe não está protegendo o clipe, está
desligando o produto. E a regra do dono é que a legenda é a única coisa que ele
espera.

## Ler continua sendo o ponto

O whisper escuta errado. **"Em 1826" foi para a tela em 15/09** porque uma linha
foi marcada e assinada no mesmo fôlego. O lugar de ler é a saída da própria
ferramenta, que imprime cada linha marcada como ela aparece na tela — isso não
custa chamada de visão nem turno extra.

Isto **nunca** é pergunta para a pessoa: você repassa o que foi marcado, você
nunca pede permissão para legendar.

## O outro texto: o que a imagem já carrega

Coisa diferente da linha suspeita, e o `cut` também avisa sobre ela. Antes de
renderizar ele abre oito frames da janela e mede a densidade de borda da faixa de
baixo, que é o que acha texto que a imagem **já** traz: um lower-third, a legenda
queimada do canal, os subtítulos do próprio acervo. Queimar por cima disso dá
duas legendas.

Ele acha em três estados, não dois:

- **Faixa fixa** (um disclaimer permanente): trip o booleano duro, e o `cut`
  cobre com o rodapé em gradiente.
- **Legenda fina, centrada e intermitente** (a legenda do próprio vlog, que some
  entre frases): muitas vezes não trip. Medido no corte que saiu com duas
  legendas: **um frame em oito votou e a média foi 1,7× o meio do frame**, abaixo
  do 1,9× que o booleano pede.
- Esse caso virou um terceiro estado, **suspeito**, e o `cut` o trata como o
  primeiro: cobre o rodapé assim mesmo, porque um gradiente sobre imagem limpa
  custa um gradiente e uma segunda legenda custa o clipe. Depois o
  `warden style check` lê o sidecar e reprova o arquivo se um fundo suspeito
  ficou descoberto debaixo da nossa legenda.

O `cut` imprime uma linha sobre a imagem **toda vez que queima legenda**, com os
números, inclusive quando não achou nada — antes disso, "limpo" e "ninguém olhou"
eram a mesma saída vazia. Leia essa linha. Quando ela diz que achou ou suspeitou
texto, isso é um aviso que você repassa em UMA oração ao lado do clipe e a pessoa
decide; você não abre um frame para resolver. Só se ELE disser que o texto da
fonte deve ficar é que você re-corta sem `--subtitles`.
