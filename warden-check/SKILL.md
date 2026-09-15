---
name: warden-check
description: Decide whether a finished clip may be posted to a campaign. Use before any post, when the person asks if a clip is within the rules, after an edit, or when a submission was rejected and they want to know why.
---

# The first of three gates

`warden check <file> --campaign <id> --caption -` with the caption on stdin.

Exit 0 means nothing mechanical blocks it. Exit 1 means do not post.

**Exit 0 is not "this clip is good".** This command reads the container and the
text: duration, resolution, aspect, audio policy, hashtags, the cap, the
deadline. It never opens a frame. Two gates stand after it, and both look at
things this one cannot see:

- the `-estilo.json` the render wrote beside the clip, which carries what the
  renderer measured of itself -- the hook's drawn width against the usable
  width, lines per cue, the longest cue, how long the hook stayed on screen.
  `warden style check` reads it.
- the contact sheet, which is the only step in the whole path that looks at the
  picture. `cut` writes one and refuses to print the `MEDIA:` line without it.

A clip that passes here and was never looked at is how a file with ten visible
defects got reported as passing.

## Reading the verdict out

Lead with the answer, then the blocking lines, then what only they can confirm.
Do not paraphrase a measurement; the tool already stated it in seconds and
pixels and those are the words to use.

**That holds when the person ASKED whether a clip passes.** It is the one place
the persona's ban on machine numbers gives way, because there the number is the
answer to their question. It is not licence to report a measurement nobody asked
for: on the way to a clip, a check that passed is silence.

The `CHECK` lines are not filler and you never bury them. They are the rules no
script can see: whether the footage really came from the authorised archive,
whether official material fills the share of screen the brief demands, whatever
the brief left open. A verdict that hides its blind spots is how someone learns
a rule from a rejection notice.

## When it rejects

Say which single change fixes it. "Four seconds too long, cut it to 30" beats a
list. If the fix is a re-render, offer to do it: you know the source and the
window.

## When they were rejected by the campaign itself

Ask what the campaign said, then put that reason into the rule set so it is
caught next time. A rejection reason that does not end up in `unknown` or in a
field is a rejection that will happen again.
