#!/usr/bin/env python3
"""From the campaign's own archive to a clip that already obeys the campaign.

The order here is not an implementation detail, it is the point. Footage comes
only from the links the brief publishes, the cut is chosen on text before any
video is opened, and the render takes its numbers from the rule set rather than
from a house default. A pipeline that picks its own footage or its own duration
produces a file that looks finished and gets the submission thrown out.

  warden fetch <url>                       a page as text, for reading a brief
  warden archive --campaign <id> --text-first   the WORDS: subs, or audio. First.
  warden transcribe <file>                 words with timing, subs if published
  warden digest --source <file>            the transcript a model can afford
  warden archive --campaign <id> --window <a>-<b>   only the seconds chosen
  warden cut <file> --start --end          one clip, inside the campaign's rules

The two downloads are in that order on purpose, and the order is the whole
point: `--text-first` costs 73 KB, `--window` a few MB, the whole file 361 MB
and 195s of transcription on top. Pulling the file first took 12min26s to the
first clip against 62s. `warden archive` with neither flag is the exception.
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
import warden_beat
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
    # `"youtube" in host` era o teste, e ele dizia sim para
    # `youtube.com.evil.example` e para `myyoutube.example`. Como `authorize`
    # decide pelo ID, bastava ao atacante pôr um id autorizado numa URL do
    # host dele para o agente baixar de lá e reportar "autorizado". O host é
    # comparado inteiro agora, normalizado pela mesma função que o resto do
    # arquivo já usava e que este caminho não usava.
    if _norm_host(host) == "youtube.com" and "youtu.be" in host:
        cand = parsed.path.lstrip("/").split("/")[0]
        return cand if _YT_ID.match(cand) else None
    if _norm_host(host) in ("youtube.com", "youtube-nocookie.com"):
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

def fiscal(url, rules=None, trusted=None):
    """O portão do download, nas DUAS formas que existem. (ok, porque, titulo).

    Com campanha, o acervo decide. Sem campanha, a lista de fontes do dono
    decide. Não há terceira forma e não há caminho de download que não passe por
    uma das duas -- e é por isso que a pergunta mora aqui, numa função só, em
    vez de cada chamador escolher o seu portão.

    Medido em 15/09, e é o motivo de esta função existir: o caminho barato
    (`--text-first`, `--window`) exigia `--campaign`, então quem mandava um link
    solto -- que é o caso comum -- não tinha caminho barato nenhum. O agente
    baixava o vídeo inteiro porque era a única porta aberta, e pagava 3min46s de
    transcrição por um texto que a legenda publicada entrega em 5s. Abrir o
    caminho barato para link confiável sem abrir uma porta lateral significa
    exatamente isto: o portão continua existindo, só muda quem responde.
    """
    if trusted is not None:
        ok, porque = trusted_check(url, trusted)
        return ok, porque, None
    return authorize(rules, url)


def archive_trusted(url, out_dir, entries, mode="video"):
    """O acervo de uma fonte que o dono avalizou, sem campanha. Um link, um arquivo.

    Mesma função que `archive()` cumpre para a campanha, com o outro portão.
    `mode="text"` é o caminho barato e é o padrão do fluxo: legenda publicada se
    houver, áudio se não houver, vídeo nunca.
    """
    ok, porque, _ = fiscal(url, trusted=entries)
    if not ok:
        raise RuntimeError(
            f"refusing to pull {url}: {porque}. Add the channel or the domain "
            f"with `warden trusted add <@channel|domain>` if it is a source you "
            f"vouch for.")
    os.makedirs(out_dir, exist_ok=True)
    return _download_one(url, out_dir, mode=mode)


def archive(rules, out_dir, limit=None, mode="video"):
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
            got.append(_download_one(url, out_dir, mode=mode))
        except Exception as exc:                       # one bad link, not a dead run
            failed.append((url, str(exc).splitlines()[0]))
    return got, failed


def _carimbo(segundos):
    """HH:MM:SS.mmm, que é o formato que `--download-sections` entende."""
    t = max(0.0, float(segundos))
    h, resto = divmod(t, 3600)
    m, seg = divmod(resto, 60)
    return f"{int(h):02d}:{int(m):02d}:{seg:06.3f}"


def archive_window(rules, out_dir, url, start, end, folga=2.0, trusted=None):
    """Baixa SÓ a janela pedida de um link do acervo. (caminho, in_point).

    LEIA ISTO ANTES DE MEXER NO DOWNLOAD. Esta função é as duas coisas ao mesmo
    tempo: ela derrubou o tempo até o primeiro clipe de 12min26s para 62s, e ela
    criou o pior defeito que este projeto entregou.

    O ganho: baixar 5 MB de uma janela em vez de 361 MB de um vídeo de 18
    minutos, e pular os 195s de transcrição usando a legenda publicada.

    O defeito: o arquivo que ela escreve tem RELÓGIO PRÓPRIO. O que era 179s da
    fonte virou 0s aqui. O SRT continua no relógio da fonte, e o `cut` filtrava
    as cues pelo tempo do arquivo -- então dois cortes do minuto três saíram com
    a legenda da ABERTURA do vídeo. Hook de um assunto, legenda de outro, nos
    dois. E cada portão disse que estava certo: hook cabendo, karaoke ligado,
    nenhuma cue pendurada, letra na faixa, duração igual à pedida, 273 testes
    verdes. Só o contact sheet pegou, porque uma pessoa olhou.

    Por isso a ficha `.origem.json` é escrita ao lado do arquivo e `cut` a
    EXIGE: um arquivo com relógio implícito não circula neste sistema. Se você
    criar outro caminho que produza recorte de vídeo -- outro formato, outra
    fonte, um cache -- ele tem de escrever a mesma ficha, ou `cut` vai recusar
    queimar legenda nele. Essa recusa é o recurso, não o obstáculo.
    

    O FISCAL VEM PRIMEIRO, e este parágrafo é o motivo de esta função existir
    em vez de um `--download-sections` solto em algum lugar. O caminho de
    download é onde a autorização de acervo é aplicada: `archive()` só puxa o
    que está em `sources.archive_urls`. Um atalho novo que aceitasse uma URL e
    baixasse um pedaço dela seria uma porta lateral por onde material não
    autorizado entra -- e um clipe rápido feito de material não autorizado é
    pior que um clipe lento, porque ele é reprovado DEPOIS das visualizações.
    Então a mesma pergunta que `archive()` faz é feita aqui, antes de qualquer
    byte descer.

    `folga` existe porque `--download-sections` corta em keyframe: pedir com
    dois segundos de sobra dos dois lados garante que a janela pedida está
    inteira dentro do arquivo. Devolve junto o `in_point`, que é onde a janela
    original começa DENTRO do arquivo baixado -- sem ele, quem cortar depois
    usa o tempo da fonte num arquivo que já não tem esse tempo.
    """
    ok, porque, titulo = fiscal(url, rules=rules, trusted=trusted)
    if not ok:
        raise RuntimeError(
            f"refusing to pull a window of {url}: {porque}. "
            + (f"(the video is {titulo!r}) " if titulo else "")
            + "Downloading a slice is still downloading: the archive gate is "
              "the same one, and a fast clip made of unauthorised footage is "
              "worse than a slow one.")
    os.makedirs(out_dir, exist_ok=True)
    de = max(0.0, float(start) - folga)
    ate = float(end) + folga
    # O nome carrega as DUAS pontas. Com só o início, `--window 181-201` e
    # `--window 181-300` davam o mesmo arquivo -- e o yt-dlp não rebaixa um
    # destino que já existe, então a segunda chamada devolvia o arquivo de 24s
    # da primeira e o comando imprimia "corte com --end 121" sobre ele.
    stem = "janela-%s-%d-%d" % (hashlib.sha256(url.encode()).hexdigest()[:10],
                                int(de * 1000), int(ate * 1000))
    template = os.path.join(out_dir, stem + ".%(ext)s")
    # `[protocol^=http]` NÃO é decoração. Sem ele o yt-dlp escolhe o HLS deste
    # vídeo, e `--download-sections` sobre m3u8 entrega um mp4 SEM FAIXA DE
    # VÍDEO, sem erro nenhum -- medido. Era o defeito que este caminho traria
    # de brinde, e ele seria descoberto num clipe entregue.
    run(_ytdlp() + ["--no-playlist", "--restrict-filenames",
                    "--download-sections", f"*{_carimbo(de)}-{_carimbo(ate)}",
                    "-f", "bv*[height<=1080][protocol^=http]+ba[protocol^=http]/"
                          "b[height<=1080][protocol^=http]/b[protocol^=http]",
                    "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD, "yt-dlp (window)")
    saiu = sorted(f for f in os.listdir(out_dir)
                  if f.startswith(stem)
                  and os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm"))
    if not saiu:
        raise RuntimeError(f"yt-dlp returned no file for the {de:.0f}-{ate:.0f}s "
                           f"window of that link")
    caminho = os.path.join(out_dir, saiu[0])
    _confere_janela(caminho, de, ate)
    # A ORIGEM VIAJA COM O ARQUIVO, e este é o conserto de um defeito que
    # chegou a dois clipes entregues.
    #
    # O arquivo de janela tem relógio PRÓPRIO: o que era 179s da fonte virou 0s
    # aqui. O SRT continua no relógio da fonte. Nada casava os dois, e o `cut`
    # filtrava as cues pelo tempo do ARQUIVO -- então um corte do minuto três
    # saiu com a legenda da abertura do vídeo, nos dois clipes, e cada portão
    # que existe disse que estava tudo certo: hook cabendo, karaoke ligado,
    # nenhuma cue pendurada, letra na faixa, duração igual à pedida.
    #
    # Um arquivo com relógio implícito não pode circular neste sistema. Então
    # ele passa a carregar de onde veio, ao lado dele, e quem consome exige
    # isso: `cut` que receba um arquivo de janela SEM esta ficha recusa queimar
    # legenda, em vez de queimar a legenda errada.
    _escreve_origem(caminho, url, de, ate, start, end)
    return caminho, max(0.0, float(start) - de)


def _confere_janela(caminho, de, ate):
    """A faixa de vídeo existe mesmo? Escrita uma vez, para os dois caminhos.

    A resposta já foi NÃO, calada: `--download-sections` sobre HLS entrega um
    mp4 sem faixa de vídeo e sem erro. Um arquivo assim atravessa todo o resto
    do sistema e vira um clipe entregue. Apagar é higiene; a mensagem é o que
    importa.
    """
    sonda = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", caminho],
        capture_output=True, text=True)
    if sonda.returncode != 0 or not sonda.stdout.strip():
        try:
            os.remove(caminho)
        except OSError:
            pass
        raise RuntimeError(
            f"the {de:.0f}-{ate:.0f}s window that came down has no video track "
            f"(ffprobe found none). That is what `--download-sections` does over "
            f"an HLS stream, and it does it silently. The file was deleted "
            f"rather than handed on.")


def _escreve_origem(caminho, url, de, ate, start, end):
    """A ficha que faz o relógio do recorte ser explícito. Ver `archive_window`."""
    with open(caminho + ".origem.json", "w", encoding="utf-8") as fh:
        json.dump({"url": url,
                   "source_start": round(de, 3),
                   "source_end": round(ate, 3),
                   "in_point": round(max(0.0, float(start) - de), 3),
                   "asked_start": round(float(start), 3),
                   "asked_end": round(float(end), 3)}, fh, indent=1)


def archive_windows(rules, out_dir, url, janelas, folga=2.0, trusted=None):
    """Várias janelas do mesmo link, numa execução só do yt-dlp. [(caminho, in_point)].

    Medido em 15/09, no vídeo de 18 minutos: duas janelas em duas execuções
    custam 38s; as mesmas duas numa execução custam **30s**. O ganho é de uma
    negociação de conexão a menos, e é o único ganho de download que sobrou
    depois de medir.

    E aqui está o motivo de o resto não ter ganho: `--download-sections` obriga
    o yt-dlp a usar o ffmpeg como downloader, e o ffmpeg **remuxa a seção em
    tempo real**, a cerca de 1,8x. Uma janela de 28s custa uns 17s de ffmpeg
    independentemente da banda. Por isso duas janelas (30s) custam mais que o
    arquivo inteiro deste vídeo (13s): a janela economiza DISCO -- 14 MB contra
    382 MB -- e só economiza tempo quando a fonte é longa o bastante para o
    download inteiro passar dos 30s.

    `--download-sections` aceita ser repetido -- está na documentação do
    yt-dlp -- mas o nome de saída então colide entre as seções, e a
    documentação não trata desse caso. Por isso o template carrega
    `%(section_start)s`, que é o campo que o próprio yt-dlp expõe para isto.
    """
    ok, porque, titulo = fiscal(url, rules=rules, trusted=trusted)
    if not ok:
        raise RuntimeError(
            f"refusing to pull windows of {url}: {porque}. "
            + (f"(the video is {titulo!r}) " if titulo else "")
            + "Downloading slices is still downloading: the gate is the same "
              "one, and a fast clip made of unauthorised footage is worse than "
              "a slow one.")
    if not janelas:
        raise RuntimeError("archive_windows was given no window to pull")
    os.makedirs(out_dir, exist_ok=True)
    pedidos = []
    secoes = []
    for start, end in janelas:
        de = max(0.0, float(start) - folga)
        ate = float(end) + folga
        pedidos.append((float(start), float(end), de, ate))
        secoes += ["--download-sections", f"*{_carimbo(de)}-{_carimbo(ate)}"]
    marca = hashlib.sha256(url.encode()).hexdigest()[:10]
    template = os.path.join(
        out_dir, f"janela-{marca}-%(section_start)s-%(section_end)s.%(ext)s")
    run(_ytdlp() + ["--no-playlist", "--restrict-filenames", *secoes,
                    "-f", "bv*[height<=1080][protocol^=http]+ba[protocol^=http]/"
                          "b[height<=1080][protocol^=http]/b[protocol^=http]",
                    "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD * max(1, len(janelas)), "yt-dlp (windows)")

    saiu = sorted(f for f in os.listdir(out_dir)
                  if f.startswith(f"janela-{marca}-")
                  and os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm"))
    resultados = []
    for start, end, de, ate in pedidos:
        # O yt-dlp arredonda o carimbo no nome, então o arquivo é casado pelo
        # início mais próximo e não por igualdade de string -- casar por string
        # devolveria "nenhum arquivo" com o arquivo no disco.
        melhor, dist = None, None
        for nome in saiu:
            partes = os.path.splitext(nome)[0].split("-")
            try:
                inicio = float(partes[-2])
            except (ValueError, IndexError):
                continue
            d = abs(inicio - de)
            if dist is None or d < dist:
                melhor, dist = nome, d
        if melhor is None or dist > 1.5:
            raise RuntimeError(
                f"yt-dlp returned no file for the {start:.0f}-{end:.0f}s window "
                f"of that link (it wrote {saiu or 'nothing'})")
        caminho = os.path.join(out_dir, melhor)
        _confere_janela(caminho, de, ate)
        _escreve_origem(caminho, url, de, ate, start, end)
        resultados.append((caminho, max(0.0, start - de)))
    return resultados


def origem_da_janela(source):
    """(ficha, motivo). A origem de um arquivo que é recorte de outro.

    `None, None` para um arquivo comum. `None, motivo` quando o arquivo PARECE
    ser uma janela e a ficha não está lá ou não dá para ler -- que é o caso em
    que quem consome tem de parar, não adivinhar.
    """
    caminho = str(source or "")
    ficha = caminho + ".origem.json"
    if os.path.isfile(ficha):
        try:
            with open(ficha, encoding="utf-8") as fh:
                dados = json.load(fh)
            if "source_start" in dados:
                return dados, None
            return None, f"{os.path.basename(ficha)} has no source_start"
        except Exception as exc:
            return None, (f"{os.path.basename(ficha)} is not readable "
                          f"({type(exc).__name__})")
    if os.path.basename(caminho).startswith("janela-"):
        return None, (f"{os.path.basename(caminho)} was cut out of a longer "
                      f"source -- its clock starts at that window, not at the "
                      f"source's zero -- and the .origem.json that says where "
                      f"it came from is missing")
    return None, None


# As línguas de legenda que valem a pena pedir, e por que são só estas.
#
# Cada língua na lista é uma requisição a mais, e cada requisição a mais é uma
# chance a mais de 429. `en` sozinho já derrubou o `archive` duas vezes num
# vídeo em português. Então a lista é curta e o ambiente pode trocá-la.
SUB_LANGS = os.environ.get("WARDEN_SUB_LANGS") or "pt,pt-BR"


def _pull_subs(url, out_dir, stem, template, playlist_args):
    """(caminho do .srt, motivo de não ter vindo). Falhar aqui não é fatal.

    Corrida própria, de propósito. Uma legenda que não existe, ou um 429 do
    YouTube, não pode impedir o vídeo de baixar -- e era exatamente isso que
    acontecia quando as duas coisas vinham no mesmo comando.
    """
    def _achadas():
        return [f for f in sorted(os.listdir(out_dir))
                if f.startswith(stem) and f.lower().endswith(".srt")]

    ja_tinha = _achadas()
    try:
        run(_ytdlp() + [*playlist_args, "--restrict-filenames", "--skip-download",
             "--write-auto-subs", "--write-subs", "--sub-langs", SUB_LANGS,
             "--convert-subs", "srt", "-o", template, "--", url],
            TIMEOUT_FETCH * 4, "yt-dlp (subtitles)")
    except RuntimeError as exc:
        # Uma corrida que falhou não apaga o que já estava no disco. Se a
        # legenda de uma corrida anterior está aqui, ela serve.
        if ja_tinha:
            return os.path.join(out_dir, ja_tinha[0]), None
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"
    # O que EXISTE, não o que é novo. A versão anterior comparava o diretório
    # antes e depois, e na segunda corrida o `.srt` já estava lá: não aparecia
    # como novo, e a ferramenta respondia "este vídeo não publica legenda"
    # com a legenda em disco. Um arquivo que já está aqui é um arquivo que
    # temos.
    achadas = _achadas()
    if not achadas:
        return None, "this video publishes no subtitle in " + SUB_LANGS
    return os.path.join(out_dir, achadas[0]), None


def _pull_text_first(url, out_dir, stem, template, playlist_args):
    """O caminho barato: legenda publicada, ou o áudio -- nunca o vídeo.

    Medido em 14/09 no vídeo do teste: a legenda desce em 4s e 73 KB; o áudio
    inteiro em 4s e 15 MB; o vídeo inteiro em 16s e 361 MB. E transcrever custa
    195s, que é o que a legenda publicada economiza.

    Escolher a janela é trabalho de TEXTO. Baixar 361 MB para descobrir onde
    cortar é pagar o vídeo antes de saber se vai usá-lo.
    """
    legenda, porque = _pull_subs(url, out_dir, stem, template, playlist_args)
    if legenda:
        return legenda
    # Sem legenda publicada, o texto ainda sai barato: transcrever 15 MB de áudio
    # dá o mesmo resultado que transcrever 361 MB de vídeo, e o whisper só ouve.
    print(f"  ({porque}, so pulling the audio to transcribe instead of the "
          f"video: measured 15 MB against 361 MB for the same words.)",
          file=sys.stderr)
    run(_ytdlp() + [*playlist_args, "--restrict-filenames",
         "--max-filesize", str(MAX_DIRECT_BYTES), "-f", "ba",
         "-o", template, "--", url], TIMEOUT_DOWNLOAD, "yt-dlp (audio)")
    audios = sorted(f for f in os.listdir(out_dir)
                    if f.startswith(stem)
                    and os.path.splitext(f)[1].lower() in
                    (".m4a", ".webm", ".opus", ".mp3", ".ogg"))
    if not audios:
        raise RuntimeError(
            "yt-dlp returned neither a subtitle nor an audio track for that "
            "link, so there is no text to choose a window from.")
    return os.path.join(out_dir, audios[0])


def _download_one(url, out_dir, mode="video"):
    """`mode="video"` baixa a fonte; `mode="text"` baixa só o que dá as palavras."""
    url = safe_url(url)
    parsed = urlparse(url)
    direct = os.path.splitext(parsed.path)[1].lower() in (
        ".mp4", ".mov", ".m4v", ".webm", ".mkv")
    if mode == "text" and (direct or "drive.google.com" in parsed.netloc):
        # Um arquivo solto não publica legenda e não tem faixa de áudio
        # separada para pedir: não há caminho barato aqui. Dizer isso é melhor
        # que baixar o arquivo inteiro fingindo que é o modo barato.
        raise RuntimeError(
            f"{url} is a plain file, not a hosted video: there is no published "
            f"subtitle to fetch and no audio-only stream to ask for. Pull it "
            f"whole with `warden archive` and transcribe that.")
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
    # `have("yt-dlp")` perguntava pelo BINÁRIO no PATH. O venv da imagem instala
    # o yt-dlp como módulo, e o PATH que um subprocesso herda da árvore de
    # supervisão não tem o bin do venv -- então em máquina limpa o `archive`
    # morria dizendo "yt-dlp is not installed" com o yt-dlp instalado. É a
    # mesma armadilha que `_ytdlp()` já resolvia para os caminhos de consulta e
    # que os de download não usavam. Agora a pergunta é se dá para IMPORTAR.
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        if not have("yt-dlp"):
            raise RuntimeError(
                "yt-dlp is neither importable by this interpreter nor on PATH, "
                "so this link cannot be pulled.")
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
    if mode == "text":
        return _pull_text_first(url, out_dir, stem, template, playlist_args)
    # A legenda NÃO é pedida junto com o vídeo, e essa separação é um conserto,
    # não um estilo. Medido em 14/09: `--sub-langs pt,pt-BR,en` numa só execução
    # faz o YouTube responder 429 no `en`, e o 429 derruba a execução inteira
    # ANTES de baixar o vídeo. Reproduzido duas vezes. Qualquer fonte sem legenda
    # em inglês quebrava o `archive`, e a mensagem falava de legenda, não de
    # vídeo, então o defeito lia como problema da fonte.
    #
    # Agora são duas corridas: a legenda tem a sua, pode falhar, e a falha vira
    # aviso. O vídeo tem a sua e não depende de legenda nenhuma.
    legenda, porque = _pull_subs(url, out_dir, stem, template, playlist_args)
    run(_ytdlp() + [*playlist_args, "--restrict-filenames",
         "--max-filesize", str(MAX_DIRECT_BYTES),
         "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
         "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD, "yt-dlp")
    if not legenda and porque:
        print(f"  (no published subtitle came down for {stem}: {porque}. The "
              f"cut will need `warden transcribe`, which costs minutes rather "
              f"than seconds.)", file=sys.stderr)
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
            # O filtro antigo era `not l.strip().isdigit()`, para tirar o número
            # do índice do SRT, e comia qualquer linha do corpo que fosse só
            # dígitos. Reproduzido: as cues "custou" / "5000" / "reais por mês"
            # voltavam como ['custou', 'reais por mes'] -- a cue do meio sumia
            # inteira, sem uma linha em lugar nenhum. O índice é POSIÇÃO, não
            # conteúdo: ele é o que vem antes do timestamp, e VTT nem tem
            # índice, então ali o filtro só fazia mal.
            head = block.index(stamps[0])
            body = " ".join(l for l in block[head + 1:] if "-->" not in l)
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


_TEM_FILTRO = {}


def ffmpeg_tem_filtro(nome):
    """Se este ffmpeg tem o filtro `nome`. Perguntado ao binário, não suposto.

    A legenda passou a ser ASS queimado pelo libass, e libass é uma opção de
    compilação: o ffmpeg da imagem tem (`--enable-libass`), o do Homebrew de um
    Mac normalmente não. Descobrir isso com o clipe pronto e sem legenda é tarde
    demais, e supor que tem é como o `force_style` nunca renderizou nada.
    """
    if nome in _TEM_FILTRO:
        return _TEM_FILTRO[nome]
    try:
        saida = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                               capture_output=True, text=True, timeout=60).stdout
    except Exception:
        saida = ""
    _TEM_FILTRO[nome] = bool(re.search(rf"^\s*\S+\s+{re.escape(nome)}\s+", saida,
                                       re.M))
    return _TEM_FILTRO[nome]


def _ff_valor(texto):
    """Um caminho seguro para ir dentro de uma opção de filtro do ffmpeg.

    O filtergraph corta em `,` `;` `[` `]` e a opção corta em `:`. Um caminho com
    qualquer um deles vira dois filtros inventados e uma mensagem de erro que não
    fala de caminho nenhum.
    """
    saida = str(texto).replace("\\", "\\\\")
    for c in (":", "'", ",", ";", "[", "]"):
        saida = saida.replace(c, "\\" + c)
    return saida


def _k_texto(cue, visivel_de=None, visivel_ate=None):
    """O texto da cue com um `\\k` por palavra, ou None se os tempos não vierem.

    `\\k<centésimos>` é o que faz o destaque andar palavra a palavra: o libass
    pinta a palavra de SecondaryColour para PrimaryColour quando o relógio dela
    chega. Os centésimos vêm do reflow, repartidos por sílaba.

    `visivel_de`/`visivel_ate` são a janela da cue que SOBRA depois do recorte,
    no tempo da própria cue, e existem por um defeito medido. Um corte que
    começa no meio de um segmento faz a primeira cue ser cortada na cabeça: ela
    fica 1,4s na tela carregando 2,9s de `\\k`. O libass conta o `\\k` a partir
    do início do Dialogue, então o amarelo andava um segundo e meio atrás da
    boca e três das oito palavras nunca acendiam antes de a cue sair.

    Palavra que já foi dita antes da janela sai com `\\k0`, que o libass pinta
    no primeiro quadro: ela JÁ está amarela quando a cue aparece, que é a
    verdade -- aquela palavra já foi falada. A soma dos `\\k` passa a ser a
    duração visível, e `soma_k` existe para um teste conferir isso.

    Devolve None -- e não um texto pela metade -- quando a contagem de palavras
    dos tempos não bate com a das linhas. Uma legenda com palavra faltando é
    exatamente o tipo de perda silenciosa que este arquivo inteiro combate, e
    quem chama trata o None dizendo o que aconteceu.
    """
    palavras = list(cue.get("words") or [])
    linhas = [l for l in (cue.get("lines") or []) if l.strip()]
    if not palavras or not linhas:
        return None
    if sum(len(l.split()) for l in linhas) != len(palavras):
        return None
    de = float(cue.get("start", 0.0)) if visivel_de is None else float(visivel_de)
    ate = float(cue.get("end", 0.0)) if visivel_ate is None else float(visivel_ate)
    saida, i = [], 0
    for linha in linhas:
        pedaco = []
        for _ in linha.split():
            p = palavras[i]
            i += 1
            # A fatia desta palavra que cai DENTRO do que vai aparecer.
            a = max(float(p["start"]), de)
            b = min(float(p["end"]), ate)
            centesimos = max(0, int(round((b - a) * 100)))
            pedaco.append("{\\k%d}%s" % (centesimos, _ass_limpo(p["w"])))
        saida.append(" ".join(pedaco))
    return "\\N".join(saida)


def soma_k(texto_ass):
    """Os centésimos de `\\k` somados, para quem precisa conferir a sincronia."""
    return sum(int(n) for n in re.findall(r"\{\\k(\d+)\}", str(texto_ass or "")))


def _ass_limpo(texto):
    """Um `\\` abre tag e um `{` abre bloco de override. Nenhum dos dois é fala."""
    return " ".join(str(texto).replace("\\", "").replace("{", "(")
                    .replace("}", ")").split())


def to_ass(segments, width, height, safe=None, offset=0.0, length=None,
           perdas=None):
    """Um transcript vira ASS: reflow primeiro, depois `ass_from_cues`.

    Continua sendo a porta de entrada para quem tem segmentos crus na mão. Quem
    já reflowou -- o `cut`, que precisa das mesmas cues para contar linhas e
    finais pendurados -- chama `ass_from_cues` direto, para não reflowar duas
    vezes e correr o risco de as duas passadas discordarem.
    """
    return ass_from_cues(S.reflow_cues(segments), width, height, safe=safe,
                         offset=offset, length=length, perdas=perdas)


def ass_from_cues(cues, width, height, safe=None, offset=0.0, length=None,
                  perdas=None):
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
    fontsize = S.caption_size(height)
    outline = 3
    margin_l = max(10, x0)
    margin_r = max(10, width - x1)
    # O piso da legenda não pode depender só do `y1` das regras. Ele vem em
    # pixels da resolução DECLARADA, e num render 720x1280 com o padrão de
    # 1586 a conta dá negativa: o `max(10, ...)` então punha a legenda a 10px
    # do fundo, dentro da interface do app, e nada acusava. E mesmo em
    # 1080x1920 o padrão deixava a legenda terminando a ~60px do começo da
    # interface de baixo -- dentro da regra, encostada nela. O piso passa a
    # ser a margem MAIS a folga, escalado por este quadro; `y1` só pode subir
    # a legenda, nunca baixá-la para dentro da faixa do app.
    margin_v = max(S.caption_piso(height), height - y1)
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
        # PrimaryColour é a palavra JÁ falada e SecondaryColour a que ainda
        # vem: é assim que o `\\k` pinta, e é o contrário do que os nomes
        # sugerem. Amarelo depois, branco antes -- escolha do dono em 14/09.
        f"Style: Default,Anton,{fontsize},{S.COR_FALADA},{S.COR_POR_FALAR},"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,"
        f"{margin_l},{margin_r},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n")
    offset = float(offset or 0.0)
    rows = []
    sem_k = []
    for seg in cues or []:
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
        # O destaque palavra a palavra. Quando os tempos vieram, cada palavra
        # entra com o seu `\\k`; quando não vieram, a cue entra inteira e é
        # FORÇADA a branco -- sem `\\k` o libass pinta a linha toda de
        # PrimaryColour, que aqui é o amarelo do "já falado", e a cue sairia
        # amarela do início ao fim sem ninguém pedir.
        # O recorte acima mudou a janela da cue; o `\k` tem de ser recalculado
        # nela, senão o destaque anda atrás da voz pelo tanto que foi cortado.
        corpo = _k_texto(seg, visivel_de=start + offset, visivel_ate=end + offset)
        if corpo is None:
            sem_k.append(text[:40])
            linhas = [_ass_limpo(l) for l in (seg.get("lines") or [text])]
            corpo = ("{\\c%s&}" % S.COR_POR_FALAR.rstrip("&")
                     + "\\N".join(l for l in linhas if l))
        rows.append((start, end, corpo))
    rows.sort(key=lambda r: r[0])
    body = "".join(
        f"Dialogue: 0,{_ass_stamp(a)},{_ass_stamp(b)},Default,,0,0,0,,{t}\n"
        for a, b, t in rows)
    if sem_k and perdas is not None:
        perdas.append(f"{len(sem_k)} cue(s) ficaram sem destaque palavra a "
                      f"palavra porque os tempos por palavra não bateram com as "
                      f"linhas: {'; '.join(sem_k[:3])}")
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
        shots=None, language=None, motion=True, cover_footer=None,
        asked_s=None):
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
    # O número que a PESSOA pediu, e ele vem antes da campanha de propósito.
    #
    # Em 14/09 o dono pediu "20 segundos" e recebeu 20,6 e 22,2. A ferramenta
    # não errou: ela entregou exatamente a janela que lhe passaram. O que não
    # existia era um caminho para o pedido do usuário chegar até aqui -- o
    # agente alinhava a janela nas fronteiras da transcrição e nunca voltava ao
    # número. Um pedido que não vira parâmetro é um pedido que ninguém cumpre.
    #
    # Então: quando `asked_s` vem, a janela é ajustada para ele, mantendo o
    # começo. A campanha ainda manda -- os dois clamps abaixo rodam depois --
    # e quando ela obriga a sair do número, a nota diz qual regra obrigou.
    pedido = None if asked_s is None else max(0.1, float(asked_s))
    # Qual regra da campanha venceu o pedido da pessoa, quando alguma venceu.
    # Sem isto o portão de entrega reprovava um clipe que a PRÓPRIA campanha
    # mandou encurtar, dizendo que nenhuma regra justificava o desvio -- com a
    # nota logo acima nomeando a regra. A nota e o portão discordavam.
    venceu_o_pedido = None
    if pedido is not None and abs(length - pedido) > 0.05:
        notes.append(f"the person asked for {pedido:.0f}s and the window given "
                     f"was {length:.2f}s, so the cut was moved to {pedido:.0f}s "
                     f"from the same start. A number a person says is the "
                     f"request, not a suggestion.")
        length = pedido
    if hi is not None and length > hi:
        notes.append(f"trimmed from {length:.1f}s to the {hi}s maximum"
                     + (f" -- this campaign's limit overrides the {pedido:.0f}s "
                        f"that was asked for, and that is why the file is not "
                        f"{pedido:.0f}s" if pedido is not None else ""))
        if pedido is not None:
            venceu_o_pedido = f"video.duration_max_s = {hi}"
        length = float(hi)
    if lo is not None and length < lo:
        notes.append(f"extended from {length:.1f}s to the {lo}s minimum"
                     + (f" -- this campaign's floor overrides the {pedido:.0f}s "
                        f"that was asked for, and that is why the file is not "
                        f"{pedido:.0f}s" if pedido is not None else ""))
        if pedido is not None:
            venceu_o_pedido = f"video.duration_min_s = {lo}"
        length = float(lo)

    # An edit is cut to the bar, and it is cut to the bar even when the file
    # ships silent. The platform's own sound player starts where you tell it,
    # so a clip that is a whole number of bars long still lands on the beat
    # once the track is added in the app, which is the only way a campaign that
    # bans embedded audio can have an edit at all.
    grid = None
    if track:
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
            # Um lado nomeado é o dono dizendo onde o sujeito está, e isso é
            # honrado. Mas continua sendo um lado, não uma medição: a nota diz
            # isso em vez de deixar parecer que alguém conferiu.
            notes.append(f"framed by crop {crop} as given, NOT measured: there "
                         f"is no face detector on this machine ({why}), so "
                         f"nothing checked that the subject is on that side. "
                         f"The contact sheet is the only check this clip got.")
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
    # ------------------------------------------------------ decupagem
    #
    # A lista de planos, encaixada na batida. `planos` é [(in, out, batidas)]
    # no relógio da FONTE; `length` passa a ser a soma das durações, porque um
    # edit dura o que os planos dele somam e não o que a janela media.
    planos = []
    if shots:
        pedidos = []
        for plano in shots:
            if isinstance(plano, dict):
                a = plano.get("in", plano.get("start"))
                b = plano.get("out", plano.get("end"))
            else:
                a, b = plano[0], plano[1]
            if a is None or b is None or float(b) <= float(a):
                raise RuntimeError(
                    f"a shot needs an in and an out, and out after in; got "
                    f"{plano!r}. A shot list that does not describe a shot is "
                    f"not something to render past.")
            pedidos.append((float(a), float(b)))
        planos = warden_beat.snap_shots(pedidos, (grid or {}).get("beat_s"))
        length = round(sum(b - a for a, b, _ in planos), 3)
        bordas = []
        acc = 0.0
        for a, b, _ in planos[:-1]:
            acc += (b - a)
            bordas.append(round(acc, 4))
        notes.append(
            f"{len(planos)} shots spliced in order, {length:.2f}s in total"
            + (f", each a whole number of beats ("
               + ", ".join(str(n or "?") for _, _, n in planos) + ")"
               if (grid or {}).get("beat_s") else
               ", with no beat grid to snap to -- durations are as asked"))

    try:
        # Com planos, os quadros vêm DOS PLANOS. Amostrar start..start+length
        # num edit olha um trecho contínuo que o clipe não mostra -- e foi
        # exatamente assim que uma legenda do próprio vídeo passou pela
        # detecção e apareceu atrás da nossa.
        looked = (S.sample_shots(source, planos, count=8) if planos
                  else S.sample_frames(source, start, length, count=8))
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
    # `shots` deixou de ser aceito-e-descartado. Até 15/09 este bloco escrevia
    # uma nota dizendo "IGNOREI os N planos deste clipe", e era a verdade: o
    # campo atravessava a assinatura inteira e nenhuma linha do render o lia.
    # A decupagem agora é feita mais abaixo, em `_decupagem()`, e o que sobra
    # aqui é o zoom da janela contínua -- que é o caso SEM planos.
    if motion and not planos:
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

    # A legenda do acervo não fica na borda: no vlog de 14/09 ela está a 69% da
    # altura da fonte, que é o meio do quadro e é exatamente onde a nossa cai
    # depois do recorte. `burned_text_bands` olha os 16% de cima e os 16% de
    # baixo e por construção não podia vê-la -- a conclusão de que o limiar
    # estava alto demais era falsa. Esta varredura olha a faixa inteira que o
    # corte vai manter.
    if kept and sw:
        _e = fx * (1 - kept)
        acervo_faixas = S.legenda_do_acervo(
            looked, recorte=(int(_e * sw), int((_e + kept) * sw)))
    else:
        acervo_faixas = S.legenda_do_acervo(looked)
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
    overlays = []                     # (png, y, de, ate, fade_de_saida_s)
    art_dir = os.path.dirname(os.path.abspath(out)) or "."
    stem = os.path.splitext(os.path.basename(out))[0]
    # O que este render fez consigo mesmo, gravado ao lado dele. `style check`
    # lê daqui em vez de tentar recuperar dos pixels: a largura do hook aqui é a
    # que o PIL mediu com a fonte real, e não uma estimativa que confunde letra
    # com letreiro de neon.
    style_facts = {"frame": {"w": width, "h": height},
                   "margins": S.margens(width, height),
                   "source_caption_bands": [
                       {"y0": round(f["y0"], 3), "y1": round(f["y1"], 3),
                        "span": round(f["span"], 3), "frames": f["frames"]}
                       for f in acervo_faixas],
                   "hook": None, "caption": None, "footer_covered": False,
                   "motion": bool(motion), "source_text": source_text,
                   "duration_s": round(float(length), 2),
                   "asked_s": None if pedido is None else round(pedido, 2),
                   "asked_overridden_by": venceu_o_pedido}

    footer = None
    # A decisão de COBRIR o rodapé do acervo mudou de lugar, e o lugar é o
    # conserto. Ela era tomada aqui em cima, com `caption_srt` existindo no
    # disco como única prova de que haveria legenda nossa -- e os três portões
    # abaixo (arquivo aprovado, idioma, libass) ainda podiam derrubar a legenda
    # DEPOIS. Quando derrubavam, o degradê já tinha apagado a legenda do acervo
    # em troca de nada, e a nota afirmava uma disputa que não houve. Agora ela
    # roda depois dos portões, onde `cues`/`ass_path` dizem o que de fato foi
    # queimado. A ordem das CAMADAS não muda com isso: a montagem lá embaixo
    # separa o degradê por identidade (`o[0] == footer`) e não por posição.
    if source_text.get("top") and hook:
        # Era um pedido para olhar o mosaico, e o mosaico é justamente o portão
        # que falha quando ninguém olha. Com texto do acervo no topo E hook
        # nosso, são dois textos no mesmo lugar: não é gosto, é ilegível.
        raise RuntimeError(
            f"this footage already carries burned text along the TOP "
            f"({source_text['evidence']}), and this cut puts a hook there too. "
            f"Two texts in the same band is not a judgement call, it is "
            f"unreadable. Cut without --hook, or choose a window whose top is "
            f"clean.")
    if source_text.get("top"):
        notes.append("this footage carries burned text along the TOP "
                     f"({source_text['evidence']}). No hook was asked for, so "
                     "nothing of ours lands on it.")
    elif source_text.get("top_suspect"):
        # Em cima não existe o lado barato que existe embaixo. O degradê do
        # rodapé cobre a faixa de baixo por quase nada; no topo não há degradê
        # nenhum, e parar o corte por uma SUSPEITA seria matar o clipe para
        # resolver um talvez. Então aqui a suspeita vira o que ela é -- uma
        # linha que manda olhar o mosaico -- e não um portão.
        notes.append(
            "this footage MAY carry burned text along the TOP (suspect, not "
            f"the hard boolean: {', '.join(source_text.get('top_why') or ['-'])}"
            f"; {source_text['evidence']})."
            + (" The hook goes there too: look at the top band on the contact "
               "sheet before delivering, and re-cut without --hook or on "
               "another window if there are two texts in it."
               if hook else
               " No hook was asked for, so nothing of ours lands on it."))

    # ------------------------------------------------- de que relógio é este arquivo
    #
    # Se a fonte é uma janela recortada de um vídeo maior, o zero dela não é o
    # zero da fonte, e TODA conta que envolve a legenda tem de ser feita no
    # relógio da FONTE: o SRT está nele, a aprovação foi dada nele, e a janela
    # que o espectador vai ver está nele. Sem isso a legenda sai de outro
    # trecho -- aconteceu, nos dois clipes de uma medição, e nenhum portão viu.
    ficha, porque_ficha = origem_da_janela(source)
    fonte_zero = float((ficha or {}).get("source_start") or 0.0)
    if porque_ficha and caption_srt:
        raise RuntimeError(
            f"refusing to burn captions onto {os.path.basename(source)}: "
            f"{porque_ficha}. Without it there is no way to line the subtitle's "
            f"timestamps up with this file, and a caption from the wrong part "
            f"of the video looks finished and says things the person never said "
            f"at that moment. Cut it without --subtitles, or pull the window "
            f"again with `warden archive --window`, which writes the origin "
            f"beside the file.")
    if ficha:
        notes.append(
            f"this source is the {ficha['source_start']:.1f}-"
            f"{ficha['source_end']:.1f}s window of a longer file, so the caption "
            f"is lined up on the SOURCE clock: this cut burns "
            f"{fonte_zero + float(start):.1f}-"
            f"{fonte_zero + float(start) + length:.1f}s of it.")

    burn_reason = None
    # A faixa onde a NOSSA legenda vai cair, em fração da altura: do piso para
    # cima, duas linhas de entrelinha. Se o acervo já escreve ali, queimar por
    # cima é a legenda dupla -- e aqui o degradê do rodapé não salva, porque
    # cobrir 70% da altura cobriria o assunto do clipe.
    _piso = S.caption_piso(height)
    _linha = int(S.caption_size(height) * S.LEADING)
    nossa_faixa = ((height - _piso - 2 * _linha) / height,
                   (height - _piso) / height)
    acervo_na_nossa_faixa = [
        f for f in acervo_faixas
        if f["y1"] >= nossa_faixa[0] - 0.03 and f["y0"] <= nossa_faixa[1] + 0.03]
    style_facts["source_caption_clash"] = bool(acervo_na_nossa_faixa)

    cues = []
    fala_apos_o_corte = False
    # Onde a frase que o corte parte realmente fecha, em segundos do clipe.
    # É o número que faltava: quando alguém pede 20 segundos e a fala não cabe
    # em 20, a única saída que a ferramenta oferecia era "mova o --end", que é
    # mandar adivinhar. Com este número a escolha é entre dois valores, e a
    # pessoa pode ouvir a frase inteira em uma linha sem jargão nenhum.
    frase_fecha_em = None
    if caption_srt and os.path.exists(caption_srt) and acervo_na_nossa_faixa:
        f = acervo_na_nossa_faixa[0]
        burn_reason = (
            f"not burning captions: this footage already burns its own, at "
            f"{f['y0']:.0%}-{f['y1']:.0%} of the frame height, which is where "
            f"ours would go ({f['frames']} of {len(looked)} sampled frames, "
            f"{f['span']:.0%} of the kept width). Two captions in one frame is "
            f"the reject; the gradient footer cannot help this one, because "
            f"that band is the middle of the picture and covering it would "
            f"cover the subject. The speech is already on screen, so this clip "
            f"ships with the footage's own captions and no second set. Say that "
            f"to the person in one line -- \"esse vídeo já vem legendado, então "
            f"não pus legenda em cima\" -- and nothing about bands or frames.")
    elif caption_srt and os.path.exists(caption_srt):
        if R.get(rules, "sources.archive_has_captions") is True:
            # The campaign says this archive already burns its own captions.
            # Burning ours over them is the doubling the owner set this flag to
            # prevent, so the tool refuses it here rather than leaving it to be
            # caught by eye on the first clip.
            burn_reason = ("not burning captions: this campaign's archive already "
                           "carries its own (sources.archive_has_captions is true)")
        else:
            # A janela QUE VAI QUEIMAR, no tempo da fonte -- não o arquivo.
            # Perguntar pelo arquivo era a porta por onde `aromasas` passou.
            #
            # Com planos, não há UMA janela: há várias, e a aprovação tem de
            # cobrir todas. Perguntar só pela primeira e pela última aprovaria
            # de brinde tudo o que está entre elas, inclusive material que este
            # corte nem mostra.
            if planos:
                queima_de = fonte_zero + min(a for a, _b, _n in planos)
                queima_ate = fonte_zero + max(b for _a, b, _n in planos)
            else:
                queima_de = fonte_zero + float(start)
                queima_ate = queima_de + length
            approved, why = S.approval_state(
                caption_srt, start=queima_de, end=queima_ate)
            if not approved:
                # O portão do defeito 2.5. Sem aprovação o clipe sai SEM legenda,
                # em vez de sair com a palavra errada queimada: um clipe mudo se
                # conserta, um `jokovic jokovic` no vídeo publicado não.
                burn_reason = f"not burning captions: {why}"
            else:
                rows = _read_srt(caption_srt)
                if planos:
                    # A fala de cada plano, realocada para o relógio da EMENDA.
                    # O plano vem do minuto três da fonte e vai para o segundo
                    # sete do clipe; a cue tem de ir junto, ou a legenda fala de
                    # uma cena que já passou. É o mesmo defeito do arquivo de
                    # janela com relógio próprio, multiplicado por N.
                    window = []
                    saida = 0.0
                    # Quantos planos ABREM no meio de uma frase. Com planos
                    # espalhados pelo material é quase todo: a fala de cada um
                    # começa antes do plano e termina depois dele, então o que
                    # sobra são meias-frases emendadas. Medido em 15/09 num edit
                    # de 15 planos: a legenda saiu "portas, a que vai pra sala /
                    # e a que vai Você lembra," seguida de "Nossa dispensa é
                    # cheia. Então," -- mecanicamente correta, ilegível.
                    partidos = 0
                    # Planos COLADOS um no outro são um trecho só para a fala:
                    # a imagem não pula, então a frase continua. Cortar a cue em
                    # cada borda de plano nesse caso partiria frases que a
                    # emenda não partiu.
                    trechos = []
                    for a0, b0, _n in planos:
                        if trechos and abs(a0 - trechos[-1][1]) <= 0.05:
                            trechos[-1] = (trechos[-1][0], b0)
                        else:
                            trechos.append((a0, b0))
                    for a0, b0 in trechos:
                        a, b = fonte_zero + a0, fonte_zero + b0
                        primeira = True
                        for r in rows:
                            if r["end"] <= a or r["start"] >= b:
                                continue
                            de = max(r["start"], a)
                            ate = min(r["end"], b)
                            if ate - de < 0.12:
                                continue          # sobra de cue, não é fala
                            if primeira and r["start"] < a - 0.05:
                                partidos += 1
                            primeira = False
                            window.append({
                                "start": round(queima_de + saida + (de - a), 3),
                                "end": round(queima_de + saida + (ate - a), 3),
                                "text": r["text"],
                                "words": [
                                    {**w,
                                     "start": round(queima_de + saida
                                                    + (float(w["start"]) - a), 3),
                                     "end": round(queima_de + saida
                                                  + (float(w["end"]) - a), 3)}
                                    for w in (r.get("words") or [])
                                    if a <= float(w.get("start", -1)) < b],
                            })
                        saida += (b - a)
                    window.sort(key=lambda r: r["start"])
                    queima_ate = round(queima_de + saida, 3)
                    colagem = len(trechos) > 1 and partidos > len(trechos) / 2
                    style_facts["caption_collage"] = {
                        "runs": len(trechos),
                        "runs_opening_mid_sentence": partidos,
                        "shots": len(planos),
                        "is_collage": bool(colagem),
                    }
                    if colagem:
                        # Não é aviso: é recusa. Uma legenda de meias-frases é
                        # pior que legenda nenhuma, pela mesma razão que uma
                        # palavra errada queimada é -- ela vai para a tela e
                        # fica lá. O edit sai sem legenda e o motivo é dito.
                        burn_reason = (
                            f"not burning captions: {partidos} of "
                            f"{len(trechos)} spliced stretches open in the "
                            f"middle of a "
                            f"sentence, so the caption would be a collage of "
                            f"half-sentences -- measured, it reads like "
                            f"'portas, a que vai pra sala / e a que vai Você "
                            f"lembra,'. Either take the shots from one "
                            f"continuous moment so the speech holds together, "
                            f"or drop --subtitles and put a written line on it "
                            f"with --hook, which is what the approved "
                            f"reference does.")
                        window = []
                else:
                    window = [r for r in rows
                              if r["end"] > queima_de and r["start"] < queima_ate]
                clash = S.language_clash(
                    hook, " ".join(r["text"] for r in window),
                    declared=language or R.get(rules, "caption.language"))
                if clash:
                    # Defeito 2.4. Recusa e diz por quê, em vez de queimar inglês
                    # sobre um hook em português como saiu da última vez.
                    burn_reason = f"not burning captions: {clash}"
                else:
                    # A fala continua depois do fim do corte? Só quem tem o
                    # SRT inteiro sabe, e é isso que decide se a ÚLTIMA cue está
                    # pendurada ou só é a última. Ver `finais_pendurados`.
                    fim_janela = queima_ate
                    fala_apos_o_corte = any(r["end"] > fim_janela + 0.05
                                            for r in rows)
                    partida = [r for r in rows
                               if r["start"] < fim_janela < r["end"]]
                    if partida:
                        frase_fecha_em = round(partida[0]["end"] - queima_de, 2)
                    descartes = []
                    cues = S.reflow_cues(window, descartes=descartes)
                    # ---- encaixar o destaque na fala de verdade ----
                    #
                    # A legenda automática do YouTube carimba o tempo DEPOIS da
                    # fala: o reconhecedor só emite quando já tem contexto.
                    # Queimar esse tempo cru nasce atrasado, e o dono viu no
                    # clipe -- o homem fala a palavra e o amarelo chega depois.
                    #
                    # Medido contra a régua que ele mediu à mão no clipe
                    # entregue, a mediana era +0,543s. O detector de começo de
                    # fala reproduz essa régua: 8 de 8, com 0,01s de diferença
                    # num único valor.
                    # O detector ouve o ARQUIVO e devolve no relógio dele;
                    # os onsets são levados para o da fonte, que é o relógio em
                    # que as cues estão.
                    falas, porque_fala = S.fala_comeca(source, start, length)
                    falas = [f + fonte_zero for f in falas]
                    if falas:
                        cues, sincronia = S.encaixa_na_fala(cues, falas)
                    else:
                        sincronia = {"porque": porque_fala or "sem fala detectada"}
                        notes.append(
                            f"não consegui achar os começos de fala deste trecho "
                            f"({porque_fala}), então o destaque ficou no tempo "
                            f"cru da legenda de origem, que costuma chegar atrasado.")
                    if descartes:
                        # Era nota, e nota não protege: fala que existe no SRT
                        # e não vai para a tela não aparece no mosaico -- o que
                        # falta é invisível por definição. Quem olha conta as
                        # cues que estão lá, não as que deveriam estar.
                        raise RuntimeError(
                            f"{len(descartes)} subtitle line(s) could not be "
                            f"used and would silently not reach the screen: "
                            + "; ".join(descartes[:3])
                            + ". Speech that exists in the SRT and is missing "
                              "from the picture is invisible on the contact "
                              "sheet. Fix the srt, or cut without --subtitles.")
                    if not cues:
                        burn_reason = ("the subtitle file had no usable lines in "
                                       "this window, so nothing was burned")

    # ------------------------------------------------------- a legenda é ASS
    #
    # O hook continua PNG do PIL, porque ele é medido antes de ser desenhado e
    # é isso que garante que ele cabe. A LEGENDA sai do PIL e vira ASS queimado
    # pelo libass, por um motivo que o PNG não resolve: destaque palavra a
    # palavra. Um PNG por estado de palavra seriam cinquenta e cinco entradas de
    # ffmpeg num clipe de vinte segundos; `{\k}` faz o mesmo dentro de uma linha
    # de texto, e o libass já está no ffmpeg da imagem (`--enable-libass`).
    ass_path = None
    if cues:
        if not ffmpeg_tem_filtro("ass"):
            # Dependência ausente não degrada calado. Um clipe de podcast sem
            # legenda não é um clipe entregue, e "renderizou sem legenda" com
            # uma nota no meio de doze é como um defeito atravessa.
            raise RuntimeError(
                "este ffmpeg não foi compilado com libass, e a legenda deste "
                "renderizador é ASS com destaque palavra a palavra. O filtro "
                "`ass` não existe neste binário -- `ffmpeg -filters | grep ass` "
                "confirma. A imagem do agente traz um ffmpeg com "
                "`--enable-libass`; um ffmpeg de Homebrew normalmente não. "
                "Rode o corte na imagem, ou corte sem --subtitles.")
        perdas = []
        texto_ass = ass_from_cues(cues, width, height, safe=safe,
                                  offset=queima_de, length=float(length),
                                  perdas=perdas)
        if not texto_ass.strip():
            burn_reason = ("o ASS saiu vazio: nenhuma cue sobrou dentro da "
                           "janela depois de deslocada para o tempo do clipe")
            cues = []
        else:
            ass_path = os.path.join(art_dir, f"{stem}.ass")
            with open(ass_path, "w", encoding="utf-8") as fh:
                fh.write(texto_ass)
            escritas = texto_ass.count("Dialogue:")
            # Cues que caíram fora da janela do clipe. Elas são fala que
            # acontece depois do fim do corte, então descartá-las é certo --
            # mas descartá-las CALADO não é. Medido: num clipe de 22,2s o
            # reflow produzia cues até 30,66s, e três delas sumiam sem uma
            # linha em lugar nenhum.
            fora = len(cues) - escritas
            if ficha and cues:
                # O portão do desencontro de relógios, e ele mede a coisa certa.
                #
                # A primeira versão deste portão contava quantas cues caíam
                # fora do clipe, e isso era falso positivo: numa janela que
                # termina no meio da fala, duas cues sobrando é rotina. O sinal
                # de verdade não é QUANTAS sobram -- é a legenda estar em OUTRO
                # LUGAR DO VÍDEO. No defeito medido, o clipe cobria 181-201s da
                # fonte e as cues vinham de 2-22s: nenhuma sobreposição, e cada
                # outro portão dizia que estava tudo bem.
                cue_de = min(float(c["start"]) for c in cues)
                cue_ate = max(float(c["end"]) for c in cues)
                overlap = min(cue_ate, queima_ate) - max(cue_de, queima_de)
                if overlap < length * 0.5:
                    raise RuntimeError(
                        f"the caption for this cut sits at {cue_de:.1f}-"
                        f"{cue_ate:.1f}s of the source and the cut shows "
                        f"{queima_de:.1f}-{queima_ate:.1f}s: they overlap by "
                        f"{max(0.0, overlap):.1f}s of {length:.1f}s. The "
                        f"subtitle and this file are on different clocks. That "
                        f"is how two clips went out carrying the opening of the "
                        f"video as their caption, with every other gate green. "
                        f"Refusing rather than burning.")
            if fora > 0:
                notes.append(
                    f"{fora} cue(s) da legenda caem fora da janela deste corte "
                    f"e não foram queimadas. É fala que acontece depois do "
                    f"--end, então está certo descartá-las -- mas se o corte "
                    f"deveria incluir essa fala, é o --end que está curto.")
            longest = max(c["end"] - c["start"] for c in cues)
            most = max(len(c["lines"]) for c in cues)
            pendurados = S.finais_pendurados(
                cues, continua_depois=fala_apos_o_corte)
            style_facts["caption"] = {
                # Onde a última linha da legenda termina, e até onde ela pode
                # ir para a direita. Os dois saem das margens que o ASS
                # recebeu, que é o mesmo lugar onde o libass as aplica.
                "bottom_px": height - max(S.caption_piso(height),
                                          height - int(safe.get("y1", 0))),
                "right_px": width - max(10, width - int(safe.get("x1", width - 140))),
                "cues": escritas, "max_lines": most,
                "max_cue_s": round(longest, 2),
                "renderer": "ass",
                "karaoke": not perdas,
                "hanging_endings": pendurados,
                # A ÚLTIMA cue pendurada é outro defeito, e ele não é da
                # legenda: é o corte terminando no meio da frase. Medido no
                # render de conferência: o clipe fechava em "trabalho manual,
                # só que" e a fala seguia depois do fim. O reflow não tem o que
                # fazer -- não existe palavra seguinte DENTRO do clipe. Quem
                # conserta isso é o `--end`, e a mensagem tem de dizer isso, em
                # vez de mandar mexer na legenda.
                "ends_mid_sentence": bool(
                    cues and pendurados and cues[-1]["text"] in pendurados),
                # Onde a frase partida fecha. Sem isto, "mova o --end" é um
                # conselho sem destino.
                "sentence_closes_at_s": frase_fecha_em,
                # O corpo e a altura de letra que ele produz. A altura é o que
                # se vê e é o que encolheu 45% sem ninguém notar quando a
                # legenda virou ASS -- registrada aqui, ela vira aritmética.
                # A sincronia do destaque contra a fala medida no áudio. Fica
                # gravada para dar para acompanhar se piora, que foi o pedido.
                "sync": sincronia,
                "cues_fora_da_janela": fora,
                "size_px": S.caption_size(height),
                "ink_ratio": round(S.caption_size(height)
                                   * S.ASS_INK_POR_CORPO / height, 4),
            }
            notes.append(f"burned {escritas} caption cues as ASS: at most {most} "
                         f"lines and {longest:.1f}s each, each word lit as it is "
                         f"said (\\k, time split by syllable)")
            for perda in perdas:
                notes.append("ATENÇÃO: " + perda)
            if pendurados:
                # A nota afirmava "porque o trecho não tinha fronteira nenhuma"
                # para toda cue acusada, e para a ÚLTIMA da janela isso é falso:
                # ali o que corta é o fim do clipe, não a falta de fronteira.
                # Duas causas, duas frases.
                porque = ("o reflow não achou fronteira utilizável no trecho"
                          if len(pendurados) > 1 or not fala_apos_o_corte else
                          "a fala continua depois do fim do corte")
                notes.append(
                    f"{len(pendurados)} cue(s) fecham numa palavra que pede "
                    f"complemento ({porque}): " + "; ".join(pendurados[:3]))
    if not cues and burn_reason:
        notes.append(burn_reason)

    # ------------------------------------------------------- texto do acervo
    #
    # Defeito 2.6, as duas metades. Detectar já foi feito acima; aqui é tratar,
    # e a decisão vai num aviso ao dono, nunca num silêncio.
    #
    #  - se o material já tem texto embaixo e nós vamos queimar legenda, sem
    #    tratamento saem DUAS legendas no mesmo quadro. O degradê do PRIME cobre
    #    a de baixo e a nossa fica sozinha.
    #  - `cover_footer=False` é a outra saída legítima: não cobre e não queima.
    queimamos = bool(cues and ass_path)
    # O terceiro estado, e o que ele provoca. `bottom` é o booleano duro,
    # calibrado para tarja fixa; `bottom_suspect` é o material que mostra sinal
    # de texto sem alcançá-lo -- ver os gatilhos em `warden_style`. Os dois
    # ligam o degradê, e essa escolha é assimétrica de propósito: cobrir a faixa
    # de baixo de um material limpo custa um degradê discreto num quinto do
    # quadro; NÃO cobrir um material com legenda custa o clipe, porque saem duas
    # legendas de origens diferentes no mesmo quadro e isso não se conserta
    # depois de publicado. Em 14/09 esse `False` desligou em cascata o degradê,
    # o REJECT do `check_sidecar` e todas as notas, e o clipe saiu.
    tem_texto = bool(source_text.get("bottom"))
    suspeito = bool(source_text.get("bottom_suspect"))
    # Como a nota chama o que foi visto. "Já carrega" e "pode carregar" são
    # coisas diferentes e a nota não pode dizer a primeira quando mediu a
    # segunda -- é o mesmo defeito de "limpo" e "nem olhei" serem a mesma frase.
    como = ("this footage already carries burned text along the bottom"
            if tem_texto else
            "this footage MAY already carry burned text along the bottom "
            "(SUSPECT, not the hard boolean)")
    if tem_texto or suspeito:
        cover = cover_footer
        if cover is None:
            # Cobre quando a nossa legenda vai disputar a mesma faixa. Se não
            # queimamos nada, o texto do acervo não está competindo com
            # ninguém, e cobri-lo seria estragar o quadro por nada. `queimamos`
            # é o que SAIU do render, não o que se pretendia queimar.
            cover = queimamos
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
            overlays.append((footer, fy, 0.0, float(length), 0.0))
            style_facts["footer_covered"] = True
            aviso_marca_dagua = (
                " If that bottom text is another clipper's watermark rather "
                "than the archive's own captions, covering it breaks the rules "
                "-- re-cut with cover_footer=False and drop --subtitles.")
            if queimamos:
                notes.append(
                    f"{como} ({source_text['evidence']}). Covered it with the "
                    "gradient footer so there is one caption in frame and not "
                    "two." + aviso_marca_dagua)
            else:
                # A nota afirmava a disputa "uma legenda em quadro e não duas"
                # mesmo quando legenda nenhuma nossa foi queimada. Chegar aqui
                # agora só é possível com `cover_footer=True` explícito, e aí o
                # degradê apagou a legenda do acervo em troca de nada -- o que a
                # nota tem de dizer, com o motivo de a nossa não ter saído.
                notes.append(
                    f"{como} ({source_text['evidence']}). Covered it with the "
                    "gradient footer because cover_footer=True was asked for "
                    "-- but NO caption of ours was burned in the end"
                    # Só a primeira frase do motivo: o texto inteiro do
                    # portão já saiu numa nota própria logo acima, e repeti-lo
                    # aqui enterra a frase que importa.
                    + (f" ({burn_reason.split('. ')[0]})" if burn_reason else "")
                    + ", so the gradient erased the archive's own text and put "
                    "nothing in its place. Re-cut with cover_footer=False to "
                    "get that text back, or fix what stopped our captions."
                    + aviso_marca_dagua)
        else:
            # Suspeito sem cobertura é tratado como o ramo duro, e o ramo
            # duro não derruba a legenda aqui: ele diz para não queimar por
            # cima, e o `check_sidecar` lá embaixo REPROVA o arquivo que saiu
            # assim mesmo. Não derruba porque chegar aqui exige um
            # `cover_footer=False` explícito -- o dono já disse que não quer o
            # degradê, e jogar a legenda dele fora em silêncio seria o mesmo
            # defeito pelo avesso. O que não pode é a saída ficar muda, e não
            # fica: a nota aqui, o REJECT no sidecar.
            notes.append(
                f"{como} ({source_text['evidence']}). Not covering it, so do "
                "not burn captions over it -- two captions in one frame is a "
                "reject.")

    # A decisão dita em voz alta, TODA VEZ QUE QUEIMA LEGENDA -- inclusive
    # quando a detecção não achou nada. Antes deste bloco a nota sobre o texto
    # do acervo só existia dentro do `if` acima, então um material limpo e um
    # material que ninguém olhou saíam idênticos para quem lê a saída: nada. E
    # "nada" era a saída do clipe de 14/09, onde a detecção tinha rodado, tinha
    # números, e eles não chegaram a lugar nenhum. Uma linha, com os números.
    if queimamos:
        if tem_texto and style_facts.get("footer_covered"):
            decidiu = ("covered the bottom with the gradient footer before "
                       "burning ours")
        elif suspeito and style_facts.get("footer_covered"):
            decidiu = ("covered the bottom with the gradient footer anyway -- a "
                       "gradient on clean footage is cheap, a second caption in "
                       "frame is not")
        elif tem_texto or suspeito:
            decidiu = ("did NOT cover it (cover_footer=False), so our caption "
                       "was burned over whatever is down there -- check the "
                       "contact sheet before delivering")
        else:
            decidiu = ("found nothing down there, so our caption was burned "
                       "straight onto the picture")
        notes.append(f"footage checked before burning captions "
                     f"({source_text['evidence']}): {decidiu}.")

    if hook:
        # O hook é medido contra a largura útil e quebrado em até duas linhas
        # antes de virar pixel. 854px num quadro de 1080: a coluna direita do
        # TikTok reserva 140px e a margem esquerda come 86px.
        usable = S.usable_width(width)
        size = max(20, int(width * S.HOOK_SIZE_RATIO))
        floor = max(16, int(width * S.HOOK_MIN_RATIO))
        # `*assim*` marca as palavras de acento. As marcas saem ANTES de medir:
        # medir o texto com os asteriscos dentro daria uma largura que não é a
        # do que vai para a tela, e a conta de "o hook cabe" é exata de propósito.
        hook, acento = S.split_acento(hook)
        lines, fitted, whole = S.fit_lines(hook, usable, size, floor)
        # O que a margem governa é o TOPO do bloco, não o centro dele, e essa
        # confusão é o defeito inteiro. `hook_y` sempre foi o centro que
        # `text_png` recebe: com `y0` 200 mais corpo 76 dando 276, e duas
        # linhas de entrelinha 101, metade do bloco subia -- topo em 175 e a
        # primeira linha de tinta medida em 184 no arquivo entregue (199 no
        # segundo corte, que abre sem acento). As abas e a lupa do TikTok
        # cobrem os primeiros ~200px, então o hook estava dentro da faixa de
        # risco desde sempre, e a conta que dizia "o hook cabe" só olhava a
        # LARGURA. Agora o topo do bloco é a margem e o centro é derivado dele.
        marge = S.margens(width, height)
        leading = int(fitted * S.LEADING)
        hook_top = max(marge["top"], int(safe.get("y0", 0)))
        hook_y = hook_top + (len(lines) * leading) // 2
        png = os.path.join(art_dir, f"{stem}-hook.png")
        png, oy = S.text_png(lines, png, width, height, hook_y, fitted,
                             scrim=True, acento=acento)
        # Três segundos e um fade curto, não o clipe inteiro. Ver HOOK_SECONDS
        # em warden_style: em 14/09 a frase estava nos oito quadros do mosaico,
        # e dos 3s em diante ela só disputava o quadro com a legenda da fala.
        hook_s = min(float(length), S.HOOK_SECONDS)
        hook_fade = min(S.HOOK_FADE_S, hook_s / 2)
        overlays.append((png, oy, 0.0, hook_s, hook_fade))
        # A largura REAL do texto desenhado, medida com a fonte que o desenhou.
        # Não é estimativa: é o número contra o qual a quebra foi decidida, e é
        # o que permite a "o hook cabe inteiro" ser aritmética em vez de olhar.
        from PIL import Image as _Im, ImageDraw as _Dr
        _probe = _Dr.Draw(_Im.new("RGBA", (8, 8)))
        _f = S._font(fitted)
        drawn = int(max(_probe.textlength(l, font=_f) for l in lines)) if lines else 0
        style_facts["hook"] = {"width_px": drawn, "usable_px": usable,
                               # Onde a primeira linha começa e onde a última
                               # coluna de tinta termina. Vem de quem DESENHOU,
                               # não de uma estimativa: é o mesmo número que o
                               # `text_png` usou. Sem ele o portão de margem
                               # teria de adivinhar.
                               "top_px": hook_top,
                               "right_px": (width + drawn) // 2,
                               "lines": len(lines), "size_px": fitted,
                               "chars": len(str(hook)), "complete": bool(whole),
                               "seconds_on_screen": round(hook_s, 2),
                               "fade_s": round(hook_fade, 2),
                               "accent_words": sorted(
                                   " ".join(lines).split()[i] for i in acento
                                   if i < len(" ".join(lines).split())),
                               "accent_rgb": list(S.COR_ACENTO_HOOK[:3])}
        notes.append(f"hook drawn at {fitted}px in {len(lines)} line(s): "
                     f"{drawn}px against {usable}px of usable width")
        notes.append(f"the hook leaves at {hook_s:.1f}s with a "
                     f"{hook_fade:.1f}s fade, instead of standing on the picture "
                     f"for the whole {float(length):.1f}s")
        if fitted <= floor:
            notes.append(f"the hook only fits at the {floor}px floor. It is "
                         f"{len(hook)} characters -- shorter reads better in a feed.")

    audio_policy = R.get(rules, "video.audio")
    silent = audio_policy == "forbidden" or (sound == "platform" and audio_policy != "required")
    embed = bool(track) and not silent

    # Com planos, a entrada é posicionada no PRIMEIRO plano e cada `trim` corre
    # relativo a isso: `-ss` antes de `-i` é o seek rápido (o wiki do ffmpeg diz
    # "very fast" contra "relatively slow, frame-by-frame" para a forma de
    # saída), e um edit que decodifica desde o zero de uma fonte de 18 minutos
    # paga isso em cada plano.
    seek = float(planos[0][0]) if planos else float(start)
    args = ["ffmpeg", "-y", "-ss", f"{seek:.3f}", "-i", source]
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
    for png, _y, _a, _b, _f in overlays:
        args += ["-loop", "1", "-framerate", "30", "-i", png]
        overlay_index[png] = next_input
        next_input += 1

    args += ["-t", f"{length:.3f}"]

    # Um filter_complex quando há overlay ou trilha; o -vf simples continua
    # servindo o caso sem texto, que é o mais barato e o mais comum nos testes.
    # A ORDEM das camadas, e ela não é arbitrária. O degradê do rodapé cobre o
    # texto queimado do próprio material, então ele vai por baixo de tudo; a
    # nossa legenda vem sobre ele, senão o degradê apagaria a nossa junto com a
    # do acervo; e o hook vai por último, porque nada pode passar por cima dele
    # nos três segundos em que ele existe.
    camadas = [("overlay",) + o for o in overlays if o[0] == footer]
    if ass_path:
        camadas.append(("ass", ass_path))
    camadas += [("overlay",) + o for o in overlays if o[0] != footer]

    if planos:
        # Um `trim` por plano, cada um com o seu zoom, e `concat` no fim. O
        # enquadramento vertical vem DEPOIS da emenda, uma vez só: recortar
        # plano a plano daria a cada um um enquadramento diferente da mesma
        # cena, que é o oposto do que um edit quer.
        cortes = []
        rotulos = []
        for i, (a, b, _batidas) in enumerate(planos):
            dur = b - a
            filtro = (f"[0:v]trim=start={a - seek:.4f}:end={b - seek:.4f},"
                      f"setpts=PTS-STARTPTS,fps=30")
            if motion:
                # Zoom leve POR PLANO, alternando o sentido: todo plano
                # empurrando para dentro lê como um efeito, não como montagem.
                de, para = (1.0, 1.06) if i % 2 == 0 else (1.06, 1.0)
                quadros = max(1.0, dur * 30)
                passo = (para - de) / quadros
                filtro += (f",zoompan=z='clip({de}+{passo:.8f}*on,"
                           f"{min(de, para)},{max(de, para)})'"
                           f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
                           f":s={sw or width}x{sh or height}:fps=30,setsar=1")
            filtro += f"[p{i}]"
            cortes.append(filtro)
            rotulos.append(f"[p{i}]")
        cortes.append("".join(rotulos)
                      + f"concat=n={len(planos)}:v=1:a=0[emendado]")
        partes = cortes + [f"[emendado]{chain}[v0]"]
        if motion:
            notes.append(f"scale moves on each of the {len(planos)} shots, "
                         f"alternating in and out. A cut with no scale move "
                         f"reads as raw footage.")
    else:
        partes = [f"[0:v]{chain}[v0]"]
    last, n = "v0", 0
    for camada in camadas:
        n += 1
        alvo = f"v{n}"
        if camada[0] == "ass":
            # `fontsdir` apontando para os assets do repo: sem ele o libass não
            # acha a Anton e cai numa fallback genérica de peso errado, que é o
            # defeito 2.3 voltando pela porta da fonte.
            partes.append(f"[{last}]ass=f={_ff_valor(camada[1])}"
                          f":fontsdir={_ff_valor(S.ASSETS)}[{alvo}]")
        else:
            _, png, oy, a, b, fade = camada
            idx = overlay_index[png]
            # `shortest=0:repeatlast=0` impede que o último quadro do PNG fique
            # carimbado depois do fim da janela, que é como um texto de 2s vira
            # um texto que não sai mais da tela.
            filtros = "format=yuva420p,setpts=PTS-STARTPTS"
            if fade:
                # O fade corre no relógio do próprio clipe: a entrada é uma
                # imagem em laço a 30fps que começa em 0, igual ao vídeo. Um
                # `st` relativo à janela do overlay sairia adiantado.
                st = max(float(a), float(b) - float(fade))
                filtros += (f",fade=t=out:st={st:.3f}:d={float(fade):.3f}"
                            f":alpha=1")
            partes.append(f"[{idx}:v]{filtros}[o{n}]")
            partes.append(f"[{last}][o{n}]overlay=0:{int(oy)}"
                          f":shortest=0:repeatlast=0"
                          f":enable='between(t,{a:.3f},{b:.3f})'[{alvo}]")
        last = alvo
    video = ";".join(partes).replace(f"[{last}]", "[vid]")

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
        if has_source_audio and not planos:
            # Speech over music, not music over speech: the track carries the
            # cut, the words carry the clip.
            mix = (f"[0:a]atrim=duration={length:.3f},asetpts=N/SR/TB,volume=1.0[v];"
                   f"[v][m]amix=inputs=2:duration=first:weights=1 0.55[a]")
        else:
            mix = "[m]anull[a]"
        if has_source_audio and planos:
            # Mesma razão do bloco acima: a fala contínua sobre imagem emendada
            # fala da cena errada. Num edit a trilha é o áudio, e o que a fala
            # tinha a dizer está na legenda.
            notes.append("the source speech was left out: the picture is "
                         "spliced from several places and continuous speech "
                         "would be talking over the wrong scene. The track is "
                         "the audio, and the words are in the caption.")
        args += ["-filter_complex", f"{video};{music};{mix}",
                 "-map", "[vid]", "-map", "[a]"]
        notes.append(f"track mixed in from {float(at):.2f}s with a {fade:.1f}s fade out")
    elif overlays or ass_path or planos:
        # `ass_path` sem overlay nenhum é um caso real: clipe com legenda e sem
        # hook. Com o teste velho (`elif overlays`) ele caía no `-vf chain` e
        # saía sem legenda, sem erro e sem nota. `planos` entra pelo mesmo
        # motivo: a emenda vive no filter_complex, e cair no `-vf` a jogaria
        # fora em silêncio -- que é o defeito que este bloco existe para não
        # repetir.
        args += ["-filter_complex", video, "-map", "[vid]"]
        if not silent and not planos:
            args += ["-map", "0:a?"]
        elif planos and not silent:
            # A fala da fonte não acompanha uma imagem emendada: os planos vêm
            # de pontos diferentes do material e o áudio contínuo ficaria
            # falando sobre a cena errada a partir do primeiro corte. Um edit
            # sem trilha e com planos sai mudo, e diz isso.
            args += ["-an"]
            notes.append("spliced shots and no track, so this clip ships "
                         "silent: the source speech does not follow a picture "
                         "that jumps, and the platform is where the sound goes.")
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
    #
    # E ele fica sendo o que é: um número no relatório, não o orçamento contra
    # o qual `style check` conta faixas de texto no render pronto. Quem faz essa
    # conta é `_linhas_demais`, e ela usa LINHAS (`hook.lines`,
    # `caption.max_lines`) e não camadas, porque uma cue de duas linhas lê como
    # duas faixas -- entre uma linha e outra passa imagem limpa. Cruzar com
    # `text_layers`, que diria 1, reprovaria todo corte de fala do projeto.
    # As bordas dos planos, no relógio do CLIPE, e o erro de cada uma contra a
    # grade de batida. É o que transforma "cortou na batida" em número: sem
    # isso, quem olha o mosaico vê trocas de cena e não tem como saber se elas
    # caem na música ou se são as do material de origem -- que foi exatamente o
    # que aconteceu no edit do Djokovic, onde as 12 trocas eram do vídeo e
    # nenhuma tinha sido escolhida.
    if planos:
        bordas, acc = [], 0.0
        for a, b, _n in planos[:-1]:
            acc += (b - a)
            bordas.append(round(acc, 4))
        erros, fase = warden_beat.erro_na_grade(bordas, (grid or {}).get("beat_s"))
        style_facts["shots"] = {
            "count": len(planos),
            "cuts_s": bordas,
            "beats": [n for _a, _b, n in planos],
            "per_20s": round(len(planos) * 20.0 / max(0.001, length), 1),
            "beat_s": (grid or {}).get("beat_s"),
            "grid_phase_s": fase,
            "cut_error_ms": [round(e * 1000) for e in erros],
            "worst_error_ms": round(max(erros) * 1000) if erros else 0,
        }
        if erros:
            pior = max(erros) * 1000
            notes.append(
                f"the {len(bordas)} scene change(s) land on the beat to within "
                f"{pior:.0f}ms at worst"
                + ("" if pior < 80 else
                   " -- over the 80ms the approved reference measures, so this "
                   "does not read as cut to the music"))

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
            "asked_for_captions": bool(caption_srt),
            "notes": notes, "grid": grid, "sheet": sheet,
            "style": style_facts, "style_path": style_path,
            "style_breaches": [m for lv, m in breaches if lv == "REJECT"]}
