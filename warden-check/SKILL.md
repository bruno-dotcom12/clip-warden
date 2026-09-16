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
  picture. `cut` still writes one. Since 16/09/2026 it does NOT block the
  `MEDIA:` line: the owner traded that gate for his own time, after two vision
  calls cost 31s of a 706s request he was watching on a screen share. His
  decision, its price, and the list for when somebody does look:
  `warden-check/references/contact-sheet.md`.

A clip that passes here and was never looked at is how a file with ten visible
defects got reported as passing. That is the price of the trade, and it is the
owner's to pay: the mosaic is written and sits beside the render for when
something comes back wrong.

## What proves what

| The claim is about | What proves it |
|---|---|
| what is on the screen | `ffmpeg -ss <t> -i <clip> -frames:v 1`, and look at it |
| whether there are two captions | that frame, cropped to the band, at full size |
| what our caption says | the `.ass` — and only ours is in it |
| whether the clip is the length asked | `warden check`, never your own reading |
| whether a file arrived | `warden delivered` |

That table answers a question SOMEONE ASKED. It is not a round of checks before
delivering: **you do not look at the clip before you send it.**

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

Say which single change fixes it, with the number the tool gave -- one change
and its size, not a list. If the fix is a re-render and you are the one
delivering, re-render and deliver: you know the source and the window. Do not
offer and wait.

## When they were rejected by the campaign itself

Ask what the campaign said, then put that reason into the rule set so it is
caught next time. A rejection reason that does not end up in `unknown` or in a
field is a rejection that will happen again.
