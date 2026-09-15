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
""".split())

# `when` estava faltando e o contact sheet de um render de teste mostrou: a cue
# saiu "My Pokemon journey started / when". A lista é uma lista, e a única forma
# de descobrir o que falta nela é olhar um clipe.

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
    escreva_assim = (f"A marca de acento é um PAR em volta da palavra: "
                     f"`{MARCA_ACENTO}assim{MARCA_ACENTO}`, colada na palavra e "
                     f"com espaço por fora. Não existe asterisco literal num "
                     f"hook -- se o asterisco é para aparecer na tela, ele não "
                     f"pode estar aqui.")
    if len(marcas) % 2:
        raise RuntimeError(
            f"o hook tem {len(marcas)} asterisco(s), que é ímpar, então há uma "
            f"marca de acento aberta e nunca fechada: {bruto!r}. " +
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
FOOTER_FADE = 0.26
FOOTER_ALPHA = 252


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


def footer_png(path, width, height, band=None):
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
    """
    Image, _D, _F, _Ft = _pil()
    band = int(band or height * 0.20)
    band = max(1, min(band, height))
    fade = max(1, int(band * FOOTER_FADE))
    column = [FOOTER_ALPHA if yy >= fade
              else int(FOOTER_ALPHA * (yy / fade) ** 1.15) for yy in range(band)]
    # Só a faixa, e o y em que ela entra -- pelo mesmo motivo do `text_png`.
    _gradient(width, column).save(path)
    return path, height - band


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
        if funcional and not fecha:
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
    return proibido, pontuada


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
        if palavras and _palavra(palavras[-1]) in NAO_FECHA_CUE:
            ruins.append(cue.get("text"))
    return ruins


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
    comando = (f"`warden captions review {srt_path} --start {start:.0f} "
               f"--end {end:.0f} --approve`"
               if start is not None and end is not None
               else f"`warden captions review {srt_path} --approve`")
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
    quando = saved.get("approved_at", "at an unknown time")
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
                          f"inside the {a:.0f}-{b:.0f}s approved {quando}")
    lista = "; ".join(f"{a:.0f}-{b:.0f}s" for a, b in janelas) or "none"
    return False, (
        f"this cut burns {float(start):.0f}-{float(end):.0f}s and the approved "
        f"windows are {lista}. Approving one window never approved the file: "
        f"that is how `aromasas` reached the screen. Read this window and "
        f"approve it with {comando}.")


def write_approval(srt_path, start=None, end=None):
    """Assina o SRT para UMA janela, ou para o arquivo inteiro quando não vem uma.

    Aprovações acumulam: aprovar a segunda janela não apaga a primeira, desde
    que o arquivo não tenha mudado. Se mudou, a lista recomeça, porque as
    janelas antigas apontam para palavras que já não estão ali.
    """
    from datetime import datetime, timezone
    path = approval_path(srt_path)
    impressao = srt_fingerprint(srt_path)
    janelas = []
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
        except Exception:
            janelas = []
    if start is None or end is None:
        janelas = ["all"]
    elif "all" not in janelas:
        nova = [float(start), float(end)]
        if nova not in janelas:
            janelas.append(nova)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"sha256": impressao,
                   "approved_at": datetime.now(timezone.utc).isoformat(
                       timespec="seconds"),
                   "file": os.path.basename(srt_path),
                   "windows": janelas}, fh, indent=1)
    return path


# ------------------------------------------------------------------ olhar o material

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
    edges = img.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist) or 1
    strong = sum(hist[70:])
    return strong / total


def burned_text_bands(frames, band=0.16):
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
    """
    if not frames:
        return {"top": False, "bottom": False, "bottom_reach": 0.0,
                "evidence": "no frames to look at"}
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
            if ratio > 1.9:
                votes[name] += 1
        # Até onde a faixa de baixo sobe. Um limiar fixo não serve: arte densa
        # (grades, multidão, cenário carregado) fica em 2-3x a vida toda e a
        # varredura sobe para sempre -- medido, deu 50% da altura e o degradê
        # comeu um terço do quadro. O que separa texto de arte densa é a QUEDA:
        # a faixa de letras é um pico, e ela acaba onde a densidade despenca para
        # menos da metade desse pico.
        step = BAND_SLICE
        densities = []
        for i in range(int(BAND_CAP / step) + 1):
            y0 = int(h * (1 - (i + 1) * step))
            y1 = int(h * (1 - i * step))
            if y1 > y0:
                densities.append(_edge_density(img.crop((0, y0, w, y1))) / base)
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
    # A mediana, não o máximo: um quadro em que a arte também é densa embaixo não
    # deve mandar o degradê cobrir metade do clipe.
    ordered = sorted(reaches)
    found["bottom_reach"] = ordered[len(ordered) // 2] if ordered else 0.0
    found["evidence"] = "; ".join(
        f"{name}: {votes[name]}/{len(frames)} frames, "
        f"{sum(ratios[name]) / len(ratios[name]):.1f}x the middle's edge density"
        for name in ("top", "bottom"))
    if found["bottom"]:
        found["evidence"] += f"; the bottom band reaches {found['bottom_reach']:.0%} up"
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
        for i in range(tiles):
            # Amostra no meio de cada fatia: o primeiro e o último quadro de um
            # render costumam ser transição, e um mosaico de transições não conta
            # o que o clipe mostra.
            t = dur * (i + 0.5) / tiles
            png = os.path.join(tmp, f"f{i:02d}.png")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss",
                            f"{t:.3f}", "-i", video, "-frames:v", "1", png],
                           capture_output=True, timeout=60)
            if os.path.isfile(png) and os.path.getsize(png) > 0:
                try:
                    shots.append((t, Image.open(png).convert("RGB")))
                except Exception as exc:
                    perdidos.append(f"{t:.1f}s ({type(exc).__name__})")
            else:
                perdidos.append(f"{t:.1f}s")
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
CHECKLIST = [
    "o hook cabe inteiro no quadro, sem corte lateral, em no máximo duas linhas",
    "o hook SAIU da tela: está nos primeiros quadros do mosaico e não nos últimos",
    "a legenda tem no máximo duas linhas",
    "nenhuma cue termina no meio da frase (preposição, artigo, conjunção)",
    "o destaque amarelo acompanha a fala, não a cue inteira acesa de uma vez",
    "não há duas legendas no mesmo quadro",
    "não há moldura, borda de material ou texto de terceiro cortado nas bordas",
    "o rosto do sujeito não está coberto por texto",
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
    "caption_lines_max",     # linhas na maior cue
    "cut_interval_s",        # média entre trocas de plano
    "scale_variation",       # o quanto a escala mudou ao longo do clipe
    "bottom_text",           # texto na faixa inferior
)


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
        return None, f"o detector de cena não rodou ({type(exc).__name__})"
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
        return None, (f"só {len(frames)} quadro(s) abriram deste arquivo, e "
                      f"variação de escala se mede entre dois")
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
        "variação de escala ao longo do clipe. Zero é um trecho bruto de vinte "
        "segundos com texto por cima. Separa limpo: 0,12 a 0,83 nos vinte "
        "aprovados, 0 num corte sem movimento."),
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
                 "text_width_ratio", "text_rows", "bottom_text",
                 "fps", "width", "height")


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
            out.append(("REJECT", f"the hook is {hook['width_px']}px wide against "
                                  f"{hook['usable_px']}px of usable width "
                                  f"({ratio:.2f}x): it is cropped at the frame edge"))
        else:
            out.append(("ok", f"hook fits: {hook['width_px']}px of "
                              f"{hook['usable_px']}px usable ({ratio:.2f}x)"))
    if hook.get("complete") is False:
        out.append(("REJECT",
                    f"the hook is {hook.get('chars')} characters and does not fit "
                    f"in {MAX_LINES} lines even at the {hook.get('size_px')}px "
                    "floor, so words were dropped from it. A hook missing words "
                    "is worse than one cropped at the edge: cropped shows on the "
                    "contact sheet, missing does not. Write a shorter hook."))
    if hook.get("lines") and hook["lines"] > MAX_LINES:
        out.append(("REJECT", f"the hook is on {hook['lines']} lines; at most "
                              f"{MAX_LINES} fit above the picture"))
    # O hook é uma promessa, e uma promessa que fica na tela vinte segundos vira
    # uma placa. Em 14/09 os dois cortes tinham a frase nos oito quadros do
    # mosaico. A folga de meio segundo é o fade.
    if hook.get("seconds_on_screen") is not None and side.get("duration_s"):
        ficou = float(hook["seconds_on_screen"])
        clipe = float(side["duration_s"])
        if clipe > HOOK_SECONDS + 1.0 and ficou > HOOK_SECONDS + 0.5:
            out.append(("REJECT",
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
        out.append(("REJECT",
                    f"this clip ENDS mid-sentence, on \"…{pendurados[-1]}\". The "
                    f"speech goes on past --end, so the last thing the viewer "
                    f"hears is half a clause. No caption setting fixes this: "
                    f"move --end to where the sentence closes."))
    outros = [t for t in pendurados
              if not (cap.get("ends_mid_sentence") and t == pendurados[-1])]
    if outros:
        amostra = "; ".join(f"…{t}" for t in outros[:3])
        out.append(("REJECT",
                    f"{len(outros)} caption cue(s) end on a word that needs "
                    f"what comes next ({amostra}). That is the 'HATE THE' and "
                    f"'CARA, EU VOTARIA NO' of 14/09: each half is grammatical "
                    f"and neither says anything"))
    if cap.get("cues") and cap.get("karaoke") is False:
        out.append(("REJECT",
                    "the caption was burned with no word-by-word timing, so the "
                    "whole cue lights at once. The per-word times come from the "
                    "reflow; a cue without them means they were lost on the way"))
    if cap.get("max_lines") and cap["max_lines"] > MAX_LINES:
        out.append(("REJECT", f"a caption cue is {cap['max_lines']} lines; the "
                              "six-line block that covered a face was this"))
    elif cap.get("cues"):
        out.append(("ok", f"{cap['cues']} cues, at most {cap['max_lines']} lines"))
    # 3,0s e não 2,5s, e o meio segundo foi pago para comprar o item 2. Uma cue
    # pode estourar MAX_CUE_S em até CUE_TOLERANCIA_S para alcançar uma
    # fronteira sintática; acima disso já não é a fronteira, é um bloco parado.
    teto_cue = round(MAX_CUE_S_TETO + 0.2, 2)
    if cap.get("max_cue_s") and cap["max_cue_s"] > teto_cue:
        out.append(("REJECT", f"a cue stays {cap['max_cue_s']}s on screen; over "
                              f"{teto_cue}s it is a block parked on the picture"))
    elif cap.get("max_cue_s"):
        out.append(("ok", f"longest cue {cap['max_cue_s']}s"))
    # Duas legendas num quadro só é a NOSSA por cima da do acervo. A pergunta é
    # essa e só essa: o material tem texto embaixo, nós queimamos legenda, e o
    # degradê não cobriu? Contar camadas sem perguntar se havia texto do acervo
    # reprovava clipes cujo material é limpo.
    tem_texto_do_acervo = (side.get("source_text") or {}).get("bottom")
    if cap.get("cues") and tem_texto_do_acervo and not side.get("footer_covered"):
        out.append(("REJECT", "this footage burns its own text along the bottom "
                              "and it was not covered, so our caption sits on "
                              "top of it: two captions in one frame"))
    if side.get("motion") is False:
        out.append(("REJECT", "no scale movement at all: this reads as raw footage"))
    # O número que a pessoa pediu contra o que o arquivo tem. Quando a campanha
    # obrigou a sair do pedido, `cut` já disse qual regra obrigou -- e aí a
    # diferença é legítima e aparece nas notas, não aqui. O que este portão pega
    # é a diferença SEM motivo, que foi o 20,6s e o 22,2s de 14/09.
    pedido, tem = side.get("asked_s"), side.get("duration_s")
    venceu = side.get("asked_overridden_by")
    if pedido and tem and venceu:
        out.append(("ok", f"{float(tem):.2f}s instead of the {float(pedido):.0f}s "
                          f"asked for, because {venceu} says so -- and the note "
                          f"says which rule, which is the whole requirement"))
    elif pedido and tem:
        folga = abs(float(tem) - float(pedido))
        if folga > 0.3:
            out.append(("REJECT",
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
        return [("REJECT", "no frame of this file could be opened, so nothing "
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
            porque = measured.get(name + "_why") or "não foi medido"
            out.append(("REJECT",
                        f"{name} não pôde ser medido ({porque}), e é o único "
                        f"limite que reprova alguma coisa. Sem ele este comando "
                        f"não tem opinião sobre este arquivo -- não trate o "
                        f"silêncio como aprovação."))
            continue
        band = ranges.get(name) or {}
        contexto = (f" (approved clips: {band['min']}–{band['max']})"
                    if "min" in band else "")
        if hi is not None and got > hi:
            out.append(("REJECT", f"{what}\n           measured {got}, the limit "
                                  f"is {hi}{contexto}"))
        elif lo is not None and got < lo:
            out.append(("REJECT", f"{what}\n           measured {got}, the floor "
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
                porque = ("o -estilo.json está aqui, mas este clipe foi cortado "
                          "SEM hook, então não há largura medida pelo PIL para "
                          "cruzar com ela")
            else:
                porque = ("o -estilo.json tem hook mas não anotou a largura "
                          "útil (`usable_px`), então não há com o que cruzar")
            out.append(("note", f"text_width_ratio: {px} medido nos pixels, "
                                f"{porque}. Sozinho ele não reprova: erra para "
                                f"mais em material com muito contraste."))
        return out
    nosso = largura / float(util)
    if px > 1.0 and nosso > 1.0:
        out.append(("REJECT",
                    f"largura de texto acima do quadro nos DOIS instrumentos: "
                    f"{px} medido nos pixels do arquivo e {nosso:.2f}x medido "
                    f"pelo PIL no hook ({largura}px contra {util}px úteis). "
                    f"Dois instrumentos com cegueiras diferentes apontando o "
                    f"mesmo lugar é a única leitura em que a métrica de pixel "
                    f"vota."))
    elif px > 1.0:
        out.append(("note",
                    f"os dois instrumentos discordam: os pixels dizem "
                    f"{px} de largura e o PIL diz que o hook cabe "
                    f"({nosso:.2f}x, {largura}px de {util}px). O PIL mediu com a "
                    f"fonte real antes de desenhar, então o hook está bem -- o "
                    f"que está largo é outra coisa no quadro. Texto queimado do "
                    f"próprio material, legenda de outro clipador, ou um "
                    f"letreiro com muito contraste. Olhe as bordas no mosaico."))
    elif nosso > 1.0:
        out.append(("note",
                    f"os dois instrumentos discordam ao contrário: o PIL diz "
                    f"que o hook não cabe ({nosso:.2f}x) e os pixels não viram "
                    f"({px}). O PIL manda, e o REJECT dele já está acima; os "
                    f"pixels perdem hook claro sobre fundo claro."))
    else:
        out.append(("ok", f"largura de texto confere nos dois: {px} nos pixels, "
                          f"{nosso:.2f}x no hook medido pelo PIL"))
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
        return [], f"o detector de fala não rodou ({type(exc).__name__})"
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
        return [], "nenhum começo de fala foi detectado neste trecho"
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
                      "porque": "nenhum começo de fala para encaixar"}
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
