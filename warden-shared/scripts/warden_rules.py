"""The rule set: what a campaign demands, in a shape a script can judge.

A campaign brief is prose written by a brand manager. A clip is a file. The gap
between them is where clippers lose work that is already done: the views accrue,
the submission is rejected for a missing hashtag or an unauthorised source, and
nobody pays. This module is the contract that closes that gap.

The division of labour is deliberate and it is the whole design. The MODEL reads
the brief and fills this structure, because prose is what models are for. The
SCRIPT decides pass or fail, because a duration is a number and a number is not
a thing to have an opinion about. A model that judges its own extraction will
talk itself into approving a clip, and the clipper finds out a week later.

Every field is optional. A brief that does not mention duration leaves duration
unset, and an unset field is never a rule: it is reported as something nobody
checked. Inventing a plausible limit is worse than admitting the brief is silent
about it, because a clipper acts on what this file says.
"""

SCHEMA_VERSION = 1

# What a filled rule set looks like. Handed to the model verbatim by the
# warden-campaign skill; every key here is one the checker knows how to read.
TEMPLATE = {
    "schema": SCHEMA_VERSION,
    "id": "slug-of-the-campaign",
    "name": "Campaign as the brief names it",
    "source": "url or 'pasted by the owner'",
    "captured_at": "YYYY-MM-DD",
    "video": {
        "duration_min_s": None,
        "duration_max_s": None,
        "aspect": None,               # "9:16", "1:1", "16:9"
        "width": None,
        "height": None,
        "audio": None,                # "required" | "forbidden" | None
        "max_file_mb": None,
    },
    "sources": {
        "allowed": [],                # what footage may be used, in the brief's words
        "forbidden": [],              # what may not
        # The archive the brief points at, as direct links. This is what makes
        # one message with one link enough: the campaign page names where the
        # authorised footage lives, and the pipeline pulls from here and from
        # nowhere else. An empty list means the brief did not publish an
        # archive, and the run stops and says so rather than going to find
        # footage of its own, which is exactly how a submission gets rejected
        # for an unauthorised source.
        "archive_urls": [],
        "official_min_screen_pct": None,
    },
    "caption": {
        "required_hashtags": [],      # ["#primevideobr", "#invincible", "#ad"]
        "required_mentions": [],      # ["@primevideobr"]
        "required_text": [],          # exact strings the caption must carry
        "banned_terms": [],           # words that disqualify the post
        "max_len": None,
    },
    "posting": {
        "platforms": [],              # ["tiktok", "instagram", "youtube"]
        "sound_policy": None,         # free prose: where the audio must come from
        "min_days_live": None,
        "per_clipper_cap": None,      # max posts this clipper may submit
        "deadline": None,             # YYYY-MM-DD
    },
    # Where burned text may sit, in the pixels of the declared resolution. The
    # default is TikTok's: the right rail eats 140px, the caption block eats the
    # bottom. A brief rarely states this; the clipper pays for it anyway when the
    # hook is under the like button.
    "safe_area": {"x0": 86, "x1": 940, "y0": 200, "y1": 1586},
    "notes": [],
    # The honest half. Anything the brief did not settle goes here by name, and
    # the checker repeats it on every verdict so it is never mistaken for a pass.
    "unknown": [],
}

_SECTIONS = ("video", "sources", "caption", "posting", "safe_area")


def blank():
    """A deep copy of the template, safe to fill."""
    import copy
    return copy.deepcopy(TEMPLATE)


def validate(rules):
    """Structural complaints, as a list of strings. Empty list means usable.

    This does not ask whether the extraction is faithful to the brief, which no
    script can know. It asks whether the file is the shape the checker reads, so
    a typo in a key name fails here rather than silently disabling a rule.
    """
    problems = []
    if not isinstance(rules, dict):
        return ["the rule set is not an object"]
    if rules.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema must be {SCHEMA_VERSION}, got {rules.get('schema')!r}")
    if not rules.get("id"):
        problems.append("id is missing")
    elif not all(c.isalnum() or c in "-_" for c in str(rules["id"])):
        problems.append("id must be letters, digits, - or _")
    for section in _SECTIONS:
        value = rules.get(section)
        if value is not None and not isinstance(value, dict):
            problems.append(f"{section} must be an object")
    known = set(TEMPLATE)
    for key in rules:
        if key not in known:
            problems.append(f"unknown top-level key {key!r}")
    for section in _SECTIONS:
        value = rules.get(section)
        if isinstance(value, dict):
            for key in value:
                if key not in TEMPLATE[section]:
                    problems.append(f"unknown key {section}.{key!r}")

    video = rules.get("video") or {}
    lo, hi = video.get("duration_min_s"), video.get("duration_max_s")
    for label, n in (("duration_min_s", lo), ("duration_max_s", hi)):
        if n is not None and (not isinstance(n, (int, float)) or n <= 0):
            problems.append(f"video.{label} must be a positive number or null")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
        problems.append("video.duration_min_s is above video.duration_max_s")
    audio = video.get("audio")
    if audio not in (None, "required", "forbidden"):
        problems.append("video.audio must be 'required', 'forbidden' or null")

    caption = rules.get("caption") or {}
    for tag in caption.get("required_hashtags") or []:
        if not str(tag).startswith("#"):
            problems.append(f"required hashtag {tag!r} does not start with #")
    for at in caption.get("required_mentions") or []:
        if not str(at).startswith("@"):
            problems.append(f"required mention {at!r} does not start with @")
    return problems


def get(rules, path, default=None):
    """rules['video']['duration_min_s'] without five lines of None checks."""
    node = rules
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return default if node is None else node
