# O mosaico deixou de ser portão em 16/09/2026

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

A regra que o agente obedece mora **dentro da imagem**, nas skills que a usam —
`warden-clip`, `warden-check` e `warden-style` — no corpo da SKILL.md ou em
`references/`. Até 16/09/2026 as três apontavam para este arquivo, e isso era o
defeito: `docs/` não é copiado para a imagem, então o ponteiro mandava o agente
a um endereço que não existe para ele. O ponteiro saiu.

O que ficou aqui é a decisão, o preço dela e o defeito que ela deixa passar.

## A decisão, em uma linha

**Não abra o contact sheet antes de entregar.** A obrigação do agente são duas
coisas — o hook e a legenda — e o `warden cut` recusa nas duas por conta própria.
O resto se entrega e se repassa.

## Por que o portão existiu, para ninguém reconstruí-lo por acidente

Um clipe foi entregue uma vez com o hook cortado nas duas bordas, uma legenda
inglesa de seis linhas cobrindo o rosto de quem falava, e o disclaimer da fonte
fatiado ao meio no rodapé — e foi **reportado como tendo passado na
verificação**, porque verificação conta pixels e segundos e cada um desses
defeitos é invisível para aritmética. É real, e é por isso que o `warden cut`
passou a escrever um contact sheet do próprio render. Até 16/09/2026 abrir essa
imagem era condição de envio: o `deliver` recusava imprimir `MEDIA:` sem isso.

A evidência daquele clipe está no repositório: `notas/evidencia-clipe-reprovado.jpg`.

## Por que mudou, e quanto custou

O preço foi medido. **16/09/2026, num pedido real de 706 segundos: as duas
chamadas de visão daquele pedido custaram 31 segundos** — e o dono roda isto numa
chamada de tela compartilhada, com alguém olhando.

Perguntado diretamente se o mosaico devia continuar obrigatório antes de uma
entrega, a resposta dele, nas palavras dele:

> "2 = entrega sem olhar, sua unica obrigacao = hook e legenda"

No mesmo dia, a mesma pessoa: *"quero o mais rapido possivel com o minimo de
qualidade"*, *"Velocidade acima de tudo"*.

## O que se perde

Exatamente o defeito do primeiro parágrafo. Uma falha que só a imagem mostra —
hook cortado sobre um fundo claro, uma segunda legenda vinda da fonte — não é
mais pega aqui. Ela chega às mãos do dono, e é ele quem vê.

Ele decidiu isso com os 31 segundos na frente dele. **Não reponha o portão em
silêncio porque um clipe saiu errado**: reabrir é decisão dele, não do agente, e
se for reaberta ganha data nova e medição nova nesta seção.

## O que a ferramenta faz hoje

O `warden cut` continua escrevendo e imprimindo o sheet, e o `lote render`
continua empilhando um mosaico combinado do lote inteiro:

```
SHEET:/var/lib/hermes/cache/videos/clip-01-contato.jpg
MEDIA:/var/lib/hermes/cache/videos/clip-01.mp4
```

O `MEDIA:` é impresso com ou sem ele. O sheet custa cerca de 1,3 s para ser
escrito e fica porque é a única evidência visual que existe depois, quando algo
volta errado e alguém pergunta o que foi que subiu. **Nenhuma chamada de visão é
gasta nele**, e um render sem sheet se entrega como qualquer outro.

**Aviso na saída se repassa, não se investiga.** Quando o `cut` diz que uma linha
de legenda é suspeita, que a imagem pode já trazer texto queimado, ou que o
enquadramento caiu no centro por não achar rosto, isso vai para a pessoa em UMA
oração ao lado do clipe — *"pode ter texto queimado no topo desse, olha antes de
subir"* — e ela decide. Puxar um frame ou re-renderizar para resolver isso
sozinho são os dez minutos que ele mandou não gastar.

## Se alguém FOR olhar, esta é a lista

Isto não é mais portão e nenhum item aqui reprova um clipe sozinho. É o que
conferir quando o dono pede para olhar um clipe, ou quando um volta errado:

1. **O hook cabe na tela?** Nenhuma letra tocando as bordas laterais, e ele
   ainda legível sobre o fundo que ficou atrás dele.
2. **A legenda cobre o rosto de quem fala?** Seis linhas empilhadas no meio da
   tela é o caso que já saiu.
3. **Tem uma segunda legenda?** A da fonte (lower-third, subtítulo do canal,
   legenda queimada do acervo) aparecendo junto com a nossa.
4. **O rodapé da fonte ficou pela metade?** Disclaimer ou faixa fatiada pelo
   corte 9:16.
5. **O enquadramento pegou quem fala?** Ou ficou no centro porque nenhum rosto
   foi encontrado — o `cut` imprime qual dos dois foi.
6. **A legenda está no mesmo idioma do hook?** O `cut` compara e recusa queimar
   quando divergem, mas um hook em português sobre legenda inglesa já saiu uma
   vez.

Um clipe que passa na verificação numérica e nunca foi olhado é como um arquivo
com dez defeitos visíveis foi reportado como aprovado. Esse é o preço da troca, e
ele é do dono.
