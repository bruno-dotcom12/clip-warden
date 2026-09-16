#!/usr/bin/env python3
"""clip-warden: the command the skills and the persona call.

One message with one campaign link has to end in a clip the owner can post
without losing the work. Everything measurable on that path lives here, and
nothing here asks a model for an opinion: durations, pixels, hashtags and caps
are arithmetic, and arithmetic that a turn can talk itself out of is not a
guardrail. The model reads the brief and writes the rule set; this file decides.

  warden schema                         the rule set a campaign skill fills
  warden campaign save --file -         store one, after validating its shape
  warden campaign list | show <id>
  warden check <video> --campaign <id> [--caption -]
  warden package --campaign <id> --hook "..."
  warden log --campaign <id> --clip <name> --platform tiktok --url <url>
  warden status

Exit codes are the contract, because the caller is usually a shell: 0 the clip
may be posted, 1 it may not, 2 the command itself was wrong.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warden_rules as R
import warden_prefs as P

REPROVA, ATENCAO, OK = "REJECT", "WARN", "ok"


def state_dir():
    """Where campaigns and the post ledger live.

    The image puts this at /var/lib/hermes/warden and owns it. Outside the
    image, a developer running the tests gets a directory under their home
    rather than a permission error on a path that does not exist.
    """
    explicit = os.environ.get("WARDEN_DIR")
    if explicit:
        path = explicit
    elif os.path.isdir("/var/lib/hermes"):
        path = "/var/lib/hermes/warden"
    else:
        path = os.path.expanduser("~/.clip-warden")
    os.makedirs(os.path.join(path, "campaigns"), exist_ok=True)
    return path


def clips_dir():
    """Where a finished clip has to sit for the chat to accept it.

    The runtime will not attach a file from just anywhere. Its media validator
    denies the whole of /var/lib -- which is where this agent's state lives --
    and then allowlists a short list of cache directories back in, ahead of that
    denial. /var/lib/hermes/cache/videos is on that list; /var/lib/hermes/warden
    is not, and a clip written there is refused with a warning in a log the owner
    never reads. So the render lands where delivery is possible, and the rest of
    the state stays where it was.

    Outside the image there is no runtime to satisfy, so this follows the state
    directory and the tests get a writable path rather than a permission error.
    """
    if os.path.isdir("/var/lib/hermes") and not os.environ.get("WARDEN_DIR"):
        path = "/var/lib/hermes/cache/videos"
    else:
        path = os.path.join(state_dir(), "clips")
    os.makedirs(path, exist_ok=True)
    return path


def tracks_dir():
    """Onde as trilhas que o dono mandou ficam, entre uma sessão e outra.

    Existe porque perguntar de novo a cada clipe é o agente esquecendo o que já
    lhe deram. Nenhuma faixa é embarcada no repositório: a biblioteca de áudio
    do YouTube é gratuita para usar, e ainda assim ela não é NOSSA para
    redistribuir -- quem baixa concorda com os termos na própria conta. Então o
    agente aponta onde pegar e guarda o que a pessoa trouxer.
    """
    path = os.path.join(state_dir(), "tracks")
    os.makedirs(path, exist_ok=True)
    return path


AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")


def tracks_guardadas():
    """[(nome, caminho)] das faixas na pasta do dono, em ordem."""
    alvo = tracks_dir()
    if not os.path.isdir(alvo):
        return []
    return [(f, os.path.join(alvo, f)) for f in sorted(os.listdir(alvo))
            if os.path.splitext(f)[1].lower() in AUDIO_EXT]


def ficha_da_trilha(caminho):
    """O que já foi medido desta faixa: BPM, barra, drop, de onde veio.

    Medir custa segundos e o resultado não muda, então ele é escrito uma vez, no
    momento em que a faixa é guardada. Pedir a mesma medição a cada edit é a
    mesma falha que pedir a mesma faixa a cada clipe.
    """
    ficha = os.path.splitext(caminho)[0] + ".ficha.json"
    if not os.path.isfile(ficha):
        return {}
    try:
        with open(ficha, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


def resolve_track(nome):
    """Um nome curto vira o caminho da trilha guardada; um caminho fica como está.

    O nome pode vir SEM extensão. Medido em 15/09: o agente pediu
    `--track disfigure-blank` para uma faixa guardada como
    `disfigure-blank.mp3`, e o comando disse que ela não existia, com ela na
    pasta e listada na própria mensagem de erro. Uma faixa que o dono já mandou
    e que o comando recusa por causa de três letras é a faixa sendo pedida de
    novo.
    """
    if not nome:
        return nome
    aberto = os.path.expanduser(str(nome))
    if os.path.isfile(aberto):
        return aberto
    base = os.path.basename(aberto)
    guardada = os.path.join(tracks_dir(), base)
    if os.path.isfile(guardada):
        return guardada
    # Sem extensão, e sem diferença de maiúsculas: o dono deu um nome, não um
    # nome de arquivo.
    sem_ext = os.path.splitext(base)[0].lower()
    for arquivo, caminho in tracks_guardadas():
        if os.path.splitext(arquivo)[0].lower() == sem_ext:
            return caminho
    # E por pedaço do nome, quando ele identifica uma só.
    parciais = [c for a, c in tracks_guardadas() if sem_ext in a.lower()]
    if len(parciais) == 1:
        return parciais[0]
    tem = [a for a, _c in tracks_guardadas()]
    die(f"there is no track called {nome!r}: not a path on disk, and not in "
        f"{tracks_dir()}" + (f" (which holds: {', '.join(tem)})" if tem else
                             " (which is empty)")
        + (". More than one track matches that name, so say which."
           if len(parciais) > 1 else
           ". Send the audio file, or the link to it, and "
           "`warden tracks add <file|url>` keeps it."), code=1)


# O risco de cada fonte de trilha, medido no mundo real e não no texto da
# licença. Isto está aqui porque o dono já perdeu um vídeo por causa disso.
#
# `~/clipagem/PRIME/TRILHAS/BLOQUEADA/LEIA-ME.txt`, do dono, palavra por
# palavra: uma faixa baixada do Pixabay como royalty-free foi reivindicada no
# Content ID como "Aggressive Phonk Drift" de "Birthday PAPA", e o resultado foi
# **vídeo bloqueado no mundo todo e sem monetização**. A licença do Pixabay não
# impede o bloqueio automático: ela só dá base para CONTESTAR, depois.
#
# A única fonte com risco zero POR CONSTRUÇÃO para o YouTube é a Biblioteca de
# Áudio do próprio YouTube, porque o YouTube não reivindica o próprio catálogo.
# O NCS libera para uso em vídeo e pede crédito ao artista, e é o que o dono usa.
RISCO_DA_FONTE = {
    "youtube.com/audiolibrary": ("none by construction",
                                 "YouTube does not claim its own catalogue"),
    "studio.youtube.com": ("none by construction",
                           "YouTube does not claim its own catalogue"),
    "ncs.io": ("low", "NCS licenses for video use and asks for artist credit"),
    "nocopyrightsounds": ("low", "NCS licenses for video use and asks for "
                                 "artist credit"),
    "ncs": ("low", "NCS licenses for video use and asks for artist credit"),
    "pixabay.com": ("KNOWN TO HAVE BEEN CLAIMED",
                    "a Pixabay track the owner used was claimed on Content ID "
                    "and the video was blocked worldwide"),
    "uppbeat.io": ("unknown", "free libraries have been claimed before"),
}


def _risco_da_origem(origem, nome=""):
    """O risco da fonte, lido do link E do nome com que a faixa desceu.

    O nome conta porque o link quase nunca é `ncs.io`: o dono manda o vídeo do
    canal do NoCopyrightSounds no YouTube, e é o TÍTULO que diz de quem é a
    faixa ("NCS", "copyright free music"). Olhar só o domínio classificaria como
    desconhecida uma faixa cuja procedência está escrita no nome do arquivo.
    """
    baixo = (str(origem or "") + " " + str(nome or "")).lower()
    for chave, (risco, porque) in RISCO_DA_FONTE.items():
        if chave in baixo:
            return risco, porque
    if "copyright" in baixo and "free" in baixo:
        return "low", "the track names itself copyright-free"
    return "unknown", "nothing here knows this source"


def _guarda_trilha(destino, origem):
    """Mede a faixa UMA vez e escreve a ficha ao lado dela.

    O BPM não muda, então medir a cada edit é desperdício -- e foi o que
    aconteceu em 15/09: `warden beat` rodado no meio da conversa, e o número
    (92,3 BPM, barra de 2,6s, drop em 28,75s) virou mensagem para uma pessoa
    que não tinha o que fazer com ele.
    """
    ficha = {"name": os.path.basename(destino), "from": origem}
    risco, porque = _risco_da_origem(origem, os.path.basename(destino))
    ficha["claim_risk"] = risco
    ficha["claim_risk_why"] = porque
    try:
        import warden_beat
        grade = warden_beat.analyse(destino)
        ficha.update({k: grade[k] for k in
                      ("bpm", "beat_s", "bar_s", "drop_s", "duration_s")
                      if k in grade})
    except Exception as exc:
        # Uma faixa sem batida detectável continua sendo uma faixa. O que não
        # pode é a ficha dizer que mediu.
        ficha["beat_error"] = f"{type(exc).__name__}: {exc}"
    try:
        with open(os.path.splitext(destino)[0] + ".ficha.json", "w",
                  encoding="utf-8") as fh:
            json.dump(ficha, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return ficha


def cmd_tracks(args):
    alvo = tracks_dir()
    if args.action == "add":
        pedido = (args.file or "").strip()
        if not pedido:
            die("tracks add needs a file or a link: "
                "`warden tracks add <file|url>`", code=2)
        os.makedirs(alvo, exist_ok=True)
        if pedido.lower().startswith(("http://", "https://")):
            # O LINK QUE O DONO MANDA ENTRA. Medido em 15/09: o dono mandou o
            # canal oficial do NoCopyrightSounds e foi recusado duas vezes, com
            # uma aula de direitos autorais, e teve de escrever "esse video do
            # YouTube pode usar, nao tem copyright" para conseguir uma faixa que
            # o NCS publica exatamente para isso. A escolha é dele e o risco é
            # dele; o que este comando faz é guardar e ANOTAR o risco da fonte,
            # não discutir.
            try:
                destino = _media().baixa_trilha(pedido, alvo)
            except Exception as exc:
                die(f"{type(exc).__name__}: {exc}", code=1)
            origem = pedido
        else:
            origem_arquivo = os.path.expanduser(pedido)
            if not os.path.isfile(origem_arquivo):
                die(f"there is no file at {origem_arquivo}", code=1)
            if os.path.splitext(origem_arquivo)[1].lower() not in AUDIO_EXT:
                die(f"{os.path.basename(origem_arquivo)} is not an audio file",
                    code=1)
            destino = os.path.join(alvo, os.path.basename(origem_arquivo))
            if os.path.abspath(origem_arquivo) != os.path.abspath(destino):
                shutil.copy2(origem_arquivo, destino)
            # Uma faixa que já está na pasta e é "guardada" de novo não é erro:
            # é o pedido de medir uma que entrou antes de existir ficha. Copiar
            # sobre si mesma é que seria.
            origem = origem_arquivo
        ficha = _guarda_trilha(destino, origem)
        print(destino)
        curto = os.path.splitext(os.path.basename(destino))[0]
        if ficha.get("bpm"):
            print(f"kept: {curto}, {ficha['bpm']} BPM, "
                  f"{ficha['bar_s']}s a bar, gains body at "
                  f"{ficha['drop_s']}s.", file=sys.stderr)
        else:
            print(f"kept: {curto}. No beat could be measured "
                  f"({ficha.get('beat_error', 'no reason given')}), so an edit "
                  f"on it will not snap to a grid.", file=sys.stderr)
        print(f"`warden cut --track {curto}` finds it by name from now on -- "
              f"do not ask for this file again.", file=sys.stderr)
        if ficha.get("claim_risk") not in ("none by construction", "low"):
            print(f"source risk: {ficha['claim_risk']} -- "
                  f"{ficha['claim_risk_why']}.", file=sys.stderr)
        return 0

    guardadas = tracks_guardadas()
    for nome, caminho in guardadas:
        ficha = ficha_da_trilha(caminho)
        curto = os.path.splitext(nome)[0]
        if ficha.get("bpm"):
            print(f"{curto}\t{ficha['bpm']} BPM\t{ficha['bar_s']}s a bar\t"
                  f"drop {ficha['drop_s']}s\t{caminho}")
        else:
            print(f"{curto}\t(not measured)\t{caminho}")
    if not guardadas:
        print("no track has been kept yet. A file the owner sends, or a link "
              "from a free source -- the YouTube Audio Library "
              "(studio.youtube.com > Áudio) or NCS -- goes in with "
              "`warden tracks add <file|url>`, and it is measured once and "
              "never asked for again.", file=sys.stderr)
        return 1
    return 0


def safe_id(cid):
    """A campaign id is one flat name, and this is checked on the way in AND on
    the way out. Validating it only at save time was worth nothing: every read
    takes the id straight from a turn, and `../../something` walked out of the
    state directory and read a file that was never ours."""
    cid = str(cid or "")
    if not cid or not all(c.isalnum() or c in "-_" for c in cid):
        die(f"{cid!r} is not a campaign id: letters, digits, - and _ only")
    return cid


def campaign_path(cid):
    return os.path.join(state_dir(), "campaigns", f"{safe_id(cid)}.json")


def safe_out(path, what="output"):
    """Where this agent is allowed to write.

    Anything a turn can name, a prompt injection can name. ffmpeg runs with -y,
    so an unchecked path is an overwrite of any file the agent can reach. Output
    belongs under the agent's own state, or in scratch.
    """
    resolved = os.path.realpath(os.path.expanduser(path))
    allowed = [os.path.realpath(state_dir()), os.path.realpath(clips_dir()),
               os.path.realpath("/tmp")]
    for root in allowed:
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    die(f"refusing to write the {what} outside this agent's own directories: "
        f"{resolved}. Write it under {allowed[0]} or /tmp.")


def load_campaign(cid):
    path = campaign_path(cid)
    if not os.path.exists(path):
        known = list_campaigns()
        hint = f" Known: {', '.join(known)}." if known else " None stored yet."
        die(f"no campaign called {cid!r}.{hint}")
    return _json_ou_morre(path, "campaign")


def _json_ou_morre(caminho, oque):
    """Lê um JSON de estado, ou morre nomeando o ARQUIVO.

    Medido em 15/09/2026: um arquivo de estado corrompido derrubava
    `warden status` -- o primeiro comando que um estranho roda -- com um
    `JSONDecodeError` que não dizia QUAL arquivo, e portanto não dizia o que
    apagar. O conteúdo é do agente, não do dono: apagar é sempre seguro, e
    dizer isso é a diferença entre um minuto e uma reinstalação.
    """
    try:
        with open(caminho) as fh:
            return json.load(fh)
    except (ValueError, OSError) as exc:
        die(f"the {oque} file at {caminho} cannot be read ({type(exc).__name__}). "
            f"Nothing the owner typed lives in it -- this agent wrote it -- so "
            f"deleting it is safe and it will be written again.", code=2)


def list_campaigns():
    root = os.path.join(state_dir(), "campaigns")
    return sorted(f[:-5] for f in os.listdir(root) if f.endswith(".json"))


def die(message, code=2):
    print(f"warden: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_text(spec):
    """A path, or '-' for stdin. Captions arrive both ways."""
    if spec is None:
        return None
    if spec == "-":
        return sys.stdin.read()
    if not os.path.exists(spec):
        die(f"no such file: {spec}")
    with open(spec, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- probing

def probe(path):
    """What the file actually is, from ffprobe, never from its name.

    A clip named 1080x1920 is not a 1080x1920 clip, and the campaign checks the
    file. ffprobe ships in the base image; its absence is a broken install and
    is reported as one rather than skipped as an optional nicety.
    """
    if not shutil.which("ffprobe"):
        die("ffprobe is not on PATH, so no clip can be measured")
    if not os.path.isfile(path):
        die(f"there is no file at {path} to measure", code=1)
    # UMA chamada, não três. Medido em 15/09/2026: 0,198s por ffprobe, 0,618s
    # pelos três -- e a duração era medida aqui pela terceira vez no mesmo
    # clipe, depois do render e do contact sheet. O arquivo continua sendo
    # MEDIDO de propósito, que é o ponto desta função; só deixa de ser aberto
    # três vezes para isso.
    bruto = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path], capture_output=True, text=True).stdout
    try:
        tudo = json.loads(bruto or "{}")
    except ValueError:
        tudo = {}
    correntes = tudo.get("streams") or []
    v_stream = next((c for c in correntes if c.get("codec_type") == "video"), {})
    a_stream = next((c for c in correntes if c.get("codec_type") == "audio"), {})
    fields = {k: str(v_stream[k]) for k in
              ("width", "height", "r_frame_rate", "codec_name") if k in v_stream}
    duration = str((tudo.get("format") or {}).get("duration") or "")
    audio = str(a_stream.get("codec_name") or "")
    def number(value, cast=float):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None
    return {
        "width": number(fields.get("width"), int),
        "height": number(fields.get("height"), int),
        "codec": fields.get("codec_name"),
        "fps": fields.get("r_frame_rate"),
        "duration_s": number(duration),
        "audio_codec": audio or None,
        # Faixas de legenda DENTRO do contêiner. Não é a legenda queimada: o
        # que este projeto queima vira pixel e o ffprobe não vê pixel. Está
        # aqui porque sem campanha o `check` é uma MEDIÇÃO, e "tem faixa de
        # legenda?" é uma das poucas perguntas sobre legenda que um ffprobe
        # responde. Quem quer saber se a fala está escrita na tela abre o
        # contact sheet, que é o único portão que olha a imagem.
        "subtitle_tracks": sum(1 for c in correntes
                               if c.get("codec_type") == "subtitle"),
        "size_mb": round(os.path.getsize(path) / (1024 * 1024), 2),
    }


def fold(text):
    """Lowercase, accent-stripped. A banned word does not stop being banned
    because the caption spelled it with an accent or in capitals."""
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return stripped.lower()


def whole(term, folded):
    r"""O termo aparece no texto como token inteiro? Recebe e compara já dobrado.

    Dois defeitos medidos, os dois silenciosos, os dois nesta fronteira:

    `\b` antes do termo assume que ele começa com caractere de palavra. Com
    `#ad` proibido, `re.search(r"\b\#ad", "post #ad de novo")` dá False -- o
    `\b` exige palavra à esquerda do `#`, e à esquerda tem espaço. Num campo
    vizinho de required_hashtags, hashtag é a forma mais provável do termo
    banido, e a saída dizia "Nothing blocks this clip".

    E não havia fronteira nenhuma à direita: `ad` proibido casava dentro de
    "advogado", e `#prime` obrigatório era conferido por substring, então uma
    legenda que só trazia `#primevideobr` passava como se cumprisse a campanha
    -- a plataforma lê uma tag só, e não é a pedida.

    Cada ponta olha o caractere do PRÓPRIO termo: só se cobra fronteira do lado
    em que o termo tem caractere de palavra. Assim `#prime` casa em `#prime,` no
    fim da frase mas não em `#primevideobr`, e `ad` casa em `#ad` mas não em
    "advogado". Custo aceito e conhecido: `aposta` proibido não pega mais
    "apostas" -- a campanha lista as duas, e errar aqui erra alto e visível, em
    vez de liberar o post em silêncio.
    """
    head = r"(?<!\w)" if re.match(r"\w", term) else ""
    tail = r"(?!\w)" if re.search(r"\w$", term) else ""
    return re.search(head + re.escape(term) + tail, folded) is not None


# ---------------------------------------------------------------- checking

def check(rules, media, caption, ledger):
    """Every finding, in the order a person would want to read them."""
    out = []
    def reject(m): out.append((REPROVA, m))
    def warn(m):   out.append((ATENCAO, m))
    def ok(m):     out.append((OK, m))

    # Shape of the file.
    want_w = R.get(rules, "video.width")
    want_h = R.get(rules, "video.height")
    w, h = media.get("width"), media.get("height")
    if want_w and want_h:
        if (w, h) != (want_w, want_h):
            reject(f"{w}x{h}: the campaign asks for {want_w}x{want_h}")
        else:
            ok(f"resolution {w}x{h}")
    want_aspect = R.get(rules, "video.aspect")
    if want_aspect and not (w and h):
        # Sem dimensões legíveis a regra de aspecto sumia da saída inteira, e o
        # relatório terminava em "Nothing blocks this clip" sobre um arquivo cujo
        # formato ninguém conferiu.
        reject(f"this campaign requires {want_aspect} and the file gives no "
               "readable width or height, so the shape could not be checked")
    if want_aspect and w and h:
        try:
            a, b = (int(n) for n in str(want_aspect).split(":"))
            if abs((w / h) - (a / b)) > 0.01:
                reject(f"aspect {w}:{h} is not the {want_aspect} the campaign asks for")
            else:
                ok(f"aspect {want_aspect}")
        except (ValueError, ZeroDivisionError):
            warn(f"the rule set has an aspect this tool cannot read: {want_aspect!r}")

    # Duration, the single most common reason a submission is thrown out.
    dur = media.get("duration_s")
    lo, hi = R.get(rules, "video.duration_min_s"), R.get(rules, "video.duration_max_s")
    duration_rejected = False
    if dur is None:
        reject("the file has no readable duration")
        duration_rejected = True
    else:
        if lo is not None and dur < lo:
            reject(f"{dur:.1f}s is under the {lo}s minimum")
            duration_rejected = True
        if hi is not None and dur > hi:
            reject(f"{dur:.1f}s is over the {hi}s maximum")
            duration_rejected = True
        if (lo is None) and (hi is None):
            warn(f"{dur:.1f}s, and the brief sets no duration limit this tool knows")
        elif not duration_rejected:
            ok(f"duration {dur:.1f}s")

    # Audio. Some campaigns require it; the ones that add the official track on
    # the platform require its absence, and a clip that arrives with a scratch
    # track gets the sound rejected at upload.
    policy = R.get(rules, "video.audio")
    has_audio = bool(media.get("audio_codec"))
    if policy == "required" and not has_audio:
        reject("no audio track, and the campaign requires one")
    elif policy == "forbidden" and has_audio:
        reject(f"carries an audio track ({media['audio_codec']}), and the campaign "
               "requires the sound to be added on the platform")
    elif policy:
        ok(f"audio: {media.get('audio_codec') or 'none'}, as required")
    else:
        warn(f"audio: {media.get('audio_codec') or 'none'}, and the brief is silent "
             "about where sound must come from")

    cap_mb = R.get(rules, "video.max_file_mb")
    if cap_mb and media.get("size_mb", 0) > cap_mb:
        reject(f"{media['size_mb']} MB is over the {cap_mb} MB the campaign accepts")

    # The caption. Cheap to get right, and it is what the reviewer reads first.
    required_tags = R.get(rules, "caption.required_hashtags", [])
    required_ats = R.get(rules, "caption.required_mentions", [])
    required_text = R.get(rules, "caption.required_text", [])
    banned = R.get(rules, "caption.banned_terms", [])
    max_len = R.get(rules, "caption.max_len")
    if caption is None:
        if required_tags or required_ats or required_text:
            warn("no caption was given to check, and this campaign has caption rules")
    else:
        folded = fold(caption)
        missing = [t for t in required_tags if not whole(fold(t), folded)]
        if missing:
            reject("the caption is missing " + ", ".join(missing))
        elif required_tags:
            ok(f"all {len(required_tags)} required hashtags present")
        missing_ats = [a for a in required_ats if not whole(fold(a), folded)]
        if missing_ats:
            reject("the caption is missing " + ", ".join(missing_ats))
        elif required_ats:
            ok("required mentions present")
        for phrase in required_text:
            if fold(phrase) not in folded:
                reject(f"the caption must carry the exact words: {phrase!r}")
        hits = [term for term in banned if whole(fold(term), folded)]
        if hits:
            reject("the caption uses terms this campaign bans: " + ", ".join(hits))
        if max_len and len(caption) > max_len:
            reject(f"the caption is {len(caption)} characters, over the {max_len} allowed")

    # The cap and the deadline, from this install's own ledger of what it has
    # already submitted. A clipper past the cap is working for free.
    cap = R.get(rules, "posting.per_clipper_cap")
    already = len(ledger)
    if cap:
        if already >= cap:
            reject(f"{already} posts already logged for this campaign and the cap is {cap}")
        else:
            ok(f"{already} of {cap} posts used")
    deadline = R.get(rules, "posting.deadline")
    if deadline:
        try:
            left = (datetime.strptime(deadline, "%Y-%m-%d").date()
                    - datetime.now(timezone.utc).date()).days
            if left < 0:
                reject(f"the campaign closed on {deadline}")
            elif left <= 2:
                warn(f"{left} day(s) left: the campaign closes {deadline}")
            else:
                ok(f"{left} days left")
        except ValueError:
            warn(f"the rule set has a deadline this tool cannot read: {deadline!r}")

    # What nobody checked. Repeated on every verdict on purpose: an approval
    # that hides its blind spots is how a clipper learns about a rule from a
    # rejection notice.
    for item in R.get(rules, "unknown", []):
        warn(f"not checked, the brief does not settle it: {item}")
    for item in R.get(rules, "sources.allowed", []):
        warn(f"only you can confirm this: footage must be {item}")
    for item in R.get(rules, "sources.forbidden", []):
        warn(f"only you can confirm this: footage must not be {item}")
    pct = R.get(rules, "sources.official_min_screen_pct")
    if pct:
        warn(f"only you can confirm this: official footage must fill at least {pct}% of screen")
    return out


def verdict(findings):
    return REPROVA if any(level == REPROVA for level, _ in findings) else OK


def render(findings, rules, media):
    # A LINHA MEDIDA, e ela cresceu de propósito em 15/09/2026.
    #
    # Sem campanha não há regra para julgar, e a persona deste projeto manda
    # que todo número citado venha do `warden` e nunca da leitura do modelo --
    # "se você vai dizer que um clipe tem 28 segundos, rode o check e cite".
    # Se este cabeçalho não traz o número, não existe de onde citá-lo, e a
    # única saída que sobra ao agente é falar de cabeça, que é o que a regra
    # proíbe. Então tudo o que o ffprobe respondeu aparece aqui: duração,
    # resolução, fps, codec, som e faixas de legenda.
    nome = rules.get("name") or rules.get("id")
    fps = media.get("fps")
    if fps and "/" in str(fps):
        try:
            a, b = str(fps).split("/", 1)
            fps = f"{float(a) / float(b):.3g}"
        except (ValueError, ZeroDivisionError):
            pass
    medido = (f"{media.get('width')}x{media.get('height')}, "
              f"{(media.get('duration_s') or 0):.1f}s, "
              f"{fps or '?'} fps, {media.get('codec') or 'codec ?'}, "
              f"audio {media.get('audio_codec') or 'none'}, "
              f"{media.get('subtitle_tracks', 0)} subtitle track(s), "
              f"{media.get('size_mb')} MB")
    lines = [f"{nome}  |  {medido}" if nome
             else f"(no campaign)  |  {medido}", ""]
    order = {REPROVA: 0, ATENCAO: 1, OK: 2}
    label = {REPROVA: "REJECT ", ATENCAO: "CHECK  ", OK: "ok     "}
    for level, message in sorted(findings, key=lambda f: order[f[0]]):
        lines.append(f"  {label[level]} {message}")
    lines.append("")
    if verdict(findings) == REPROVA:
        lines.append("Do not post this one. Fix what is marked REJECT and run it again.")
    elif not nome:
        # SEM CAMPANHA O VEREDITO NÃO É "PASSA". Não pode ser: não havia regra
        # para ser quebrada, então nada foi aprovado -- foi medido. Dizer
        # "nothing blocks this clip" aqui seria a mesma mentira que um campo
        # adivinhado ("a guessed field reads as a rule and gets a clipper
        # rejected for obeying you"), com a diferença de que ninguém sequer
        # adivinhou: o silêncio estaria passando por aprovação.
        lines.append("MEASURED, NOT APPROVED: no campaign was given, so there "
                     "was no rule to judge this against. The numbers above are "
                     "the file; every field is unchecked. Cite the numbers, and "
                     "do not say this clip passes anything.")
        lines.append("Whether the words are on screen is not in this list: a "
                     "burned caption is pixels, and ffprobe reads tracks. The "
                     "contact sheet, written beside each render, is where it shows.")
    else:
        lines.append("Nothing blocks this clip. The CHECK lines are yours to confirm.")
    return "\n".join(lines)


# ---------------------------------------------------------------- ledger

def ledger_path():
    return os.path.join(state_dir(), "posts.json")


def ledger_all():
    path = ledger_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        # O ledger é contagem, não verdade do dono: um arquivo ilegível vira
        # lista vazia com aviso, em vez de derrubar o comando. `entregas_all`
        # já fazia assim; este não fazia, e era o mesmo par no mesmo arquivo.
        print(f"warden: the post ledger at {path} is unreadable and is being "
              f"treated as empty. The campaign cap cannot be trusted until it "
              f"is deleted and rebuilt.", file=sys.stderr)
        return []


def ledger_for(cid):
    return [row for row in ledger_all() if row.get("campaign") == cid]


def ledger_add(row):
    rows = ledger_all()
    rows.append(row)
    tmp = ledger_path() + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, ledger_path())


# ------------------------------------------------------- delivery ledger

# Um clipe renderizado não é um clipe entregue, e até 14/09 essa frase era só
# uma frase: o repo inteiro não tinha uma linha de código que soubesse quantos
# envios estavam devendo. O `--plan` contava RENDERS numa lista Python que
# morria com o processo, e o modelo ficava sendo a única memória de quem já
# tinha ido. Na rodada do vlog dois cortes saíram e um chegou, e a falta só
# apareceu porque o dono contou os arquivos na tela dele.
#
# Isto é essa contagem, em disco. `deliver()` anota o clipe como DEVENDO no
# instante em que imprime o `MEDIA:`; `warden delivered <clipe>` risca da
# lista depois que o envio voltou; `warden delivered` sozinho responde
# "falta alguém?" e sai 1 enquanto faltar. Não impede o modelo de mentir --
# nada impede -- mas troca um esquecimento invisível por uma pergunta que tem
# resposta.

PRAZO_ENTREGA_H = 6


def entregas_path():
    return os.path.join(state_dir(), "entregas.json")


def entregas_all():
    path = entregas_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        # Um registro corrompido não pode derrubar um corte. Perder a conta é
        # ruim; recusar-se a renderizar por causa dela é pior.
        return []


def _entregas_grava(rows):
    tmp = entregas_path() + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, entregas_path())


def _recente(row):
    """Uma pendência velha não é uma dívida, é lixo de outra sessão."""
    try:
        quando = datetime.fromisoformat(row.get("at", ""))
    except ValueError:
        return False
    idade = datetime.now(timezone.utc) - quando
    return idade.total_seconds() < PRAZO_ENTREGA_H * 3600


def conversa_de_agora():
    """O id da conversa que roda AGORA, ou None quando não dá para saber.

    Existe porque a dívida de entrega é com uma CONVERSA, não com a máquina.
    Medido em 16/09/2026, no segundo teste real: o dono mandou `/new`, e a
    sessão nova abriu tentando acertar cinco clipes da sessão anterior -- duas
    versões intermediárias de um ciclo de rerender entre elas. O agente gastou
    quatro chamadas conferindo arquivo antigo e vasculhando sessões passadas em
    vez de atender o pedido novo.

    E não é só desperdício: um clipe devido numa conversa que não existe mais
    NÃO PODE ser entregue na nova. Quem recebesse veria um arquivo que não
    pediu, sem o texto que o acompanhava.

    `WARDEN_SESSION_ID` primeiro, porque é exato; depois a sessão da mensagem
    mais nova do state.db, que é a deste turno. `None` quando o banco não dá
    para ler -- e aí o livro volta a valer por tempo, como antes, porque perder
    a cobrança inteira seria pior que cobrar demais.
    """
    explicita = (os.environ.get("WARDEN_SESSION_ID") or "").strip()
    if explicita:
        return explicita
    caminho = os.environ.get("WARDEN_STATE_DB", "/var/lib/hermes/state.db")
    if not os.path.isfile(caminho):
        return None
    try:
        import sqlite3 as _sq
        con = _sq.connect("file:" + caminho + "?mode=ro", uri=True, timeout=2.0)
        try:
            linha = con.execute(
                "select session_id from messages order by id desc limit 1").fetchone()
        finally:
            con.close()
        return linha[0] if linha and linha[0] else None
    except Exception:
        return None


def entregas_pendentes():
    """Os clipes liberados que ninguém confirmou ter enviado, ainda de pé.

    Um arquivo apagado sai da conta: o que se cobra é entrega de clipe que
    existe. E a janela de seis horas existe para o portão valer dentro de uma
    conversa sem virar um bloqueio permanente na primeira vez que alguém fechar
    o terminal no meio de um lote.
    """
    agora = conversa_de_agora()
    def desta_conversa(r):
        # Sem saber a conversa de agora, ou sem a conversa anotada na linha
        # (livro escrito por uma versão anterior), vale o prazo como antes:
        # cobrar demais é ruim, não cobrar nada é pior.
        if agora is None or r.get("session") is None:
            return True
        return r.get("session") == agora
    return [r for r in entregas_all()
            if not r.get("sent") and _recente(r) and desta_conversa(r)
            and os.path.isfile(r.get("clip", ""))]


def entregas_registra(clip):
    """Anota um clipe como devendo envio. Idempotente por caminho."""
    clip = os.path.abspath(clip)
    rows = [r for r in entregas_all() if _recente(r) and r.get("clip") != clip]
    rows.append({"clip": clip,
                 "at": datetime.now(timezone.utc).isoformat(),
                 "session": conversa_de_agora(),
                 "sent": False, "sent_at": None})
    try:
        _entregas_grava(rows)
    except OSError:
        pass                      # sem estado gravável, o resto do corte segue
    return clip


def entregas_registro(clip):
    """A linha deste clipe no livro, com o instante em que foi liberado.

    `at_ts` em segundos epoch, porque quem compara com o log do gateway compara
    em segundos. `None` quando o clipe não está devendo -- já confirmado, ou
    nunca liberado por este `cut`.
    """
    alvo = os.path.abspath(os.path.expanduser(clip))
    for row in entregas_all():
        if row.get("clip") == alvo and not row.get("sent"):
            try:
                quando = datetime.fromisoformat(row["at"]).timestamp()
            except (ValueError, KeyError, TypeError):
                quando = 0.0
            saida = dict(row)
            saida["at_ts"] = quando
            return saida
    return None


def entregas_confirma(clip):
    """Risca um clipe da lista. Devolve (achou, pendentes_restantes)."""
    alvo = os.path.abspath(os.path.expanduser(clip))
    rows = entregas_all()
    achou = False
    for row in rows:
        if row.get("clip") == alvo and not row.get("sent"):
            row["sent"] = True
            row["sent_at"] = datetime.now(timezone.utc).isoformat()
            achou = True
    if achou:
        _entregas_grava(rows)
    return achou, entregas_pendentes()


# ------------------------------------------------- o livro dos renders que passaram

# Um clipe que PASSOU pelos três portões não muda se for renderizado de novo com
# os mesmos parâmetros: a mesma janela do mesmo arquivo, o mesmo hook, a mesma
# legenda e a mesma duração produzem o mesmo quadro. Renderizar de novo é gastar
# o tempo do render para chegar exatamente onde já se estava.
#
# Medido em 15/09/2026, no primeiro teste real: o ciclo de re-render custou cerca
# de 2 minutos do pedido, e nenhum dos renders repetidos mudou um pixel.
#
# A impressão digital NÃO inclui o `--out`. É de propósito: o mesmo corte com
# outro nome de arquivo continua sendo o mesmo corte, e foi com outro nome que
# ele voltou a ser renderizado. O que ela inclui é tudo o que um quadro depende:
# a fonte (com tamanho e mtime, porque um arquivo reescrito é outro arquivo), a
# janela, a duração pedida, o hook, a legenda, a trilha, o som, o crop, o motion
# e a campanha cujas regras julgam o resultado.
PRAZO_RENDER_H = 24


def renders_path():
    return os.path.join(state_dir(), "renders.json")


def _marca_do_arquivo(caminho):
    """(caminho, tamanho, mtime) -- ou só o caminho quando ele não existe.

    O tamanho e o mtime entram porque um SRT reescrito no mesmo caminho é outra
    legenda, e reaproveitar o clipe anterior nesse caso seria entregar a legenda
    velha com a cara de nova.
    """
    bruto = os.path.abspath(os.path.expanduser(str(caminho)))
    try:
        st = os.stat(bruto)
        return [bruto, st.st_size, int(st.st_mtime)]
    except OSError:
        return [bruto, None, None]


def impressao_do_corte(args, rules, planos, sound, segundos):
    """O que faz deste corte ESTE corte, como uma string curta."""
    import hashlib
    campos = {
        "source": _marca_do_arquivo(args.source) if args.source else None,
        "start": None if args.start is None else round(float(args.start), 3),
        "end": None if args.end is None else round(float(args.end), 3),
        "seconds": None if segundos is None else round(float(segundos), 3),
        "any_length": bool(getattr(args, "any_length", False)),
        "hook": getattr(args, "hook", None),
        "subtitles": (_marca_do_arquivo(args.subtitles)
                      if getattr(args, "subtitles", None) else None),
        "track": (_marca_do_arquivo(args.track)
                  if getattr(args, "track", None) else None),
        "track_start": getattr(args, "track_start", None),
        "sound": sound,
        "crop": getattr(args, "crop", None),
        "motion": bool(getattr(args, "motion", True)),
        "cover_footer": getattr(args, "cover_footer", None),
        "shots": [[round(a, 3), round(b, 3)] for a, b in (planos or [])],
        # A campanha entra porque ela é quem JULGA: o mesmo arquivo passa numa
        # campanha e é reprovado na outra, e reaproveitar o veredito de uma
        # delas na outra seria o portão respondendo pela campanha errada.
        "campaign": R.get(rules, "id"),
    }
    bruto = json.dumps(campos, sort_keys=True, ensure_ascii=False,
                       default=str)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:32]


def renders_all():
    try:
        with open(renders_path()) as fh:
            linhas = json.load(fh)
    except (OSError, ValueError):
        # O mesmo princípio do livro de entregas: perder a conta é ruim,
        # recusar-se a renderizar por causa dela é pior.
        return []
    return linhas if isinstance(linhas, list) else []


def renders_liberado(impressao):
    """A linha deste corte, se ele já passou E o mp4 ainda está lá.

    O MP4, e só ele, desde 16/09/2026. Esta função exigia o contact sheet
    também, porque `deliver` recusava a entrega sem ele. `deliver` não recusa
    mais -- o dono trocou esse portão pelo tempo dele -- e a exigência que
    sobrou aqui mandava RENDERIZAR TUDO DE NOVO quando só o mosaico tinha
    sumido do disco. Um clipe pronto, aprovado e no lugar, refeito do zero por
    causa de um JPEG ausente.
    """
    for row in renders_all():
        if row.get("fingerprint") != impressao or not _recente_render(row):
            continue
        clip = row.get("clip")
        if clip and os.path.isfile(clip):
            return row
    return None


def _recente_render(row):
    # `TypeError` junto com `ValueError` porque `renders.json` é um arquivo em
    # disco que outra versão (ou uma mão) pode ter escrito com `at: null`, e
    # `fromisoformat(None)` levanta TypeError. Um livro estragado não pode
    # derrubar um corte.
    if not isinstance(row, dict):
        return False
    try:
        quando = datetime.fromisoformat(row.get("at", ""))
    except (ValueError, TypeError):
        return False
    idade = datetime.now(timezone.utc) - quando
    return idade.total_seconds() < PRAZO_RENDER_H * 3600


def renders_registra(impressao, result):
    """Anota que este corte passou, com o que `deliver` precisa para repeti-lo."""
    rows = [r for r in renders_all()
            if _recente_render(r) and r.get("fingerprint") != impressao]
    rows.append({"fingerprint": impressao,
                 "clip": os.path.abspath(result.get("out") or ""),
                 "sheet": os.path.abspath(result.get("sheet") or ""),
                 "at": datetime.now(timezone.utc).isoformat(),
                 "asked_for_captions": bool(result.get("asked_for_captions")),
                 "style": result.get("style") or {},
                 "notes": list(result.get("notes") or [])})
    try:
        tmp = renders_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(rows, fh, indent=1, ensure_ascii=False, default=str)
        os.replace(tmp, renders_path())
    except (OSError, TypeError, ValueError):
        pass                      # sem estado gravável, o corte segue igual


# ---------------------------------------------------------------- commands

def cmd_schema(args):
    print(json.dumps(R.blank(), indent=2, ensure_ascii=False))
    return 0


def cmd_campaign(args):
    if args.action == "list":
        names = list_campaigns()
        print("\n".join(names) if names else "no campaigns stored yet")
        return 0
    if args.action == "show":
        cid = args.id or args.which
        if not cid:
            die("show needs the campaign id, as `campaign show <id>`")
        print(json.dumps(load_campaign(cid), indent=2, ensure_ascii=False))
        return 0
    if args.action == "save":
        # --json first, because a model driving this through a shell cannot
        # always be sure its heredoc reached stdin, and a rule set that silently
        # arrives empty is how an agent ends up believing it saved something.
        raw = args.json if args.json else read_text(args.file or "-")
        if not (raw or "").strip():
            die("nothing arrived on stdin. Pass the rule set with --json '<json>' "
                "or --file <path>; do not report this campaign as stored.")
        try:
            rules = json.loads(raw)
        except json.JSONDecodeError as exc:
            die(f"that is not valid JSON: {exc}")
        problems = R.validate(rules)
        if problems:
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            die("the rule set was not stored")
        with open(campaign_path(rules["id"]), "w", encoding="utf-8") as fh:
            json.dump(rules, fh, indent=2, ensure_ascii=False)
        # Read it back before saying it is there. The whole agent rests on the
        # stored rule set, and "saved" is a claim, not a hope.
        try:
            with open(campaign_path(rules["id"])) as fh:
                back = json.load(fh)
        except Exception as exc:
            die(f"wrote {rules['id']} but could not read it back: {exc}")
        unknown = len(back.get("unknown") or [])
        print(f"stored and verified {back['id']} at {campaign_path(back['id'])}"
              + (f", with {unknown} thing(s) the brief left open" if unknown else ""))
        return 0
    die(f"unknown campaign action {args.action!r}")


def cmd_check(args):
    """Mede o arquivo, e julga-o quando existe uma campanha para julgá-lo.

    `--campaign` deixou de ser obrigatória em 15/09/2026, e o motivo é uma
    corrente de regras que não fechava. A persona manda citar números do
    `warden` e nunca da própria leitura; a tabela de provas dela manda provar
    a duração com `warden check`; a skill do clipe PROÍBE inventar uma campanha
    para passar de um flag obrigatório -- "that was the road into the caption
    question that stopped every first clip". Com `--campaign` obrigatória, um
    link solto deixava o agente sem saída nenhuma: precisava checar, não podia
    checar, não podia inventar campanha e não podia falar de cabeça.

    Sem campanha isto é uma MEDIÇÃO e diz que é: cada campo sai como não
    conferido, e o veredito não afirma que o clipe passa, porque não havia
    regra da qual passar.
    """
    if not os.path.exists(args.video):
        die(f"no such clip: {args.video}")
    rules = regras_de(args.campaign)
    media = probe(args.video)
    caption = read_text(args.caption)
    findings = check(rules, media, caption,
                     ledger_for(args.campaign) if args.campaign else [])
    if args.json:
        print(json.dumps({"verdict": verdict(findings),
                          # Sem campanha, `verdict` diz OK porque nada foi
                          # rejeitado -- e OK sem regra não é aprovação. O
                          # campo abaixo é o que impede um `--json` de ser lido
                          # como um passe: ele diz que não houve julgamento.
                          "judged": bool(args.campaign),
                          "campaign": args.campaign,
                          "media": media,
                          "findings": [{"level": l, "message": m} for l, m in findings]},
                         indent=2, ensure_ascii=False))
    else:
        print(render(findings, rules, media))
    return 1 if verdict(findings) == REPROVA else 0


def _monta_legenda(rules, hook):
    """(texto, limite). A legenda que a campanha exige, montada uma vez só.

    Extraída de `cmd_package` quando `warden tiktok` passou a precisar da mesma
    coisa: o rascunho sobe sem legenda -- o endpoint de inbox do TikTok não tem
    campo para ela -- então quem publica cola este texto, e ele tem de ser o
    MESMO que o `package` imprime. Duas montagens divergem no dia em que uma das
    duas for corrigida.
    """
    parts = [hook.strip()] if hook else []
    parts += [t for t in R.get(rules, "caption.required_text", [])]
    tail = " ".join(R.get(rules, "caption.required_mentions", [])
                    + R.get(rules, "caption.required_hashtags", []))
    if tail:
        parts.append(tail)
    return "\n".join(p for p in parts if p), R.get(rules, "caption.max_len")


def cmd_package(args):
    """The caption, assembled from the campaign's own requirements.

    Not a creative act and not meant to be one: the hook is the owner's, and
    everything after it is what the brief demands, in the brief's own spelling.

    Sem campanha não há brief, e portanto não há hashtag nem menção a exigir:
    sai o gancho do dono e mais nada. Inventar uma hashtag aqui seria o defeito
    que o `warden_rules` inteiro existe para não cometer -- um campo adivinhado
    lê como regra -- só que do lado da legenda, onde ele custa a submissão de
    quem obedeceu.
    """
    rules = regras_de(args.campaign)
    caption, max_len = _monta_legenda(rules, args.hook)
    print(caption)
    if not args.campaign:
        print("\n[no campaign was given, so nothing was required of this "
              "caption: no hashtag, no mention, no exact wording, and no "
              "length limit. This is your hook and nothing else. If there is a "
              "campaign, pass --campaign and run it again -- a missing tag is "
              "the cheapest rejection there is.]", file=sys.stderr)
    if max_len and len(caption) > max_len:
        print(f"\n[{len(caption)} characters, over the {max_len} this campaign allows]",
              file=sys.stderr)
        return 1
    policy = R.get(rules, "posting.sound_policy")
    if policy:
        print(f"\n[sound: {policy}]", file=sys.stderr)
    days = R.get(rules, "posting.min_days_live")
    if days:
        print(f"[leave it up for at least {days} days]", file=sys.stderr)
    return 0


def cmd_tiktok(args):
    """Sobe o clipe para a CAIXA DE ENTRADA do TikTok, e entrega a legenda.

    O que este comando faz e o que ele NÃO faz, porque a diferença decide o que
    o agente pode prometer:

    Ele sobe o arquivo. O rascunho aparece na **Caixa de entrada** do app --
    a notificação --, NÃO na aba Rascunhos do perfil. Isso foi medido em
    14/09/2026 e custou tempo ao dono, que procurou em Rascunhos e concluiu que
    não tinha chegado.

    Ele NÃO põe legenda nem hashtag. O endpoint de inbox não tem campo para
    isso: os únicos campos do corpo são `source_info.source`, `video_size`,
    `chunk_size` e `total_chunk_count`. Pré-preencher legenda existe só no
    Direct Post, que exige o escopo `video.publish` e auditoria do app. Então a
    legenda é impressa aqui para a pessoa colar, e dizer o contrário seria
    prometer o que a API não faz.
    """
    T = _tiktok()
    caminho = args.file
    if not os.path.isfile(caminho):
        die(f"{caminho} is not a file", code=2)
    legenda = None
    if args.campaign:
        rules = load_campaign(args.campaign)
        legenda, limite = _monta_legenda(rules, args.hook or "")
        if limite and len(legenda) > limite:
            die(f"the caption this campaign requires is {len(legenda)} "
                f"characters and it allows {limite}. Shorten the hook before "
                f"posting, not after.", code=2)
    try:
        saida = T.envia(caminho, progresso=lambda m: print(f"  {m}", file=sys.stderr),
                        espera=args.wait)
    except T.TikTokIndisponivel as exc:
        die(str(exc), code=2)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    estado = saida.get("status")
    if estado == "SEND_TO_USER_INBOX":
        print("the clip is in the TikTok INBOX -- the notification, NOT the "
              "Drafts tab of the profile. Open it there to publish.")
    else:
        # NÃO se promete quanto tempo falta, e a primeira versão desta linha
        # prometia: dizia que "usually reaches SEND_TO_USER_INBOX within a
        # minute". A medição de 14/09 é UMA amostra (~21s), e
        # CRITERIO-TESTE-TIKTOK-INBOX.md registra que quanto tempo o rascunho
        # leva para aparecer continua não provado. Uma amostra não é "usually".
        print(f"the upload finished and TikTok last reported {estado}, which is "
              f"not the inbox yet. How long it takes from here has not been "
              f"measured -- ask again with the publish_id rather than guessing:")
        print(f"  publish_id: {saida.get('publish_id')}")
    # Os avisos existem para serem lidos. Deixá-los no dicionário é o mesmo que
    # não os ter: é ali que mora "parei de perguntar ao bater o teto".
    for aviso in saida.get("avisos") or []:
        print(f"  note: {aviso}", file=sys.stderr)
    if legenda:
        print("\nPaste this as the caption (the API cannot set it):")
        print("---")
        print(legenda)
        print("---")
    return 0


def cmd_youtube(args):
    """Conecta o canal da pessoa, e publica nele. Sem senha nenhuma no caminho.

    O `connect` usa o Device Flow do OAuth 2.0 -- o fluxo que uma TV usa --
    porque a pessoa está no celular, não num teclado: o agente manda um código
    curto, ela aprova em google.com/device, e todo envio depois disso é calado.

    Por que YouTube e não TikTok, decidido em 15/09/2026: o TikTok exige App
    Review para qualquer conta que não seja a do desenvolvedor, e antes disso a
    única porta é cadastrar a conta à mão COM A SENHA dela
    (developers.tiktok.com/doc/add-a-sandbox). Isso não escala e não se pede a
    um estranho. O YouTube, com o app publicado em produção, deixa qualquer
    pessoa autorizar sozinha.

    O preço, e ele é maior do que este arquivo dizia até 15/09/2026: enquanto o
    projeto não passar na auditoria da YouTube API Services, todo vídeo enviado
    pela API fica TRANCADO como privado. Está na fonte oficial
    (support.google.com/youtube/answer/7300965): não aceita recurso e não pode
    ser tornado público depois -- nem no Studio, nem por apelação. Só a
    auditoria tira isso, e o Google não publica prazo.

    A frase que estava aqui dizia que esse último toque era "na prática, o
    consentimento final dela". Era falsa nas duas metades: não existe toque, e
    portanto ele não consente nada. Publicar continua sendo possível por este
    caminho; o que não se pode é vendê-lo como publicação.
    """
    Y = _youtube()
    fala = lambda m: print(f"  {m}", file=sys.stderr)
    if args.action == "status":
        ok, porque = Y.status_conta()
        print(("connected -- " if ok else "not connected -- ") + porque)
        return 0 if ok else 1
    if args.action == "connect":
        try:
            saida = Y.conecta(progresso=fala, espera=args.wait)
        except Y.YouTubeIndisponivel as exc:
            die(str(exc), code=2)
        canal = saida.get("canal")
        print(f"connected{f' to {canal}' if canal else ''}. "
              f"Nothing else to do -- uploads from here are silent.")
        for aviso in saida.get("avisos") or []:
            fala(aviso)
        return 0
    # publish
    caminho = args.file
    if not caminho or not os.path.isfile(caminho):
        die(f"{caminho} is not a file", code=2)
    titulo = args.title
    descricao = ""
    if args.campaign:
        rules = load_campaign(args.campaign)
        legenda, limite = _monta_legenda(rules, args.hook or "")
        if limite and len(legenda) > limite:
            die(f"the caption this campaign requires is {len(legenda)} "
                f"characters and it allows {limite}. Shorten the hook.", code=2)
        descricao = legenda
        # O título do YouTube não é a legenda: ele tem 100 caracteres e aparece
        # sozinho na busca. A primeira linha da legenda é o gancho que a pessoa
        # escreveu, e é o melhor título que existe sem inventar um.
        if not titulo:
            titulo = (legenda.splitlines() or [""])[0][:100].strip()
    if not titulo:
        die("a title is required: pass --title, or --campaign with --hook so "
            "the campaign's own first line becomes it.", code=2)
    try:
        saida = Y.publica(caminho, titulo=titulo, descricao=descricao,
                          privacidade=args.privacy, progresso=fala)
    except Y.YouTubeIndisponivel as exc:
        die(str(exc), code=2)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    url = saida.get("url") or saida.get("video_id")
    print(f"uploaded: {url}")
    for aviso in saida.get("avisos") or []:
        fala(aviso)
    # Este comando e `warden post youtube` passaram a parecer a mesma coisa, e
    # o que mente é este, o mais antigo. O upload acima existe -- há um videoId
    # e um 200 -- e ele está TRANCADO como privado, porque o projeto Google
    # deste repositório não passou pela auditoria da YouTube API Services
    # (support.google.com/youtube/answer/7300965): não aceita recurso e não se
    # desfaz no Studio. Um 200 aqui não é publicação, e quem lê esta saída tem
    # de sair dela sabendo qual é o comando que publica de verdade.
    fala("this upload is locked as PRIVATE by YouTube and cannot be made "
         "public later -- not in Studio, not by appeal. The command that "
         "actually publishes is `warden post youtube`, which goes through an "
         "already-audited app; run `warden post status` to see whether it is "
         "on.")
    return 0


def cmd_post(args):
    """Publica pelo INTERMEDIÁRIO auditado, que é o caminho que não trava.

    Por que este comando existe ao lado de `warden youtube publish`: aquele
    sobe pela YouTube Data API com o projeto Google DESTE repositório, que não
    passou pela auditoria, e o YouTube tranca o vídeo como privado sem recurso
    (support.google.com/youtube/answer/7300965). Ele devolve 200 e um videoId,
    e nada disso é publicação. Este aqui manda por um app JÁ auditado, e em
    15/09/2026 foi medido saindo PÚBLICO no canal do dono.

    O que este comando NUNCA faz: dizer que o vídeo está público porque a API
    respondeu 200. Ela responde 200 do mesmo jeito para um vídeo que ficou
    privado. Quem confirma é a janela anônima, e é isso que a última linha da
    saída manda fazer.
    """
    P = _post()

    # ── warden post setkey
    #
    # ANTES do portão de "desligado", porque este é o comando que LIGA. Ele lê a
    # chave da entrada padrão e nunca de um argumento: um argumento fica no
    # `ps`, no histórico do shell e no log de comandos do runtime, e os três
    # são lidos por quem não deveria ler a chave do canal de alguém.
    #
    # Por que ele existe, já que a variável de ambiente sempre funcionou: na
    # nuvem da Plow cada pessoa tem a própria máquina e não há `.env` nem
    # `compose.yml` -- o ambiente traz o que a Plow põe nele. Sem este comando,
    # a chave da PRÓPRIA PESSOA não tem por onde entrar, e ela fica dependendo
    # da chave do dono do agente ou de nenhuma.
    if args.action == "setkey":
        bruto = sys.stdin.read()
        try:
            caminho = P.guardar_chave(bruto)
        except P.PostIndisponivel as exc:
            die(str(exc), code=2)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        # O caminho e a permissão. NUNCA a chave, nem um prefixo dela, nem o
        # tamanho: um prefixo é o que se usa para procurar a chave inteira num
        # log, e o tamanho diz qual provedor é.
        print(f"key stored at {caminho} (0600). It is never printed, never "
              f"logged and never repeated back.")
        print("  next:  warden post connect  -- ONE address for them to open",
              file=sys.stderr)
        return 0

    # O portão de "desligado". Sem chave não há requisição, não há traceback e
    # não há `invalid choice`: há uma frase que diz o que falta e o que o agente
    # faz em vez disso. Código 1 e não 2 de propósito -- 2 é erro de uso do
    # comando, e não usar errado quem não configurou o ambiente.
    if not P.esta_configurado():
        die("the publishing intermediary is OFF: there is no key on this "
            "install -- not in WARDEN_POST_API_KEY and not in this machine's "
            "own file. Nothing was sent and nothing was tried. Do NOT claim "
            "anything was published. Two roads: hand the finished file over in "
            "your final message, with the title and the description written "
            # "em menos de um minuto" saiu daqui: ninguém cronometrou um upload
            # manual do dono, e esta frase existe justamente para ser a única
            # da saída que não promete nada.
            "out, so the owner can upload it by hand; and offer them the setup "
            "-- a free account at https://app.upload-post.com, the key at "
            "https://app.upload-post.com/api-keys, and they paste it here so "
            "you can run `warden post setkey`. The persona's \"Publishing\" "
            "section has the exact three lines to send.", code=1)

    # ── warden post connect [nome do perfil]
    #
    # UM link, e é o único passo que não cabe no chat -- a senha do Google é
    # digitada na tela do Google e em lugar nenhum mais. Antes disto, ligar um
    # canal eram cinco passos num painel web, e o dono mediu o custo em
    # 16/09/2026 com o auditor do hackathon a caminho: "precisa ser no chat
    # apenas que ele conecta ou no máximo um link de autenticação onde ele
    # aprova ou não e posta".
    if args.action == "connect":
        try:
            saida = P.conectar(args.file or None)
        except P.PostIndisponivel as exc:
            die(str(exc), code=2)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        for aviso in saida.get("avisos") or []:
            print(f"  note: {aviso}", file=sys.stderr)
        if saida["ja_conectado"]:
            print(f"already connected: {saida['canal']} {saida['handle']} on "
                  f"profile {saida['perfil']}. Nothing to do -- "
                  f"`warden post youtube <clip> --title \"...\"` publishes.")
            print(f"  to swap to another channel, open: {saida['url']}",
                  file=sys.stderr)
            return 0
        if saida["perfil_criado"]:
            # O NOME, sempre. Ele é o que distingue esta máquina de outra que
            # use a mesma chave, e é o que a pessoa procura no painel do
            # provedor quando quer apagar um perfil que não usa mais.
            print(f"  created this machine's own profile: {saida['perfil']}",
                  file=sys.stderr)
        if saida["pede_reautenticacao"]:
            print(f"  {saida['canal']} is connected but needs REAUTH, which is "
                  f"why it would not publish. The same link fixes it.",
                  file=sys.stderr)
        print(saida["url"])
        print("  GIVE THAT ADDRESS TO THE PERSON, on its own, and say in one "
              "line what it does: it opens Google's own screen, they pick the "
              "channel and press Allow. Their password is typed on Google's "
              "page and nowhere else. Then run `warden post connect` again -- "
              "it says `already connected` when it worked.", file=sys.stderr)
        return 0

    # `and not args.file`, e a ausência dessa condição era um defeito.
    #
    # Este ramo estava escrito só como `args.action == "status"` e vinha ANTES
    # do ramo que lê um request_id -- então ele retornava SEMPRE, e o bloco de
    # baixo, o que consulta um envio, era código inalcançável. `warden post
    # status <id>` lia a conta e ignorava o id em silêncio.
    #
    # O que isso custava: a própria persona manda o agente rodar `warden post
    # status <id>` quando o envio cai na fila do intermediário. Ele rodava,
    # recebia a listagem da conta, não encontrava o envio ali, e ficava sem o
    # endereço do vídeo -- que era justamente a coisa que ele tinha prometido
    # mandar de volta.
    if args.action == "status" and not args.file:
        try:
            saida = P.status()
        except P.PostIndisponivel as exc:
            die(str(exc), code=2)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        print(f"provider: {saida['provedor']} ({saida['base']})")
        print(f"account:  {saida['email']} -- plan {saida['plano']}")
        # DE ONDE veio a chave e DE ONDE veio o nome do perfil. Nunca o valor
        # da chave. A procedência é o que a pessoa precisa ler quando o canal
        # não é o que ela esperava: uma chave do ambiente é a do dono do
        # agente, e aí o perfil desta máquina tem de ser só dela.
        print(f"key:      configured (from the {saida.get('chave_origem')})")
        print(f"profile:  {saida['perfil'] or 'NOT CHOSEN'}"
              + (f" ({saida['perfil_origem']})" if saida.get("perfil_origem")
                 else ""))
        publicam = []
        for perfil in saida["perfis"]:
            for rede, conta in sorted(perfil["contas"].items()):
                if not conta["conectada"]:
                    estado = "not connected"
                elif conta["reauth_required"]:
                    estado = "CONNECTED BUT NEEDS REAUTH -- it will not publish"
                else:
                    estado = f"{conta['display_name']} {conta['handle']}"
                    publicam.append(f"{perfil['nome']}/{rede}")
                print(f"  {perfil['nome']}/{rede}: {estado}")
        for aviso in saida["avisos"]:
            print(f"  note: {aviso}", file=sys.stderr)
        # O CÓDIGO DE SAÍDA distingue TRÊS estados, e não dois. Este é o
        # conserto: a persona lê este comando de forma binária -- saiu != 0, o
        # caminho está desligado; saiu 0, é "a estrada que publica" -- e a
        # chave válida com a conta pedindo reautenticação saía 0. O agente
        # prometia publicação, o envio falhava depois de subir o arquivo
        # inteiro, e a frase "CONNECTED BUT NEEDS REAUTH" ficava no meio da
        # listagem que ninguém precisava ler para decidir.
        #
        #   0  há pelo menos uma conta que publica AGORA
        #   1  não há chave (o `die` lá em cima)
        #   2  a chave existe e o provedor não respondeu (o `die` acima)
        #   3  a chave vale e NENHUMA conta vai publicar
        #
        # 3 e não 1 de propósito: 1 é "ninguém configurou nada", e aqui está
        # configurado -- o que falta é uma reconexão no painel, que é outra
        # coisa a fazer e outra frase a dizer.
        if publicam:
            return 0
        pedem_reauth = saida.get("reauth") or []
        if pedem_reauth:
            print(f"NO account here will publish: {', '.join(pedem_reauth)} "
                  f"{'is' if len(pedem_reauth) == 1 else 'are'} connected but "
                  f"need(s) REAUTH, and nothing else is connected. Do NOT "
                  f"promise a publication: `warden post connect` prints the "
                  f"address that reconnects it, or hand the file over in your "
                  f"final message with the title and description written out.",
                  file=sys.stderr)
        else:
            print("NO account here will publish: the key is valid and not one "
                  "network is connected on any profile. Do NOT promise a "
                  "publication. Run `warden post connect`: it prints ONE "
                  "address for the person to open, where they pick the channel "
                  "on Google's own screen and press Allow. Until they do, hand "
                  "the file over in your final message with the title and "
                  "description written out.", file=sys.stderr)
        return 3

    # ── warden post status <request_id>
    #
    # Existia como `post status` sem argumento, que lê a CONTA. Quando o envio
    # devolvia um request_id, não havia comando nenhum para perguntar por ele.
    # Agora o mesmo verbo responde as duas perguntas: sem id, a conta; com id,
    # aquele envio.
    if args.action == "status" and args.file:
        try:
            saida = P.wait_for(args.file, timeout_s=args.wait)
        except P.PostIndisponivel as exc:
            die(str(exc), code=2)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        for aviso in saida["avisos"]:
            print(f"  note: {aviso}", file=sys.stderr)
        if not saida["concluido"]:
            print(f"still in the intermediary's queue after "
                  f"{saida['esperou_s']}s ({saida['status']}).")
            print(f"  ask again with:  warden post status {saida['request_id']}")
            return 0
        if saida["sucesso"] is False:
            die("the intermediary reported the upload FAILED, so nothing was "
                "published. Its own words are in the notes above.", code=2)
        if saida["post_url"]:
            print(saida["post_url"])
        print(f"  video id: {saida['platform_post_id']}", file=sys.stderr)
        print(f"\n{saida['prova']}", file=sys.stderr)
        return 0

    # ── warden post youtube|tiktok|instagram <clip> --title "..."
    #
    # Uma rede por invocação, e `--also` acrescenta outras ao MESMO envio: a
    # API recebe `platform[]` repetido e o arquivo sobe uma vez só para todas.
    redes = [args.action] + [r.strip().lower()
                             for r in (args.also or "").split(",") if r.strip()]
    if not args.file:
        die(f"post {args.action} needs the clip to publish: "
            f"`warden post {args.action} CLIP --title \"...\"`", code=2)
    if not args.title:
        die(f"post {args.action} needs --title. YouTube requires a title and "
            f"the other networks use it as the caption; this command will not "
            f"invent one for the owner's channel.", code=2)

    try:
        envio = P.publicar(args.file, redes, args.title,
                           descricao=args.description or "",
                           privacidade=args.privacy,
                           shorts=args.shorts,
                           rascunho=args.draft,
                           stories=args.stories)
    except P.PostIndisponivel as exc:
        die(str(exc), code=2)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)

    print(f"accepted for processing: request_id={envio['request_id']} "
          f"({', '.join(envio['plataformas'])}, profile {envio['perfil']})",
          file=sys.stderr)
    for aviso in envio["avisos"]:
        print(f"  note: {aviso}", file=sys.stderr)

    try:
        saida = P.wait_for(envio["request_id"], timeout_s=args.wait)
    except P.PostIndisponivel as exc:
        die(str(exc), code=2)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)

    # Os avisos primeiro, e em stderr: é ali que mora "a API não confirmou a
    # privacidade" e "pedi público e voltou privado". Enterrá-los depois do
    # endereço é o mesmo que não os ter.
    for aviso in saida["avisos"]:
        print(f"  note: {aviso}", file=sys.stderr)

    if not saida["concluido"]:
        # O COMANDO, não o nome de uma função interna.
        #
        # Medido em 16/09/2026: esta saída dizia `consulte de novo com
        # wait_for('<id>')`. `wait_for` é uma função do módulo e não existe
        # como comando, então o agente tentou `warden post status <id>` (que
        # ignorava o id), não entendeu, e passou seis minutos LENDO O PRÓPRIO
        # CÓDIGO-FONTE atrás dela -- seis minutos de silêncio para quem estava
        # esperando. Uma mensagem que manda chamar o que não se pode chamar é
        # pior que uma mensagem sem saída nenhuma.
        print(f"still processing after {saida['esperou_s']}s -- this is neither "
              f"a failure nor a success. It is the intermediary's queue, not "
              f"this machine: the file left here already.")
        print(f"  ask again with:  warden post status {saida['request_id']}")
        print(f"  TELL THE PERSON, in one line, that it is uploading and you "
              f"will come back with the address. Do not go looking for another "
              f"way to ask: this is the way.", file=sys.stderr)
        return 0
    if saida["sucesso"] is False:
        die(f"the intermediary reported the upload FAILED, so nothing was "
            f"published. Its own words are in the notes above.", code=2)

    # O ENDEREÇO QUE A API DEVOLVEU, por rede, e nenhum endereço composto aqui.
    # Montar `youtube.com/watch?v=<id>` a partir de um id seria inventar um
    # link que ninguém devolveu -- e num envio para três redes seriam três
    # invenções.
    por_rede = saida.get("por_plataforma") or {}
    if len(por_rede) > 1:
        for rede, res in por_rede.items():
            if res["url"]:
                print(f"{rede}: {res['url']}")
            elif res["sucesso"] is False:
                print(f"{rede}: FAILED -- see the notes above", file=sys.stderr)
            else:
                print(f"{rede}: the API returned no address", file=sys.stderr)
    elif saida["post_url"]:
        print(saida["post_url"])
    print(f"  video id: {saida['platform_post_id']}", file=sys.stderr)
    if saida["prevalidacao"]:
        meta = saida["prevalidacao"]
        print(f"  the intermediary read the file as {meta.get('width')}x"
              f"{meta.get('height')}, {meta.get('duration')}s, "
              f"{meta.get('video_codec')}", file=sys.stderr)
    # A última linha, e a única que prova alguma coisa.
    print(f"\n{saida['prova']}", file=sys.stderr)
    return 0


def cmd_log(args):
    load_campaign(args.campaign)
    ledger_add({"campaign": args.campaign, "clip": args.clip,
                "platform": args.platform, "url": args.url,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    rows = ledger_for(args.campaign)
    print(f"logged. {len(rows)} post(s) recorded for {args.campaign}")
    return 0


# ------------------------------------------------------------------ a voz

# O caminho do banco de conversas do runtime. Só é lido, nunca escrito.
CONVERSAS_DB = os.environ.get("WARDEN_CONVERSAS_DB",
                              "/var/lib/hermes/state.db")

# Quantas mensagens uma tarefa de dois cortes pode custar. A persona fixa três:
# recebi, primeiro corte, segundo corte. Quatro ainda é aceitável quando a
# pessoa teve de decidir alguma coisa. Cinco não é.
VOZ_ALVO = 3
VOZ_TETO = 4


def _conversas(desde=None):
    """(linhas, escopo) do banco de conversas. Só DESTA sessão, quando dá.

    Leitura pura, em modo somente-leitura, e num banco que pertence ao runtime
    e não a este comando. Um banco ausente não é erro: é "não dá para medir
    daqui", e dizer isso é melhor que devolver zero como se fosse uma medida.

    O FILTRO POR SESSÃO é o conserto de um vazamento, e ele não é teórico: o
    `state.db` do Hermes guarda TODAS as conversas daquela instalação, uma
    sessão por `session_id`, e o volume sobrevive a `up`/`down`. Sem filtro,
    `warden voz --since 0` imprimia trechos de mensagens de quem tivesse usado
    a mesma instalação antes -- conversa de outra pessoa, na tela de quem
    rodou o comando. Contar não exige ler o texto dos outros.

    `escopo` é {"sessao", "porque", "isolada"}. `isolada` é o que autoriza
    imprimir texto: verdadeiro quando as linhas são de UMA conversa só.
    """
    import sqlite3
    if not os.path.isfile(CONVERSAS_DB):
        raise RuntimeError(
            f"{CONVERSAS_DB} is not here, so the messages cannot be counted "
            f"from this machine. Point WARDEN_CONVERSAS_DB at the runtime's "
            f"state.db if it lives somewhere else.")
    con = sqlite3.connect(f"file:{CONVERSAS_DB}?mode=ro", uri=True)
    try:
        corte = float(desde or 0)
        colunas = {linha[1] for linha in
                   con.execute("PRAGMA table_info(messages)").fetchall()}
        sessao, porque, isolada = _sessao_corrente(con, colunas)
        campos = ("select timestamp, role, coalesce(content, '') from messages "
                  "where role in ('user', 'assistant') and timestamp >= ?")
        if sessao is not None:
            linhas = con.execute(campos + " and session_id = ? order by "
                                 "timestamp", (corte, sessao)).fetchall()
        else:
            linhas = con.execute(campos + " order by timestamp",
                                 (corte,)).fetchall()
    finally:
        con.close()
    return linhas, {"sessao": sessao, "porque": porque, "isolada": isolada}


def _sessao_corrente(con, colunas):
    """(session_id, por que esse, dá para isolar?) da conversa que roda AGORA.

    Não existe variável do runtime dizendo em que sessão este processo está --
    procurada em 15/09/2026 e não achada -- então sobram duas respostas, nesta
    ordem:

      1. `WARDEN_SESSION_ID`, se alguém exportar. É a única forma EXATA, e
         existe para o dia em que o runtime passe a exportá-la;
      2. a sessão da mensagem mais NOVA do banco. Este comando roda dentro de
         um turno, e o turno começou com a mensagem que acabou de entrar: a
         linha mais recente é desta conversa. É uma inferência, e é por isso
         que a saída diz em voz alta qual sessão está contando.

    Quando a tabela TEM `session_id` e nenhuma das duas responde, `isolada` é
    falso: as linhas misturam conversas, e aí o comando conta e não mostra
    texto nenhum.

    Sem a coluna `session_id` não há o que separar -- uma tabela que não
    guarda sessão guarda uma conversa só -- e aí `isolada` é verdadeiro.
    """
    explicita = (os.environ.get("WARDEN_SESSION_ID") or "").strip()
    if "session_id" not in colunas:
        return None, "this messages table has no session_id column, so there "\
                     "is only one conversation in it to count", True
    if explicita:
        return explicita, "WARDEN_SESSION_ID in the environment", True
    try:
        linha = con.execute(
            "select session_id from messages where session_id is not null "
            "order by timestamp desc, rowid desc limit 1").fetchone()
    except Exception:
        linha = None
    if linha and linha[0]:
        return linha[0], ("the newest message in the database is in it, and "
                          "this command runs inside the turn that message "
                          "started"), True
    return None, ("no session could be told apart in this database, so the "
                  "rows below are every conversation it holds"), False


_ENTROU = "inbound message:"
_URL = re.compile(r"https?://[^\s'\"<>]+")


def _chegadas(depois):
    """[(quando, texto)] das mensagens que ENTRARAM depois de `depois`.

    Lê o log do gateway, que registra cada mensagem recebida no instante em que
    ela chega -- antes e independentemente de o agente ser acordado por ela.
    É a única fonte que existe para a pergunta "chegou mais alguma coisa
    enquanto eu pensava?".
    """
    if not os.path.isfile(GATEWAY_LOG):
        return None
    saiu = []
    try:
        with open(GATEWAY_LOG, encoding="utf-8", errors="replace") as fh:
            for linha in fh:
                if _ENTROU not in linha:
                    continue
                t = _carimbo_do_log(linha)
                if t is None or t <= float(depois):
                    continue
                corpo = linha.split("msg='", 1)
                texto = corpo[1].rsplit("' reply_to_id=", 1)[0] if len(corpo) > 1 else ""
                saiu.append((t, texto))
    except OSError:
        return None
    return saiu


_ID_YT = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)"
                    r"([A-Za-z0-9_-]+)")


def _link_incompleto(texto, link):
    """O que PROVA que este endereço foi cortado, ou None. Nunca um palpite.

    O critério anterior era `texto.endswith(link) and len(texto) >= 79`, e ele
    é a forma de uma mensagem ÍNTEIRA: quem manda "pega esse podcast e me faz
    os cortes https://..." escreve exatamente isso -- mais de 79 caracteres,
    terminando no link. O comando recusava um link perfeito e mandava perguntar
    de novo, o que é a SEGUNDA pergunta num turno que só pode gastar uma.

    Então nada de comprimento. O corte só é afirmado quando o endereço em si
    está comprovadamente pela metade:

      * o id de um vídeo do YouTube tem ONZE caracteres, sempre (é o formato do
        `v=`, do `youtu.be/`, do `/shorts/`). Um id no fim da URL com menos que
        isso não é um vídeo, é um id cortado;
      * uma URL que acaba dentro de um escape de porcentagem (`%2`, `%`) acaba
        no meio de um caractere.

    Fora desses dois, não dá para distinguir uma URL cortada de uma URL curta
    -- e aí o honesto é NÃO afirmar truncamento e devolver o link. Se ele
    estiver mesmo quebrado, quem diz isso é o download falhando, com o endereço
    na mão; um palpite aqui custa uma ida e volta em toda mensagem comprida.
    """
    # Um corte acontece no FIM da linha: se ainda há texto depois do link,
    # o que quer que tenha sido cortado não foi o link.
    if not texto.rstrip().endswith(link):
        return None
    if re.search(r"%[0-9A-Fa-f]?$", link):
        return "it ends inside a percent-escape"
    achado = _ID_YT.search(link)
    if achado and link.endswith(achado.group(1)) and len(achado.group(1)) < 11:
        return (f"a YouTube id is 11 characters and {achado.group(1)!r} has "
                f"{len(achado.group(1))}")
    return None


# Quantos segundos o `inbox` espera pelo link que a mensagem prometeu.
#
# Já foi 60 e já foi 12. Sessenta era silêncio demais na frente de um avaliador
# -- "não posso esperar tanto tempo assim" -- e doze errava por um segundo.
#
# 14s desde 16/09/2026, e o motivo é uma medição de duas vezes.
#
# Em DOIS pedidos reais no mesmo dia o dono mandou o link UM SEGUNDO depois de a
# espera acabar -- 11:29:35 a desistência, 11:29:36 o link; e 10:07:26 contra
# 10:07:27. As duas vezes custaram uma mensagem ("manda o link que eu já corto")
# que não precisava existir.
#
# Dois segundos a mais custam dois segundos nos pedidos em que o link já veio
# junto, e economizam uma volta inteira quando ele vem logo atrás. O dono
# escolheu o número: "sobe para 14 segundos".
INBOX_ESPERA_S = 14.0


def _espera_do_inbox(pedida=None):
    """Os segundos que o `inbox` vai esperar. Ver `INBOX_ESPERA_S`.

    Configurável em dois lugares porque são duas perguntas diferentes:
    `--wait` é "nesta chamada", e `WARDEN_INBOX_WAIT` é "nesta instalação" --
    quem roda a suíte ou um teste de fumaça não quer 45s de relógio por
    chamada, e não deveria ter de editar o código para não tê-los.
    """
    for valor in (pedida, os.environ.get("WARDEN_INBOX_WAIT")):
        if valor in (None, ""):
            continue
        try:
            return max(1.0, float(valor))
        except (TypeError, ValueError):
            continue
    return INBOX_ESPERA_S


def cmd_inbox(args):
    """Espera o link que a mensagem prometeu, em vez de responder "faltou".

    Medido em 15/09, três vezes em três conversas, com o mesmo placar:

        00:42:10  a pessoa pede os cortes "desse video do YouTube abaixo"
        00:42:15  o agente responde "faltou o link"       (+5s)
        00:42:17  o link chega, em mensagem separada       (+7s)

    O agente não é cego ao link e o cartão de pré-visualização não tem nada a
    ver com isso: o link simplesmente ainda não chegou, e a resposta saiu antes.
    Uma ida e volta queimada, sempre, porque responder rápido demais parecia
    grátis.

    Então, quando o texto aponta para um link que não está nele -- "esse vídeo",
    "esse link", "abaixo", "essa música" -- isto é o que se roda em vez de
    perguntar. Sai 0 com o link na primeira linha, ou sai 1 depois do prazo,
    e aí sim a pergunta é legítima.

    OS CÓDIGOS DE SAÍDA, e por que são três e não dois:

      0  chegou um link, e ele está na primeira linha do stdout
      1  o prazo passou e nada com link entrou. A pergunta é legítima
      2  não deu para OLHAR: o log não é legível daqui, e nada foi esperado

    2 é diferente de 1 e a diferença é o projeto inteiro: 1 é uma medição
    ("esperei e não veio"), 2 não é medição nenhuma. Tratar os dois como a
    mesma coisa era este comando dizendo "ninguém mandou link" sobre uma
    espera que nunca aconteceu.
    """
    espera = _espera_do_inbox(getattr(args, "wait", None))
    inicio = time.time()
    # A régua é o ÚLTIMO carimbo que já estava no log, não a hora atual: a
    # mensagem que disparou este turno já está lá, e ela não conta como nova.
    ja = _chegadas(0)
    if ja is None:
        # NÃO é "não chegou link": é "não olhei". Nenhum segundo foi esperado
        # e nenhuma mensagem foi lida, então esta saída não afirma nada sobre
        # o que a pessoa mandou ou deixou de mandar. Fora do runtime -- na
        # máquina de quem desenvolve -- este é o caso NORMAL, e a frase antiga
        # ("Ask for the link") fazia dele um veredito.
        die(f"{GATEWAY_LOG} is not readable from here, so NOTHING was waited "
            f"for and nothing was measured. This is not 'no link arrived' -- "
            f"it is 'this machine cannot see messages arriving at all'. If the "
            f"message points at a link you cannot see, asking once is the only "
            f"move left, and that one question is the whole budget for this "
            f"turn.", code=2)
    marca = max([t for t, _ in ja], default=0.0)
    while True:
        for quando, texto in (_chegadas(marca) or []):
            achados = _URL.findall(texto)
            if not achados:
                continue
            link = achados[-1]
            # O log corta a mensagem por volta de 80 caracteres, e um link
            # cortado é pior que link nenhum: ele baixa outra coisa, ou nada, e
            # o erro aparece três passos adiante. Mas o corte só é AFIRMADO
            # quando o próprio endereço prova que está incompleto -- ver
            # `_link_incompleto`.
            cortado = _link_incompleto(texto, link)
            if cortado:
                print(f"a link arrived but the log truncated it ({link!r}): "
                      f"{cortado}. Ask the person to send just the URL.",
                      file=sys.stderr)
                return 1
            print(link)
            print(f"arrived {quando - inicio:.0f}s into the wait", file=sys.stderr)
            return 0
        if time.time() - inicio >= espera:
            break
        time.sleep(0.15)
    print(f"nothing with a link arrived in {espera:.0f}s. Now the question is "
          f"fair: ask for the URL.", file=sys.stderr)
    return 1


def cmd_voz(args):
    """Conta o que a pessoa REALMENTE recebeu, por pedido dela.

    Existe porque "o agente fala demais" era uma impressão e virou um número:
    nas três conversas de 15/09 foram 57 mensagens do agente para três pedidos,
    16 delas entre um link e um clipe.

    E porque a contagem óbvia está errada. O log do gateway registra UMA linha
    de envio por turno, o que faz parecer que só a última mensagem do turno
    sai. Não é o caso: `interim_assistant_messages` vem ligada, então cada
    texto escrito entre duas chamadas de ferramenta é entregue como mensagem.
    O que NÃO viaja do meio do turno é o anexo. Então aqui se conta prosa, toda
    ela, que é o que aparece no celular.

    E conta a DESTA conversa. O banco tem todas as que já passaram por esta
    instalação; ver `_conversas`. Quando não dá para isolar uma sessão, o
    comando continua contando e para de imprimir texto: para contar não é
    preciso mostrar a mensagem de ninguém.
    """
    try:
        linhas, escopo = _conversas(getattr(args, "since", None))
    except RuntimeError as exc:
        die(str(exc), code=2)
    if escopo["sessao"]:
        print(f"# counting session {escopo['sessao']} only: "
              f"{escopo['porque']}.", file=sys.stderr)
    elif not escopo["isolada"]:
        print(f"# {escopo['porque']}. Counting them, and printing NO message "
              f"text: a message from another conversation is not this "
              f"command's to show. Export WARDEN_SESSION_ID to get the text "
              f"back.", file=sys.stderr)
    if not linhas:
        print("no conversation in that window")
        return 0

    # Um PEDIDO é uma mensagem da pessoa que não é resposta a um processo de
    # fundo: o despertar de um render terminado não é ela falando.
    tarefas = []
    for ts, papel, texto in linhas:
        if papel == "user":
            if texto.startswith("[IMPORTANT:"):
                continue
            tarefas.append({"em": ts, "pediu": texto.strip(), "msgs": []})
        elif tarefas and texto.strip():
            tarefas[-1]["msgs"].append((ts, texto))

    pior = 0
    for t in tarefas:
        n = len(t["msgs"])
        pior = max(pior, n)
        com_arquivo = sum(1 for _, m in t["msgs"] if "MEDIA:" in m)
        quando = datetime.fromtimestamp(
            t["em"], timezone.utc).strftime("%d/%m %H:%M")
        veredito = "ok" if n <= VOZ_TETO else "DEMAIS"
        # O texto do pedido e o das mensagens só saem quando as linhas são de
        # UMA conversa -- a de quem está lendo. Ver `_conversas`: sem isso,
        # estes 56 e estes 88 caracteres eram conversa de outra pessoa impressa
        # na tela de quem rodou o comando.
        rotulo = t["pediu"][:56] if escopo["isolada"] else "(text not shown)"
        print(f"{quando}  {n:2d} message(s), {com_arquivo} with a file  "
              f"[{veredito}]  {rotulo}")
        if n > VOZ_TETO and escopo["isolada"]:
            for _, m in t["msgs"]:
                if "MEDIA:" not in m:
                    print(f"       - {m.splitlines()[0][:88]}")

    print(f"\n{len(tarefas)} request(s); the worst one cost {pior} message(s). "
          f"Target {VOZ_ALVO}, ceiling {VOZ_TETO}.")
    # Sai 1 quando alguma tarefa passou do teto, para um teste poder travar
    # nisso em vez de alguém ler a tabela e achar que está bom.
    return 1 if pior > VOZ_TETO else 0


def cmd_status(args):
    root = state_dir()
    names = list_campaigns()
    # Where the state lives, and why it lives there. An override is legitimate,
    # but a silent one turns "campaigns: none" into a mystery: two people look at
    # two directories and reach opposite conclusions about the same agent.
    source = ("WARDEN_DIR in the environment" if os.environ.get("WARDEN_DIR")
              else "the default for this image")
    print(f"state: {root}  ({source})")
    print(f"writable: {'yes' if os.access(root, os.W_OK) else 'NO, and that is why nothing saves'}")
    print(f"campaigns: {', '.join(names) if names else 'none'}")
    for cid in names:
        rules = load_campaign(cid)
        cap = R.get(rules, "posting.per_clipper_cap")
        used = len(ledger_for(cid))
        print(f"  {cid}: {used} post(s)" + (f" of {cap}" if cap else "")
              + (f", closes {R.get(rules, 'posting.deadline')}"
                 if R.get(rules, "posting.deadline") else ""))
    devendo = entregas_pendentes()
    if devendo:
        print(f"deliveries owed: {len(devendo)} clip(s) cleared and never "
              f"confirmed as sent")
        for row in devendo:
            print(f"  {row['clip']}")
    else:
        print("deliveries owed: none")
    print("ffprobe: " + (shutil.which("ffprobe") or "MISSING"))
    print("ffmpeg:  " + (shutil.which("ffmpeg") or "MISSING"))
    # Tudo que um corte precisa e que pode não estar aqui. Cada linha existe
    # porque a ausência dela já degradou um clipe em silêncio: sem o detector o
    # enquadramento foi para o centro e entregou o rosto na borda; sem Pillow não
    # há hook, que é PNG do PIL; e sem libass não há legenda, que desde o Bloco B
    # é ASS. Dizer "todo texto é PNG do PIL" deixou de ser verdade nesse dia e a
    # linha continuou aqui -- relatório que envelhece é relatório falso.
    try:
        ok, why = _media().face_detection_status()
        print("face detection: " + (why if ok else f"MISSING -- {why}"))
    except Exception as exc:
        print(f"face detection: MISSING -- {type(exc).__name__}: {exc}")
    try:
        import PIL
        print(f"pillow (the hook, drawn as a PNG): {PIL.__version__}")
    except ImportError:
        print("pillow (the hook, drawn as a PNG): MISSING -- the hook cannot "
              "be drawn without it")
    import warden_style as S
    print("style font: " + (S.FONT_PATH if S.font_available()
                            else f"MISSING at {S.FONT_PATH} -- text would fall "
                                 "back to a system face"))
    # A legenda é ASS queimado pelo libass, e libass é opção de compilação do
    # ffmpeg. Sem ele o `cut` recusa queimar legenda -- em voz alta, mas recusa.
    # Esta linha é para o defeito aparecer no `install.sh` e não no primeiro
    # corte de alguém.
    try:
        tem = _media().ffmpeg_tem_filtro("ass")
    except Exception as exc:
        tem, _ = False, exc
    print("libass (burned captions): " + ("yes, the `ass` filter is here" if tem
                                          else "MISSING -- this ffmpeg was built "
                                               "without libass, so no clip can "
                                               "carry burned captions"))
    spec = specs_path()
    print("measured style spec: " + (spec if os.path.isfile(spec)
                                     else f"MISSING at {spec}"))
    # O estado do downloader, e ele é DUAS perguntas, não uma.
    #
    # Medido em 15/09: o agente não conseguia puxar link nenhum do YouTube e
    # inventou duas causas diferentes em dois dias. O que `yt-dlp -v` dizia era
    # `JS runtimes: none` e `PO Token Providers: none`, e nada no `status`
    # perguntava por isso -- então o defeito só aparecia como um download
    # falhando, que é onde ele lê como problema da fonte.
    #
    # Sem runtime JS o yt-dlp derruba o client `web` do conjunto padrão e não
    # decifra n/sig. Sem o provedor de PO token o endereço desta instalação vai
    # sendo marcado até o YouTube recusar tudo -- e depois de marcado nem o
    # token levanta mais, o que faz desta linha um aviso, não um relatório.
    try:
        M = _media()
        rt = M._js_runtimes()
        print("yt-dlp JS runtime: " + (rt if rt else
              "MISSING -- yt-dlp drops the `web` client and cannot decipher "
              "n/sig, so YouTube links fail for a reason that reads like the "
              "source's fault"))
        # Diz QUAL dos dois caminhos está de pé, porque "answering" sobre um
        # endereço que ninguém declarou era uma linha que não queria dizer nada.
        if os.path.isfile(os.path.join(M.POT_SCRIPT_HOME, "build",
                                       "generate_once.js")):
            onde = f"script, inside this image: {M.POT_SCRIPT_HOME}"
        elif M.POT_BASE_URL:
            onde = f"HTTP, {M.POT_BASE_URL}"
        else:
            onde = "nenhum caminho configurado"
        print(f"PO token provider ({onde}): " + (
              "ready" if M._pot_alive() else
              "NOT available -- this install's address is being spent without "
              "one, and YouTube flags addresses that ask without it"))
        print("yt-dlp cookies file (%s): " % M.COOKIES_FILE + (
              "present" if os.path.isfile(M.COOKIES_FILE) else
              "absent (only needed if this address is already refused)"))
    except Exception as exc:
        print(f"yt-dlp downloader: could not be read ({type(exc).__name__})")
    # A ponta do TikTok. Uma linha, porque a pergunta "dá para mandar pro
    # rascunho?" tem de ser respondida ANTES de alguém renderizar um clipe
    # contando com isso -- e a resposta depende de um token que não vem na
    # imagem e não pode vir: ele é de uma conta, não do agente.
    try:
        ok, porque = _tiktok().status_conta()
        print("tiktok draft upload: " + ("ready -- " if ok else "NOT set up -- ") + porque)
    except Exception as exc:
        print(f"tiktok draft upload: could not be read ({type(exc).__name__})")
    # PUBLICAR NO YOUTUBE, e o caminho que aparece primeiro é o que FUNCIONA.
    #
    # Medido em 16/09/2026, rodando este comando como quem acabou de instalar:
    # a única linha sobre YouTube dizia `youtube publishing: NOT connected` --
    # e ela é sobre o caminho DIRETO, o que o Google tranca como privado e que
    # não publica nem conectado. Quem lê isso conclui que publicar no YouTube
    # não funciona neste agente. Funciona: é o intermediário auditado, e ele
    # não tinha linha nenhuma aqui.
    try:
        P = _post()
        if not P.esta_configurado():
            # O texto antigo mandava criar um `.env` ao lado do compose.yml e
            # rodar `docker compose up -d`. Na nuvem da Plow a pessoa não tem
            # nem o arquivo nem o terminal: era uma instrução para um passo que
            # ela não pode dar. Decisão do dono, 16/09 -- ver
            # runtime/persona.md, "Publishing to YouTube": a chave se cola na
            # conversa. A semântica de WARDEN_POST_API_KEY no compose.yml não
            # muda; o que muda é o que este print manda o agente dizer.
            print("youtube publishing: NOT set up -- there is no intermediary "
                  "key. Two steps, no programming: they open an account at "
                  "upload-post.com (free, 10 posts/month, no card asked) and "
                  "generate a key, then they paste that key here, in this "
                  "conversation. After that, `warden post connect` prints ONE "
                  "address that links the channel.")
        else:
            info = P.status()
            publicam = [f"{perfil['nome']}/{rede}"
                        for perfil in info["perfis"]
                        for rede, conta in sorted(perfil["contas"].items())
                        if conta["conectada"] and not conta["reauth_required"]]
            canais = [f"{conta['display_name']} {conta['handle']}"
                      for perfil in info["perfis"]
                      for _rede, conta in sorted(perfil["contas"].items())
                      if conta["conectada"] and not conta["reauth_required"]]
            if publicam:
                print(f"youtube publishing: ready -- {', '.join(canais)} "
                      f"(via {info['provedor']}, perfil {info['perfil']}). "
                      f"`warden post youtube <clip> --title \"...\"` publica.")
            else:
                print("youtube publishing: the key works and NO channel is "
                      "linked. `warden post connect` prints ONE address: they "
                      "open it, pick the channel on Google's own screen and "
                      "approve.")
    except Exception as exc:
        print(f"youtube publishing: could not be read ({type(exc).__name__}: "
              f"{exc})")
    # E o caminho DIRETO, depois e nomeado como o que é: ele sobe, devolve 200
    # e o vídeo fica trancado como privado enquanto o app não passar pela
    # auditoria do Google. Fica na saída porque `warden youtube` existe e
    # alguém vai perguntar por ele; fica DEPOIS porque não é o caminho.
    try:
        ok, porque = _youtube().status_conta()
        print("youtube direct API (locked private until Google audits this "
              "app; not the road): " + ("connected -- " if ok else "not "
              "connected -- ") + porque)
    except Exception as exc:
        print(f"youtube direct API: could not be read ({type(exc).__name__})")
    # The models arrive after boot rather than inside the image, so whether they
    # are here yet is a real question with a real answer, not a constant.
    # "ainda baixando" e "nunca baixou" eram a mesma linha, e são coisas
    # diferentes: numa é só esperar, na outra o primeiro corte vai puxar 484 MB
    # no meio do pedido de alguém.
    try:
        estados = _media().model_status()
    except Exception as exc:
        print(f"whisper models: could not be read ({type(exc).__name__})")
        return 0
    for nome, info in sorted(estados.items()):
        if info["state"] == "ready":
            print(f"whisper model {nome}: ready ({info['mb']} MB)")
        elif info["state"] == "fetching":
            print(f"whisper model {nome}: downloading, {info['mb']} of "
                  f"{info['want_mb']} MB ({info['pct']}%)")
        elif info["state"] == "failed":
            print(f"whisper model {nome}: FAILED at boot -- the first cut would "
                  f"fetch {info['want_mb']} MB itself")
        else:
            print(f"whisper model {nome}: not here yet ({info['want_mb']} MB to "
                  f"download; it runs in the background after install)")
    return 0



# ---------------------------------------------------------------- media

def _media():
    import warden_media
    return warden_media


def _tiktok():
    import warden_tiktok
    return warden_tiktok


def _post():
    import warden_post
    return warden_post


def _youtube():
    import warden_youtube
    return warden_youtube


def cmd_fetch(args):
    """A brief as text, or an honest failure.

    This runs wherever the container runs, and a campaign page can be behind a
    login, behind a bot wall, or simply unreachable from that network. None of
    those are reasons to invent what the brief says, so the command fails with
    the one instruction that always works.
    """
    try:
        print(_media().fetch_text(args.url))
    except Exception as exc:
        die(f"could not read {args.url}: {type(exc).__name__}. "
            "Ask the owner to paste the brief instead; never fill a rule set "
            "from a page you could not read.", code=1)
    return 0


def trusted_path():
    return os.path.join(state_dir(), "trusted.json")


def load_trusted(strict=False):
    if not os.path.exists(trusted_path()):
        return []
    try:
        with open(trusted_path()) as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError("the trusted list is not a list")
        return data
    except Exception as exc:
        # Um arquivo quebrado não é um arquivo ausente. A resposta antiga --
        # "nenhuma fonte confiável foi configurada" -- afirma que o dono nunca
        # fez nada, e o `trusted add` seguinte gravava por cima do que ele fez.
        if strict:
            die(f"{trusted_path()} exists but could not be read as a trusted "
                f"list ({type(exc).__name__}: {exc}). Fix or delete it; writing "
                f"over it now would erase the sources you did add.", code=1)
        print(f"warden: {trusted_path()} is unreadable "
              f"({type(exc).__name__}), so no source counts as trusted until "
              f"it is fixed", file=sys.stderr)
        return []


def save_trusted(entries):
    tmp = trusted_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, trusted_path())


def cmd_trusted(args):
    """The owner's list of sources worth clipping without a campaign to vouch.

    A campaign publishes its own archive; outside one, this is what says a link
    is safe to cut -- a channel or a domain the owner named, not a stranger's.
    """
    entries = load_trusted()
    if args.action == "list":
        print("\n".join(entries) if entries else "no trusted sources yet")
        return 0
    if args.action == "check":
        if not args.value:
            die("check needs a url: `warden trusted check <url>`")
        try:
            ok, reason = _media().trusted_check(args.value, entries)
        except _media().FonteBloqueada as exc:
            # Código 2, nunca 1. Sair 1 aqui seria indistinguível de "NOT
            # trusted", e é o agente quem lê o código: ele concluiria que a
            # fonte do dono não é de confiança por causa de uma recusa de rede,
            # e ofereceria `warden trusted add` para um canal que já está lá.
            die(str(exc), code=2)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        if ok:
            print(f"trusted: {reason}")
            return 0
        print(f"NOT trusted: {reason}", file=sys.stderr)
        return 1
    if args.action in ("add", "remove"):
        entries = load_trusted(strict=True)
    if args.action == "add":
        if not args.value:
            die("add needs a channel or domain: `warden trusted add @Channel` "
                "or `warden trusted add youtube.com`")
        entry = args.value.strip()
        if entry not in entries:
            entries.append(entry)
            save_trusted(entries)
        print(f"trusted sources: {', '.join(entries)}")
        return 0
    if args.action == "remove":
        entries = [e for e in entries if e != (args.value or "").strip()]
        save_trusted(entries)
        print(f"trusted sources: {', '.join(entries) if entries else '(none)'}")
        return 0
    die(f"unknown trusted action {args.action!r}")


def links_path():
    return os.path.join(state_dir(), "links.json")


def registra_link(url, porque):
    """O rastro de um link que entrou pela mão de quem o mandou.

    Não é um portão -- nada aqui recusa nada. É o registro de que aquele
    endereço entrou nesta máquina, quando, e por qual motivo foi tratado como
    autorizado. Existe porque a decisão do dono de 15/09/2026 tirou a pergunta
    sobre direitos do caminho ("quem manda o link está afirmando que pode usar
    o material") e uma decisão sem rastro é uma decisão que ninguém consegue
    revisar depois.

    Escreve e segue. Um estado não gravável não pode impedir um corte: perder o
    rastro é ruim, recusar-se a cortar por causa dele é pior.
    """
    linha = {"url": url, "why": porque,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        rows = []
        if os.path.exists(links_path()):
            with open(links_path()) as fh:
                carregado = json.load(fh)
            if isinstance(carregado, list):
                rows = carregado
        rows.append(linha)
        tmp = links_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows[-200:], fh, indent=2, ensure_ascii=False)
        os.replace(tmp, links_path())
    except (OSError, ValueError):
        pass
    return linha


def avaliza_o_link_de_quem_mandou(url, entries):
    """A lista de fontes do dono, mais o link que a pessoa acabou de mandar.

    A decisão do dono, de 15/09/2026: QUEM MANDA O LINK ESTÁ AFIRMANDO QUE PODE
    USAR O MATERIAL. O agente não pede licença, não pergunta sobre direitos e
    não trava um link solto por ele não estar numa lista.

    O que estava errado não era a lista -- ela continua valendo e continua
    sendo o que avaliza um link que o agente foi BUSCAR sozinho. O que estava
    errado era aplicá-la ao link que a própria pessoa colou na conversa, e o
    preço disso foi medido: `warden archive --trusted <url>` recusava o link do
    dono antes de baixar um byte, e a conversa virava um pedido de permissão
    para uma coisa que ele tinha acabado de mandar fazer.

    As regras de campanha continuam valendo onde sempre valeram -- no `check`,
    no `deliver` e na checagem do pacote de post.
    """
    from urllib.parse import urlparse
    saida = [str(e).strip() for e in (entries or []) if str(e).strip()]
    host = (urlparse(str(url or "")).netloc or "").strip()
    if host and host not in saida:
        saida.append(host)
    return saida


def cmd_authorize(args):
    """Decide whether a link is in the campaign's archive, and say why.

    The one honest answer to 'can I clip this video?' when the archive is a set
    of playlists: expand them and look, rather than claim membership you cannot
    see. Exit 0 authorised, exit 1 not -- so a skill can gate on it.

    SEM campanha não há arquivo a consultar e não há pergunta a fazer: o link
    veio da pessoa, e ela está afirmando que pode usar o material. Sai 0 e
    escreve o rastro. Este comando nunca foi sobre direitos autorais -- ele é
    sobre a lista que UMA campanha publicou -- e usá-lo como se fosse era o
    agente inventando um portão que a decisão do dono não tem.
    """
    if not args.campaign:
        registra_link(args.url, "sent by the person, with no campaign to check "
                                "it against")
        print(f"authorised: this link came from the person who asked for the "
              f"clip, and that is the authorisation. No campaign was named, so "
              f"there is no archive list to check it against -- campaign rules "
              f"still apply to the post package when there is a campaign.")
        return 0
    rules = load_campaign(args.campaign)
    try:
        ok, reason, title = _media().authorize(rules, args.url)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    named = f'"{title}"' if title else "this link"
    if ok:
        print(f"authorised: {named} is {reason}")
        return 0
    # The title is on stdout even on a no, so the agent can name the video when
    # it asks the owner whether to cut outside the archive.
    print(f"title: {title}" if title else "title: (unavailable)")
    print(f"NOT authorised: {named} is {reason}", file=sys.stderr)
    return 1


def _o_que_fazer_com_o_texto():
    """A linha que impede o reflexo de baixar o vídeo inteiro em seguida.

    Escrita uma vez e impressa pelos dois portões, porque a frase tem de ser a
    mesma nos dois: duas versões dela é o começo de duas ordens de trabalho.
    """
    print("this is the cheap pass: subtitles if the source publishes them, "
          "otherwise the audio. Choose the windows on this text FIRST, then "
          "pull ONLY those windows with --window, one per window. Do not pull "
          "the whole file to cut 20s out of it.", file=sys.stderr)
    print("measured on the 18-minute source of 15/09: the published subtitle "
          "is 5s and 89 KB and covers the whole video; the whole file is 13s "
          "and 382 MB, and transcribing it costs 3min46s. The subtitle pass is "
          "what saves the three minutes.", file=sys.stderr)


def _janela_pedida(valor):
    try:
        de, ate = [float(x) for x in str(valor).split("-", 1)]
    except ValueError:
        die("--window is two seconds, as 181-201.6", code=2)
    return de, ate


def _diz_o_in_point(caminho, dentro, de, ate):
    print(caminho)
    print(f"IN_POINT:{dentro:.3f}")
    print(f"this file is ONLY the {de:.1f}-{ate:.1f}s window of the source, "
          f"plus a couple of seconds of keyframe slack on each side. Inside "
          f"it, the window you asked for starts at {dentro:.3f}s -- so cut "
          f"it with `--start {dentro:.3f} --end {dentro + (ate - de):.3f}`, "
          f"not with the source's own timestamps, which this file no longer "
          f"has.", file=sys.stderr)


def _varias_janelas(valor):
    """"181-201.6,745.5-765" -> [(181.0, 201.6), (745.5, 765.0)]."""
    janelas = []
    for pedaco in str(valor).split(","):
        pedaco = pedaco.strip()
        if pedaco:
            janelas.append(_janela_pedida(pedaco))
    if not janelas:
        die("--windows is a comma separated list of windows, as "
            "181-201.6,745.5-765", code=2)
    return janelas


def _diz_as_janelas(resultados, janelas):
    for (caminho, dentro), (de, ate) in zip(resultados, janelas):
        _diz_o_in_point(caminho, dentro, de, ate)


def _archive_trusted(args, url):
    """O mesmo caminho barato, para um link que a pessoa mandou.

    A lista do dono continua na conta -- ela é o que avaliza um link que o
    agente foi buscar sozinho -- mas o link que veio na mensagem entra junto
    dela, com rastro. Ver `avaliza_o_link_de_quem_mandou`: quem manda o link
    está afirmando que pode usar o material.
    """
    registra_link(url, "sent by the person to `warden archive`")
    entries = avaliza_o_link_de_quem_mandou(url, load_trusted())
    out = safe_out(args.out or os.path.join(state_dir(), "footage", "trusted"),
                   "footage directory")
    varias = getattr(args, "windows", None)
    if varias:
        janelas = _varias_janelas(varias)
        try:
            resultados = _media().archive_windows(
                None, out, url, janelas, trusted=entries)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        _diz_as_janelas(resultados, janelas)
        return 0
    janela = getattr(args, "window", None)
    if janela:
        de, ate = _janela_pedida(janela)
        try:
            caminho, dentro = _media().archive_window(
                None, out, url, de, ate, trusted=entries)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        _diz_o_in_point(caminho, dentro, de, ate)
        return 0
    modo = "text" if getattr(args, "text_first", False) else "video"
    try:
        caminho = _media().archive_trusted(url, out, entries, mode=modo)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    print(caminho)
    if modo == "text":
        _o_que_fazer_com_o_texto()
    return 0


def cmd_archive(args):
    """Puxa o material, pelo caminho barato, de onde alguém apontou.

    Os dois caminhos são a campanha e o link. A campanha traz o acervo que o
    brief publicou; o `--link` (ou `--trusted`, o nome antigo) traz o endereço
    que a pessoa mandou. Um dos dois tem de existir porque sem nenhum não há
    ENDEREÇO de onde puxar -- não porque falte permissão. Quem manda o link
    está afirmando que pode usar o material, e isso é decisão do dono, de
    15/09/2026.
    """
    confiavel = getattr(args, "trusted", None)
    if confiavel and args.campaign:
        die("--campaign and --link name two different sources and you pick "
            "one: the campaign's archive, or the link the person sent.", code=2)
    if not confiavel and not args.campaign:
        die("this needs an address to pull from: `--campaign <id>` for that "
            "campaign's archive, or `--link <url>` for the link the person "
            "sent. Neither is a permission check -- there is just nowhere to "
            "download from without one of them.", code=2)

    if confiavel:
        return _archive_trusted(args, confiavel)

    rules = load_campaign(args.campaign)
    out = safe_out(args.out or os.path.join(state_dir(), "footage", safe_id(args.campaign)),
                   "footage directory")
    varias = getattr(args, "windows", None)
    if varias:
        janelas = _varias_janelas(varias)
        urls = R.get(rules, "sources.archive_urls", []) or []
        if not urls:
            die("this campaign publishes no archive, so there is no authorised "
                "link to take windows of", code=1)
        try:
            resultados = _media().archive_windows(
                rules, out, args.url or urls[0], janelas)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        _diz_as_janelas(resultados, janelas)
        return 0
    janela = getattr(args, "window", None)
    if janela:
        # Baixa SÓ a janela escolhida, de cada link autorizado do acervo. O
        # fiscal é o mesmo: `archive_window` recusa um link que não está em
        # sources.archive_urls antes de qualquer byte descer.
        de, ate = _janela_pedida(janela)
        urls = R.get(rules, "sources.archive_urls", []) or []
        if not urls:
            die("this campaign publishes no archive, so there is no authorised "
                "link to take a window of", code=1)
        alvo = args.url or urls[0]
        try:
            caminho, dentro = _media().archive_window(rules, out, alvo, de, ate)
        except Exception as exc:
            die(f"{type(exc).__name__}: {exc}", code=1)
        _diz_o_in_point(caminho, dentro, de, ate)
        return 0
    modo = "text" if getattr(args, "text_first", False) else "video"
    try:
        got, failed = _media().archive(rules, out, limit=args.limit, mode=modo)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    for path in got:
        print(path)
    for url, why in failed:
        print(f"could not pull {url}: {why}", file=sys.stderr)
    if not got:
        die("nothing came down from this campaign's archive", code=1)
    if modo == "text":
        # O que fazer em seguida, dito aqui, porque é aqui que o agente está
        # olhando. Sem esta linha ele baixa o vídeo inteiro de novo por reflexo.
        _o_que_fazer_com_o_texto()
    return 0


def cmd_transcribe(args):
    target = safe_out(args.out or os.path.splitext(args.file)[0] + ".transcript.json",
                      "transcript")
    window = None
    if args.window:
        try:
            a, b = args.window.split("-")
            window = (float(a), float(b))
        except ValueError:
            die("--window is two seconds, as 120-240")
    try:
        result = _media().transcribe(
            args.file,
            # `--scan` é a primeira passada: `tiny` na fonte inteira, só para
            # achar os candidatos. Transcrever 23 minutos com o modelo bom para
            # aproveitar 40 segundos é a conta que fazia isto levar 15 minutos.
            model_size="tiny" if args.scan else args.model,
            window=window, prefer_lang=[args.lang] if args.lang else None)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print(f"{target}  ({result['source']}, {len(result['segments'])} segments)")
    # An SRT beside the JSON, so `warden cut --subtitles` has something to burn
    # when the archive shipped no subtitles of its own. The JSON is for choosing
    # the moment; this is for putting the words on the screen. Only the usable
    # lines survive to_srt, so a transcript that was all silence writes no file
    # and says so rather than leaving an empty one that looks like a caption.
    if args.scan:
        # A passada barata não escreve SRT de propósito. `tiny` erra palavra, e
        # um arquivo que se parece com legenda é um arquivo que alguém queima.
        print("  this was the scan pass: good enough to choose a window, not to "
              "put on screen. Re-run with --window <a>-<b> for the words you "
              "will actually burn.", file=sys.stderr)
        return 0
    srt_text = _media().to_srt(result["segments"])
    if srt_text.strip():
        srt_path = safe_out(os.path.splitext(target)[0].replace(".transcript", "")
                            + ".srt", "subtitles")
        with open(srt_path, "w", encoding="utf-8") as fh:
            fh.write(srt_text)
        print(f"{srt_path}  (subtitles to burn with --subtitles, if you want them)")
        print("  nothing burns from it until you have read it: "
              f"`warden captions review {srt_path} --start <s> --end <s>`",
              file=sys.stderr)
    else:
        print("  no usable lines for a caption track: nothing to burn",
              file=sys.stderr)
    return 0


def cmd_digest(args):
    try:
        with open(args.transcript, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        die(f"could not read {args.transcript}: {type(exc).__name__}", code=1)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        die(f"{args.transcript} is not a transcript this tool wrote", code=1)
    window = None
    if args.window:
        a, b = args.window.split("-")
        window = (float(a), float(b))
    print(_media().digest(data["segments"], window=window))
    return 0


def cmd_signals(args):
    """The moments the words and the sound point at, for choosing viral cuts.

    Evidence, not a verdict: where a question is asked, an absolute claimed, a
    fight named, the room loud. The model reads these on top of the digest and
    decides which become clips -- the tool does not rank them, because it cannot
    watch.
    """
    try:
        with open(args.transcript, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        die(f"could not read {args.transcript}: {type(exc).__name__}", code=1)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        die(f"{args.transcript} is not a transcript this tool wrote", code=1)
    try:
        rows, quiet = _media().analyze_signals(data["segments"], source=args.source)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    if quiet:
        # Sem esta linha, "nenhum pico de som" e "não consegui ler o som" eram a
        # mesma saída, e quem escolhe a janela não sabia em qual dos dois estava.
        print(f"# the loud signal is NOT in this list: {quiet}", file=sys.stderr)
    if not rows:
        print("no strong signals stood out. Read the digest and choose on the "
              "words; a quiet transcript is not a bad one.", file=sys.stderr)
        return 0
    print(f"# {len(rows)} moments carry a signal, in time order. Evidence, not a "
          "ranking: cluster them into clips and judge on the words.")
    for r in rows:
        start = r["start"] or 0
        stamp = "%d:%02d" % (int(start // 60), int(start % 60))
        print(f"[{stamp}] {','.join(r['signals'])}: {r['text']}")
    return 0


def clip_out(path):
    """A bare filename becomes a deliverable path; a full path is left alone.

    The skills document `--out <file>`, and a bare name resolves against whatever
    the turn's working directory happens to be -- which is not a directory this
    agent is allowed to write to, so the documented form failed. It now lands in
    the one directory the chat will attach from.
    """
    path = os.path.expanduser(str(path or ""))
    if not os.path.dirname(path):
        path = os.path.join(clips_dir(), path)
    return safe_out(path, "clip")


def _nome_livre(nome):
    """`nome`, ou o primeiro vizinho dele que ainda não existe em disco.

    O segundo pedido apagava o primeiro. Os clipes do lote se chamavam
    `corte-01.mp4`, `corte-02.mp4` -- nomes FIXOS -- e todos resolvem para o
    mesmo `clips_dir()`, que é global e não por pedido. Então:

      1. a pessoa pede cortes do link A: saem `corte-01/02/03.mp4`;
      2. ela pede cortes do link B antes de os de A terem sido enviados;
      3. os três arquivos de A são sobrescritos pelos de B, e a dívida de
         entrega de A -- que guarda o CAMINHO -- passa a apontar para o clipe
         errado. Ninguém percebe: o caminho existe e o vídeo é outro.

    O nome agora carrega a marca da fonte (`_marca_da_fonte`, a MESMA conta que
    nomeia os arquivos de janela), o que separa link de link. Esta função fecha
    o resto: dois pedidos do MESMO link em janelas diferentes ainda batem no
    mesmo nome, e aqui o arquivo que já está lá não é tocado -- o novo vira
    `-2`, `-3`, e quem for entregar recebe os dois caminhos certos.

    Recusar seria a outra saída possível, e é pior: um segundo pedido do mesmo
    link é legítimo, e morrer nele é não entregar clipe nenhum para não
    arriscar sobrescrever um.
    """
    alvo = clip_out(nome)
    if not os.path.exists(alvo):
        return nome
    raiz, ext = os.path.splitext(nome)
    for n in range(2, 1000):
        tentativa = f"{raiz}-{n}{ext}"
        if not os.path.exists(clip_out(tentativa)):
            print(f"# {nome} already exists from an earlier batch of this same "
                  f"link, and it was NOT overwritten: this clip is "
                  f"{tentativa}.", file=sys.stderr)
            return tentativa
    die(f"a thousand clips already share the name {nome} in {clips_dir()}. "
        f"Nothing was overwritten; clear that directory before asking again.",
        code=1)


def _cues_queimadas(result):
    """Quantas cues este render REALMENTE pôs na tela. -1 = não dá para saber.

    O portão de entrega perguntava `not result["style"]["caption"]`, que é uma
    pergunta sobre um caminho ter sido passado -- e um caminho pode existir sem
    haver legenda nenhuma dentro dele. Foi assim em 15/09/2026: `--subtitles
    lote.srt.aprovado` apontava para um arquivo que existe, `asked_for_captions`
    saiu `True`, o render não queimou nada e o clipe foi entregue mudo.

    A pergunta certa é um NÚMERO: quantas cues o render escreveu. O
    `warden_media` já o registra em `style.caption.cues` -- é a contagem de
    `Dialogue:` no ASS, ou seja, linhas que o libass de fato desenhou -- e zero
    é zero, tenha o caminho existido ou não.

    -1 é o terceiro estado e ele não é zero: um render antigo (ou um dublê de
    teste) que só diz "sim, queimei" sem dizer quantas. Tratá-lo como zero
    reprovaria entregas boas; tratá-lo como "queimou" é o que ele afirma.
    """
    cap = (result.get("style") or {}).get("caption")
    if isinstance(cap, dict):
        try:
            return int(cap.get("cues") or 0)
        except (TypeError, ValueError):
            return -1
    return -1 if cap else 0


# "Este `deliver` está dentro de um LOTE?" -- e por que não é um parâmetro.
#
# `deliver` é trocado por um dublê em vários testes desta suíte, com a
# assinatura de quatro argumentos escrita à mão. Um quinto parâmetro
# transformaria cada um desses dublês num TypeError no meio do lote -- que é
# exatamente a classe de falha silenciosa que este arquivo existe para não ter.
#
# `executa_o_plano` liga isto em volta do laço e desliga no `finally`. O laço
# que IMPRIME é sequencial de propósito (ver o comentário do ThreadPoolExecutor:
# "renderiza em paralelo, ENTREGA em ordem"), então não há duas leituras
# concorrentes disto.
_DENTRO_DE_UM_LOTE = False


def deliver(result, rules, campaign, ledger):
    """Um render pronto vira entrega, ou não vira e diz por quê.

    Os portões, nesta ordem, porque é a ordem em que uma entrega se perde: as
    regras da campanha (que é aritmética), o hook e a legenda, e só então a
    linha que anexa o arquivo.

    O contact sheet era o portão do meio até 16/09/2026 -- o `MEDIA:` não saía
    sem ele, porque enquanto a imagem não existisse ninguém tinha olhado o
    clipe, e foi assim que um arquivo com dez defeitos visíveis foi relatado
    como aprovado. O dono trocou esse portão pelo tempo dele naquele dia:
    "entrega sem olhar, sua unica obrigacao = hook e legenda". O mosaico
    continua sendo GERADO e impresso -- é a única prova visual que existe
    depois, quando algo volta errado -- e não bloqueia mais nada.

    Devolve 0 quando a entrega saiu, 1 quando não saiu. Quando não saiu, o
    stdout fica VAZIO: quem reprova não tem caminho para dar a ninguém.
    """
    for note in result["notes"]:
        print(f"  note: {note}", file=sys.stderr)
    media = probe(result["out"])
    findings = check(rules, media, None, ledger)
    blocking = [m for level, m in findings if level == REPROVA]
    # O caminho do arquivo NÃO é impresso aqui, e a ordem é o conserto.
    #
    # Ele era a primeira linha do stdout, antes dos portões. Numa reprovação o
    # stdout inteiro do comando virava UMA linha -- o caminho do clipe
    # reprovado -- no mesmo formato da primeira linha de um sucesso. E a
    # persona manda o contrário: "if `warden cut` exited non-zero there is no
    # line to send and no clip to describe". Havia: quem lesse o stdout achava
    # um caminho de arquivo pronto e entregava ao dono um clipe que o portão
    # tinha acabado de reprovar.
    #
    # Agora o stdout de uma reprovação é VAZIO, e o caminho só é escrito no
    # bloco de entrega, junto do SHEET: e do MEDIA: (ver abaixo).
    if blocking:
        print("this render does not clear the campaign yet:", file=sys.stderr)
        for message in blocking:
            print(f"  REJECT {message}", file=sys.stderr)
        return 1
    # O que o próprio render registrou sobre si: largura do hook contra a área
    # útil, linhas por cue, duração da cue mais longa. São exatos -- medidos por
    # quem desenhou -- então uma falha aqui é a definição de pronto quebrada, e
    # não uma estimativa discutível.
    # Legenda pedida e nenhuma legenda queimada é uma entrega muda, e isso
    # saía como UMA nota no meio de doze. O motivo já existe e é bom -- SRT não
    # aprovado, idioma conflitante, nenhuma linha na janela -- mas ele avisava
    # e não parava nada, e um lote de podcast inteiro pode sair sem legenda com
    # a explicação enterrada.
    breaches = list(result.get("style_breaches") or [])
    # Exceção deliberada: quando a fonte já traz a fala escrita na tela, não
    # queimar a nossa é a decisão CERTA, não uma falha. Reprovar aqui obrigaria
    # a entregar legenda dupla ou a não entregar nada.
    ja_vem_legendado = (result.get("style") or {}).get("source_caption_clash")
    # ZERO CUES, e não "o campo veio falso". Ver `_cues_queimadas`: o campo
    # falso é o que deixou passar o clipe mudo de 15/09/2026, porque a pergunta
    # que ele respondia era sobre o caminho e não sobre a tela.
    queimadas = _cues_queimadas(result)
    if (result.get("asked_for_captions") and not ja_vem_legendado
            and queimadas == 0):
        porques = [n for n in result.get("notes") or []
                   if "not burning captions" in n or "nothing was burned" in n]
        breaches.append(
            "captions were asked for and 0 cues were burned"
            + (": " + porques[0] if porques else "")
            + ". A podcast cut with no speech on screen is not the clip that "
              "was asked for -- fix what the note says, or cut without "
              "--subtitles on purpose.")
    if breaches:
        print("this render breaks the style rules, so it is not delivered:",
              file=sys.stderr)
        for message in breaches:
            print(f"  REJECT {message}", file=sys.stderr)
        # A ordem imperativa de re-cortar existe só no corte AVULSO, onde não
        # há nenhum outro clipe esperando entrega e a pessoa acabou de pedir
        # este.
        #
        # Num LOTE ela é a segunda de duas ordens que se excluem na mesma
        # saída: em 7a, para o clipe 02 reprovado saiu `re-cut it: a shorter
        # hook, a different window, or no --subtitles.` e para o clipe 01
        # liberado saiu `END YOUR TURN NOW ... (no tool call after it)`
        # (render-outs.txt:79-95). Uma manda terminar o turno sem chamar
        # ferramenta; a outra manda chamar de novo. O agente obedeceu esta, 21
        # segundos depois, e nunca mais falou com a pessoa.
        #
        # No lote quem dá a ordem é `conta_do_lote`, UMA vez, no fim: entregar
        # o que passou, dizer numa linha o que falhou, e re-cortar só se a
        # pessoa pedir.
        if _DENTRO_DE_UM_LOTE:
            print("  another take would need a shorter hook, a different "
                  "window, or no --subtitles -- but do NOT start one now. The "
                  "single order for this batch is at the END of this output.",
                  file=sys.stderr)
        else:
            print("  re-cut it: a shorter hook, a different window, or no "
                  "--subtitles.", file=sys.stderr)
        # A linha que faltava, e a falta dela custou os dois clipes de
        # 15/09/2026. O agente levou dois REPROVADO, tirou o `--hook`, o portão
        # calou, e ele entregou dois clipes sem legenda E sem hook dizendo que
        # estavam prontos. Cada remoção era, isolada, uma reação razoável a uma
        # recusa; juntas, eram o pedido do dono sendo apagado até a ferramenta
        # parar de reclamar. O portão que só diz "não" ensina a tirar coisas.
        print("  DO NOT drop what the person asked for to make this quiet. "
              "Removing --hook, --subtitles or the length silences the gate "
              "and delivers a clip nobody asked for: on 15/09 that shipped two "
              "cuts with no caption and no hook, reported as done. If you "
              "cannot clear this, say what is blocking it -- that is a real "
              "answer; a stripped clip is not.", file=sys.stderr)
        return 1
    # Os avisos que `check` repete em todo veredito DE PROPÓSITO morriam aqui: o
    # caminho da entrega lia `findings` e só imprimia o nível REJECT. Medido: um
    # clipe com "not checked, the brief does not settle it", com as regras de
    # `sources` que só o dono pode confirmar, e com o prazo a vencer saía na tela
    # idêntico a um clipe que passou por tudo -- SHEET:, checklist, MEDIA: -- e o
    # aviso não aparecia em lugar nenhum. Vale para `cut` e para `cut --plan`,
    # que passam pelos dois pela mesma função.
    warnings = [m for level, m in findings if level == ATENCAO]
    if warnings:
        print(f"{len(warnings)} thing(s) this render does not settle. Nothing "
              "below blocks the file; every one of them is yours to confirm "
              "before you send it:", file=sys.stderr)
        # Agrupado pelo prefixo que o próprio `check` escreve, porque em campanha
        # com muitos itens de `unknown` a mesma frase se repetia linha após linha
        # e o que variava -- o item -- ficava escondido no fim dela.
        groups = [("not checked, the brief does not settle it: ",
                   "nobody checked these, the brief does not settle them:"),
                  ("only you can confirm this: ",
                   "only you can confirm these, from the footage itself:")]
        for prefix, header in groups:
            items = [m[len(prefix):] for m in warnings if m.startswith(prefix)]
            if items:
                print(f"  {header}", file=sys.stderr)
                for item in items:
                    print(f"    CHECK  {item}", file=sys.stderr)
        loose = [m for m in warnings
                 if not any(m.startswith(prefix) for prefix, _ in groups)]
        for message in loose:
            print(f"  CHECK  {message}", file=sys.stderr)
    # O MOSAICO DEIXOU DE SER PORTÃO EM 16/09/2026, POR DECISÃO DO DONO.
    #
    # Ele nasceu de um clipe entregue com o hook cortado e uma legenda de seis
    # linhas, aprovado por toda a verificação numérica porque nenhum dos dez
    # defeitos era um número. A resposta foi exigir que alguém OLHASSE a imagem
    # antes de entregar, e `deliver` recusava sem ela.
    #
    # O que mudou é o preço. Medido em 16/09 num pedido real: as duas chamadas
    # de visão custaram 31s de 706s, e o dono roda isto numa chamada de tela
    # compartilhada. A frase dele, quando perguntei se o mosaico continuava
    # obrigatório: "entrega sem olhar, sua unica obrigacao = hook e legenda".
    #
    # Então o mosaico continua sendo ESCRITO -- ele custa ~1,3s e é a única
    # prova visual que existe depois, quando algo sai errado -- e deixou de
    # BLOQUEAR. A lista de conferência continua saindo, como lista do que olhar
    # se alguém for olhar, e não como portão.
    #
    # O que se perde está dito aqui para quem for mexer nisso depois: um defeito
    # que só a imagem mostra agora sai na mão do dono. Os portões que continuam
    # de pé são os dois que ele nomeou -- hook e legenda -- e esses `deliver`
    # ainda recusa, logo acima.
    sheet = result.get("sheet")
    import warden_style as S
    # A primeira linha do stdout de uma ENTREGA, e só de uma entrega: passou
    # pelos portões que sobraram. Ver o comentário lá em cima sobre por que ela
    # não sai antes deles.
    print(result["out"])
    if sheet and os.path.isfile(sheet):
        print(f"SHEET:{sheet}")
        print(f"the mosaic is written and NOT a gate any more (owner, 16/09): "
              f"deliver without opening it. It is here for when something comes "
              f"back wrong. If you do look, these are the {len(S.CHECKLIST)}:",
              file=sys.stderr)
        for item in S.CHECKLIST:
            print(f"  [ ] {item}", file=sys.stderr)
    else:
        print("no contact sheet was written for this render. Not a blocker "
              "since 16/09 -- deliver anyway -- but nothing visual exists for "
              "this clip if it comes back wrong.", file=sys.stderr)
    # A linha que realmente entrega o arquivo. Impressa pela ferramenta e não
    # composta pelo modelo, pelo mesmo motivo que todo número aqui vem da
    # ferramenta: um caminho digitado de memória é um caminho que não existe, e a
    # falha é silenciosa.
    #
    # E a instrução do que fazer com ela vem JUNTO, porque o SKILL sozinho não
    # bastou. Medido em 14/09: o agente escreveu esta linha no meio de um turno,
    # onde nada é enviado, e seguiu para o clipe seguinte. O arquivo existia, a
    # linha existia, e ninguém recebeu nada -- nem o agente soube.
    print("hand this file over by ENDING YOUR TURN with this line in the last "
          "message:", file=sys.stderr)
    print(f'  <caption>\n\n  MEDIA:{result["out"]}', file=sys.stderr)
    print("  Only the final message of a turn is read for attachments. A MEDIA: "
          "line written between tool calls attaches nothing and says nothing: "
          "the prose arrives and the file does not.", file=sys.stderr)
    # A frase que estava aqui -- "comece o próximo render em background primeiro,
    # que o fim dele te acorda para o próximo clipe" -- mandava fazer exatamente
    # o que não funciona, e foi escrita antes de a medição existir.
    #
    # Medido em 15/09/2026: a linha `MEDIA:` numa mensagem INTERMEDIÁRIA (a que
    # sai com `finish_reason=tool_calls`, porque ainda vem uma chamada de
    # ferramenta depois) é DESCARTADA pelo gateway -- 5 de 5 pedidos de mais de
    # um clipe. Na mensagem FINAL (`finish_reason=stop`) o anexo chega sempre:
    # 8 de 8, inclusive com duas linhas `MEDIA:` juntas na mesma mensagem.
    #
    # Ou seja: "um clipe por turno" não é uma regra do gateway, é uma regra que
    # este arquivo inventou. Um turno leva quantos anexos couberem na última
    # mensagem, e a única coisa que perde anexo é escrever a linha antes do fim.
    print("  If more clips were asked for, render ALL of them first and only "
          "then answer: every MEDIA: line goes in the SAME final message, one "
          "under the other. Two MEDIA: lines in one final message arrive "
          "(measured 15/09, 8 of 8); a MEDIA: line with another tool call "
          "after it does not (measured 15/09, 0 of 5).", file=sys.stderr)
    # A dívida, em disco, no instante em que o caminho fica disponível. Daqui
    # para a frente existe uma pergunta com resposta -- `warden delivered` --
    # em vez de só a memória do turno, que foi o que falhou em 14/09.
    entregas_registra(result["out"])
    # E a confirmação é no turno SEGUINTE, não neste. O texto antigo -- "quando
    # o envio voltar, rode `warden delivered`" -- pedia uma coisa impossível:
    # o anexo só sai QUANDO o turno termina, então não existe instante dentro
    # deste turno em que este comando possa ver o envio que ele cobra.
    print(f"  then, at the START of your NEXT turn, run `warden delivered "
          f"{result['out']}` so the count stops owing this one.",
          file=sys.stderr)
    print(f"MEDIA:{result['out']}")
    return 0


# A instrução curta que fecha toda saída que tem clipe para entregar.
#
# Fica em INGLÊS, e isso não é estilo: quem lê esta linha é o modelo, não a
# pessoa. Medido no teste 7a de 16/09/2026 -- o cabeçalho do `prep` e o
# checklist do render saíam 100% em português (`duração: 30s (pedida na
# mensagem)`, `o hook cabe inteiro no quadro...`) e eram a primeira e a última
# coisa que o agente lia a cada render; a conversa era em inglês e ele
# respondeu "Em produção." (live.db msg 21, 14:51:58). Texto de ferramenta é
# instrução para o modelo; a frase para a pessoa é o modelo que escreve, na
# língua dela.
#
# "nothing else pending" é a metade que importa: em 7a o agente leu o bloco de
# entrega e, DEPOIS dele, uma linha que dizia que o lote não estava pronto
# (render-outs.txt:97). Escolheu a última e chamou a ferramenta de novo.
MANDA_AS_LINHAS = "send these lines in your FINAL reply, nothing else pending"

# O rótulo que ABRE o bloco. Ele é uma constante, e não uma string solta dentro
# do `print`, por uma razão de auditoria: `warden-clip/SKILL.md` e
# `tests/test_skills.py` citavam um bloco `MEDIA: PRONTAS` que NENHUMA linha
# deste arquivo jamais imprimiu -- a skill prometia, o teste da skill afirmava a
# mesma string inventada, e as duas metades da rede dupla se confirmavam sem
# nunca olharem o programa. Daqui para a frente existe UMA string e ela mora
# aqui; quem a cita lê `warden.ROTULO_PRONTAS`.
#
# E ela NÃO é `MEDIA: PRONTAS`, embora fosse esse o nome usado. Duas razões,
# ambas de defeito e nenhuma de estilo:
#
#   1. tudo abaixo deste rótulo é copiado palavra por palavra para a mensagem
#      final, e o gateway lê TODA linha que começa com `MEDIA:` dessa mensagem
#      como caminho de arquivo. `MEDIA: PRONTAS` seria uma tentativa de anexar
#      um arquivo chamado `PRONTAS` no meio da única mensagem que entrega;
#   2. `PRONTAS` é português dentro do único texto da saída que o modelo é
#      mandado repassar SEM traduzir. Em 7a a conversa era em inglês e o agente
#      respondeu "Em produção." (live.db msg 21, 14:51:58) depois de um banho de
#      português vindo da ferramenta. O bloco final é o último lugar onde isso
#      pode voltar.
ROTULO_PRONTAS = ("END YOUR TURN NOW with exactly this as your final message "
                  "(no tool call after it):")

# A linha que acompanha cada anexo, e ela é um MOLDE, não uma frase.
#
# Era `Corte {i}: <caption>`. `Corte` é português, e esta é a única linha da
# saída inteira que o modelo é mandado copiar LITERALMENTE -- então a ferramenta
# estava escolhendo a língua da pessoa por ela, em toda conversa, inclusive nas
# que não são em português. É o defeito de idioma de 7a entrando pela porta da
# correção de entrega.
#
# Tudo dentro de `<>` é para SUBSTITUIR, como o `<caption>` já era. O texto de
# dentro é instrução em inglês porque quem o lê é o modelo; o que sai para a
# pessoa é o modelo que escreve, na língua dela.
RUBRICA_DO_CLIPE = "<clip {i} caption, in the person's own language>"


def caminhos_devidos_antes(ja_no_lote):
    """Os clipes que já deviam entrega e NÃO estão neste lote. [(nome, caminho)].

    Existe por uma órfã medida: em 7a o `corte-27cd49161b-01.mp4` ficou pronto
    às 14:54:33 com `"sent": false`, e os dois lotes seguintes reprovaram tudo
    -- `# 0 of 1 cleared for delivery`. Um lote que não libera nada imprimia
    ZERO linhas `MEDIA:`, e o clipe pronto de cinco minutos antes só existia
    numa parte do histórico que a poda já tinha reescrito
    (`[SKILL_PRUNED: content lost in compression]`, 14:52:50).

    A dívida está em disco desde `deliver()`. O bloco final tem de lê-la, para
    que TODA linha `MEDIA:` devida saia na mesma mensagem -- e não só as deste
    lote.

    E só quando se sabe QUAL conversa é esta. `entregas_pendentes()` cobra pelo
    prazo de seis horas quando não consegue ler a sessão, e para uma CONTAGEM
    cobrar demais é o lado seguro do erro. Aqui não é: estes caminhos vão para
    dentro da mensagem que a pessoa recebe. Um clipe devido numa conversa que
    não existe mais, anexado nesta, é alguém recebendo um arquivo que não pediu
    e sem o texto que o acompanhava -- é o que `conversa_de_agora` já diz com
    todas as letras. Sem saber a conversa, este bloco leva só o que ESTE lote
    liberou.
    """
    if conversa_de_agora() is None:
        return []
    dentro = {os.path.abspath(c) for c in ja_no_lote}
    saida = []
    for row in entregas_pendentes():
        caminho = os.path.abspath(row.get("clip", ""))
        if caminho in dentro:
            continue
        dentro.add(caminho)
        saida.append((os.path.basename(caminho), caminho))
    return saida


def bloco_da_mensagem_final(liberados, arquivo=None, nao_saiu=None, de_antes=0):
    """O texto exato da mensagem que entrega o lote, pronto para copiar.

    Escrito uma vez e impresso pelo `cut --plan` e pelo `lote render`, porque
    duas versões dele são duas ordens de trabalho -- e a versão anterior desta
    saída dava a ordem ERRADA: ela mandava entregar "um clipe por turno", com
    `MEDIA:` na última mensagem de cada turno.

    Medido em 15/09/2026, e é por isso que a ordem mudou: o gateway lê `MEDIA:`
    só da mensagem FINAL do turno (`finish_reason=stop`), e dessa ele lê
    QUANTAS houver -- duas linhas juntas chegaram nas 8 de 8 vezes. A linha
    escrita numa mensagem intermediária (`finish_reason=tool_calls`) foi
    descartada nas 5 de 5. Então o lote inteiro cabe num turno, e o que perde
    clipe é escrever a linha antes do fim, não escrever duas.

    `liberados` é [(nome, caminho)]. Vai para stderr de propósito: o stdout do
    lote já carrega um `MEDIA:` por clipe, impresso pelo `deliver`, e repetir
    os mesmos caminhos lá faria a contagem de anexos do lote dobrar.

    `nao_saiu` é [(nome, porquê)] dos clipes que NÃO passaram, e ele entra
    DENTRO do texto a enviar, como um `<...>` a substituir. Era uma linha de
    contabilidade impressa DEPOIS do bloco -- e era a última linha da saída
    (render-outs.txt:97), portanto a ordem que o modelo obedeceu. Dizer o que
    faltou faz parte da entrega; proibir a entrega por causa disso é o que
    fazia o clipe pronto ficar na máquina.

    O caminho é ABSOLUTO, sempre. Um `out` relativo é legítimo num plano de
    corte e é um arquivo que não existe para quem recebe a mensagem.
    """
    if not liberados:
        return
    print("", file=sys.stderr)
    print(ROTULO_PRONTAS, file=sys.stderr)
    print("", file=sys.stderr)
    caminhos = [os.path.abspath(c) for _nome, c in liberados]
    for i, caminho in enumerate(caminhos, 1):
        if i > 1:
            print("", file=sys.stderr)
        print(RUBRICA_DO_CLIPE.format(i=i), file=sys.stderr)
        print(f"MEDIA:{caminho}", file=sys.stderr)
    if nao_saiu:
        # Em inglês e entre `<>`, como o `<caption>`: é uma instrução para o
        # modelo escrever UMA frase na língua da pessoa, e não uma frase pronta
        # para ele repassar palavra por palavra. Uma frase pronta em português
        # numa conversa em inglês é o defeito de 7a por outra porta.
        quais = "; ".join(f"{n}: {p}" for n, p in nao_saiu)
        print("", file=sys.stderr)
        print(f"<one more line, in the person's own language: these did NOT "
              f"come out -- {quais}. Say you will re-cut only if they ask.>",
              file=sys.stderr)
    print("", file=sys.stderr)
    print(f"That is ONE message carrying all {len(liberados)} clip(s). Replace "
          f"every <...> with your own words for that clip, in the language the "
          f"person is writing in, and send nothing else after it: a tool call "
          f"after these lines throws the attachments away, silently.",
          file=sys.stderr)
    if de_antes:
        print(f"# {de_antes} of the path(s) above cleared in an EARLIER render "
              f"and was never sent. It is owed, so it goes out in this same "
              f"message.", file=sys.stderr)
    if arquivo:
        # O bloco também em disco, porque copiar de stderr um bloco de seis
        # linhas é onde uma linha se perde.
        try:
            with open(arquivo, "w", encoding="utf-8") as fh:
                fh.write(ROTULO_PRONTAS + "\n\n")
                for i, caminho in enumerate(caminhos, 1):
                    fh.write(RUBRICA_DO_CLIPE.format(i=i)
                             + f"\nMEDIA:{caminho}\n\n")
            print(f"# the same block is in {arquivo}, if you would rather read "
                  f"it than scroll.", file=sys.stderr)
        except OSError:
            pass
    # A última linha da saída, sempre, quando existe clipe para entregar.
    print("", file=sys.stderr)
    print(MANDA_AS_LINHAS, file=sys.stderr)


def _planos_pedidos(args):
    """`--shots 12.0-13.9,41.2-42.1` -> [(12.0, 13.9), (41.2, 42.1)].

    Os tempos são do relógio do ARQUIVO, exatamente como `--start` e `--end`.
    Num arquivo de janela isso quer dizer a partir do `IN_POINT` que o
    `archive` imprimiu, e não do segundo da fonte -- o mesmo relógio, a mesma
    regra, para não haver dois.
    """
    bruto = getattr(args, "shots", None)
    if not bruto:
        return None
    planos = []
    for pedaco in str(bruto).split(","):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        try:
            a, b = [float(x) for x in pedaco.split("-", 1)]
        except ValueError:
            die("--shots is a comma separated list of in-out pairs on the "
                "file's own clock, as 2.0-3.9,7.2-8.1", code=2)
        planos.append((a, b))
    if len(planos) < 2:
        die("--shots with fewer than two shots is a window, not an edit: pass "
            "--start/--end for that.", code=2)
    return planos


def _janela_sa(start, end, seconds):
    """Recusa uma janela impossível ANTES de renderizar. None se está tudo bem.

    Medido em 15/09/2026: `--start 10 --end 5` não dava erro. `length` era
    `max(0.1, end - start)`, o `max` engolia o sinal, e o corte saía ESTENDIDO
    ao mínimo da campanha -- um clipe de 15s, com contact sheet, `MEDIA:` e
    saída 0. Quem digitou o intervalo ao contrário recebia um arquivo pronto
    para postar, sem um aviso. O mesmo valia para `--seconds -5`.

    É o único defeito desta auditoria que ENTREGA um arquivo errado dizendo que
    está certo, e por isso o portão é aqui, antes do primeiro quadro.
    """
    if start is not None and end is not None and float(end) <= float(start):
        return (f"--start {start} comes at or after --end {end}, so there is no "
                f"window to cut. If you meant the other way round, swap them.")
    if seconds is not None and float(seconds) <= 0:
        return (f"--seconds {seconds} is not a duration. Pass the number of "
                f"seconds the person asked for, or --any-length when nobody "
                f"named one.")
    return None


def regras_de(cid):
    """As regras da campanha, ou um conjunto em branco quando não há campanha.

    O portão de link solto, por decisão do dono de 15/09/2026: QUEM MANDA O
    LINK ESTÁ AFIRMANDO QUE PODE USAR O MATERIAL. O agente não pede licença,
    não pergunta sobre direitos e não trava um link por não estar numa lista.

    O que some não é a checagem, é a PERGUNTA. `R.blank()` tem todos os campos
    nulos, e campo nulo neste projeto nunca foi "pode": ele é reportado como
    "ninguém checou", e o `check` repete isso em todo veredito. Então um corte
    sem campanha sai com a lista do que ninguém conferiu em cima dele, que é a
    resposta honesta -- e não com uma recusa sobre direitos que quem mandou o
    link já resolveu.

    Com campanha, nada muda: as regras dela continuam vencendo em `check`, no
    `deliver` e no pacote de post.
    """
    if cid:
        return load_campaign(cid)
    vazio = R.blank()
    # Os campos de identidade do TEMPLATE são textos de exemplo --
    # "slug-of-the-campaign", "Campaign as the brief names it" -- escritos para
    # o modelo preencher. Deixados de pé, o `render` imprimiria "Campaign as
    # the brief names it" como cabeçalho de um clipe que não tem campanha
    # nenhuma, e um exemplo na tela lê como um fato. Nulos, eles dizem a
    # verdade: não há campanha.
    for campo in ("id", "name", "source", "captured_at"):
        vazio[campo] = None
    return vazio


def _som_decidido(rules, stored, override=None):
    """(som, linha a dizer em voz alta ou None).

    Nunca devolve "não decidido". Antes devolvia, e a ausência de decisão
    virava um `die` que custava uma pergunta ao dono antes do primeiro clipe --
    medido em 15/09: 10min34s dos 26min22s até o único clipe foram perguntas.

    O padrão é o som ORIGINAL do arquivo, e a campanha continua vencendo: a
    regra `video.audio` = "forbidden" força `platform` dentro de `effective()`,
    que é onde ela sempre venceu.
    """
    settings, _ = P.effective(stored, rules)
    if override:
        return override, None
    som = settings.get("sound") or "embedded"
    if R.get(rules, "video.audio"):
        return som, (f"sound: {'muted' if som == 'platform' else 'original'} "
                     f"(the brief decides this one: video.audio="
                     f"{R.get(rules, 'video.audio')!r})")
    if "sound" in (stored or {}):
        return som, None
    return som, "sound: original (default; the brief does not decide this one)"


def lingua_do_hook(stored, lingua_da_fonte=None, medida=None):
    """(tag de idioma ou "none" ou None, linha a dizer em voz alta).

    A decisão do dono, de 15/09/2026: "o idioma da legenda tem que ser o mesmo
    que a linguagem do vídeo disponibilizado". Vídeo em inglês, hook e legenda
    em inglês; em espanhol, espanhol; em português, português.

    O padrão era `pt` FIXO, e o preço disso não era um clipe feio, era um lote
    vazio. `warden_media.cut` RECUSA queimar quando o hook e a legenda estão em
    línguas diferentes -- e essa recusa é a regra certa, ela existe porque um
    hook em português sobre uma legenda em inglês foi entregue uma vez. Com o
    padrão fixo e uma fonte em inglês, o agente escrevia o hook em português, a
    legenda descia em inglês, e a recusa pegava TODOS os clipes. Medido em
    15/09: o mesmo link trouxe legenda `en` em 2 de 6 rodadas.

    Três respostas, e nenhuma delas é um palpite sobre a língua:

      "none"  -> o dono não quer linha nenhuma em cima do quadro;
      "en"    -> uma tag, vinda da fonte ou pinada pela pessoa;
      None    -> ninguém leu a língua da fonte ainda, e isto diz isso em vez de
                 chutar `pt`, que é exatamente o chute que custava o lote.

    A preferência guardada VENCE a fonte. "hook em português num vídeo em
    inglês" é escolha de quem pediu, e o padrão é só o que acontece quando
    ninguém disse nada.

    `medida` diz se essa língua foi LIDA da fonte ou se saiu de desempate por
    ordem de preferência, e ela muda a JUSTIFICATIVA sem mudar a instrução.
    Medido em 15/09/2026, na imagem construída: o vídeo era em inglês, a
    legenda escolhida foi a `pt-br` auto-traduzida, e esta função dizia "a
    língua da fonte, lida do material" -- uma afirmação que ninguém tinha
    apurado, com cara de medição. A causa da escolha errada foi consertada no
    `warden_media`; a frase continuava estruturalmente capaz de mentir porque
    não perguntava se alguém chegou a ler a língua. Agora pergunta.

    `medida=None` -- o campo não veio, uma ficha antiga, um dublê que não o
    escreve -- conta como NÃO medida. É a única resposta honesta: não saber se
    foi medido não é a mesma coisa que ter medido.
    """
    escolha = (stored or {}).get("hook") or P.DEFAULTS.get("hook")
    if escolha == "none":
        return "none", "hook: none (their stored preference)"
    if escolha and escolha != P.HOOK_DA_FONTE:
        return escolha, (f"hook and captions: {escolha} (their stored "
                         f"preference, and it beats the source language)")
    # A tag vem do outro lado -- `pt`, `en-US`, `pt-BR` -- e o que interessa é a
    # raiz: `language_clash` compara os dois primeiros caracteres, e um `en-US`
    # contra um `en` seria uma divergência inventada.
    raiz = str(lingua_da_fonte or "").strip().split("-")[0].split("_")[0].lower()
    if not raiz or raiz == "na":
        return None, ("hook and captions: the SOURCE language (default) -- "
                      "and it has not been read yet, so nothing here picked a "
                      "language. Do NOT write the hook until it shows up: a "
                      "hook in one language over captions in another makes "
                      "`cut` refuse the whole clip.")
    if medida:
        return raiz, (f"hook and captions: {raiz} (default: the source "
                      f"language, read from the material)")
    return raiz, (f"hook and captions: {raiz} (default: it is the subtitle "
                  f"track that came down, picked by preference order -- the "
                  f"LANGUAGE OF THE VIDEO was never measured, so do not tell "
                  f"anyone the video is in {raiz})")


def _duracao_decidida(rules, stored):
    """(segundos, linha a dizer em voz alta). O número SAI DAQUI, nunca do modelo.

    Era um `die` -- "ninguém disse quanto tempo" -- e o `die` estava certo
    sobre o problema e errado sobre o remédio: a duração tinha de virar
    parâmetro, não tinha de virar pergunta. Agora ela vira parâmetro com um
    padrão anunciado, e quem quiser outro número passa `--seconds`.

    Os limites da campanha vencem, e vencem em `P.effective()`, que é onde eles
    já venciam: um padrão de 20s numa campanha que exige no mínimo 25 sai 25, e
    a linha diz que foi a campanha que mandou.
    """
    settings, sobrepostas = P.effective(stored, rules)
    try:
        segundos = float(settings.get("target_s") or P.DEFAULTS["target_s"])
    except (TypeError, ValueError):
        segundos = float(P.DEFAULTS["target_s"])
    de_onde = ("from their stored preference" if "target_s" in (stored or {})
               else f"default of {P.DEFAULTS['target_s']}s")
    lo = R.get(rules, "video.duration_min_s")
    hi = R.get(rules, "video.duration_max_s")
    faixa = ""
    if lo is not None or hi is not None:
        faixa = (f", inside the brief's limits ("
                 f"{'min ' + str(lo) + 's' if lo is not None else 'no minimum'}, "
                 f"{'max ' + str(hi) + 's' if hi is not None else 'no maximum'})")
    porque = [r for r in sobrepostas if "campaign" in r]
    return segundos, (f"length: {segundos:.0f}s ({de_onde}{faixa})"
                      + (" -- " + "; ".join(porque) if porque else ""))


# O sufixo que o `captions review --approve` escreve. Ver `S.approval_path`: é
# o caminho do SRT MAIS isto, então `lote.srt` e `lote.srt.aprovado` são vizinhos
# de nome e um `--subtitles` errado troca um pelo outro sem nenhum sintoma.
SUFIXO_ASSINATURA = ".aprovado"


def _porque_isso_nao_e_legenda(caminho):
    """None quando o arquivo TEM cue para queimar; a frase do erro quando não.

    O defeito mais caro do primeiro teste real, medido em 15/09/2026 na
    sequência de comandos que ficou no `state.db`: o agente levou dois
    REPROVADO, trocou `--subtitles lote.srt` por `--subtitles lote.srt.aprovado`
    e seguiu. O `.aprovado` é o arquivo de ASSINATURA -- um JSON com o sha256 do
    SRT e as janelas que alguém leu -- e não tem uma cue dentro. O `cut` aceitou
    o caminho, não achou legenda nenhuma, e renderizou MUDO em silêncio. Dois
    clipes foram entregues sem legenda e sem hook, relatados como prontos.

    O ponto não é o `.aprovado`: é que qualquer caminho existente servia. Um SRT
    vazio, um arquivo pela metade, um JSON, um mp4 -- todos passavam por
    `os.path.exists` e todos rendiam o mesmo clipe mudo. Então a pergunta aqui
    não é "esse caminho existe", é "esse arquivo tem fala dentro". Um clipe sem
    legenda que ninguém pediu é um clipe refeito, e refazer custa mais do que
    morrer aqui com o nome do arquivo certo na tela.
    """
    if not caminho:
        return None
    bruto = os.path.expanduser(str(caminho))
    if not os.path.isfile(bruto):
        return (f"--subtitles {caminho} is not a file. Nothing would be burned "
                f"and the clip would come back silent, which is not the clip "
                f"that was asked for.")
    if bruto.endswith(SUFIXO_ASSINATURA):
        srt = bruto[:-len(SUFIXO_ASSINATURA)]
        # A frase NOMEIA o arquivo certo, e não só o errado. A alternativa --
        # "esse arquivo não é uma legenda" -- deixa quem lê procurando, e quem
        # está procurando tende a tirar o flag em vez de trocá-lo, que foi
        # exatamente o que aconteceu no passo 4 da sequência medida.
        return (f"{os.path.basename(bruto)} is the APPROVAL SIGNATURE that "
                f"`warden captions review --approve` writes, not a subtitle "
                f"file: it carries the SRT's sha256 and the windows a person "
                f"actually read, and it has no cue in it. Burning it burns "
                f"nothing. The subtitle is {srt}"
                + ("" if os.path.isfile(srt)
                   else " -- which is not on disk either, so run "
                        "`warden lote prep` or `warden transcribe` first")
                + f". Pass `--subtitles {srt}`.")
    try:
        cues = _media()._read_srt(bruto)
    except Exception as exc:
        return (f"{caminho} cannot be read as a subtitle "
                f"({type(exc).__name__}: {exc}). A file that does not parse "
                f"burns nothing: the render would come back silent with every "
                f"other gate green.")
    if not cues:
        return (f"{caminho} has no readable cue in it -- it parsed and came "
                f"back empty, so there is no line to burn and the clip would "
                f"come back silent. Check that this is the SRT and not the "
                f"transcript JSON, the approval card, or a file that was "
                f"written but never filled.")
    return None


def _exige_legenda_de_verdade(caminho):
    """Morre com a frase inteira, ou devolve o caminho. Ver a função acima."""
    porque = _porque_isso_nao_e_legenda(caminho)
    if porque:
        die("refusing to cut with this as the subtitle: " + porque
            + " Rendering without the caption anyway is what this refusal "
              "exists to stop -- a silent podcast cut looks finished on the "
              "contact sheet and is a clip nobody can use.", code=2)
    return caminho


def cmd_cut(args):
    if getattr(args, "plan", None):
        return cmd_cut_plan(args)
    # Com `--shots` o começo e o fim vêm dos planos: o primeiro plano é o
    # começo e a soma das durações é o comprimento. Exigir `--start/--end`
    # junto seria pedir a mesma coisa duas vezes, e em dois relógios que podem
    # discordar.
    planos_pedidos = _planos_pedidos(args)
    # `campaign` saiu daqui em 15/09/2026. Ver `regras_de`: quem manda o link
    # está afirmando que pode usar o material, e exigir uma campanha para
    # cortar um link solto era o agente pedindo licença por algo que a pessoa
    # já decidiu. Um corte sem campanha sai com todos os campos como "ninguém
    # checou", que é o que eles são.
    obrigatorios = ("source", "out") if planos_pedidos else (
        "source", "start", "end", "out")
    if (mal := _janela_sa(getattr(args, "start", None),
                          getattr(args, "end", None),
                          getattr(args, "seconds", None))):
        die(mal, code=2)
    missing = [name for name in obrigatorios
               if getattr(args, name, None) is None]
    if missing:
        die("cut needs " + ", ".join("--" + m if m != "source" else "a source"
                                     for m in missing)
            + " -- or a --plan that carries them for a whole batch")
    # A dívida em voz alta, e só em voz alta. Ver `avisa_do_que_esta_devendo`:
    # um corte com `--start`, `--end` e `--out` escritos à mão é uma ordem
    # explícita, e recusá-la seria a ferramenta discutindo com quem já decidiu.
    # A linha `MEDIA:` devida continua saindo no bloco final.
    avisa_do_que_esta_devendo()
    if planos_pedidos:
        args.start = min(a for a, _b in planos_pedidos)
        args.end = max(b for _a, b in planos_pedidos)
    # Antes de qualquer coisa cara: o arquivo que foi apontado como legenda é
    # uma legenda? Aqui em cima de propósito -- depois do render a resposta
    # custa o render inteiro, e o render é o que demora.
    if getattr(args, "subtitles", None) is not None:
        _exige_legenda_de_verdade(args.subtitles)
    rules = regras_de(args.campaign)
    stored = P.load(state_dir())
    # Som e duração são decididos antes de um quadro ser escrito, como sempre
    # foram. O que mudou em 15/09/2026 é que a falta de decisão deixou de ser
    # um `die` e passou a ser um PADRÃO ANUNCIADO.
    #
    # Os dois `die` que estavam aqui estavam certos sobre o problema -- um
    # clipe mudo por acidente, um clipe 22% mais longo que o número pedido --
    # e errados sobre o remédio. Eles não impediam o defeito, eles obrigavam
    # uma pergunta: e a pergunta custa um turno inteiro, medido em 10min34s de
    # 26min22s no pedido de 15/09. Um padrão dito em voz alta corrige as duas
    # coisas -- a pessoa vê qual número foi usado e de onde ele veio, e pode
    # discordar no turno seguinte em vez de esperar para ser consultada.
    sound, diz_som = _som_decidido(rules, stored, args.sound)
    if diz_som:
        print(diz_som, file=sys.stderr)
    if args.seconds is None and not args.any_length:
        args.seconds, diz_duracao = _duracao_decidida(rules, stored)
        print(diz_duracao, file=sys.stderr)
    # Este corte já foi feito e já passou? Ver `impressao_do_corte`: a pergunta
    # só é feita DEPOIS de som e duração estarem decididos, porque os dois
    # entram no quadro -- perguntá-la antes compararia um corte de 20s com um
    # de 15s como se fossem o mesmo.
    impressao = impressao_do_corte(args, rules, planos_pedidos, sound,
                                   args.seconds)
    if (ja := renders_liberado(impressao)):
        print(f"this exact clip has already been rendered and it CLEARED every "
              f"gate: {ja['clip']}. Same source, same window, same hook, same "
              f"subtitles, same length -- so re-rendering cannot change a "
              f"single frame, it can only cost the render again. The file "
              f"below is that clip; hand it over.", file=sys.stderr)
        print("  If something about it is wrong, change what is wrong -- "
              "another window, another hook, another length -- and this will "
              "render, because it will be another clip. Running the same "
              "command again is the one thing that cannot help.",
              file=sys.stderr)
        return deliver({"out": ja["clip"], "sheet": ja["sheet"],
                        "notes": list(ja.get("notes") or []),
                        "style": ja.get("style") or {},
                        "asked_for_captions": bool(ja.get("asked_for_captions")),
                        "style_breaches": []},
                       rules, args.campaign,
                       ledger_for(args.campaign) if args.campaign else [])
    try:
        result = _media().cut(args.source, clip_out(args.out), rules,
                              args.start, args.end,
                              caption_srt=args.subtitles, hook=args.hook,
                              track=resolve_track(args.track),
                              track_start=args.track_start,
                              sound=sound, crop=args.crop,
                              motion=args.motion, cover_footer=args.cover_footer,
                              asked_s=args.seconds, shots=planos_pedidos)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    # Sem campanha não há livro de posts a consultar: o teto por clipador é uma
    # regra de campanha, e uma lista vazia diz exatamente isso.
    code = deliver(result, rules, args.campaign,
                   ledger_for(args.campaign) if args.campaign else [])
    # Só o que PASSOU entra no livro. Um render reprovado tem de poder ser
    # tentado de novo -- e vai ser, porque consertar a campanha ou o SRT muda o
    # veredito sem mudar um parâmetro do corte.
    if code == 0:
        renders_registra(impressao, result)
    return code


def specs_dir():
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "SPECS")


def specs_path():
    """Onde a faixa medida do corpus mora, dentro do repo e versionada.

    No repo e não no estado do agente de propósito: é uma spec do projeto, ela
    entra num commit e um golden test quebra quando alguém a muda sem querer.

    O nome diz de que formato ela é a medida, e isso não é cosmético. Chamada
    `estilo-aprovado.json`, ela lia como "o estilo aprovado", ponto -- e o que
    ela mede são vinte scenepacks de animação, nenhum deles um corte de fala.
    Um nome genérico sobre um corpus específico é como uma faixa medida num
    formato acaba reprovando outro.
    """
    return os.path.join(specs_dir(), "estilo-aprovado-scenepack.json")


def specs_path_antigo():
    """O nome que a spec tinha até o Bloco B. Só para dizer onde ela foi parar."""
    return os.path.join(specs_dir(), "estilo-aprovado.json")


def cmd_style(args):
    """Medir o que já ficou bom, e reprovar o que sai da faixa.

    "Ficou feio" não é um erro que o agente vê sozinho. Um número fora da faixa
    dos aprovados é. Isto não treina modelo nenhum: mede clipes que o dono já
    aprovou, guarda as faixas, e compara o render novo com elas.
    """
    import warden_style as S
    if args.action == "extract":
        alvos = []
        for alvo in args.files:
            if os.path.isdir(alvo):
                alvos += [os.path.join(alvo, f) for f in sorted(os.listdir(alvo))
                          if f.lower().endswith((".mp4", ".mov", ".m4v"))]
            elif os.path.isfile(alvo):
                alvos.append(alvo)
        if not alvos:
            die("no clip to measure in what you passed", code=1)
        medidas = []
        for path in alvos:
            print(f"# measuring {os.path.basename(path)}", file=sys.stderr)
            try:
                m = S.measure(path)
            except Exception as exc:
                print(f"  skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            m["file"] = os.path.basename(path)
            medidas.append(m)
        if not medidas:
            die("nothing could be measured", code=1)
        if args.consolidate:
            spec = {"schema": 1,
                    "measured_from": [m["file"] for m in medidas],
                    "ranges": S.consolidate(medidas)}
            target = args.out or specs_path()
            if not args.out and target.endswith("estilo-aprovado-scenepack.json"):
                print("  (writing to the SCENEPACK spec. If what you just "
                      "measured are talking-head cuts, pass --out "
                      "SPECS/estilo-aprovado-fala.json: the two corpora are "
                      "not the same, and overwriting one with the other wipes "
                      "the measurement.)", file=sys.stderr)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(spec, fh, ensure_ascii=False, indent=1)
            print(target)
            print(json.dumps(spec["ranges"], ensure_ascii=False, indent=1))
        else:
            print(json.dumps(medidas, ensure_ascii=False, indent=1))
        return 0

    if args.action == "check":
        spec_file = args.spec or specs_path()
        if not os.path.isfile(spec_file):
            if os.path.isfile(specs_path_antigo()):
                die(f"the spec still has its old name "
                    f"({os.path.basename(specs_path_antigo())}). It measures twenty "
                    f"scenepacks and not one talking-head cut, so it was "
                    f"renamed to {os.path.basename(spec_file)}. Rename the "
                    f"file, or point --spec at it.", code=2)
            die(f"no measured spec at {spec_file}. Build one first with "
                f"`warden style extract <approved clips> --consolidate`.", code=2)
        with open(spec_file, encoding="utf-8") as fh:
            spec = json.load(fh)
        medida = S.measure(args.files[0])
        achados = S.check_against(medida, spec.get("ranges", {}))
        # Se o `cut` escreveu o que fez ao lado do arquivo, esses números valem
        # mais que qualquer coisa recuperada dos pixels: a largura do hook ali é
        # a que o PIL mediu com a fonte real antes de desenhar.
        lado = os.path.splitext(args.files[0])[0] + "-estilo.json"
        sidecar = None
        if os.path.isfile(lado):
            try:
                with open(lado, encoding="utf-8") as fh:
                    sidecar = json.load(fh)
                achados = S.check_sidecar(sidecar) + achados
            except Exception as exc:
                print(f"  (could not read {lado}: {type(exc).__name__})",
                      file=sys.stderr)
        else:
            print("  (no -estilo.json beside this file, so the text checks are "
                  "the approximate pixel ones. A clip this tool rendered has "
                  "one.)", file=sys.stderr)
        # O item 5 do Bloco B. A métrica de pixel não é apagada e não vota
        # sozinha: ela cruza com o que o PIL mediu, e o sinal é a dois.
        achados = S.cross_check(sidecar, medida) + achados
        # E o aviso de formato. Esta spec mede vinte scenepacks; um clipe com
        # legenda queimada é um corte de fala, que é um formato que este corpus
        # não contém. Dizer isso na tela é a diferença entre uma faixa que
        # informa e uma faixa que finge autoridade que não tem.
        e_fala = bool((sidecar or {}).get("caption"))
        if e_fala and (spec.get("formato") or "scenepack") == "scenepack":
            print(f"  WARNING  this clip has burned speech captions, and "
                  f"{os.path.basename(spec_file)} was measured over "
                  f"{len(spec.get('measured_from') or [])} scenepacks, none of "
                  f"them a talking-head cut. The ranges below describe another "
                  f"format: read them as context, not as approval. The speech "
                  f"corpus is Block E and it does not exist yet.",
                  file=sys.stderr)
        print(json.dumps(medida, ensure_ascii=False, indent=1))
        piores = [m for lv, m in achados if lv == "REJECT"]
        rotulo = {"REJECT": "REJECT", "ok": "ok    ", "note": "      "}
        for lv, m in achados:
            print(f"  {rotulo[lv]} {m}", file=sys.stderr)
        if piores:
            print(f"\nthis render is outside the approved range on "
                  f"{len(piores)} count(s). The contact sheet shows it, if you "
                  f"want to look: a number "
                  "out of band is usually visible.", file=sys.stderr)
            return 1
        print("\ninside the approved range on every enforced metric. That is not "
              "the same as good -- it is the same as not obviously broken. The "
              "contact sheet is written beside the render, and since 16/09 "
              "it is not a gate: pass the warning on in one line instead "
              "of investigating it.", file=sys.stderr)
        return 0
    die(f"unknown style action {args.action!r}")


# As linhas que ninguém pode assinar de olhos fechados.
#
# Escrito a partir do que foi de fato queimado. Em 15/09 o agente escreveu, com
# todas as letras: *"Legenda um pouco confusa ('Em 1826' — provavelmente erro do
# Whisper..., mas mantém sentido de comparação histórica). Vou aprovar assim
# mesmo"* -- e aprovou. Antes disso saiu `jokovic jokovic`. Um portão que quem
# está passando por ele decide contornar não é portão.
#
# O que dá para detectar sem adivinhar:
#
# - NÚMEROS. O Whisper erra número mais que qualquer outra coisa, e um ano
#   errado queimado ("Em 1826") é exatamente o caso medido.
# - PALAVRA REPETIDA colada ("jokovic jokovic"), que é a assinatura do erro de
#   transcrição, não da fala.
# - MARCAS DE LEGENDA AUTOMÁTICA: `>>` de troca de locutor e `[risadas]` de
#   anotação. Elas existem no arquivo do YouTube e não são o que alguém quer na
#   tela -- e o `>>` foi para a tela num edit de 15/09.
_SUSPEITA_NUMERO = re.compile(r"\d")
_SUSPEITA_REPETIDA = re.compile(r"\b(\w{3,})\s+\1\b", re.IGNORECASE)
_SUSPEITA_MARCA = re.compile(r">>|\[[^\]]+\]")


def linhas_suspeitas(window):
    """[(linha, motivo)] do que não se assina sem olhar."""
    saiu = []
    for r in window:
        # A LIMPEZA VEM PRIMEIRO, e isso é um conserto.
        #
        # `warden_style` apaga marcação decorativa -- o símbolo musical,
        # `[Music]`, `[risadas]`, o `[ __ ]` de palavrão censurado -- ANTES de
        # montar a cue, então nada disso chega à tela. Mas esta checagem lia o
        # SRT cru, via o marcador, e travava a legenda inteira por causa de um
        # texto que seria apagado de qualquer jeito.
        #
        # Medido duas vezes em dois testes reais, 15 e 16/09/2026: `[risadas]`
        # e `[ __ ]` entraram na lista de linhas suspeitas e os DOIS clipes do
        # pedido saíram sem legenda. O agente teve de passar `--keep` para um
        # marcador -- confirmar que leu e aprovou um texto que não existe no
        # produto final.
        #
        # Julgar o que vai para a tela é o ponto desta função. Então ela julga
        # o que vai para a tela.
        bruto = r.get("text") or ""
        try:
            import warden_style as _S
            texto = _S._limpa_marcacao(bruto)
        except Exception:
            texto = bruto
        if not texto.strip():
            continue          # só marcação: some na queima, nada a aprovar
        motivos = []
        if _SUSPEITA_NUMERO.search(texto):
            motivos.append("carries a number, which is what Whisper gets wrong "
                           "most often")
        if _SUSPEITA_REPETIDA.search(texto):
            motivos.append("repeats a word back to back, which is a "
                           "transcription artefact and not speech")
        if _SUSPEITA_MARCA.search(texto):
            motivos.append("carries an auto-subtitle marker (>> or [..]), "
                           "which is not something anyone wants on screen")
        if motivos:
            saiu.append((r, "; ".join(motivos)))
    return saiu


def cmd_captions(args):
    """As linhas que vão para a tela, para alguém ler antes de queimar.

    O comentário do `to_srt` já dizia que uma palavra errada queimada é pior que
    legenda nenhuma e que essa checagem fica com a pessoa. Só que nada obrigava
    essa pessoa a existir, e saiu um clipe com `jokovic jokovic` e uma frase que
    o sujeito não disse. Este comando é essa pessoa: imprime as linhas da janela
    do corte, e `--approve` assina o conteúdo do arquivo.
    """
    import warden_style as S
    if args.action == "review":
        if not os.path.isfile(args.srt):
            die(f"there is no subtitle file at {args.srt}", code=1)
        rows = _media()._read_srt(args.srt)
        lo = args.start if args.start is not None else 0.0
        hi = args.end if args.end is not None else float("inf")
        window = [r for r in rows if r["end"] > lo and r["start"] < hi]
        if not window:
            die(f"no caption line falls between {lo} and {hi}s", code=1)
        approved, why = S.approval_state(
            args.srt,
            start=args.start if args.start is not None else None,
            end=args.end if args.end is not None else None)
        print(f"# {len(window)} lines will burn between {lo:.1f}s and "
              f"{'end' if hi == float('inf') else f'{hi:.1f}s'} "
              f"({'approved' if approved else 'NOT approved'})")
        print("# read every one against the video. Whisper mishears, and the "
              "viewer finds out before you do.")
        for r in window:
            print(f"[{r['start']:7.2f} -> {r['end']:7.2f}]  {r['text']}")
        # As cues como vão realmente aparecer, que não é como estão no SRT: o
        # reflow parte um segmento longo em pedaços de duas linhas.
        cues = S.reflow_cues(window)
        longest = max((c["end"] - c["start"] for c in cues), default=0)
        most = max((len(c["lines"]) for c in cues), default=0)
        print(f"\n# on screen that becomes {len(cues)} cues, at most {most} "
              f"lines and {longest:.1f}s each.")
        suspeitas = linhas_suspeitas(window)
        if suspeitas:
            print(f"\n# {len(suspeitas)} line(s) here cannot be signed without "
                  f"saying what you decided about them:")
            for r, porque in suspeitas:
                print(f"  [{r['start']:7.2f}]  {r['text']}")
                print(f"             ^ {porque}")

        if args.approve and suspeitas:
            # O PORTÃO QUE FOI CONTORNADO. Em 15/09 o agente marcou uma linha
            # como provável erro e a aprovou no mesmo fôlego. Agora ele não
            # pode: ou corrige a linha, ou a repete inteira dizendo que leu e
            # está certa -- e o que ele decidiu fica escrito na aprovação.
            decididas = {t.strip() for t in (getattr(args, "keep", None) or [])}
            pendentes = [(r, w) for r, w in suspeitas
                         if r["text"].strip() not in decididas]
            if pendentes:
                print("", file=sys.stderr)
                print(f"NOT approving: {len(pendentes)} suspect line(s) have "
                      f"not been decided. You do not get to sign a line you "
                      f"just called probably wrong -- on 15/09 that is exactly "
                      f"what happened, and 'Em 1826' went to the screen.",
                      file=sys.stderr)
                for r, _w in pendentes:
                    print(f"  {r['text']}", file=sys.stderr)
                print("", file=sys.stderr)
                print("Two ways through, and both make you look at the line:",
                      file=sys.stderr)
                print("  * it is WRONG -> fix it in the srt and review again "
                      "(editing voids any signature, which is the point)",
                      file=sys.stderr)
                print("  * it is RIGHT -> repeat it back, exactly: "
                      "`--keep \"<the line>\"`, once per line",
                      file=sys.stderr)
                return 1
            print(f"\n# {len(decididas)} suspect line(s) read and kept as they "
                  f"are.")

        if args.approve:
            # Assina SÓ o que foi impresso. Assinar o arquivo depois de mostrar
            # uma janela é o defeito que queimou `aromasas`: cinco linhas lidas,
            # cento e cinquenta e duas assinadas.
            path = S.write_approval(args.srt, start=args.start, end=args.end)
            if args.start is None or args.end is None:
                print(f"\napproved the WHOLE file: {path}")
                print("every line above was printed, so every line is signed. "
                      "Edit the srt and the approval stops counting.")
            else:
                print(f"\napproved {args.start:.0f}-{args.end:.0f}s only: {path}")
                print(f"that is the window whose {len(window)} line(s) you just "
                      f"read, and nothing else. A cut outside it will render "
                      f"WITHOUT captions until you read and approve that window "
                      f"too. Approving one window never approved the file -- "
                      f"that is how a misheard word reached the screen.")
            return 0
        if not approved:
            print("\nnothing is approved yet, so `warden cut --subtitles` will "
                  "render this clip WITHOUT captions rather than burn a word "
                  "nobody checked. Re-run with --approve when the lines are "
                  "right.", file=sys.stderr)
            return 1
        print(f"\n{why}")
        return 0
    die(f"unknown captions action {args.action!r}")


def _batendo(futuro, rotulo, cada=15.0):
    """Espera o futuro, batendo o ponto enquanto espera.

    Silêncio longo lê como agente morto -- está escrito na persona deste
    projeto e é a razão de esta função existir. Um render de trinta segundos
    com nada na tela, vezes dois clipes, é um minuto em que quem pediu não
    sabe se alguma coisa está viva.
    """
    import concurrent.futures as _cf
    esperou = 0.0
    while True:
        try:
            return futuro.result(timeout=cada)
        except _cf.TimeoutError:
            esperou += cada
            print(f"  … still rendering {rotulo}, {esperou:.0f}s so far",
                  file=sys.stderr, flush=True)


def cmd_cut_plan(args):
    """Um lote de N janelas, com ponto de controle por clipe e uma conta no fim.

    O contrato de quantidade é a correção do defeito 2.9: pediram dois cortes e
    chegou um, e nada acusou a falta porque não havia comando de lote nem número
    pedido em lugar nenhum. Aqui o número pedido é o tamanho de `clips`, cada
    clipe é entregue assim que existe -- não o lote no fim -- e o comando só sai
    com 0 se todos saíram. Um que falhe é nomeado com o motivo, e o lote não é
    declarado pronto.

    O plano é um JSON:

      {"campaign": "<id>", "source": "<arquivo>", "sound": "platform",
       "clips": [{"out": "corte-01.mp4", "start": 12.0, "end": 32.0,
                  "hook": "...", "_": "por que este trecho"}]}

    O campo `_` de cada clipe é a função narrativa daquele corte, na gramática
    dos EDITS do PRIME: escrever por que o trecho é um clipe antes de renderizar
    é o que separa uma janela escolhida de vinte segundos de material bruto.
    """
    try:
        with open(args.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
    except Exception as exc:
        die(f"could not read the plan {args.plan}: {type(exc).__name__}: {exc}")
    if not isinstance(plan, dict) or not isinstance(plan.get("clips"), list):
        die(f"{args.plan} is not a cut plan: it needs a 'clips' list")
    liberados, failed, asked, _mosaicos = executa_o_plano(plan, args)
    return conta_do_lote(liberados, failed, asked)


def executa_o_plano(plan, args):
    """Renderiza e libera os clipes do plano. ([(nome, caminho)], [(nome, porquê)], N).

    Separada de `cmd_cut_plan` para que `warden lote render` reuse o MOTOR do
    lote em vez de escrever outro. Duas implementações de "renderiza N janelas
    e cobra a conta" são duas contagens que divergem no dia em que uma delas
    ganha um conserto -- e a contagem é justamente o que se está consertando.

    Quem chama decide o que fazer com o resultado: `cmd_cut_plan` imprime a
    conta e o bloco da mensagem final; `lote render` monta o contact sheet
    combinado do lote antes de fazer o mesmo.
    """
    clips = plan["clips"]
    if not clips:
        die(f"{getattr(args, 'plan', None) or 'this batch'} asks for no clips")

    # A dívida em voz alta, antes do primeiro render. Aqui é AVISO e não
    # portão, ao contrário de `lote render`: este é o caminho do `cut --plan`,
    # que é justamente o comando que traz o RESTO de um lote já começado --
    # travá-lo deixaria os clipes que faltam sem nenhum comando que os busque.
    # As linhas `MEDIA:` devidas saem no fim, em `conta_do_lote`.
    avisa_do_que_esta_devendo()

    # Sem campanha o lote roda igual, com todos os campos como "ninguém
    # checou". Ver `regras_de`: quem manda o link autorizou o material.
    cid = args.campaign or plan.get("campaign")
    rules = regras_de(cid)
    # A fonte do TOPO é o padrão de quem não trouxer a sua. Exigi-la mesmo
    # quando todo clipe traz a dele quebrava o `lote render`, onde cada clipe
    # sai de um ARQUIVO DE JANELA diferente e não existe uma fonte só para o
    # lote inteiro. O que continua sendo erro é um clipe sem fonte nenhuma.
    source = args.source or plan.get("source")
    sem_fonte = [i for i, c in enumerate(clips, 1)
                 if not (isinstance(c, dict) and c.get("source"))]
    if not source and sem_fonte:
        die(f"no source for clip(s) {', '.join(map(str, sem_fonte))}: the plan "
            f"has no 'source' at the top, none was passed, and those clips do "
            f"not carry one of their own")
    # A mesma pergunta do corte avulso, feita UMA vez para o lote inteiro e
    # antes do primeiro render: o que foi apontado como legenda tem cue dentro?
    # Um plano com o `.aprovado` no lugar do SRT renderizava N clipes mudos em
    # vez de um -- o mesmo defeito de 15/09/2026, multiplicado pelo tamanho do
    # lote. Ver `_porque_isso_nao_e_legenda`.
    for legenda in {c.get("subtitles") for c in clips
                    if isinstance(c, dict)} | {plan.get("subtitles")}:
        if legenda is not None:
            _exige_legenda_de_verdade(legenda)
    stored = P.load(state_dir())
    sound, diz_som = _som_decidido(rules, stored,
                                   args.sound or plan.get("sound"))
    if diz_som:
        print(diz_som, file=sys.stderr)

    # O mesmo padrão do corte avulso, na porta do lote. `seconds` no topo do
    # plano vale para todos; um clipe pode trazer o seu; e quando ninguém disse
    # nada, o padrão entra e é dito em voz alta -- era um `die` até 15/09/2026,
    # e o `die` custava uma pergunta antes do primeiro clipe.
    pedido_topo = plan.get("seconds", getattr(args, "seconds", None))
    sem_pedido = [c for c in clips
                  if not isinstance(c, dict) or c.get("seconds") is None]
    if pedido_topo is None and sem_pedido and not args.any_length:
        pedido_topo, diz_duracao = _duracao_decidida(rules, stored)
        plan["seconds"] = pedido_topo
        print(f"{diz_duracao} -- for the {len(sem_pedido)} of {len(clips)} "
              f"clips in this plan that name no length of their own",
              file=sys.stderr)

    asked = len(clips)
    # "delivered" era a palavra errada e ela contradizia o conserto do P0: este
    # laço RENDERIZA e libera; quem entrega é a MENSAGEM FINAL de um turno, que
    # este processo não escreve. Dizer "2 of 2 delivered" aqui é a ferramenta
    # afirmando uma entrega que não aconteceu -- exatamente o defeito de 14/09,
    # dito pela outra ponta.
    # "one clip per turn" saiu daqui em 15/09/2026, porque foi medido e é
    # falso: a mensagem FINAL de um turno leva quantos `MEDIA:` tiver (8 de 8,
    # inclusive com duas linhas juntas). O que perde anexo é escrever a linha
    # antes do fim do turno (0 de 5), e não escrever duas.
    print(f"# {asked} clips asked for. This command RENDERS and clears them; it "
          f"does not deliver. They reach the person when a turn ENDS with their "
          f"MEDIA: lines in the last message -- all of them in that SAME last "
          f"message.", file=sys.stderr)
    if asked > LOTE_INLINE:
        # Rede de segurança barata: este comando imprime TODAS as linhas, e
        # `lote render` é quem divide o lote para que os primeiros clipes
        # cheguem antes de o último terminar de renderizar.
        print(f"# {asked} clips is more than {LOTE_INLINE}: this command "
              f"renders all of them before printing anything to send, so the "
              f"first clip waits for the last. `warden lote render` splits the "
              f"batch instead -- the first {LOTE_INLINE} come out now and the "
              f"rest are left in a plan file it names, for a second command. "
              f"Neither way wakes anybody: nothing in this tool can.",
              file=sys.stderr)
    liberados, failed, mosaicos = [], [], []
    ledger = ledger_for(cid) if cid else []

    def _renderiza(spec, i):
        """Só o render. A entrega fica na thread principal, em ordem."""
        if not isinstance(spec, dict):
            raise RuntimeError("not an object in the plan")
        if spec.get("start") is None or spec.get("end") is None:
            raise RuntimeError("the plan gives no start/end for this clip")
        name = spec.get("out") or f"clip-{i:02d}.mp4"
        return _media().cut(
            spec.get("source") or source, clip_out(name), rules,
            float(spec["start"]), float(spec["end"]),
            caption_srt=spec.get("subtitles", plan.get("subtitles")),
            hook=spec.get("hook"),
            track=spec.get("track", plan.get("track")),
            track_start=spec.get("track_start", plan.get("track_start")),
            sound=spec.get("sound", sound),
            crop=spec.get("crop", plan.get("crop")),
            shots=spec.get("shots"),
            asked_s=spec.get("seconds", plan.get("seconds")),
            language=spec.get("language", plan.get("language")),
            motion=spec.get("motion", plan.get("motion", True)),
            cover_footer=spec.get("cover_footer", plan.get("cover_footer")))

    # Renderiza em paralelo, ENTREGA em ordem.
    #
    # As duas coisas juntas de propósito. Em paralelo porque render é o que
    # sobrou de lento depois que o download deixou de trazer o vídeo inteiro;
    # em ordem porque quem lê esta saída é um modelo, e blocos de dois clipes
    # intercalados na tela são dois clipes que ele confunde. Então o clipe 1
    # é impresso assim que fica pronto -- e o 2 já está renderizando enquanto
    # isso, em vez de começar depois.
    #
    # Quantos de cada vez, e o que se sabe de verdade sobre isso.
    #
    # O comentário anterior aqui dizia "dois cabem porque o pico medido foi
    # 1815 MiB num container de 3 GB". Era uma afirmação minha e ela não estava
    # medida -- e a própria aritmética dela dizia o contrário: 2 x 1815 = 3630,
    # acima dos 3072 MiB do limite. O 1815 é o pico de um render COM
    # transcrição junto, que é outra coisa; dois renders simultâneos nunca
    # foram medidos.
    #
    # Então o padrão é UM, que é o comportamento de antes e não inventa nada, e
    # o paralelismo fica atrás de uma variável de ambiente até alguém medir dois
    # renders simultâneos neste container. `WARDEN_RENDER_PARALELO=2` liga.
    # Velocidade que o OOM killer interrompe não é velocidade.
    import concurrent.futures as _cf
    try:
        largura = int(os.environ.get("WARDEN_RENDER_PARALELO") or 1)
    except ValueError:
        die("WARDEN_RENDER_PARALELO must be a whole number of renders", code=2)
    largura = max(1, min(largura, 4))
    # Dois clipes com o mesmo `out` eram determinísticos no laço sequencial: o
    # primeiro era sondado e entregue antes de o segundo o sobrescrever. Em
    # paralelo são dois ffmpeg escrevendo o MESMO mp4, o mesmo .ass e o mesmo
    # contact sheet ao mesmo tempo, e o que sai é lixo intercalado com um
    # mosaico que pode ser do outro clipe. Nada detectava isso.
    nomes = {}
    for i, spec in enumerate(clips, 1):
        nome = (spec.get("out") if isinstance(spec, dict) else None) or f"clip-{i:02d}.mp4"
        nomes.setdefault(nome, []).append(i)
    repetidos = {n: onde for n, onde in nomes.items() if len(onde) > 1}
    if repetidos:
        die("two clips in this plan write to the same file: "
            + "; ".join(f"{n} (clips {', '.join(map(str, onde))})"
                        for n, onde in sorted(repetidos.items()))
            + ". Give each clip its own `out`: rendered at the same time they "
              "would overwrite each other mid-write, and what you would get is "
              "neither of them.", code=2)

    print(f"# rendering {asked} clip(s), {largura} at a time. Each one is "
          f"printed the moment it is ready -- do not wait for the batch.",
          file=sys.stderr)
    # Sem `with`, e isto é um conserto. Dentro de um `with`, um `raise` cai no
    # `__exit__` -> `shutdown(wait=True)`, que ESPERA todos os renders já
    # submetidos terminarem. Medido: um `die` no clipe 1 de um lote de 6
    # deixava os outros cinco ffmpeg irem até o fim antes de o erro aparecer --
    # nove segundos de trabalho que ninguém queria mais. No laço sequencial o
    # lote parava na hora. Ctrl+C tinha o mesmo destino, e sem uma linha na
    # tela explicando por que nada respondia.
    piscina = _cf.ThreadPoolExecutor(max_workers=largura)
    # Daqui até o `finally` toda reprovação é reprovação DE LOTE, e num lote
    # quem dá a ordem é `conta_do_lote`, uma vez, no fim. Ver `deliver`.
    global _DENTRO_DE_UM_LOTE
    _antes_do_lote = _DENTRO_DE_UM_LOTE
    _DENTRO_DE_UM_LOTE = True
    try:
        futuros = [(i, spec, piscina.submit(_renderiza, spec, i))
                   for i, spec in enumerate(clips, 1)]
        for i, spec, futuro in futuros:
            name = (spec.get("out") if isinstance(spec, dict) else None) \
                or f"clip-{i:02d}.mp4"
            why = spec.get("_") if isinstance(spec, dict) else None
            print(f"\n# clip {i} of {asked}: {name}"
                  + (f"  -- {why}" if why else ""), file=sys.stderr)
            try:
                result = _batendo(futuro, f"clip {i} ({name})")
                code = deliver(result, rules, cid, ledger)
            except SystemExit:                 # `die` inside a clip is that clip's
                raise
            except Exception as exc:
                failed.append((name, f"{type(exc).__name__}: {exc}"))
                print(f"  this clip failed: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue
            if code == 0:
                # Nome E caminho. O bloco final tem de imprimir a linha
                # `MEDIA:` pronta para copiar, e um nome solto não é um
                # caminho: foi por caminho digitado de memória que este
                # projeto já entregou arquivo que não existia.
                liberados.append((name, result["out"]))
                if result.get("sheet"):
                    mosaicos.append((name, result["sheet"]))
            else:
                failed.append((name, "rendered but did not clear delivery"))
    except (SystemExit, KeyboardInterrupt):
        pendentes = sum(1 for _i, _s, f in futuros if f.cancel())
        print(f"\n# stopping the batch: cancelled {pendentes} clip(s) that had "
              f"not started. Any render already running finishes, because "
              f"ffmpeg is a child process and killing it mid-write leaves a "
              f"broken file.", file=sys.stderr)
        piscina.shutdown(wait=False)
        raise
    finally:
        _DENTRO_DE_UM_LOTE = _antes_do_lote
        piscina.shutdown(wait=True)
    return liberados, failed, asked, mosaicos


def conta_do_lote(liberados, failed, asked, sheet=None, depois=None,
                  sem_legenda=None, plano_do_resto=None):
    """A conta do lote e o bloco da mensagem final. 0 se todos saíram, 1 se não.

    `sheet` é o contact sheet combinado do lote, quando quem chama montou um;
    `depois` são os clipes que FICARAM DE FORA deste turno, quando ficaram, e
    `plano_do_resto` é o arquivo de plano que os traz -- os dois andam juntos,
    porque nomear o que faltou sem dizer o comando que o busca é a promessa que
    esta conta deixou de fazer (ver `_lote_render`);
    `sem_legenda` são os clipes que saíram sem uma palavra na tela num lote que
    PEDIU legenda, com o porquê de cada um.
    """
    print(f"\n# {len(liberados)} of {asked} cleared for delivery. NONE of them "
          f"has been sent by this command.", file=sys.stderr)
    for name, why in failed:
        print(f"#   missing: {name} -- {why}", file=sys.stderr)
    # Clipe mudo ao lado de clipe que falhou, e no MESMO lugar: a conta final.
    #
    # Ele não é pego por nenhum portão. `deliver` reprova "legenda pedida e
    # nenhuma queimada", mas só quando `asked_for_captions` é verdadeiro -- e
    # isso é `bool(caption_srt)` (`warden_media.cut`), ou seja, um clipe cortado
    # SEM SRT nunca pediu legenda e nada falta. O clipe passa por tudo, sai
    # bonito e mudo, e o motivo ficava numa linha de stderr trinta linhas
    # acima. Aqui ele fica onde o modelo tem de repetir.
    for name, why in (sem_legenda or []):
        print(f"#   NO CAPTIONS: {name} -- {why}", file=sys.stderr)
    if sem_legenda:
        print(f"# {len(sem_legenda)} of the {asked} clip(s) render with NO "
              f"words on screen, and this batch asked for captions. Say that "
              f"in the same final message, naming each clip and why: a silent "
              f"clip reported as a finished one is the delivery this tool "
              f"exists to stop.", file=sys.stderr)
    if sheet:
        # UM mosaico para o lote inteiro. Cada clipe já tem o seu -- `deliver`
        # desde 16/09 não bloqueia -- e este não substitui nenhum: ele é a
        # conferência única do lote, para que olhar dois clipes não custe dois
        # `vision_analyze` de ~14s cada.
        print(f"SHEET:{sheet}")
        print(f"# one image for the whole batch. Since 16/09 you do NOT have to "
              f"open it before delivering -- it is here for when a clip "
              f"comes back wrong.", file=sys.stderr)
    if depois:
        # Em voz alta, e com o comando na mão. A frase anterior aqui dizia
        # "still rendering in the background (...) when that render finishes it
        # wakes you": nada renderizava sob vigilância de ninguém e nada
        # acordava o agente, então esses clipes ficavam parados até a pessoa
        # escrever de novo. Ver o bloco em `_lote_render`.
        print(f"# {len(depois)} clip(s) asked for did NOT render in this turn: "
              f"{', '.join(depois)}. Nothing is rendering them and NOTHING "
              f"WILL WAKE YOU for them.", file=sys.stderr)
        if plano_do_resto:
            print(f"# this is the exact command that renders them, and it is "
                  f"the only thing that brings them:", file=sys.stderr)
            print(f"#     warden cut --plan {plano_do_resto}", file=sys.stderr)
            print(f"# Start it with your own terminal tool as a BACKGROUND "
                  f"process with completion notice on (measured 15/09: that is "
                  f"the only thing in this runtime that wakes an agent -- the "
                  f"`[IMPORTANT: Background process ... completed]` message; "
                  f"nohup and disown are refused). If you cannot, run the line "
                  f"above at the START of your next turn. Either way, say in "
                  f"your final message that {len(depois)} of the clips asked "
                  f"for are still owing -- do not report this batch as done.",
                  file=sys.stderr)
        print(f"# Deliver the ones below now -- do not wait for those.",
              file=sys.stderr)
    # ── A CONTA PRIMEIRO, O BLOCO POR ÚLTIMO ────────────────────────────────
    #
    # A ordem era a inversa, e foi ela que custou a entrega de 7a: o bloco
    # `END YOUR TURN NOW` saía e LOGO DEPOIS vinha `# and say in that same
    # message that this batch is NOT done: ...` (render-outs.txt:97, a última
    # linha da saída). Duas ordens que se excluem, e a segunda ganha por ser a
    # última: 21 segundos depois o agente chamou outro `lote render` em vez de
    # terminar o turno (live.db msg 69 -> msg 70, 14:57:20 -> 14:57:41).
    #
    # Agora a contabilidade vem antes e o bloco fecha a saída. O que faltou não
    # some: ele entra DENTRO do texto a enviar, que é onde a pessoa precisa
    # dele.
    incompleto = len(liberados) < asked
    if incompleto:
        print(f"# this batch is NOT done: {asked - len(liberados)} of the "
              f"{asked} clips asked for did not even clear. That goes in the "
              f"final message, naming which and why. Do not report the batch "
              f"as finished, and do not quietly deliver fewer than were asked "
              f"for.", file=sys.stderr)
    else:
        print("# the batch is done when those attachments came back, not when "
              "this line printed. Run `warden delivered` at the START of your "
              "NEXT turn: it reads whether each path actually left in a final "
              "message. It cannot confirm a send that has not happened yet, so "
              "running it before you end this turn answers nothing.",
              file=sys.stderr)
    # A dívida que já existia antes deste lote entra no MESMO bloco. Sem isto,
    # um lote que reprova tudo imprime zero linhas `MEDIA:` e o clipe pronto de
    # antes fica órfão -- foi o que aconteceu duas vezes em 7a.
    antigos = caminhos_devidos_antes([c for _n, c in liberados])
    if antigos:
        print(f"# {len(antigos)} clip(s) cleared in an EARLIER render and were "
              f"never sent: {', '.join(n for n, _c in antigos)}. They are owed "
              f"and they go in the same final message, below.", file=sys.stderr)
    todos = list(liberados) + antigos
    if liberados:
        # Esta linha sai SEMPRE que algo passou, inclusive num lote curto. Sem
        # ela, um lote em que o clipe 1 explode e os clipes 2 e 3 ficam
        # prontos não tinha uma linha mandando enviá-los: eles apareciam só
        # numa contagem, e clipe pronto que ninguém manda é clipe perdido --
        # que é o defeito de 14/09 chegando por outra porta.
        print(f"# {len(liberados)} clip(s) cleared: "
              + ", ".join(n for n, _c in liberados), file=sys.stderr)
    if todos:
        bloco_da_mensagem_final(todos, nao_saiu=list(failed),
                                de_antes=len(antigos))
    else:
        # Nada para anexar, e mesmo assim UMA ordem só. Em 7a os renders 2 e 3
        # deram `# 0 of 1 cleared` e o agente re-cortou nas duas vezes, sem
        # dizer uma palavra à pessoa desde as 14:51:58.
        print("# nothing cleared, so there is nothing to attach. END YOUR TURN "
              "by telling the person, in their language, what blocked each "
              "clip. Do NOT start another render on your own: re-cut only if "
              "they ask for it.", file=sys.stderr)
    return 1 if incompleto else 0


# ════════════════════════════════════════════════════════════════════ o lote
#
# `warden lote` existe por uma medição, e a medição é sobre CHAMADAS DO MODELO.
#
# Em 15/09/2026 um pedido de 2 clipes levou 26min22s até o primeiro -- e único
# -- clipe. A decomposição: 10min34s de perguntas que não mudaram um quadro,
# 8min50s de 35 chamadas do modelo, e 4min52s de ferramenta. Ou seja: o
# trabalho custou cinco minutos e a CONVERSA sobre o trabalho custou vinte.
#
# Nenhuma dessas 35 chamadas foi desnecessária isoladamente. O que era
# desnecessário era o formato: `archive`, `transcribe`, `digest`, `signals`,
# `captions review`, `cut`, `delivered` são sete comandos, cada um com uma
# saída que o modelo lê e sobre a qual decide a próxima -- e entre duas delas
# não há decisão nenhuma a tomar. `digest` e `signals` leem o MESMO arquivo e
# respondem sobre o MESMO texto; separá-los custa uma ida e volta para
# devolver algo que já estava decidido.
#
# Então `lote` é duas saídas, não sete. `prep` junta tudo o que o modelo
# precisa para escolher as janelas; `render` faz tudo o que vem depois de
# escolhê-las. A meta é ~4 chamadas do modelo por pedido, contra 35.
#
# Ele NÃO é um comando novo por dentro: cada pedaço aqui chama a função que já
# existia. Duas implementações de "renderiza N janelas" são duas contagens que
# divergem no primeiro conserto de uma delas.

LOTE_INLINE = 3
"""Quantos clipes saem no turno que pediu, antes de o resto ir para o fundo.

Três porque a conta é de tempo e não de gosto: um render mede ~64s neste
container, e o quarto clipe empurra a primeira entrega para mais de quatro
minutos de silêncio. Os três primeiros chegam, e o resto fica escrito num
plano com o comando que o busca -- o que é melhor que os cinco chegarem
juntos vinte minutos depois de ninguém ver nada.

O que este número NÃO significa: que alguém vai ser acordado quando o resto
ficar pronto. Nada neste arquivo consegue acordar o agente; ver o bloco de
`_lote_render` que escreve o `lote-resto.json`.
"""


def _marca_da_fonte(url):
    """O identificador deste link, e ele é o MESMO que o do arquivo de janela.

    `warden_media.archive_windows` nomeia cada janela `janela-<marca>-...` com
    `sha256(url)[:10]` -- é de lá que vem o `janela-0424974c68-...` que está no
    disco. Esta função repete a MESMA conta de propósito, em vez de inventar
    outra: duas contas para responder "que link é este" são duas respostas
    diferentes no dia em que uma delas mudar, e aí o diretório do lote e os
    arquivos que caem dentro dele falam de links diferentes.

    A normalização é só `strip()`, e isso é deliberado. Qualquer coisa além
    disso -- minúsculas no host, tirar a barra final, ordenar a query -- faria
    esta marca divergir da que o nome `janela-` carrega, que é exatamente o que
    não pode acontecer enquanto `warden_media` fizer a conta sobre a URL crua.
    """
    return hashlib.sha256(str(url or "").strip().encode("utf-8")).hexdigest()[:10]


def _dir_do_lote(url):
    """O diretório deste LINK, e não um diretório de lote só para todos.

    Era fixo -- `footage/lote`, o mesmo para qualquer link -- e isso entregava
    arquivo errado em silêncio. A sequência, achada numa auditoria adversarial
    de instalação nova:

      1. `lote prep <A>`: A publica legenda, e `lote.legenda.json` fica com a
         ficha de A;
      2. `lote prep <B>`: B não publica legenda e a fonte inteira não rende SRT
         nenhum, então a ficha NÃO é reescrita (ver `_lote_prep`: ela só é
         reescrita quando o prep produziu SRT);
      3. `lote render <B>`: lê a ficha que sobrou de A e queima a legenda de A
         nos clipes de B.

    Ninguém percebe: o clipe sai bonito, com as palavras erradas. Um diretório
    por link corta a raiz -- a ficha de A não está no caminho de B para ser
    lida. O cinto e suspensório está na própria ficha, que grava de que link
    ela é e é recusada quando não bate (ver `_lote_render`), porque o hash
    sozinho não protege de alguém mudar o esquema de diretório depois.
    """
    return safe_out(os.path.join(state_dir(), "footage", "lote",
                                 _marca_da_fonte(url)),
                    "batch directory")


def _padroes_do_lote(rules, stored, quantos=None, segundos=None, lingua=None,
                     medida=None):
    """As decisões que o lote toma sozinho, e a linha que anuncia cada uma.

    Devolve (dict, [linhas]). Nenhuma delas é uma pergunta: a decisão do dono
    de 15/09/2026 é no máximo UMA mensagem com perguntas antes do primeiro
    clipe, e só quando o pedido não diz nem quantidade nem duração. Estas
    linhas existem para que o agente repita UMA delas ao dono em vez de abrir
    uma conversa com ele.

    `lingua` é a língua da FONTE, quando já se sabe qual é, e `medida` diz se
    ela foi lida do material ou se saiu de desempate. As duas entram aqui e
    nenhuma é adivinhada aqui: quem as lê é `_lote_prep`, do material; quem as
    repete é `_lote_render`, da ficha da legenda. Ver `lingua_do_hook`.
    """
    settings, _ = P.effective(stored, rules)
    som, diz_som = _som_decidido(rules, stored)
    duracao, diz_duracao = (
        (float(segundos), f"length: {float(segundos):.0f}s (asked for in the message)")
        if segundos is not None else _duracao_decidida(rules, stored))
    if quantos is not None:
        n, diz_n = int(quantos), f"how many: {int(quantos)} clip(s) (asked for in the message)"
    else:
        n = int(settings.get("batch") or P.DEFAULTS["batch"])
        diz_n = (f"how many: {n} clip(s) (default; nobody said how many)")
    # O IDIOMA, e ele é um só para o hook e para a legenda de propósito: `cut`
    # recusa queimar os dois em línguas diferentes, então tratá-los como duas
    # decisões é criar a divergência que o portão existe para pegar.
    hook, diz_hook = lingua_do_hook(stored, lingua, medida)
    # A legenda é determinística, e é o serviço contratado numa campanha
    # musical: a campanha vem do detentor dos direitos, entrega o material
    # oficial e EXIGE legenda -- na língua do material. Transcrever e queimar é
    # o trabalho, não uma escolha editorial: a ferramenta faz, e o modelo não
    # precisa repetir o texto da letra na conversa para que aconteça.
    #
    # Duas coisas desligam: a preferência `captions` em "no", e a ficha da
    # campanha dizendo que o MATERIAL JÁ VEM LEGENDADO
    # (`sources.archive_has_captions`), caso em que queimar a nossa por cima
    # entrega legenda dupla.
    ja_legendado = R.get(rules, "sources.archive_has_captions") is True
    legenda = (settings.get("captions") != "no") and not ja_legendado
    if ja_legendado:
        diz_legenda = ("captions: do NOT burn them (the brief says the "
                       "material already ships subtitled)")
    elif legenda:
        # O idioma sai da mesma resposta do hook, e não de uma constante: dizer
        # "queimar em português" sobre um vídeo em inglês era a linha que fazia
        # o agente escrever o gancho na língua errada.
        onde = (f"in {hook}" if hook and hook != "none"
                else "in the source language, as soon as it is read")
        diz_legenda = (f"captions: burn them {onde}, transcribed by the tool "
                       f"(default)")
    else:
        diz_legenda = "captions: do not burn them (their stored preference)"
    padroes = {"n": n, "seconds": duracao, "sound": som, "hook": hook,
               "language": None if hook in (None, "none") else hook,
               "captions": legenda}
    linhas = [diz_n, diz_duracao,
              diz_som or f"sound: {'muted' if som == 'platform' else 'original'}",
              diz_hook, diz_legenda]
    return padroes, linhas


# ------------------------------------------------- o tamanho da saída do `prep`
#
# Medido em 15/09/2026, num podcast real: o `prep` devolvia 43.000 caracteres e
# o modelo gastava ~40s só LENDO, antes de decidir qualquer coisa. Quase tudo
# era o digest linha a linha da fonte inteira -- a transcrição de novo, com
# carimbo de tempo a cada 12 segundos. O modelo não precisa da transcrição para
# escolher uma janela: ele precisa dos MOMENTOS e de contexto suficiente em
# volta de cada um para saber do que aquele momento fala.
#
# Então a saída deixou de ser "o vídeo inteiro resumido" e passou a ser "os
# trechos com sinal, com contexto". Nada se perde: a transcrição completa
# continua em disco e o caminho dela sai como `TRANSCRIPT:` três linhas acima,
# então uma janela escolhida fora dos trechos continua possível -- é só ler o
# arquivo. O que sumiu foi a repetição obrigatória.
#
# Decisão do dono, 15/09/2026, e a meta dele: abaixo de 15 mil caracteres.
PREP_TETO_CHARS = 15000
_TETO_DIGEST = 9000
_TETO_SINAIS = 2600
# Quanto de contexto vai em volta de um sinal. Antes menos que depois porque um
# sinal é quase sempre a REAÇÃO a algo -- a risada vem depois da piada, a
# resposta depois da pergunta -- e o que vem depois dela é o que diz se aquilo
# fecha sozinho fora do episódio, que é a pergunta do clipe.
_ANTES_S = 15.0
_DEPOIS_S = 25.0


def _carimbo(segundos):
    segundos = int(segundos or 0)
    return f"{segundos // 60}:{segundos % 60:02d}"


def _janela_do_sinal(r, antes=_ANTES_S, depois=_DEPOIS_S):
    """(de, ate) em volta de um sinal, ou None quando ele não traz tempo."""
    try:
        comeco = float(r.get("start") or 0.0)
    except (TypeError, ValueError):
        return None
    try:
        fim = float(r.get("end") or comeco)
    except (TypeError, ValueError):
        fim = comeco
    return (max(0.0, comeco - antes), fim + depois)


def _funde(janelas):
    """As janelas em ordem de tempo, com as que se tocam viradas uma só.

    Fundir importa: num podcast os sinais vêm em rajada -- três risadas em
    quarenta segundos -- e três blocos sobrepostos imprimiriam a mesma fala três
    vezes, que é o desperdício que este corte existe para acabar, em escala
    menor.
    """
    saida = []
    for de, ate in sorted(janelas):
        if saida and de <= saida[-1][1]:
            saida[-1] = (saida[-1][0], max(saida[-1][1], ate))
        else:
            saida.append((de, ate))
    return saida


def _o_que_o_modelo_le(segmentos, rows, teto=_TETO_DIGEST):
    """(texto, trechos mostrados, sinais deixados de fora), dentro do teto.

    O TETO É DURO, e a primeira versão desta função não segurava: ela media
    bloco a bloco e sempre aceitava o primeiro, e num podcast em que metade das
    falas carrega sinal os blocos se fundem num só de 34 mil caracteres --
    medido num transcript sintético de 45 minutos com 351 segmentos marcados.
    Um teto que o primeiro item pode estourar não é um teto.

    Agora o orçamento é gasto em SEGUNDOS DE FONTE antes de qualquer texto ser
    montado. `densidade` é quantos caracteres o digest gasta por segundo de
    episódio -- medida no próprio digest, não estimada -- e o teto vira uma
    quantidade de fonte que cabe. Os sinais entram por peso até esse orçamento
    acabar; um sinal que cai dentro do que já foi coberto entra de graça, que é
    o que faz a rajada valer mais que o sinal solto.

    A ordem de CORTE é por peso e a de IMPRESSÃO é por tempo, de propósito: um
    modelo que lê fora da ordem do episódio monta janelas que não existem.
    """
    try:
        corrido = _media().digest(segmentos)
    except Exception as exc:
        return f"# the digest failed: {type(exc).__name__}: {exc}", 0, 0
    # Fonte curta: o digest inteiro já cabe, e aí ele é estritamente melhor que
    # um recorte dele. Cortar por cortar esconderia fala de graça.
    if len(corrido) <= teto:
        return corrido, 0, 0
    janelas = [(i, j) for i, j in
               ((i, _janela_do_sinal(r)) for i, r in enumerate(rows or []))
               if j]
    if not janelas:
        return (corrido[:teto].rsplit("\n", 1)[0]
                + f"\n# ... cut at {teto} characters. No moment stood out, so "
                  f"there was nothing to centre this on: the rest of the words "
                  f"are in the TRANSCRIPT: file above.", 0, 0)
    duracao = max((float(s.get("end") or s.get("start") or 0)
                   for s in segmentos), default=0.0)
    densidade = (len(corrido) / duracao) if duracao > 0 else 0.0
    orcamento_s = (teto / densidade) if densidade > 0 else duracao
    peso = {i: len(set((rows[i].get("signals") or []))) for i, _ in janelas}
    cobertura, dentro = [], []
    for i, janela in sorted(janelas, key=lambda par: (-peso[par[0]], par[0])):
        tentativa = _funde(cobertura + [janela])
        if sum(b - a for a, b in tentativa) > orcamento_s and dentro:
            continue
        cobertura = tentativa
        dentro.append(i)
    # Que sinais cada trecho FUNDIDO carrega, para a cabeça do bloco dizer por
    # que aquele pedaço está ali.
    sinais_do_trecho = []
    for de, ate in cobertura:
        tags = set()
        for i in dentro:
            a, b = _janela_do_sinal(rows[i])
            if a >= de and b <= ate:
                tags.update(rows[i].get("signals") or [])
        sinais_do_trecho.append(sorted(tags))
    blocos = []
    for (de, ate), tags in zip(cobertura, sinais_do_trecho):
        try:
            texto = _media().digest(segmentos, window=(de, ate))
        except Exception:
            continue
        if not str(texto).strip():
            continue
        blocos.append(f"--- {_carimbo(de)}-{_carimbo(ate)}  "
                      f"{','.join(tags) or 'signal'} ---\n{texto}")
    corpo = "\n".join(blocos)
    # A garantia final, depois de toda a aritmética: a densidade é uma média, e
    # uma média erra num trecho denso. O corte é na borda de uma linha, para não
    # entregar meia fala.
    if len(corpo) > teto:
        corpo = (corpo[:teto].rsplit("\n", 1)[0]
                 + f"\n# ... cut here to hold this under {teto} characters. "
                   f"The TRANSCRIPT: file above has the rest.")
    return corpo, len(cobertura), len(rows or []) - len(dentro)


def _lote_prep(args):
    """Uma saída só, sem perguntar nada, com tudo para escolher as janelas."""
    url = args.url
    registra_link(url, "sent by the person to `warden lote prep`")
    rules = regras_de(args.campaign)
    stored = P.load(state_dir())
    entries = avaliza_o_link_de_quem_mandou(url, load_trusted())
    out = _dir_do_lote(url)
    os.makedirs(out, exist_ok=True)

    # 1+2. O portão e o texto, na mesma chamada. O portão não pergunta e não
    # pede licença: quem mandou o link autorizou o material (decisão do dono,
    # 15/09/2026). O que o `archive_trusted` traz em `mode="text"` é a legenda
    # publicada quando ela existe, e o áudio quando não existe -- nunca o vídeo
    # inteiro, que é o que custava 13s e 382 MB por fonte.
    try:
        caminho = _media().archive_trusted(url, out, entries, mode="text")
    except Exception as exc:
        die(f"could not pull the words of {url}: {type(exc).__name__}: {exc}",
            code=1)
    print(f"SOURCE_TEXT:{caminho}")

    # A fonte INTEIRA, porque é nela que se escolhe o momento. `proposito` diz
    # para que serve e é o que escolhe o modelo barato para esta passada: o
    # texto que vira legenda é transcrito de novo, janela a janela, na hora do
    # render.
    try:
        transcricao = _media().transcribe(caminho, proposito="fonte")
    except Exception as exc:
        die(f"could not read the words of {os.path.basename(caminho)}: "
            f"{type(exc).__name__}: {exc}", code=1)
    alvo = safe_out(os.path.join(out, "lote.transcript.json"), "transcript")
    try:
        with open(alvo, "w", encoding="utf-8") as fh:
            json.dump(transcricao, fh, ensure_ascii=False, indent=1)
    except OSError as exc:
        die(f"could not write {alvo}: {exc}", code=1)
    print(f"TRANSCRIPT:{alvo}")
    print(f"# the words came from: {transcricao.get('source')}")
    # A LÍNGUA DA FONTE, lida e não suposta, e ela decide o idioma do gancho e
    # da legenda a partir daqui (decisão do dono, 15/09/2026). Impressa numa
    # linha própria porque é o dado que o modelo precisa ANTES de escrever o
    # gancho: um gancho numa língua e a legenda em outra faz `cut` recusar o
    # clipe, e o lote todo volta vazio.
    lingua_da_fonte = transcricao.get("language")
    # `language_measured` separa "eu li a língua do vídeo" de "escolhi esta
    # legenda por ordem de preferência". Medido em 15/09/2026, na imagem
    # construída: vídeo em inglês, legenda `pt-br` auto-traduzida escolhida, e
    # a linha abaixo afirmando que português era a língua do vídeo. A escolha
    # errada foi consertada no `warden_media`; o que se conserta aqui é a
    # AFIRMAÇÃO, que não perguntava se alguém tinha medido.
    lingua_medida = bool(transcricao.get("language_measured"))
    lingua_do_video = transcricao.get("source_language")
    print(f"LANG:{lingua_da_fonte or 'unknown'}"
          + ("" if lingua_medida else "  (NOT measured: this is the subtitle "
                                      "that came down, picked by preference "
                                      "order)"))
    if lingua_do_video:
        print(f"SOURCE_LANG:{lingua_do_video}")

    # O SRT ao lado, e a ficha de ONDE ele veio. A ficha é o que deixa o
    # `render` aprovar sozinho as linhas: aprovar automaticamente o que uma
    # transcrição inventou seria assinar palavra não lida, e foi assim que
    # `jokovic jokovic` foi para a tela. Aprovar o que o detentor dos direitos
    # publicou é outra coisa.
    srt = None
    # E "publicada" deixa de valer quando ela está FORA DA LÍNGUA DA FONTE.
    #
    # Medido em 16/09/2026, num pedido real: o vídeo era `en-US`, o YouTube não
    # entregou a faixa em inglês, e a tradução automática em português entrou
    # como legenda publicada -- assinada sozinha, queimada na tela. A regra do
    # dono, textual: "se o video for ingles quero legenda em ingles, se o vídeo
    # for em português, quero legenda em português".
    #
    # `fora_da_lingua_da_fonte` vem de `warden_media.transcribe` e traz o motivo
    # escrito. Com ela, esta ficha sai com `published: False` e o `lote render`
    # cai no ramo que já existe: transcrever a janela com o modelo bom, NA
    # língua da fonte, em vez de assinar uma tradução de máquina como se fosse
    # o que o detentor dos direitos escreveu.
    fora_da_lingua = transcricao.get("fora_da_lingua_da_fonte")
    publicada = ("published subtitle" in str(transcricao.get("source") or "")
                 and not fora_da_lingua)
    if fora_da_lingua:
        print(f"# the published subtitle is NOT in the source's language: "
              f"{fora_da_lingua}. It does not count as published, so the "
              f"windows will be transcribed in the language of the video.",
              file=sys.stderr)
    texto_srt = _media().to_srt(transcricao.get("segments") or [])
    if texto_srt.strip():
        srt = safe_out(os.path.join(out, "lote.srt"), "subtitles")
        try:
            with open(srt, "w", encoding="utf-8") as fh:
                fh.write(texto_srt)
            # `url` e `source_id` são o cinto e suspensório do diretório por
            # link. O diretório já separa A de B, mas ele é um ESQUEMA, e um
            # esquema pode ser mudado por quem vier depois; a ficha dizendo de
            # quem ela é sobrevive à mudança, porque o `render` a confere
            # contra o link que lhe pediram e recusa a que não bater.
            with open(os.path.join(out, "lote.legenda.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"srt": srt, "published": publicada,
                           "source": transcricao.get("source"),
                           # A língua viaja na ficha porque o `render` roda num
                           # processo novo: sem ela, ele teria de readivinhar
                           # o idioma, e readivinhar é como se chega a um
                           # gancho em português sobre uma legenda em inglês.
                           "language": lingua_da_fonte,
                           # E se ela foi MEDIDA, porque o `render` reconstrói
                           # o anúncio a partir desta ficha e tem de repeti-lo
                           # com a mesma honestidade -- uma ficha que só
                           # carrega a tag faz o `render` afirmar o que o
                           # `prep` teve o cuidado de não afirmar.
                           "language_measured": lingua_medida,
                           "source_language": lingua_do_video,
                           # A DURAÇÃO QUE A PESSOA PEDIU, pela mesma razão que
                           # o idioma: o `render` é outro processo e não viu a
                           # mensagem dela.
                           #
                           # Medido em 15/09/2026, no primeiro teste real pela
                           # linha da Plow: a pessoa escreveu "2 clipes de 20
                           # segundos", o `prep` leu certo ("duração: 20s
                           # (pedida na mensagem)") e o `render`, sem `--seconds`
                           # na linha de comando, caiu na preferência guardada e
                           # usou 15s. O número que ela disse perdeu para uma
                           # preferência de outro dia -- que é exatamente o que
                           # a regra "a duração é o que a mensagem disser" existe
                           # para impedir. `--seconds` continua vencendo isto.
                           "seconds": getattr(args, "seconds", None),
                           "url": url, "source_id": _marca_da_fonte(url)}, fh,
                          ensure_ascii=False, indent=1)
        except OSError:
            srt = None
    if srt:
        print(f"SRT:{srt}")

    # 3. digest E signals na MESMA saída. Eram dois comandos sobre o mesmo
    # arquivo, com nenhuma decisão a tomar entre um e outro: separá-los custava
    # uma ida e volta do modelo para devolver algo que já estava decidido.
    segmentos = transcricao.get("segments") or []
    # Os sinais são lidos ANTES do digest, embora sejam impressos depois: é em
    # volta deles que o digest é centrado. Ver `_o_que_o_modelo_le`. A ordem na
    # TELA continua a de sempre -- primeiro o texto, depois as provas -- porque
    # é a ordem em que se lê.
    try:
        rows, quiet = _media().analyze_signals(segmentos, source=caminho)
    except Exception as exc:
        rows, quiet = [], f"{type(exc).__name__}: {exc}"
    print("\n===== DIGEST =====")
    corpo, trechos, sinais_fora = _o_que_o_modelo_le(segmentos, rows)
    if trechos:
        print(f"# NOT the whole transcript: the {trechos} stretch(es) below are "
              f"where something happens -- a hook, a conflict, a reaction -- "
              f"with about {_ANTES_S:.0f}s before and {_DEPOIS_S:.0f}s after "
              f"each, so every one reads on its own. Choosing a window OUTSIDE "
              f"them is allowed and sometimes right: the full words are in the "
              f"TRANSCRIPT: file above, and reading it costs one call.")
    print(corpo)
    if sinais_fora:
        print(f"# {sinais_fora} weaker moment(s) fell outside those stretches, "
              f"to keep this readable. They carry the fewest signals of the "
              f"list; the SIGNALS section below still names the strongest of "
              f"them, and the TRANSCRIPT: file has every word.")
    print("\n===== SIGNALS =====")
    if quiet:
        print(f"# the loud signal is NOT in this list: {quiet}", file=sys.stderr)
    if not rows:
        print("# no strong signal stood out. Choose on the digest; a quiet "
              "transcript is not a bad one.")
    # Esta lista também tem teto, e pela mesma razão: num podcast de uma hora
    # ela sozinha passava de dez mil caracteres, uma linha por segmento, e
    # devolvia a transcrição pela porta dos fundos. O que sobrevive ao corte é
    # o trecho mais forte de cada rajada, que é o que decide a janela.
    # Escolhido por PESO, impresso por TEMPO -- a mesma separação do digest, e
    # pela mesma razão: uma lista fora da ordem do episódio faz o modelo montar
    # janelas que não existem.
    gasto, cortados, ficam = 0, 0, []
    for i, r in sorted(enumerate(rows or []),
                       key=lambda par: (-len(set(par[1].get("signals") or [])),
                                        par[0])):
        linha = (f"[{_carimbo(r.get('start') or 0)}] "
                 f"{','.join(r['signals'])}: {r['text']}")
        if gasto + len(linha) > _TETO_SINAIS and gasto:
            cortados += 1
            continue
        ficam.append((i, linha))
        gasto += len(linha) + 1
    for _i, linha in sorted(ficam):
        print(linha)
    if cortados:
        print(f"# and {cortados} more, left out to keep this readable. They "
              f"carry the fewest signals of the list; the TRANSCRIPT: file has "
              f"every word.")

    # 4. Os padrões, uma linha cada, para o modelo REPETIR e não PERGUNTAR.
    padroes, linhas = _padroes_do_lote(rules, stored, args.n, args.seconds,
                                       lingua=lingua_da_fonte,
                                       medida=lingua_medida)
    print("\n===== WHAT HAPPENS NEXT, WITHOUT ASKING =====")
    for linha in linhas:
        print(linha)
    print("\n# Say those five lines to the person in ONE line of prose and keep "
          "going. Do not ask them to confirm: each of these has a default and "
          "the default is what they get, so a question here buys a turn of "
          "waiting and changes nothing. If they want another number, they say "
          "so and the next render uses it.")
    # Até onde a fonte vai, para que as janelas escolhidas caibam nela. Sai do
    # FIM do último segmento e não do começo dele, que é a diferença entre
    # dizer onde a fala acaba e dizer onde a última frase começa.
    fim = max((seg.get("end") or seg.get("start") or 0 for seg in segmentos),
              default=0)
    # A língua entra no exemplo do comando, e não como nota de rodapé. O gancho
    # é a única coisa desta linha que o MODELO escreve, e escrevê-lo na língua
    # errada é o que faz `cut` recusar o clipe inteiro.
    idioma = padroes.get("language")
    em = (f"<hook line in {idioma}>" if idioma
          else "<hook line in the source language>")
    print(f"# then, in the SAME turn, pick {padroes['n']} window(s) on the text "
          f"above and run:\n"
          f"#   warden lote render {url} --windows <a-b,c-d> "
          f"--hooks '{em} 1|{em} 2'"
          + (f" --campaign {args.campaign}" if args.campaign else "")
          + (f"\n# the words run to about {int(fim // 60)}:{int(fim % 60):02d}, "
             f"so every window has to end before that." if fim else ""))
    # A INSTRUÇÃO é a mesma nos dois casos; o que muda é a JUSTIFICATIVA.
    # Escrever o gancho na língua da legenda é o que evita a recusa do `cut`,
    # e isso vale tenha a língua sido medida ou não -- mas só uma das duas
    # frases pode dizer "essa é a língua do vídeo", porque só numa delas
    # alguém leu a língua do vídeo.
    if idioma and lingua_medida:
        print(f"# WRITE THE HOOKS IN {idioma.upper()}. The caption burns in "
              f"{idioma} because that is the language of the video, and `cut` "
              f"REFUSES a clip whose hook and caption are in different "
              f"languages -- so a hook in another language does not come back "
              f"wrong, it comes back as no clip at all.")
    elif idioma:
        print(f"# WRITE THE HOOKS IN {idioma.upper()} anyway, and do NOT say "
              f"this is the video's language: nothing here measured it. "
              f"{idioma} is the subtitle that came down, chosen by preference "
              f"order, not by reading the source. The hooks go in {idioma} "
              f"because the caption burns in {idioma} and `cut` REFUSES a "
              f"clip whose hook and caption disagree -- that is the whole "
              f"reason, and it is not a claim about the video."
              + (f" The video itself is tagged {lingua_do_video}, so that "
                 f"{idioma} subtitle is very likely an auto-translation."
                 if lingua_do_video
                 and str(lingua_do_video).split("-")[0].lower() != idioma
                 else ""))
    return 0


def _folga_da_frase():
    """Quanto o `cut` pode andar para fechar a frase, em segundos.

    Lido de `warden_media` e não copiado: dois números para a mesma folga são
    duas folgas na primeira vez que um deles muda, e o sintoma é um clipe
    reprovado por uma assinatura que não alcança o próprio conserto.
    """
    try:
        return float(_media().TETO_DA_FRASE_S)
    except Exception:
        return 6.0


def _aviso_de_linha_suspeita(pendentes):
    """O aviso das linhas que merecem um olhar. A legenda QUEIMA de qualquer jeito.

    Até 16/09/2026 isto era uma RECUSA: uma linha suspeita e o clipe inteiro
    saía mudo até alguém repetir a linha de volta em `--keep`. A intenção era
    não queimar palavra que ninguém leu. O efeito medido foi o contrário do
    produto.

    Medido em 16/09/2026 na legenda publicada em português de uma live do
    YouTube: `>>` em 184 das 706 cues, mais as gaguejadas da transcrição
    automática ("esse esse", "do do", "ocular ocular") e os números. Nos dois
    testes reais pela linha da Plow, 2 clipes de 2 saíram SEM LEGENDA, duas
    vezes -- e é exatamente a reclamação do dono, "veio sem legenda também".
    Uma proteção que dispara em todo clipe não protege clipe nenhum: ela
    desliga o produto.

    A regra do dono, escolhida por ele em 16/09, é "nunca entrega sem legenda",
    e ele roda isto numa chamada de tela compartilhada, onde a volta extra do
    `--keep` custa mais um render inteiro. Então a linha suspeita deixa de
    calar o clipe e passa a ser o que sempre pôde ser: um aviso, ao lado do
    mosaico que o render escreve. As palavras estão na
    tela do mosaico; ler é olhar.

    `--keep` continua valendo, e agora quer dizer "eu já li esta, pare de
    avisar". O que ele nunca mais faz é decidir se o clipe tem legenda.

    Uma função só, e não duas cópias, porque o aviso tem DOIS caminhos até
    aqui -- a legenda publicada e a transcrição da janela -- e uma regra que
    vive em dois lugares são duas regras na primeira vez que uma é editada.
    """
    colar = " ".join(f'--keep "{r["text"].strip()}"' for r in pendentes)
    return (f"CAPTIONS BURNED, {len(pendentes)} line(s) worth a second look "
            f"(a number, a word repeated back to back, or an auto-subtitle "
            f"marker -- what a transcription gets wrong most often). They are "
            f"on the contact sheet the render wrote, if you want "
            f"to look. If one is wrong, say so in one line when "
            f"you hand the clip over; do NOT re-render for it unless the "
            f"person asks. To silence this warning on a re-run, append: "
            f"\n#     {colar}\n"
            f"#   the lines, as they appear on screen: "
            + " | ".join(r["text"].strip() for r in pendentes))


def _legenda_da_janela(out, resultados, janelas, guardadas, lingua=None):
    """Transcreve CADA janela e assina o que der.

    Devolve ({janela: srt}, {janela: porquê}, língua ouvida ou None, se ela
    foi MEDIDA).

    A língua volta junto porque sem legenda publicada ela não existe em lugar
    nenhum antes daqui: a ficha do `prep` não a traz, e o gancho precisa dela.
    É a mesma decisão do dono de 15/09/2026 -- o idioma segue a fonte -- pelo
    caminho em que a fonte não publica legenda.

    Existe porque uma fonte SEM legenda publicada entregava o lote inteiro sem
    legenda, em silêncio. O `render` não assinava nada, `queimar` ficava vazio,
    todo clipe saía sem uma palavra na tela -- e o portão de `deliver` não
    dispara nesse caso, porque `asked_for_captions` é `bool(caption_srt)`
    (`warden_media.cut`): sem SRT passado, ninguém pediu legenda, logo nada
    falta. Só que a legenda queimada é o produto anunciado e o padrão do
    projeto é `captions: yes`.

    É a JANELA que é transcrita, e não a fonte: `proposito="janela"` é o
    caminho que escolhe o modelo `small`. O SRT da fonte que o `prep` escreve
    veio do modelo BARATO e serve para escolher o momento, não para queimar --
    é o que o próprio `_lote_prep` já diz.

    Os tempos voltam no relógio da FONTE. O arquivo de janela tem relógio
    próprio: o zero dele é `de - dentro`, onde `dentro` é o IN_POINT que o
    `archive_windows` devolveu. É nesse relógio que a aprovação é escrita e que
    o `cut` alinha a legenda pela ficha `.origem.json`, então somar o
    deslocamento aqui é o que impede a legenda de sair de outro trecho.

    O portão da linha suspeita NÃO relaxa: `--keep` continua obrigatório. Ele
    existe porque "Em 1826" e "jokovic jokovic" foram para a tela.
    """
    import warden_style as S
    decididas = {t.strip() for t in (guardadas or [])}
    queimar, porques = {}, {}
    ouvida, ouvida_medida = None, False
    for i, ((caminho, dentro), (de, ate)) in enumerate(zip(resultados, janelas), 1):
        queimar[(de, ate)] = None
        try:
            # A LÍNGUA DO VÍDEO vai junto, e ela é a do vídeo e não a da
            # legenda. Sem ela o Whisper detecta o idioma em alguns segundos de
            # áudio e às vezes erra; com ela, ouve na língua que a fonte
            # declarou. É a regra do dono de 16/09: "se o video for ingles
            # quero legenda em ingles".
            transcricao = _media().transcribe(caminho, proposito="janela",
                                              prefer_lang=([lingua] if lingua
                                                           else None))
        except Exception as exc:
            # Uma janela que não transcreveu não derruba as outras: ela sai
            # muda, e a conta final do lote diz qual e por quê.
            porques[(de, ate)] = (f"the window could not be transcribed "
                                  f"({type(exc).__name__}: {exc}), so there is "
                                  f"nothing to burn on it")
            continue
        # A primeira janela que responder decide a língua do lote. Não é média
        # nem votação: um lote é de UM vídeo, então as janelas não podem estar
        # em línguas diferentes -- e se estiverem, é o portão de divergência do
        # `cut` que tem de falar, não uma heurística daqui.
        if not ouvida and transcricao.get("language"):
            ouvida = transcricao.get("language")
            # Repassado como veio. Se o transcritor não disse que mediu, esta
            # função não diz por ele: afirmar medição em nome de outro módulo é
            # a mesma mentira, com um endereço a mais.
            ouvida_medida = bool(transcricao.get("language_measured"))
        zero = float(de) - float(dentro)
        segmentos = []
        for seg in transcricao.get("segments") or []:
            comeco = seg.get("start")
            fim = seg.get("end")
            if comeco is None or fim is None:
                continue
            segmentos.append({"start": round(float(comeco) + zero, 2),
                              "end": round(float(fim) + zero, 2),
                              "text": seg.get("text") or ""})
        texto = _media().to_srt(segmentos)
        if not texto.strip():
            porques[(de, ate)] = ("faster-whisper heard no speech in this "
                                  "window, so there is nothing to burn on it")
            continue
        alvo = safe_out(os.path.join(out, f"lote-janela-{i:02d}.srt"),
                        "subtitles")
        try:
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write(texto)
        except OSError as exc:
            porques[(de, ate)] = (f"could not write "
                                  f"{os.path.basename(alvo)} ({exc}), so this "
                                  f"clip has no subtitle file to burn")
            continue
        dentro_da_janela = [r for r in _media()._read_srt(alvo)
                            if r["end"] > de and r["start"] < ate]
        pendentes = [r for r, _w in linhas_suspeitas(dentro_da_janela)
                     if r["text"].strip() not in decididas]
        if pendentes:
            # Mesma decisão do caminho publicado, e aqui ela pesa mais: esta
            # transcrição é NOSSA, e se uma linha suspeita calasse o clipe,
            # toda fonte sem legenda publicada entregaria clipe mudo.
            porques[(de, ate)] = _aviso_de_linha_suspeita(pendentes)
        S.write_approval(alvo, start=de, end=ate + _folga_da_frase())
        queimar[(de, ate)] = alvo
    return queimar, porques, ouvida, ouvida_medida


def _aprova_as_janelas(srt, janelas, guardadas):
    """Assina as linhas das janelas escolhidas. ({janela: ok}, [(janela, porquê)]).

    O porquê volta ATRELADO à janela, e não como uma linha solta. Ele tem de
    chegar à conta final do lote nomeando o clipe que saiu mudo -- um aviso
    solto no meio de trinta linhas de render é um aviso que ninguém repete.

    Automático porque a legenda vem PUBLICADA pelo detentor dos direitos, e é
    o serviço contratado numa campanha musical: a campanha entrega o material
    oficial e exige legenda -- na língua do material, que é a decisão do dono
    de 15/09/2026 e não uma língua fixa. Transcrever e queimar é o trabalho,
    não uma opinião. A revisão humana linha a linha existia para o que o
    Whisper inventa; ela não se aplica ao que o dono dos direitos escreveu.

    Uma linha suspeita -- número, palavra repetida, marcador `>>` de
    auto-legenda -- é AVISADA e assinada, desde 16/09/2026. Ela calava o clipe
    inteiro, e o porquê de não calar mais está em `_aviso_de_linha_suspeita`,
    com a medição: 2 clipes de 2 saíram sem uma palavra na tela, duas vezes
    seguidas, porque a legenda publicada de uma live traz `>>` em um quarto das
    cues. O aviso vai para a conta final, ao lado do mosaico que ninguém
    entrega sem abrir.

    Uma janela sem NENHUMA linha continua sem legenda -- não há o que assinar
    -- e isso continua indo para a conta final como clipe mudo.
    """
    import warden_style as S
    decididas = {t.strip() for t in (guardadas or [])}
    linhas = _media()._read_srt(srt)
    situacao, avisos = {}, []
    for de, ate in janelas:
        dentro = [r for r in linhas if r["end"] > de and r["start"] < ate]
        if not dentro:
            situacao[(de, ate)] = False
            avisos.append(((de, ate), "no caption line falls in this window, "
                                      "so it renders with no words on screen"))
            continue
        pendentes = [r for r, _w in linhas_suspeitas(dentro)
                     if r["text"].strip() not in decididas]
        if pendentes:
            # Avisa e assina. O porquê está inteiro em `_aviso_de_linha_suspeita`:
            # calar o clipe por causa de uma linha entregava zero palavra na
            # tela em 4 clipes de 4 medidos, e a regra do dono é que a legenda
            # é a única coisa pela qual vale a pena esperar.
            avisos.append(((de, ate), _aviso_de_linha_suspeita(pendentes)))
        # Assina até onde o corte PODE chegar, não até onde ele foi planejado.
        #
        # `cut` fecha sozinho a frase que o `--end` parte ao meio, andando no
        # máximo `TETO_DA_FRASE_S` para a frente. Assinar só a janela nominal
        # fazia o próprio conserto invalidar a assinatura: medido em 16/09,
        # o corte esticou 0,4s e voltou "as approved windows are 323-343s" --
        # clipe reprovado por uma linha que a ferramenta mesma foi buscar.
        # A folga é limitada pelo mesmo teto, então nada fora do alcance do
        # corte entra na assinatura.
        S.write_approval(srt, start=de, end=ate + _folga_da_frase())
        situacao[(de, ate)] = True
    return situacao, avisos


def _mosaico_do_lote(mosaicos, destino):
    """Empilha os contact sheets dos clipes num só. O caminho, ou None.

    UMA conferência visual por lote. Cada clipe continua tendo o seu -- o
    `deliver` o escreve e desde 16/09 não bloqueia por ele -- mas olhar dois
    clipes custava duas chamadas de visão de ~14s cada. Esta imagem é as duas
    numa, e não é um render novo: são os mosaicos que já foram escritos, um
    embaixo do outro.

    None quando não deu para montar, e quem chama trata None como "não há
    mosaico do lote", nunca como detalhe: os mosaicos por clipe continuam
    todos impressos pelo `deliver`.
    """
    if not mosaicos:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        abertas = []
        for nome, caminho in mosaicos:
            if caminho and os.path.isfile(caminho):
                abertas.append((nome, Image.open(caminho).convert("RGB")))
        if not abertas:
            return None
        largura = max(im.width for _n, im in abertas)
        altura = sum(im.height for _n, im in abertas)
        folha = Image.new("RGB", (largura, altura), (16, 16, 16))
        y = 0
        for _nome, im in abertas:
            folha.paste(im, (0, y))
            y += im.height
        folha.save(destino, quality=88)
        for _n, im in abertas:
            im.close()
        return destino
    except Exception:
        # Um mosaico que não montou não pode derrubar um lote que renderizou.
        return None


def _ficha_da_legenda(out, url, passado=None):
    """(srt, veio_publicada, língua da legenda, se ela foi MEDIDA, segundos,
    língua do VÍDEO).

    O quarto valor existe porque o `render` reconstrói o anúncio a partir desta
    ficha, e uma ficha que só carrega a tag faz o `render` afirmar o que o
    `prep` teve o cuidado de não afirmar. Ficha antiga, sem o campo, conta como
    NÃO medida -- não saber se foi medido não é ter medido.

    Separada do `_lote_render` porque a LÍNGUA saiu daqui e passou a ser
    necessária antes do download -- ela é metade do que os padrões anunciam --
    e ler a ficha em dois lugares seria ler duas fichas.

    O suspensório continua aqui. O diretório já é por link, então esta ficha só
    deveria ser a deste link -- mas o diretório é um ESQUEMA, e foi um esquema
    de diretório único que queimou a legenda de um link nos clipes de outro.
    Então a ficha diz de quem ela é, e o render RECUSA a que não bater,
    nomeando os dois links. Uma ficha antiga, escrita antes de esse campo
    existir, também não passa: ela não consegue provar de quem é, e queimar sem
    essa prova é a aposta que este bloco existe para não fazer -- um
    `warden lote prep` a reescreve em segundos.
    """
    if passado:
        # Passado à mão é uma decisão de quem passou, inclusive sobre a língua:
        # quem escolheu o arquivo leu o que tem dentro dele.
        return ((passado if os.path.isfile(passado) else None),
                True, None, None, None, None)
    ficha = os.path.join(out, "lote.legenda.json")
    if not os.path.isfile(ficha):
        return None, False, None, None, None, None
    try:
        with open(ficha, encoding="utf-8") as fh:
            carregado = json.load(fh)
    except (OSError, ValueError):
        return None, False, None, None, None, None
    if not isinstance(carregado, dict):
        return None, False, None, None, None, None
    if carregado.get("source_id") != _marca_da_fonte(url):
        die(f"the caption card in {ficha} is not this link's and will not be "
            f"burned: it was written for "
            f"{carregado.get('url') or '(a link it does not name)'} and this "
            f"command was asked for {url}. A caption from another video looks "
            f"finished and says things nobody said. Run `warden lote prep "
            f"{url}` to write this link's own card, or delete that file.",
            code=2)
    srt = carregado.get("srt")
    if srt and not os.path.isfile(srt):
        srt = None
    return (srt, bool(carregado.get("published")), carregado.get("language"),
            bool(carregado.get("language_measured")), carregado.get("seconds"),
            # A língua do VÍDEO, que não é a mesma coisa que a da legenda. Elas
            # só divergem quando a legenda é tradução -- e desde 16/09/2026 uma
            # tradução não conta como publicada, então a divergência agora leva
            # à transcrição da janela. É ESTA que o Whisper tem de ouvir: pedir
            # a língua da legenda ali seria transcrever um áudio em inglês
            # mandando o modelo ouvir português.
            carregado.get("source_language"))


def avisa_do_que_esta_devendo():
    """Diz, antes do primeiro render, o que já está pronto e não foi enviado.

    AVISO, e não portão. A trava que PARA é `para_por_clipe_devendo`, e ela
    fica só no `lote render`. A auditoria pediu esta decisão por escrito, e ela
    é esta: os três comandos não pedem a mesma coisa.

      `lote render`  -- "faça um LOTE deste link", sem janela nomeada. É o
                        comando que 7a chamou três vezes com o corte 01
                        devendo desde as 14:54:33 (links.json: 14:52:50,
                        14:57:42, 15:02:34), gastando download e N renders
                        para adiar a entrega que já podia acontecer. É o único
                        que PARA, e tem `--even-if-owed` para quando a pessoa
                        REALMENTE pedir outro corte.

      `cut --plan`   -- é o comando que `conta_do_lote` IMPRIME quando sobram
                        clipes de um lote (`warden cut --plan <arquivo>`).
                        Travá-lo deixaria os clipes que faltam sem nenhum
                        comando que os busque: a trava viraria um beco sem
                        saída pela porta que a própria ferramenta mandou usar.

      `cut` avulso   -- é um corte NOMEADO: alguém escreveu `--start`, `--end`
                        e `--out`. Quem digita uma janela exata já decidiu, e
                        recusar aqui é a ferramenta discutindo com uma ordem
                        explícita.

    Nos dois casos de aviso a dívida não fica órfã: as linhas `MEDIA:` devidas
    saem no bloco final, em `conta_do_lote` -> `caminhos_devidos_antes`.
    """
    devendo = entregas_pendentes()
    for row in devendo:
        print(f"# owed since before this batch, and NOT sent: "
              f"{os.path.basename(row['clip'])}. Its MEDIA: line is in the "
              f"final block at the end of this output.", file=sys.stderr)
    return devendo


def para_por_clipe_devendo(devendo, comando):
    """Imprime o que já está pronto e não foi enviado, e devolve 1.

    Por que PARA, e por que o código de saída não é 0.
    ---------------------------------------------------------------
    Medido em 7a: o `corte-27cd49161b-01.mp4` ficou pronto às 14:54:33 com
    `"sent": false` e continuava assim na coleta, nove minutos depois. Nesse
    meio tempo o agente rodou mais DOIS `lote render` (14:57:42 e 15:02:34,
    links.json), gastou cerca de 150s de CPU e cerca de 523s de laço de espera,
    e os dois voltaram reprovados. Nada em `_lote_render` lia o livro de
    entregas: `grep -n entregas_pendentes warden.py` só achava `cmd_status` e
    `cmd_delivered`, e nenhum dos dois é portão.

    Renderizar de novo com clipe pronto na mão não adia um trabalho: adia a
    ENTREGA que já podia ter acontecido, e cada render novo empurra o caminho
    do clipe pronto para mais longe na janela de contexto -- que é exatamente
    onde a poda o apagou.

    Imprimir-e-parar, e não `die`: `die` sai 2 com uma frase de erro e sem
    payload, e o que esta saída precisa CARREGAR são as linhas `MEDIA:`. E não
    sai 0 porque 0 quer dizer "fiz o que você pediu", e nada foi renderizado; 1
    é o mesmo código que `warden delivered` já usa para "alguém ainda está
    devendo". A porta de saída é `--even-if-owed`, e ela é nomeada aqui mesmo:
    sem ela a trava seria um beco sem saída no dia em que a pessoa REALMENTE
    pedir outro corte.
    """
    agora = datetime.now(timezone.utc)
    print("", file=sys.stderr)
    print(f"# {len(devendo)} clip(s) are already rendered and have NOT been "
          f"sent. Nothing was downloaded and nothing was rendered by this "
          f"command.", file=sys.stderr)
    for row in devendo:
        try:
            idade = int((agora - datetime.fromisoformat(row["at"])).total_seconds())
            quando = f"cleared {idade // 60}m{idade % 60:02d}s ago"
        except (ValueError, KeyError, TypeError):
            quando = "cleared earlier in this conversation"
        print(f"#   {os.path.basename(row['clip'])} -- {quando}",
              file=sys.stderr)
    print(f"# Send those first: that is what this turn is for. If the person "
          f"really asked for ANOTHER cut, run the same {comando} again with "
          f"--even-if-owed.", file=sys.stderr)
    bloco_da_mensagem_final(
        [(os.path.basename(r["clip"]), r["clip"]) for r in devendo],
        de_antes=len(devendo))
    return 1


def _lote_render(args):
    """Baixa as janelas, aprova a legenda delas, renderiza e entrega o lote."""
    url = args.url
    registra_link(url, "sent by the person to `warden lote render`")
    # O portão ANTES do download: um clipe pronto e não enviado é a entrega
    # deste turno, e renderizar por cima dela é o defeito de 7a. Ver
    # `para_por_clipe_devendo`.
    devendo = entregas_pendentes()
    if devendo and not getattr(args, "even_if_owed", False):
        return para_por_clipe_devendo(devendo, "`warden lote render`")
    rules = regras_de(args.campaign)
    stored = P.load(state_dir())
    entries = avaliza_o_link_de_quem_mandou(url, load_trusted())
    out = _dir_do_lote(url)
    os.makedirs(out, exist_ok=True)
    if not args.windows:
        die("lote render needs --windows: the seconds of the source you chose "
            "on `warden lote prep`, as 181-201.6,745.5-765", code=2)
    janelas = _varias_janelas(args.windows)
    # `--subtitles` aqui substitui o SRT que o `prep` escreveu, e a substituição
    # é justamente onde o erro de 15/09/2026 entrou: `lote.srt` e
    # `lote.srt.aprovado` ficam LADO A LADO no mesmo diretório do lote, com
    # nomes que só diferem no sufixo. Ver `_porque_isso_nao_e_legenda`.
    if getattr(args, "subtitles", None) is not None:
        _exige_legenda_de_verdade(args.subtitles)
    hooks = [h.strip() for h in str(args.hooks or "").split("|")]
    hooks = [h for h in hooks if h] if args.hooks else []

    # A ficha da legenda é lida ANTES de anunciar os padrões, e a ordem é o
    # ponto: é dela que sai a língua da fonte, e a língua é metade do que o
    # anúncio tem de dizer. Anunciar primeiro e descobrir o idioma depois era
    # anunciar um idioma que ninguém tinha lido.
    srt, publicada, lingua_da_fonte, lingua_medida, seg_da_ficha, lingua_do_video = (
        _ficha_da_legenda(out, url, args.subtitles))

    # A duração vem, em ordem: do `--seconds` desta linha de comando, depois do
    # número que a pessoa disse ao `prep` e que a ficha guardou, e só então do
    # padrão. O meio dessa ordem é o conserto de 15/09: sem ele, um `render`
    # sem `--seconds` caía direto na preferência guardada e um pedido de "20
    # segundos" voltava com 15.
    segundos = args.seconds if args.seconds is not None else seg_da_ficha
    padroes, linhas = _padroes_do_lote(rules, stored,
                                       args.n if args.n is not None else len(janelas),
                                       segundos, lingua=lingua_da_fonte,
                                       medida=lingua_medida)
    for linha in linhas:
        print(linha, file=sys.stderr)
    if hooks and len(hooks) < len(janelas):
        print(f"# {len(janelas)} windows and {len(hooks)} hook(s): the clips "
              f"past the last hook render with no line on top of the frame.",
              file=sys.stderr)

    # 1. Só as janelas escolhidas, numa execução do yt-dlp. Os arquivos que
    # saem daqui têm RELÓGIO PRÓPRIO -- ver `archive_window` -- e é por isso
    # que o `IN_POINT` de cada um vira o `start` do corte, e não o segundo da
    # fonte.
    try:
        resultados = _media().archive_windows(
            None, out, url, janelas, trusted=entries)
    except Exception as exc:
        die(f"could not pull the windows of {url}: {type(exc).__name__}: {exc}",
            code=1)
    _diz_as_janelas(resultados, janelas)

    # 2. A legenda das janelas, assinada sozinha quando ela é a publicada.
    queimar, porques = {}, {}
    if not padroes["captions"]:
        if srt:
            print("# captions are off for this batch, so nothing is signed or "
                  "burned.", file=sys.stderr)
    elif srt and publicada:
        situacao, avisos = _aprova_as_janelas(srt, janelas,
                                              getattr(args, "keep", None))
        for janela, ok in situacao.items():
            queimar[janela] = srt if ok else None
        for janela, aviso in avisos:
            porques[janela] = aviso
    else:
        # A fonte não publica legenda -- ficha ausente, ou ficha dizendo que o
        # texto do `prep` é transcrição nossa. Antes daqui saía o lote inteiro
        # SEM legenda, calado: `queimar` vazio, nenhum SRT passado ao `cut`, e
        # o portão de entrega dorme porque `asked_for_captions` é
        # `bool(caption_srt)`. Agora a janela é transcrita com o modelo bom e
        # essa transcrição é o que queima -- com o portão da linha suspeita
        # intacto.
        queimar, porques, ouvida, ouvida_medida = _legenda_da_janela(
            out, resultados, janelas, getattr(args, "keep", None),
            lingua=lingua_do_video or lingua_da_fonte)
        # A língua só apareceu AGORA, depois de o anúncio já ter saído dizendo
        # que ela não tinha sido lida. Refazer a conta e dizer o que mudou é a
        # única resposta honesta: calar deixaria o gancho ser escrito no escuro,
        # e é no escuro que ele sai em português sobre uma legenda em inglês.
        if ouvida and not padroes.get("language"):
            padroes, linhas = _padroes_do_lote(
                rules, stored,
                args.n if args.n is not None else len(janelas),
                args.seconds, lingua=ouvida, medida=ouvida_medida)
            print(f"# the source had no published subtitle, so the language "
                  f"came from transcribing the windows: it is "
                  f"{str(padroes.get('language') or ouvida).upper()}. Write "
                  f"the hooks in that language -- `cut` refuses a clip whose "
                  f"hook and caption disagree.", file=sys.stderr)
            print(linhas[3], file=sys.stderr)
    for janela, porque in porques.items():
        print(f"# {janela[0]:.1f}-{janela[1]:.1f}s: {porque}", file=sys.stderr)

    # 3. O plano, e o motor do lote que já existe. `lote` é orquestração: uma
    # segunda implementação de "renderiza N janelas e cobra a conta" é uma
    # segunda contagem, e a contagem é o que se está consertando.
    clips, mudos = [], []
    marca = _marca_da_fonte(url)
    for i, ((caminho, dentro), (de, ate)) in enumerate(zip(resultados, janelas), 1):
        nome = _nome_livre(f"corte-{marca}-{i:02d}.mp4")
        legenda = queimar.get((de, ate))
        # Um clipe que sai mudo num lote que pediu legenda é entrega errada, e
        # o porquê dele não pode morrer numa linha de stderr no meio do render.
        # Ele vai para a CONTA FINAL, ao lado dos clipes que falharam, porque é
        # lá que o modelo é obrigado a repetir o que aconteceu.
        if padroes["captions"] and not legenda:
            mudos.append((nome, porques.get((de, ate))
                          or f"nothing was signed for the {de:.1f}-{ate:.1f}s "
                             f"window, so no words go on screen"))
        clips.append({"out": nome, "source": caminho,
                      "start": dentro, "end": dentro + (ate - de),
                      "hook": hooks[i - 1] if i <= len(hooks) else None,
                      "subtitles": legenda,
                      "seconds": padroes["seconds"],
                      # A língua desce até o `cut`, que a repassa ao portão de
                      # divergência. Deixá-la de fora era o modelo adivinhando
                      # o idioma da legenda e o `cut` não tendo contra o que
                      # comparar o gancho.
                      "language": padroes.get("language"),
                      "_": f"window {de:.1f}-{ate:.1f}s of the source"})
    plano = {"campaign": args.campaign, "sound": padroes["sound"],
             "seconds": padroes["seconds"], "crop": args.crop,
             "language": padroes.get("language"),
             "clips": clips}

    # Mais de três: os três primeiros saem NESTE turno e o resto vai para o
    # fundo. Não é um limite de capacidade, é um limite de silêncio: um render
    # mede ~64s, e o quarto clipe empurra a primeira entrega para além de
    # quatro minutos com nada na tela. Quem espera clipe prefere três agora e
    # dois depois a cinco daqui a vinte minutos.
    #
    # E NADA ACORDA NINGUÉM, por isso nada é prometido aqui.
    #
    # O que existia até aqui era um `subprocess.Popen(start_new_session=True)`
    # e a frase "their finishing is what wakes you for them". O processo subia
    # e renderizava de verdade; o despertar não existia. Medido em 15/09/2026:
    # o que acorda o agente neste runtime é a ferramenta de terminal DELE, com
    # `background=true, notify_on_complete=true` -- é daí que saem as mensagens
    # `[IMPORTANT: Background process proc_... completed]` que abriram os 4
    # turnos com anexo de 13-15/09. O mesmo registro diz que o shell recusa
    # `nohup`/`disown`. Um processo que este arquivo solta por conta própria é
    # invisível para esse notificador: ele não tem `proc_id`, não é vigiado, e
    # ninguém lê o `lote-resto.log` em que ele despeja a saída.
    #
    # Ou seja: os clipes 4 e 5 ficavam prontos em disco e paravam ali até a
    # pessoa escrever de novo -- exatamente o buraco de 14/09, com um processo
    # solto por cima.
    #
    # Então este comando parou de soltar o processo e passou a dizer o comando
    # EXATO. Quem pode fazer o despertar acontecer é o agente, com a sua
    # própria ferramenta de fundo; e se ele não puder, o pior caso é rodar o
    # plano no turno seguinte -- que é o que já acontecia, sem ninguém saber.
    #
    # Soltar o Popen E mandar rodar o plano seria pior que os dois: dois ffmpeg
    # escrevendo o mesmo mp4, que é a corrupção que `executa_o_plano` mata com
    # `die` quando ela vem de dentro do plano.
    depois = []
    if len(clips) > LOTE_INLINE:
        resto = clips[LOTE_INLINE:]
        caminho_resto = os.path.join(out, "lote-resto.json")
        sobra = dict(plano)
        sobra["clips"] = resto
        try:
            with open(caminho_resto, "w", encoding="utf-8") as fh:
                json.dump(sobra, fh, ensure_ascii=False, indent=1)
            plano["clips"] = clips[:LOTE_INLINE]
            depois = [c["out"] for c in resto]
            print(f"# {len(clips)} clips asked for, and {LOTE_INLINE} come out "
                  f"in this turn. The other {len(resto)} are NOT rendering: "
                  f"their plan is written at {caminho_resto} and nothing has "
                  f"started it. Deliver these first {LOTE_INLINE} now instead "
                  f"of waiting for all {len(clips)}.", file=sys.stderr)
        except Exception as exc:
            # Não deu para escrever o plano: renderiza tudo aqui mesmo.
            # Entregar menos clipes do que foram pedidos porque um arquivo não
            # foi escrito seria a falta de 14/09 com outra desculpa.
            plano["clips"] = clips
            depois = []
            print(f"# could not write the plan for the rest "
                  f"({type(exc).__name__}: {exc}), so all {len(clips)} clips "
                  f"render in this turn instead. It will take longer and "
                  f"nothing is lost.", file=sys.stderr)

    argumentos = argparse.Namespace(
        plan=None, campaign=args.campaign, source=None,
        sound=padroes["sound"], seconds=padroes["seconds"], any_length=False)
    liberados, failed, asked, mosaicos = executa_o_plano(plano, argumentos)
    folha = _mosaico_do_lote(mosaicos, os.path.join(out, "lote-contato.jpg"))
    return conta_do_lote(liberados, failed, asked, sheet=folha, depois=depois,
                         sem_legenda=mudos,
                         plano_do_resto=(caminho_resto if depois else None))


def cmd_lote(args):
    """Do link aos clipes em duas saídas, em vez de sete comandos.

    `prep` é tudo o que o modelo precisa para ESCOLHER as janelas: o material,
    o texto, o digest, os sinais e os padrões que serão usados -- numa leitura
    só. `render` é tudo o que vem depois de escolhê-las: as janelas baixadas, a
    legenda assinada, os N renders, um contact sheet do lote e as linhas
    `MEDIA:` juntas no fim.

    Nenhuma das duas pergunta nada. A decisão do dono, de 15/09/2026: no máximo
    UMA mensagem com perguntas antes do primeiro clipe, e só quando o pedido
    não diz nem quantidade nem duração.
    """
    if args.action == "prep":
        return _lote_prep(args)
    if args.action == "render":
        return _lote_render(args)
    die(f"unknown lote action {args.action!r}")


# O log do gateway é o ÚNICO registro independente de que um anexo saiu.
#
# Medido em 15/09: não existe ferramenta `send_message` neste runtime, então o
# agente não recebe resultado nenhum ao entregar um arquivo. A regra "leia o
# resultado do envio" não tinha onde acontecer -- era frase, não regra.
#
# O que existe é isto: quando o gateway extrai um `MEDIA:` da mensagem FINAL do
# turno, ele escreve uma linha por anexo. E quando o envio falha, escreve outra.
# Nenhuma das duas carrega o caminho do arquivo -- só a extensão e o horário --
# então o que dá para provar é quantos anexos saíram desde que um clipe foi
# liberado, e se algum falhou. É pouco, e é MUITO mais do que a palavra do
# modelo, que é o que havia antes.
GATEWAY_LOG = os.environ.get("WARDEN_GATEWAY_LOG",
                             "/var/lib/hermes/logs/gateway.log")
_SAIU = "Sending video attachment"

# ATENÇÃO: esta string NÃO foi confirmada em log nenhum.
#
# `_SAIU` foi lido de um gateway.log de verdade. `_FALHOU` não: a auditoria
# deste projeto registra, duas vezes, que "Failed to send media" nunca foi
# observada -- nem em 15/09 nem depois. Ela entrou junto do bloco "Medido em
# 15/09" acima e herdou uma autoridade que não tem.
#
# O que isso significa, exatamente: se a frase real do gateway for outra, esta
# rede de segurança NUNCA dispara. Ela não gera alarme falso -- uma linha que
# a contenha é uma linha que está mesmo lá -- mas o silêncio dela não é prova
# de que nada falhou, e a saída de `warden delivered` não pode dizer que é.
# Por isso a mensagem que a usa fala em "linha que casa com este padrão", e
# não em "falha de envio". Quando alguém puser as mãos num log com uma falha
# de verdade, é esta constante que muda -- e aí o comentário sai.
_FALHOU = "Failed to send media"
_ANUNCIOU = re.compile(r"Delivering (\d+) non-image MEDIA")

# O state.db do Hermes é o registro do que ESTE agente escreveu, e é o que o
# log do gateway não tem: o caminho do arquivo.
#
# Medido em 15/09/2026, e é a razão de este bloco existir. O `warden delivered`
# de antes não verificava clipe nenhum: ele contava QUALQUER "Sending video
# attachment" no gateway.log desde que o clipe foi liberado. Num lote de dois,
# o clipe 1 saindo fazia o comando confirmar o clipe 2 também -- o clipe
# PERDIDO era riscado pelo anexo do outro. A conta que ele fazia respondia
# "algum anexo saiu?", e a pergunta é "ESTE arquivo saiu?".
#
# O state.db responde essa. Cada mensagem do assistente está lá com o texto e
# com o `finish_reason`, e é o `finish_reason` que decide o destino do anexo:
#   `stop`        -> mensagem FINAL do turno. O gateway lê o MEDIA:. 8 de 8.
#   `tool_calls`  -> mensagem do MEIO do turno. O gateway descarta. 0 de 5.
# Então "o caminho aparece numa mensagem com finish_reason=stop" é a única
# afirmação verificável de que aquele clipe foi anexado, e o gateway.log
# confirma que o anexo realmente subiu depois dela.
#
# O schema é INTERNO do Hermes e pode mudar a cada atualização dele. Por isso
# tudo aqui degrada para "não consegui verificar" -- nunca para uma exceção e
# nunca para um silêncio que pareça confirmação. Ler é só leitura: `mode=ro`,
# que não cria arquivo, não roda migração e não mexe no WAL de quem está
# escrevendo.
STATE_DB = os.environ.get("WARDEN_STATE_DB", "/var/lib/hermes/state.db")

# As colunas que esta verificação lê, e nada além delas. Uma a menos e o
# comando diz que o schema mudou, em vez de estourar um `sqlite3.OperationalError`
# no meio de uma entrega.
_COLUNAS_ESPERADAS = ("role", "content", "timestamp", "finish_reason")


def _carimbo_do_log(linha):
    """Segundos epoch da linha do log, ou None. Formato: 2026-09-15 03:11:40,013.

    Os MILISSEGUNDOS entram na conta, e não são detalhe: duas mensagens no
    mesmo segundo são comuns -- a pessoa cola o link logo depois do texto -- e
    truncar em segundos faz as duas terem o mesmo carimbo, o que apaga a
    segunda de qualquer comparação "chegou depois de".
    """
    try:
        base = datetime.strptime(linha[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, TypeError):
        return None
    if len(linha) > 23 and linha[19] == "," and linha[20:23].isdigit():
        base += int(linha[20:23]) / 1000.0
    return base


def envios_desde(quando):
    """O que o log do gateway registrou depois de `quando`.

    Devolve {"saiu", "falhou", "anunciados"} -- anexos de vídeo que subiram,
    falhas de envio, e quantos MEDIA o gateway disse que ia entregar
    ("Delivering N non-image MEDIA") -- ou None quando não há log para ler.

    None é diferente de zero, e a diferença é o projeto inteiro: zero é uma
    medida, "não consegui olhar" não é, e tratar os dois como a mesma coisa é
    como este projeto perdeu clipe antes.
    """
    if not os.path.isfile(GATEWAY_LOG):
        return None
    saiu, falhou, anunciados = 0, 0, 0
    try:
        with open(GATEWAY_LOG, encoding="utf-8", errors="replace") as fh:
            for linha in fh:
                anuncio = _ANUNCIOU.search(linha)
                if _SAIU not in linha and _FALHOU not in linha and not anuncio:
                    continue
                t = _carimbo_do_log(linha)
                if t is None or t < float(quando) - 2:
                    continue
                if anuncio:
                    try:
                        anunciados += int(anuncio.group(1))
                    except ValueError:
                        pass
                elif _FALHOU in linha:
                    falhou += 1
                else:
                    saiu += 1
    except OSError:
        return None
    return {"saiu": saiu, "falhou": falhou, "anunciados": anunciados}


def _carimbo_da_mensagem(valor):
    """Segundos epoch do `timestamp` de uma linha do state.db, ou None.

    O Hermes é de outra equipe e o formato desta coluna é dele: já foi visto
    como epoch (int ou float) e como texto ISO. Ler os dois é mais barato que
    depender de qual deles a próxima versão vai gravar -- e quando não é
    nenhum dos dois, isto devolve None e quem chama trata como "não deu para
    ler a hora", nunca como zero.
    """
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    return _carimbo_do_log(texto)


def mensagem_que_citou(caminho):
    """O que o state.db diz sobre a última mensagem do agente que citou `caminho`.

    Uma das cinco respostas, e nenhuma delas é um palpite:

      {"estado": "ilegivel", "porque": <frase>}  -- não deu para ler o registro
      {"estado": "ausente"}                      -- nenhuma mensagem citou o arquivo
      {"estado": "meio-do-turno", ...}           -- citou, mas com tool_calls depois
      {"estado": "final", ...}                   -- citou na mensagem final do turno

    `ilegivel` cobre tudo o que pode dar errado ao ler um banco de outra
    equipe: arquivo ausente, sqlite recusando abrir, WAL travado por quem está
    escrevendo, schema com outras colunas. Cada um traz a sua frase, porque
    "não verifiquei" sem dizer o que não deu para ler é uma frase que ninguém
    consegue consertar.
    """
    import sqlite3
    alvo = os.path.abspath(os.path.expanduser(str(caminho or "")))
    if not os.path.isfile(STATE_DB):
        return {"estado": "ilegivel",
                "porque": f"the Hermes state.db is not at {STATE_DB}, so "
                          f"nothing here can read which of your messages "
                          f"carried this path"}
    con = None
    try:
        # Somente leitura, e é uma exigência e não um detalhe: este banco é de
        # outro processo que está escrevendo nele agora. `mode=ro` não cria
        # arquivo, não roda migração e não toca no journal de quem escreve.
        con = sqlite3.connect("file:" + STATE_DB + "?mode=ro", uri=True,
                              timeout=5.0)
        colunas = {linha[1] for linha in
                   con.execute("PRAGMA table_info(messages)").fetchall()}
        if not colunas:
            return {"estado": "ilegivel",
                    "porque": f"{STATE_DB} has no `messages` table, so this is "
                              f"not the schema this check was written against "
                              f"(the Hermes schema is internal and changes with "
                              f"its updates)"}
        faltando = [c for c in _COLUNAS_ESPERADAS if c not in colunas]
        if faltando:
            return {"estado": "ilegivel",
                    "porque": f"the `messages` table in {STATE_DB} has no "
                              f"{', '.join(faltando)} column, so this check "
                              f"cannot tell a final message from a mid-turn one "
                              f"(the Hermes schema is internal and changes with "
                              f"its updates)"}
        # O LIKE filtra no sqlite e o `in` confere em Python: o LIKE é para não
        # arrastar a conversa inteira para a memória, e a conferência é porque
        # `_` e `%` são curingas do LIKE e um caminho de arquivo tem `_`.
        linhas = con.execute(
            "SELECT content, timestamp, finish_reason FROM messages "
            "WHERE role = 'assistant' AND content LIKE ? "
            "ORDER BY id DESC LIMIT 200",
            ("%MEDIA:%" + os.path.basename(alvo) + "%",)).fetchall()
    except sqlite3.OperationalError as exc:
        # "database is locked" é o WAL de quem está escrevendo, e é o caso
        # mais provável de todos: o gateway grava a conversa enquanto isto lê.
        travado = "locked" in str(exc).lower() or "busy" in str(exc).lower()
        return {"estado": "ilegivel",
                "porque": (f"{STATE_DB} is locked by whoever is writing to it "
                           f"({exc}), so this check could not read your "
                           f"messages") if travado else
                          (f"{STATE_DB} did not open for reading ({exc})")}
    except sqlite3.Error as exc:
        return {"estado": "ilegivel",
                "porque": f"{STATE_DB} is not readable as a sqlite database "
                          f"({type(exc).__name__}: {exc})"}
    except OSError as exc:
        return {"estado": "ilegivel",
                "porque": f"{STATE_DB} could not be opened "
                          f"({type(exc).__name__}: {exc})"}
    except Exception as exc:
        # A rede de segurança final. O schema do Hermes é interno: uma coluna
        # que troca de tipo, um `content` que vira BLOB, uma versão do sqlite
        # que levanta outra coisa. Nenhuma dessas pode virar um traceback no
        # meio de uma entrega -- todas viram "não consegui verificar".
        return {"estado": "ilegivel",
                "porque": f"reading {STATE_DB} failed in a way this check does "
                          f"not know ({type(exc).__name__}: {exc}). The Hermes "
                          f"schema is internal and changes with its updates"}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    # TODAS as mensagens que citam o caminho, não só a última.
    #
    # Medido em 16/09/2026: a MESMA mensagem de entrega aparece DUAS vezes no
    # state.db, 10:11:00 e 10:17:31, as duas com `finish_reason: stop` e o
    # conteúdo idêntico. O log do gateway explica -- "Normal final-send NOT
    # suppressed despite active stream consumer (...) possible duplicate send"
    # -- e a duplicata é gravada com o carimbo de quando foi regravada.
    #
    # A versão anterior devolvia a mais recente e parava. Os anexos saíram
    # depois da PRIMEIRA (10:11:01 e 10:11:05); depois da segunda não saiu
    # nada, porque não havia nada a sair. Resultado: dois clipes que o dono
    # tinha na mão voltaram como "NOT confirming (...) the gateway logged no
    # video attachment leaving after it", e a instrução ao agente era reenviar.
    # É o defeito de "abriu conversa nova reenviando os cortes de ontem",
    # pela outra ponta.
    #
    # Então a pergunta deixa de ser "qual foi a última" e passa a ser "alguma
    # delas foi seguida de anexo" -- e quem responde isso é quem lê o log, que
    # é `cmd_delivered`. Aqui saem todas, da mais nova para a mais velha.
    candidatas = []
    for content, quando, finish in linhas:
        texto = content if isinstance(content, str) else str(content or "")
        if ("MEDIA:" + alvo) not in texto and ("MEDIA:" + str(caminho)) not in texto:
            continue
        candidatas.append({
            "estado": "final" if (finish or "") == "stop" else "meio-do-turno",
            "at_ts": _carimbo_da_mensagem(quando),
            "finish_reason": finish})
    if not candidatas:
        return {"estado": "ausente"}
    # A mais nova continua sendo a resposta, para quem só quer uma.
    return dict(candidatas[0], candidatas=candidatas)


def verifica_um_envio(caminho, varredura=False):
    """Um clipe devido: o envio aconteceu? Risca o livro quando sim. -> bool.

    Extraída de `cmd_delivered` em 16/09/2026, e a extração É o conserto.
    Toda a verificação -- `mensagem_que_citou` + `envios_desde` -- morava
    DENTRO do `if args.clip:`, e a forma SEM argumento do comando só listava.
    Ninguém era obrigado a passar um caminho, e o agente não passou.

    Medido em 7a (achado 11 do ACHADOS-FASE-1): duas mensagens finais com
    `MEDIA:` -- msg 91 às 15:14:47 e msg 97 às 15:17:10 -- com o gateway
    confirmando `Delivering 2 non-image MEDIA attachment(s)` nas duas, e
    `entregas.json` com `"sent": false` nos dois clipes até o fim da coleta.
    `warden delivered` continuava respondendo `2 clip(s) rendered and cleared,
    and NOT confirmed as sent`, e a msg 97 começa com "Reenviando os dois
    cortes, porque o sistema não confirmou que chegaram". O livro não fechava,
    então o agente reenviava em todo turno, para sempre.

    E fechar o livro virou pré-requisito de outra coisa: a trava do `lote
    render` (`para_por_clipe_devendo`) recusa renderizar enquanto houver
    `"sent": false`. Com um livro que nunca fecha, essa trava deixaria de ser
    uma rede e viraria um `lote render` que não roda mais nesta conversa.

    `varredura=True` é a forma SEM argumento, e ela é mais estrita de propósito
    num ponto só: um registro ILEGÍVEL não é riscado. Ver o bloco marcado
    abaixo -- riscar o que não se conseguiu ler é uma decisão que só faz
    sentido quando alguém NOMEOU aquele arquivo; feita em varredura, ela
    esvazia o livro inteiro toda vez que o `state.db` do Hermes não estiver
    onde este processo espera, e o livro vazio é o que desliga a trava do
    pedido 4 sem dizer nada a ninguém.

    Tudo o que ela imprime vai para stderr menos a confirmação, e tudo em
    INGLÊS: quem lê é o modelo, e é exatamente aqui -- quando a entrega já
    falhou -- que uma frase em português o faz trocar de idioma com a pessoa.
    """
    nome = os.path.basename(caminho)
    registro = entregas_registro(caminho)
    if registro is None:
        return True
    citou = mensagem_que_citou(caminho)
    estado = citou.get("estado")
    if estado == "ausente":
        # (a) Ninguém escreveu esta linha em lugar nenhum. Não é um envio que
        # falhou, é um envio que nunca foi tentado.
        print(f"NOT sent: no message of yours cites that file. {nome} was "
              f"rendered and cleared, and no message of yours in this "
              f"conversation carries its MEDIA: line. The person does not "
              f"have it. Put `MEDIA:{os.path.abspath(caminho)}` in the FINAL "
              f"message of a turn.", file=sys.stderr)
        return False
    if estado == "meio-do-turno":
        # (b) O caso medido: a linha existe, o arquivo existe, e o anexo foi
        # descartado porque o turno continuou depois dela.
        ts = citou.get("at_ts")
        hora = (datetime.fromtimestamp(ts).strftime("%H:%M")
                if ts else "??:??")
        print(f"NOT sent: that MEDIA: line was written MID-TURN (message at "
              f"{hora}), and that attachment was thrown away. Write the same "
              f"MEDIA: line again in the FINAL message of a turn. The message "
              f"that carried {nome} ended with "
              f"finish_reason={citou.get('finish_reason')!r}, not 'stop': "
              f"another tool call came after it, and the gateway reads MEDIA: "
              f"only from the last message of a turn (measured 15/09: 0 of 5 "
              f"mid-turn lines arrived, 8 of 8 final ones did).",
              file=sys.stderr)
        return False
    if estado != "final":
        # Não deu para ler o registro -- `state.db` ausente, sqlite recusando
        # abrir, schema de outra equipe.
        #
        # NOMEADO: risca e diz. Riscar em silêncio seria a mentira que este
        # comando existe para não contar, e travar a entrega porque um banco de
        # outra equipe mudou seria pior que o defeito.
        #
        # EM VARREDURA: não risca. A mesma decisão, aplicada a todos os
        # pendentes de uma vez, deixa de ser "não trave o lote por um registro
        # que não é seu" e vira "esvazie o livro sempre que o `state.db` não
        # estiver legível" -- e o livro vazio desliga a trava do `lote render`
        # (`para_por_clipe_devendo`) sem dizer nada a ninguém. A varredura só
        # fecha o que ela conseguiu PROVAR que saiu.
        if varredura:
            print(f"{nome}: NOT verified and NOT crossed off -- "
                  f"{citou.get('porque')}. Name the file "
                  f"(`warden delivered {os.path.abspath(caminho)}`) if you "
                  f"want it crossed off without that reading.",
                  file=sys.stderr)
            return False
        entregas_confirma(caminho)
        print(f"{nome}: NOT verified -- {citou.get('porque')}. Crossing it "
              f"off anyway, because a batch cannot stop on a record this "
              f"command does not own. Nothing here says this clip arrived: "
              f"if the person has not said they got it, ask.",
              file=sys.stderr)
        return True
    # Cada mensagem final que citou este arquivo, da mais nova para a mais
    # velha, até uma delas ter anexo depois. A duplicata que o gateway grava é
    # idêntica e não leva anexo nenhum; a original levou. Ver o comentário em
    # `mensagem_que_citou`.
    log = None
    for tentativa in (citou.get("candidatas") or [citou]):
        if tentativa.get("estado") != "final":
            continue
        lida = envios_desde(tentativa.get("at_ts") or registro["at_ts"])
        if lida is None:
            log = None
            break
        if lida["saiu"] >= 1 and not lida["falhou"]:
            log = lida
            break
        if log is None or not log.get("saiu"):
            log = lida
    if log is None:
        # Metade lida é melhor que nenhuma, e ela é dita como metade.
        entregas_confirma(caminho)
        print(f"confirmed: {nome} -- the message carrying its MEDIA: line was "
              f"the final one of a turn, which is what attaches. NOT verified "
              f"beyond that: {GATEWAY_LOG} is not readable from here, so "
              f"nothing independent says the attachment actually left the "
              f"machine.", file=sys.stderr)
        return True
    if log["falhou"]:
        # Não afirma "o envio falhou" nem "a pessoa não tem o arquivo": o
        # padrão que casou (`_FALHOU`) nunca foi visto num log real, então ele
        # é uma pista e não um veredito. O que esta saída pode dizer com
        # segurança é que nada aqui confirma a chegada -- e reenviar custa uma
        # linha.
        print(f"NOT confirming {nome}: {log['falhou']} line(s) after the "
              f"message that carried it match the send-failure pattern this "
              f"check looks for ({_FALHOU!r}) -- a wording this project has "
              f"never seen in a real gateway log, so treat it as a lead, not "
              f"a verdict. Nothing here says the file arrived either. Send it "
              f"again, in the LAST message of your turn, and say in one line "
              f"that you are resending.", file=sys.stderr)
        return False
    if log["saiu"] < 1:
        print(f"NOT confirming {nome}: the message carrying its MEDIA: line "
              f"was a final one, but the gateway logged no video attachment "
              f"leaving after it"
              + (f" (it announced {log['anunciados']} MEDIA and sent none)"
                 if log["anunciados"] else "")
              + ". Send it again in the LAST message of a turn.",
              file=sys.stderr)
        return False
    entregas_confirma(caminho)
    print(f"confirmed: {nome} -- its MEDIA: line was in a final message"
          + (f", the gateway announced {log['anunciados']} MEDIA"
             if log["anunciados"] else "")
          + f" and {log['saiu']} video attachment(s) left after it.")
    return True


def cmd_delivered(args):
    """Risca os clipes já entregues, ou diz quem ainda falta. 0 se o livro fecha.

    Com um caminho é a confirmação daquele clipe. SEM argumento ele varre TODOS
    os pendentes e faz a MESMA leitura em cada um -- e essa varredura é o
    conserto do achado 11: antes, a forma sem argumento só listava, então o
    livro nunca fechava e o agente reenviava os mesmos clipes em todo turno
    (msg 97 de 7a: "Reenviando os dois cortes, porque o sistema não confirmou
    que chegaram"). Ver `verifica_um_envio`.

    Desde 15/09 a confirmação é uma LEITURA, não uma promessa. Antes, este
    comando acreditava no modelo: ele dizia "entreguei" e o clipe era riscado.
    Isso valia zero, porque o caso que se quer pegar é justamente aquele em que
    o modelo acha que entregou e não entregou -- foi o que aconteceu três vezes
    seguidas.

    E a versão seguinte, que lia o gateway.log, valia quase zero pelo mesmo
    motivo por outro caminho: ela contava QUALQUER anexo saído desde que o
    clipe foi liberado, sem olhar o caminho. Num lote de dois, o anexo do
    clipe 1 confirmava o clipe 2 -- o comando riscava como entregue exatamente
    o clipe que se perdeu. Por isso a pergunta é por ARQUIVO: em qual mensagem
    sua este caminho apareceu, e essa mensagem foi a FINAL do turno?
    """
    if args.clip:
        if entregas_registro(args.clip) is None:
            print(f"nothing was owing for {args.clip}. Either it was already "
                  f"confirmed, or this is not a path `warden cut` printed in "
                  f"the last {PRAZO_ENTREGA_H}h.", file=sys.stderr)
            return 1 if entregas_pendentes() else 0
        verifica_um_envio(args.clip)
    else:
        # Um por um, e sobre uma CÓPIA da lista: `verifica_um_envio` escreve no
        # mesmo livro que está sendo percorrido.
        for row in list(entregas_pendentes()):
            verifica_um_envio(row["clip"], varredura=True)
    pendentes = entregas_pendentes()
    if not pendentes:
        print("nothing is owing. Every clip cleared in this window came back "
              "confirmed.")
        return 0
    print(f"{len(pendentes)} clip(s) rendered and cleared, and NOT confirmed "
          f"as sent:", file=sys.stderr)
    for row in pendentes:
        print(f"  {row['clip']}", file=sys.stderr)
    # A frase que estava aqui -- "não termine o turno enquanto este comando
    # sair 1" -- pedia uma condição impossível, e foi ela que travou o pedido
    # de 15/09: o anexo só sai QUANDO o turno termina, então o comando não
    # pode ver o envio antes do fim do turno, e o agente não pode terminar o
    # turno antes de o comando ver. Os dois ficavam esperando um pelo outro.
    print("Each one is a file the person does not have. Put its `MEDIA:` line "
          "in the LAST message of a turn -- all of them in the SAME last "
          "message, nowhere else delivers. Run this command again at the START "
          "of your NEXT turn: it cannot confirm a send that has not happened "
          "yet.", file=sys.stderr)
    return 1


def cmd_discover(args):
    """Every listing, with what this owner said they are looking for on top.

    The filtering is the agent's, not this command's. A downloader that decides
    which campaign suits somebody is a ranking invented by a program that has
    never seen their footage, wearing the clothes of advice.
    """
    prefs = P.load(state_dir())
    wanted = {k: prefs[k] for k in P.KEYS
              if P.GROUPS[k] == "search" and k in prefs}
    if wanted:
        print("# what this owner is looking for: "
              + json.dumps(wanted, ensure_ascii=False))
    still = P.missing(prefs, "search")
    if still:
        print(f"# not answered yet, so do not filter on it: {', '.join(still)}")
    pages, failed = _media().discover()
    if not pages:
        die("no campaign directory could be read from this network", code=1)
    for name, url, text in pages:
        print(f"\n===== {name}  {url} =====\n")
        print(text)
    for name, url, why in failed:
        print(f"could not read {name} ({url}): {why}", file=sys.stderr)
    return 0


def cmd_beat(args):
    """Tempo, grid and drop of one track, so a cut can land on a bar."""
    import warden_beat
    try:
        print(json.dumps(warden_beat.analyse(args.track), indent=2, ensure_ascii=False))
    except Exception as exc:
        die(f"could not find a tempo in {os.path.basename(args.track)}: {exc}", code=1)
    return 0


def cmd_prefs(args):
    prefs = P.load(state_dir())
    if args.action == "show":
        rules = load_campaign(args.campaign) if args.campaign else None
        settings, overruled = P.effective(prefs, rules)
        print(json.dumps({"stored": prefs, "effective": settings,
                          "missing": P.missing(prefs),
                          "overruled_by_campaign": overruled},
                         indent=2, ensure_ascii=False))
        return 0
    if args.action == "ask":
        # O grupo `edit` deixou de ter perguntas em 15/09/2026, e a saída diz
        # isso em vez de listar o que "falta". Todas as preferências de edição
        # têm padrão agora -- inclusive `sound` -- então listar as não
        # respondidas era entregar ao modelo uma lista de perguntas a fazer
        # antes do primeiro clipe. Medido no pedido de 15/09: 10min34s dos
        # 26min22s até o único clipe foram perguntas, e nenhuma mudou um
        # quadro. O grupo `search` continua igual: lá a resposta muda QUAIS
        # campanhas aparecem, e adivinhar o nicho de alguém é entregar a lista
        # errada.
        if args.group == "edit":
            print("nothing to ask before a clip: every edit preference has a "
                  "default")
            return 0
        left = P.missing(prefs, args.group)
        for key in left:
            question = next(q for q in P.QUESTIONS if q[0] == key)
            answers = f"  ({', '.join(question[2])})" if question[2] else "  (free text)"
            print(f"{key}: {question[1]}{answers}")
        if not left:
            print("nothing left to ask"
                  + (f" in the {args.group} group" if args.group else ""))
        return 0
    if args.action == "set":
        if not args.key:
            die("set needs --key and --value")
        if args.key not in P.KEYS:
            die(f"unknown preference {args.key!r}. Known: {', '.join(P.KEYS)}")
        value = args.value
        if args.key in ("target_s", "batch"):
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                die(f"{args.key} is a number, got {value!r}")
        else:
            allowed = next(q for q in P.QUESTIONS if q[0] == args.key)[2]
            if allowed and value not in allowed:
                die(f"{args.key} takes one of: {', '.join(allowed)}")
        prefs[args.key] = value
        P.save(state_dir(), prefs)
        # Só `chave = valor`. O "still to ask: ..." que saía aqui era uma lista
        # de perguntas entregue ao modelo no exato momento em que ele acabava
        # de gravar uma resposta -- e ele fazia todas, uma por mensagem. Não
        # falta nada antes de um clipe: toda preferência de edição tem padrão.
        print(f"{args.key} = {value}")
        return 0
    die(f"unknown prefs action {args.action!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="warden", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("schema").set_defaults(func=cmd_schema)

    p = sub.add_parser("campaign")
    p.add_argument("action", choices=["save", "list", "show"])
    p.add_argument("which", nargs="?", help="the campaign id, for show")
    p.add_argument("--id")
    p.add_argument("--file", help="path, or - for stdin")
    p.add_argument("--json", help="the rule set inline, instead of a file or stdin")
    p.set_defaults(func=cmd_campaign)

    p = sub.add_parser("check")
    p.add_argument("video")
    # Sem campanha isto vira uma medição em vez de um veredito. Ver
    # `cmd_check`: exigir a campanha aqui deixava um link solto sem saída
    # nenhuma, porque a persona manda citar número do `warden` e a skill
    # proíbe inventar uma campanha para passar do flag.
    p.add_argument("--campaign", help="judge against this campaign's rules. "
                                      "Without it the file is only MEASURED, "
                                      "and the verdict says so")
    p.add_argument("--caption", help="path, or - for stdin")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("package")
    p.add_argument("--campaign", help="the hashtags, mentions and exact "
                                      "wording this campaign requires. Without "
                                      "it the caption is the hook and nothing "
                                      "else, and it says so")
    p.add_argument("--hook", default="")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("youtube",
                       help="connect the owner's channel, and publish to it")
    p.add_argument("action", choices=["connect", "status", "publish"])
    p.add_argument("file", nargs="?", help="the clip to publish")
    p.add_argument("--campaign", help="take the description from this campaign")
    p.add_argument("--hook", default="", help="the owner's line, first in the caption")
    p.add_argument("--title", help="the video title; at most 100 characters")
    p.add_argument("--privacy", default="private",
                   choices=["private", "unlisted", "public"],
                   help="private by default, and an unaudited project cannot "
                        "do better: until this project passes the YouTube API "
                        "Services audit, YouTube LOCKS the upload as private, "
                        "and that does not come undone in Studio or by appeal "
                        "(support.google.com/youtube/answer/7300965). Only the "
                        "audit lifts it, and Google publishes no deadline for "
                        "one.")
    p.add_argument("--wait", type=float, default=300,
                   help="seconds to wait for the phone approval (default 300)")
    p.set_defaults(func=cmd_youtube)

    # O comando que a persona já mandava usar e que não existia. Sem ele o
    # agente levava `invalid choice: 'post'` e código 2 -- que não é o estado
    # "desligado" que a persona previu, então a rota de fallback nunca era
    # acionada e ele prometia uma publicação que não acontecia.
    p = sub.add_parser("post",
                       help="publish through the audited intermediary -- the "
                            "path that does NOT get locked as private")
    p.add_argument("action", choices=["youtube", "tiktok", "instagram",
                                      "status", "connect", "setkey"])
    p.add_argument("file", nargs="?",
                   help="the clip to publish; with `status`, the request_id of "
                        "an upload already sent; with `connect`, the profile "
                        "name to connect (this machine's own by default)")
    p.add_argument("--title", help="the video title on YouTube, the caption on "
                                   "TikTok and Instagram. At most 100 "
                                   "characters for YouTube and 2200 for the "
                                   "other two, and this refuses a longer one "
                                   "rather than truncating it in silence")
    p.add_argument("--description", default="",
                   help="the YouTube description. The other two networks have "
                        "no separate description: there the caption is --title")
    p.add_argument("--also", default="",
                   help="other networks to send the SAME file to in the same "
                        "upload, comma separated (e.g. `--also tiktok`). The "
                        "file is uploaded once and the intermediary fans it out")
    p.add_argument("--privacy", default="public",
                   choices=["public", "unlisted", "private"],
                   help="public by default, and unlike `warden youtube "
                        "publish` that default actually holds: the "
                        "intermediary's app is audited, so YouTube does not "
                        "lock the upload as private. Measured public on "
                        "15/09/2026. TikTok has no `unlisted` and refuses it; "
                        "Instagram has no per-post privacy at all and refuses "
                        "anything but public")
    p.add_argument("--shorts", action="store_true",
                   help="append #Shorts to the description. A HINT only -- "
                        "YouTube decides what a Short is from the file itself")
    p.add_argument("--draft", action="store_true",
                   help="[TikTok] send to the drafts instead of publishing "
                        "(`post_mode=MEDIA_UPLOAD`). In draft mode TikTok "
                        "ignores the caption and privacy sent by the API")
    p.add_argument("--stories", action="store_true",
                   help="[Instagram] post as a Story instead of a Reel")
    # 45s e não 300s.
    #
    # Medido em 16/09/2026 num envio real: o comando ficou 5 minutos BLOQUEADO
    # e calado, e o dono, que estava olhando a conversa, não tinha como saber
    # se algo estava acontecendo. O vídeo saiu 9 minutos depois -- e os 9
    # minutos eram a fila do intermediário (`attempts: 0` em três consultas
    # seguidas), não este código. Esperar mais aqui não publica mais cedo:
    # só troca o silêncio de quem espera pelo silêncio de quem trabalha.
    #
    # Então o teto devolve rápido, com o comando de consultar de novo na mão.
    p.add_argument("--wait", type=float, default=45,
                   help="seconds to wait before handing back the request id "
                        "(default 45). The upload does NOT stop when this "
                        "expires -- it is the intermediary's queue, and "
                        "`warden post status <id>` asks again")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("tiktok",
                       help="upload a finished clip to the TikTok inbox as a draft")
    p.add_argument("file")
    p.add_argument("--campaign", help="also print the caption this campaign requires")
    p.add_argument("--hook", default="", help="the owner's line, first in the caption")
    p.add_argument("--wait", type=float, default=180,
                   help="seconds to wait for TikTok to finish processing (default 180)")
    p.set_defaults(func=cmd_tiktok)

    p = sub.add_parser("log")
    p.add_argument("--campaign", required=True)
    p.add_argument("--clip", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--url", default="")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("fetch")
    p.add_argument("url")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("trusted")
    p.add_argument("action", choices=["add", "remove", "list", "check"])
    p.add_argument("value", nargs="?", help="a channel (@handle or UC… id), a "
                   "domain, or a url to check")
    p.set_defaults(func=cmd_trusted)

    p = sub.add_parser("authorize")
    p.add_argument("url")
    # Deixou de ser obrigatória em 15/09/2026. Sem campanha não existe lista a
    # consultar, e a resposta é sim: o link veio da pessoa.
    p.add_argument("--campaign", help="check the link against THIS campaign's "
                                      "published archive. Without it the "
                                      "answer is yes, because the person sent "
                                      "the link")
    p.set_defaults(func=cmd_authorize)

    p = sub.add_parser("tracks")
    p.add_argument("action", choices=["list", "add"])
    p.add_argument("file", nargs="?")
    p.set_defaults(func=cmd_tracks)

    p = sub.add_parser("inbox")
    p.add_argument("--wait", type=float, default=None,
                   help=f"seconds to wait for a message carrying a URL "
                        f"(default {INBOX_ESPERA_S:.0f}, or WARDEN_INBOX_WAIT). "
                        f"It is deliberately LONG: the link always arrives in a "
                        f"message of its own, an instant after the one asking "
                        f"for the cuts, and this command returns the moment it "
                        f"lands -- it never sits out the window. Waiting costs "
                        f"nothing when the link comes in 2s; not waiting costs a "
                        f"whole turn on a question the person had already "
                        f"answered. Exit 0 with the link, 1 if none came, 2 if "
                        f"the log could not be read at all.")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("voz")
    p.add_argument("--since", type=float,
                   help="unix timestamp; only count from there on")
    p.set_defaults(func=cmd_voz)

    p = sub.add_parser("archive")
    p.add_argument("--campaign", help="the campaign whose archive to pull from")
    # `--link` é o nome que descreve o que isto é hoje: o endereço que a pessoa
    # mandou. `--trusted` continua funcionando porque é o que as skills e as
    # conversas antigas escrevem, e um flag que some é um comando que quebra
    # na mão de quem já sabia usá-lo. Mesmo destino, então os dois são a mesma
    # coisa e não há dois caminhos para manter.
    p.add_argument("--trusted", "--link", metavar="URL",
                   help="the link the person sent, when there is no campaign. "
                        "Sending it is the authorisation: the owner's trusted "
                        "list still vouches for links the agent goes looking "
                        "for on its own, and this one is added to it for the "
                        "pull, with a line in links.json as the trail.")
    p.add_argument("--out")
    p.add_argument("--limit", type=int)
    p.add_argument("--window",
                   help="pull ONLY this window of the source, as START-END in "
                        "seconds (181-201.6). Goes through the same archive "
                        "gate as a whole pull. Prints IN_POINT: where that "
                        "window begins inside the file it wrote.")
    p.add_argument("--windows",
                   help="every window of the batch at once, comma separated "
                        "(181-201.6,745.5-765). One yt-dlp run instead of one "
                        "per window: measured 30s against 38s for two. Same "
                        "gate, same origin card, one IN_POINT line per file.")
    p.add_argument("--url", help="which authorised archive link to take the "
                                 "window of; defaults to the first one")
    p.add_argument("--text-first", action="store_true",
                   help="pull only what gives the words -- the published "
                        "subtitle, or the audio when there is none -- and no "
                        "video. Choose the windows on that, then pull ONLY those "
                    "windows with --window. The whole file is the exception.")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("transcribe")
    p.add_argument("file")
    p.add_argument("--out")
    p.add_argument("--model")
    p.add_argument("--scan", action="store_true",
                   help="first pass: the fast model over the whole source, to "
                        "find candidate windows. Do not burn these words")
    p.add_argument("--window", help="seconds, as 120-240: transcribe only this "
                   "slice with the good model. The second pass")
    p.add_argument("--lang", help="which published subtitle to prefer when the "
                   "archive shipped more than one (pt, pt-BR, en)")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("digest")
    p.add_argument("transcript")
    p.add_argument("--window", help="seconds, as 120-240")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("signals")
    p.add_argument("transcript")
    p.add_argument("--source", help="the video/audio file, to add reaction "
                   "(loudness) peaks to the text signals")
    p.set_defaults(func=cmd_signals)

    p = sub.add_parser("cut")
    # Tudo opcional quando vem um --plan: o plano carrega a fonte, a campanha e
    # as N janelas. `warden cut --plan lote.json` é a forma de um lote, e o
    # tamanho da lista de clipes é o número pedido, que é o que passa a ser
    # cobrado no fim.
    p.add_argument("source", nargs="?")
    p.add_argument("--plan", help="a batch of N windows as JSON; see the skill")
    p.add_argument("--campaign")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--seconds", type=float,
                   help="the duration the PERSON asked for, in seconds. The cut "
                        "is moved to it from the same start, and the delivery "
                        "gate rejects a file that misses it without a campaign "
                        "rule to blame. Pass it whenever someone said a number.")
    p.add_argument("--any-length", action="store_true",
                   help="nobody named a duration, so the window decides. Required "
                        "when --seconds is absent: a cut with no duration "
                        "decision does not render.")
    p.add_argument("--out")
    p.add_argument("--subtitles")
    p.add_argument("--hook")
    p.add_argument("--shots",
                   help="an EDIT: the shots to splice, comma separated, on the "
                        "file's own clock like --start (2.0-3.9,7.2-8.1). Each "
                        "one's length "
                        "is snapped to a whole number of beats of --track, so "
                        "the scene changes land on the music. Two or more.")
    p.add_argument("--track", help="audio file to cut to, for an edit")
    p.add_argument("--track-start", type=float,
                   help="where the track enters; its drop by default")
    p.add_argument("--sound", choices=["platform", "embedded"],
                   help="overrides the stored preference for this one render")
    p.add_argument("--no-motion", dest="motion", action="store_false",
                   help="render with no scale move. The default is a light zoom, "
                        "because a cut that never changes scale reads as raw footage")
    p.add_argument("--no-cover-footer", dest="cover_footer",
                   action="store_false", default=None,
                   help="do not cover text the footage already burns along the "
                        "bottom. Use it when that text is another clipper's "
                        "watermark, which campaigns forbid covering -- and then "
                        "do not pass --subtitles either")
    p.add_argument("--crop", help="which side of a wider source to keep: "
                   "left, center, right, auto, or a percentage. A side or auto "
                   "follows the detected face; a percentage is exact. Centre by "
                   "default WHEN this image has a face detector -- without one "
                   "this flag is required, because the centre is a guess and a "
                   "guess ships the subject's face sliced at the edge")
    p.set_defaults(func=cmd_cut)

    # O caminho do link até os clipes, em duas chamadas em vez de sete. Ver o
    # comentário de `cmd_lote`: a medição de 15/09 é de 35 chamadas do modelo
    # para dois clipes, e a meta é ~4.
    p = sub.add_parser("lote",
                       help="link -> clips in two steps: `prep` gathers "
                            "everything needed to pick the windows, `render` "
                            "does the rest")
    p.add_argument("action", choices=["prep", "render"])
    p.add_argument("url")
    p.add_argument("--campaign", help="as regras dessa campanha; sem ela o "
                                      "lote roda igual, com tudo marcado como "
                                      "não conferido")
    p.add_argument("--n", type=int, help="quantos clipes. Sem isto, o padrão "
                                         "do dono, que é 2")
    p.add_argument("--seconds", type=float,
                   help="a duração que a PESSOA pediu. Sem isto, o padrão do "
                        "dono, que é 20s, dentro dos limites da campanha")
    p.add_argument("--windows", help="render: as janelas escolhidas na fonte, "
                                     "como 181-201.6,745.5-765")
    p.add_argument("--hooks", help="render: os ganchos, um por janela, "
                                   "separados por |. NA LÍNGUA DA FONTE, que "
                                   "o `prep` imprime como LANG: -- `cut` "
                                   "recusa um clipe cujo gancho e legenda "
                                   "estejam em línguas diferentes")
    p.add_argument("--subtitles", help="render: um SRT já lido e aprovado, em "
                                       "vez do que o `prep` escreveu")
    p.add_argument("--keep", action="append", metavar="LINE",
                   help="render: uma linha suspeita repetida de volta, exata, "
                        "querendo dizer que você já a leu. Desde 16/09 a "
                        "legenda queima de qualquer jeito: isto só cala o "
                        "aviso dela")
    p.add_argument("--crop", help="render: qual lado de uma fonte mais larga "
                                  "fica, como em `warden cut --crop`")
    p.add_argument("--even-if-owed", action="store_true",
                   help="render: corta mesmo havendo clipe pronto que ninguém "
                        "enviou. Sem isto o render PARA e imprime as linhas "
                        "MEDIA: do que já existe -- em 16/09 um corte pronto "
                        "às 14:54 nunca saiu porque dois renders novos "
                        "passaram por cima dele. Use quando a pessoa pediu "
                        "OUTRO corte de verdade")
    p.set_defaults(func=cmd_lote)

    p = sub.add_parser("style")
    p.add_argument("action", choices=["extract", "check"])
    p.add_argument("files", nargs="+", help="clips, or a folder of them")
    p.add_argument("--consolidate", action="store_true",
                   help="write the ranges to SPECS/estilo-aprovado-scenepack.json")
    p.add_argument("--out", help="where to write the consolidated spec")
    p.add_argument("--spec", help="the spec to check against")
    p.set_defaults(func=cmd_style)

    p = sub.add_parser("captions")
    p.add_argument("action", choices=["review"])
    p.add_argument("srt")
    p.add_argument("--start", type=float, help="the cut's start, in source seconds")
    p.add_argument("--end", type=float, help="the cut's end, in source seconds")
    p.add_argument("--approve", action="store_true",
                   help="sign these words off for burning; without it, cut "
                        "renders the clip with no caption rather than a wrong one")
    p.add_argument("--keep", action="append", metavar="LINE",
                   help="a suspect line, repeated back exactly, meaning you "
                        "read it and it is right. Once per line. Without it a "
                        "suspect line cannot be signed.")
    p.set_defaults(func=cmd_captions)

    p = sub.add_parser("beat")
    p.add_argument("track")
    p.set_defaults(func=cmd_beat)

    sub.add_parser("discover").set_defaults(func=cmd_discover)

    p = sub.add_parser("prefs")
    p.add_argument("action", choices=["show", "ask", "set"])
    p.add_argument("--key")
    p.add_argument("--value")
    p.add_argument("--campaign", help="show what this campaign overrules")
    p.add_argument("--group", choices=["search", "edit"],
                   help="ask only the questions for finding campaigns, or only "
                        "the ones for making clips")
    p.set_defaults(func=cmd_prefs)

    sub.add_parser("status").set_defaults(func=cmd_status)

    # O contrato de quantidade só vale se alguém puder PERGUNTAR se ele foi
    # cumprido. Sem argumento este comando é a pergunta e sai 1 enquanto
    # faltar clipe; com um caminho é a confirmação de um envio que já voltou.
    p = sub.add_parser("delivered",
                       help="confirm a clip was sent, or ask which are still owed")
    p.add_argument("clip", nargs="?",
                   help="the path `warden cut` printed, confirmed AFTER the "
                        "attachment left the machine. Omit to ask what is "
                        "still owing; exits 1 while anything is.")
    p.set_defaults(func=cmd_delivered)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("warden: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        # Nenhum traceback chega ao agente, e o código é 2, nunca 1.
        #
        # Medido em 15/09/2026: um `campaigns/x.json` corrompido fazia
        # `warden status` -- o PRIMEIRO comando que um estranho roda -- morrer
        # com `json.decoder.JSONDecodeError` e sair 1. E 1 é o código
        # documentado de "este clipe NÃO pode ser postado", então um defeito de
        # arquivo lia como veredito sobre o trabalho. 2 é erro de comando.
        die(f"{type(exc).__name__}: {exc}", code=2)


if __name__ == "__main__":
    raise SystemExit(main())
