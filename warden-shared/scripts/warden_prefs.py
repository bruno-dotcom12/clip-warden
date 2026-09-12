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

# Each entry: the question in plain words, the accepted answers, and why it
# changes the render. The last column is not documentation, it is the test for
# whether the question earns its place.
QUESTIONS = [
    ("delivery",
     "Clean cuts from the archive, or an edit built on a beat?",
     ["cuts", "edit"],
     "cuts burn the spoken words and keep the source pacing; edit cuts to music"),
    ("captions",
     "Burn the words into the picture?",
     ["yes", "no"],
     "decides whether the subtitle track is rendered in"),
    ("hook",
     "Want a line across the top of the frame, and in which language?",
     ["none", "pt", "en", "es"],
     "decides the overlay and the language it is written in"),
    ("sound",
     "Sound added on the platform, or carried inside the file?",
     ["platform", "embedded"],
     "a platform sound means the file ships silent, which the campaign may also require"),
    ("target_s",
     "How long should a clip be, when the campaign leaves room?",
     ["a number of seconds"],
     "picks the length inside the campaign's window"),
    ("batch",
     "How many clips per round?",
     ["a number"],
     "how many moments are rendered before you look at them"),
    ("approval",
     "Show you the chosen moments before rendering?",
     ["yes", "no"],
     "yes costs a round trip and saves renders you did not want"),
]

DEFAULTS = {"delivery": "cuts", "captions": "yes", "hook": "pt",
            "sound": "platform", "target_s": 30, "batch": 3, "approval": "yes"}

KEYS = [q[0] for q in QUESTIONS]


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


def missing(prefs):
    """What still has to be asked. Never asks twice for the same thing."""
    return [key for key in KEYS if key not in prefs]


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
        if policy == "forbidden" and out.get("sound") == "embedded":
            out["sound"] = "platform"
            overruled.append("this campaign adds the sound on the platform, "
                             "so the file ships silent")
        if policy == "required" and out.get("sound") == "platform":
            out["sound"] = "embedded"
            overruled.append("this campaign requires an audio track in the file")
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
