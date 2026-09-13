"""How this owner wants their clips made.

A campaign says what is allowed. It never says what is good. Two clippers
working the same brief want different things out of it: one wants clean cuts
from the archive with the words burned in, the other wants an edit built on a
beat. Guessing that is how an agent delivers twenty files somebody throws away.

So the agent asks, once, and remembers. Every question here exists because the
answer changes what gets rendered, and a question whose answer changes nothing
is a question that wastes the owner's patience on their first minute.

The rule set wins every conflict. A preference for 45 second clips on a campaign
that caps at 30 is a 30 second clip, because the preference is taste and the cap
is the payment.
"""
import json
import os

# Each entry: key, the question in plain words, the accepted answers, why it
# changes what happens, and which group it belongs to. `None` for the answers
# means free text. The fourth column is not documentation, it is the test for
# whether the question earns its place: a question whose answer changes nothing
# is a question that spends the owner's patience on their first minute.
#
# Two groups, asked at different moments. The `search` group is asked only when
# someone wants campaigns found for them, because a person who arrives with a
# link already knows which campaign they are doing and being interviewed about
# their niche is noise. The `edit` group is asked before the first render.
QUESTIONS = [
    ("region",
     "Which audience are you posting to?",
     ["br", "us", "both"],
     "campaigns usually require most of the audience in one country",
     "search"),
    ("niches",
     "What footage can you actually work with? (music, anime, podcast, sport, whatever)",
     None,
     "decides which open campaigns are worth showing you at all",
     "search"),
    ("platforms",
     "Where do you post?",
     ["tiktok", "reels", "shorts", "multi"],
     "campaigns accept different platforms and pay differently per platform",
     "search"),
    ("payout",
     "What makes a campaign worth your time? (a CPM, a minimum, or just say 'any')",
     None,
     "filters the list instead of handing you thirty campaigns",
     "search"),
    ("delivery",
     "Clean cuts from the archive, or an edit built on a beat?",
     ["cuts", "edit"],
     "cuts burn the spoken words and keep the source pacing; edit cuts to music",
     "edit"),
    ("captions",
     "Burn the words into the picture?",
     ["yes", "no"],
     "decides whether the subtitle track is rendered in",
     "edit"),
    ("hook",
     "Want a line across the top of the frame, and in which language?",
     ["none", "pt", "en", "es"],
     "decides the overlay and the language it is written in",
     "edit"),
    ("sound",
     "The original sound carried in the file, or silent for the platform to add its own?",
     ["embedded", "platform"],
     "embedded keeps the original audio; platform means the file ships silent. "
     "There is no default: a clip that ships silent by accident is a clip nobody "
     "asked to be silent, so this is always asked unless the campaign settles it",
     "edit"),
    ("target_s",
     "How long should a clip be, when the campaign leaves room?",
     ["a number of seconds"],
     "picks the length inside the campaign's window",
     "edit"),
    ("batch",
     "How many clips per round?",
     ["a number"],
     "how many moments are rendered before you look at them",
     "edit"),
    ("approval",
     "Show you the chosen moments before rendering?",
     ["yes", "no"],
     "yes costs a round trip and saves renders you did not want",
     "edit"),
]

# No default for `sound`. Every other preference has a safe fallback, but a
# silent clip is the one output the owner cannot fix after the fact and would
# never have chosen without being asked -- the campaign silenced a clip that had
# every right to its audio. So `sound` is deliberately absent here: unset, and
# with a campaign that does not settle it, it stays undecided and `warden cut`
# refuses to guess.
DEFAULTS = {"region": "both", "niches": "", "platforms": "tiktok", "payout": "any",
            "delivery": "cuts", "captions": "yes", "hook": "pt",
            "target_s": 30, "batch": 3, "approval": "yes"}

KEYS = [q[0] for q in QUESTIONS]
GROUPS = {q[0]: q[4] for q in QUESTIONS}


def path(state_dir):
    return os.path.join(state_dir, "preferences.json")


def load(state_dir):
    target = path(state_dir)
    if not os.path.exists(target):
        return {}
    with open(target) as fh:
        return json.load(fh)


def save(state_dir, prefs):
    tmp = path(state_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(prefs, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path(state_dir))


def missing(prefs, group=None):
    """What still has to be asked, optionally only for one group.

    Never returns something already answered, because being asked the same
    question twice is how an agent tells someone it was not listening.
    """
    return [key for key in KEYS
            if key not in prefs and (group is None or GROUPS[key] == group)]


def effective(prefs, rules=None):
    """Preferences after the campaign has had its say.

    Returns the settings and the list of places the campaign overruled taste,
    so the agent can say which of the owner's answers it could not honour and
    why, rather than silently doing something else than what was asked.
    """
    import warden_rules as R
    out = dict(DEFAULTS)
    out.update(prefs or {})
    overruled = []
    if rules:
        policy = R.get(rules, "video.audio")
        # A campaign that settles the audio decides `sound` whether or not the
        # owner set it -- there is nothing to ask when the rule set is explicit.
        if policy == "forbidden":
            if out.get("sound") == "embedded":
                overruled.append("this campaign adds the sound on the platform, "
                                 "so the file ships silent")
            out["sound"] = "platform"
        elif policy == "required":
            if out.get("sound") == "platform":
                overruled.append("this campaign requires an audio track in the file")
            out["sound"] = "embedded"
        # policy is null: `sound` stays whatever the owner set, or undecided.
        lo = R.get(rules, "video.duration_min_s")
        hi = R.get(rules, "video.duration_max_s")
        try:
            target = float(out.get("target_s") or 0)
        except (TypeError, ValueError):
            target = 0
        if hi is not None and target > hi:
            out["target_s"] = hi
            overruled.append(f"the campaign caps a clip at {hi}s")
        if lo is not None and target and target < lo:
            out["target_s"] = lo
            overruled.append(f"the campaign asks for at least {lo}s")
    return out, overruled
