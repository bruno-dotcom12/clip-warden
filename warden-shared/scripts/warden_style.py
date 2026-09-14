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
import subprocess
import tempfile

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
LEADING = 1.34                   # entrelinha do PRIME
STROKE = 3                       # contorno preto: legibilidade sobre qualquer placa
SCRIM_ALPHA = 150                # alfa no centro do scrim; some nas bordas

# Corpo da legenda. O estilo antigo usava height // 22 (87px num quadro de 1920),
# grande demais para duas linhas de fala; o briefing mede height // 26.
CAPTION_SIZE_DIVISOR = 26

# Limites de uma cue. Um segmento do Whisper de sete segundos vira um bloco de
# seis linhas parado na tela: estes três números são o que impede isso.
MAX_CHARS_PER_LINE = 26
MAX_LINES = 2
MAX_CUE_S = 2.2
MIN_CUE_S = 0.5

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


def text_png(lines, path, width, height, y_center, size, scrim=True):
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

    for line in lines:
        t = str(line).upper()
        x = (width - d.textlength(t, font=font)) / 2
        d.text((x, y + 4), t, font=font, fill=(0, 0, 0, 110))       # sombra de peso
        d.text((x, y), t, font=font, fill=(255, 255, 255, 255),
               stroke_width=STROKE, stroke_fill=(0, 0, 0, 255))
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

def reflow_cues(segments, max_chars=MAX_CHARS_PER_LINE, max_lines=MAX_LINES,
                max_cue_s=MAX_CUE_S):
    """Cues que cabem na tela e passam rápido, a partir dos segmentos do Whisper.

    É a correção do defeito 2.3. Cada segmento do Whisper virava uma cue inteira,
    então um segmento de sete segundos com trinta palavras virava um bloco de seis
    linhas parado na tela por sete segundos, cobrindo o rosto do peito ao queixo.

    A ordem importa e custou um render para ficar certa:

    1. JUNTAR primeiro. Uma legenda de origem -- e principalmente a automática do
       YouTube -- quebra a frase em blocos de 2 a 5 segundos que não respeitam
       pontuação nenhuma. Subdividir esses blocos produzia cues de duas palavras
       ("o MBL," sozinho na tela). Blocos contíguos viram um trecho só de fala.
    2. CORTAR por tempo. O trecho é percorrido palavra a palavra, cada uma com a
       duração que lhe cabe, e a cue fecha quando chegaria a `max_cue_s` ou
       quando o texto não caberia mais em `max_lines` linhas de `max_chars`.
       Cortar pelo tempo é o que faz a cue ter tamanho de fala e não de caractere.
    3. QUEBRAR em linhas, já com o texto definido.

    Nenhuma palavra se perde e nenhuma cue se sobrepõe à seguinte.
    """
    spans = _merge_spans(segments)
    out = []
    for start, end, text in spans:
        words = text.split()
        if not words:
            continue
        span = max(0.05, end - start)
        per_word = span / len(words)
        budget = max_chars * max_lines
        atual, t = [], start
        for word in words:
            cand = atual + [word]
            duracao = len(cand) * per_word
            cabe = len(" ".join(cand)) <= budget
            if atual and (duracao > max_cue_s or not cabe):
                fim = t + len(atual) * per_word
                out.append({"start": round(t, 3), "end": round(fim, 3),
                            "text": " ".join(atual),
                            "lines": _break_lines(" ".join(atual), max_chars,
                                                  max_lines)})
                t, atual = fim, [word]
            else:
                atual = cand
        if atual:
            fim = min(end, t + len(atual) * per_word)
            out.append({"start": round(t, 3), "end": round(max(t + 0.2, fim), 3),
                        "text": " ".join(atual),
                        "lines": _break_lines(" ".join(atual), max_chars,
                                              max_lines)})
    out.sort(key=lambda r: r["start"])
    # Duas legendas na tela ao mesmo tempo é o que a definição de pronto proíbe,
    # e aqui ela sairia da nossa própria aritmética.
    for i in range(len(out) - 1):
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = round(out[i + 1]["start"], 3)
    return [c for c in out if c["end"] > c["start"]]


def _merge_spans(segments, gap=0.4):
    """(início, fim, texto) de fala contínua, juntando cues coladas.

    O corte de uma cue na origem não é o fim de uma frase; é onde o legendador
    automático decidiu fechar o bloco. Juntar o que está colado devolve a frase,
    e é a frase que se corta bem.
    """
    limpos = []
    for seg in segments or []:
        texto = " ".join(str(seg.get("text") or "").split())
        if not texto:
            continue
        try:
            a, b = float(seg.get("start")), float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        if not (b > a >= 0):
            continue
        limpos.append((a, b, texto))
    limpos.sort(key=lambda r: r[0])
    spans = []
    for a, b, texto in limpos:
        if spans and a - spans[-1][1] <= gap:
            anterior = spans[-1]
            spans[-1] = (anterior[0], max(anterior[1], b),
                         anterior[2] + " " + texto)
        else:
            spans.append((a, b, texto))
    return spans


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


def approval_state(srt_path):
    """(aprovado?, motivo). O portão do defeito 2.5.

    O comentário do `to_srt` já dizia, com todas as letras, que uma palavra
    errada queimada é pior que legenda nenhuma e que essa checagem fica com a
    pessoa. Só que nada no fluxo obrigava essa pessoa a existir, e o agente
    queimou direto -- com `jokovic jokovic` e uma frase que o sujeito não disse.

    A aprovação é do CONTEÚDO, não do caminho: o hash é do arquivo, então
    reescrever o SRT depois de aprovar invalida a aprovação em vez de herdá-la.
    """
    if not srt_path or not os.path.isfile(srt_path):
        return False, "there is no subtitle file to approve"
    path = approval_path(srt_path)
    if not os.path.isfile(path):
        return False, (
            f"{os.path.basename(srt_path)} has not been approved. Whisper "
            f"mishears, and a wrong word burned on the screen is worse than no "
            f"caption because the viewer finds out before the clipper does. Read "
            f"the lines with `warden captions review {srt_path} --start <s> "
            f"--end <s>`, fix what is wrong, then approve with --approve.")
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
    return True, f"approved {saved.get('approved_at', 'at an unknown time')}"


def write_approval(srt_path):
    from datetime import datetime, timezone
    path = approval_path(srt_path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"sha256": srt_fingerprint(srt_path),
                   "approved_at": datetime.now(timezone.utc).isoformat(
                       timespec="seconds"),
                   "file": os.path.basename(srt_path)}, fh, indent=1)
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
        for name in sorted(os.listdir(tmp)):
            try:
                out.append(Image.open(os.path.join(tmp, name)).convert("L").copy())
            except Exception:
                continue
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
                except Exception:
                    continue
        if not shots:
            return None

        tw = 300
        th = int(tw * shots[0][1].height / max(1, shots[0][1].width))
        head = 46
        sheet = Image.new("RGB", (cols * tw, head + rows * th), (17, 17, 19))
        d = ImageDraw.Draw(sheet)
        try:
            title_font = _font(24)
            tag_font = _font(20)
        except Exception:
            title_font = tag_font = None

        w, h = shots[0][1].size
        caption = label or os.path.basename(video)
        d.text((12, 12), f"{caption}   {w}x{h}   {dur:.2f}s   {len(shots)} quadros",
               font=title_font, fill=(235, 235, 240))

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
    "a legenda tem no máximo duas linhas",
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
    if not dur or not out["width"]:
        return out

    frames = sample_frames(video, 0, dur, count=samples)
    if not frames:
        return out

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

    out["cut_interval_s"] = _cut_interval(video, dur)
    out["scale_variation"] = _scale_variation(frames)
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
    """Segundos médios entre trocas de plano, pelo detector de cena do ffmpeg."""
    try:
        done = subprocess.run(
            ["ffmpeg", "-nostats", "-hide_banner", "-i", video, "-vf",
             "select='gt(scene,0.4)',metadata=print", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180)
    except Exception:
        return None
    hits = done.stderr.count("lavfi.scene_score")
    return round(dur / hits, 2) if hits else round(dur, 2)


def _scale_variation(frames):
    """O quanto a escala mudou do primeiro ao último quadro, de 0 para cima.

    Zero é um trecho bruto: nenhum movimento de câmera, nenhuma variação de
    escala, que é o defeito 2.8. Comparar os quadros reescalados basta -- não
    interessa quanto, interessa se houve.
    """
    if len(frames) < 2:
        return None
    from PIL import ImageChops
    a = frames[0].resize((160, 284))
    b = frames[-1].resize((160, 284))
    dif = ImageChops.difference(a, b)
    return round(sum(dif.histogram()[40:]) / (160 * 284), 4)


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
    cue passa de 2,5s" deixam de ser coisas que alguém tem de olhar e viram
    aritmética que quebra um teste.
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
    cap = side.get("caption") or {}
    if cap.get("max_lines") and cap["max_lines"] > MAX_LINES:
        out.append(("REJECT", f"a caption cue is {cap['max_lines']} lines; the "
                              "six-line block that covered a face was this"))
    elif cap.get("cues"):
        out.append(("ok", f"{cap['cues']} cues, at most {cap['max_lines']} lines"))
    if cap.get("max_cue_s") and cap["max_cue_s"] > 2.5:
        out.append(("REJECT", f"a cue stays {cap['max_cue_s']}s on screen; over "
                              "2,5s it is a block parked on the picture"))
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
    return out


def check_against(measured, spec):
    """[(nível, mensagem)] de um render contra os limites, com a faixa ao lado."""
    out = []
    ranges = spec or {}
    for name, (lo, hi, what) in LIMITS.items():
        got = measured.get(name)
        if got is None:
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
