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
        missing = [t for t in required_tags if fold(t) not in folded]
        if missing:
            reject("the caption is missing " + ", ".join(missing))
        elif required_tags:
            ok(f"all {len(required_tags)} required hashtags present")
        missing_ats = [a for a in required_ats if fold(a) not in folded]
        if missing_ats:
            reject("the caption is missing " + ", ".join(missing_ats))
        elif required_ats:
            ok("required mentions present")
        for phrase in required_text:
            if fold(phrase) not in folded:
                reject(f"the caption must carry the exact words: {phrase!r}")
        hits = [term for term in banned if re.search(rf"\b{re.escape(fold(term))}", folded)]
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
    print("ffprobe: " + (shutil.which("ffprobe") or "MISSING"))
    print("ffmpeg:  " + (shutil.which("ffmpeg") or "MISSING"))
    # The models arrive after boot rather than inside the image, so whether they
    # are here yet is a real question with a real answer, not a constant.
    home = os.environ.get("HF_HOME", "/var/lib/hermes/models")
    found = []
    for root, dirs, files in os.walk(home):
        for name in dirs:
            if "faster-whisper" in name:
                found.append(name.split("faster-whisper-")[-1])
    print("whisper models: " + (", ".join(sorted(set(found))) if found
                                else "still downloading, or not fetched yet"))
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


def cmd_archive(args):
    rules = load_campaign(args.campaign)
    out = safe_out(args.out or os.path.join(state_dir(), "footage", safe_id(args.campaign)),
                   "footage directory")
    try:
        got, failed = _media().archive(rules, out, limit=args.limit)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    for path in got:
        print(path)
    for url, why in failed:
        print(f"could not pull {url}: {why}", file=sys.stderr)
    if not got:
        die("nothing came down from this campaign's archive", code=1)
    return 0


def cmd_transcribe(args):
    target = safe_out(args.out or os.path.splitext(args.file)[0] + ".transcript.json",
                      "transcript")
    try:
        result = _media().transcribe(args.file, model_size=args.model)
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
    srt_text = _media().to_srt(result["segments"])
    if srt_text.strip():
        srt_path = safe_out(os.path.splitext(target)[0].replace(".transcript", "")
                            + ".srt", "subtitles")
        with open(srt_path, "w", encoding="utf-8") as fh:
            fh.write(srt_text)
        print(f"{srt_path}  (subtitles to burn with --subtitles, if you want them)")
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


def cmd_cut(args):
    rules = load_campaign(args.campaign)
    try:
        result = _media().cut(args.source, clip_out(args.out), rules,
                              args.start, args.end,
                              caption_srt=args.subtitles, hook=args.hook,
                              track=args.track, track_start=args.track_start,
                              sound=args.sound or P.effective(P.load(state_dir()),
                                                              rules)[0]["sound"],
                              crop=args.crop)
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}", code=1)
    for note in result["notes"]:
        print(f"  note: {note}", file=sys.stderr)
    media = probe(result["out"])
    findings = check(rules, media, None, ledger_for(args.campaign))
    blocking = [m for level, m in findings if level == REPROVA]
    print(result["out"])
    if blocking:
        print("this render does not clear the campaign yet:", file=sys.stderr)
        for message in blocking:
            print(f"  REJECT {message}", file=sys.stderr)
        return 1
    # The line that actually hands the file over. Printed by the tool rather
    # than composed by the model, for the same reason every other number here
    # comes from the tool: a path typed from memory is a path that does not
    # exist, and the failure is silent -- the runtime drops an unattachable
    # MEDIA line and the person is told about a clip that never arrived.
    print(f"MEDIA:{result['out']}")
    return 0


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

    p = sub.add_parser("archive")
    p.add_argument("--campaign", required=True)
    p.add_argument("--out")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("transcribe")
    p.add_argument("file")
    p.add_argument("--out")
    p.add_argument("--model")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("digest")
    p.add_argument("transcript")
    p.add_argument("--window", help="seconds, as 120-240")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("cut")
    p.add_argument("source")
    p.add_argument("--campaign", required=True)
    p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--subtitles")
    p.add_argument("--hook")
    p.add_argument("--track", help="audio file to cut to, for an edit")
    p.add_argument("--track-start", type=float,
                   help="where the track enters; its drop by default")
    p.add_argument("--sound", choices=["platform", "embedded"],
                   help="overrides the stored preference for this one render")
    p.add_argument("--crop", help="which side of a wider source to keep: "
                   "left, center, right, or a percentage; center by default")
    p.set_defaults(func=cmd_cut)

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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
