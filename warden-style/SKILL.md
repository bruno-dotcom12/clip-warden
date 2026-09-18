---
name: warden-style
description: The measured look of a clip that has been approved before - text, captions, framing and movement. Read this before choosing a hook or burning a caption, not after the render comes out wrong.
---

# The look, in numbers

Nothing here was invented. Every number comes from `PRIME/edit.py`, which
produced the clips the owner approved, or from measuring those clips with
`warden style extract`. Do not invent a new one.

Read this **before** you write the hook and before you decide to burn captions.
It is cheaper to choose a nine-word hook than to re-render a cropped one.

## The frame

1080 x 1920, 30 fps. Every one of the twenty approved clips is exactly that.

The usable width for text is **854px**, not 1080. TikTok's right rail reserves
140px and the left crop margin eats 86px. Text that uses the full frame width is
text with its ends under the platform's own furniture, or off the edge entirely.

## The hook

- **Two lines maximum**, upper case, Anton (the font ships in
  `warden-shared/assets/`). Never a system fallback: light weight is the mark of
  an amateur edit.
- Body 76px on a 1080 frame, shrinking in steps of 2 until it fits 854px, floor
  34px. `warden cut` does this measuring for you and prints the result.
- Gradient scrim behind it, never a hard bar. A full-width black bar reads like
  a free app's watermark.
- 3px black stroke plus a separate weight shadow. That is what makes it readable
  over a bright plate.
- **It leaves at 3s**, with a 0.4s fade. A hook is a promise about the first
  seconds; after that it is a sign parked on the picture, fighting the caption
  for the same frame. Measured: the two cuts of 14/09 carried theirs through all
  eight tiles of the contact sheet, 1.2s to 18.8s. `cut` writes the seconds it
  stayed into the `-estilo.json` and prints a `LOOK:` for a hook that never
  left. It does not hold the clip: only a missing hook or missing captions do.

**Write the hook to about nine words.** The measured fact: at the 76px body,
roughly 44 characters fit on a line, so two lines is about 88 characters. Past
that the body drops, and past ~150 characters it will not fit even at the floor:
`warden cut` then drops words and ships the clip, and the sidecar rejects it
afterwards. It does not refuse the render, so a hook that long costs a render.

A hook that only fits at 34px is a hook nobody reads on a phone. The tool says
so; shorten it rather than shipping it.

## The caption

The caption is **ASS burned by libass**, not a PIL PNG like the hook. That is
what makes word-by-word highlighting possible at all: `{\k}` lights each word
as it is said, inside one line of text, instead of one PNG per word state.
The image's ffmpeg is built `--enable-libass`; a homebrew ffmpeg usually is not,
and there `cut` **refuses** to burn rather than shipping a podcast cut with no
speech on screen. `warden status` has a `libass` line for exactly this.

- **Two lines maximum per cue, about 26 characters a line.** This is the limit
  that actually protects the frame, and it did not move.
- **Target 2.2s a cue, hard ceiling 3.4s.** A whisper segment is not a cue: a
  seven-second segment with thirty words becomes a six-line block parked on the
  speaker's face.
- **A cue never ends on a word that needs the next one.** No preposition,
  article, conjunction or unstressed pronoun at the end, in any language.
  `cut` moves the break to the nearest real boundary — punctuation first, then
  by extending past the dangling word, then by pulling back. Extending is why
  the ceiling is 3.4s and not 2.2s: carrying one more word to the end of the
  clause costs about half a second, and it is the difference between a sentence
  and a fragment.
- **A fixed expression is one word.** One was split on 14/09 and half of it sat
  alone on screen for two seconds. The list lives in `EXPRESSOES_FIXAS`; it
  grows when a cut shows the next one.
- **Each word gets the time its syllables ask for**, not an equal share. That is
  what keeps the yellow highlight on the mouth instead of ahead of it.
- **The body size, the ink height and the white→yellow highlight are calibrated
  constants the renderer applies.** You do not set them and you do not
  "simplify" them; `warden-style/references/estilo-calibracao.md` has the
  measurements and the delivered clip that each one cost.
- The caption and the hook must be **in the same language**. `cut` compares them
  and refuses to burn on a mismatch. A Portuguese hook over an English caption
  went out once.
- **Nothing burns without a signature**, and who signs on each road is written
  in `warden-clip` 5, "Who signs the captions". Not here: a second copy of that
  rule is how "burn it, nobody is asked" and "nothing burns without approval"
  ended up in the same prompt saying opposite things.

## The hook's accent colour

The hook is **white**, and one or two key words carry an accent. Mark them in
the hook text with asterisks:

```
--hook "<THE LINE, WITH THE *KEY WORDS* MARKED>"
```

A whole line in colour reads cheap. And the accent is **#FF3B30**, a red-orange,
deliberately not the caption's yellow: if the hook's colour and the karaoke's
colour are the same, the eye reads them as one thing and the word-by-word
highlight stops meaning anything.

Neither colour comes from the approved corpus — that corpus is scenepacks, with
no running caption and no accented hook. Both are recorded decisions, and both
live in one constant each.

## The track, when someone asks for an edit on the beat

**No track is shipped inside this repository**, and a link the owner hands you
goes in without an argument:

```
warden tracks add <file|url>    keeps it, and measures it once
warden tracks list              what is already kept, with its tempo
warden cut … --track <name>     finds it by a short name, extension optional
```

Keeping it is the point: asking for the same file on every clip is the agent
forgetting what it was already given. On 15/09 the owner's NCS link was refused
twice, with a copyright lecture, and he had to argue for a track NCS publishes
for exactly this use.

**The source decides the risk, and the owner already paid for that lesson.** A
track he downloaded from Pixabay as royalty-free was claimed on Content ID as
someone else's, and the video was **blocked worldwide and demonetised**. The
free library's licence does not stop the automatic claim -- it gives grounds to
contest it afterwards. The one source with no risk by construction, for YouTube,
is **YouTube Studio's own audio library**, because YouTube does not claim its
own catalogue. `tracks add` writes down where each track came from and what that
means; it does not refuse anything.

## The footage

- If the footage already carries burned text along the bottom — an archive's own
  subtitles, a disclaimer — `cut` detects it and covers it with the gradient
  footer so there is one caption in frame instead of two.
- The detection has three states. Certain (a fixed band, 1.9x the middle's edge
  density in most frames) and **suspect** — signs of text that do not reach that
  bar: one frame over the line, or an average over 1.35x, or a dense band running
  to the 22% cap. A vlog's own captions are thin, centred and intermittent, and
  they land in *suspect*: measured on the clip that shipped with two captions,
  1 frame in 8 and 1.7x on average. Suspect covers the footer too. The trade is
  deliberate and one-sided: a gradient over clean footage costs a gradient, and
  an uncovered caption under ours costs the clip.
- `cut` prints what the detection found and what it decided **every time it burns
  a caption**, including when it found nothing. Without that line, "clean" and
  "nobody looked" read the same.
- `style check` gives a second opinion on the finished file: it counts bands of
  text per frame and compares them with the lines the sidecar says we drew. More
  bands than we drew, in most frames, with two to spare, is a `LOOK:` -- said to
   the person, never a reason to hold the clip. Two to
  spare because the band count invents a band of its own on real footage — the
  approved corpus measured up to 2.1 bands on average for a single two-line
  phrase.
- Covering **another clipper's watermark** is against most campaign rules. The
  archive's own captions are not that. If the bottom text is somebody's mark,
  re-cut with `cover_footer: false` in the plan and do not pass `--subtitles`.
- A bar or capture border on a source edge is trimmed before framing. Left in,
  it sits frozen at the edge for the whole clip.

## The movement

**Every clip moves in scale.** A light zoom, 1.00 to 1.06 across the window, is
the default and not the exception. The measured range across the approved clips
is 0.12 to 0.83 on `scale_variation`; a clip with zero reads as twenty seconds
of raw footage with text on top, and that is the cheapest difference there is
between a cut and an edit.

The grammar the PRIME edits use is a list of shots, each with `in`, `out`, a
`grade`, an `efeito` — and a `_` field saying what that shot is *for*. Open
`PRIME/EDITS/tese-01-trabalho-a-fazer.json`: the first shot is always the hook
and it carries its reason written down. Write that field before you render.

## Checking it

```
warden style extract <clip|folder> [--consolidate]   measure approved clips
warden style check <render.mp4>                      measure a new one
```

`extract --consolidate` writes `SPECS/estilo-aprovado-scenepack.json`: min, median and max
of each metric across the corpus. Ranges, not single values — one approved clip
runs 13s and another 73s and neither is "the right one".

`check` reports every metric with the approved range beside it. Since 16/09 it
rejects on ONE thing: a file no frame of which opens. The corpus is twenty scenepack edits with no running caption,
so most ranges do not apply to a podcast cut and `check` says so out loud rather
than rejecting a format it never measured. Why the list is short, with the
numbers, is in `warden-style/references/estilo-calibracao.md`.

What **is** enforced is exact, because it comes from the renderer rather than
from pixels: `cut` writes a `-estilo.json` beside every clip with the hook's
real drawn width against the usable width, the seconds the hook stayed on, the
lines per cue, the longest cue, whether the scale moved, and — when `--seconds`
was passed — the duration the person asked for against the duration delivered.
Those are the numbers the render reports on itself, and since 16/09 each is a
`LOOK:`: the clip IS delivered and whoever asked decides on another take. Only a
missing hook and missing captions hold a `MEDIA:` back -- and it is because only
those two hold it that dropping them is never the way out.

## And none of it replaces looking — but looking is no longer yours to do

Every number here can pass on a clip that is still wrong. That is not a
theory: a clip once went out with the hook cropped at both edges and a six-line
caption over the speaker's face, with every numeric check green. Until
16/09/2026 the answer to that was the contact sheet, and it was the gate.

**The owner took that gate out on 16/09/2026**, asked directly whether the
mosaic should stay mandatory before delivery, with the two vision calls of one
request costing 31s of 706s in front of him. He answered that a clip ships
without being looked at, and that the only obligation left is the hook and the
caption. The price, and the list for when somebody does look:
`warden-check/references/contact-sheet.md`.

So the sheet is still written — it costs about 1.3s and it is the only visual
evidence that exists later — and nothing waits on it. What is lost is exactly
the defect above: a fault only the picture shows now reaches the owner's hands.
He chose that. What did NOT change is this file: the hook and the caption are
still the two things that have to be right, and every number here is how they
get right BEFORE the render rather than after it.
