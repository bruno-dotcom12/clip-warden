# Briefing de correção: render do clip-warden

Contexto: o agente recebeu um pedido de **dois** cortes de 20s de um vídeo de 23
minutos. Entregou **um** corte, feio, com legenda errada queimada, e levou mais
de 15 minutos. O arquivo entregue foi analisado frame a frame e o que está
abaixo é diagnóstico com causa localizada no código, não impressão.

Este documento é o escopo do trabalho. Leia inteiro antes de mexer em qualquer
arquivo.

---

## 1. O que o arquivo entregue realmente tem

Medido no render final (20,0s, 9:16, 30fps):

1. O hook em português aparece como `le apostou contra o Djokovic e ganhou 90 m`.
   O texto estoura a largura do frame e é cortado nos dois lados, em todos os
   frames do clipe.
2. O hook fica dentro de uma tarja preta dura de largura total, colada no topo.
   Lê como marca d'água de app gratuito.
3. A legenda queimada é um bloco de **seis linhas** parado na tela por sete
   segundos, cobrindo o rosto do sujeito do peito até o queixo.
4. A legenda está **em inglês**, num clipe cujo hook está em português.
5. A legenda carrega erro de transcrição queimado: `jokovic jokovic` e
   `He did the voting was the underdog`, que não é o que o cara fala.
6. O vídeo de origem já traz legenda queimada própria (o disclaimer amarelo da
   Polymarket). Ela aparece cortada ao meio nas bordas, embaixo da nossa. São
   duas legendas concorrendo no mesmo frame.
7. A faixa vertical ciano da borda esquerda do material de origem entra no
   enquadramento e fica parada ali o clipe inteiro.
8. Zero movimento de câmera, zero corte de plano, zero variação de escala. É um
   trecho bruto de 20s com texto por cima.
9. Oito trocas de cena detectadas vêm do material de origem, nenhuma foi
   escolhida. Em 5,7s o clipe pula para um plano de arena completamente
   deslocado do assunto.
10. A fonte da legenda é a fallback do sistema (o estilo pede `Arial`, que não
    existe na imagem). O peso e o contorno não têm nada a ver com o padrão dos
    clipes já aprovados.

E o mais grave dos processos: o agente relatou que os dois clipes "passaram na
verificação". A verificação aprovou um arquivo com todos os defeitos acima
porque ela só confere números, nunca olha a imagem.

---

## 2. Causa de cada defeito, com endereço

Todos os caminhos são a partir da raiz do repo.

### 2.1 Hook cortado nos dois lados
`warden-shared/scripts/warden_media.py`, no final de `cut()` (bloco `if hook:`).

```
chain += (f",drawtext=text='{text}':fontcolor=white:fontsize={max(28, width // 22)}:"
          f"box=1:boxcolor=black@0.55:boxborderw=18:"
          f"x=(w-text_w)/2:y={max(40, int(safe.get('y0', 200)))}")
```

`drawtext` não quebra linha e não ajusta corpo. Com `width=1080` o corpo sai
49px, e 44 caracteres nesse corpo passam de 1100px de largura contra 1080px de
frame. `x=(w-text_w)/2` fica negativo e o ffmpeg corta os dois lados sem erro
nenhum. Ou seja: **qualquer hook com mais de uns 35 caracteres sai cortado, e
sempre saiu.** Isso nunca foi pego porque ninguém olhou um frame.

Correção: medir o texto antes de desenhar, quebrar em até duas linhas e reduzir
o corpo até caber na largura útil. A largura útil não é 1080. É **854px**, e
esse número já está justificado em `PRIME/edit.py`: a coluna direita do TikTok
reserva 140px e a margem esquerda come 86px.

### 2.2 Tarja preta dura
Mesmo bloco, `box=1:boxcolor=black@0.55`. Tarja retangular de largura total.

Correção: scrim em gradiente, alfa máximo no centro do texto e some nas bordas.
Já implementado e comentado em `PRIME/edit.py`, função `png_texto`.

### 2.3 Legenda ilegível e mal formatada
`warden_media.py`, função `to_ass()`.

Três problemas somados:

`fontsize = max(18, height // 22)` dá 87px num frame de 1920. Grande demais.

`Style: Default,Arial,...` pede uma fonte que não está instalada na imagem, e
o libass cai numa fallback genérica. O repo do PRIME tem `Anton-Regular.ttf`
justamente por causa disso.

E o pior: cada segmento do Whisper vira **uma cue inteira**. Um segmento de
sete segundos com trinta palavras vira um bloco de seis linhas parado na tela.
Não existe limite de caracteres por linha, nem de linhas por cue, nem de
duração de cue.

Correção, na ordem: reflow das cues antes de gerar o ASS (máximo 2 linhas,
máximo 26 caracteres por linha, alvo de 2,2s por cue, quebrando o segmento do
Whisper por fronteira sintática e não só por tempo), fonte embarcada no repo e
passada por `fontsdir` ao filtro, e corpo `height // 16` com contorno de 3px
mais sombra de peso.

Dois números deste parágrafo foram corrigidos depois de medidos, e seguir a
versão antiga reintroduz o defeito:

**O corpo é `height // 16`, não `height // 26`.** O `Fontsize` do ASS e o
tamanho de fonte do PIL não valem a mesma coisa. Quando a legenda passou de PNG
do PIL para ASS o número ficou igual e a LETRA encolheu 45% -- medido nos
arquivos, 3,07%-4,06% da altura do quadro antes, 1,88% depois -- e nada acusou,
porque nada media a altura da tinta, só o número da configuração, que não tinha
mudado. O alvo passou a ser a altura da LETRA: 3,5% do quadro, que na tabela
medida no próprio libass com a Anton em 14/09 é corpo 120 em 1920, ou seja
`height // 16`. `CAPTION_SIZE_DIVISOR = 26` continua existindo em
`warden_style`, mas serve à estimativa de linhas em `measure`, que olha pixels
de um arquivo pronto -- não é o corpo da legenda.

**O teto de duração de cue é 3,4s, com alvo em 2,2s.** A quebra por fronteira
precisa poder ESTENDER meia palavra para alcançar uma vírgula: quando a cue
inteira é palavra funcional ("é o meu", "que o", "e dá para") não há para onde
recuar. Então `MAX_CUE_S = 2,2` é o alvo, `CUE_TOLERANCIA_S = 1,0` é o quanto
uma cue pode passar dele para fechar numa fronteira, e o portão reprova em
3,4s. Foi uma troca deliberada: um segundo a mais na tela ninguém percebe,
"HATE THE" sozinho todo mundo percebe. O que não afrouxou, e é o que de fato
protegia o quadro, é o orçamento de caracteres -- duas linhas de 26.

### 2.4 Legenda em inglês num clipe em português
Não existe verificação de idioma em lugar nenhum. O hook vem do agente, a
legenda vem do Whisper, e ninguém compara.

Correção: `cut()` recusa queimar quando o idioma detectado na transcrição
diverge do idioma do hook ou do idioma declarado na campanha, e diz por quê.

### 2.5 Erro de transcrição queimado
O comentário em `to_srt()` diz, com todas as letras, que uma palavra errada
queimada é pior que legenda nenhuma, e que essa checagem fica com a pessoa. Só
que nada no fluxo obriga essa pessoa a existir. O agente queimou direto.

Correção: um gate explícito. O SRT precisa estar aprovado para ser queimado.
Sugestão de forma: `warden captions review <srt>` imprime as linhas que caem
dentro da janela do corte, e `cut()` recusa `--subtitles` se não houver um
arquivo de aprovação ao lado com o hash daquele SRT. Sem aprovação, o clipe sai
sem legenda em vez de sair errado.

### 2.6 Legenda dupla e texto do acervo cortado
`cut()` já sabe que não enxerga texto queimado no material. Ele avisa e queima
mesmo assim, o que transfere um problema que ele detectou para depois da
publicação.

Correção em duas partes. Detectar: a imagem já carrega OpenCV por causa do
YuNet, então dá para amostrar de seis a dez frames da janela e medir densidade
de borda nas faixas superior e inferior, que é o suficiente para dizer "esse
material tem texto queimado embaixo". Tratar: quando houver, ou a legenda nova
não é queimada, ou a faixa do acervo é coberta com o degradê (`PRIME/rodape.png`
e a função `rodape_cmd` em `PRIME/edit.py` fazem exatamente isso). A decisão vai
num aviso ao dono, não num silêncio.

### 2.7 Enquadramento cego a borda de material
`chain = scale=W:H:force_original_aspect_ratio=increase,crop=...` centrado no
rosto. O rosto ficou certo, mas a faixa escolhida incluiu a borda ciano do
material e cortou o disclaimer no meio das letras.

Correção: depois de calcular a faixa pelo rosto, conferir se as colunas das
bordas são conteúdo ou moldura, e recuar a faixa ou aplicar um leve
desfoque/degradê nas laterais quando for moldura.

### 2.8 Nenhum movimento, nenhum plano
Isso não é bug, é ausência de recurso. `cut()` renderiza uma janela contínua e
só. O padrão que já funciona no PRIME é outro: uma lista de planos com `in` e
`out`, cada um com um `efeito` de zoom leve (`1.02` para `1.08`) e uma `grade`
de cor. Está em `PRIME/EDITS/*.json` e é lido por `PRIME/edit.py`.

Correção: `warden cut` passa a aceitar uma lista de planos, e o zoom leve vira
o padrão e não a exceção. Um corte sem nenhum movimento de escala lê como
material bruto, é a diferença mais barata entre corte e edit.

### 2.9 Só um clipe de dois
Não há comando de lote e não há contrato de quantidade. O `warden-clip/SKILL.md`
pede "uma mensagem por clipe" e "entregue o primeiro assim que existir", o que
na prática deixa o agente parar no primeiro sem que nada acuse a falta.

Correção: um plano de lote (`warden cut --plan plano.json`) que renderiza N
janelas com checkpoint por clipe, e uma regra no SKILL de que o agente não
encerra o turno enquanto o número de arquivos entregues for menor que o número
pedido. Se um falhar, ele diz qual falhou e por quê, e não declara o lote
pronto.

### 2.10 Lentidão
`pick_model()` com `SMALL_CEILING_S = 900` manda uma fonte de 23 minutos para o
modelo `base`, o que ainda assim significa transcrever 23 minutos inteiros para
aproveitar 40 segundos.

Três correções, por ordem de ganho:

Primeiro, investigar por que a legenda publicada não foi usada. O `archive` já
baixa com `--write-auto-subs --write-subs --sub-langs pt,pt-BR,en
--convert-subs srt`, e `transcribe()` prefere um sidecar se encontrar. Ou o
vídeo não tinha legenda automática, ou `_subtitle_beside()` não casou o nome do
arquivo. Se for a segunda, é um conserto de dez linhas que economiza a etapa
inteira.

Segundo, transcrição em duas passadas: `tiny` na fonte toda só para localizar
candidatos, depois o modelo bom apenas nas janelas escolhidas. Passa de 23
minutos de áudio para uns 2 minutos.

Terceiro, progresso visível. Uma barra ou uma linha por minuto processado. Dez
minutos de silêncio num chat lê como agente morto, e isso já está escrito como
preocupação no próprio código.

### 2.11 A verificação aprovou tudo isso
`warden-check` confere duração, resolução e regras da campanha. Nenhum dos dez
defeitos da seção 1 é numérico.

Correção, e essa é a mais importante de todas: **nenhum clipe sai sem um contact
sheet revisado.** O `cut()` passa a gerar um mosaico de seis a oito frames do
próprio render, e o SKILL passa a obrigar o agente a abrir essa imagem e olhar
antes de chamar `send_message` para aquele clipe (a entrega está definida na
persona, em "Handing the file over"). O checklist do olhar é curto: o hook cabe
inteiro no frame, a legenda tem no máximo duas linhas, não há duas legendas, não
há moldura de material na borda, o rosto não está coberto. Qualquer item que
falhe reprova o clipe.

### 2.12 Resolução a confirmar
O arquivo entregue está em 720x1280. Pode ser transcode da entrega ou pode ser
`video.width`/`video.height` da campanha. Confirmar e, se for a campanha, fazer
`warden check` falhar alto quando a resolução real do arquivo não bater com a
regra, imprimindo os dois números.

---

## 3. A fonte da verdade do estilo já existe, e não é para inventar outra

Não crie um padrão visual novo. Existe um pipeline que já produziu clipes
aprovados, e ele está em `/Users/brunoarantes/clipagem/PRIME/`.

Leia, nesta ordem:

`PRIME/edit.py`, função `png_texto`. É o padrão de texto: caixa alta, fonte
condensada de peso alto, ajuste automático do corpo contra 854px de largura
útil, entrelinha de 1,34, contorno preto de 3px, sombra de peso separada, scrim
em gradiente e nunca tarja. Os comentários explicam o porquê de cada número, e
um deles diz a coisa que mais importa aqui: peso leve é marca de edit amador.

`PRIME/edit.py`, função `rodape_cmd` e o `rodape.png`. É como se cobre legenda
queimada do próprio acervo sem violar regra de campeonato.

`PRIME/EDITS/*.json`. É a gramática de montagem: lista de planos com `in`/`out`,
`grade` de cor, `efeito` de zoom, e um campo `_` em cada plano dizendo qual é a
função narrativa daquele plano. Abra `tese-01-trabalho-a-fazer.json` e repare
que o primeiro plano é sempre gancho e traz a razão anotada.

`PRIME/Anton-Regular.ttf`. É a fonte. Copie para o repo do warden e passe por
`fontsdir` ao libass, para nunca mais cair em fallback.

O trabalho aqui é **portar isso para dentro do `warden cut`**, não reimplementar
do zero. O HOOK ficou como PNG do PIL, e por um motivo: ele é medido com a fonte
real contra a largura útil ANTES de ser desenhado, e é essa medida que faz "o
hook cabe inteiro" ser aritmética em vez de olhar.

A LEGENDA não ficou. Ela saiu do PIL e virou ASS queimado pelo libass, por uma
coisa que o PNG não resolve: destaque palavra a palavra. Um PNG por estado de
palavra seriam cinquenta e cinco entradas de ffmpeg num clipe de vinte
segundos; `{\k}` faz o mesmo dentro de uma linha de texto. O preço é uma
dependência de compilação: sem `--enable-libass` o `cut` recusa queimar legenda,
em voz alta.

---

## 4. Corpus de referência e teste de regressão visual

O pedido do dono é que o agente aprenda com o que já ficou bom. Isso não é
treino de modelo, é spec medida mais teste que falha.

Material de referência disponível:

`PRIME/APROVADOS/` e `PRIME/APROVADOS-POS-MUDANCA/`, com os `.mp4` e o
`-LEGENDA.txt` de cada um.

`/Users/brunoarantes/clipagem/clipes cowork prime/lote 11-09/` e `lote 12-09/`,
que são os lotes mais recentes que o dono considerou publicáveis.

O que construir:

**`warden style extract <clipe.mp4>`**, que mede um clipe aprovado e cospe
números: resolução, fps, corpo do texto em px, posição y do bloco, número de
linhas por cue, duração média e mediana de cue, intervalo médio entre cortes,
variação de escala ao longo do plano, e se há texto na faixa inferior. Rode
sobre todos os aprovados e consolide num `SPECS/estilo-aprovado-scenepack.json`
com faixas, não valores únicos. O nome diz de que formato ela é a medida, e isso
não é cosmético: chamada `estilo-aprovado.json`, ela lia como "o estilo
aprovado", ponto -- e o que ela mede são vinte scenepacks de animação, nenhum
deles um corte de fala.

**`warden style check <render.mp4>`**, que mede o render novo pelas mesmas
métricas e reprova fora da faixa. É isso que transforma "ficou feio" em erro que
o agente vê sozinho.

**Golden test.** Escolha um aprovado, guarde a fonte, a janela e o hook, e faça
o teste renderizar de novo e comparar as métricas com o arquivo aprovado. Se
alguém mexer no renderer e piorar o padrão, o teste quebra.

E um `warden-style/SKILL.md` curto, com o padrão em texto, para o agente ler
antes de escolher hook e legenda, não depois.

---

## 5. Ordem de trabalho

A ordem importa porque os primeiros itens são os que impedem o agente de
entregar lixo achando que está bom.

**Fase 1, o portão.** Contact sheet automático no `cut()`, obrigação no SKILL de
olhar antes de entregar, e o contrato de quantidade de clipes. Sem isso, todo o
resto pode regredir sem ninguém perceber.

**Fase 2, o texto.** Hook com medição, quebra e scrim. Reflow das cues com
limite de linhas e de duração. Fonte embarcada. Gate de idioma e gate de
aprovação do SRT. É aqui que o clipe deixa de ser feio.

**Fase 3, a imagem.** Detecção de texto queimado no material, tratamento por
degradê, borda de moldura no enquadramento, e zoom leve por padrão.

**Fase 4, a velocidade.** Legenda publicada quando existir, transcrição em duas
passadas, progresso visível.

**Fase 5, o padrão medido.** `style extract`, `style check`, golden test e o
SKILL de estilo.

Entre uma fase e outra, renderize o mesmo clipe de teste e abra o contact sheet.

---

## 6. Definição de pronto

Um clipe só está pronto quando todas estas forem verdade, e a verificação disso
tem que ser automática onde der e visual onde não der:

O hook aparece inteiro, sem corte lateral, em no máximo duas linhas.
A legenda tem no máximo duas linhas, nenhuma cue passa de 3,4s, e nenhuma cue
fecha em palavra pendurada.
Não há duas legendas no mesmo frame.
A legenda está no mesmo idioma do hook.
Nenhuma palavra queimada foi para a tela sem aprovação humana.
Não há moldura, borda de material ou texto de terceiro cortado nas bordas.
O rosto do sujeito não está coberto por texto.
Há movimento de escala em pelo menos um trecho.
A resolução do arquivo bate com a regra da campanha.
O número de arquivos entregues é igual ao número pedido.
O contact sheet foi gerado e olhado.

---

## 7. Uma coisa a não fazer

Não conserte os sintomas um a um no filtergraph até o próximo frame parecer ok.
O defeito de fundo não é o `drawtext`, é que o sistema inteiro não tem nenhum
ponto em que alguém ou algo **olha a imagem** antes de dizer que passou. Se
sair daqui só com o hook quebrando linha, o próximo clipe vai sair feio de um
jeito diferente e o relatório vai dizer de novo que passou na verificação.
