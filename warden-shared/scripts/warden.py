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


def resolve_track(nome):
    """Um nome curto vira o caminho da trilha guardada; um caminho fica como está."""
    if not nome:
        return nome
    aberto = os.path.expanduser(str(nome))
    if os.path.isfile(aberto):
        return aberto
    guardada = os.path.join(tracks_dir(), os.path.basename(aberto))
    if os.path.isfile(guardada):
        return guardada
    # Nem caminho nem trilha guardada. Dizer as duas coisas que faltam, em vez
    # de deixar o ffmpeg falhar falando de um arquivo que a pessoa não nomeou.
    tem = sorted(os.listdir(tracks_dir()))
    die(f"there is no track called {nome!r}: not a path on disk, and not in "
        f"{tracks_dir()}" + (f" (which holds: {', '.join(tem)})" if tem else
                             " (which is empty)") + ". Send the audio file and "
        f"`warden tracks add <file>` keeps it.", code=1)


def cmd_tracks(args):
    alvo = tracks_dir()
    if args.action == "add":
        origem = os.path.expanduser(args.file or "")
        if not os.path.isfile(origem):
            die(f"there is no file at {origem}", code=1)
        if os.path.splitext(origem)[1].lower() not in (
                ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"):
            die(f"{os.path.basename(origem)} is not an audio file", code=1)
        destino = os.path.join(alvo, os.path.basename(origem))
        shutil.copy2(origem, destino)
        print(destino)
        print(f"kept. `warden cut --track {os.path.basename(destino)}` finds it "
              f"by name from now on -- do not ask for this file again.",
              file=sys.stderr)
        return 0
    nomes = sorted(f for f in os.listdir(alvo)
                   if os.path.splitext(f)[1].lower() in
                   (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"))
    for nome in nomes:
        print(os.path.join(alvo, nome))
    if not nomes:
        print("no track has been kept yet. This agent ships none and downloads "
              "none: ask the owner for a file, or point them at "
              "studio.youtube.com > Áudio, whose library is free to use in "
              "videos and is downloaded under their own account.",
              file=sys.stderr)
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
    print("deliver this file NOW, with a tool call, before you cut the next one:",
          file=sys.stderr)
    print(f'  send_message(target="plow_chat", message="<caption>\\n\\n'
          f'MEDIA:{result["out"]}")', file=sys.stderr)
    print("  then READ the result. Writing the MEDIA: line into your narration "
          "between tool calls attaches nothing: only the last message of a turn "
          "is ever sent. Do not say the batch is ready until every send came "
          "back successful.", file=sys.stderr)
    # A dívida, em disco, no instante em que o caminho fica disponível. Daqui
    # para a frente existe uma pergunta com resposta -- `warden delivered` --
    # em vez de só a memória do turno, que foi o que falhou em 14/09.
    entregas_registra(result["out"])
    print(f"  and when the send comes back, run `warden delivered "
          f"{result['out']}` so the count stops owing this one.",
          file=sys.stderr)
    print(f"MEDIA:{result['out']}")
    return 0


def cmd_cut(args):
    if getattr(args, "plan", None):
        return cmd_cut_plan(args)
    missing = [name for name in ("source", "campaign", "start", "end", "out")
               if getattr(args, name, None) is None]
    if missing:
        die("cut needs " + ", ".join("--" + m if m != "source" else "a source"
                                     for m in missing)
            + " -- or a --plan that carries them for a whole batch")
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
                              asked_s=args.seconds)
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
    # laço RENDERIZA e libera; quem entrega é a chamada de `send_message`, que
    # este processo não faz. Dizer "2 of 2 delivered" aqui é a ferramenta
    # afirmando uma entrega que não aconteceu -- exatamente o defeito de 14/09,
    # dito pela outra ponta.
    print(f"# {asked} clips asked for. This command RENDERS and clears them; it "
          f"does not deliver. Send each one with send_message as it clears, and "
          f"read the result.", file=sys.stderr)
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
        print(f"# send the {len(liberados)} that cleared, one send_message "
              f"each, and read every result: "
              + ", ".join(liberados), file=sys.stderr)
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


def cmd_delivered(args):
    """Risca um clipe da lista de envios devidos, ou diz quem ainda falta.

    Sem argumento é a pergunta -- "falta alguém?" -- e ela sai 1 enquanto
    faltar, para que "entreguei tudo" deixe de ser uma coisa que só o modelo
    sabe. Com um caminho é a confirmação, feita DEPOIS que o `send_message`
    voltou: confirmar antes de ler o resultado é escrever no papel que o
    pacote chegou enquanto ele ainda está na esteira.
    """
    if args.clip:
        achou, pendentes = entregas_confirma(args.clip)
        if not achou:
            print(f"nothing was owing for {args.clip}. Either it was already "
                  f"confirmed, or this is not a path `warden cut` printed in "
                  f"the last {PRAZO_ENTREGA_H}h.", file=sys.stderr)
        else:
            print(f"confirmed: {os.path.basename(args.clip)}")
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
    print("Each one is a file the person does not have. Send it with "
          "send_message, read the result, then run this again. Do not end your "
          "turn while this command exits 1.", file=sys.stderr)
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
                        "send_message result came back. Omit to ask what is "
                        "still owing; exits 1 while anything is.")
    p.set_defaults(func=cmd_delivered)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
