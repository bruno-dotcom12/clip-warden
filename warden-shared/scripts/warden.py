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
    with open(path) as fh:
        return json.load(fh)


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
    def run(args):
        return subprocess.run(["ffprobe", "-v", "error", *args, path],
                              capture_output=True, text=True).stdout.strip()
    video = run(["-select_streams", "v:0", "-show_entries",
                 "stream=width,height,r_frame_rate,codec_name",
                 "-of", "default=nw=1"])
    fields = dict(line.split("=", 1) for line in video.splitlines() if "=" in line)
    duration = run(["-show_entries", "format=duration", "-of", "default=nw=1:nk=1"])
    audio = run(["-select_streams", "a:0", "-show_entries", "stream=codec_name",
                 "-of", "csv=p=0"])
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
    lines = [f"{rules.get('name', rules.get('id'))}  |  "
             f"{media.get('width')}x{media.get('height')}, "
             f"{(media.get('duration_s') or 0):.1f}s, {media.get('size_mb')} MB", ""]
    order = {REPROVA: 0, ATENCAO: 1, OK: 2}
    label = {REPROVA: "REJECT ", ATENCAO: "CHECK  ", OK: "ok     "}
    for level, message in sorted(findings, key=lambda f: order[f[0]]):
        lines.append(f"  {label[level]} {message}")
    lines.append("")
    if verdict(findings) == REPROVA:
        lines.append("Do not post this one. Fix what is marked REJECT and run it again.")
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
    with open(path) as fh:
        return json.load(fh)


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


def entregas_pendentes():
    """Os clipes liberados que ninguém confirmou ter enviado, ainda de pé.

    Um arquivo apagado sai da conta: o que se cobra é entrega de clipe que
    existe. E a janela de seis horas existe para o portão valer dentro de uma
    conversa sem virar um bloqueio permanente na primeira vez que alguém fechar
    o terminal no meio de um lote.
    """
    return [r for r in entregas_all()
            if not r.get("sent") and _recente(r) and os.path.isfile(r.get("clip", ""))]


def entregas_registra(clip):
    """Anota um clipe como devendo envio. Idempotente por caminho."""
    clip = os.path.abspath(clip)
    rows = [r for r in entregas_all() if _recente(r) and r.get("clip") != clip]
    rows.append({"clip": clip,
                 "at": datetime.now(timezone.utc).isoformat(),
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
    if not os.path.exists(args.video):
        die(f"no such clip: {args.video}")
    rules = load_campaign(args.campaign)
    media = probe(args.video)
    caption = read_text(args.caption)
    findings = check(rules, media, caption, ledger_for(args.campaign))
    if args.json:
        print(json.dumps({"verdict": verdict(findings), "media": media,
                          "findings": [{"level": l, "message": m} for l, m in findings]},
                         indent=2, ensure_ascii=False))
    else:
        print(render(findings, rules, media))
    return 1 if verdict(findings) == REPROVA else 0


def cmd_package(args):
    """The caption, assembled from the campaign's own requirements.

    Not a creative act and not meant to be one: the hook is the owner's, and
    everything after it is what the brief demands, in the brief's own spelling.
    """
    rules = load_campaign(args.campaign)
    parts = [args.hook.strip()] if args.hook else []
    parts += [t for t in R.get(rules, "caption.required_text", [])]
    tail = " ".join(R.get(rules, "caption.required_mentions", [])
                    + R.get(rules, "caption.required_hashtags", []))
    if tail:
        parts.append(tail)
    caption = "\n".join(p for p in parts if p)
    max_len = R.get(rules, "caption.max_len")
    print(caption)
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
    """(mensagens do agente, mensagens da pessoa) do banco de conversas.

    Leitura pura, em modo somente-leitura, e num banco que pertence ao runtime
    e não a este comando. Um banco ausente não é erro: é "não dá para medir
    daqui", e dizer isso é melhor que devolver zero como se fosse uma medida.
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
        linhas = con.execute(
            "select timestamp, role, coalesce(content, '') from messages "
            "where role in ('user', 'assistant') and timestamp >= ? "
            "order by timestamp", (corte,)).fetchall()
    finally:
        con.close()
    return linhas


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
    """
    espera = max(1.0, float(getattr(args, "wait", None) or 20))
    inicio = time.time()
    # A régua é o ÚLTIMO carimbo que já estava no log, não a hora atual: a
    # mensagem que disparou este turno já está lá, e ela não conta como nova.
    ja = _chegadas(0)
    if ja is None:
        die(f"{GATEWAY_LOG} is not readable from here, so there is no way to "
            f"see a message arriving. Ask for the link.", code=2)
    marca = max([t for t, _ in ja], default=0.0)
    while True:
        for quando, texto in (_chegadas(marca) or []):
            achados = _URL.findall(texto)
            if not achados:
                continue
            link = achados[-1]
            # O log corta a mensagem por volta de 80 caracteres. Um link
            # cortado é pior que link nenhum: ele baixa outra coisa, ou nada,
            # e o erro aparece três passos adiante.
            if texto.endswith(link) and len(texto) >= 79:
                print(f"a link arrived but the log truncated it ({link!r}). "
                      f"Ask the person to send just the URL.", file=sys.stderr)
                return 1
            print(link)
            print(f"arrived {quando - inicio:.0f}s into the wait", file=sys.stderr)
            return 0
        if time.time() - inicio >= espera:
            break
        time.sleep(0.5)
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
    """
    try:
        linhas = _conversas(getattr(args, "since", None))
    except RuntimeError as exc:
        die(str(exc), code=2)
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
        print(f"{quando}  {n:2d} message(s), {com_arquivo} with a file  "
              f"[{veredito}]  {t['pediu'][:56]}")
        if n > VOZ_TETO:
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


def cmd_authorize(args):
    """Decide whether a link is in the campaign's archive, and say why.

    The one honest answer to 'can I clip this video?' when the archive is a set
    of playlists: expand them and look, rather than claim membership you cannot
    see. Exit 0 authorised, exit 1 not -- so a skill can gate on it.
    """
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
    """O mesmo caminho barato, com o portão da lista do dono em vez do acervo."""
    entries = load_trusted()
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
    """Puxa o material, pelo caminho barato, com um dos dois portões.

    Os dois portões são a campanha e a lista de fontes do dono. Um dos dois tem
    de existir: sem nenhum, não há quem responda se este link pode ser cortado,
    e a resposta nunca é o palpite de quem está lendo o link.
    """
    confiavel = getattr(args, "trusted", None)
    if confiavel and args.campaign:
        die("--campaign and --trusted are the two gates and you pick one: the "
            "campaign's archive, or the owner's trusted list.", code=2)
    if not confiavel and not args.campaign:
        die("this needs a gate: `--campaign <id>` to pull that campaign's "
            "archive, or `--trusted <url>` to pull one link the owner vouched "
            "for. There is no third way to decide a link may be cut.", code=2)

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


def deliver(result, rules, campaign, ledger):
    """Um render pronto vira entrega, ou não vira e diz por quê.

    Três portões, nesta ordem, porque é a ordem em que uma entrega se perde: as
    regras da campanha (que é aritmética), o contact sheet (que é a única coisa
    que olha a imagem) e só então a linha que anexa o arquivo. O `MEDIA:` não é
    impresso sem o mosaico: enquanto ele não existir, ninguém olhou este clipe, e
    foi assim que um arquivo com dez defeitos visíveis foi relatado como aprovado.

    Devolve 0 quando a entrega saiu, 1 quando não saiu.
    """
    for note in result["notes"]:
        print(f"  note: {note}", file=sys.stderr)
    media = probe(result["out"])
    findings = check(rules, media, None, ledger)
    blocking = [m for level, m in findings if level == REPROVA]
    print(result["out"])
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
    if (result.get("asked_for_captions") and not ja_vem_legendado
            and not (result.get("style") or {}).get("caption")):
        porques = [n for n in result.get("notes") or []
                   if "not burning captions" in n or "nothing was burned" in n]
        breaches.append(
            "captions were asked for and none were burned"
            + (": " + porques[0] if porques else "")
            + ". A podcast cut with no speech on screen is not the clip that "
              "was asked for -- fix what the note says, or cut without "
              "--subtitles on purpose.")
    if breaches:
        print("this render breaks the style rules, so it is not delivered:",
              file=sys.stderr)
        for message in breaches:
            print(f"  REJECT {message}", file=sys.stderr)
        print("  re-cut it: a shorter hook, a different window, or no "
              "--subtitles.", file=sys.stderr)
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
    sheet = result.get("sheet")
    if not sheet or not os.path.isfile(sheet):
        print("no contact sheet was written for this render, so nothing has "
              "looked at it. Not delivering: a clip nobody saw is how a file "
              "with a cropped hook and a six-line caption got reported as "
              "passing.", file=sys.stderr)
        return 1
    # Impresso antes do MEDIA: de propósito. A ordem na tela é a ordem do
    # trabalho -- abrir a imagem, conferir a lista, e só então entregar. O
    # número de itens sai da própria lista: escrito à mão, ele descolou dela
    # assim que a lista cresceu, e "confira os cinco" sobre oito itens é um
    # convite a parar no quinto.
    import warden_style as S
    print(f"SHEET:{sheet}")
    print(f"open that image and check all {len(S.CHECKLIST)} before you send "
          f"the clip:", file=sys.stderr)
    for item in S.CHECKLIST:
        print(f"  [ ] {item}", file=sys.stderr)
    print("  any one of them failing rejects the clip, even with every check "
          "green.", file=sys.stderr)
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
          "the prose arrives and the file does not. Start the next render in "
          "the background first, so its finishing wakes you for the next clip.",
          file=sys.stderr)
    # A dívida, em disco, no instante em que o caminho fica disponível. Daqui
    # para a frente existe uma pergunta com resposta -- `warden delivered` --
    # em vez de só a memória do turno, que foi o que falhou em 14/09.
    entregas_registra(result["out"])
    print(f"  and when the send comes back, run `warden delivered "
          f"{result['out']}` so the count stops owing this one.",
          file=sys.stderr)
    print(f"MEDIA:{result['out']}")
    return 0


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


def cmd_cut(args):
    if getattr(args, "plan", None):
        return cmd_cut_plan(args)
    # Com `--shots` o começo e o fim vêm dos planos: o primeiro plano é o
    # começo e a soma das durações é o comprimento. Exigir `--start/--end`
    # junto seria pedir a mesma coisa duas vezes, e em dois relógios que podem
    # discordar.
    planos_pedidos = _planos_pedidos(args)
    obrigatorios = ("source", "campaign", "out") if planos_pedidos else (
        "source", "campaign", "start", "end", "out")
    missing = [name for name in obrigatorios
               if getattr(args, name, None) is None]
    if missing:
        die("cut needs " + ", ".join("--" + m if m != "source" else "a source"
                                     for m in missing)
            + " -- or a --plan that carries them for a whole batch")
    if planos_pedidos:
        args.start = min(a for a, _b in planos_pedidos)
        args.end = max(b for _a, b in planos_pedidos)
    rules = load_campaign(args.campaign)
    # Sound is decided before a frame is written, and never by default. If the
    # campaign settles it, use that; otherwise it is the owner's stored choice;
    # and if neither has said, the render stops rather than shipping a silent
    # clip nobody asked to be silent.
    sound = args.sound or P.effective(P.load(state_dir()), rules)[0].get("sound")
    if not sound:
        die("no sound decision: this campaign does not settle it and the owner "
            "has not chosen. Ask whether the clip keeps the original sound or "
            "ships silent for the platform to add its own, then "
            "`warden prefs set --key sound --value embedded|platform` (or pass "
            "--sound). A clip must not ship silent by accident.", code=1)
    # A duração é decidida antes de um quadro ser escrito, como o som. O
    # mecanismo do `--seconds` já existia e já funcionava -- quando ele chega, a
    # janela é movida para o número e o portão de entrega cobra. O que não
    # existia era nada que NOTASSE a falta dele. No vlog de 14/09 pediram 20
    # segundos e saíram 24,5s e 15,4s: 22% acima e 23% abaixo, com as janelas
    # coladas nas fronteiras da transcrição e o número da pessoa nunca tendo
    # virado parâmetro. Nenhum portão disparou, porque sem `asked_s` não há o
    # que comparar. Agora a ausência é uma decisão que alguém toma em voz alta.
    if args.seconds is None and not args.any_length:
        die("no duration decision: nobody said how long this clip should be. If "
            "the person named a number, pass --seconds <n> and the cut lands on "
            "it. If nobody named one, pass --any-length and the window decides. "
            "A clip that came out 22% longer than the number someone said is a "
            "clip they have to ask for again.", code=1)
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
    return deliver(result, rules, args.campaign, ledger_for(args.campaign))


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
                print("  (gravando na spec de SCENEPACK. Se o que você acabou de "
                      "medir são cortes de fala, passe --out "
                      "SPECS/estilo-aprovado-fala.json: as duas faixas não são a "
                      "mesma e sobrescrever uma com a outra apaga a medição.)",
                      file=sys.stderr)
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
                die(f"a spec ainda está com o nome antigo "
                    f"({os.path.basename(specs_path_antigo())}). Ela mede vinte "
                    f"scenepacks e nenhum corte de fala, então passou a se "
                    f"chamar {os.path.basename(spec_file)}. Renomeie o arquivo, "
                    f"ou aponte --spec para ele.", code=2)
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
            print(f"  AVISO  este clipe tem legenda de fala queimada, e "
                  f"{os.path.basename(spec_file)} foi medida sobre "
                  f"{len(spec.get('measured_from') or [])} scenepacks, nenhum "
                  f"deles um corte de fala. As faixas abaixo descrevem outro "
                  f"formato: leia como contexto, não como aprovação. O corpus "
                  f"de fala é o Bloco E e ainda não existe.", file=sys.stderr)
        print(json.dumps(medida, ensure_ascii=False, indent=1))
        piores = [m for lv, m in achados if lv == "REJECT"]
        rotulo = {"REJECT": "REJECT", "ok": "ok    ", "note": "      "}
        for lv, m in achados:
            print(f"  {rotulo[lv]} {m}", file=sys.stderr)
        if piores:
            print(f"\nthis render is outside the approved range on "
                  f"{len(piores)} count(s). Look at the contact sheet: a number "
                  "out of band is usually visible.", file=sys.stderr)
            return 1
        print("\ninside the approved range on every enforced metric. That is not "
              "the same as good -- it is the same as not obviously broken. The "
              "contact sheet is still the gate.", file=sys.stderr)
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
        texto = r.get("text") or ""
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
    clips = plan["clips"]
    if not clips:
        die(f"{args.plan} asks for no clips")

    cid = args.campaign or plan.get("campaign")
    if not cid:
        die("the plan has no 'campaign' and --campaign was not passed")
    rules = load_campaign(cid)
    source = args.source or plan.get("source")
    if not source:
        die("the plan has no 'source' and none was passed")
    sound = (args.sound or plan.get("sound")
             or P.effective(P.load(state_dir()), rules)[0].get("sound"))
    if not sound:
        die("no sound decision for this batch: neither the campaign, the plan "
            "nor the owner has chosen. Ask, then set it in the plan's 'sound' "
            "or with `warden prefs set --key sound --value embedded|platform`.",
            code=1)

    # O mesmo portão do corte avulso, na porta do lote. `seconds` no topo do
    # plano vale para todos; um clipe pode trazer o seu.
    pedido_topo = plan.get("seconds", getattr(args, "seconds", None))
    sem_pedido = [c for c in clips
                  if not isinstance(c, dict) or c.get("seconds") is None]
    if pedido_topo is None and sem_pedido and not args.any_length:
        die(f"no duration decision for this batch: {len(sem_pedido)} of "
            f"{len(clips)} clips say nothing about how long they should be. Put "
            f"the number the person said in the plan's 'seconds' (or on each "
            f"clip), or pass --any-length if nobody named one. On 14/09 two "
            f"clips of \"20 segundos\" came out 24.5s and 15.4s because the "
            f"number never reached the renderer.", code=1)

    asked = len(clips)
    # "delivered" era a palavra errada e ela contradizia o conserto do P0: este
    # laço RENDERIZA e libera; quem entrega é a MENSAGEM FINAL de um turno, que
    # este processo não escreve. Dizer "2 of 2 delivered" aqui é a ferramenta
    # afirmando uma entrega que não aconteceu -- exatamente o defeito de 14/09,
    # dito pela outra ponta.
    print(f"# {asked} clips asked for. This command RENDERS and clears them; it "
          f"does not deliver. Each one reaches the person only when a turn ENDS "
          f"with its MEDIA: line in the last message, one clip per turn.",
          file=sys.stderr)
    liberados, failed = [], []
    ledger = ledger_for(cid)

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
                liberados.append(name)
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
        piscina.shutdown(wait=True)

    print(f"\n# {len(liberados)} of {asked} cleared for delivery. NONE of them "
          f"has been sent by this command.", file=sys.stderr)
    for name, why in failed:
        print(f"#   missing: {name} -- {why}", file=sys.stderr)
    if liberados:
        # Esta linha sai SEMPRE que algo passou, inclusive num lote curto. Sem
        # isso, um lote em que o clipe 1 explode e os clipes 2 e 3 ficam
        # prontos não tinha uma linha mandando enviá-los: eles apareciam só
        # numa contagem, e clipe pronto que ninguém manda é clipe perdido --
        # que é o defeito de 14/09 chegando por outra porta.
        print(f"# deliver the {len(liberados)} that cleared, one per turn, "
              f"MEDIA: in the last message of each, and confirm every one with "
              f"`warden delivered`: " + ", ".join(liberados), file=sys.stderr)
    if len(liberados) < asked:
        print(f"# and then say this batch is NOT done: "
              f"{asked - len(liberados)} of the {asked} clips asked for did not even "
              f"clear. Name which failed and why. Do not report the batch as "
              f"finished, and do not quietly deliver fewer than were asked for.",
              file=sys.stderr)
        return 1
    print("# the batch is done when those sends came back, not when this line "
          "printed. `warden delivered` answers whether any are still owed, and "
          "it exits 1 while one is. Run it before you end your turn.",
          file=sys.stderr)
    return 0


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
_FALHOU = "Failed to send media"


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
    """(anexos que saíram, falhas de envio) no log do gateway, depois de `quando`.

    `None, None` quando não há log para ler -- que é diferente de zero. Zero é
    uma medida; "não consegui olhar" não é, e tratar os dois como a mesma coisa
    é como este projeto perdeu clipe antes.
    """
    if not os.path.isfile(GATEWAY_LOG):
        return None, None
    saiu, falhou = 0, 0
    try:
        with open(GATEWAY_LOG, encoding="utf-8", errors="replace") as fh:
            for linha in fh:
                if _SAIU not in linha and _FALHOU not in linha:
                    continue
                t = _carimbo_do_log(linha)
                if t is None or t < float(quando) - 2:
                    continue
                if _FALHOU in linha:
                    falhou += 1
                else:
                    saiu += 1
    except OSError:
        return None, None
    return saiu, falhou


def cmd_delivered(args):
    """Risca um clipe da lista de envios devidos, ou diz quem ainda falta.

    Sem argumento é a pergunta -- "falta alguém?" -- e ela sai 1 enquanto
    faltar, para que "entreguei tudo" deixe de ser uma coisa que só o modelo
    sabe.

    Com um caminho é a confirmação, e desde 15/09 ela é uma LEITURA, não uma
    promessa. Antes, este comando acreditava no modelo: ele dizia "entreguei" e
    o clipe era riscado. Isso valia zero, porque o caso que se quer pegar é
    justamente aquele em que o modelo acha que entregou e não entregou -- foi o
    que aconteceu três vezes seguidas. Agora o comando vai ao log do gateway e
    pergunta se um anexo realmente saiu depois que aquele clipe foi liberado.
    Sem essa linha, ele RECUSA riscar.
    """
    if args.clip:
        registro = entregas_registro(args.clip)
        if registro is None:
            print(f"nothing was owing for {args.clip}. Either it was already "
                  f"confirmed, or this is not a path `warden cut` printed in "
                  f"the last {PRAZO_ENTREGA_H}h.", file=sys.stderr)
            return 1 if entregas_pendentes() else 0

        saiu, falhou = envios_desde(registro["at_ts"])
        if saiu is None:
            # Nada para ler não é permissão para acreditar. Mas travar a
            # entrega inteira porque o log mudou de lugar seria pior que o
            # defeito: aqui se risca e se diz, em voz alta, o que não foi
            # possível verificar.
            entregas_confirma(args.clip)
            print(f"confirmed: {os.path.basename(args.clip)} -- but NOT "
                  f"verified: {GATEWAY_LOG} is not readable from here, so "
                  f"nothing independent says the attachment went out.",
                  file=sys.stderr)
        elif falhou:
            print(f"NOT confirming {os.path.basename(args.clip)}: the gateway "
                  f"logged {falhou} media send failure(s) since this clip was "
                  f"cleared. The person does not have it. Send it again, in "
                  f"the LAST message of your turn, and say in one line that "
                  f"you are resending.", file=sys.stderr)
        elif saiu < 1:
            print(f"NOT confirming {os.path.basename(args.clip)}: no attachment "
                  f"has left this machine since it was cleared. A `MEDIA:` "
                  f"line written anywhere but the LAST message of a turn "
                  f"attaches nothing, silently -- that is how two clips were "
                  f"lost. Put it in the final message and end the turn.",
                  file=sys.stderr)
        else:
            entregas_confirma(args.clip)
            print(f"confirmed: {os.path.basename(args.clip)} "
                  f"({saiu} attachment(s) left the machine since it cleared)")
        pendentes = entregas_pendentes()
    else:
        pendentes = entregas_pendentes()
    if not pendentes:
        print("nothing is owing. Every clip cleared in this window came back "
              "confirmed.")
        return 0
    print(f"{len(pendentes)} clip(s) rendered and cleared, and NOT confirmed "
          f"as sent:", file=sys.stderr)
    for row in pendentes:
        print(f"  {row['clip']}", file=sys.stderr)
    print("Each one is a file the person does not have. Put its `MEDIA:` line "
          "in the LAST message of a turn -- nowhere else delivers -- then run "
          "this again. Do not end your turn while this command exits 1.",
          file=sys.stderr)
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
        left = P.missing(prefs, P.GROUPS[args.key])
        print(f"{args.key} = {value}"
              + (f", still to ask: {', '.join(left)}" if left else ", nothing left to ask"))
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
    p.add_argument("--campaign", required=True)
    p.add_argument("--caption", help="path, or - for stdin")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("package")
    p.add_argument("--campaign", required=True)
    p.add_argument("--hook", default="")
    p.set_defaults(func=cmd_package)

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
    p.add_argument("--campaign", required=True)
    p.set_defaults(func=cmd_authorize)

    p = sub.add_parser("tracks")
    p.add_argument("action", choices=["list", "add"])
    p.add_argument("file", nargs="?")
    p.set_defaults(func=cmd_tracks)

    p = sub.add_parser("inbox")
    p.add_argument("--wait", type=float, default=20.0,
                   help="seconds to wait for a message carrying a URL "
                        "(default 20). Exit 0 with the link, 1 if none came.")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("voz")
    p.add_argument("--since", type=float,
                   help="unix timestamp; only count from there on")
    p.set_defaults(func=cmd_voz)

    p = sub.add_parser("archive")
    p.add_argument("--campaign", help="the campaign whose archive to pull from")
    p.add_argument("--trusted", metavar="URL",
                   help="a single link from the owner's trusted list, when "
                        "there is no campaign. Same gate, different list: "
                        "`warden trusted check` decides, and an unvouched "
                        "source is refused before a byte comes down.")
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
