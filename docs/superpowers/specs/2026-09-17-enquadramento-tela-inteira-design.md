# Enquadramento: a tela inteira cabe, e a cara aparece

Decidido em 17/09/2026, a partir de dois clipes reprovados pelo dono
(`corte-c0c040fc97-01` e `-02`, da live `kZAAnNJHaUc`, alanzoka jogando
Intruder). Cada número aqui é medido; onde não é, está dito que não é.

## Os dois defeitos, e a causa real de cada um

**1. A tarja preta no pé não é uma tarja.** É o fundo borrado colapsando.
Medido no quadro dos 4s do clipe 01: **93,1% dos pixels do rodapé são
exatamente 0**; no clipe 02, 27,2%. A fonte é escura -- luma mediana 15 no
quadro 01, 20 no 02 -- e `cadeia_dividida` aplica
`eq=brightness=-0.10`, que subtrai ~25 de 255 e clipa a cena inteira em zero.
Trocar preto por borrado não conserta: já **é** o borrado.

**2. A imagem cortada vem de dois lugares diferentes.**
O clipe 02 é o caminho normal: `kept = (9/16)/(16/9) = 0,3164`, ou seja
**68% da largura da fonte vai fora**. O clipe 01 é o ramo `quando_cheio` de
`cadeia_dividida`, que encaixa a fonte por COBERTURA numa caixa de
1080x1240 -- 49% da largura sobrevive -- e deixa de 1240 a 1920 sem nada.
Naquela janela os "trechos de câmera" cobrem 15,3s de 20,9s: **73% do clipe**.
É por isso que o clipe inteiro parece um corte normal com tarja.

Prova na fonte: o texto do jogo é `YOU ARE DEAD - Press Space to spectate`.
No clipe entregue lê-se `AD - Press Space to spectate`.

## O que o layout novo é

Quadro 1080x1920, de cima para baixo:

    0     interface do app (280px) -- só fundo borrado, nada nosso
    280   TELA: a fonte INTEIRA, encaixada por largura, NADA cortado
    +16   folga
          ROSTO: a webcam recortada, na proporção dela, centrada
    1240  legenda queimada (2 linhas, 320px)
    1560  folga da legenda (90px) + interface do app (270px)

Números para esta fonte (1920x1080, webcam em x 1470-1910, y 10-265):
tela **996x560** (escala 0,52 da fonte), rosto **663x384** (escala 1,51 do PiP).

### Por que o rosto fica EMBAIXO, e não em cima

Medido nas duas maquetes, do quadro real dos 4s:

| ordem | distância entre a cara grande e a webcam dentro do jogo |
|---|---|
| rosto em cima | **297px** (15% do quadro) -- leem como erro de render |
| rosto embaixo | **753px** (39%) |

E o jogo tem texto próprio no pé do quadro (`YOU ARE DEAD`, Health/Energy).
Com a tela embaixo esse texto encosta na nossa legenda; com a tela em cima a
faixa do rosto separa os dois.

### De onde sai o tamanho da faixa do rosto

Não é escolha: é o mínimo que já existe no arquivo. `layout_dividido` já
reserva `int(height * 0.20)` para a faixa da pessoa, e o teste
`test_a_webcam_fica_em_cima_e_encosta_na_tela` já assere `wh > 1920*0.2`.
Mantido o mínimo, a tela fica com o resto e encolhe de 1080 para 996 de
largura. O dono escolheu essa troca olhando as quatro maquetes: 12% menos de
tela por 21% mais de rosto.

### Margens

Ficam as de hoje (280 topo / 270 base / folga 90). O levantamento não achou
**nenhuma** documentação do YouTube para criadores com número de área segura;
o único número oficial em texto é do lado de anúncios do Google ("evite os
10% de cima, os 25% de baixo, os 10% da direita"), e 25% = 480px moveria o
piso da legenda de 360 para 570 em TODOS os clipes, inclusive os que já saem
bem. **Pendência declarada:** o rodapé do Shorts nunca foi medido num print do
app, do jeito que os 280 do topo foram medidos num clipe real do TikTok.

## O gatilho

`decide_enquadramento` passa a decidir por **webcam de canto estável**, não
por planura. A planura vira informação na justificativa.

O que segura a pessoa falando de frente, e não é uma regra nova -- são as duas
que já existem: um rosto grande demais é recusado por `PIP_ROSTO_AREA_MAX`
(5% do quadro) e, quando passa, `_cresce_pip` cresce a caixa enquanto a
vizinhança for material de câmera -- numa câmera cheia ela cresce até o quadro
todo e é recusada por `PIP_AREA_MAX` (30%).

**A regra de estabilidade muda de denominador, e isso é o conserto medido do
clipe 02.** Hoje ela é `em_quantos*2 >= max(2, quadros_de_tela)`. Medido:

| janela | quadros | rosto em algum lugar | de tela | PiP achado | regra de hoje |
|---|---|---|---|---|---|
| 01 | 39 | 39 | 18 | 15 (38%) | passa (30 >= 18) |
| 02 | 40 | 40 | 33 | 11 (28%) | **falha** (22 >= 33) |

A webcam está nas duas janelas. O numerador depende de o detector achar rosto
DENTRO dela, e ele falha quando a pessoa olha para baixo; o denominador conta
quadros de tela, que não tem relação com isso. O novo critério é sobre o total
amostrado: a caixa vencedora tem de aparecer em pelo menos 20% dos quadros e
em pelo menos 3. Uma foto numa página aparece em 1-2 de 40 (2-5%) e continua
recusada.

## O que NÃO muda

- O modo continua a string `"dividido"`. Um terceiro valor cairia como
  `"normal"` nos 13 ramos que leem o booleano e seria narrado errado na nota,
  que volta pelo cache.
- O caminho normal (`:5901`) não é tocado.
- A legenda ASS continua ancorada em `safe_area` + `caption_piso`, sem
  consultar a geometria das faixas.
- O `fingerprint` do cache não muda. Consequência declarada: um corte já
  entregue destas janelas continua voltando errado até alguém limpar o
  `renders.json`.

## Como isto é conferido

1. `WARDEN_RENDER_PARALELO=2 /opt/anaconda3/bin/python3.11 -m pytest tests/ -q`
   -- a linha de base medida na main é `1338 passed, 31 skipped, 60 subtests`.
2. A suíte DENTRO da imagem publicada, com `-u hermes`, porque libass e cv2
   não existem no Mac.
3. Render das duas janelas reais, contact sheet do antes e do depois, e o
   agente olhando as duas imagens. Suíte verde não é clipe visto.

## Pendências declaradas

Escritas em 18/09/2026, no fim do trabalho. Nenhuma é conserto pela metade:
são coisas que NÃO foram medidas, e o custo de fingir que foram é alto.

### 1. A cobertura de enquadramento é de UMA janela real

Tudo que sustenta o layout novo foi medido em **uma única fonte**: a live
`kZAAnNJHaUc` (alanzoka jogando Intruder), em duas janelas (481-502 e
2422-2444) que são o MESMO layout de tela — mesma webcam, no mesmo canto, do
mesmo tamanho: x 75%-100%, y 0%-33%.

O que isso NÃO prova, e ninguém deve dizer que prova:

- **webcam em OUTRO canto.** Todos os números saíram de uma caixa no canto
  superior direito. `_corte_sem_webcam` tem os quatro lados e `_cresce_pip`
  cresce em qualquer direção, mas nenhum deles foi exercitado em material real
  com a webcam à esquerda, embaixo, ou centralizada numa barra.
- **webcam de OUTRA proporção.** A caixa detectada aqui é 1,33:1 (480x360,
  com a barra de sub dentro). A caixa do rosto herda essa proporção, e é ela
  que dita quanto sobra de fundo borrado nos lados. Uma webcam 16:9 ou
  quadrada dá outra composição, e ninguém olhou uma.
- **webcam que se MOVE no meio da janela.** `_agrupa_pips` agrupa caixas a até
  0,08 de distância e devolve a mediana; uma mudança maior vira outro grupo e
  o maior vence, então parte da janela sairia com o recorte errado. Não há
  material medido desse caso.
- **tela que não é jogo.** Slide, planilha, browser claro: a planura deles é
  alta e o gatilho novo não olha planura, então devem entrar no dividido. Só
  que "devem" não é "foram medidos".

`AJanelaRealDe1709VirouNUMEROS` congela os números desta fonte e roda em toda
máquina, o que impede uma regressão silenciosa. Não substitui uma segunda
fonte.

**Como fechar:** uma segunda live, de outro streamer, com a webcam em canto
diferente. Baixar duas janelas, rodar `tela_compartilhada` dentro da imagem,
renderizar e OLHAR o contact sheet -- o mesmo caminho desta rodada. Se a caixa
sair errada, o lugar de consertar é `_cresce_pip` e `_agrupa_pips`.

### 2. Os quatro testes da detecção não rodam em lugar nenhum

`ADetecaoAchaAWebcamDeCanto` (3) e `NoMaterialRealAClassificacaoAcerta` (1)
pulam no Mac por falta de OpenCV e pulam na imagem por falta da filmagem
`/var/lib/hermes/warden/footage/lote/7893834d2c` -- que não está lá, embora a
mensagem do skip afirmasse que sim (corrigida neste ramo).

### 3. Setenta e quatro testes são cegos dentro da imagem

`test_persona.py` (32) e `test_ponteiros.py` (42) são pytest puro; o
`unittest discover` colhe zero. Pôr `pytest` na imagem resolveria e foi
**recusado pelo dono em 18/09/2026**: a imagem é pública e ferramenta de dev
pesa em quem instala. Rodar esses 74 no host, e saber que o portão do
container não os cobre.

### 4. O rodapé do Shorts nunca foi medido num print do app

Os 25% do Google são spec de ANÚNCIO. As margens de hoje (280/270+90) são do
TikTok e foram medidas num clipe real. Para o Shorts, ninguém mediu.

### 5. O cache não distingue enquadramento

O `fingerprint` não inclui o modo, então um corte já entregue destas janelas
reentrega o arquivo antigo até alguém limpar o `renders.json`. Decisão do dono,
para manter o escopo pequeno.
