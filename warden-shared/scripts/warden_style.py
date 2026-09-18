#!/usr/bin/env python3
"""O padrão visual, portado do PRIME, e o único lugar que olha a imagem.

Nada aqui foi inventado. Os números vêm de `PRIME/edit.py`, que já produziu os
clipes que o dono aprovou, e cada um deles carrega o motivo de ser aquele número
e não outro. O que este arquivo acrescenta ao PRIME é o que faltava no warden:
uma função que olha o render depois de pronto e devolve uma imagem que uma
pessoa consegue reprovar.

  contact_sheet()   o mosaico do próprio render, que é o portão da entrega
  fit_lines()       mede o texto antes de desenhar, quebra e reduz até caber
  text_png()        o texto como PNG do PIL: caixa alta, scrim, contorno
  reflow_cues()     a legenda em cues que cabem na tela e passam rápido
  footer_png()      o degradê que cobre a legenda queimada do próprio acervo

A largura útil é 854px num quadro de 1080, e esse número é do PRIME: a coluna
direita do TikTok reserva 140px e a margem esquerda de corte come 86px. Tudo
aqui escala a partir dessa proporção, para um quadro de qualquer largura.
"""
import json
import math
import os
import re
import subprocess
import sys as _sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")

# A fonte mora no repo por um motivo medido: o estilo antigo pedia `Arial`, que
# não está instalada na imagem, e o libass caía numa fallback genérica. Peso leve
# é marca de edit amador -- é o comentário do PRIME e é o que separa os dois.
FONT_PATH = os.environ.get("WARDEN_FONT") or os.path.join(ASSETS, "Anton-Regular.ttf")

# O quadro de referência em que os números do PRIME foram medidos.
REF_W, REF_H = 1080, 1920
REF_USABLE = 854                 # largura útil: 1080 - 140 (coluna do TikTok) - 86

# As três margens da interface do TikTok, em pixels do quadro de referência de
# 1080x1920 e escaladas por regra de três para qualquer outro. Até aqui a única
# proteção era a largura útil de 854, que cuida da coluna direita POR SIMETRIA
# -- texto centralizado nunca chega lá. Simetria não é portão, e não cobre nem
# o topo nem a base.
#
# TOPO: as abas "Seguindo / Para você" e a lupa ocupam os primeiros ~200px. No
# clipe entregue em 14/09 a primeira linha do hook começava a 184px -- medido
# no arquivo, isolando a tinta parada contra o fundo em movimento; 199px no
# segundo corte, que abre com maiúscula sem acento. Os dois caem dentro da
# faixa que o próprio app cobre. 280 é o primeiro Y em que a linha de cima está
# livre com folga em vez de por pouco.
MARGEM_TOPO = 280
# BASE: a legenda do app, o nome de quem postou e o som ocupam os últimos ~270px.
MARGEM_BASE = 270
# DIREITA: a coluna de curtir / comentar / compartilhar come os últimos ~100px.
MARGEM_DIREITA = 100
# A folga da legenda é outra coisa que a margem. A margem é onde o portão
# reprova; a folga é onde o render mira. Sem ela a legenda encostava no próprio
# limite: medida em ~1590 num quadro de 1920, ou seja 60px do começo da
# interface de baixo. Passava, e passava por pouco. 90px é a diferença entre
# obedecer à regra e disputar espaço com ela.
CAPTION_FOLGA = 90


def margens(width, height):
    """As três margens da interface, em pixels DESTE quadro."""
    return {"top": round(height * MARGEM_TOPO / REF_H),
            "bottom": round(height * MARGEM_BASE / REF_H),
            "right": round(width * MARGEM_DIREITA / REF_W)}


def caption_piso(height):
    """Quantos pixels a legenda deixa livres embaixo: a margem MAIS a folga.

    Existe separado de `margens` porque um é o alvo do render e o outro é o
    limite do portão. Confundir os dois foi como a legenda foi parar a 60px da
    interface com todos os checks verdes.
    """
    return round(height * (MARGEM_BASE + CAPTION_FOLGA) / REF_H)

# Corpo do hook, como fração da largura do quadro. 76/1080 é o tamanho da âncora
# do `tese-01`, que é o clipe que o dono aprovou com uma frase sustentada.
HOOK_SIZE_RATIO = 76 / REF_W
HOOK_MIN_RATIO = 34 / REF_W      # o piso do PRIME: abaixo disso não se lê no feed

# Quanto tempo o hook fica na tela, e o fade com que ele sai.
#
# Três segundos, não o clipe inteiro. Um hook é uma promessa: ele existe para
# fazer o espectador ficar nos primeiros segundos, e depois disso ele é uma
# placa parada em cima da imagem. Medido nos dois cortes de 14/09: a frase
# estava nos oito quadros do mosaico, de 1,2s a 18,8s, disputando o quadro com
# a legenda da fala durante dezessete segundos em que não acrescentava nada.
#
# O fade é curto de propósito. Um corte seco no texto lê como falha de render;
# meio segundo lê como decisão.
HOOK_SECONDS = 3.0
HOOK_FADE_S = 0.4
LEADING = 1.34                   # entrelinha do PRIME
STROKE = 3                       # contorno preto: legibilidade sobre qualquer placa
SCRIM_ALPHA = 150                # alfa no centro do scrim; some nas bordas

# Corpo da legenda, e este número é CALIBRADO, não escolhido.
#
# O `Fontsize` do ASS e o tamanho de fonte do PIL não valem a mesma coisa, e
# descobrir isso custou um clipe entregue. Quando a legenda passou de PNG do PIL
# para ASS, o número 73 ficou igual e a LETRA encolheu 45%: medido nos arquivos,
# 3,07%-4,06% da altura do quadro antes, 1,88% depois. Nada acusou, porque nada
# media a altura da tinta -- só o número da configuração, que não tinha mudado.
#
# Então o alvo passa a ser a altura da LETRA, que é o que se vê, e o corpo sai
# de uma tabela medida no próprio libass com a Anton, em 14/09:
#
#   corpo 73  -> 40px de letra = 2,08% do quadro | 21 maiúsculas em 403px
#   corpo 100 -> 56px          = 2,92%           | 553px
#   corpo 120 -> 67px          = 3,49%           | 664px   <- o alvo
#   corpo 137 -> 76px          = 3,96%           | 758px
#   corpo 150 -> 84px          = 4,38%           | 830px
#
# 3,5% é a faixa em que legenda de clipe vertical se lê num telefone na mão. Em
# 1920 isso é corpo 120, ou seja height // 16.
#
# E 26 caracteres continuam cabendo: 664px por 21 maiúsculas dá 822px por 26,
# contra 854px úteis. O limite de linha não precisou mudar junto.
CAPTION_ASS_DIVISOR = 16
CAPTION_INK_ALVO = 0.035        # o que a tabela acima mira, para o teste conferir
ASS_INK_POR_CORPO = 0.558       # 67px de letra por 120 de corpo, da tabela acima

# O divisor antigo, do tempo em que a legenda era PNG do PIL. Continua servindo
# à estimativa de linhas em `measure`, que olha pixels de um arquivo pronto.
CAPTION_SIZE_DIVISOR = 26


def caption_size(height):
    """O corpo de ASS que dá ~3,5% de altura de letra neste quadro."""
    return max(18, height // CAPTION_ASS_DIVISOR)

# Limites de uma cue. Um segmento do Whisper de sete segundos vira um bloco de
# seis linhas parado na tela: estes três números são o que impede isso.
MAX_CHARS_PER_LINE = 26
# ============================================================================
# O QUE BLOQUEIA UMA ENTREGA, E O QUE APENAS A ACOMPANHA
# ============================================================================
# Decisão do dono, 16/09/2026, depois do degrau 4 do demo falhar.
#
# O vídeo tinha tela dividida e uma faixa escura em duas bordas. `cross_check`
# leu a faixa como moldura e reprovou OS DOIS cortes; o `lote render` saiu com
# código 1 e NADA foi entregue -- com os dois arquivos prontos em disco, cada
# um com o seu hook.png e o seu .ass. A pessoa pediu dois clipes com legenda,
# os dois clipes com legenda existiam, e ela não recebeu nenhum.
#
# A regra passou a ser:
#
#   OBRIGACAO  -- gancho e legenda, e só. Sem uma delas não é o produto, e o
#                 clipe não sai. Isto é o que a pessoa pediu com todas as
#                 letras.
#   OBSERVACAO -- toda conferência visual ou de estilo: moldura, faixa escura,
#                 texto queimado da fonte, enquadramento, mosaico, margens,
#                 movimento, duração. O clipe SAI, e o agente conta em uma
#                 frase o que a ferramenta notou.
#
# O que NÃO mudou: regra escrita no briefing de uma campanha continua
# bloqueando, e ela vive noutro caminho (`blocking`, em warden.py), que só
# existe quando há campanha ligada ao pedido.
#
# Por que uma observação não é um portão fraco: ela chega à pessoa. O risco que
# os portões defendiam -- entregar um clipe torto sem ninguém saber -- vira
# entregar um clipe torto DIZENDO o que tem de torto, e quem decide re-cortar
# é quem pediu. O risco que eles criaram, medido duas vezes (15/09 e 16/09), é
# não entregar nada e o trabalho morrer em disco.
OBRIGACAO = "REJECT"
OBSERVACAO = "WARN"

MAX_LINES = 2
MAX_CUE_S = 2.2
MIN_CUE_S = 0.5

# Quanto uma cue pode passar de MAX_CUE_S para alcançar uma fronteira decente.
#
# Este número existe porque o primeiro desenho da quebra por fronteira estava
# errado e um teste mostrou: quando a cue inteira é palavra funcional -- "é o
# meu", "que o", "e dá para" -- não há para onde recuar, e recuar era a única
# saída que eu tinha escrito. Estender meia palavra resolve: "é o meu veredito,"
# fecha na vírgula.
#
# Então estender vem ANTES de recuar, e o teto é o preço. Um segundo a mais na
# tela ninguém percebe; "HATE THE" sozinho todo mundo percebe. Foi uma troca
# deliberada e é a única coisa que este bloco afrouxou: o portão de duração de
# cue foi de 2,5s para 3,4s para que o de fronteira pudesse ser absoluto.
#
# O que NÃO afrouxou, e é o que de fato protegia o quadro, é o orçamento de
# caracteres: duas linhas de 26. Uma cue só chega perto do teto quando a fala
# está lenta, e fala lenta significa pouco texto na tela -- o defeito de 14/09
# era um bloco de SEIS LINHAS por sete segundos, e são as linhas que o faziam.
CUE_TOLERANCIA_S = 1.0
MAX_CUE_S_TETO = MAX_CUE_S + CUE_TOLERANCIA_S

# Palavras que não podem FECHAR uma cue.
#
# Todas pedem um complemento que só vem na palavra seguinte, então a cue que
# termina nelas deixa a frase pendurada e o espectador lê um fragmento que não
# significa nada. Medido nos dois cortes de 14/09, no mosaico: "ESTÃO USANDO,
# JOGANDO / CONFORME", "O JOGO. DON'T HATE / THE PLAYER, HATE THE", "CARA, EU
# VOTARIA NO", "INTEIRA. E DÁ PARA". Quatro cues de dezenove.
#
# Preposição, artigo, conjunção e pronome átono, em português e em inglês,
# porque a legenda pode estar em qualquer das duas e a fala do podcast troca de
# língua no meio da frase. Verbo de ligação entra junto ("hate the" e "that is"
# quebram do mesmo jeito).
#
# Sem acento e em minúsculas: a consulta passa por `_nucleo`, que tira acento e
# pontuação, para "até," e "ATÉ" caírem no mesmo lugar.
NAO_FECHA_CUE = frozenset("""
de do da dos das dum duma dele dela deles delas disso disto daquilo
em no na nos nas num numa ao aos a as o os um uma uns umas
pelo pela pelos pelas por para pra pro com sem sob sobre entre até ate desde
após apos contra perante conforme durante mediante à às
e ou mas nem que se porque pois como quando enquanto embora caso
meu minha meus minhas teu tua seu sua seus suas nosso nossa nossos nossas
me te lhe lhes vos
qualquer cada todo toda todos todas outro outra outros outras mesmo mesma
of the an in on at to for and or but that with from into onto upon about
my your his her its our their this these those than
is are was were be been being am
when while because since as by although though unless until whether
whose which
i he she it we they you
eu ele ela eles elas você voce vocês voces nós
so very too quite muito mais bem tão tao
favorite favourite favorito favorita elementary primary secondary
vou vai vamos vão vao tenho tem temos está esta estão estao foi ser ter
nunca sempre já ja ainda também tambem só
can will would should could may might must shall
gonna wanna gotta going get gets got getting
each both either neither every
what how why where who whom
more most such then
""".split())

# AS SEIS LINHAS ACIMA SÃO INGLÊS, e entraram em 15/09/2026 porque a regra da
# língua mudou no mesmo dia: a legenda passou a sair na língua do VÍDEO, e a
# lista inteira tinha sido calibrada em português.
#
# Medido olhando o contact sheet de um clipe real, em inglês, que o portão de
# entrega APROVOU: as cues fechavam em "…We've known each" (falta "other"),
# "…what's been going" (falta "on") e "…as you can" (falta "see"). `and`, `the`
# e `been` já estavam na lista; `each`, `going`, `can` e os interrogativos não.
#
# Cada classe aqui vem de um caso visto na tela ou da classe gramatical que ele
# representa: modais e semi-auxiliares (`can`, `gonna`, `going`), quantificadores
# e correlatos (`each`, `both`, `either`), interrogativos (`what`, `how`) e
# comparativos (`more`, `such`, `then`). O que NÃO veio de um caso medido não
# entrou.

# As duas últimas linhas são de 15/09, e vêm de uma legenda que NÃO tinha
# pontuação nenhuma: a letra publicada de uma música. O contact sheet mostrou
# quatro cues fechando em palavra que a lista não conhecia --
#
#     "Nunca vou desistir de você / Nunca"      <- advérbio abrindo a oração
#     "vou te decepcionar Eu / nunca"           <- o mesmo, e "Eu" pendurado
#     "adeus Nunca / vou"                       <- auxiliar sem o principal
#
# -- e nenhuma delas é preposição, artigo ou conjunção. Auxiliar sem verbo
# principal ("vou", "vai", "tem", "foi") e advérbio que abre sintagma
# ("nunca", "sempre", "já") quebram exatamente como "HATE THE": cada metade é
# gramatical e nenhuma diz nada.
#
# Entram só as que o corpus do defeito mostrou mais as que o dono nomeou no
# mesmo pedido; "ser" e "ter" entraram por serem a outra metade de "vai ser" e
# "vai ter", que é o mesmo par. Não é uma lista de verbos auxiliares do
# português -- é o que apareceu.

# `when` estava faltando e o contact sheet de um render de teste mostrou: a cue
# saiu "My Pokemon journey started / when". A lista é uma lista, e a única forma
# de descobrir o que falta nela é olhar um clipe.
#
# 15/09, olhando dois mosaicos do teste final, mais dois: "I know typically Jake
# is / Pam's **favorite**" (a palavra que falta é "son") e "My Pokemon journey
# started when I was in / **elementary**" ("school"). Nenhuma das duas é
# preposição, artigo ou conjunção -- são adjetivos que pedem o substantivo -- e
# por isso a regra escrita não as pegava. Não dá para listar todo adjetivo;
# entram as que apareceram, pelo mesmo motivo e do mesmo jeito que `when`
# entrou. O que pega o resto continua sendo alguém olhando o mosaico.

# Pronomes que a lista acima proíbe, mas que fecham bem quando são o
# COMPLEMENTO de uma preposição que já está na cue.
#
# "…nunca vou desistir de você" é uma cue inteira e diz exatamente o que
# promete. A lista a reprovava por "você" ser pronome, e o efeito medido em
# 15/09 foi que a letra inteira não tinha UMA fronteira utilizável: o reflow
# recuava até "desistir", órfãos de "de você" cascateavam por todas as cues
# seguintes e o clipe saía com seis fragmentos.
#
# A lista tem duas coisas dentro: palavra que pede o que vem DEPOIS (de, que,
# the, vou) e pronome que pende de um sujeito ausente ("CARA, EU"). O pronome
# depois de preposição não é nenhuma das duas -- o que ele pedia está na
# palavra anterior, dentro da mesma cue. É a única exceção aqui, e ela é
# ESTREITA de propósito: exige a preposição imediatamente antes.
#
# Isto não afrouxa o portão. O portão continua reprovando "CARA, EU", "HATE
# THE" e "…Nunca vou"; o que deixou de reprovar é uma cue que sempre esteve
# certa.
# UMA LISTA POR LÍNGUA, e isso é um conserto, não organização.
#
# Até 15/09/2026 as duas línguas viviam numa lista só, e o comentário tratava
# isso como virtude ("a legenda pode estar em qualquer das duas"). É o defeito:
# `as` é preposição em português e conjunção em inglês; `you` é pronome em
# inglês e nada em português. Com as listas fundidas, a exceção casava o `as`
# português com o `you` inglês e fechava a cue em "…that as you", que é
# exatamente o "HATE THE" que ela existe para impedir. Reproduzido rodando o
# `reflow_cues` de verdade.
#
# A legenda tem UMA língua, e desde 15/09 ela é conhecida (é a do vídeo). A
# exceção agora exige que a preposição e o pronome venham do MESMO idioma, o
# que fecha o cruzamento sem precisar que ninguém passe a língua adiante.
PREPOSICOES_PT = frozenset("""
de do da dos das em no na nos nas a ao aos as à às para pra pro por
pelo pela pelos pelas com sem sobre sob entre até ate desde contra
""".split())

PRONOMES_COMPLEMENTO_PT = frozenset("""
mim ti si ele ela eles elas você voce vocês voces nós nos isso isto aquilo
""".split())

PREPOSICOES_EN = frozenset("""
of to in on at for with from by about into over under between
""".split())

PRONOMES_COMPLEMENTO_EN = frozenset("""
me him her it us them you
""".split())

# Mantidas como a união, porque outras partes do arquivo perguntam "é
# preposição?" sem se importar com a língua. Só a exceção precisa do par.
PREPOSICOES = PREPOSICOES_PT | PREPOSICOES_EN
PRONOMES_COMPLEMENTO = PRONOMES_COMPLEMENTO_PT | PRONOMES_COMPLEMENTO_EN

_PARES_DE_LINGUA = ((PREPOSICOES_PT, PRONOMES_COMPLEMENTO_PT),
                    (PREPOSICOES_EN, PRONOMES_COMPLEMENTO_EN))

# Conjunção coordenativa logo DEPOIS do pronome quer dizer que ele é metade de
# um par: "entre ele | e ela" parte "entre ele e ela" no meio. Medido em
# 15/09/2026.
_COORDENATIVAS = frozenset("e ou nem and or nor but mas".split())

# Palavras que TIPICAMENTE abrem um sintagma, e por isso são um bom lugar para
# a cue ANTERIOR fechar.
#
# Existe porque sem pontuação o código não tinha plano B. A ordem era:
# pontuação, estender, recuar -- e numa letra de música não há pontuação, então
# sobrava o relógio. Resultado medido: "Nunca vou te fazer" numa cue e "chorar"
# aberto na seguinte. Nenhuma lista razoável proíbe fechar em infinitivo
# ("fazer"), então a palavra culpada não é a que fecha: é a que ABRE a cue
# seguinte no meio de um sintagma.
#
# Virando a pergunta -- onde a próxima oração começa? -- a mesma letra fecha
# em "chorar", "adeus", "magoar", que é onde o cantor respira.
#
# Só entram as que apareceram na letra medida (advérbio de negação/frequência e
# pronome sujeito) e os subordinadores que abrem oração sem ambiguidade. "e" e
# "que" ficaram DE FORA: são frequentes demais e fechar antes de cada um
# picotaria a legenda, que é o defeito oposto.
ABRE_SINTAGMA = frozenset("""
nunca sempre já ja ainda também tambem só talvez agora
eu ele ela eles elas você voce vocês voces nós
mas porque quando então entao
never always still also i he she it we they you but because when
""".split())

# Pares que NÃO se partem: o que está entre eles é uma unidade, como uma
# expressão fixa.
#
# Medido em 15/09 no `.ass` de uma letra com vocal de apoio entre parênteses:
#
#     {\k27}nunca {\k14}vou {\k41}desistir\N{\k78}(Desistir   <- cue 1
#     {\k22}de {\k44}você)                                    <- cue 2
#
# "(Desistir de você)" foi partido no meio: parêntese aberto pendurado no fim
# de uma cue e o fechamento órfão na seguinte. O espectador lê um parêntese que
# nunca fecha, e o portão de entrega reprovou o clipe.
#
# Aspa reta `"` entra como alternância (abre/fecha com o mesmo caractere).
# Apóstrofo e aspa simples curva ficaram FORA de propósito: `don't` e `’` são
# apóstrofo em muito mais texto do que citação, e tratá-los como par inventaria
# grupos que não existem.
DELIMITADORES = {"(": ")", "[": "]", "{": "}", "«": "»", "“": "”"}
DELIMITADOR_SIMETRICO = '"'

# Expressões que não se partem no meio, custe o que custar.
#
# "crème de la crème" foi partido em 14/09 e "LA CREME DO MERCADO" ficou sozinho
# na tela por dois segundos. Cada metade é gramatical -- nenhuma regra de
# preposição pega isso -- e nenhuma das duas significa coisa alguma. Para quem
# corta, estas são uma palavra só.
#
# A lista é curta e é uma lista: não existe regra que descubra que "crème de la
# crème" é uma unidade. Cresce quando um corte mostrar a próxima.
EXPRESSOES_FIXAS = (
    "creme de la creme", "de facto", "de fato", "ou seja", "por exemplo",
    "de repente", "com certeza", "a gente", "cada vez mais",
    "de vez em quando", "ponto paragrafo", "status quo", "modus operandi",
    "dont hate the player", "hate the player", "hate the game",
    "a olho nu", "de cara", "no fim das contas", "em tese", "na prática",
)

# As duas cores do destaque palavra a palavra.
#
# No ASS o `\k` pinta progressivamente: a palavra ainda não falada sai na
# SecondaryColour e vira PrimaryColour no instante em que é dita. Então o
# "depois" é o Primary e o "antes" é o Secondary, que é o contrário do que o
# nome sugere e custa um render a quem inverter.
#
# Cores em &HAABBGGRR, que é ABGR e não RGB. Amarelo #FFE500 vira 00E5FF.
#
# Escolha do dono, 14/09: branco vira amarelo. O amarelo NÃO vem do corpus de
# aprovados -- o corpus é scenepack e não tem legenda corrida nenhuma, então
# não havia de onde tirar esta cor. Está aqui como decisão registrada, não como
# medida.
COR_FALADA = "&H0000E5FF"        # amarelo: a palavra que já foi dita
COR_POR_FALAR = "&H00FFFFFF"     # branco: a que ainda vem

# O acento do HOOK, e ele NÃO pode ser o amarelo da legenda.
#
# Pintar a linha inteira de colorido lê barato; o hook fica branco e uma ou duas
# palavras recebem o acento. E o acento tem de ser de outra matiz que o amarelo
# do `\k`, senão o olho lê as duas coisas como a mesma coisa e o destaque da
# fala perde o sentido.
#
# #FF3B30, vermelho-laranja. Como o amarelo da legenda, ele não sai do corpus de
# aprovados -- o corpus é scenepack e não tem nem hook com acento nem legenda
# corrida. É decisão registrada, e trocá-la é trocar esta linha.
COR_ACENTO_HOOK = (255, 59, 48, 255)
MARCA_ACENTO = "*"


def split_acento(texto):
    """(texto sem as marcas, índices das palavras que levam acento).

    A marcação é `*assim*`, e ela é do dono do hook: adivinhar qual palavra é a
    importante é o tipo de palpite que sai errado na frente do espectador. Fora
    das marcas o hook é branco, como sempre foi.

    Marcação torta é RECUSADA aqui, alto. Ver o comentário abaixo.
    """
    # Três formas de errar a marca saíam CALADAS, e cada uma medida:
    #
    #  - ímpar. `"GANHA *70 MIL POR MES"` devolvia um pedaço final órfão que
    #    caía no ramo `n % 2 == 1`, e TODO o resto do hook virava acento. O dono
    #    marcou uma palavra e recebeu quatro pintadas de vermelho.
    #  - asterisco literal. `"2*3"` perdia o asterisco no `split` e ninguém
    #    ficava sabendo: o texto que foi para a tela não era o texto escrito.
    #  - `**negrito**`. O segmento entre as duas marcas coladas é vazio, então a
    #    palavra não entrava no conjunto de acento nenhuma vez -- quem marcou com
    #    força recebeu MENOS destaque, não mais.
    #
    # Um hook é uma frase curta escrita à mão. Errar a marca é erro de digitação,
    # e a resposta a um erro de digitação é dizer qual é, não adivinhar qual era
    # a intenção e pintar o quadro com o palpite. Por isso RuntimeError, com a
    # frase certa dentro.
    #
    # O que ainda escapa, e escapa por não ter como distinguir: dois asteriscos
    # literais separados por espaço no meio da frase (`"lucro * imposto * 2"`)
    # são marcação válida para qualquer leitor. A checagem de marca colada em
    # caractere não-espaço pega os casos grudados, que são a maioria dos
    # literais; o resto exige uma fuga que este hook não tem.
    bruto = str(texto or "")
    marcas = [n for n, ch in enumerate(bruto) if ch == MARCA_ACENTO]
    escreva_assim = (f"The accent mark is a PAIR around the word: "
                     f"`{MARCA_ACENTO}like_this{MARCA_ACENTO}`, tight against "
                     f"the word and spaced on the outside. There is no literal "
                     f"asterisk in a hook -- if the asterisk is meant to show "
                     f"on screen, it cannot be here.")
    if len(marcas) % 2:
        raise RuntimeError(
            f"the hook has {len(marcas)} asterisk(s), an odd number, so one "
            f"accent mark was opened and never closed: {bruto!r}. " +
            escreva_assim)
    for abre, fecha in zip(marcas[0::2], marcas[1::2]):
        dentro = bruto[abre + 1:fecha]
        if not dentro.strip():
            raise RuntimeError(
                f"há um par de marcas de acento sem palavra nenhuma dentro em "
                f"{bruto!r}. `{MARCA_ACENTO * 2}assim{MARCA_ACENTO * 2}` não é "
                f"negrito: são dois pares vazios, e a palavra fica SEM acento. "
                + escreva_assim)
        antes = bruto[abre - 1] if abre > 0 else " "
        depois = bruto[fecha + 1] if fecha + 1 < len(bruto) else " "
        if (dentro[0].isspace() or dentro[-1].isspace()
                or not (antes.isspace() or antes in "(\u201c\u2018\"'\u00bf\u00a1")
                or not (depois.isspace() or depois in ".,;:!?)\u201d\u2019\"'\u2026-")):
            raise RuntimeError(
                f"uma marca de acento não está colada na palavra que ela marca, "
                f"em {bruto!r} (o trecho marcado é {dentro!r}). Isso é quase "
                f"sempre asterisco literal lido como marcação. " + escreva_assim)
    partes = bruto.split(MARCA_ACENTO)
    limpas, acentuadas = [], set()
    for n, parte in enumerate(partes):
        for palavra in parte.split():
            if n % 2 == 1:                 # entre um par de marcas
                acentuadas.add(len(limpas))
            limpas.append(palavra)
    return " ".join(limpas), acentuadas

# A varredura da faixa de texto queimado do material. Fatias de 2% da altura, e
# um teto de 22%: legenda de acervo e disclaimer moram no quinto de baixo de um
# quadro -- medido no material de teste, a faixa real ia de 0 a 14%.
BAND_SLICE = 0.02
BAND_CAP = 0.22

# O degradê do rodapé, medido no `rodape.png` do PRIME: a transição ocupa 26% da
# faixa e o alfa satura em 252. Um degradê que só chega a 93% deixa amarelo
# saturado passar por baixo, que é o que o disclaimer do material é.
# O perfil do rodapé, e por que ele mudou em 15/09.
#
# O PRIME satura o alfa em 252 sobre 74% da faixa: uma tarja preta com a borda
# de cima suavizada. Isso funciona lá, onde a faixa cobre o terço inferior de um
# scenepack e não há imagem embaixo dela que alguém queira ver.
#
# Aqui virou defeito. Medido linha a linha no clipe "colonizadores": tarja de
# **289px**, 15% do quadro, em TODOS os quadros, sobre material limpo -- 0 de 8
# quadros acima do limiar, média 1,5x contra um piso de 1,35x. O código chamava
# de degradê, o comentário prometia "cobre o texto de baixo sem virar tarja", e
# o que saía era tarja.
#
# O perfil agora é degradê de verdade: o alfa sobe da borda superior da faixa
# até o pé do quadro sem nunca saturar, e o teto é 200 de 255. Cobrir a legenda
# do acervo não exige apagar a imagem: exige rebaixar o contraste dela o
# bastante para a nossa ganhar, e 200 já faz isso -- o texto de baixo fica
# ilegível e a imagem continua existindo.
FOOTER_FADE = 0.55
FOOTER_ALPHA = 200


def usable_width(width):
    """A largura que o texto pode ocupar num quadro desta largura."""
    return int(round(width * (REF_USABLE / REF_W)))


def font_available():
    return os.path.isfile(FONT_PATH)


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        return Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        raise RuntimeError(
            "Pillow não está instalado, e todo texto deste renderizador é PNG do "
            "PIL. Sem ele não há hook nem legenda para queimar.")


def _font(size):
    _I, _D, _F, ImageFont = _pil()
    if not os.path.isfile(FONT_PATH):
        raise RuntimeError(
            f"a fonte não está em {FONT_PATH}. Ela é do repo justamente para "
            "nunca cair numa fallback do sistema, que é o que deixou o texto com "
            "peso errado no clipe reprovado.")
    return ImageFont.truetype(FONT_PATH, size)


# ------------------------------------------------------------------ medir texto

def fit_lines(text, max_width, size, min_size, max_lines=MAX_LINES):
    """(linhas, corpo, coube) para `max_width`, medidos antes de desenhar.

    É a correção do defeito 2.1. `drawtext` não quebra linha e não ajusta corpo:
    com 1080px de quadro o corpo saía 49px, e 44 caracteres nesse corpo passavam
    de 1100px contra 1080px de frame, então `x=(w-text_w)/2` ficava negativo e o
    ffmpeg cortava os dois lados sem erro nenhum. Qualquer hook com mais de uns
    35 caracteres saía cortado, e sempre saiu.

    Aqui o texto é medido com a fonte real. Tenta quebrar em até `max_lines`
    linhas equilibradas; se a linha mais larga ainda passar, reduz o corpo de 2
    em 2 até caber ou bater no piso. O piso existe porque texto que ninguém lê no
    celular não é melhor que texto cortado.

    Devolve `(linhas, corpo, coube)`. `coube=False` quando nem no piso o texto
    inteiro entra em `max_lines` -- e aí as linhas voltam TRUNCADAS. Quem chama
    tem de tratar isso como recusa, nunca como resultado: entregar um hook ao
    qual faltam palavras, sem dizer, é pior que entregá-lo cortado na borda,
    porque cortado se vê no contact sheet e faltando não se vê.
    """
    _I, ImageDraw, _F, _Ft = _pil()
    from PIL import Image
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    words = (str(text or "").upper()).split()
    if not words:
        return [], size, True

    def widest(lines, font):
        return max(probe.textlength(l, font=font) for l in lines)

    def wrap(font, limit):
        """Quebra gulosa em até `limit` linhas, equilibrando a última."""
        lines, cur = [], ""
        for w in words:
            cand = (cur + " " + w).strip()
            if cur and probe.textlength(cand, font=font) > max_width:
                lines.append(cur)
                cur = w
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines

    while True:
        font = _font(size)
        lines = wrap(font, max_lines)
        if len(lines) <= max_lines and widest(lines, font) <= max_width:
            return lines, size, True
        if size - 2 < min_size:
            # Não coube nem no piso. As linhas voltam truncadas E `coube=False`:
            # o truncamento é visível para quem chama, que recusa. Antes isto
            # devolvia o texto cortado calado, e um hook de 158 caracteres saía
            # com 48 deles faltando sem uma linha de aviso.
            font = _font(min_size)
            lines = wrap(font, max_lines)
            return lines[:max_lines], min_size, len(lines) <= max_lines
        size -= 2


# ------------------------------------------------------------------ desenhar

def _gradient(width, column):
    """Uma faixa preta de `width` px cujo alfa vem de `column`, uma linha por y.

    Montada como uma coluna de 1px e esticada, em vez de um laço por pixel: um
    scrim de 1080x260 são 280 mil atribuições em Python puro, e um clipe de 20s
    tem quinze deles. Era metade do tempo de um render.
    """
    Image, _D, _F, _Ft = _pil()
    alpha = Image.new("L", (1, len(column)))
    alpha.putdata(column)
    alpha = alpha.resize((width, len(column)), Image.NEAREST)
    band = Image.new("RGBA", (width, len(column)), (0, 0, 0, 0))
    band.putalpha(alpha)
    return band


def text_png(lines, path, width, height, y_center, size, scrim=True, acento=None):
    """O texto como PNG transparente do tamanho do quadro. Porte do `png_texto`.

    Caixa alta, fonte condensada de peso alto, entrelinha de 1,34, contorno preto
    de 3px e uma sombra de peso separada. O scrim é gradiente e nunca tarja: o
    alfa é máximo no centro do texto e some nas bordas, porque branco sobre placa
    clara é o pior par de contraste possível e uma tarja dura de largura total lê
    como marca d'água de app gratuito -- que é exatamente o defeito 2.2.

    Devolve `(caminho, y)`: a imagem salva é só a faixa que o texto ocupa, e `y`
    é onde ela entra no quadro. Salvar 1080x1920 por cue fazia o ffmpeg decodificar
    quinze streams RGBA de quadro inteiro num clipe de 20s -- medido, era quase
    todo o tempo do render. A faixa recortada é a mesma imagem com um décimo dos
    pixels.
    """
    Image, ImageDraw, _F, _Ft = _pil()
    lines = [l for l in lines if str(l).strip()]
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if not lines:
        im.save(path)
        return path, 0
    font = _font(size)
    leading = int(size * LEADING)
    y = int(y_center) - (len(lines) * leading) // 2

    if scrim:
        block = len(lines) * leading
        pad = int(leading * 0.85)
        y0, y1 = max(y - pad, 0), min(y + block + pad, height)
        if y1 > y0:
            mid = (y1 - y0) / 2
            column = [int(SCRIM_ALPHA * max(0.0, 1.0 - (abs(yy - mid) / mid) ** 1.7))
                      for yy in range(y1 - y0)]
            im.alpha_composite(_gradient(width, column), (0, y0))
            d = ImageDraw.Draw(im)

    # Sem acento, a linha inteira é desenhada de uma vez -- que é como sempre
    # foi, e é o caminho que o kerning trata melhor. Com acento, a linha é
    # desenhada palavra a palavra, e o x de cada uma sai da largura do PREFIXO
    # medido na linha inteira, não da soma das palavras: somar larguras ignora o
    # kerning e o texto abre alguns pixels a cada palavra.
    indice = 0
    for line in lines:
        t = str(line).upper()
        largura = d.textlength(t, font=font)
        x0 = (width - largura) / 2
        palavras = t.split()
        if not acento:
            d.text((x0, y + 4), t, font=font, fill=(0, 0, 0, 110))  # sombra de peso
            d.text((x0, y), t, font=font, fill=(255, 255, 255, 255),
                   stroke_width=STROKE, stroke_fill=(0, 0, 0, 255))
            indice += len(palavras)
        else:
            for k, palavra in enumerate(palavras):
                prefixo = " ".join(palavras[:k])
                dx = d.textlength(prefixo + " ", font=font) if prefixo else 0.0
                cor = (COR_ACENTO_HOOK if indice in acento
                       else (255, 255, 255, 255))
                d.text((x0 + dx, y + 4), palavra, font=font, fill=(0, 0, 0, 110))
                d.text((x0 + dx, y), palavra, font=font, fill=cor,
                       stroke_width=STROKE, stroke_fill=(0, 0, 0, 255))
                indice += 1
        y += leading
    box = im.getbbox()                    # só o que não é transparente
    if not box:
        im.save(path)
        return path, 0
    top, bottom = box[1], box[3]
    im.crop((0, top, width, bottom)).save(path)
    return path, top


def footer_png(path, width, height, band=None, bottom=None, span=None):
    """O degradê que cobre a legenda queimada do próprio acervo.

    Porte do `rodape.png` do PRIME, gerado em vez de copiado para servir a um
    quadro de qualquer tamanho. Preto que sobe do rodapé e some: cobre o texto de
    baixo sem virar tarja. A regra que o PRIME anota vale aqui: cobrir marca
    d'água de OUTRO clipador é proibido; a legenda do próprio acervo não é isso.

    O perfil é o do `rodape.png` do PRIME, medido nele e não escolhido aqui: a
    transição ocupa 26% da faixa e o alfa satura em 252. O que muda é o tamanho
    da faixa. O PRIME cobre 35% da altura porque lá o texto mora no centro do
    quadro e o terço de baixo pode escurecer inteiro; aqui a nossa própria
    legenda fica por volta de 25% de baixo, então a faixa tem de parar antes
    dela -- quem chama passa `band` e essa é a conta que ele faz.

    `bottom` é o y em que a faixa ACABA, e existe porque ela nem sempre acaba no
    pé do quadro. No clipe dividido a imagem da fonte é a faixa de cima e o pé
    dela fica 1080px acima do pé do quadro; ancorar aqui, calado, foi como o
    degradê de 18/09/2026 cobriu a faixa da pessoa inteira. `span` são as
    colunas que a faixa ocupa, pelo mesmo motivo: a tela tem 996 de 1080.
    O padrão dos dois é o quadro inteiro, que é o caminho normal.
    """
    Image, _D, _F, _Ft = _pil()
    bottom = height if bottom is None else int(bottom)
    bottom = max(1, min(bottom, height))
    band = int(band or height * 0.20)
    band = max(1, min(band, bottom))
    # Sobe até `FOOTER_ALPHA` ao longo de `FOOTER_FADE` da faixa e depois
    # continua subindo devagar até o pé, em vez de travar num platô. Nenhuma
    # linha chega a opaca: a menor luminância que sobra é o que separa "cobri o
    # texto" de "apaguei a imagem".
    fade = max(1, int(band * FOOTER_FADE))
    column = []
    for yy in range(band):
        if yy < fade:
            a = FOOTER_ALPHA * (yy / fade) ** 1.15
        else:
            # do fim da rampa ao pé do quadro, mais 12% de alfa, no máximo
            resto = (yy - fade) / max(1, band - fade)
            a = FOOTER_ALPHA + (255 - FOOTER_ALPHA) * 0.12 * resto
        column.append(int(min(228, a)))
    # Só a faixa, e o y em que ela entra -- pelo mesmo motivo do `text_png`.
    faixa = _gradient(width, column)
    if span:
        # Fora das colunas da imagem da fonte não há texto para cobrir, e o que
        # há é fundo borrado: escurecê-lo desenharia duas abas que acabam no
        # nada. A faixa continua do tamanho do quadro para que quem desenha
        # continue a pôr em x=0 -- o que muda é o alfa.
        x0, x1 = (max(0, int(span[0])), min(width, int(span[1])))
        vazio = Image.new("L", (width, len(column)), 0)
        vazio.paste(faixa.split()[3].crop((x0, 0, x1, len(column))), (x0, 0))
        faixa.putalpha(vazio)
    faixa.save(path)
    return path, bottom - band


# ------------------------------------------------------------------ legendas

def _sem_acento(texto):
    base = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


def _palavra(palavra):
    """A palavra sem pontuação e em minúsculas, COM acento, para consulta.

    "ATÉ," e "até" têm de cair no mesmo lugar, senão a lista de palavras que não
    fecham cue só pega a metade das ocorrências -- e a metade que ela deixa
    passar é justamente a que vem antes de vírgula, que é onde a cue fecha.

    Mas o acento FICA, e isso custou um render para ficar claro: tirando o
    acento, **"é" vira "e"** -- o verbo vira conjunção. A lista proíbe fechar em
    "e", e com a conflação ela passou a proibir fechar em "é" também, que é uma
    das palavras mais comuns do português e fecha ideia sem problema nenhum
    ("o cara é bom, é"). Duas palavras diferentes, duas perguntas diferentes.
    """
    return re.sub(r"[^0-9a-zà-öø-ÿ]+", "", str(palavra).lower())


def _nucleo(palavra):
    """A palavra reduzida a letras sem acento. Serve para CONTAR, não para saber
    que palavra é: a contagem de vogais não se importa com acento, a identidade
    da palavra se importa muito."""
    return re.sub(r"[^a-z0-9]+", "", _sem_acento(palavra))


# Pontuação que fecha uma ideia, e pontuação que só separa. As duas servem de
# fronteira para cortar a cue; a primeira é melhor, mas nenhuma das duas é
# obrigatória -- fala corrida de podcast passa minutos sem nenhuma.
_FECHA = ".!?…"
_SEPARA = ",;:"


def _pontua(palavra):
    """(fecha ideia, separa) pela pontuação com que a palavra termina."""
    limpa = str(palavra).rstrip("\"')]»”’")
    return limpa.endswith(tuple(_FECHA)), limpa.endswith(tuple(_SEPARA))


def silabas(palavra):
    """Quantos núcleos vocálicos a palavra tem -- o tempo que ela ocupa na boca.

    Não é um separador silábico e não precisa ser. A pergunta aqui é quanto de
    uma cue de dois segundos cabe a cada palavra, e repartir por caractere ou em
    partes iguais dá a "que" o mesmo tempo que a "constituição" -- que é o que
    faz o destaque andar na frente ou atrás da fala e ficar visivelmente errado.

    Grupos de vogais, então: "que" conta 1 e "constituição" conta 4 -- a
    separação silábica de verdade dá 5 ("cons-ti-tu-i-ção"), e essa diferença
    não muda o reparto o bastante para pagar um separador silábico de verdade.
    """
    nucleo = _nucleo(palavra)
    if not nucleo:
        return 1
    return max(1, len(re.findall(r"[aeiouy]+", nucleo)))


def _grupos_delimitados(words):
    """[(primeira, última)] dos trechos entre parênteses, colchetes ou aspas.

    Índices sobre a lista de palavras: o par cobre da palavra que traz o sinal
    de abertura até a que traz o fechamento, inclusive. Abertura sem
    fechamento não vira grupo -- o texto continua depois do fim da janela e não
    há unidade nenhuma para proteger.
    """
    grupos, pilha, aspa = [], [], None
    for i, w in enumerate(words):
        for ch in str(w):
            if ch == DELIMITADOR_SIMETRICO:
                if aspa is None:
                    aspa = i
                else:
                    grupos.append((aspa, i))
                    aspa = None
            elif ch in DELIMITADORES:
                pilha.append((DELIMITADORES[ch], i))
            elif pilha and ch in DELIMITADORES.values():
                for n in range(len(pilha) - 1, -1, -1):
                    if pilha[n][0] == ch:
                        grupos.append((pilha[n][1], i))
                        del pilha[n:]
                        break
    return [(a, b) for a, b in grupos if b > a]


def _e_infinitivo(palavra):
    """Um infinitivo português, grosseiramente. Só para a guarda abaixo.

    Não precisa ser um analisador: precisa separar "…para você ENTENDER" de
    "…desistir de você". Termina em -ar/-er/-ir e é longo o bastante para não
    casar com "par", "ver", "ir".
    """
    p = _palavra(palavra)
    return len(p) > 4 and p[-2:] in ("ar", "er", "ir")


def _fecha_apesar_da_lista(words, k):
    """Se a palavra `k` está na lista proibida mas mesmo assim fecha bem.

    O caso é um só: pronome logo depois de preposição. O que a lista acusa é a
    palavra pedir o que vem DEPOIS; aqui o que ela pedia está na palavra
    anterior. "…desistir de você" fecha, e sem esta exceção uma letra de música
    não tem uma única fronteira utilizável.

    TRÊS GUARDAS, e as três saem de casos reproduzidos em 15/09/2026 rodando o
    `reflow_cues` de verdade, não de leitura de código:

    1. **Mesma língua.** `as`(pt) + `you`(en) fechava "…that as you". Ver o
       comentário das listas.
    2. **O próximo não é infinitivo.** Em "…para você entender", o pronome é o
       SUJEITO do infinitivo seguinte, não o objeto da preposição: fechar ali
       deixa "entender" órfão. É o mesmo defeito que `ABRE_SINTAGMA` conserta,
       reintroduzido duas listas adiante.
    3. **O próximo não é conjunção coordenativa.** "entre ele | e ela" parte
       "entre ele e ela" no meio.

    Em todos os três casos a cue fecha melhor SEM a exceção -- medido, não
    suposto.
    """
    if k <= 0 or k >= len(words):
        return False
    palavra = _palavra(words[k])
    anterior = _palavra(words[k - 1])
    if palavra not in PRONOMES_COMPLEMENTO:
        return False
    # DUAS FORMAS de o pronome ter o que pedia ATRÁS dele, e as duas foram
    # medidas em 15/09/2026:
    #
    #   a) depois de preposição da MESMA língua -- "…desistir de você";
    #   b) depois de uma palavra de conteúdo, tipicamente um verbo --
    #      "…tell a lie and hurt you", que é uma frase inteira e que o portão
    #      reprovou por engano até esta linha existir, derrubando o lote todo.
    #
    # O (b) é "a palavra anterior não está, ela própria, na lista de quem pede o
    # que vem depois". É isso que separa "hurt you" (fecha) de "as you" (não
    # fecha): `as` está na lista, `hurt` não.
    depois_de_preposicao = any(anterior in preps and palavra in prons
                               for preps, prons in _PARES_DE_LINGUA)
    depois_de_conteudo = anterior not in NAO_FECHA_CUE
    if not (depois_de_preposicao or depois_de_conteudo):
        return False
    if k + 1 < len(words):
        seguinte = _palavra(words[k + 1])
        if seguinte in _COORDENATIVAS or _e_infinitivo(words[k + 1]):
            return False
    return True


def _fronteiras(words):
    """(onde NÃO se pode fechar a cue, onde há fronteira de pontuação).

    Índices sobre a lista de palavras. Um índice em `proibido` é uma palavra
    depois da qual a cue não pode terminar; um em `pontuada` é onde ela termina
    bem. Uma expressão fixa marca como proibidos todos os seus índices menos o
    último: para quem corta, ela é uma palavra só.
    """
    proibido, pontuada = set(), set()
    for i, w in enumerate(words):
        fecha, separa = _pontua(w)
        funcional = _palavra(w) in NAO_FECHA_CUE
        if fecha or separa:
            pontuada.add(i)
        # O `elif` daqui fazia os dois conjuntos serem DISJUNTOS por construção,
        # e com isso o guarda `k not in proibido` do passo 1 de
        # `_ajusta_fronteira` nunca podia ser falso: era código morto. Medido,
        # a cue fechava em "ele me disse que," -- o mesmo fragmento pendurado de
        # 14/09, com um sinal de pontuação a mais.
        #
        # Ponto final perdoa: "...não sei o que." fecha uma ideia de verdade.
        # Vírgula não perdoa: ela separa, e o que vem depois dela é justamente o
        # complemento que a palavra funcional está pedindo.
        if funcional and not fecha and not _fecha_apesar_da_lista(words, i):
            proibido.add(i)
    nucleos = [_palavra(w) for w in words]
    for expressao in EXPRESSOES_FIXAS:
        alvo = [_palavra(p) for p in expressao.split()]
        n = len(alvo)
        if n < 2:
            continue
        for i in range(len(nucleos) - n + 1):
            if nucleos[i:i + n] == alvo:
                for k in range(i, i + n - 1):
                    proibido.add(k)
                    pontuada.discard(k)
    # Parênteses e aspas pelo mesmo princípio da expressão fixa: para quem
    # corta, o trecho entre eles é uma palavra só. Fechar em qualquer ponto
    # interno deixa o delimitador aberto na tela -- foi o `(Desistir` de 15/09.
    for a, b in _grupos_delimitados(words):
        # Um grupo maior que a tela inteira não tem o que ser protegido: ele
        # vai ser partido de qualquer jeito, e proibir todas as fronteiras
        # internas só apagaria a pontuação que existe DENTRO dele. É também o
        # que segura uma aspa solta na origem, que pareia com a próxima aspa
        # dez frases adiante e viraria um grupo de trinta palavras.
        if not cabe_na_tela(" ".join(words[a:b + 1])):
            continue
        for k in range(a, b):
            proibido.add(k)
            pontuada.discard(k)
    return proibido, pontuada


def _alcance(i, j, words, marco, max_cue_s):
    """O último índice que a cue que começa em `i` alcança sem estourar nada.

    "Nada" são os dois orçamentos que já existiam: o teto de tempo com a
    tolerância, e duas linhas de 26 caracteres.
    """
    teto = max_cue_s + CUE_TOLERANCIA_S
    limite, k = j, j + 1
    while k < len(words):
        if marco[k + 1] - marco[i] > teto:
            break
        if not cabe_na_tela(" ".join(words[i:k + 1]), MAX_CHARS_PER_LINE,
                            MAX_LINES):
            break
        limite = k
        k += 1
    return limite


def _fronteira_sintatica(i, j, words, marco, proibido, max_cue_s):
    """Onde fechar quando não há UM sinal de pontuação para seguir.

    É o plano B que faltava, e a falta dele é o que dava zero clipe em fonte
    sem pontuação -- música, live, podcast transcrito, legenda automática.
    Sem pontuação a ordem antiga caía direto no relógio, e o relógio corta no
    meio do sintagma: "Nunca vou te fazer" numa cue, "chorar" na seguinte.

    A pergunta aqui é a outra ponta da mesma coisa: em vez de "esta palavra
    pode fechar?", **onde começa a próxima oração?**. Fechar imediatamente
    antes de uma palavra que abre sintagma deixa as duas metades dizendo
    alguma coisa, que é o critério que o próprio REJECT usa.

    Dentro da janela que os orçamentos permitem, e escolhendo a fronteira mais
    próxima do que o relógio pediu: andar muito para trás pica a legenda, e
    andar muito para a frente é o bloco parado que o item 1 combate.
    """
    piso = i + (j - i) // 2
    limite = _alcance(i, j, words, marco, max_cue_s)
    candidatos = [k for k in range(piso, limite + 1)
                  if k + 1 < len(words)
                  and k not in proibido
                  and _palavra(words[k + 1]) in ABRE_SINTAGMA]
    if not candidatos:
        return None
    # Empate entre recuar e estender vai para a frente: a cue maior é a que
    # leva o sintagma inteiro.
    return min(candidatos, key=lambda k: (abs(k - j), -k))


def _ajusta_fronteira(i, j, words, marco, proibido, pontuada, budget,
                      max_cue_s=MAX_CUE_S):
    """Onde a cue que começa em `i` deve realmente fechar, dado o corte por tempo.

    `j` é onde o relógio mandou fechar. Três correções, nesta ordem, e a ordem
    é o que um teste corrigiu:

    1. Se existe pontuação na metade final da cue, fecha ali. É a fronteira que
       o próprio falante marcou, e vale mais que o relógio. A metade final é um
       piso de propósito: fechar numa vírgula que está na terceira palavra de
       uma cue de dez transforma a legenda em picotes.
    2. Se `j` pede complemento, ESTENDE até a primeira palavra que fecha bem,
       respeitando o teto de tempo e o de caracteres. Estender é quase sempre a
       resposta certa -- "é o meu" vira "é o meu veredito," com uma palavra.
    3. Só então RECUA. Recuar encurta a cue de verdade, e às vezes demais.

    Quando as três falham, o trecho não tem fronteira nenhuma: fica o corte do
    relógio, e `finais_pendurados` conta isso para quem entrega.
    """
    piso = i + (j - i) // 2
    for k in range(j, piso - 1, -1):
        if k in pontuada and k not in proibido:
            return k
    # Passo 1b, e ele só existe quando o passo 1 não tinha COM O QUE trabalhar:
    # nenhum sinal de pontuação em nenhuma palavra da janela. Ficou atrás do
    # passo 1 e na frente de tudo o mais de propósito -- onde há pontuação ela
    # continua mandando, e nada do que já estava medido muda de resposta.
    if not any(k in pontuada for k in range(i, min(j + 1, len(words)))):
        escolha = _fronteira_sintatica(i, j, words, marco, proibido, max_cue_s)
        if escolha is not None:
            return escolha
    if j not in proibido:
        return j
    teto = max_cue_s + CUE_TOLERANCIA_S
    k = j + 1
    while k < len(words):
        if marco[k + 1] - marco[i] > teto:
            break
        if not cabe_na_tela(" ".join(words[i:k + 1]), MAX_CHARS_PER_LINE,
                            MAX_LINES):
            break
        if k not in proibido:
            return k
        k += 1
    k = j
    while k > i and k in proibido:
        k -= 1
    return j if k in proibido else k


# Marcação decorativa da legenda automática: some antes de virar cue.
#
# Medido em 15/09/2026, olhando o contact sheet de um clipe real em inglês: o
# YouTube marca trecho musical com U+266A e afins, e eles estavam sendo
# QUEIMADOS na tela -- "…let you down [nota] [nota]", "(Give you up) [nota]".
# Não são palavras: comem o orçamento de 26 caracteres da linha e empurram
# palavra de verdade para fora. O pior caso visto foi uma cue cujo conteúdo real
# eram duas palavras separadas por ruído.
#
# O que sai: os símbolos musicais (U+2669..U+266C) e os rótulos de som entre
# colchetes que a legenda automática do YouTube usa em inglês e em português.
#
# `risadas` e `[ __ ]` entraram depois, e a razão é medida: no primeiro teste
# real pela linha da Plow, 15/09/2026, um vídeo em português trouxe `[risadas]`
# e `[ __ ]` -- o marcador de palavrão censurado do YouTube. Os dois passaram
# por esta limpeza (a lista só tinha `risos`, e `_` não estava em lugar nenhum),
# caíram na checagem de linha suspeita, e travaram a legenda dos DOIS clipes do
# pedido. O agente teve de passar `--keep` para cada um. Marcação que sobrevive
# aqui vira trabalho lá na frente.
# O que FICA: parêntese comum. "(Give you up)" é letra de verdade, e o trabalho
# de 15/09 tornou parêntese uma unidade indivisível -- limpar o parêntese aqui
# desfaria aquilo.
_SIMBOLOS_MUSICAIS = "\u2669\u266a\u266b\u266c\u266d\u266e\u266f\u2192\u25ba"
_RE_MARCACAO = re.compile(
    r"\[\s*(?:m[uú]sic[ao]|music|applause|aplausos?|palmas|laughter|"
    r"ris[oa]s?|risadas?|gargalhadas?|sound|som|silence|sil[êe]ncio|"
    r"inaudible|ininteligível|ininteligivel|_+|"
    r"[" + _SIMBOLOS_MUSICAIS + r"\s]+)\s*\]",
    re.IGNORECASE)


def _limpa_marcacao(texto):
    r"""Tira marcação decorativa de um texto. Devolve "" se não sobrar palavra.

    Duas coisas entraram aqui em 16/09/2026, medidas na legenda publicada em
    português de uma live do YouTube (706 cues):

    `\h` -- o espaço duro do próprio YouTube, escrito com barra e agá dentro do
    arquivo. Sem ele, `[\h__\h]` não casava com o padrão de `[ __ ]` e o
    palavrão censurado chegava à tela E à checagem de linha suspeita. Ele vira
    espaço antes de qualquer outra coisa.

    `>>` -- o marcador de troca de falante, em 184 das 706 cues. Ninguém quer
    duas setas na tela, elas comem o orçamento de 26 caracteres da linha, e
    enquanto sobrevivessem aqui cada uma delas era uma linha "suspeita" a mais.
    """
    limpo = re.sub(r"\\h", " ", texto or "")
    limpo = _RE_MARCACAO.sub(" ", limpo)
    limpo = limpo.replace(">>", " ")
    limpo = "".join(" " if ch in _SIMBOLOS_MUSICAIS else ch for ch in limpo)
    # Parêntese que ficou vazio porque só tinha símbolo dentro.
    limpo = re.sub(r"\(\s*\)|\[\s*\]", " ", limpo)
    return " ".join(limpo.split())


def _sem_marcacao(segments):
    r"""Os mesmos segmentos, sem marcação decorativa. Sem palavra, sem segmento.

    Limpa o texto do segmento E a lista de palavras, porque é dela que sai o
    `\k` de cada palavra: deixar o símbolo nas palavras poria de volta na tela
    o que o texto tirou.
    """
    saida = []
    for seg in segments:
        if not isinstance(seg, dict):
            saida.append(seg)
            continue
        novo = dict(seg)
        novo["text"] = _limpa_marcacao(seg.get("text"))
        palavras = seg.get("words")
        if isinstance(palavras, list):
            limpas = []
            for w in palavras:
                if not isinstance(w, dict):
                    limpas.append(w)
                    continue
                texto = _limpa_marcacao(w.get("word") or w.get("text") or "")
                if not texto:
                    continue
                nova = dict(w)
                if "word" in nova:
                    nova["word"] = texto
                else:
                    nova["text"] = texto
                limpas.append(nova)
            novo["words"] = limpas
            if not limpas:
                continue
        if not novo["text"]:
            continue
        saida.append(novo)
    return saida


def reflow_cues(segments, max_chars=MAX_CHARS_PER_LINE, max_lines=MAX_LINES,
                max_cue_s=MAX_CUE_S, descartes=None):
    """Cues que cabem na tela, passam rápido e fecham onde a frase fecha.

    É a correção do defeito 2.3, mais a de 14/09. Cada segmento do Whisper
    virava uma cue inteira, então um segmento de sete segundos com trinta
    palavras virava um bloco de seis linhas parado na tela por sete segundos,
    cobrindo o rosto do peito ao queixo. E mesmo depois de picado por tempo, o
    corte caía onde o relógio mandasse: "HATE THE" e "CARA, EU VOTARIA NO".

    A ordem importa e custou dois renders para ficar certa:

    1. JUNTAR primeiro. Uma legenda de origem -- e principalmente a automática do
       YouTube -- quebra a frase em blocos de 2 a 5 segundos que não respeitam
       pontuação nenhuma. Subdividir esses blocos produzia cues de duas palavras
       ("o MBL," sozinho na tela). Blocos contíguos viram um trecho só de fala.
    2. DAR TEMPO A CADA PALAVRA, por sílaba e não em partes iguais. É o que faz
       o destaque do `\\k` andar junto com a boca, e é o relógio do passo 3.
    3. CORTAR por tempo, e então MOVER o corte para a fronteira mais próxima:
       pontuação se houver, e nunca depois de palavra que pede complemento.
    4. QUEBRAR em linhas, já com o texto definido.

    Cada cue leva `words`, com o tempo de cada palavra, que é de onde sai o
    `\\k`. Nenhuma palavra se perde e nenhuma cue se sobrepõe à seguinte.

    `descartes` é uma lista opcional: o que `_merge_spans` teve de jogar fora
    por não ter tempo legível é anexado nela, porque uma fala que some da
    legenda sem ninguém dizer é a mesma falha que este arquivo inteiro combate.
    """
    segments = _sem_marcacao(segments)
    spans = _merge_spans(segments, descartes=descartes)
    out = []
    budget = max_chars * max_lines
    for span in spans:
        words = [w for w, _a, _b in span]
        if not words:
            continue
        # `marco[k]` é quando a palavra k começa, e ele vem do tempo da LINHA
        # de origem daquela palavra -- não de uma repartição linear sobre o
        # trecho fundido inteiro.
        #
        # Repartir sobre o trecho fundido foi o defeito. Medido nos dois clipes
        # entregues: dentro de cada trecho o erro crescia de 1,7 a 1,9 segundos
        # por cue e ZERAVA na fronteira do trecho seguinte -- dente de serra,
        # que é a assinatura de erro de taxa, não de deslocamento. O SRT já
        # trazia o tempo de cada linha, com precisão de fração de segundo, e a
        # fusão jogava esse tempo fora para redistribuir em linha reta sobre
        # dez segundos de fala que não é reta.
        marco = [span[0][1]] + [b for _w, _a, b in span]
        proibido, pontuada = _fronteiras(words)
        i = 0
        while i < len(words):
            j = i
            while j + 1 < len(words):
                if marco[j + 2] - marco[i] > max_cue_s:
                    break
                if not cabe_na_tela(" ".join(words[i:j + 2]), max_chars,
                                    max_lines):
                    break
                j += 1
            if j + 1 < len(words):          # há cue depois: a fronteira importa
                j = _ajusta_fronteira(i, j, words, marco, proibido, pontuada,
                                      budget, max_cue_s)
            a, b = marco[i], marco[j + 1]
            if b <= a:
                b = a + 0.2
            texto = " ".join(words[i:j + 1])
            tempos = []
            for k in range(i, j + 1):
                tempos.append({"w": words[k],
                               "start": round(max(marco[k], a), 3),
                               "end": round(min(marco[k + 1], b), 3)})
            out.append({"start": round(a, 3), "end": round(b, 3),
                        "text": texto, "words": tempos,
                        "lines": _break_lines(texto, max_chars, max_lines)})
            i = j + 1
    out.sort(key=lambda r: r["start"])
    # Duas legendas na tela ao mesmo tempo é o que a definição de pronto proíbe,
    # e aqui ela sairia da nossa própria aritmética.
    for i in range(len(out) - 1):
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = round(out[i + 1]["start"], 3)
            for palavra in out[i]["words"]:
                palavra["end"] = min(palavra["end"], out[i]["end"])
                palavra["start"] = min(palavra["start"], out[i]["end"])
    return [c for c in out if c["end"] > c["start"]]


def finais_pendurados(cues, continua_depois=False):
    """As cues que terminam numa palavra que pede complemento.

    Depois do reflow isto deveria ser vazio. Quando não é, é porque o trecho não
    tinha nenhuma fronteira utilizável -- e aí quem entrega precisa saber, em
    vez de descobrir no mosaico. Devolve o texto de cada cue culpada.

    `continua_depois` diz se a FALA continua depois da última cue desta lista.
    """
    # A ÚLTIMA cue não podia ser acusada, e era. Medido: `['eu gostei muito']`.
    # Duas coisas erradas na mesma acusação.
    #
    # "muito" no fim da frase é intensificador e não pede complemento nenhum --
    # mas a lista não tem como saber disso, e tirá-lo dela quebraria "muito mais
    # caro" partido no meio. O que a lista TEM como saber é a segunda: pendurada
    # é a cue cuja continuação existe e não está na tela. Se não vem nada
    # depois, não há nada pendurando, qualquer que seja a última palavra.
    #
    # E a lista que o `cut` passa é a da JANELA do corte, não a do vídeo: a fala
    # pode muito bem continuar depois do fim do clipe, e aí a última cue está
    # pendurada de verdade -- o espectador ouve a frase morrer no corte. Quem
    # chama é quem sabe disso, então é parâmetro e não adivinhação daqui. O
    # padrão é `False` porque uma lista de cues sem contexto nenhum é o clipe
    # inteiro, e no fim do clipe não há próxima palavra.
    ruins = []
    cues = list(cues or [])
    for n, cue in enumerate(cues):
        ultima = (n == len(cues) - 1)
        if ultima and not continua_depois:
            continue
        palavras = (cue.get("text") or "").split()
        if not palavras:
            continue
        # Um delimitador que abre e não fecha dentro da cue é a MESMA falha,
        # dita com um caractere em vez de uma palavra: o que falta vem depois.
        # Medido em 15/09 -- a cue fechava em "(Desistir" e o parêntese ficava
        # aberto na tela. A regra escrita não pegava, porque "desistir" não
        # está em lista nenhuma; quem pegou o clipe foi a cue SEGUINTE, por
        # sorte. Aqui é de propósito.
        if _delimitador_aberto(cue.get("text") or ""):
            ruins.append(cue.get("text"))
            continue
        # "Ponto final perdoa" é regra de `_fronteiras` desde sempre, e ela
        # NÃO estava aqui: a mesma regra escrita em dois lugares, com duas
        # respostas. Medido em 15/09 sobre um trecho de fala comum --
        # "ninguém aguenta mais." -- o reflow fechava na pontuação, fazendo
        # exatamente o que lhe mandaram, e este relatório acusava a cue por
        # "mais" estar na lista. Um ponto final não pendura nada.
        fecha, _separa = _pontua(palavras[-1])
        if fecha:
            continue
        if (_palavra(palavras[-1]) in NAO_FECHA_CUE
                and not _fecha_apesar_da_lista(palavras, len(palavras) - 1)):
            ruins.append(cue.get("text"))
    return ruins


def _delimitador_aberto(texto):
    """Se o texto abre um parêntese ou colchete e não o fecha.

    Aspa reta NÃO entra, e a assimetria é de propósito: `(` só pode ser
    abertura, e uma aspa reta ímpar numa cue pode muito bem ser a origem que
    veio torta -- legenda automática esquece a aspa de fechamento o tempo todo.
    Acusar isso reprovaria clipes em que não há nada para ver. O reflow
    continua mantendo o par de aspas inteiro quando ele existe; o que este
    portão acusa é só o que é inequívoco na tela.
    """
    pilha = []
    for ch in str(texto or ""):
        if ch in DELIMITADORES:
            pilha.append(DELIMITADORES[ch])
        elif pilha and ch in DELIMITADORES.values():
            for n in range(len(pilha) - 1, -1, -1):
                if pilha[n] == ch:
                    del pilha[n:]
                    break
    return bool(pilha)


def _merge_spans(segments, gap=0.4, descartes=None):
    """Trechos de fala contínua, cada um como lista de (palavra, início, fim).

    O corte de uma cue na origem não é o fim de uma frase; é onde o legendador
    automático decidiu fechar o bloco. Juntar o que está colado devolve a frase,
    e é a frase que se corta bem.

    O que não dá para usar -- segmento sem texto, sem tempo, ou com fim antes do
    início -- é anexado em `descartes` com o motivo, e não some. Um SRT
    meio corrompido tirava fala da tela sem uma linha em lugar nenhum.
    """
    limpos = []
    for seg in segments or []:
        texto = " ".join(str(seg.get("text") or "").split())
        if not texto:
            continue
        try:
            a, b = float(seg.get("start")), float(seg.get("end"))
        except (TypeError, ValueError):
            if descartes is not None:
                descartes.append(f"{texto[:40]!r}: sem tempo legível")
            continue
        if not (b > a >= 0):
            if descartes is not None:
                descartes.append(f"{texto[:40]!r}: tempo impossível "
                                 f"({a} até {b})")
            continue
        limpos.append((a, b, texto))
    limpos.sort(key=lambda r: r[0])
    spans = []
    for a, b, texto in limpos:
        palavras = texto.split()
        if not palavras:
            continue
        # O tempo de cada palavra sai da SUA linha, repartido por sílaba dentro
        # dela. Juntar as linhas continua servindo para recortar a frase num
        # lugar decente; o que não pode é a junção apagar o relógio que a linha
        # já trazia.
        pesos = [silabas(w) for w in palavras]
        total = float(sum(pesos)) or float(len(palavras))
        dur = max(0.05, b - a)
        tempos, t = [], a
        for palavra, peso in zip(palavras, pesos):
            fim = t + dur * peso / total
            tempos.append((palavra, t, fim))
            t = fim
        if spans and a - spans[-1][-1][2] <= gap:
            spans[-1].extend(tempos)
        else:
            spans.append(tempos)
    return spans


def cabe_na_tela(texto, max_chars=MAX_CHARS_PER_LINE, max_lines=MAX_LINES):
    """Se este texto cabe mesmo em `max_lines` linhas de `max_chars`.

    `len(texto) <= max_chars * max_lines` NÃO responde isso, e essa confusão
    entregou uma linha de 36 caracteres com limite de 26: a quebra gulosa
    produzia três linhas dentro do orçamento total, `_break_lines` juntava o
    excedente na última, e o libass rebritava por cima e desenhava TRÊS linhas
    enquanto a nota e o `-estilo.json` afirmavam duas.

    Então a pergunta passa a ser feita à própria quebra. Uma palavra sozinha
    maior que o limite é aceita: não há onde cortá-la, e recusá-la travaria o
    reflow em laço.
    """
    palavras = str(texto or "").split()
    if len(palavras) <= 1:
        return True
    linhas = _break_lines(texto, max_chars, max_lines)
    return len(linhas) <= max_lines and all(len(l) <= max_chars for l in linhas)


def _break_lines(text, max_chars, max_lines):
    """Quebra em até `max_lines` linhas de ~`max_chars`, equilibrando o final."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and len(cand) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:                 # junta o excedente na última linha
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return lines


# ------------------------------------------------------------------ idioma

# Palavras que só um falante daquela língua usa em quantidade. Não é um
# identificador de idioma de verdade e não precisa ser: a pergunta aqui é binária
# e grosseira -- "o hook e a legenda estão na mesma língua?" -- e para isso
# contar artigos, preposições e pronomes resolve com duas linhas de texto.
# Só entram aqui palavras que NÃO existem na outra língua da lista. "a", "no",
# "do", "as", "so", "is" são português e inglês ao mesmo tempo, então marcam os
# dois lados e não decidem nada -- ficaram de fora de propósito.
_LANG_WORDS = {
    "pt": ["que", "não", "nao", "para", "com", "uma", "você", "voce", "isso",
           "porque", "mas", "como", "mais", "eu", "ele", "ela", "foi", "está",
           "esta", "então", "entao", "muito", "tem", "quando", "meu", "minha",
           "pra", "são", "sao", "já", "ja", "gente", "coisa", "o", "e", "da",
           "em", "um", "os", "por", "se", "seu", "ser", "ter", "vai", "aqui",
           "isso", "essa", "esse", "nós", "nos", "sem", "até", "ate"],
    "en": ["the", "and", "you", "that", "this", "with", "for", "was", "have",
           "just", "like", "what", "when", "they", "there", "about", "would",
           "because", "really", "going", "know", "think", "of", "to", "it",
           "he", "she", "we", "on", "at", "my", "your", "his", "her", "but",
           "all", "get", "got", "can", "how", "why", "who"],
    "es": ["que", "para", "con", "una", "pero", "porque", "esto", "muy",
           "cuando", "ellos", "está", "esta", "así", "asi", "todo", "hay",
           "ahora", "nosotros", "también", "tambien", "el", "y", "en", "un",
           "por", "se", "su", "los", "las", "del", "más", "mas"],
}

# Marcadores ortográficos. Valem mais que uma palavra solta porque uma só já
# decide: `ã` e `õ` não existem em espanhol nem em inglês, `ñ` e `¿` não existem
# em português. É o que permite ler um hook de seis palavras, que é o tamanho que
# um hook tem -- e foi por não ler o hook que a divergência passou.
_LANG_MARKS = {
    "pt": ("ã", "õ", "ç", "ê", "á", "í", "ó", "ú", "â", "ô"),
    "es": ("ñ", "¿", "¡"),
    "en": (),
}
_MARK_WEIGHT = 2


def language_of(text, minimum=2):
    """'pt', 'en', 'es' ou None quando o texto não dá para afirmar.

    None é uma resposta legítima e é a resposta certa para "90 mil" ou para uma
    linha de três palavras. Quem chama trata None como "não sei", nunca como
    "diverge" -- recusar por falta de evidência seria pior que o defeito que
    isto existe para pegar.
    """
    import re as _re
    raw = str(text or "").lower()
    flat = " " + " ".join(_re.sub(r"[^\w\s]", " ", raw, flags=_re.UNICODE).split()) + " "
    scores = {}
    for lang, words in _LANG_WORDS.items():
        hits = sum(1 for w in words if (" " + w + " ") in flat)
        marks = sum(_MARK_WEIGHT for m in _LANG_MARKS[lang] if m in raw)
        scores[lang] = hits + marks
    # `ç` também é francês e turco, mas `ã`/`õ` são decisivos entre as três
    # línguas desta lista, e é entre essas três que a pergunta é feita.
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < minimum:
        return None
    ordered = sorted(scores.values(), reverse=True)
    if len(ordered) > 1 and ordered[0] == ordered[1]:
        # Português e espanhol dividem muita palavra curta. Um empate não é uma
        # identificação, e dizer "es" sobre um clipe em pt seria um falso bloqueio.
        return None
    return best


def language_clash(hook, caption_text, declared=None):
    """A frase que explica a divergência de idioma, ou None quando não há.

    Correção do defeito 2.4: o clipe reprovado tinha hook em português e legenda
    em inglês, e nada no fluxo comparava os dois -- o hook vem do agente, a
    legenda vem do Whisper, e ninguém olhava. Compara o que dá para afirmar e
    cala sobre o que não dá.
    """
    cap = language_of(caption_text)
    if cap is None:
        return None
    for name, text in (("the hook", hook), ("the campaign", declared)):
        if not text:
            continue
        other = language_of(text) if name == "the hook" else str(text).lower()[:2]
        if other and other != cap:
            return (f"the caption reads as {cap} and {name} is {other}. A clip "
                    f"whose hook is in one language and whose burned caption is "
                    f"in another is the defect this gate exists for: it went out "
                    f"once with a Portuguese hook over an English caption.")
    return None


# ------------------------------------------------------------------ aprovação do SRT

def approval_path(srt_path):
    return os.path.abspath(srt_path) + ".aprovado"


def srt_fingerprint(srt_path):
    import hashlib
    with open(srt_path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _chave_da_janela(start, end):
    """A chave de `by_window` para uma janela, ou "all" para o arquivo inteiro.

    Formatada com 3 casas e num lugar só: quem grava e quem lê têm de produzir
    a MESMA string, e `0.0` contra `0` contra `0.000` são três chaves
    diferentes para a mesma janela.
    """
    if start is None or end is None:
        return "all"
    return f"{float(start):.3f}-{float(end):.3f}"


def _janelas_salvas(saved):
    """As janelas aprovadas, como lista de (de, ate). `None` é o arquivo inteiro."""
    cruas = saved.get("windows")
    if cruas is None:
        return None                       # aprovação antiga, sem janela nenhuma
    saida = []
    for par in cruas:
        if par in (None, "all", ["all"]):
            return "tudo"
        try:
            saida.append((float(par[0]), float(par[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return saida


def approval_state(srt_path, start=None, end=None):
    """(aprovado?, motivo) PARA A JANELA PEDIDA. O portão do defeito 2.5.

    O comentário do `to_srt` já dizia, com todas as letras, que uma palavra
    errada queimada é pior que legenda nenhuma e que essa checagem fica com a
    pessoa. Só que nada no fluxo obrigava essa pessoa a existir, e o agente
    queimou direto -- com `jokovic jokovic` e uma frase que o sujeito não disse.

    A aprovação é do CONTEÚDO: o hash é do arquivo, então reescrever o SRT
    depois de aprovar invalida a aprovação em vez de herdá-la.

    E a aprovação é DE UMA JANELA, o que custou uma semana para ficar claro.
    `captions review --start 128 --end 148 --approve` imprimia cinco linhas e
    assinava o ARQUIVO -- 152 linhas, no caso medido. Dali em diante toda outra
    janela respondia "(approved)" sem ninguém ter lido uma palavra dela, e o
    portão que existe para impedir palavra errada na tela passou a carimbar
    justamente o que não foi lido. Uma semana depois do `jokovic jokovic`, com
    236 testes e este portão no lugar, `aromasas` foi para a tela -- "aeromoças"
    ouvido errado -- por esta porta.

    Sem `start`/`end` a pergunta é sobre o arquivo inteiro, e só uma aprovação
    de arquivo inteiro responde sim.
    """
    if not srt_path or not os.path.isfile(srt_path):
        return False, "there is no subtitle file to approve"
    path = approval_path(srt_path)
    # Para FORA, nos dois lados, e isso não é cosmético. Com `:.0f` a sugestão
    # arredondava: um corte que queima 461,35-481,35s mandava aprovar
    # `--start 461 --end 481`, a pessoa rodava exatamente esse comando, e o
    # mesmo portão recusava em seguida -- porque 481,35 não cabe em 481. A
    # ferramenta mandava rodar um comando que ela própria não aceitava, e a
    # saída dizia "the approved windows are 461-481s" logo abaixo de "not
    # approved". Medido no corte do vlog, 14/09.
    # SEM `--approve`, e isto é o conserto de 18/09/2026. Esta string é a única
    # coisa que o `cut` devolve ao agente quando a janela não está assinada --
    # ou seja, aparece no momento em que ele está bloqueado, que é quando ele
    # obedece. Entregar o comando de assinar pronto, ali, foi o que fez um
    # agente assinar sozinho em produção. Ele revê e imprime; quem assina é a
    # pessoa, e o `--approve` não é dele para digitar.
    comando = (f"`warden captions review {srt_path} --start {math.floor(start)} "
               f"--end {math.ceil(end)}`"
               if start is not None and end is not None
               else f"`warden captions review {srt_path}`")
    if not os.path.isfile(path):
        return False, (
            f"{os.path.basename(srt_path)} has not been approved. Whisper "
            f"mishears, and a wrong word burned on the screen is worse than no "
            f"caption because the viewer finds out before the clipper does. Read "
            f"the lines with {comando}, fix what is wrong, then approve.")
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    except Exception:
        return False, f"{os.path.basename(path)} is not readable as an approval"
    if saved.get("sha256") != srt_fingerprint(srt_path):
        return False, (
            f"{os.path.basename(srt_path)} changed after it was approved, so the "
            "approval is for words that are no longer in the file. Review and "
            "approve it again.")
    quando_bruto = saved.get("approved_at", "at an unknown time")
    # QUEM, junto do quando, e QUEM DAQUELA JANELA. Sem isto a saída do `cut`
    # dizia que a janela estava aprovada e não dizia por quem -- e "aprovada"
    # era a mesma frase para uma pessoa que leu as linhas e para o agente que
    # assinou a si mesmo. O `by` do topo é o ÚLTIMO a assinar e mentiria sobre
    # qualquer janela anterior; `by_window` é a resposta certa.
    por_janela = saved.get("by_window")
    por_janela = por_janela if isinstance(por_janela, dict) else {}

    def _quando(chave):
        assinou = str(por_janela.get(chave)
                      or (saved.get("by") if not por_janela else "")
                      or "").strip()
        return f"{quando_bruto} by {assinou}" if assinou else quando_bruto

    quando = _quando("all")
    janelas = _janelas_salvas(saved)
    if janelas is None:
        # Aprovação do formato antigo: ela não sabe o que foi lido. Tratá-la
        # como "vale para tudo" é repetir o defeito; ela é recusada em voz alta.
        return False, (
            f"{os.path.basename(path)} is an approval from before windows were "
            f"recorded, so it cannot say WHICH lines a person actually read. It "
            f"is refused rather than trusted: approve the window you are about "
            f"to burn, with {comando}.")
    if janelas == "tudo":
        return True, f"the whole file was approved {quando}"
    if start is None or end is None:
        return False, (
            f"only these windows were approved: "
            f"{'; '.join(f'{a:.0f}-{b:.0f}s' for a, b in janelas)}. Nobody read "
            f"the rest of the file, so it cannot be burned as a whole.")
    for a, b in janelas:
        if a - 0.05 <= float(start) and float(end) <= b + 0.05:
            return True, (f"the {float(start):.0f}-{float(end):.0f}s window is "
                          f"inside the {a:.0f}-{b:.0f}s approved "
                          f"{_quando(_chave_da_janela(a, b))}")
    lista = "; ".join(f"{a:.0f}-{b:.0f}s" for a, b in janelas) or "none"
    return False, (
        f"this cut burns {float(start):.0f}-{float(end):.0f}s and the approved "
        f"windows are {lista}. Approving one window never approved the file: "
        f"that is how `aromasas` reached the screen. Read this window and "
        f"approve it with {comando}.")


def quem_assina(origem):
    """A assinatura em texto: de onde ela veio, e se havia terminal.

    NÃO IMPEDE NADA, e é de propósito. O portão é criptográfico quanto ao
    CONTEÚDO -- sha256 do srt mais as janelas, então as palavras que queimam são
    as que foram assinadas -- e ZERO quanto à AUTORIA: não há tty, token, uid
    nem segredo em lugar nenhum desse caminho, e o agente tem um shell. Em
    18/09/2026 ele assinou uma janela sozinho e nada registrou que tinha sido
    ele.

    `tty=no` é o sinal honesto de que provavelmente ninguém estava digitando.
    Ele não decide nada; ele aparece na saída do `cut` para que a pergunta
    "quem leu estas linhas?" tenha resposta DEPOIS, que é o que não existia.
    """
    import getpass
    try:
        usuario = getpass.getuser()
    except Exception:
        usuario = "?"
    try:
        terminal = "yes" if _sys.stdin.isatty() else "no"
    except Exception:
        terminal = "?"
    return f"{origem} (user={usuario}, tty={terminal})"


def write_approval(srt_path, start=None, end=None, by=None):
    """Assina o SRT para UMA janela, ou para o arquivo inteiro quando não vem uma.

    Aprovações acumulam: aprovar a segunda janela não apaga a primeira, desde
    que o arquivo não tenha mudado. Se mudou, a lista recomeça, porque as
    janelas antigas apontam para palavras que já não estão ali.

    `by` é QUEM assinou, e ele existe porque o campo já existia num artefato
    real (`legenda-01.srt.aprovado`, com `"by": "teste de enquadramento 17/09"`)
    e nenhuma linha do código o escrevia nem o lia. Ver `quem_assina`: não
    impede nada, só para de ser invisível.
    """
    from datetime import datetime, timezone
    path = approval_path(srt_path)
    impressao = srt_fingerprint(srt_path)
    janelas = []
    anteriores_by = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                antigo = json.load(fh)
            if antigo.get("sha256") == impressao:
                anteriores = _janelas_salvas(antigo)
                if anteriores == "tudo":
                    janelas = ["all"]
                elif anteriores:
                    janelas = [[a, b] for a, b in anteriores]
                # As assinaturas acompanham as janelas: se a lista recomeça
                # porque o arquivo mudou, elas recomeçam junto -- assinatura de
                # palavras que já não estão ali não vale nada.
                guardadas = antigo.get("by_window")
                if isinstance(guardadas, dict):
                    anteriores_by = {str(k): str(v) for k, v in guardadas.items()}
        except Exception:
            janelas = []
            anteriores_by = {}
    if start is None or end is None:
        janelas = ["all"]
    elif "all" not in janelas:
        nova = [float(start), float(end)]
        if nova not in janelas:
            janelas.append(nova)
    # O `by` É POR JANELA, e não por arquivo. Achado por auditoria em
    # 18/09/2026, logo depois de o campo nascer: `windows` ACUMULA -- aprovar a
    # segunda janela não apaga a primeira -- mas um `by` único no topo era
    # sobrescrito pela última assinatura. Medido: uma pessoa assinava 0-2s no
    # terminal, o `lote` assinava 2-4s em seguida, e o portão passava a dizer
    # que a janela que a PESSOA leu tinha sido assinada pelo `lote`, com
    # `tty=no`. Um `by` errado é indistinguível de um `by` certo para quem lê
    # depois, e é exatamente para quem lê depois que o campo existe -- então
    # isso era pior que não ter campo nenhum.
    #
    # `by` no topo continua, como o ÚLTIMO a assinar, porque os `.aprovado`
    # gravados entre o nascimento do campo e este conserto só têm ele.
    assinaturas = dict(anteriores_by)
    if janelas == ["all"]:
        assinaturas = {"all": by or "unknown"}
    else:
        assinaturas[_chave_da_janela(start, end)] = by or "unknown"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"sha256": impressao,
                   "approved_at": datetime.now(timezone.utc).isoformat(
                       timespec="seconds"),
                   "by": by or "unknown",
                   "by_window": assinaturas,
                   "file": os.path.basename(srt_path),
                   "windows": janelas}, fh, indent=1)
    return path


# ------------------------------------------------------------------ olhar o material

def sample_shots(video, planos, count=8):
    """`count` quadros tirados DOS PLANOS, não de um trecho contínuo.

    Um edit é feito de pedaços espalhados pelo material, e amostrar
    `start..start+length` olha um trecho contínuo que o clipe não mostra. Medido
    em 15/09: um edit de 7 planos entre 11s e 100s da fonte teve os 8 quadros
    tirados de 11s a 29s, a detecção disse "limpo", e o render saiu com a
    legenda do próprio vídeo aparecendo atrás da nossa em um dos planos. O
    quadro em resolução cheia mostrou as duas.

    Cada plano recebe pelo menos um quadro; o resto é repartido pelos mais
    longos, que é onde há mais material para uma faixa de texto aparecer.
    """
    if not planos:
        return []
    count = max(1, int(count))
    quotas = [1] * len(planos)
    sobra = max(0, count - len(planos))
    if sobra:
        ordem = sorted(range(len(planos)),
                       key=lambda i: planos[i][1] - planos[i][0], reverse=True)
        for k in range(sobra):
            quotas[ordem[k % len(ordem)]] += 1
    saiu = []
    for (a, b, *_), quota in zip(planos, quotas):
        dur = max(0.1, float(b) - float(a))
        saiu += sample_frames(video, float(a), dur, count=quota)
    return saiu


def sample_frames(video, start, length, count=8):
    """`count` quadros da janela, como imagens do PIL. [] quando não deu.

    É o que permite ao renderizador *ver* o material antes de decidir, em vez de
    avisar e queimar mesmo assim. Amostra a janela do corte, não o arquivo todo:
    o que interessa é o que entra neste clipe.
    """
    Image, _D, _F, _Ft = _pil()
    out = []
    count = max(1, int(count))
    length = max(0.1, float(length))
    tmp = tempfile.mkdtemp(prefix="warden-look-")
    try:
        # Uma chamada só, não uma por quadro. Com `-ss` por amostra isto abria
        # oito processos de ffmpeg por corte, em cima dos sete que a detecção de
        # rosto já abre -- quinze decodificações da mesma janela para olhar
        # oito quadros dela.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{float(start):.3f}",
             "-i", video, "-t", f"{length:.3f}",
             "-vf", f"fps={count / length:.6f}", "-frames:v", str(count),
             os.path.join(tmp, "s%03d.png")],
            capture_output=True, timeout=120)
        perdidos = 0
        for name in sorted(os.listdir(tmp)):
            try:
                out.append(Image.open(os.path.join(tmp, name)).convert("L").copy())
            except Exception:
                perdidos += 1
        if perdidos:
            # Metade das evidências é uma evidência mais fraca, e quem lê o
            # resultado tem de saber disso em vez de recebê-lo como completo.
            print(f"warden: {perdidos} of {count} sampled frames could not be "
                  f"read from {os.path.basename(video)}; the footage checks ran "
                  f"on {len(out)}", file=_sys.stderr)
    except Exception:
        return out
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    return out


def _edge_density(img):
    """Fração de pixels de borda forte. Texto tem muita; um céu não tem."""
    from PIL import ImageFilter
    return _histogram_density(img.filter(ImageFilter.FIND_EDGES))


def _histogram_density(edges):
    """A mesma fração, sobre um mapa de bordas que JÁ foi calculado.

    Existe separada porque a ordem importa: `FIND_EDGES` carimba uma borda forte
    na primeira e na última linha de tudo o que recebe, então filtrar DEPOIS de
    recortar mede o recorte. Em fatias finas isso domina -- ver o comentário da
    varredura em `burned_text_bands`, onde custou uma medição inteira.
    """
    hist = edges.histogram()
    total = sum(hist) or 1
    return sum(hist[70:]) / total


def legenda_do_acervo(frames, recorte=None, min_ratio=2.2, min_largura=0.12,
                      max_altura=0.07):
    """Texto queimado EM QUALQUER ALTURA do quadro, não só nas duas bordas.

    LEIA ISTO ANTES DE MEXER NO DETECTOR DE LEGENDA DUPLA.

    `burned_text_bands` olha os 16% de cima e os 16% de baixo, e foi calibrada
    para o que ela foi escrita para pegar: tarja de disclaimer no topo, rodapé
    de acervo embaixo. No vlog de 14/09 ela devolveu `bottom: false` com 1 voto
    em 8 e média 0,9x -- e a conclusão de que o limiar estava alto demais estava
    errada. Medido no quadro da fonte aos 95,1s: a legenda queimada do vídeo
    fica em y 747-774 de 1080, ou seja a **69% da altura**. No meio. Nenhum
    ajuste de limiar faria aquele detector enxergá-la, porque ele não olha ali.
    Legenda de vídeo vertical moderno mora no terço inferior do meio, não na
    borda, e é exatamente a altura em que a NOSSA cai depois do recorte 9:16.

    O que separa letra de textura não é densidade de borda, é a assinatura que
    `_row_span` mede: branco >=225 encostado em preto <70. No mesmo quadro, o
    teto ripado da garagem dá `span` 0 e a legenda dá 468px de 607 de largura.
    Por isso uma faixa só conta com largura de verdade.

    `recorte` é (x0, x1) no quadro da FONTE: a faixa vertical que o corte vai
    manter. Medir o quadro inteiro responde a pergunta errada -- o que importa
    é o texto que sobrevive ao enquadramento.

    Devolve faixas em FRAÇÃO da altura, com em quantos quadros cada uma
    apareceu. Uma legenda pisca entre falas, então um quadro já é sinal: o
    custo de achar que tem quando não tem é um clipe sem a nossa legenda; o de
    achar que não tem quando tem é o clipe que o dono reprovou.
    """
    try:
        from PIL import ImageFilter
    except ImportError:
        return []
    achadas = []
    for img in frames or []:
        quadro = img
        if recorte:
            x0, x1 = int(recorte[0]), int(recorte[1])
            if x1 > x0:
                quadro = img.crop((x0, 0, x1, img.size[1]))
        w, h = quadro.size
        for y0, y1, span in _text_rows(quadro, ImageFilter, min_ratio=min_ratio):
            if span / max(1, w) < min_largura:
                continue                      # textura, não letra
            achadas.append((y0 / h, y1 / h, span / w))
    if not achadas:
        return []
    achadas.sort()
    faixas = []
    for a, z, larg in achadas:
        for f in faixas:
            if a <= f["y1"] and z >= f["y0"]:        # mesma faixa, outro quadro
                f["y0"] = min(f["y0"], a)
                f["y1"] = max(f["y1"], z)
                f["span"] = max(f["span"], larg)
                f["frames"] += 1
                break
        else:
            faixas.append({"y0": a, "y1": z, "span": larg, "frames": 1})
    # Uma legenda é uma LINHA, e uma linha é fina.
    #
    # Medido em 16/09/2026, nos dois casos que existem:
    #   a legenda de verdade do vlog de 14/09 ficava em y 747-774 de 1080 --
    #   2,5% da altura, e juntar três quadros dela não passa de 3%;
    #   o falso positivo de 16/09 -- a camisa de estampa geométrica de um
    #   corretor, com o microfone de lapela preto encostado no branco dela --
    #   juntou 0,600 a 0,683, 8,3% da altura, a partir de trechos espalhados
    #   em alturas diferentes a cada quadro. Legenda não anda de altura; o
    #   peito de quem fala, sim.
    #
    # Esse falso positivo custou a legenda INTEIRA de um clipe entregue: o
    # corte saiu mudo porque a ferramenta achou que o vídeo já vinha legendado,
    # e não vinha. A regra do dono é nunca entregar sem legenda, então o
    # equilíbrio deste detector vira para o outro lado: duas legendas no mesmo
    # quadro é feio e o mosaico obrigatório mostra; um clipe mudo é o produto
    # faltando. Sete por cento deixa passar uma legenda de duas linhas e mata
    # uma camisa.
    return [f for f in faixas if (f["y1"] - f["y0"]) <= max_altura]


# A faixa da borda em que se procura texto queimado da fonte, como fração da
# altura. Era só o padrão de `burned_text_bands`; virou constante em 18/09/2026
# porque o enquadramento dividido precisa da MESMA fração para saber até onde o
# texto da fonte pode chegar dentro da faixa da tela, e dois 0,16 soltos em
# arquivos diferentes é o tipo de coisa que sai de sincronia sem ninguém ver.
BANDA_TEXTO_FONTE = 0.16


def burned_text_bands(frames, band=BANDA_TEXTO_FONTE):
    """Onde o material já carrega texto queimado, e até onde essa faixa sobe.

    Correção do defeito 2.6. O `cut()` já sabia que não enxerga texto queimado no
    material -- ele avisava e queimava por cima mesmo assim, o que transfere para
    depois da publicação um problema que ele mesmo detectou.

    Texto é uma faixa de densidade de borda muito acima do resto do quadro, e
    **estável entre quadros**: um rodapé de disclaimer fica; uma mão que passa,
    não. Por isso a decisão pede as duas coisas -- densidade alta e persistência
    na maioria dos quadros. É grosseiro de propósito: a pergunta é "tem texto
    aqui embaixo?", e ela não precisa de OCR para ser respondida.

    `bottom_reach` é a fração da altura, contada de baixo, em que a faixa de
    texto começa. Sem ela o degradê era de tamanho fixo e cobria o disclaimer
    mas não a legenda do acervo, que fica mais acima -- medido num render, não
    suposto: o rodapé apagava o amarelo e deixava a fala legível.

    São TRÊS estados e não dois. `bottom`/`top` continuam sendo o booleano duro,
    calibrado para tarja fixa; `bottom_suspect`/`top_suspect` são o material que
    mostra sinal de texto sem alcançar esse booleano, que é o que uma legenda de
    vlog -- fina, centralizada, intermitente -- produz. Ver o bloco de gatilhos
    lá embaixo para o número de cada um e a medição que o justifica. `*_why` traz
    os gatilhos que dispararam, em texto, para a nota e para a evidência.
    """
    if not frames:
        return {"top": False, "bottom": False,
                "top_suspect": False, "bottom_suspect": False,
                "bottom_reach": 0.0, "evidence": "no frames to look at"}
    from PIL import ImageFilter
    votes = {"top": 0, "bottom": 0}
    ratios = {"top": [], "bottom": []}
    reaches = []
    for img in frames:
        w, h = img.size
        strip = max(1, int(h * band))
        middle = img.crop((0, strip, w, h - strip))
        base = _edge_density(middle) or 1e-6
        for name, box in (("top", (0, 0, w, strip)),
                          ("bottom", (0, h - strip, w, h))):
            ratio = _edge_density(img.crop(box)) / base
            ratios[name].append(ratio)
            # 1,9x a densidade do miolo: medido para separar uma faixa de letras
            # de um horizonte ou de uma borda de cenário, que ficam perto de 1.
            #
            # E ele NÃO separa tudo. Medido em 16/09/2026, na mesma escala: uma
            # faixa de letras de verdade dá 5,71x, mas uma fila de telhados
            # escuros contra céu branco dá 1,96x e 2,43x -- e uma linha só de
            # texto no topo dá 2,71x. Não há corte que separe os dois com folga,
            # então o número fica onde estava e quem mudou foi a CONSEQUÊNCIA:
            # ver `cut`, onde um texto achado no topo deixou de matar o clipe.
            if ratio > 1.9:
                votes[name] += 1
        # Até onde a faixa de baixo sobe. Um limiar fixo não serve: arte densa
        # (grades, multidão, cenário carregado) fica em 2-3x a vida toda e a
        # varredura sobe para sempre -- medido, deu 50% da altura e o degradê
        # comeu um terço do quadro. O que separa texto de arte densa é a QUEDA:
        # a faixa de letras é um pico, e ela acaba onde a densidade despenca para
        # menos da metade desse pico.
        #
        # UMA detecção de bordas no quadro INTEIRO, e depois recorta. A versão
        # anterior chamava `_edge_density` em cada fatia, o que roda FIND_EDGES
        # dentro do recorte -- e FIND_EDGES inventa uma borda forte na primeira e
        # na última linha do que recebe. Numa fatia de 2% da altura (10px num
        # quadro de 540) essas duas linhas são 20% dos pixels da fatia, então
        # TODA fatia saía densa e a varredura subia até o teto sempre. Medido em
        # 14/09, com o código como estava: `bottom_reach` deu exatamente 0,22 --
        # o BAND_CAP -- nos seis quadros sintéticos limpos, nos oito quadros de
        # um `testsrc2` e nos quadros com disclaimer, os três iguais. Um número
        # que vale 0,22 para tudo não é uma medição, é uma constante com nome de
        # medição, e era ela que o degradê estava usando para se dimensionar.
        # Com a detecção feita uma vez no quadro inteiro: limpo 0,02, disclaimer
        # 0,10. Agora separa.
        edges = img.filter(ImageFilter.FIND_EDGES)
        base_reach = _histogram_density(edges.crop((0, strip, w, h - strip))) or 1e-6
        step = BAND_SLICE
        densities = []
        for i in range(int(BAND_CAP / step) + 1):
            y0 = int(h * (1 - (i + 1) * step))
            y1 = int(h * (1 - i * step))
            if y1 > y0:
                densities.append(
                    _histogram_density(edges.crop((0, y0, w, y1))) / base_reach)
        peak = max(densities) if densities else 0.0
        floor = max(2.0, peak * 0.45)
        reach = 0.0
        for i, d in enumerate(densities):
            if d >= floor:
                reach = (i + 1) * step
            else:
                break
        # E um teto duro: legenda queimada e disclaimer moram no quinto de baixo
        # de um quadro. Passar disso é apagar imagem para resolver um problema de
        # texto, o que troca um defeito por outro.
        reaches.append(min(reach, BAND_CAP))
    need = max(2, int(len(frames) * 0.6))         # persistente, não um quadro solto
    found = {name: votes[name] >= need for name in votes}
    medias = {name: sum(ratios[name]) / len(ratios[name]) for name in ratios}
    # A mediana, não o máximo: um quadro em que a arte também é densa embaixo não
    # deve mandar o degradê cobrir metade do clipe.
    ordered = sorted(reaches)
    found["bottom_reach"] = ordered[len(ordered) // 2] if ordered else 0.0

    # ------------------------------------------------------------ o terceiro estado
    #
    # O booleano duro acima foi calibrado para TARJA FIXA: um disclaimer que fica
    # no lugar o clipe inteiro. Ele pede 1,9x de densidade E persistência em 60%
    # dos quadros, e as duas exigências juntas são cegas para o caso que passou
    # em 14/09: um vlog do YouTube com legenda própria, fina, centralizada e
    # INTERMITENTE -- ela some entre as falas. Medido naquele corte: 1 de 8
    # quadros votou, média 1,7x. O `bottom` saiu False, e False aqui desliga em
    # cascata o degradê, o REJECT do `check_sidecar` e todas as notas. Saíram
    # duas legendas de origens diferentes no mesmo quadro e nada reprovou.
    #
    # Não se afrouxa o booleano duro -- ele decide sozinho e por isso tem de
    # continuar caro. O que entra é um terceiro estado, e o que ele custa é um
    # degradê discreto num rodapé, não um clipe.
    #
    # Os três gatilhos, e o número de cada um:
    #
    #  - UM quadro que votou (ratio > 1,9). Uma faixa que atinge a assinatura de
    #    texto num quadro só é exatamente o que uma legenda intermitente faz.
    #    Ela não basta para o booleano (uma mão que passa também produz isso),
    #    mas basta para desconfiar.
    #  - média >= 1,35. É o piso mais baixo que ainda não toca material limpo:
    #    medido, um `testsrc2` (o padrão de teste mais carregado que existe) dá
    #    0,99x e o quadro sintético limpo da suíte dá 1,03x. O caso que passou
    #    deu 1,7x. 1,35 fica no meio, com 31% de folga sobre a leitura limpa mais
    #    alta que foi medida.
    #  - `bottom_reach` no teto. Depois do conserto da varredura acima isto
    #    voltou a significar alguma coisa: uma faixa densa de ponta a ponta até
    #    22% da altura, na metade dos quadros. Limpo mede 0,02.
    SUSPEITO_PISO = 1.35
    for name in ("top", "bottom"):
        gatilhos = []
        if votes[name] >= 1:
            gatilhos.append(f"{votes[name]} frame(s) over 1.9x")
        if medias[name] >= SUSPEITO_PISO:
            gatilhos.append(f"mean {medias[name]:.1f}x over the {SUSPEITO_PISO}x floor")
        if name == "bottom" and found["bottom_reach"] >= BAND_CAP:
            gatilhos.append(f"the dense band runs to the {BAND_CAP:.0%} cap")
        found[name + "_suspect"] = bool(gatilhos) and not found[name]
        found[name + "_why"] = gatilhos

    found["evidence"] = "; ".join(
        f"{name}: {votes[name]}/{len(frames)} frames, "
        f"{medias[name]:.1f}x the middle's edge density"
        for name in ("top", "bottom"))
    # `bottom_reach` SEMPRE, e não só quando `bottom` é True. Ele era escondido
    # justamente no caso em que era a única coisa a dizer: no corte de 14/09 ele
    # marcava o teto enquanto o booleano dizia "limpo", e ninguém leu porque a
    # evidência não o continha.
    found["evidence"] += f"; the bottom band reaches {found['bottom_reach']:.0%} up"
    for name in ("top", "bottom"):
        if found[name + "_suspect"]:
            found["evidence"] += (f"; {name.upper()} SUSPECT (not the hard "
                                  f"boolean): " + ", ".join(found[name + "_why"]))
    return found


def frame_border(frames, probe=0.035):
    """Quantos pixels de moldura há em cada lateral: {'left': n, 'right': n}.

    Correção do defeito 2.7. A faixa vertical ciano da borda do material entrou
    no enquadramento e ficou parada ali o clipe inteiro, porque o crop foi
    calculado pelo rosto e ninguém perguntou se aquelas colunas eram conteúdo ou
    moldura.

    Moldura é uma coluna que quase não varia dentro do quadro **e** quase não
    muda entre quadros: uma tarja preta, uma barra de cor, uma borda de captura.
    Conteúdo varia nas duas direções. Devolve a largura em pixels da fonte, para
    quem calcula a faixa recuar dela.
    """
    if not frames:
        return {"left": 0, "right": 0}
    w, h = frames[0].size
    reach = max(2, int(w * probe))
    ys = list(range(0, h, max(1, h // 48)))

    def column_is_border(x):
        values = []
        for img in frames:
            col = [img.getpixel((x, y)) for y in ys]
            spread = max(col) - min(col)
            if spread > 34:                # varia dentro do quadro: é conteúdo
                return False
            values.append(sum(col) / len(col))
        return (max(values) - min(values)) <= 12   # e não muda entre quadros

    def mean_at(x):
        return sum(sum(img.getpixel((x, y)) for y in ys) / len(ys)
                   for img in frames) / len(frames)

    def contrasts(n, side):
        """Uma barra se destaca do que está do lado; uma parede lisa continua.

        Sem isto, uma parede branca ou um céu uniforme encostado na borda era
        lido como moldura e o enquadramento recuava de imagem boa -- trocar um
        defeito por outro, que é o que a seção 7 do briefing manda não fazer.
        """
        if n <= 0:
            return False
        edge = mean_at(0 if side == "left" else w - 1)
        inside = [mean_at(n + k if side == "left" else w - 1 - n - k)
                  for k in range(1, 6) if 0 <= (n + k) < w]
        if not inside:
            return False
        return abs(edge - sum(inside) / len(inside)) > 25

    left = 0
    while left < reach and column_is_border(left):
        left += 1
    right = 0
    while right < reach and column_is_border(w - 1 - right):
        right += 1
    return {"left": left if contrasts(left, "left") else 0,
            "right": right if contrasts(right, "right") else 0}


# ------------------------------------------------------------------ contact sheet

def contact_sheet(video, out_png, tiles=8, cols=4, label=None):
    """O mosaico do próprio render. É o portão, e é a correção mais importante.

    A verificação do warden confere duração, resolução e regras da campanha --
    nenhum dos dez defeitos do clipe reprovado é numérico, então ela aprovou
    todos. O que faltava não era mais uma métrica, era um ponto em que alguém
    **olha a imagem** antes de dizer que passou.

    Quadros amostrados do render pronto, não da fonte: o que se vê aqui é o que
    o espectador vê. Cada quadro traz o segundo em que foi tirado, para que uma
    reprovação possa dizer onde. Devolve o caminho do PNG ou None se não deu para
    montar -- e quem chama trata None como motivo para não entregar, nunca como
    detalhe.
    """
    Image, ImageDraw, _F, _Ft = _pil()
    dur = _duration(video)
    if not dur or dur <= 0:
        return None
    tiles = max(2, int(tiles))
    cols = max(1, min(cols, tiles))
    rows = (tiles + cols - 1) // cols

    shots = []
    tmp = tempfile.mkdtemp(prefix="warden-sheet-")
    perdidos = []
    try:
        # UMA passada de ffmpeg, não uma por quadro.
        #
        # Amostra no meio de cada fatia: o primeiro e o último quadro de um
        # render costumam ser transição, e um mosaico de transições não conta o
        # que o clipe mostra.
        #
        # `select` e não `fps`, e o motivo está medido em `_quadros_coloridos`:
        # `-ss` de meia fatia mais `fps=1/passo` NÃO cai nos instantes que o
        # rótulo diz. O `fps` sintetiza uma grade e ela sai MEIO PASSO adiantada
        # -- num mosaico de 20s com oito casas isso é 1,25s de erro por ladrilho.
        # O portão é uma imagem com um segundo escrito embaixo; um segundo errado
        # é uma reprovação apontando para o lugar errado, e o primeiro ladrilho
        # caindo depois dos 3s é o hook já fora do quadro quando ele deveria
        # estar nele. `showinfo` diz o `pts_time` de cada quadro escolhido, e o
        # custo continua sendo uma passada só.
        #
        # Medido em 15/09/2026 no container: oito `-ss` separados custavam
        # 4,3-8,0s; a passada única custa 1,2-1,6s. É o mesmo conserto que
        # `sample_frames` já tinha recebido neste arquivo e que não havia sido
        # propagado para cá.
        #
        # E a confissão de cegueira do portão NÃO se perde: o espaçamento é
        # determinístico, então o índice de saída volta a ser instante, e um
        # quadro que não saiu continua sendo nomeado pelo segundo dele.
        passo = dur / tiles
        zero = passo / 2
        instantes = [dur * (i + 0.5) / tiles for i in range(tiles)]
        saiu = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
                               "-ss", f"{zero:.3f}", "-i", video,
                               "-vf", (f"select='isnan(prev_selected_t)+"
                                       f"gte(t-prev_selected_t,{passo:.6f})',"
                                       f"showinfo"),
                               "-frames:v", str(tiles),
                               "-fps_mode", "passthrough",
                               os.path.join(tmp, "f%02d.png")],
                              capture_output=True, timeout=120)
        texto = (saiu.stderr or b"").decode("utf-8", "replace")
        medidos = [float(v) for v in re.findall(r"pts_time:([0-9.]+)", texto)]
        saidas = sorted(n for n in os.listdir(tmp)
                        if n.startswith("f") and n.endswith(".png")
                        and os.path.getsize(os.path.join(tmp, n)) > 0)
        for i, nome in enumerate(saidas):
            # O carimbo é o `pts_time` do quadro que SAIU, não a casa da grade
            # que pedimos. `-ss` antes do `-i` zera o relógio, então o instante
            # no clipe é `zero + pts`. Sem showinfo -- outro ffmpeg, outra build
            # -- vale a grade, aproximada como antes e nunca pior.
            t = zero + medidos[i] if i < len(medidos) else instantes[min(i, tiles - 1)]
            try:
                shots.append((round(t, 3),
                              Image.open(os.path.join(tmp, nome)).convert("RGB")))
            except Exception as exc:
                perdidos.append(f"{t:.1f}s ({type(exc).__name__})")
        # O `select` trunca no fim, nunca no meio: o que faltou são as últimas
        # casas da grade, e são elas que voltam pelo caminho de um `-ss` cada.
        for t in instantes[len(saidas):]:
            perdidos.append(f"{t:.1f}s")
        # O que a passada não trouxe, buscado um a um.
        #
        # Uma passada só, seja `fps` ou `select`, pode devolver menos quadros
        # do que se pediu: medido em 15/09 num clipe de 15,00s, a oitava
        # amostra (14,1s) existia no vídeo e não saía. Sete de oito enfraquece
        # o portão -- e um portão que some por otimização é pior que um portão
        # lento.
        #
        # Então a passada única é o caminho rápido, não o único: o que faltar
        # volta pelo caminho antigo. No caso comum isso é zero ou uma chamada,
        # e no pior caso o custo é o de antes, nunca maior.
        if perdidos:
            faltando = list(perdidos)
            perdidos = []
            for t_txt in faltando:
                t = float(t_txt.split("s")[0])
                png = os.path.join(tmp, "fill%.3f.png" % t)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss",
                                f"{t:.3f}", "-i", video, "-frames:v", "1", png],
                               capture_output=True, timeout=60)
                if os.path.isfile(png) and os.path.getsize(png) > 0:
                    try:
                        shots.append((t, Image.open(png).convert("RGB")))
                        continue
                    except Exception as exc:
                        perdidos.append(f"{t:.1f}s ({type(exc).__name__})")
                        continue
                perdidos.append(f"{t:.1f}s")
            shots.sort(key=lambda par: par[0])
        if not shots:
            return None

        tw = 300
        th = int(tw * shots[0][1].height / max(1, shots[0][1].width))
        head = 46
        sheet = Image.new("RGB", (cols * tw, head + rows * th), (17, 17, 19))
        d = ImageDraw.Draw(sheet)
        # Sem try: a folha de contato É o portão, e uma folha com a fonte
        # bitmap do PIL tem carimbo de tempo que ninguém lê. Cair para uma
        # fallback aqui, calado, é entregar um portão que não se atravessa.
        title_font = _font(24)
        tag_font = _font(20)

        w, h = shots[0][1].size
        caption = label or os.path.basename(video)
        # Quantos quadros NÃO entraram, escrito no próprio mosaico. Um ffmpeg
        # que falha em três das oito amostras devolvia uma folha com cinco
        # quadros e nenhuma marca disso: quem olha conta cinco e acha que o
        # clipe foi visto inteiro. O portão tem de dizer onde ele é cego.
        faltou = (f"   FALTARAM {len(perdidos)} DE {tiles}: {', '.join(perdidos[:4])}"
                  if perdidos else "")
        d.text((12, 12),
               f"{caption}   {w}x{h}   {dur:.2f}s   {len(shots)} quadros{faltou}",
               font=title_font, fill=(255, 120, 120) if perdidos else (235, 235, 240))

        for i, (t, img) in enumerate(shots):
            img = img.resize((tw, th))
            x, y = (i % cols) * tw, head + (i // cols) * th
            sheet.paste(img, (x, y))
            stamp = f"{t:.1f}s"
            d.rectangle([x + 4, y + 4, x + 4 + 9 * len(stamp) + 12, y + 32],
                        fill=(0, 0, 0))
            d.text((x + 10, y + 7), stamp, font=tag_font, fill=(255, 214, 0))
            d.rectangle([x, y, x + tw - 1, y + th - 1], outline=(40, 40, 44))

        os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
        sheet.save(out_png, quality=88)
        return out_png
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def _duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


# O checklist que acompanha todo contact sheet. Curto e objetivo de propósito:
# uma lista longa não é olhada, e cada item aqui corresponde a um defeito medido
# no clipe que foi reprovado.
#
# EM INGLÊS, como o resto da saída do render, e por medição: em 16/09/2026 estes
# oito itens saíram em português dentro de um bloco em inglês, numa conversa em
# inglês (state.db id 69; logs-7a/container/render-outs.txt, bloco do render 1).
# São as ÚLTIMAS linhas que o modelo lê antes de escrever, e a prosa que ele
# escreveu saiu em português (msg 21, 14:51:58, "Em produção.").
#
# Isto não é texto para a pessoa: é a conferência que o modelo faz olhando o
# mosaico. Quem escolhe a língua da conversa é a pessoa, e o modelo responde
# nela; a ferramenta fala uma língua só, a dela.
CHECKLIST = [
    "the hook fits whole in frame, no side crop, at most two lines",
    "the hook LEFT the screen: it is in the first frames of the sheet, not the last",
    "the caption is at most two lines",
    "no cue ends mid-sentence (on a preposition, article or conjunction)",
    "the yellow highlight follows the speech, not the whole cue lit at once",
    "there are no two captions in the same frame",
    "no frame, letterbox edge or third-party text cropped at the borders",
    "the speaker's face is not covered by text",
]


# ------------------------------------------------------------------ padrão medido
#
# Fase 5. "Ficou feio" não é um erro que o agente vê sozinho; um número fora de
# faixa é. O que segue mede um clipe -- aprovado ou novo -- pelas mesmas réguas,
# para que a diferença entre os dois seja aritmética.

# As métricas que um clipe carrega. Cada uma existe porque um defeito do clipe
# reprovado é invisível sem ela.
METRICS = (
    "width", "height", "fps", "duration_s",
    "text_width_ratio",      # a linha mais larga contra a largura útil: >1 é corte
    "text_rows",             # quantas faixas de texto no quadro: 3 é legenda dupla
    "text_rows_max",         # e no quadro mais carregado de todos
    "caption_lines_max",     # linhas na maior cue
    "cut_interval_s",        # média entre trocas de plano
    "scale_variation",       # o quanto a escala mudou ao longo do clipe
    "bottom_text",           # texto na faixa inferior
)


# Uma linha é "preta" abaixo disto. Não é zero: um render de H.264 não devolve
# 0 exato, e o degradê do rodapé chega perto do pé do quadro sem ser preto.
BARRA_LUZ = 8
# Quantos por cento da altura (ou largura) precisam ser pretos para ser barra, e
# em quantos quadros. Uma barra é uma coisa constante: o que aparece num quadro
# só é imagem escura.
#
# Era 0,02 -- 38px num quadro de 1920, que é um fio de cabelo. Passou para 0,08
# em 16/09/2026, medido no primeiro lote que saiu com legenda de verdade: um
# corte de uma live cujo cenário compartilhado é escuro mediu 5,6% no pé e foi
# REPROVADO, e a "barra" era a própria faixa da legenda -- o fundo desfocado
# sobre um material preto, com a legenda amarela em cima e legível. Um clipe
# perfeito por todo o resto não sai por causa de 107 pixels de banda escura
# onde o texto mora.
#
# O defeito que abriu este portão continua pego, e com folga: o clipe de 15/09
# tinha 289px, 15% do quadro. 8% são 154px -- ainda metade daquilo, e ainda uma
# moldura visível no feed. O que sai da rede é a faixa da legenda, que é onde
# ela sempre esteve.
BARRA_MIN = 0.08
BARRA_QUADROS = 0.8


def barras_pretas(frames):
    """(quanto de cada borda é barra preta, motivo). Frações da altura/largura.

    Existe porque um clipe entregue em 15/09 tinha **289px de tarja preta no pé
    do quadro, 15% da altura, em todos os quadros** -- e passou por toda a
    verificação, porque nenhum dos instrumentos olhava as bordas. A causa era o
    próprio "degradê" de rodapé, que saturava o alfa em 252 sobre 74% da faixa.
    O degradê foi consertado; isto é o portão que diz se ele voltou a ser tarja,
    ou se a barra veio de outro lugar -- letterbox do material, render errado,
    escala com padding.

    Mede a MEDIANA por borda em vez do máximo: um quadro que é preto porque a
    cena é preta não é uma barra.
    """
    if not frames:
        return None, "no frame was opened, so the edges were not looked at"
    por_borda = {"top": [], "bottom": [], "left": [], "right": []}
    for frame in frames:
        g = frame.convert("L")
        w, h = g.size
        if w < 8 or h < 8:
            continue
        px = g.load()
        passo_x = max(1, w // 64)
        passo_y = max(1, h // 64)

        def linha_preta(y):
            vals = [px[x, y] for x in range(0, w, passo_x)]
            return (sum(vals) / len(vals)) <= BARRA_LUZ

        def coluna_preta(x):
            vals = [px[x, y] for y in range(0, h, passo_y)]
            return (sum(vals) / len(vals)) <= BARRA_LUZ

        n = 0
        while n < h // 2 and linha_preta(n):
            n += 1
        por_borda["top"].append(n / h)
        n = 0
        while n < h // 2 and linha_preta(h - 1 - n):
            n += 1
        por_borda["bottom"].append(n / h)
        n = 0
        while n < w // 2 and coluna_preta(n):
            n += 1
        por_borda["left"].append(n / w)
        n = 0
        while n < w // 2 and coluna_preta(w - 1 - n):
            n += 1
        por_borda["right"].append(n / w)

    saiu = {}
    for borda, valores in por_borda.items():
        if not valores:
            saiu[borda] = 0.0
            continue
        # Barra é o que está em quase todo quadro: o percentil (1 - BARRA_QUADROS).
        ordenados = sorted(valores)
        idx = int(len(ordenados) * (1.0 - BARRA_QUADROS))
        saiu[borda] = round(ordenados[min(idx, len(ordenados) - 1)], 4)
    return saiu, None


def measure(video, samples=10):
    """As métricas de um clipe, medidas no arquivo e não no que ele deveria ser.

    Roda igual sobre um aprovado e sobre um render novo, que é a única forma de
    `style check` significar alguma coisa: a faixa vem do corpus, não de um
    número que alguém escreveu com boa intenção.
    """
    Image, _D, ImageFilter, _Ft = _pil()
    dur = _duration(video) or 0.0
    out = {m: None for m in METRICS}
    out["duration_s"] = round(dur, 2)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "default=nw=1", video],
        capture_output=True, text=True).stdout
    fields = dict(l.split("=", 1) for l in probe.splitlines() if "=" in l)
    out["width"] = int(fields.get("width") or 0) or None
    out["height"] = int(fields.get("height") or 0) or None
    rate = fields.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        out["fps"] = round(float(num) / float(den), 2) if float(den) else None
    except ValueError:
        out["fps"] = None
        out["fps_why"] = f"o ffprobe devolveu r_frame_rate={rate!r}"
    if not dur or not out["width"]:
        # A outra saída desta função. Sem a chave, `check_against` não via
        # `measured is False` e voltava a imprimir "dentro da faixa em tudo"
        # sobre zero evidência -- o mesmo defeito, pela porta de cima.
        out["measured"] = False
        return out

    frames = sample_frames(video, 0, dur, count=samples)
    if not frames:
        # `measure` devolvendo tudo None fazia o `style check` pular toda métrica
        # e imprimir "dentro da faixa em tudo": uma aprovação construída sobre
        # zero evidência, que é o defeito que este comando existe para não ter.
        out["measured"] = False
        return out
    out["measured"] = True

    w, h = frames[0].size
    usable = usable_width(w)
    rows_per_frame, widths, bottoms = [], [], 0
    for img in frames:
        bands = _text_rows(img, ImageFilter)
        rows_per_frame.append(len(bands))
        for y0, y1, span in bands:
            widths.append(span / max(1, usable))
            if y0 > h * 0.78:
                bottoms += 1
                break
    out["text_rows"] = round(sum(rows_per_frame) / len(rows_per_frame), 2)
    # A média sozinha não responde à pergunta que interessa. O hook sai da tela
    # aos HOOK_SECONDS, então o número de faixas CAI no meio do clipe: num corte
    # de 20s com hook, oito dos dez quadros amostrados já não têm hook nenhum, e
    # a média dilui para perto de uma camada o que em alguns quadros são três.
    # A pergunta é "existe algum quadro com mais faixas do que as nossas camadas
    # explicam?", e quem responde isso é a distribuição, não a média. Por isso
    # saem os três: a média (que o corpus já mede), o máximo, e a contagem por
    # quadro, que é de onde `cross_check` tira a mediana.
    out["text_rows_max"] = max(rows_per_frame)
    out["text_rows_per_frame"] = rows_per_frame
    out["text_width_ratio"] = round(max(widths), 3) if widths else 0.0
    out["bottom_text"] = bottoms >= max(2, int(len(frames) * 0.5))
    # Linhas na maior cue: uma faixa de texto contígua mais alta que ~1,6 linhas
    # é uma cue de duas linhas, e assim por diante.
    tall = 0
    for img in frames:
        for y0, y1, _span in _text_rows(img, ImageFilter):
            tall = max(tall, round((y1 - y0) / (h / CAPTION_SIZE_DIVISOR / LEADING)))
    out["caption_lines_max"] = tall or None

    out["cut_interval_s"], porque = _cut_interval(video, dur)
    if porque:
        out["cut_interval_s_why"] = porque
    out["black_bars"], porque_barras = barras_pretas(frames)
    if porque_barras:
        out["black_bars_why"] = porque_barras
    out["scale_variation"], porque = _scale_variation(frames)
    if porque:
        out["scale_variation_why"] = porque
    return out


def _text_rows(img, ImageFilter, min_ratio=3.0):
    """(y0, y1, largura) de cada faixa horizontal com cara de texto no quadro.

    Contíguas: duas faixas separadas por imagem limpa são duas legendas, que é o
    que a definição de pronto proíbe num quadro só.
    """
    w, h = img.size
    edges = img.filter(ImageFilter.FIND_EDGES)
    step = max(4, h // 120)
    base_hist = edges.histogram()
    base = (sum(base_hist[70:]) / (sum(base_hist) or 1)) or 1e-6
    dense = []
    for y in range(0, h - step, step):
        strip = edges.crop((0, y, w, y + step))
        hist = strip.histogram()
        ratio = (sum(hist[70:]) / (sum(hist) or 1)) / base
        dense.append((y, ratio >= min_ratio))
    bands, run = [], None
    for y, hot in dense + [(h, False)]:
        if hot and run is None:
            run = y
        elif not hot and run is not None:
            if y - run >= step * 2:               # faixas de uma fatia são ruído
                span = _row_span(img, run, y, ImageFilter)
                bands.append((run, y, span))
            run = None
    return bands


def _row_span(img, y0, y1, ImageFilter):
    """Largura em px do TEXTO nesta faixa, não da faixa.

    A primeira versão disto pegava a primeira e a última coluna com borda forte,
    e media o quadro inteiro: numa faixa que tem texto POR CIMA de imagem, a
    imagem também tem borda, então a extensão dava 1,26x a largura útil -- em
    clipes aprovados, cujo texto obviamente cabe. Uma métrica que reprova todo o
    corpus não está medindo o corpus, está medindo a si mesma.

    O que distingue o nosso texto de imagem é a assinatura dele: branco quase
    puro encostado em preto quase puro, que é o contorno de 3px. Uma coluna
    conta como texto quando tem um pixel claro com um escuro a até 4px dele, e
    isso se repete em várias alturas da faixa. Nuvem clara não tem contorno;
    letra tem.
    """
    w, _h = img.size
    band = img.crop((0, y0, w, y1))
    bh = y1 - y0
    ys = list(range(0, bh, max(1, bh // 14)))
    cols = []
    for x in range(0, w, 3):
        hits = 0
        for y in ys:
            if band.getpixel((x, y)) < 225:
                continue
            near = [band.getpixel((min(w - 1, max(0, x + dx)), y))
                    for dx in (-4, -2, 2, 4)]
            if min(near) < 70:
                hits += 1
        if hits >= 2:                     # em mais de uma altura: é uma letra
            cols.append(x)
    return (cols[-1] - cols[0]) if len(cols) > 1 else 0


def _cut_interval(video, dur):
    """(segundos médios entre trocas de plano, motivo de não ter medido).

    Devolvia só o número, e `None` quando o ffmpeg falhava -- e `None` some no
    `continue` de quem lê as métricas. Um detector de cena que não rodou e um
    clipe de plano único davam a mesma ausência na tela.
    """
    try:
        done = subprocess.run(
            ["ffmpeg", "-nostats", "-hide_banner", "-i", video, "-vf",
             "select='gt(scene,0.4)',metadata=print", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return None, f"the scene detector did not run ({type(exc).__name__})"
    if done.returncode != 0:
        cauda = (done.stderr or "").strip().splitlines()[-1:]
        return None, ("o detector de cena saiu com erro"
                      + (f": {cauda[0][:120]}" if cauda else ""))
    hits = done.stderr.count("lavfi.scene_score")
    return (round(dur / hits, 2) if hits else round(dur, 2)), None


def _scale_variation(frames):
    """O quanto a escala mudou do primeiro ao último quadro, de 0 para cima.

    Zero é um trecho bruto: nenhum movimento de câmera, nenhuma variação de
    escala, que é o defeito 2.8. Comparar os quadros reescalados basta -- não
    interessa quanto, interessa se houve.
    """
    if len(frames) < 2:
        return None, (f"only {len(frames)} frame(s) opened from this file, and "
                      f"scale drift is measured between two")
    from PIL import ImageChops
    a = frames[0].resize((160, 284))
    b = frames[-1].resize((160, 284))
    dif = ImageChops.difference(a, b)
    return round(sum(dif.histogram()[40:]) / (160 * 284), 4), None


def consolidate(measurements):
    """As faixas do corpus: mínimo, mediana e máximo de cada métrica.

    Faixas, não valores únicos. Um aprovado tem 62s e outro tem 19s, e nenhum dos
    dois é o certo -- o que o corpus diz é o intervalo em que o dono aprovou.
    """
    spec = {}
    for name in METRICS:
        vals = [m.get(name) for m in measurements if m.get(name) is not None]
        nums = [v for v in vals if isinstance(v, (int, float))
                and not isinstance(v, bool)]
        if nums:
            nums.sort()
            spec[name] = {"min": nums[0], "median": nums[len(nums) // 2],
                          "max": nums[-1], "n": len(nums)}
        elif vals:
            spec[name] = {"values": sorted({bool(v) for v in vals}),
                          "n": len(vals)}
    return spec


# O que reprova é REGRA ABSOLUTA, não faixa do corpus, e essa distinção custou
# uma medição para ficar clara.
#
# O corpus são vinte edits de scenepack: uma frase-âncora sustentada, sem legenda
# corrida. Um corte de podcast tem hook E legenda, então ele tem mais faixas de
# texto que qualquer aprovado -- medido, 2,75 contra 0,9-2,1. Reprovar por essa
# faixa seria reprovar um modo legítimo que o corpus não contém, que é uma régua
# medindo a si mesma e não o clipe.
#
# Então o corpus informa (a faixa aparece ao lado de cada número, para quem
# olha), e quem reprova são estes três limites, cada um ligado a um defeito
# medido no clipe que saiu publicado:
LIMITS = {
    "scale_variation": (
        0.02, None,
        "scale drift across the clip. Zero is a raw twenty-second stretch "
        "with text over it. It separates cleanly: 0.12 to 0.83 across the "
        "twenty approved clips, 0 on a cut with no movement."),
}

# Medidas que saem no relatório e não reprovam ninguém. A lista é longa de
# propósito, e duas entradas dela são confissões:
#
# `text_width_ratio` medido em pixels erra. Ele acerta o caso extremo -- 1,22 num
# controle com o hook cortado de propósito, contra 0,67-0,98 na maioria dos
# aprovados -- mas deu 1,14 num aprovado cujo texto é pequeno e está claramente
# dentro do quadro: a assinatura "branco encostado em preto" também é um letreiro
# de neon e um demônio vermelho sobre preto. Uma métrica que reprovaria um clipe
# que o dono aprovou não pode reprovar nada. Ela fica no relatório como sinal.
#
# `caption_lines_max` estima linha pela altura da faixa e deu 4 num aprovado que
# tem 2. Mesma regra: não se confia, não vota.
#
# O que reprova de verdade o texto cortado é o `style.json` que o `cut` escreve,
# porque lá a largura do hook não é recuperada de pixels -- é o número que o PIL
# mediu com a fonte real antes de desenhar. Exato onde dá para ser exato.
REPORTED_ONLY = ("duration_s", "cut_interval_s", "caption_lines_max",
                 "text_width_ratio", "text_rows", "text_rows_max", "bottom_text",
                 "fps", "width", "height")


def cap_margem(side):
    """A legenda do sidecar, ou um dicionário vazio.

    Existe porque o bloco de margens roda junto com o do hook, antes de `cap`
    ser definido, e ler `side["caption"]` direto dava `None` em clipe sem
    legenda -- que é um clipe legítimo, não um erro.
    """
    return (side.get("caption") or {}) if isinstance(side, dict) else {}


def check_sidecar(side):
    """[(nível, mensagem)] do que o próprio render registrou sobre si.

    Estes números não são estimados: vêm de quem desenhou o texto, medido com a
    fonte real contra a largura útil. É aqui que "o hook cabe inteiro" e "nenhuma
    cue passa do teto" deixam de ser coisas que alguém tem de olhar e viram
    aritmética que quebra um teste. O teto são dois números e não um:
    `MAX_CUE_S_TETO` (3,2s) é até onde o reflow estende para alcançar uma
    fronteira, e este portão reprova acima de 3,4s.
    """
    out = []
    if not side:
        return out
    hook = side.get("hook") or {}
    if hook.get("width_px") is not None and hook.get("usable_px"):
        ratio = hook["width_px"] / hook["usable_px"]
        if ratio > 1.0:
            out.append((OBSERVACAO, f"the hook is {hook['width_px']}px wide against "
                                  f"{hook['usable_px']}px of usable width "
                                  f"({ratio:.2f}x): it is cropped at the frame edge"))
        else:
            out.append(("ok", f"hook fits: {hook['width_px']}px of "
                              f"{hook['usable_px']}px usable ({ratio:.2f}x)"))
    if hook.get("complete") is False:
        out.append((OBRIGACAO,
                    f"the hook is {hook.get('chars')} characters and does not fit "
                    f"in {MAX_LINES} lines even at the {hook.get('size_px')}px "
                    "floor, so words were dropped from it. A hook missing words "
                    "is worse than one cropped at the edge: a cropped hook still "
                    "says what it says, one missing its last words says something "
                    "else. Write a shorter hook."))
    if hook.get("lines") and hook["lines"] > MAX_LINES:
        out.append((OBSERVACAO, f"the hook is on {hook['lines']} lines; at most "
                              f"{MAX_LINES} fit above the picture"))
    # As três margens da interface, medidas contra o que foi desenhado. Até
    # aqui o único portão de posição era a LARGURA do hook contra os 854px
    # úteis -- que protege a coluna direita por simetria e não protege nem o
    # topo nem a base. O hook entregue em 14/09 passava nesse portão e começava
    # a 184px do topo, dentro dos ~200px que as abas e a lupa do app cobrem.
    # Largura certa e altura errada saíam com a mesma cara de aprovado.
    margens_ = side.get("margins") or {}
    quadro = side.get("frame") or {}
    if margens_ and quadro:
        topo = margens_.get("top")
        if hook.get("top_px") is not None and topo and hook["top_px"] < topo:
            out.append((OBSERVACAO,
                        f"the hook starts {hook['top_px']}px from the top and the "
                        f"app's own tabs and search icon cover the first {topo}px: "
                        f"the first line lands under them"))
        elif hook.get("top_px") is not None:
            out.append(("ok", f"hook starts at {hook['top_px']}px, clear of the "
                              f"top {topo}px"))
        direita = margens_.get("right")
        limite_x = (quadro.get("w") or 0) - (direita or 0)
        for nome, bloco in (("hook", hook), ("caption", cap_margem(side))):
            if not bloco or bloco.get("right_px") is None or not limite_x:
                continue
            if bloco["right_px"] > limite_x:
                out.append((OBSERVACAO,
                            f"the {nome} reaches {bloco['right_px']}px across and "
                            f"the like/comment/share column owns the last "
                            f"{direita}px (from {limite_x}px): it sits under the "
                            f"buttons"))
        base = margens_.get("bottom")
        fundo = cap_margem(side).get("bottom_px") if cap_margem(side) else None
        limite_y = (quadro.get("h") or 0) - (base or 0)
        if fundo is not None and limite_y and fundo > limite_y:
            out.append((OBSERVACAO,
                        f"the caption ends {fundo}px down and the app's caption, "
                        f"handle and sound own the last {base}px (from "
                        f"{limite_y}px): the last line is behind them"))
        elif fundo is not None and limite_y:
            out.append(("ok", f"caption ends at {fundo}px, {limite_y - fundo}px "
                              f"clear of the bottom {base}px"))
    # O hook é uma promessa, e uma promessa que fica na tela vinte segundos vira
    # uma placa. Em 14/09 os dois cortes tinham a frase nos oito quadros do
    # mosaico. A folga de meio segundo é o fade.
    if hook.get("seconds_on_screen") is not None and side.get("duration_s"):
        ficou = float(hook["seconds_on_screen"])
        clipe = float(side["duration_s"])
        if clipe > HOOK_SECONDS + 1.0 and ficou > HOOK_SECONDS + 0.5:
            out.append((OBSERVACAO,
                        f"the hook stays {ficou:.1f}s of a {clipe:.1f}s clip. It "
                        f"should leave at {HOOK_SECONDS:.0f}s: after that it is "
                        f"not a promise any more, it is a sign parked on the "
                        f"picture, fighting the caption for the same frame"))
        else:
            out.append(("ok", f"hook leaves at {ficou:.1f}s"))
    cap = side.get("caption") or {}
    # Item 2 do Bloco B, como aritmética e não como olhar. Depois do reflow por
    # fronteira isto tem de ser vazio; quando não é, o trecho não tinha nenhuma
    # fronteira usável e quem entrega precisa saber ANTES do mosaico.
    pendurados = cap.get("hanging_endings") or []
    if pendurados and cap.get("ends_mid_sentence"):
        # A última delas é o CORTE, não a legenda. Mandar re-quebrar a legenda
        # aqui é mandar consertar o lugar errado: não existe palavra seguinte
        # dentro do clipe para a cue alcançar.
        # NENHUM conselho pode repetir o número que já está no corte.
        #
        # Esta frase era montada sem guarda para `falta <= 0` e saiu assim, duas
        # vezes idênticas, nos renders 2 e 3 de 16/09/2026
        # (logs-7a/container/render-outs.txt:127 e :169):
        #
        #   That sentence closes 0.0s later, at 30.0s: cut 30.0s instead of
        #   30.0s, or move the start so it fits in the number that was asked for.
        #
        # O portão reprovava e mandava fazer exatamente o que já estava feito.
        # Custou 2 renders (~150s de CPU) e 2 laços de espera (~523s) em cima de
        # uma instrução que não dizia nada, e todo re-corte nascia reprovado.
        #
        # `falta <= 0.05` quer dizer que as palavras que existem acabam JUNTO com
        # o corte: daqui não dá para saber onde a frase fecha, porque não há
        # texto depois dela. Um `--end` maior não tem o que alcançar, e a saída
        # é outra -- mover o COMEÇO, ou escolher uma janela que termine em ponto.
        fecha = cap.get("sentence_closes_at_s")
        dur = float(side["duration_s"]) if side.get("duration_s") else None
        onde = ""
        if fecha and dur:
            falta = float(fecha) - dur
            if falta > 0.05:
                onde = (f" That sentence closes {falta:.1f}s later, at "
                        f"{float(fecha):.1f}s: cut {float(fecha):.1f}s instead "
                        f"of {dur:.1f}s, or move the start so it fits in the "
                        f"number that was asked for. When the extra second is "
                        f"the right trade, say so to the person in ONE clause, "
                        f"in THEIR language, about what they will see -- and "
                        f"never with the word cue in it.")
            else:
                onde = (" The words that exist end WITH this cut, so nothing "
                        "here can say where that sentence closes: a longer "
                        "--end has nothing to reach, and the same numbers would "
                        "come back. Move the START, or choose a window that "
                        "ends on a full stop.")
        out.append((OBSERVACAO,
                    f"this clip ENDS mid-sentence, on \"…{pendurados[-1]}\". The "
                    f"speech goes on past --end, so the last thing the viewer "
                    f"hears is half a clause. No caption setting fixes this."
                    + (onde or " Move --end to where the sentence closes.")))
    outros = [t for t in pendurados
              if not (cap.get("ends_mid_sentence") and t == pendurados[-1])]
    if outros:
        amostra = "; ".join(f"…{t}" for t in outros[:3])
        out.append((OBSERVACAO,
                    f"{len(outros)} caption cue(s) end on a word that needs "
                    f"what comes next ({amostra}). That is the 'HATE THE' and "
                    f"'CARA, EU VOTARIA NO' of 14/09: each half is grammatical "
                    f"and neither says anything"))
    if cap.get("cues") and cap.get("karaoke") is False:
        out.append((OBSERVACAO,
                    "the caption was burned with no word-by-word timing, so the "
                    "whole cue lights at once. The per-word times come from the "
                    "reflow; a cue without them means they were lost on the way"))
    if cap.get("max_lines") and cap["max_lines"] > MAX_LINES:
        out.append((OBSERVACAO, f"a caption cue is {cap['max_lines']} lines; the "
                              "six-line block that covered a face was this"))
    elif cap.get("cues"):
        out.append(("ok", f"{cap['cues']} cues, at most {cap['max_lines']} lines"))
    # 3,0s e não 2,5s, e o meio segundo foi pago para comprar o item 2. Uma cue
    # pode estourar MAX_CUE_S em até CUE_TOLERANCIA_S para alcançar uma
    # fronteira sintática; acima disso já não é a fronteira, é um bloco parado.
    teto_cue = round(MAX_CUE_S_TETO + 0.2, 2)
    if cap.get("max_cue_s") and cap["max_cue_s"] > teto_cue:
        out.append((OBSERVACAO, f"a cue stays {cap['max_cue_s']}s on screen; over "
                              f"{teto_cue}s it is a block parked on the picture"))
    elif cap.get("max_cue_s"):
        out.append(("ok", f"longest cue {cap['max_cue_s']}s"))
    # Duas legendas num quadro só é a NOSSA por cima da do acervo. A pergunta é
    # essa e só essa: o material tem texto embaixo, nós queimamos legenda, e o
    # degradê não cobriu? Contar camadas sem perguntar se havia texto do acervo
    # reprovava clipes cujo material é limpo.
    fonte = side.get("source_text") or {}
    # E o terceiro estado entra AQUI também. `bottom` saindo False era o que
    # desligava este REJECT em cascata junto com o degradê: em 14/09 a detecção
    # tinha visto 1 quadro em 8 acima do limiar e ninguém foi avisado de nada,
    # porque a primeira condição da conjunção era o booleano duro. Um material
    # suspeito com a nossa legenda queimada por cima e sem degradê é a mesma
    # imagem que o booleano duro descreve -- o portão não pode ser mais estreito
    # que a detecção que o alimenta.
    tem_texto_do_acervo = fonte.get("bottom")
    suspeito = fonte.get("bottom_suspect")
    if cap.get("cues") and tem_texto_do_acervo and not side.get("footer_covered"):
        out.append((OBSERVACAO, "this footage burns its own text along the bottom "
                              "and it was not covered, so our caption sits on "
                              "top of it: two captions in one frame"))
    elif cap.get("cues") and suspeito and not side.get("footer_covered"):
        porque = ", ".join(fonte.get("bottom_why") or ["-"])
        out.append((OBSERVACAO,
                    f"this footage shows signs of its own burned text along the "
                    f"bottom ({porque}) and it was not covered, so our caption "
                    f"may be sitting on top of it. Cover it, or re-cut without "
                    f"--subtitles: 'probably clean' is not a thing this can "
                    f"ship on, and the contact sheet is where you settle it."))
    if side.get("motion") is False:
        out.append((OBSERVACAO, "no scale movement at all: this reads as raw footage"))
    # O número que a pessoa pediu contra o que o arquivo tem. Quando a campanha
    # obrigou a sair do pedido, `cut` já disse qual regra obrigou -- e aí a
    # diferença é legítima e aparece nas notas, não aqui. O que este portão pega
    # é a diferença SEM motivo, que foi o 20,6s e o 22,2s de 14/09.
    pedido, tem = side.get("asked_s"), side.get("duration_s")
    venceu = side.get("asked_overridden_by")
    if pedido and tem and venceu:
        out.append(("ok", f"{float(tem):.2f}s instead of the {float(pedido):.0f}s "
                          f"asked for: {venceu}. The note above says it in the "
                          f"words the person hears, which is the whole "
                          f"requirement"))
    elif pedido and tem:
        folga = abs(float(tem) - float(pedido))
        if folga > 0.3:
            out.append((OBSERVACAO,
                        f"the person asked for {float(pedido):.0f}s and this "
                        f"file is {float(tem):.2f}s ({folga:.2f}s off). A number "
                        f"a person says is the request. Only a campaign rule may "
                        f"override it, and then the note has to say which rule."))
        else:
            out.append(("ok", f"{float(tem):.2f}s against the {float(pedido):.0f}s "
                              f"asked for"))
    return out


def check_against(measured, spec):
    """[(nível, mensagem)] de um render contra os limites, com a faixa ao lado."""
    out = []
    if measured.get("measured") is False:
        return [(OBRIGACAO, "no frame of this file could be opened, so nothing "
                           "was measured. 'inside the range' on zero evidence "
                           "is the failure this command exists to avoid.")]
    ranges = spec or {}
    for name, (lo, hi, what) in LIMITS.items():
        got = measured.get(name)
        if got is None:
            # `scale_variation` é o ÚNICO limite que reprova. Quando ele sai
            # None -- menos de dois quadros abertos, ffmpeg que falhou -- o laço
            # antigo dava `continue` e o comando terminava dizendo "dentro da
            # faixa em todas as métricas aplicadas", que era verdade e era vazio:
            # nenhuma foi aplicada. Falta de medição não é aprovação.
            porque = measured.get(name + "_why") or "it was not measured"
            out.append((OBSERVACAO,
                        f"{name} could not be measured ({porque}), and it is "
                        f"the only limit that rejects anything. Without it this "
                        f"command has no opinion about this file -- do not read "
                        f"the silence as approval."))
            continue
        band = ranges.get(name) or {}
        contexto = (f" (approved clips: {band['min']}–{band['max']})"
                    if "min" in band else "")
        if hi is not None and got > hi:
            out.append((OBSERVACAO, f"{what}\n           measured {got}, the limit "
                                  f"is {hi}{contexto}"))
        elif lo is not None and got < lo:
            out.append((OBSERVACAO, f"{what}\n           measured {got}, the floor "
                                  f"is {lo}{contexto}"))
        else:
            out.append(("ok", f"{name}: {got}{contexto}"))
    for name in REPORTED_ONLY:
        if measured.get(name) is None:
            continue
        band = ranges.get(name) or {}
        contexto = (f"  (approved clips: {band['min']}–{band['max']})"
                    if "min" in band else "")
        out.append(("note", f"{name}: {measured[name]}{contexto}"))
    return out


# A folga que o segundo instrumento exige para reprovar. Ver `_linhas_demais`.
LINHAS_DEMAIS_FOLGA = 2


def _linhas_demais(side, medida):
    """A segunda opinião contra legenda dupla: faixas de texto no render pronto
    contra as linhas que nós desenhamos.

    `burned_text_bands` olha o MATERIAL antes do render e pode errar para menos
    -- foi o que fez em 14/09. `_text_rows` olha o arquivo PRONTO e não sabe de
    onde veio nada, mas conta faixas de texto contíguas; e o `-estilo.json` sabe
    exatamente o que nós desenhamos, porque foi o PIL que desenhou. Cruzar os
    dois responde "o render tem faixa de texto que nós não pomos ali?", que é a
    pergunta de legenda dupla, sem depender da detecção que falhou.

    O orçamento são LINHAS e não camadas. `text_layers` do sidecar conta três
    coisas (rodapé, legenda, hook) e é o número errado para cruzar: uma cue de
    DUAS linhas lê como DUAS faixas, porque entre uma linha e outra passa imagem
    limpa e `_text_rows` só junta o que é contíguo -- medido, 2 faixas para uma
    cue de 2 linhas, 3 faixas para hook de 1 linha mais cue de 2. Usar
    `text_layers` faria este portão reprovar todo corte com legenda de duas
    linhas. O orçamento são as linhas que o sidecar registra: `caption.max_lines`
    mais, QUANDO ELE ESTÁ LÁ, `hook.lines`.

    "Quando ele está lá" é a parte que custou uma medição. O hook sai aos
    HOOK_SECONDS (3s), e `measure` amostra o clipe inteiro por igual: num corte
    de 20s com dez amostras, oito ou nove quadros já não têm hook. Somar o hook
    ao orçamento de todos eles compra um crédito de duas linhas para a metade do
    clipe em que ele não existe -- medido: com o hook no orçamento, um render com
    a nossa cue de 2 linhas E uma legenda de acervo de 2 linhas por baixo dá 4
    faixas contra um limite de 5, e passa. Sem ele, dá 4 contra 4, e reprova. Por
    isso o hook só entra no orçamento quando fica mais da metade do clipe na
    tela, que é o único caso em que o quadro MEDIANO o contém -- e é um corte
    curto, de seis segundos ou menos. `seconds_on_screen` e `duration_s` já estão
    no sidecar; nenhum dos dois é estimado.

    E a folga é +2, não +1, porque `_text_rows` inventa uma faixa sozinha em
    material real. A prova está no próprio corpus: os vinte scenepacks aprovados
    carregam UMA frase-âncora de no máximo duas linhas e mediram `text_rows` de
    0,9 a 2,1 de MÉDIA -- 2,1 de média com duas linhas desenhadas quer dizer
    quadros com três e quatro faixas em clipes que o dono aprovou. Com +1 este
    portão reprovaria o corpus inteiro, e um portão que reprova clipe bom é
    desligado na semana seguinte.

    Mediana e não máximo nem média, pelo mesmo motivo dos dois lados: o máximo é
    um quadro (um letreiro no fundo, um flash de placa) e a média é puxada para
    baixo pela saída do hook. A mediana pergunta "na MAIORIA dos quadros sobra
    texto que não é nosso?", e é a única agregação que sobrevive às duas coisas.

    O preço disto é conhecido e está dito: uma legenda de acervo de UMA linha
    por baixo da nossa de duas dá 3 faixas contra um orçamento de 2, que é +1, e
    este portão não a pega. Ele é a segunda opinião, não a primeira -- quem pega
    aquele caso é o degradê que o `bottom_suspect` liga no `cut`. Aqui o erro
    caro seria o outro.
    """
    por_quadro = (medida or {}).get("text_rows_per_frame")
    hook = ((side or {}).get("hook") or {})
    cap = ((side or {}).get("caption") or {})
    if not por_quadro or not side:
        return []
    ficou = hook.get("seconds_on_screen")
    clipe = (side or {}).get("duration_s")
    hook_no_quadro_mediano = bool(
        hook.get("lines") and ficou is not None and clipe
        and float(ficou) > float(clipe) / 2.0)
    linhas_hook = int(hook.get("lines") or 0) if hook_no_quadro_mediano else 0
    orcamento = linhas_hook + int(cap.get("max_lines") or 0)
    if not orcamento:
        # Sem hook e sem legenda nossa, qualquer faixa de texto no render é de
        # outra pessoa -- mas isso é `bottom_text`/`burned_text_bands` falando,
        # não este cruzamento, que existe para separar o nosso do resto e aqui
        # não tem nosso nenhum com que separar.
        return []
    ordenado = sorted(por_quadro)
    mediana = ordenado[len(ordenado) // 2]
    limite = orcamento + LINHAS_DEMAIS_FOLGA
    if mediana >= limite:
        return [(OBSERVACAO,
                 f"the finished file shows {mediana} bands of text in most "
                 f"frames and we only drew {orcamento} line(s) of our own "
                 f"(caption {cap.get('max_lines') or 0}"
                 + (f", hook {linhas_hook}" if linhas_hook
                    else "" if not hook.get("lines") else
                    f"; the hook's {hook['lines']} line(s) are not counted, it "
                    f"leaves at {float(ficou):.1f}s of {float(clipe):.1f}s so "
                    f"the median frame has no hook in it")
                 + f"). That is {mediana - orcamento} "
                 f"band(s) of text nobody here put in the frame, in more than "
                 f"half the clip -- burned text of the footage itself, or "
                 f"another clipper's caption, under ours. Two captions in one "
                 f"frame is the reject; look at the contact sheet and re-cut "
                 f"with the footer covered.")]
    return [("ok", f"text bands: {mediana} in the median frame against "
                   f"{orcamento} line(s) of ours, rejecting at {limite}")]


def _barra_preta_reprova(medida):
    """Qualquer borda preta constante reprova o render.

    Isto existe porque um clipe entregue tinha 289px de tarja no pé do quadro --
    15% da altura, em todos os quadros -- e a verificação inteira aprovou, porque
    ela conta segundos, pixels de largura e faixas de texto, e **nenhum
    instrumento olhava as bordas**. O defeito era do nosso próprio rodapé, que
    se chamava degradê e saturava o alfa em 252. Consertar o degradê não impede
    que ele volte, nem pega uma barra que venha de outro lugar: letterbox do
    material, escala com padding, render errado.

    O limiar é 2% da altura. Uma borda preta de 2% num vertical de 1920 são 38
    pixels, que já é visível como moldura no feed; abaixo disso é cor de cena.
    """
    barras = (medida or {}).get("black_bars")
    if not barras:
        porque = (medida or {}).get("black_bars_why")
        if porque:
            return [(OBSERVACAO, f"the edges of this render were not looked at: "
                               f"{porque}. A black bar is invisible to every "
                               f"other measurement here.")]
        return []
    achadas = {b: v for b, v in barras.items() if v and v >= BARRA_MIN}
    if not achadas:
        return [("ok", "no black bar on any edge")]
    partes = []
    for borda, fracao in sorted(achadas.items(), key=lambda kv: -kv[1]):
        partes.append(f"{borda} {fracao * 100:.1f}%")
    return [(OBSERVACAO,
             f"this render has a black bar on {len(achadas)} edge(s): "
             + ", ".join(partes)
             + ". It is there in most frames, so it reads as a frame around the "
               "picture and not as a dark scene. On 15/09 a delivered clip had "
               "289px of it along the bottom -- 15% of the frame -- and every "
               "check passed, because none of them looked at the edges. If it "
               "is ours, it is the footer gradient saturating; if it is the "
               "footage's, the framing kept its letterbox.")]


def cross_check(side, medida):
    """A métrica de pixel cruzada com o que o render anotou de si.

    `text_width_ratio` é medido nos pixels do arquivo pronto, e ele erra para
    mais: deu 1,14 num clipe que o dono aprovou e cujo texto está claramente
    dentro do quadro, porque a assinatura que ele procura -- branco encostado em
    preto -- também é um letreiro de neon e um demônio vermelho sobre fundo
    escuro. Por isso ele nunca reprovou sozinho, e por isso a tentação era
    apagá-lo.

    Só que ele vê uma coisa que o `-estilo.json` não vê. O sidecar conhece o
    hook, porque foi o PIL que o desenhou; ele não conhece o texto queimado do
    próprio material, nem a legenda de outro clipador, nem nada que já estava no
    quadro antes de nós. São dois instrumentos com cegueiras diferentes.

    Então o sinal é a dois: reprova quando os dois apontam largura demais, e
    quando discordam diz qual é qual, em vez de escolher um e calar o outro.
    """
    out = []
    out += _linhas_demais(side, medida)
    out += _barra_preta_reprova(medida)
    px = (medida or {}).get("text_width_ratio")
    hook = ((side or {}).get("hook") or {})
    largura, util = hook.get("width_px"), hook.get("usable_px")
    if px is None or not util:
        if px is not None:
            # A mensagem dizia "sem -estilo.json para cruzar" nos DOIS casos, e
            # num deles isso é falso: o sidecar foi lido e está ali, só não tem
            # `hook` -- é o clipe cortado sem hook, que é um corte legítimo.
            # Mandar quem entrega procurar um arquivo que existe é mandá-lo
            # procurar o defeito errado.
            if side is None:
                porque = ("sem -estilo.json ao lado para cruzar")
            elif not (side or {}).get("hook"):
                porque = ("the -estilo.json is here, but this clip was cut "
                          "with NO hook, so there is no PIL-measured width to "
                          "cross it with")
            else:
                porque = ("the -estilo.json has a hook but never wrote down the "
                          "usable width (`usable_px`), so there is nothing to "
                          "cross it with")
            out.append(("note", f"text_width_ratio: {px} measured in pixels, "
                                f"{porque}. On its own it does not reject: it "
                                f"over-reports on high-contrast material."))
        return out
    nosso = largura / float(util)
    if px > 1.0 and nosso > 1.0:
        out.append((OBSERVACAO,
                    f"text wider than the frame on BOTH instruments: {px} "
                    f"measured in the file's pixels and {nosso:.2f}x measured "
                    f"by PIL on the hook ({largura}px against {util}px usable). "
                    f"Two instruments with different blind spots pointing at "
                    f"the same place is the only reading where the pixel metric "
                    f"gets a vote."))
    elif px > 1.0:
        out.append(("note",
                    f"the two instruments disagree: the pixels say {px} wide "
                    f"and PIL says the hook fits ({nosso:.2f}x, {largura}px of "
                    f"{util}px). PIL measured with the real typeface before "
                    f"drawing, so the hook is fine -- what is wide is something "
                    f"else in the frame. Text burned into the material itself, "
                    f"another clipper's captions, or a high-contrast title "
                    f"card. Look at the edges in the mosaic."))
    elif nosso > 1.0:
        out.append(("note",
                    f"the two instruments disagree the other way: PIL says the "
                    f"hook does not fit ({nosso:.2f}x) and the pixels did not "
                    f"see it ({px}). PIL wins, and its REJECT is already above; "
                    f"the pixel metric loses a light hook on a light "
                    f"background."))
    else:
        out.append(("ok", f"text width agrees on both: {px} in the pixels, "
                          f"{nosso:.2f}x on the hook measured by PIL"))
    return out


# ------------------------------------------------------------------ encaixar na fala

# A janela em que uma cue procura o começo de fala que lhe pertence, e o quanto
# ela pode ser puxada para trás.
#
# O detector é `silencedetect` e ele foi VALIDADO contra a régua que o dono
# mediu à mão no clipe entregue: com `noise=-35dB:d=0.10`, os `silence_end` no
# `clip-chave-de-buceta.mp4` saem em 3,18 10,41 11,94 12,37 15,46 16,69 20,52
# 21,17 -- oito de oito contra 3,18 10,41 11,94 12,37 15,47 16,69 20,52 21,17,
# com 0,01s de diferença num único valor. Não é estimativa nossa: é o número
# dele, reproduzido por uma ferramenta.
FALA_RUIDO_DB = -35
FALA_MINIMO_S = 0.10
ENCAIXE_JANELA_S = 0.70      # até onde procurar o onset que pertence à cue
ENCAIXE_MAXIMO_S = 1.00      # o quanto uma cue pode ser puxada para trás


def fala_comeca(source, start=0.0, length=None):
    """(começos de fala em segundos do arquivo, motivo de não ter medido).

    `silence_end` do `silencedetect` é o instante em que a fala volta. É o que a
    legenda deveria marcar e quase nunca marca: a legenda automática do YouTube
    carimba o tempo DEPOIS da fala, porque o reconhecedor só emite quando já tem
    contexto, e queimar esse tempo cru nasce atrasado.

    Devolve o motivo junto, e não uma lista vazia calada: "não tem áudio" e "o
    ffmpeg falhou" são a mesma lista e não são a mesma coisa.
    """
    args = ["ffmpeg", "-nostats", "-hide_banner"]
    if start:
        args += ["-ss", f"{float(start):.3f}"]
    args += ["-i", str(source)]
    if length:
        args += ["-t", f"{float(length):.3f}"]
    args += ["-map", "0:a:0", "-af",
             f"silencedetect=noise={FALA_RUIDO_DB}dB:d={FALA_MINIMO_S}",
             "-f", "null", "-"]
    try:
        feito = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return [], f"the speech detector did not run ({type(exc).__name__})"
    if feito.returncode != 0:
        cauda = (feito.stderr or "").strip().splitlines()[-1:]
        return [], ("o detector de fala saiu com erro"
                    + (f": {cauda[0][:120]}" if cauda else ""))
    achados = [float(m) for m in
               re.findall(r"silence_end:\s*([0-9.]+)", feito.stderr or "")]
    # O primeiro som do trecho também é um começo de fala, e ele não aparece
    # como `silence_end` quando o trecho já abre falando.
    if not (feito.stderr or "").lstrip().startswith("[silencedetect")  \
            and "silence_start: 0" not in (feito.stderr or "")[:400]:
        achados = [0.0] + achados
    achados = sorted(set(round(float(start) + a, 3) for a in achados))
    if not achados:
        return [], "no speech onset was detected in this stretch"
    return achados, None


def encaixa_na_fala(cues, onsets, janela=ENCAIXE_JANELA_S,
                    maximo=ENCAIXE_MAXIMO_S):
    """Puxa cada cue para o começo de fala que lhe pertence. (cues, diagnóstico).

    A regra do dono, e ela NÃO é simétrica: o destaque pode chegar um pouco
    antes da palavra, nunca depois. Legenda adiantada passa despercebida,
    atrasada incomoda na hora. Então toda correção aqui é para TRÁS -- uma cue
    que já está adiantada fica onde está.

    Como funciona, e por que assim:

    1. Cada cue procura o começo de fala mais próximo dentro de `janela`. O que
       sobra é `atraso = cue.start - onset`: positivo quer dizer que a legenda
       chega depois da fala, que é o defeito.
    2. As cues que acharam onset são puxadas para ele. Deslocamento RÍGIDO: a
       cue inteira e todas as palavras andam juntas, então a soma dos `\\k`
       continua batendo com o tempo na tela e nenhuma cue invade a seguinte.
    3. As que não acharam nenhum -- fala corrida, sem pausa para detectar --
       levam o atraso MEDIANO das que acharam. Isso é a causa (b): a legenda
       automática atrasa de forma sistemática, e a mediana é esse sistema
       medido neste arquivo, não um número chutado.

    O diagnóstico devolve o antes e o depois para ir ao `-estilo.json`.
    """
    cues = [dict(c) for c in (cues or [])]
    if not cues or not onsets:
        return cues, {"onsets": len(onsets or []), "encaixadas": 0,
                      "atraso_mediano_antes": None, "atraso_mediano_depois": None,
                      "porque": "no speech onset to snap to"}
    onsets = sorted(float(o) for o in onsets)

    def perto(t):
        """O começo de fala mais próximo de `t`, ou None se nenhum está perto."""
        melhor, dist = None, janela + 1
        for o in onsets:
            d = abs(t - o)
            if d < dist:
                melhor, dist = o, d
            elif o > t + janela:
                break
        return melhor if dist <= janela else None

    atrasos = []
    for c in cues:
        o = perto(float(c["start"]))
        c["_onset"] = o
        if o is not None:
            atrasos.append(float(c["start"]) - o)

    def mediana(xs):
        if not xs:
            return None
        ys = sorted(xs)
        meio = len(ys) // 2
        return ys[meio] if len(ys) % 2 else (ys[meio - 1] + ys[meio]) / 2.0

    antes = mediana(atrasos)
    sistematico = max(0.0, antes) if antes is not None else 0.0

    encaixadas = 0
    for c in cues:
        o = c.pop("_onset", None)
        if o is not None:
            delta = o - float(c["start"])          # negativo = puxa para trás
            encaixadas += 1
        else:
            delta = -sistematico                   # o atraso medido do arquivo
        # Só para trás, e nunca mais que o teto: um onset mal detectado não
        # pode arrancar a cue um segundo do lugar.
        delta = max(-maximo, min(0.0, delta))
        if not delta:
            continue
        c["start"] = round(float(c["start"]) + delta, 3)
        c["end"] = round(float(c["end"]) + delta, 3)
        c["words"] = [{"w": p["w"],
                       "start": round(float(p["start"]) + delta, 3),
                       "end": round(float(p["end"]) + delta, 3)}
                      for p in (c.get("words") or [])]

    depois = mediana([float(c["start"]) - perto(float(c["start"]))
                      for c in cues if perto(float(c["start"])) is not None])
    # SEGUNDA PASSADA, e ela existe por causa da regra assimétrica do dono: o
    # destaque pode chegar um pouco antes, nunca depois. Depois do encaixe
    # sobrou uma mediana positiva -- medido no clipe que ele olhou, +0,055s
    # contra a régua dele -- e sobra porque nem toda cue acha um começo de fala
    # (fala corrida não tem pausa para detectar).
    #
    # O resíduo NÃO é chutado: é o que acabou de ser medido neste arquivo. Ele
    # é subtraído de todas as cues, o que leva a mediana a zero e joga o erro
    # que sobrar para o lado de adiantar, que é o lado barato.
    residuo = 0.0
    if depois is not None and depois > 0.01:
        residuo = min(depois, maximo)
        for c in cues:
            c["start"] = round(float(c["start"]) - residuo, 3)
            c["end"] = round(float(c["end"]) - residuo, 3)
            for pal in c.get("words") or []:
                pal["start"] = round(float(pal["start"]) - residuo, 3)
                pal["end"] = round(float(pal["end"]) - residuo, 3)
        depois = mediana([float(c["start"]) - perto(float(c["start"]))
                          for c in cues if perto(float(c["start"])) is not None])
    # Uma cue puxada para trás não pode invadir a anterior.
    for i in range(1, len(cues)):
        if cues[i]["start"] < cues[i - 1]["end"]:
            cues[i]["start"] = cues[i - 1]["end"]
            for p in cues[i].get("words") or []:
                p["start"] = max(p["start"], cues[i]["start"])
                p["end"] = max(p["end"], p["start"])
    return cues, {
        "onsets": len(onsets),
        "encaixadas": encaixadas,
        "atraso_mediano_antes": None if antes is None else round(antes, 3),
        "atraso_mediano_depois": None if depois is None else round(depois, 3),
        "deslocamento_sistematico_s": round(sistematico, 3),
        "residuo_corrigido_s": round(residuo, 3),
    }
