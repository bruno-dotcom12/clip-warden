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
    charset = "utf-8"
    body = raw.decode(charset, errors="replace")
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


def transcribe(path, model_size=None):
    """Words with timing. A published subtitle beats a transcription.

    When the archive shipped subtitles, using them is not a shortcut, it is the
    better text: it is what the rights holder wrote, and it costs nothing.
    """
    sidecar = _subtitle_beside(path)
    if sidecar:
        return {"source": "published subtitles", "path": sidecar,
                "segments": _read_srt(sidecar)}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "no subtitles beside this file and faster-whisper is not installed, "
            "so there is no text to choose a moment from")
    seconds = duration_of(path)
    if model_size:
        size, why = model_size, "asked for"
    else:
        size, why = pick_model(seconds)
    model = WhisperModel(size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(path, vad_filter=True, word_timestamps=True)
    rows = [{"start": round(s.start, 2), "end": round(s.end, 2),
             "text": s.text.strip()} for s in segments]
    return {"source": f"faster-whisper {size} ({why})", "path": None,
            "segments": rows, "duration_s": seconds}


def _subtitle_beside(path):
    stem = os.path.splitext(path)[0]
    folder = os.path.dirname(path) or "."
    base = os.path.basename(stem)
    for name in sorted(os.listdir(folder)):
        if name.startswith(base) and name.lower().endswith((".srt", ".vtt")):
            return os.path.join(folder, name)
    return None


def _read_srt(path):
    def seconds(stamp):
        stamp = stamp.replace(",", ".")
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
            body = re.sub(r"<[^>]+>", "", body).strip()
            if body:
                rows.append({"start": round(seconds(start), 2),
                             "end": round(seconds(end), 2), "text": body})
        block = []
    return rows


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

def cut(source, out, rules, start, end, caption_srt=None, hook=None,
        track=None, track_start=None, sound="platform"):
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

    chain = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
             f"crop={width}:{height},setsar=1,fps=30")
    if caption_srt and os.path.exists(caption_srt):
        # Copied to a name of our own before it reaches the filter graph. ffmpeg
        # does not accept a backslash-escaped quote inside a single-quoted
        # filter argument, so a subtitle path carrying one leaves the filter and
        # can name any file the agent can read, including the host's credential.
        import shutil as _shutil
        safe_srt = os.path.join(os.path.dirname(os.path.abspath(out)) or ".",
                                "captions-%s.srt" % hashlib.sha256(
                                    caption_srt.encode()).hexdigest()[:10])
        if os.path.abspath(safe_srt) != os.path.abspath(caption_srt):
            _shutil.copyfile(caption_srt, safe_srt)
        caption_srt = safe_srt
        margin_v = max(10, height - y1)
        style = (f"Fontsize={max(18, height // 26)},Outline=3,Shadow=0,"
                 f"Alignment=2,MarginV={margin_v},"
                 f"MarginL={x0},MarginR={max(10, width - x1)}")
        escaped = caption_srt.replace("'", r"\'").replace(":", r"\:")
        chain += f",subtitles='{escaped}':force_style='{style}'"
    if hook:
        text = hook.replace("'", "").replace(":", " ").replace("\\", "")
        chain += (f",drawtext=text='{text}':fontcolor=white:fontsize={max(28, width // 22)}:"
                  f"box=1:boxcolor=black@0.55:boxborderw=18:"
                  f"x=(w-text_w)/2:y={max(40, int(safe.get('y0', 200)))}")

    audio_policy = R.get(rules, "video.audio")
    silent = audio_policy == "forbidden" or (sound == "platform" and audio_policy != "required")
    embed = bool(track) and not silent

    args = ["ffmpeg", "-y", "-ss", f"{float(start):.3f}", "-i", source]
    if embed:
        # The track enters at its drop unless told otherwise, because an edit
        # that opens on an intro has spent its first second on nothing.
        at = track_start if track_start is not None else (grid or {}).get("drop_s", 0.0)
        args += ["-ss", f"{float(at):.3f}", "-i", track]
    args += ["-t", f"{length:.3f}", "-filter_complex" if embed else "-vf"]

    if embed:
        fade = max(0.5, min(3.0, length * 0.12))
        music = (f"[1:a]atrim=duration={length:.3f},asetpts=N/SR/TB,"
                 f"volume=-6dB,afade=t=out:st={max(0, length - fade):.3f}:d={fade:.3f}[m]")
        has_source_audio = bool(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name", "-of", "csv=p=0", source],
            capture_output=True, text=True).stdout.strip())
        if has_source_audio:
            # Speech over music, not music over speech: the track carries the
            # cut, the words carry the clip.
            mix = (f"[0:a]atrim=duration={length:.3f},asetpts=N/SR/TB,volume=1.0[v];"
                   f"[v][m]amix=inputs=2:duration=first:weights=1 0.55[a]")
        else:
            mix = "[m]anull[a]"
        args += [f"[0:v]{chain}[vid];{music};{mix}", "-map", "[vid]", "-map", "[a]"]
        notes.append(f"track mixed in from {float(at):.2f}s with a {fade:.1f}s fade out")
    else:
        args += [chain]

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
    return {"out": out, "duration_s": measured, "asked_s": round(length, 2),
            "notes": notes, "grid": grid}
