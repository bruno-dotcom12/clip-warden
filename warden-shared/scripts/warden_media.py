#!/usr/bin/env python3
"""From the campaign's own archive to a clip that already obeys the campaign.

The order here is not an implementation detail, it is the point. Footage comes
only from the links the brief publishes, the cut is chosen on text before any
video is opened, and the render takes its numbers from the rule set rather than
from a house default. A pipeline that picks its own footage or its own duration
produces a file that looks finished and gets the submission thrown out.

  warden fetch <url>                       a page as text, for reading a brief
  warden archive --campaign <id>           pull the authorised footage
  warden transcribe <file>                 words with timing, subs if published
  warden digest --source <file>            the transcript a model can afford
  warden cut <file> --start --end          one clip, inside the campaign's rules
"""
import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warden_rules as R
import warden_style as S

TIMEOUT_DOWNLOAD = 900
TIMEOUT_RENDER = 900
TIMEOUT_FETCH = 60
MAX_DIRECT_BYTES = 4 * 1024 * 1024 * 1024      # a source file nobody asked for

# The only two schemes an archive link may use.
#
# Every url here was typed by a stranger into a campaign brief and read out of a
# web page by a model. file:// would copy the host's own files into the archive,
# http to a link-local address is a request to a cloud metadata service, and a
# bare word becomes an option the moment it reaches a command line.
ALLOWED_SCHEMES = ("http", "https")


def _host_is_public(host):
    """False for a name that resolves to this machine or its private network.

    A brief is a stranger's text. `http://169.254.169.254/` is the cloud
    metadata service, `http://127.0.0.1/` and `http://10.x` are whatever else
    runs on the host, and a name under the attacker's own DNS can point at any
    of them. So the host is resolved and every address it resolves to is
    checked; one private answer is enough to refuse. A determined DNS-rebind
    could still differ between this check and urllib's own resolve on connect --
    that is out of a brief's reach and noted rather than closed here.
    """
    if not host:
        return False
    host = host.strip("[]")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # A name that does not resolve is not this machine's secret to leak;
        # let the request proceed and fail as an ordinary unreachable link.
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def safe_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise RuntimeError(
            f"refusing {url!r}: an archive link has to be http or https. "
            "Anything else in a brief is not a link, it is an instruction.")
    if not parsed.netloc:
        raise RuntimeError(f"refusing {url!r}: no host in that link")
    if not _host_is_public(parsed.hostname):
        raise RuntimeError(
            f"refusing {url!r}: that points inside this machine or its private "
            "network, not at published footage. A brief cannot send this agent "
            "to read its own host.")
    return str(url)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect the way the first URL was checked.

    urllib already refuses a redirect that changes scheme to file://, but it
    follows one to http://169.254.169.254 without a word. safe_url is the same
    gate the first hop passed, so a redirect that lands on a private host or a
    non-http scheme is refused with the same message rather than followed.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# One opener for both readers, so the redirect guard cannot be forgotten at a
# call site. Built once; urllib openers are thread-safe for our use.
_OPENER = urllib.request.build_opener(_GuardedRedirect())


def have(binary):
    return shutil.which(binary) is not None


def _ytdlp():
    """How to invoke yt-dlp so it works whether or not the venv bin is on PATH.

    A skill command runs under this interpreter, but a subprocess inherits the
    supervision tree's PATH, which need not hold the venv's bin. Calling it as a
    module of this interpreter sidesteps that, and falls back to the binary for a
    dev machine that installed yt-dlp on its own.
    """
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        return ["yt-dlp"]


def run(args, timeout, label):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Raised as our own error on purpose: every caller catches RuntimeError,
        # and a timeout that escapes as itself reaches the owner as a traceback.
        raise RuntimeError(f"{label} gave up after {timeout}s")
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError(f"{label} failed:\n  " + "\n  ".join(tail))
    return done.stdout


# ---------------------------------------------------------------- reading

def fetch_text(url, limit=200_000):
    """A web page as readable text.

    The brief is usually a page, and the model that fills the rule set has to
    read it. Scripts and styles go, tags go, entities are decoded; what is left
    is what a person would have read. Truncated, because a rule set is extracted
    from the top of a brief and an unbounded page is an unbounded prompt.
    """
    url = safe_url(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": "clip-warden/1.0 (+https://github.com/plow-pbc/plow-agents)"})
    with _OPENER.open(request, timeout=60) as response:
        raw = response.read(limit * 4)
        # O charset que a resposta declara, não um palpite. Uma página latin-1
        # decodificada como utf-8 vira mojibake, e daqui saem hashtag
        # obrigatória e termo banido: uma banida corrompida nunca casa, e a
        # palavra proibida passa na verificação.
        charset = response.headers.get_content_charset()
    if not charset:
        head = raw[:4096].decode("ascii", errors="ignore").lower()
        m = re.search(r'charset=["\']?([\w-]+)', head)
        charset = m.group(1) if m else "utf-8"
    try:
        body = raw.decode(charset, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n\s*", "\n\n", body).strip()
    return body[:limit]


# ---------------------------------------------------------------- finding

# Where open campaigns are listed in public. Fetched as text and handed to the
# model to read, rather than parsed here: these are marketing pages that get
# redesigned, and a scraper that silently stops matching is worse than no
# scraper, because it reports "no campaigns" instead of "I could not read this".
#
# Editable without touching the image: WARDEN_DIRECTORIES takes a comma
# separated list and replaces this one.
DIRECTORIES = [
    ("clipmap", "https://clipmap.gg/"),
    ("whop", "https://whop.com/discover/content-rewards/"),
    ("clipradar", "https://clipradar.co/"),
    ("realoficial", "https://realoficial.com.br/"),
]


def directories():
    custom = os.environ.get("WARDEN_DIRECTORIES")
    if not custom:
        return DIRECTORIES
    out = []
    for item in custom.split(","):
        item = item.strip()
        if item:
            out.append((urlparse(item).netloc or item, item))
    return out


def discover(limit_chars=12_000):
    """Every listing page, as text, with whatever failed named out loud.

    The agent reads these and tells the owner what is open and what it pays.
    Nothing here decides which campaign is good: that depends on what the owner
    can actually make, and a ranking invented by a downloader would be noise
    wearing the clothes of advice.
    """
    pages, failed = [], []
    for name, url in directories():
        try:
            pages.append((name, url, fetch_text(url, limit=limit_chars)))
        except Exception as exc:
            failed.append((name, url, f"{type(exc).__name__}"))
    return pages, failed


# ---------------------------------------------------------------- authorising

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(url):
    """The YouTube video id in a link, or None. Handles the forms a brief uses.

    watch?v=, youtu.be/, /shorts/, /embed/, and a bare 11-character id. The id
    is what a playlist lists and what a watch link carries, so it is the thing
    two links are the same video by.
    """
    raw = str(url or "").strip()
    if _YT_ID.match(raw):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        cand = parsed.path.lstrip("/").split("/")[0]
        return cand if _YT_ID.match(cand) else None
    if "youtube" in host:
        from urllib.parse import parse_qs
        v = parse_qs(parsed.query or "").get("v", [None])[0]
        if v and _YT_ID.match(v):
            return v
        for seg in ("shorts/", "embed/", "live/"):
            if seg in parsed.path:
                cand = parsed.path.split(seg, 1)[1].split("/")[0]
                return cand if _YT_ID.match(cand) else None
    return None


def _is_playlist(url):
    parsed = urlparse(str(url or ""))
    q = parsed.query or ""
    return (parsed.path.rstrip("/").endswith("/playlist")
            or ("list=" in q and "v=" not in q))


def playlist_video_ids(url):
    """Every video id in a playlist, as a set, read without downloading a byte.

    yt-dlp's flat listing is what makes 'is this video in the authorised
    playlist?' a question the tool answers instead of the model guessing.
    """
    out = run(_ytdlp() + ["--flat-playlist", "--no-warnings",
               "--print", "%(id)s", "--", safe_url(url)],
              TIMEOUT_DOWNLOAD, "yt-dlp playlist listing")
    return {line.strip() for line in out.splitlines() if _YT_ID.match(line.strip())}


def video_title(url):
    """The title of a video, read off the link, or None if it will not give one.

    So the agent can name what it is asking about: 'the video "..." is outside
    the archive -- cut it anyway?' is a question the owner can answer; 'that link'
    is not.
    """
    try:
        out = run(_ytdlp() + ["--no-warnings", "--no-playlist", "--playlist-items",
                              "1", "--print", "%(title)s", "--", safe_url(url)],
                  TIMEOUT_DOWNLOAD, "yt-dlp title lookup")
    except Exception:
        return None
    line = (out.strip().splitlines() or [""])[0].strip()
    return line or None


def authorize(rules, url):
    """Is this link in the campaign's archive? A yes or no, the reason, the title.

    A clipper's whole risk is footage the brief did not publish, so whether a
    link is authorised must be decided, not reasoned: a playlist is expanded and
    the id looked for, a direct link is matched by id. Returns (ok, reason,
    title) -- the title so a caller can name the video it is asking about. A
    playlist that cannot be listed is reported as unknown, never as authorised --
    a maybe is a no when someone's unpaid work is on the line.
    """
    url = safe_url(url)
    title = video_title(url)
    urls = R.get(rules, "sources.archive_urls", [])
    if not urls:
        return False, "this campaign publishes no archive, so nothing is authorised", title
    vid = video_id(url)
    unlisted = []
    for allowed in urls:
        if _is_playlist(allowed):
            try:
                ids = playlist_video_ids(allowed)
            except Exception as exc:
                unlisted.append(f"{allowed} ({type(exc).__name__})")
                continue
            if vid and vid in ids:
                return True, f"in the authorised playlist {allowed}", title
        else:
            other = video_id(allowed)
            if vid and other and vid == other:
                return True, f"the authorised link {allowed}", title
            if str(allowed).strip() == url:
                return True, f"the authorised link {allowed}", title
    if unlisted:
        return False, ("not found in the archive, and these playlists could not "
                       "be read to be sure: " + "; ".join(unlisted)), title
    return False, "not in any authorised playlist or direct link of this campaign", title


# ---------------------------------------------------------------- trusted sources

def _norm_host(host):
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in ("youtu.be", "m.youtube.com", "music.youtube.com"):
        host = "youtube.com"
    return host


def _is_channel_entry(entry):
    e = str(entry or "").strip()
    return (e.startswith("@") or e.startswith("UC")
            or "/@" in e or "/channel/" in e or "/c/" in e or "/user/" in e)


def _channel_facts(url):
    """The channel a video belongs to, read without downloading it.

    yt-dlp prints the uploader handle and the channel id, which is what a
    trusted-channel entry is matched against -- so 'is this from a channel I
    trust?' is answered, not assumed.
    """
    out = run(_ytdlp() + ["--no-warnings", "--no-playlist", "--playlist-items", "1",
               "--print", "%(channel_id)s\t%(uploader_id)s\t%(channel_url)s\t%(uploader_url)s",
               "--", safe_url(url)],
              TIMEOUT_DOWNLOAD, "yt-dlp channel lookup")
    line = (out.strip().splitlines() or [""])[0]
    return [p.strip().lower() for p in line.split("\t") if p.strip() and p.strip() != "NA"]


def trusted_check(url, entries):
    """Is this link from a source the owner trusts? A yes or no with the reason.

    A domain entry matches the link's host with no network. A channel entry --
    an @handle or a UC… id -- is matched against the video's real channel, read
    off the link. Empty list means nothing is trusted yet, which is a no.
    """
    url = safe_url(url)
    entries = [str(e).strip() for e in (entries or []) if str(e).strip()]
    if not entries:
        return False, "no trusted sources are set; add one with `warden trusted add`"
    host = _norm_host(urlparse(url).netloc)
    for entry in entries:
        if _is_channel_entry(entry):
            continue
        dom = _norm_host(entry)
        if host == dom or host.endswith("." + dom):
            return True, f"from the trusted domain {entry}"
    channels = [e for e in entries if _is_channel_entry(e)]
    if channels and "youtube" in host:
        try:
            facts = _channel_facts(url)
        except Exception as exc:
            return False, ("could not read the video's channel to check it against "
                           f"your trusted list: {type(exc).__name__}")
        for entry in channels:
            token = entry.lower().rstrip("/").split("/")[-1]  # @handle or UC… id
            if token and any(token == f or token == "@" + f or "@" + token == f
                             or token in f.split("/") for f in facts):
                return True, f"from the trusted channel {entry}"
    return False, "not from any source in your trusted list"


# ---------------------------------------------------------------- footage

def archive(rules, out_dir, limit=None):
    """Download what the brief authorised, and refuse to improvise.

    An empty archive list is not a reason to go looking. A clip built from
    footage the campaign did not publish is rejected on submission, after the
    views, which is the exact loss this agent exists to prevent.
    """
    urls = R.get(rules, "sources.archive_urls", [])
    if not urls:
        raise RuntimeError(
            "this campaign's rule set publishes no archive link, so there is no "
            "authorised footage to pull. Add the links the brief gives under "
            "sources.archive_urls, or send the footage yourself.")
    os.makedirs(out_dir, exist_ok=True)
    got, failed = [], []
    for url in urls[: limit or len(urls)]:
        try:
            got.append(_download_one(url, out_dir))
        except Exception as exc:                       # one bad link, not a dead run
            failed.append((url, str(exc).splitlines()[0]))
    return got, failed


def _download_one(url, out_dir):
    url = safe_url(url)
    parsed = urlparse(url)
    direct = os.path.splitext(parsed.path)[1].lower() in (
        ".mp4", ".mov", ".m4v", ".webm", ".mkv")
    if direct:
        # The name is ours, never theirs: a remote basename lands in an ffmpeg
        # filter argument further down the pipeline, and a quote in it is enough
        # to leave the filter and name another file.
        name = "footage-%s%s" % (hashlib.sha256(url.encode()).hexdigest()[:12],
                                 os.path.splitext(parsed.path)[1].lower() or ".mp4")
        target = os.path.join(out_dir, name)
        written = 0
        with _OPENER.open(url, timeout=TIMEOUT_FETCH) as response, \
                open(target, "wb") as fh:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DIRECT_BYTES:
                    fh.close()
                    os.remove(target)
                    raise RuntimeError(
                        f"{url} is over {MAX_DIRECT_BYTES // (1024**3)} GB; "
                        "that is not a clip source, and it would fill the host's disk")
                fh.write(chunk)
        return target
    if "drive.google.com" in parsed.netloc:
        if not have("gdown"):
            raise RuntimeError("this is a Google Drive link and gdown is not installed")
        before = set(os.listdir(out_dir))
        run(["gdown", "--fuzzy", "-O", out_dir + os.sep, "--", url],
            TIMEOUT_DOWNLOAD, "gdown")
        new = sorted(set(os.listdir(out_dir)) - before)
        if not new:
            raise RuntimeError("gdown wrote nothing, the folder may need permission")
        # gdown writes the name the remote chose -- the one download path where
        # the filename comes from the far side rather than from us. Renamed to a
        # name of ours before anything downstream touches it, for the same reason
        # the direct path never trusts the remote basename: it ends up as an
        # ffmpeg input argument. And its size is checked here, because gdown has
        # no --max-filesize and would otherwise be the one path with no size cap.
        got = os.path.join(out_dir, new[0])
        if os.path.getsize(got) > MAX_DIRECT_BYTES:
            os.remove(got)
            raise RuntimeError(
                f"{url} is over {MAX_DIRECT_BYTES // (1024**3)} GB; that is not "
                "a clip source, and it would fill the host's disk")
        ours = os.path.join(out_dir, "footage-%s%s" % (
            hashlib.sha256(url.encode()).hexdigest()[:12],
            os.path.splitext(new[0])[1].lower() or ".mp4"))
        os.replace(got, ours)
        return ours
    if not have("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed, so this link cannot be pulled")
    # A playlist link is a playlist, and --no-playlist on one is undefined: it is
    # what left a stray intermediate file behind and made the archive step look
    # broken. So the two cases are told apart -- a single video keeps
    # --no-playlist, a playlist link takes exactly its first item -- and both are
    # bounded per file by --max-filesize, which is the size cap the direct path
    # has and this path did not, on the one path a campaign link most often uses.
    is_playlist = (parsed.path.rstrip("/").endswith("/playlist")
                   or ("list=" in (parsed.query or "") and "v=" not in (parsed.query or "")))
    playlist_args = (["--yes-playlist", "--playlist-items", "1"]
                     if is_playlist else ["--no-playlist"])
    # --restrict-filenames, and a name we chose: the remote title is attacker
    # text and it ends up inside an ffmpeg filter string.
    stem = "source-" + hashlib.sha256(url.encode()).hexdigest()[:12]
    template = os.path.join(out_dir, stem + ".%(ext)s")
    run(["yt-dlp", *playlist_args, "--restrict-filenames",
         "--max-filesize", str(MAX_DIRECT_BYTES),
         "--write-auto-subs", "--write-subs",
         "--sub-langs", "pt,pt-BR,en", "--convert-subs", "srt",
         "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
         "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD, "yt-dlp")
    videos = sorted(f for f in os.listdir(out_dir)
                    if f.startswith(stem)
                    and os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm"))
    if not videos:
        raise RuntimeError("yt-dlp returned no video file for that link")
    # The merged output is stem.mp4; prefer it over any intermediate a partial
    # merge left with the same prefix, and fall back to a stable sorted choice
    # rather than to whatever os.listdir happened to return first.
    merged = stem + ".mp4"
    chosen = merged if merged in videos else videos[0]
    return os.path.join(out_dir, chosen)


# ---------------------------------------------------------------- words

# Measured on this base image, emulated amd64 on Apple Silicon, 12 Sep 2026:
# `small` transcribes at 0.62x realtime, so 2m18s of source took 1m25s. Fine for
# a campaign archive, which is clips. An hour-long podcast at that rate is 37
# minutes of silence in a chat, which reads as a dead agent.
SMALL_CEILING_S = 900


def pick_model(duration_s):
    """The model this source can afford.

    An override always wins: someone who set WARDEN_WHISPER has a reason. With
    no override, short sources get `small`, which is the better text, and long
    ones get `base`, which is roughly three times faster and still good enough
    to choose a moment from. The trade is deliberate and it is stated to the
    owner rather than hidden, because the captions are burned from this text.
    """
    override = os.environ.get("WARDEN_WHISPER")
    if override:
        return override, "set by WARDEN_WHISPER"
    if duration_s and duration_s > SMALL_CEILING_S:
        return "base", (f"source is {duration_s / 60:.0f} minutes, so the faster "
                        "model, to keep this under ten minutes")
    return "small", "short enough for the better model"


def duration_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def _dimensions(path):
    """(width, height) of the first video stream, as integers."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


# Tamanho aproximado de cada modelo já convertido para int8, em MB. Serve só
# para dizer "falta X" em vez de "aguarde"; não é usado em decisão nenhuma.
MODEL_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3100}


def model_home():
    return os.environ.get("HF_HOME", "/var/lib/hermes/models")


def _model_bytes(size):
    """Quantos bytes do modelo `size` já estão em disco."""
    home = model_home()
    if not os.path.isdir(home):
        return 0
    alvo = f"faster-whisper-{size}"
    total = 0
    for raiz, dirs, arqs in os.walk(home):
        if alvo not in raiz:
            continue
        for a in arqs:
            try:
                total += os.path.getsize(os.path.join(raiz, a))
            except OSError:
                pass
    return total


def model_status(size=None):
    """O que o baixador de modelos está fazendo, em números.

    Devolve {size: {"state": ..., "mb": n, "want_mb": n, "pct": n}}. `state` é
    `ready`, `fetching`, `failed` ou `absent`.

    Existe porque "ainda baixando" e "nunca baixou" eram a mesma linha no
    `warden status`, e porque o primeiro `warden cut` de uma instalação nova cai
    justo nessa janela: o status fica verde, o dono pede um corte, e o whisper
    começa um download de cinco minutos sem dizer nada. Um silêncio de cinco
    minutos num chat lê como agente morto.
    """
    marcado = {}
    caminho = os.path.join(model_home(), "fetch-state")
    try:
        with open(caminho, encoding="utf-8") as fh:
            for linha in fh:
                if "=" in linha:
                    k, v = linha.strip().split("=", 1)
                    marcado[k] = v          # a última linha de cada chave vence
    except OSError:
        pass
    out = {}
    for nome in ([size] if size else ["small", "base"]):
        mb = _model_bytes(nome) / 1_000_000
        quer = MODEL_MB.get(nome, 500)
        estado = marcado.get(nome)
        if mb >= quer * 0.95:
            estado = "ready"
        elif estado not in ("fetching", "failed"):
            estado = "fetching" if mb > 0 else "absent"
        out[nome] = {"state": estado, "mb": round(mb), "want_mb": quer,
                     "pct": min(99, int(mb * 100 / quer)) if estado != "ready" else 100}
    return out


def model_wait_note(size):
    """A frase que o `cut` diz em vez de travar em silêncio, ou None se pronto."""
    info = model_status(size).get(size, {})
    if info.get("state") == "ready":
        return None
    falta = max(0, info.get("want_mb", 0) - info.get("mb", 0))
    if info.get("state") == "fetching":
        return (f"the {size} transcription model is still downloading: "
                f"{info['mb']} of {info['want_mb']} MB ({info['pct']}%), "
                f"{falta} MB to go. It runs in the background at boot. Wait and "
                f"try again, or transcribe with a model that is already here "
                f"(`warden transcribe <file> --model tiny`), or take the wait "
                f"on purpose with WARDEN_WAIT_FOR_MODEL=1.")
    if info.get("state") == "failed":
        return (f"the {size} model failed to download at boot. This request "
                f"would fetch {info['want_mb']} MB now, which is a long silence "
                f"in a chat -- say so before you wait.")
    return (f"the {size} transcription model is not on this machine yet "
            f"({info.get('want_mb', 0)} MB). It downloads in the background "
            f"after install; this request would fetch it now.")


def transcribe(path, model_size=None, window=None, prefer_lang=None,
               progress=None):
    """Words with timing. A published subtitle beats a transcription.

    When the archive shipped subtitles, using them is not a shortcut, it is the
    better text: it is what the rights holder wrote, and it costs nothing.

    `window` is (start, end) in source seconds: only that slice is transcribed,
    and the timings come back on the SOURCE's clock so the rest of the pipeline
    does not have to know. That is the second pass of the two-pass plan -- a
    23-minute source scanned with `tiny` to find the candidates, then the good
    model on the two minutes that actually become clips, instead of 23 minutes
    of the good model to use forty seconds of it.
    """
    say = progress or (lambda line: print(line, file=sys.stderr, flush=True))
    if window is None:
        sidecar, lang = _subtitle_beside(path, prefer=prefer_lang)
        if sidecar:
            return {"source": f"published subtitles ({lang or 'no language tag'})",
                    "path": sidecar, "language": lang,
                    "segments": _read_srt(sidecar)}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "no subtitles beside this file and faster-whisper is not installed, "
            "so there is no text to choose a moment from")
    seconds = duration_of(path)

    audio, offset, span = path, 0.0, seconds
    clean_up = None
    if window:
        lo, hi = float(window[0]), float(window[1])
        offset, span = lo, max(0.1, hi - lo)
        # Só o trecho, como wav de 16k mono: é o que o whisper quer e é uma
        # fração do arquivo. Extrair custa segundos; transcrever o resto custa
        # minutos.
        import tempfile as _tf
        fd, audio = _tf.mkstemp(suffix=".wav")
        os.close(fd)
        clean_up = audio
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{lo:.3f}", "-i", path,
             "-t", f"{span:.3f}", "-vn", "-ac", "1", "-ar", "16000", audio],
            TIMEOUT_RENDER, "ffmpeg (window for transcription)")

    if model_size:
        size, why = model_size, "asked for"
    elif window:
        # A janela é curta por construção, então o modelo bom cabe nela sempre.
        size, why = "small", f"only the {span / 60:.1f} minutes of this window"
    else:
        size, why = pick_model(seconds)

    try:
        # O modelo pode não estar aqui ainda: numa instalação nova o baixador de
        # fundo ainda está correndo quando o `warden status` já ficou verde.
        # Antes disto, o WhisperModel() simplesmente começava um download de
        # cinco minutos sem uma linha na tela.
        espera = model_wait_note(size)
        if espera:
            if os.environ.get("WARDEN_WAIT_FOR_MODEL") == "1":
                say(f"warden: {espera}")
            else:
                raise RuntimeError(espera)
        say(f"transcribing {span / 60:.1f} minutes with faster-whisper {size} "
            f"({why})...")
        model = WhisperModel(size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(audio, vad_filter=True,
                                           word_timestamps=True)
        rows = []
        # Dez minutos de silêncio num chat lê como agente morto. O whisper
        # devolve um gerador, então dá para contar o que já saiu enquanto sai --
        # uma linha por minuto de áudio processado, nem mais nem menos.
        mark = 0.0
        for s in segments:
            rows.append({"start": round(s.start + offset, 2),
                         "end": round(s.end + offset, 2),
                         "text": s.text.strip()})
            if s.end - mark >= 60:
                mark = s.end
                say(f"  {mark / 60:.0f} of {span / 60:.0f} minutes, "
                    f"{len(rows)} segments so far")
        say(f"  done: {len(rows)} segments")
    finally:
        if clean_up:
            try:
                os.remove(clean_up)
            except OSError:
                pass
    return {"source": f"faster-whisper {size} ({why})", "path": None,
            "segments": rows, "duration_s": seconds,
            "window": list(window) if window else None}


def scan(path, progress=None):
    """A passada barata sobre a fonte inteira, só para localizar candidatos.

    `tiny` erra palavra e não presta para queimar -- e não é para isso. É para
    responder "onde neste podcast de 23 minutos vale a pena olhar?", e para essa
    pergunta ele serve, a uma fração do custo. As janelas escolhidas voltam pelo
    `transcribe(window=...)` com o modelo bom.
    """
    return transcribe(path, model_size="tiny", progress=progress)


# A ordem em que uma legenda publicada é preferida. `archive` baixa com
# `--sub-langs pt,pt-BR,en`, então os dois arquivos existem lado a lado.
SUBTITLE_LANGS = ("pt-br", "pt_br", "pt-BR", "pt", "en")


def _subtitle_beside(path, prefer=None):
    """A legenda publicada ao lado do vídeo, na língua certa, ou None.

    Aqui estava um defeito silencioso e caro. A busca era `sorted(...)` e ficava
    com o PRIMEIRO arquivo em ordem alfabética: com `source-abc.en.srt` e
    `source-abc.pt.srt` no mesmo diretório -- que é o par que `archive` baixa --
    `.en` vem antes de `.pt` e o inglês ganhava sempre. É a explicação mais
    provável da legenda em inglês queimada num clipe cujo hook estava em
    português, e é um conserto de dez linhas.

    Devolve (caminho, língua) para que quem chama possa dizer qual escolheu.
    """
    stem = os.path.splitext(path)[0]
    folder = os.path.dirname(path) or "."
    base = os.path.basename(stem)
    found = []
    for name in sorted(os.listdir(folder)):
        if not name.startswith(base):
            continue
        if not name.lower().endswith((".srt", ".vtt")):
            continue
        # `source-abc.pt.srt` -> `pt`; `source-abc.srt` -> sem língua no nome.
        # A extensão sai primeiro: sem isso `source-abc.srt` dava a língua "srt".
        tag = os.path.splitext(name)[0][len(base):].lstrip(".").lower()
        found.append((os.path.join(folder, name), tag or None))
    if not found:
        return None, None
    order = [str(p).lower() for p in (prefer or []) if p] + list(SUBTITLE_LANGS)
    for want in order:
        for path_, lang in found:
            if lang == want.lower():
                return path_, lang
    if len(found) == 1:
        return found[0]
    # Mais de uma legenda e nenhuma nas línguas que sabemos comparar. Escolher a
    # primeira em ordem alfabética é o mecanismo exato que queimou inglês sobre
    # um hook em português; o gate de idioma só distingue pt/en/es, então um
    # `.fr` passaria batido. Quem escolhe é o dono, com --lang.
    tags = ", ".join(sorted(str(l) for _p, l in found))
    raise RuntimeError(
        f"{os.path.basename(path)} has subtitles in {tags} and none in "
        f"{', '.join(SUBTITLE_LANGS)}. Pick one with --lang: choosing "
        f"alphabetically is how an English caption ended up under a Portuguese "
        f"hook.")


def _read_srt(path):
    def seconds(stamp):
        # Um timestamp de VTT vem seguido dos parâmetros da cue --
        # `00:00:02.629 align:start position:0%` -- e o `split(":")` disso
        # devolvia seis pedaços contra três esperados. `archive` baixa VTT por
        # padrão, então este era o formato mais provável de chegar aqui, e ele
        # quebrava o `transcribe` inteiro com um ValueError.
        stamp = stamp.replace(",", ".").strip().split()[0]
        hours, minutes, rest = stamp.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)
    rows, block = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for line in lines + [""]:
        if line.strip():
            block.append(line)
            continue
        stamps = [l for l in block if "-->" in l]
        if stamps:
            start, end = [s.strip() for s in stamps[0].split("-->")]
            body = " ".join(l for l in block if "-->" not in l and not l.strip().isdigit())
            # As entidades saem ANTES das tags: uma legenda do YouTube traz
            # `[&nbsp;__&nbsp;]` onde censurou um palavrão e `&gt;&gt;` onde
            # marcou quem fala. Queimadas cruas, é isso que aparece na tela.
            body = html.unescape(body)
            body = re.sub(r"<[^>]+>", "", body)
            # `\h` (espaço duro) vira espaço comum, senão ele fica no meio da
            # frase e a contagem de caracteres por linha mente.
            body = " ".join(body.replace("\xa0", " ").split()).strip()
            if body:
                rows.append({"start": round(seconds(start), 2),
                             "end": round(seconds(end), 2), "text": body})
        block = []
    return _undo_rollup(rows)


def _undo_rollup(rows):
    """Desfaz a legenda rolante das transcrições automáticas do YouTube.

    Elas não entregam uma cue por fala: entregam a linha anterior mais as
    palavras novas, de novo e de novo, e ainda repetem a mesma cue duas vezes
    com dez milissegundos de diferença. Lido cru, um vídeo de 23 minutos vira um
    transcript em que cada frase aparece três vezes -- e o digest que o modelo lê
    para escolher a janela fica três vezes maior e ilegível.

    Cada cue fica só com o que ela acrescenta à anterior. Uma cue que não
    acrescenta nada desaparece, e seu tempo estende a anterior, para que a fala
    não perca duração no caminho.
    """
    out = []
    for row in rows:
        text = " ".join(str(row.get("text") or "").split())
        if not text:
            continue
        if out:
            previous = out[-1]["text"]
            if text == previous:
                out[-1]["end"] = max(out[-1]["end"], row["end"])
                continue
            if text.startswith(previous + " "):
                novo = text[len(previous):].strip()
                if not novo:
                    out[-1]["end"] = max(out[-1]["end"], row["end"])
                    continue
                text = novo
            elif previous.startswith(text + " "):
                # a cue nova é um prefixo da anterior: não acrescenta nada
                out[-1]["end"] = max(out[-1]["end"], row["end"])
                continue
        out.append({"start": row["start"], "end": row["end"], "text": text})

    # A legenda rolante ainda deixa cues de 10 milissegundos: são os pontos em
    # que o YouTube "fecha" uma linha que foi falada antes, no vão silencioso
    # desde a cue anterior. Mantidas como estão, a frase pisca por 10ms e o
    # espectador nunca a lê -- é conteúdo perdido, não um detalhe de timing. O
    # começo volta para onde a anterior acabou, que é quando aquilo foi dito.
    for i, row in enumerate(out):
        if row["end"] - row["start"] >= 0.3:
            continue
        antes = out[i - 1]["end"] if i else 0.0
        if antes < row["end"]:
            row["start"] = antes
    return [r for r in out if r["end"] - r["start"] > 0.05]


def _srt_stamp(t):
    if t is None or t < 0:
        t = 0.0
    ms = int(round(float(t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_stamp(t):
    cs = int(round(max(0.0, float(t)) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def to_ass(segments, width, height, safe=None, offset=0.0, length=None):
    """The caption as an ASS file, with the style baked into the file itself.

    Not force_style on the subtitles filter: that option takes comma-separated
    fields, and a filtergraph treats a comma as the end of a filter. Measured on
    the image's ffmpeg, every escaping of a multi-field force_style either errors
    or renders NOTHING at all -- which is why a burned caption never actually
    appeared. An ASS style line carries its own commas inside the file, where the
    filtergraph never sees them, so libass reads size, outline, alignment and the
    safe-area margins with nothing to escape.

    `offset` and `length` place the words on the CLIP's timeline, not the source's.
    A clip is cut with `-ss` seeking the source, so the render starts at 0 while
    the transcript's timestamps are still the source's -- burn them unshifted and
    every line shows at the wrong second, two at once, over the wrong shot. So each
    cue is moved back by `offset` (the cut's start), kept only if it overlaps the
    window, and clamped to `[0, length]`. The words come from the transcript; when
    they show, and that they stay on screen, is decided here.
    """
    safe = safe or {}
    x0 = int(safe.get("x0", 86))
    x1 = int(safe.get("x1", width - 140))
    y1 = int(safe.get("y1", int(height * 0.83)))
    # height // 26, não // 22. O antigo dava 87px num quadro de 1920, grande
    # demais para duas linhas de fala, e foi parte do bloco que cobriu o rosto.
    fontsize = max(18, height // S.CAPTION_SIZE_DIVISOR)
    outline = 3
    margin_l = max(10, x0)
    margin_r = max(10, width - x1)
    margin_v = max(10, height - y1)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        # WrapStyle 0: wrap long lines inside the margins. WrapStyle 2 (no wrap)
        # let a real transcript line run off the right of the frame.
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        # Anton, não Arial. `Arial` não está instalada na imagem e o libass caía
        # numa fallback genérica de peso errado -- é o defeito 2.3, e a fonte
        # está no repo (`warden-shared/assets`) justamente para acabar com isso.
        # Quem passar este ASS ao filtro `subtitles` tem de passar também
        # `fontsdir` apontando para essa pasta, senão a fallback volta.
        # A sombra sai de 0 para 1: peso separado do contorno, como no PRIME.
        f"Style: Default,Anton,{fontsize},&H00FFFFFF,&H000000FF,&H00000000,"
        f"&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,"
        f"{margin_l},{margin_r},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n")
    offset = float(offset or 0.0)
    rows = []
    # Reflow antes de virar cue: no máximo duas linhas, ~26 caracteres por linha
    # e ~2,2s por cue, com o tempo repartido proporcional às palavras. Sem isso
    # um segmento de sete segundos com trinta palavras vira um bloco de seis
    # linhas parado na tela, que foi exatamente o que cobriu o rosto do sujeito.
    segments = S.reflow_cues(segments)
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start, end = seg.get("start"), seg.get("end")
        if start is None or end is None:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0):
            continue
        start -= offset
        end -= offset
        if length is not None:
            if end <= 0 or start >= float(length):     # outside the clip window
                continue
            end = min(end, float(length))
        start = max(0.0, start)
        if end <= start:
            continue
        # A newline in ASS is \N; a lone brace opens an override block. Neither
        # belongs in a transcript line, so both are neutralised.
        lines = seg.get("lines") or [text]
        lines = [" ".join(str(l).replace("\\", "").replace("{", "(")
                          .replace("}", ")").split()) for l in lines]
        # As linhas já foram quebradas pelo reflow; o \N as fixa onde foram
        # medidas em vez de deixar o libass reembrulhar dentro das margens.
        rows.append((start, end, "\\N".join(l for l in lines if l)))
    rows.sort(key=lambda r: r[0])
    body = "".join(
        f"Dialogue: 0,{_ass_stamp(a)},{_ass_stamp(b)},Default,,0,0,0,,{t}\n"
        for a, b, t in rows)
    return header + body if rows else ""


def to_srt(segments):
    """An SRT from a transcript, carrying only lines a viewer could actually read.

    Whisper is not a court reporter. It returns empty segments, segments with no
    timing, and now and then a word it simply got wrong -- and a wrong word burned
    on the screen is worse than no caption, because the clipper cannot see it is
    wrong until a viewer does. This tool cannot tell a wrong word from a right one;
    that judgement stays with the person who can check it against the video. What
    it CAN do is refuse to burn what is structurally unusable: a blank line, a line
    with no start or end, a line whose end is not after its start. Those are
    dropped, the rest are renumbered so there is no gap, and an empty result comes
    back as an empty string -- nothing to burn, said by its emptiness.
    """
    rows = []
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start, end = seg.get("start"), seg.get("end")
        if start is None or end is None:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0):
            continue
        rows.append((start, end, text))
    rows.sort(key=lambda r: r[0])
    return "\n".join(
        f"{i}\n{_srt_stamp(a)} --> {_srt_stamp(b)}\n{t}\n"
        for i, (a, b, t) in enumerate(rows, 1))


# -------------------------------------------------------------- viral signals
#
# Where the language of a clip tends to spike, as hints -- never as a verdict.
# The tool cannot know what is funny, what is damning, what will travel. It can
# see where a question is asked, where an absolute is claimed, where a fight is
# named, where the room got loud. Those are evidence a model reads on top of the
# words; the judgement of what to cut stays with the thing that can read the
# clip, because a program scoring "viral" without watching is the ranking this
# agent refuses to be.
_SIGNAL_WORDS = {
    "superlative": {
        "pt": ["melhor", "pior", "nunca", "sempre", "jamais", "ninguém",
               "ninguem", "todo mundo", "maior", "incrível", "incrivel",
               "absurdo", "chocante", "inacreditável", "inacreditavel",
               "impossível", "impossivel", "recorde", "o mais", "a mais"],
        "en": ["best", "worst", "never", "always", "nobody", "everyone",
               "biggest", "insane", "crazy", "shocking", "unbelievable",
               "impossible", "record", "the most"],
    },
    "conflict": {
        "pt": ["mentira", "errado", "errada", "discordo", "polêmica", "polemica",
               "briga", "brigar", "contra", "ódio", "odio", "cancelado",
               "cancelada", "processo", "revoltante", "expôs", "expos",
               "acusou", "acusa"],
        "en": ["lie", "wrong", "disagree", "fight", "versus", "hate", "cancel",
               "cancelled", "controversy", "exposed", "sue", "beef", "accused"],
    },
    "reaction": {
        "pt": ["meu deus", "caramba", "nossa", "pelo amor", "que isso",
               "não acredito", "nao acredito", "surreal", "chocado", "chocada"],
        "en": ["oh my god", "wtf", "holy", "no way", "i can't", "i cant",
               "unreal", "speechless"],
    },
}
_LAUGH_RE = re.compile(r"(?:k{3,}|(?:ha){2,}|(?:he){2,}|rs{2,}|lmao|lol|haha)",
                       re.IGNORECASE)


def text_signals(text):
    """The signal tags a line of transcript carries, as a sorted list.

    A question, a laugh, an absolute claim, a named conflict, a reaction. Cheap
    keyword and punctuation matching in Portuguese and English -- a hint that a
    moment might carry, not proof that it does.
    """
    raw = str(text or "")
    # Punctuation stripped before matching, so "absurdo!" and "lie." still match
    # the word lists. The question mark and laughter are read off the raw text
    # first, before it is flattened.
    flat = re.sub(r"[^\w\s]", " ", raw.lower(), flags=re.UNICODE)
    padded = " " + " ".join(flat.split()) + " "
    tags = set()
    if "?" in raw:
        tags.add("question")
    if _LAUGH_RE.search(raw):
        tags.add("laugh")
    for tag, langs in _SIGNAL_WORDS.items():
        for words in langs.values():
            if any((" " + w + " ") in padded for w in words):
                tags.add(tag)
                break
    return sorted(tags)


def _loudness_envelope(source):
    """(second, momentary loudness) across the source, in one ffmpeg pass.

    ebur128 prints a momentary reading every tenth of a second; a spike in it is
    a laugh, a shout, a crowd -- the sound of a reaction, which is the strongest
    signal a live or a podcast gives that a moment landed. No audio, or no
    Devolve (leituras, motivo). `motivo` é None quando deu certo e uma frase
    quando não deu: um envelope vazio por falta de ffmpeg e um envelope vazio
    porque o vídeo é mudo são a mesma lista e não são a mesma coisa, e quem lê
    os sinais precisa saber qual dos dois aconteceu antes de escolher a janela
    achando que o som não tinha nada a dizer.
    """
    if not have("ffmpeg"):
        return [], "ffmpeg is not on PATH, so no loudness was read"
    try:
        done = subprocess.run(
            ["ffmpeg", "-nostats", "-hide_banner", "-i", source, "-map", "0:a:0",
             "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=TIMEOUT_RENDER)
    except Exception as exc:
        return [], f"could not read the loudness: {type(exc).__name__}"
    # ametadata prints two lines per reading: `... pts_time:X` then a line
    # `lavfi.r128.M=Y`. Pair them.
    env, cur_t = [], None
    for line in done.stderr.splitlines():
        mt = re.search(r"pts_time:([\d.]+)", line)
        if mt:
            cur_t = float(mt.group(1))
            continue
        mm = re.search(r"lavfi\.r128\.M=(-?[\d.]+)", line)
        if mm and cur_t is not None:
            env.append((cur_t, float(mm.group(1))))
            cur_t = None
    if not env:
        return [], ("no audio track to read a reaction from, so the loud "
                    "signal is missing and the words carry alone")
    return env, None


def loud_segment_indexes(segments, source, top_fraction=0.25):
    """Which segments sit in the loudest quarter of the source.

    The reaction peaks: the model reads them as 'the room got loud here'. A
    quarter is a threshold, not a truth -- it says where the sound is, and the
    model says whether that is a moment.
    """
    env, why = _loudness_envelope(source)
    if not env or not segments:
        return set(), why
    peaks = []
    for seg in segments:
        s, e = seg.get("start"), seg.get("end")
        if s is None or e is None:
            peaks.append(None)
            continue
        vals = [lufs for t, lufs in env if s <= t <= e and lufs > -70]
        peaks.append(max(vals) if vals else None)
    got = sorted(p for p in peaks if p is not None)
    if not got:
        return set(), "no segment overlapped a loudness reading"
    thr = got[min(len(got) - 1, int(len(got) * (1 - top_fraction)))]
    return {i for i, p in enumerate(peaks) if p is not None and p >= thr}, None


def analyze_signals(segments, source=None):
    """The moments worth a second look, in time order, each with why.

    A segment surfaces when its words carry a signal or the sound spikes on it.
    Everything else is dropped, because a list that keeps every line is the
    transcript again, not a shortlist. Nothing here is ranked: it is time order
    with evidence attached, for the model to cluster into clips and judge.
    """
    loud, quiet_why = ((set(), "no source file was passed, so only the words "
                        "were read") if not source
                       else loud_segment_indexes(segments, source))
    out = []
    for i, seg in enumerate(segments or []):
        tags = text_signals(seg.get("text", ""))
        if i in loud:
            tags = sorted(set(tags) | {"loud"})
        if tags:
            out.append({"start": seg.get("start"), "end": seg.get("end"),
                        "text": (seg.get("text") or "").strip(), "signals": tags})
    return out, quiet_why


def digest(segments, window=None, seconds_per_line=12):
    """The transcript a model can afford to read.

    Word-level timing is for the renderer. A model choosing a moment needs the
    words and roughly where they are, and handing it the raw artefact is how a
    three-hour source turns into a prompt nobody can pay for.
    """
    lines, bucket, bucket_start = [], [], None
    for seg in segments:
        if window and (seg["end"] < window[0] or seg["start"] > window[1]):
            continue
        if bucket_start is None:
            bucket_start = seg["start"]
        bucket.append(seg["text"])
        if seg["end"] - bucket_start >= seconds_per_line:
            lines.append(f"[{_stamp(bucket_start)}] " + " ".join(bucket))
            bucket, bucket_start = [], None
    if bucket:
        lines.append(f"[{_stamp(bucket_start)}] " + " ".join(bucket))
    return "\n".join(lines)


def _stamp(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------- rendering

CROP_PRESETS = {"left": 0.25, "center": 0.5, "right": 0.75}
FACE_MODEL = os.environ.get("WARDEN_FACE_MODEL", "/opt/plow/yunet.onnx")


def _crop_fraction(crop):
    """Where the vertical window sits across a wider source, 0.0 to 1.0.

    A landscape source does not fit a 9:16 frame, so a band of it is kept and
    the rest is thrown away. Centre is the default and it is a guess: it is
    wrong for a side-by-side, a two-shot, a gameplay with the face in a corner.
    `left`/`center`/`right`, or a percentage, let the caller say where the
    subject actually is instead of hoping it is in the middle.
    """
    if crop is None or crop == "" or crop == "auto":
        return 0.5
    if crop in CROP_PRESETS:
        return CROP_PRESETS[crop]
    try:
        pct = float(crop)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"crop {crop!r} is not left, center, right, auto or a number from 0 to 100")
    if not 0 <= pct <= 100:
        raise RuntimeError("crop as a percentage is 0 (far left) to 100 (far right)")
    return pct / 100.0


def face_detection_status():
    """(pronto?, motivo em uma linha). A pergunta que ninguém fazia.

    O enquadramento vertical inteiro depende disto, e até aqui a resposta
    "não tenho detector" era indistinguível de "procurei e não achei rosto":
    as duas viravam `None` e as duas caíam no centro. Numa máquina sem OpenCV
    o corte saiu com o rosto na borda e o aviso dizia, com toda a calma, que
    havia enquadrado no centro -- o que era verdade e não era o problema.
    """
    try:
        import cv2
    except Exception as exc:
        return False, ("OpenCV is not installed in this image, so there is no "
                       f"face detector ({type(exc).__name__})")
    if not os.path.exists(FACE_MODEL):
        return False, (f"the YuNet model is not at {FACE_MODEL}, so the face "
                       "detector has nothing to run")
    try:
        cv2.FaceDetectorYN_create(FACE_MODEL, "", (320, 320), 0.6, 0.3, 5000)
    except Exception as exc:
        return False, (f"OpenCV {getattr(cv2, '__version__', '?')} could not "
                       f"build the YuNet detector: {type(exc).__name__}: {exc}")
    return True, f"YuNet on OpenCV {getattr(cv2, '__version__', '?')}"


def _face_detector():
    """YuNet, ou None quando esta máquina não tem com que detectar rosto.

    A DNN face detector rather than a Haar cascade because the footage is
    stylised -- animation, game capture -- and the cascades, trained on
    photographs, miss it; YuNet was measured finding the faces in this footage
    where they did not.

    `None` aqui significa APENAS "não há detector nesta máquina", e quem chama
    tem de parar. Não significa "não achei rosto": isso é uma lista vazia, que é
    outra resposta e admite outro tratamento.
    """
    ok, _why = face_detection_status()
    if not ok:
        return None
    import cv2
    return cv2, cv2.FaceDetectorYN_create(FACE_MODEL, "", (320, 320),
                                          0.6, 0.3, 5000)


def _face_centers(source, start, length, samples=7):
    """(x-fraction, area-fraction) of every face found across the clip window.

    Sampled over several frames rather than one, because a face turns away, a
    shot changes, a detector blinks. The centres of the frames it did find are
    what a crop can be built on.
    """
    found = _face_detector()
    if found is None:
        return []
    cv2, det = found
    import subprocess, tempfile
    out, lidos = [], 0
    for i in range(samples):
        t = float(start) + (i + 0.5) * float(length) / samples
        fd, png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss",
                            f"{t:.3f}", "-i", source, "-frames:v", "1", png],
                           capture_output=True, timeout=30)
            img = cv2.imread(png)
            if img is None:
                continue
            h, w = img.shape[:2]
            det.setInputSize((w, h))
            _n, faces = det.detect(img)
            if faces is not None:
                for f in faces:
                    cx = (float(f[0]) + float(f[2]) / 2) / w
                    area = (float(f[2]) * float(f[3])) / (w * h)
                    out.append((cx, area))
        except Exception:
            continue                                   # a bad frame is not a failed cut
        else:
            lidos += 1
        finally:
            try:
                os.remove(png)
            except OSError:
                pass
    if not lidos:
        # Zero quadros lidos não é "não achei rosto": é "não olhei". As duas
        # devolviam lista vazia e as duas caíam no centro, que foi como o rosto
        # foi parar na borda do quadro sem uma linha de aviso.
        raise RuntimeError(
            f"the face detector could not read a single frame of "
            f"{os.path.basename(source)} between {float(start):.1f}s and "
            f"{float(start) + float(length):.1f}s, so the framing has nothing "
            f"to follow -- pass --crop to place the band by hand.")
    return out


def _face_crop_fraction(faces, hint, kept):
    """A crop fraction that centres the kept band on the subject's face.

    `hint` is the side the caller pointed at: left keeps faces on the left half,
    right the right, and center/auto/none takes them all. The prominent (larger,
    nearer) faces decide, so a face in the foreground wins over one in the back,
    which is the exact miss that put the crop on the monster behind the man.
    Returns None when there is no face to trust, and the caller falls back.
    """
    if not faces or not kept or kept >= 1:
        return None
    if hint == "left":
        faces = [f for f in faces if f[0] < 0.5]
    elif hint == "right":
        faces = [f for f in faces if f[0] > 0.5]
    if not faces:
        return None
    faces = sorted(faces, key=lambda f: -f[1])         # largest first
    top = faces[:max(1, len(faces) // 2)]
    xs = sorted(f[0] for f in top)
    target = xs[len(xs) // 2]                           # median x of the prominent faces
    fx = (target - kept / 2) / (1 - kept)
    return min(1.0, max(0.0, fx))


def cut(source, out, rules, start, end, caption_srt=None, hook=None,
        track=None, track_start=None, sound="platform", crop=None,
        shots=None, language=None, motion=True, cover_footer=None):
    """One clip, with the campaign's numbers rather than a house style.

    Duration is clamped to the campaign's window before a frame is written: a
    render that ends one second over the limit costs the whole submission, and
    the clipper is the one who finds out. Burned text stays inside the safe area
    for the same reason the resolution comes from the rule set, which is that
    the platform's furniture is not negotiable and the brief does not mention it.
    """
    if not have("ffmpeg"):
        raise RuntimeError("ffmpeg is not on PATH")
    width = int(R.get(rules, "video.width", 1080) or 1080)
    height = int(R.get(rules, "video.height", 1920) or 1920)
    lo = R.get(rules, "video.duration_min_s")
    hi = R.get(rules, "video.duration_max_s")
    length = max(0.1, float(end) - float(start))
    notes = []
    if hi is not None and length > hi:
        notes.append(f"trimmed from {length:.1f}s to the {hi}s maximum")
        length = float(hi)
    if lo is not None and length < lo:
        notes.append(f"extended from {length:.1f}s to the {lo}s minimum")
        length = float(lo)

    # An edit is cut to the bar, and it is cut to the bar even when the file
    # ships silent. The platform's own sound player starts where you tell it,
    # so a clip that is a whole number of bars long still lands on the beat
    # once the track is added in the app, which is the only way a campaign that
    # bans embedded audio can have an edit at all.
    grid = None
    if track:
        import warden_beat
        grid = warden_beat.analyse(track)
        snapped, bars = warden_beat.snap(length, grid["bar_s"], lo, hi)
        if snapped is None:
            notes.append(f"no whole number of bars of {grid['track']} fits between "
                         f"{lo}s and {hi}s, so this cut is not on the grid")
        else:
            notes.append(f"{bars} bars of {grid['track']} at {grid['bpm']} BPM "
                         f"({grid['bar_s']:.3f}s a bar), so {snapped:.2f}s")
            length = snapped

    safe = R.get(rules, "safe_area", {}) or {}
    x0 = int(safe.get("x0", 86)); x1 = int(safe.get("x1", width - 140))
    y1 = int(safe.get("y1", int(height * 0.83)))

    # Where the vertical band sits across a wider source. A bare percentage is
    # the owner's exact call and is honoured as given. A side name, or nothing,
    # is a hint the tool refines: it finds the faces and centres the band on the
    # one that matters, which is what keeps the crop off the creature behind the
    # man. Detection needs the source's real dimensions, so they are measured
    # first; the kept-band fraction they give also names, in the note, exactly
    # which pixels survived.
    manual_pct = crop is not None and crop != "auto" and crop not in CROP_PRESETS
    sw = sh = kept = None
    try:
        sw, sh = _dimensions(source)
    except Exception as exc:
        # Sem as dimensões da fonte não se sabe sequer se ela é mais larga que o
        # quadro -- e `kept=None` desligava, de uma vez, o portão de detecção de
        # rosto, o recorte de moldura E a nota que diria qual faixa ficou. Era a
        # porta lateral por onde o defeito de 14/09 voltava inteiro, e calado.
        raise RuntimeError(
            f"could not read the dimensions of {os.path.basename(source)} "
            f"({type(exc).__name__}: {exc}) -- without them this cut cannot "
            f"tell whether it needs to choose a vertical band at all.")
    if sw / sh > (width / height) * 1.05:          # wider than the target frame
        kept = width / (sw * height / sh)          # fraction of source width kept

    fx = _crop_fraction(crop)
    framed_by = "crop %s" % (crop if crop is not None else "centre (default)")
    if not manual_pct and kept:
        # A fonte é mais larga que o quadro, então alguém tem de escolher qual
        # faixa fica -- e essa escolha é o rosto. Sem detector, essa escolha não
        # existe: o centro é um chute, e num vídeo em que o sujeito senta à
        # esquerda o chute entrega o rosto cortado na borda. Foi o que saiu na
        # rodada de 14/09, e o aviso de então dizia "enquadrei no centro", que
        # era verdadeiro e inútil.
        #
        # Então para aqui. O dono resolve em um argumento; um clipe com o rosto
        # na borda não se resolve depois de publicado.
        ready, why = face_detection_status()
        if not ready:
            # Um lado nomeado é o dono dizendo onde o sujeito está, e isso é
            # honrado sem detector nenhum -- o detector só refinaria. O que não
            # se pode é inventar: sem instrução e sem detector, não há de onde
            # tirar a faixa, e recusar aqui é a diferença entre um argumento a
            # mais e um rosto cortado no arquivo publicado.
            if crop in (None, "auto"):
                raise RuntimeError(
                    f"no face detection on this machine ({why}) -- pass --crop "
                    f"left|right|center|<0-100> to say where the subject is, or "
                    f"rebuild the image, which ships the detector.")
            notes.append(f"framed by crop {crop} as given: no face detector on "
                         f"this machine to refine it ({why})")
        else:
            faces = _face_centers(source, start, length)
            face_fx = _face_crop_fraction(faces, crop, kept)
            if face_fx is not None:
                fx = face_fx
                framed_by = "face" + ("" if crop in (None, "auto")
                                      else f" on the {crop}")

    # ------------------------------------------------------- olhar o material
    #
    # A ferramenta passa a abrir alguns quadros da janela antes de montar a
    # cadeia. Até aqui ela media pixels sem nunca olhar nenhum, e foi assim que
    # a borda do material entrou no enquadramento e a legenda do acervo ficou
    # cortada ao meio embaixo da nossa.
    try:
        looked = S.sample_frames(source, start, length, count=8)
    except Exception as exc:
        raise RuntimeError(
            f"could not open any frame of {os.path.basename(source)} between "
            f"{float(start):.1f}s and {float(start) + length:.1f}s "
            f"({type(exc).__name__}: {exc}), so nothing here has seen this "
            "footage -- refusing to guess that it is clean.")
    if not looked:
        # O silêncio caro: sem quadros, `bottom` sai False, e False aqui lê como
        # "material limpo, não precisa cobrir". É uma resposta que a ferramenta
        # não tem, com a cara de uma que ela tem.
        raise RuntimeError(
            f"could not open any frame of {os.path.basename(source)} between "
            f"{float(start):.1f}s and {float(start) + length:.1f}s, so this "
            "cut cannot tell whether the footage carries its own burned text "
            "or a border. Check the window is inside the file.")
    source_text = S.burned_text_bands(looked)
    source_text["looked"] = True
    border = S.frame_border(looked)

    # A moldura sai do enquadramento antes de qualquer outra conta. `crop` no
    # source, não no destino: recua a faixa para dentro do conteúdo, e o scale
    # seguinte trabalha só com o que sobrou.
    pre = ""
    if (border["left"] or border["right"]) and sw:
        l, r = int(border["left"]), int(border["right"])
        pre = f"crop=in_w-{l + r}:in_h:{l}:0,"
        notes.append(f"trimmed {l}px off the left and {r}px off the right before "
                     f"framing: those columns are source furniture (a bar, a "
                     f"capture border), not picture. Left in, they sit frozen at "
                     f"the edge of the whole clip.")
        if kept:                             # a faixa útil encolheu junto
            kept = width / ((sw - l - r) * height / sh)
            kept = min(1.0, kept)

    chain = (f"{pre}scale={width}:{height}:force_original_aspect_ratio=increase,"
             f"crop={width}:{height}:(in_w-out_w)*{fx:.4f}:(in_h-out_h)*0.5,"
             f"setsar=1,fps=30")

    # ------------------------------------------------------- movimento
    #
    # Defeito 2.8, e não é bug: é ausência de recurso. Um corte sem nenhuma
    # variação de escala lê como material bruto, e é a diferença mais barata
    # entre corte e edit. No PRIME o zoom leve é a regra e não a exceção -- todo
    # plano do `tese-01` traz um, entre 1,02 e 1,10.
    #
    # `shots` é a gramática dos EDITS portada: uma lista de planos com `in`/`out`
    # e um `efeito`. Sem ela, um zoom leve único cobre a janela inteira, que é o
    # mínimo para o clipe não ler como trecho bruto.
    if shots:
        notes.append(f"{len(shots)} shots asked for; scale moves on each")
    if motion:
        zoom_from, zoom_to = 1.0, 1.06
        if isinstance(motion, dict):
            zoom_from = float(motion.get("de", zoom_from))
            zoom_to = float(motion.get("para", zoom_to))
        frames_total = max(1.0, length * 30)
        step = (zoom_to - zoom_from) / frames_total
        # fps antes do zoompan: com d=1 ele carimba cada quadro de entrada a
        # 1/30s, e sem acertar o fps antes o plano toca mais rápido que o áudio e
        # congela no fim. É a nota do PRIME, e ela custou um render para existir.
        chain += (f",zoompan=z='min({zoom_from}+{step:.8f}*on,{zoom_to})'"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
                  f":s={width}x{height}:fps=30,setsar=1")
        notes.append(f"scale moves {zoom_from:.2f} to {zoom_to:.2f} across the "
                     f"clip. A cut with no scale move reads as raw footage.")

    if kept:
        left_edge = fx * (1 - kept)
        note_band = f"{int(left_edge * sw)}–{int((left_edge + kept) * sw)} of {sw}px"
        if framed_by.startswith("face"):
            notes.append(f"framed by {framed_by}: keeping x {note_band}")
        elif crop is None or crop == "auto":
            notes.append(
                f"framed on the centre (no face found to follow): keeping x "
                f"{note_band}. If the subject is a side-by-side, a two-shot or a "
                "corner cam, re-cut with --crop left|right or a percentage.")
        else:
            notes.append(f"{framed_by}: keeping x {note_band}")
    # ---------------------------------------------------------------- texto
    #
    # Todo texto daqui para baixo é PNG do PIL, não `drawtext` e não `subtitles`.
    # Três motivos, e nenhum deles é preferência:
    #
    #  - `drawtext` não quebra linha e não ajusta corpo. É o defeito 2.1: com
    #    1080px de quadro o corpo saía 49px, 44 caracteres passavam de 1100px, o
    #    `x=(w-text_w)/2` ficava negativo e o ffmpeg cortava os dois lados sem
    #    erro nenhum. Qualquer hook com mais de uns 35 caracteres saía cortado.
    #  - o PIL mede o texto ANTES de desenhar, que é a única forma de garantir
    #    que ele cabe. É o que o PRIME faz, e é o que já produziu clipe aprovado.
    #  - nem todo ffmpeg tem libass ou drawtext -- este aqui não tem nenhum dos
    #    dois. Um renderizador que só funciona numa build é um renderizador que
    #    ninguém consegue conferir na máquina onde escreve o código.
    #
    # Cada texto vira um PNG transparente do tamanho do quadro e entra como
    # `overlay` com `enable='between(t,a,b)'`, que é a montagem do PRIME.
    overlays = []                     # (png, y, de, ate)
    art_dir = os.path.dirname(os.path.abspath(out)) or "."
    stem = os.path.splitext(os.path.basename(out))[0]
    # O que este render fez consigo mesmo, gravado ao lado dele. `style check`
    # lê daqui em vez de tentar recuperar dos pixels: a largura do hook aqui é a
    # que o PIL mediu com a fonte real, e não uma estimativa que confunde letra
    # com letreiro de neon.
    style_facts = {"hook": None, "caption": None, "footer_covered": False,
                   "motion": bool(motion), "source_text": source_text}

    # ------------------------------------------------------- texto do acervo
    #
    # Defeito 2.6, as duas metades. Detectar já foi feito acima; aqui é tratar,
    # e a decisão vai num aviso ao dono, nunca num silêncio.
    #
    #  - se o material já tem texto embaixo e nós vamos queimar legenda, sem
    #    tratamento saem DUAS legendas no mesmo quadro. O degradê do PRIME cobre
    #    a de baixo e a nossa fica sozinha.
    #  - `cover_footer=False` é a outra saída legítima: não cobre e não queima.
    footer = None
    wants_caption = bool(caption_srt and os.path.exists(caption_srt))
    if source_text.get("bottom"):
        cover = cover_footer
        if cover is None:
            # Cobre quando a nossa legenda vai disputar a mesma faixa. Se não
            # vamos queimar nada, o texto do acervo não está competindo com
            # ninguém, e cobri-lo seria estragar o quadro por nada.
            cover = wants_caption
        if cover:
            # A medida entra com folga, mas presa entre 18% e 24% da altura.
            #
            # A medida sozinha não serve para desenhar: material com duas faixas
            # de texto -- um disclaimer colado no rodapé e a legenda do acervo
            # acima dele -- tem um vão limpo entre as duas, e qualquer varredura
            # que pare na primeira queda cobre só a de baixo. Foi o que aconteceu
            # aqui: a varredura disse 8%, o texto ia até 14%, e a legenda do
            # acervo ficou legível embaixo da nossa.
            #
            # 20% cobre as duas faixas com folga e ainda deixa a nossa legenda,
            # que mora por volta de 25%, fora do degradê; 24% é o teto, porque
            # além disso se apaga imagem para resolver um problema de texto. A
            # medida continua na nota, como evidência de até onde o texto ia.
            reach = float(source_text.get("bottom_reach") or 0.0) + 0.06
            band = int(height * min(0.24, max(0.20, reach)))
            footer, fy = S.footer_png(os.path.join(art_dir, f"{stem}-rodape.png"),
                                      width, height, band=band)
            overlays.append((footer, fy, 0.0, float(length)))
            style_facts["footer_covered"] = True
            notes.append(
                "this footage already carries burned text along the bottom "
                f"({source_text['evidence']}). Covered it with the gradient "
                "footer so there is one caption in frame and not two. If that "
                "bottom text is another clipper's watermark rather than the "
                "archive's own captions, covering it breaks the rules -- re-cut "
                "with cover_footer=False and drop --subtitles.")
        else:
            notes.append(
                "this footage already carries burned text along the bottom "
                f"({source_text['evidence']}). Not covering it, so do not burn "
                "captions over it -- two captions in one frame is a reject.")
    if source_text.get("top"):
        notes.append("this footage carries burned text along the TOP as well "
                     f"({source_text['evidence']}) -- the hook will land on it. "
                     "Check the contact sheet before you send this one.")

    burn_reason = None
    cues = []
    if caption_srt and os.path.exists(caption_srt):
        if R.get(rules, "sources.archive_has_captions") is True:
            # The campaign says this archive already burns its own captions.
            # Burning ours over them is the doubling the owner set this flag to
            # prevent, so the tool refuses it here rather than leaving it to be
            # caught by eye on the first clip.
            burn_reason = ("not burning captions: this campaign's archive already "
                           "carries its own (sources.archive_has_captions is true)")
        else:
            approved, why = S.approval_state(caption_srt)
            if not approved:
                # O portão do defeito 2.5. Sem aprovação o clipe sai SEM legenda,
                # em vez de sair com a palavra errada queimada: um clipe mudo se
                # conserta, um `jokovic jokovic` no vídeo publicado não.
                burn_reason = f"not burning captions: {why}"
            else:
                rows = _read_srt(caption_srt)
                window = [r for r in rows
                          if r["end"] > start and r["start"] < start + length]
                clash = S.language_clash(
                    hook, " ".join(r["text"] for r in window),
                    declared=language or R.get(rules, "caption.language"))
                if clash:
                    # Defeito 2.4. Recusa e diz por quê, em vez de queimar inglês
                    # sobre um hook em português como saiu da última vez.
                    burn_reason = f"not burning captions: {clash}"
                else:
                    cues = S.reflow_cues(window)
                    if not cues:
                        burn_reason = ("the subtitle file had no usable lines in "
                                       "this window, so nothing was burned")

    if cues:
        # A legenda mora na faixa de baixo mas acima da furniture da plataforma,
        # e o corpo vem do briefing: height // 26, não height // 22 (que dava
        # 87px num quadro de 1920 e empurrava a fala para cima do rosto).
        cap_size = max(20, height // S.CAPTION_SIZE_DIVISOR)
        cap_y = int(y1 - cap_size * S.LEADING * 1.35)
        for i, cue in enumerate(cues):
            a = max(0.0, cue["start"] - start)
            b = min(float(length), cue["end"] - start)
            if b <= a:
                continue
            png = os.path.join(art_dir, f"{stem}-cue{i:03d}.png")
            png, oy = S.text_png(cue["lines"], png, width, height, cap_y,
                                 cap_size, scrim=True)
            overlays.append((png, oy, a, b))
        longest = max(c["end"] - c["start"] for c in cues)
        most = max(len(c["lines"]) for c in cues)
        style_facts["caption"] = {"cues": len(overlays) - (1 if footer else 0),
                                  "max_lines": most,
                                  "max_cue_s": round(longest, 2)}
        notes.append(f"burned {len(overlays)} caption cues: at most {most} lines "
                     f"and {longest:.1f}s each (was one cue per whisper segment, "
                     f"which is how a six-line block sat on a face for 7s)")
    elif burn_reason:
        notes.append(burn_reason)

    if hook:
        # O hook é medido contra a largura útil e quebrado em até duas linhas
        # antes de virar pixel. 854px num quadro de 1080: a coluna direita do
        # TikTok reserva 140px e a margem esquerda come 86px.
        usable = S.usable_width(width)
        size = max(20, int(width * S.HOOK_SIZE_RATIO))
        floor = max(16, int(width * S.HOOK_MIN_RATIO))
        lines, fitted, whole = S.fit_lines(hook, usable, size, floor)
        hook_y = max(int(height * 0.14), int(safe.get("y0", 200)) + fitted)
        png = os.path.join(art_dir, f"{stem}-hook.png")
        png, oy = S.text_png(lines, png, width, height, hook_y, fitted, scrim=True)
        overlays.append((png, oy, 0.0, float(length)))
        # A largura REAL do texto desenhado, medida com a fonte que o desenhou.
        # Não é estimativa: é o número contra o qual a quebra foi decidida, e é
        # o que permite a "o hook cabe inteiro" ser aritmética em vez de olhar.
        from PIL import Image as _Im, ImageDraw as _Dr
        _probe = _Dr.Draw(_Im.new("RGBA", (8, 8)))
        _f = S._font(fitted)
        drawn = int(max(_probe.textlength(l, font=_f) for l in lines)) if lines else 0
        style_facts["hook"] = {"width_px": drawn, "usable_px": usable,
                               "lines": len(lines), "size_px": fitted,
                               "chars": len(str(hook)), "complete": bool(whole)}
        notes.append(f"hook drawn at {fitted}px in {len(lines)} line(s): "
                     f"{drawn}px against {usable}px of usable width")
        if fitted <= floor:
            notes.append(f"the hook only fits at the {floor}px floor. It is "
                         f"{len(hook)} characters -- shorter reads better in a feed.")

    audio_policy = R.get(rules, "video.audio")
    silent = audio_policy == "forbidden" or (sound == "platform" and audio_policy != "required")
    embed = bool(track) and not silent

    args = ["ffmpeg", "-y", "-ss", f"{float(start):.3f}", "-i", source]
    next_input = 1
    track_index = None
    if embed:
        # The track enters at its drop unless told otherwise, because an edit
        # that opens on an intro has spent its first second on nothing.
        at = track_start
        if at is None:
            at = (grid or {}).get("drop_s")
        if at is None:
            at = 0.0
            notes.append("no drop was found in this track -- it never gains "
                         "body -- so the music enters at 0.00s, which is its "
                         "intro. Pass --track-start to choose a better entry.")
        args += ["-ss", f"{float(at):.3f}", "-i", track]
        track_index = next_input
        next_input += 1

    # Cada PNG entra como um vídeo de um quadro em laço, porque um overlay com
    # fade de alfa sobre uma imagem parada apaga o único quadro que existe --
    # é a nota do PRIME e custou um render para ser descoberta.
    overlay_index = {}
    for png, _y, _a, _b in overlays:
        args += ["-loop", "1", "-framerate", "30", "-i", png]
        overlay_index[png] = next_input
        next_input += 1

    args += ["-t", f"{length:.3f}"]

    # Um filter_complex quando há overlay ou trilha; o -vf simples continua
    # servindo o caso sem texto, que é o mais barato e o mais comum nos testes.
    video = f"[0:v]{chain}[v0]"
    last = "v0"
    for i, (png, oy, a, b) in enumerate(overlays):
        idx = overlay_index[png]
        # `shortest=0:repeatlast=0` impede que o último quadro do PNG fique
        # carimbado depois do fim da janela, que é como um texto de 2s vira um
        # texto que não sai mais da tela.
        video += (f";[{idx}:v]format=yuva420p,setpts=PTS-STARTPTS[o{i}]"
                  f";[{last}][o{i}]overlay=0:{int(oy)}:shortest=0:repeatlast=0"
                  f":enable='between(t,{a:.3f},{b:.3f})'[v{i + 1}]")
        last = f"v{i + 1}"
    video = video.replace(f"[{last}]", "[vid]") if last != "v0" else f"[0:v]{chain}[vid]"

    if embed:
        fade = max(0.5, min(3.0, length * 0.12))
        music = (f"[{track_index}:a]atrim=duration={length:.3f},asetpts=N/SR/TB,"
                 f"volume=-6dB,afade=t=out:st={max(0, length - fade):.3f}:d={fade:.3f}[m]")
        # "não tem áudio" e "não consegui perguntar" davam os dois `False`, e
        # `False` aqui joga fora a faixa de fala inteira: o clipe sai só com a
        # música, num modo cujo comentário logo abaixo diz que as palavras é que
        # carregam o clipe. Um mosaico de quadros não mostra fala faltando.
        sonda = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name", "-of", "csv=p=0", source],
            capture_output=True, text=True)
        if sonda.returncode != 0:
            raise RuntimeError(
                f"could not ask whether {os.path.basename(source)} has an audio "
                f"track (ffprobe exited {sonda.returncode}). Mixing a track over "
                f"it now would silently drop the speech, so this cut stops.")
        has_source_audio = bool(sonda.stdout.strip())
        if not has_source_audio:
            notes.append("this footage has no audio track, so the clip carries "
                         "only the music -- no speech was dropped, there was none")
        if has_source_audio:
            # Speech over music, not music over speech: the track carries the
            # cut, the words carry the clip.
            mix = (f"[0:a]atrim=duration={length:.3f},asetpts=N/SR/TB,volume=1.0[v];"
                   f"[v][m]amix=inputs=2:duration=first:weights=1 0.55[a]")
        else:
            mix = "[m]anull[a]"
        args += ["-filter_complex", f"{video};{music};{mix}",
                 "-map", "[vid]", "-map", "[a]"]
        notes.append(f"track mixed in from {float(at):.2f}s with a {fade:.1f}s fade out")
    elif overlays:
        args += ["-filter_complex", video, "-map", "[vid]"]
        if not silent:
            args += ["-map", "0:a?"]
    else:
        args += ["-vf", chain]

    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if silent:
        args += ["-an"]
        notes.append("ships silent: the sound is added on the platform"
                     + (", and the cut is on the bar so it will land"
                        if grid else ""))
    else:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args += [out]
    run(args, TIMEOUT_RENDER, "ffmpeg")
    # ffmpeg can exit 0 and write nothing worth having. What this returns is the
    # file as it is on disk, not the file we asked for.
    if not os.path.isfile(out) or os.path.getsize(out) < 1024:
        raise RuntimeError("ffmpeg finished but wrote no usable file")
    real = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", out], capture_output=True, text=True).stdout.strip()
    try:
        measured = round(float(real), 2)
    except ValueError:
        raise RuntimeError("ffmpeg wrote a file with no readable duration")
    if abs(measured - length) > 1.0:
        notes.append(f"asked for {length:.2f}s and the file is {measured:.2f}s")

    # O portão. Nenhum clipe sai daqui sem um mosaico do próprio render, porque
    # a verificação numérica aprovou um arquivo com dez defeitos visíveis e vai
    # aprovar o próximo: ela confere duração, resolução e regras da campanha, e
    # nenhum desses defeitos é um número. O arquivo pode existir sem o mosaico;
    # a ENTREGA não pode, e é quem chama que segura a entrega.
    # Camadas de texto SIMULTÂNEAS, não inputs de overlay. As cues não se
    # sobrepõem (o reflow garante), então o que divide um quadro é no máximo:
    # o degradê do rodapé, uma cue, e o hook. Contar os 14 inputs como 14
    # camadas foi o que fez o portão reprovar dois clipes corretos.
    style_facts["text_layers"] = (
        (1 if footer else 0)
        + (1 if style_facts.get("caption") else 0)
        + (1 if style_facts.get("hook") else 0))
    style_path = os.path.splitext(out)[0] + "-estilo.json"
    try:
        with open(style_path, "w", encoding="utf-8") as fh:
            json.dump(style_facts, fh, ensure_ascii=False, indent=1)
    except OSError as exc:
        style_path = None
        notes.append(f"could not write {os.path.basename(style_path or '')} "
                     f"beside this clip ({type(exc).__name__}), so a later "
                     f"`warden style check` on it has only the approximate "
                     f"pixel metrics, which reject nothing.")

    sheet = None
    try:
        sheet = S.contact_sheet(out, os.path.splitext(out)[0] + "-contato.jpg",
                                label=os.path.basename(out))
    except Exception as exc:
        notes.append(f"não consegui montar o contact sheet ({type(exc).__name__}: "
                     f"{exc}). Sem ele ninguém olhou este clipe -- não entregue.")
    if sheet is None and not any("contact sheet" in n for n in notes):
        notes.append("não consegui montar o contact sheet deste render. Sem ele "
                     "ninguém olhou este clipe -- não entregue.")
    # O render conta o que fez de si, e quem entrega confere antes do MEDIA:.
    breaches = S.check_sidecar(style_facts)
    for level, message in breaches:
        if level == "REJECT":
            notes.append(f"STYLE REJECT: {message}")
    return {"out": out, "duration_s": measured, "asked_s": round(length, 2),
            "notes": notes, "grid": grid, "sheet": sheet,
            "style": style_facts, "style_path": style_path,
            "style_breaches": [m for lv, m in breaches if lv == "REJECT"]}
