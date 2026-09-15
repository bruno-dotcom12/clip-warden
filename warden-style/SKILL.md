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
  stayed into the `-estilo.json` and the delivery gate rejects a hook that never
  left.

**Write the hook to about nine words.** The measured fact: at the 76px body,
roughly 44 characters fit on a line, so two lines is about 88 characters. Past
that the body drops, and past ~150 characters it will not fit even at the floor
and `warden cut` refuses the render rather than silently dropping words.

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
  article, conjunction or unstressed pronoun at the end, in either language.
  `cut` moves the break to the nearest real boundary — punctuation first, then
  by extending past the dangling word, then by pulling back. Extending is why
  the ceiling is 3.4s and not 2.2s: "é o meu veredito," costs half a second more
  than "é o meu" and is the difference between a sentence and a fragment.
- **A fixed expression is one word.** "crème de la crème" was split in 14/09 and
  "LA CREME DO MERCADO" sat alone on screen for two seconds. The list lives in
  `EXPRESSOES_FIXAS`; it grows when a cut shows the next one.
- **Each word gets the time its syllables ask for**, not an equal share. That is
  what keeps the yellow highlight on the mouth instead of ahead of it.
- **Body `height // 16`, which is 120 on a 1920 frame, and that number is
  CALIBRATED — do not "simplify" it back to a round divisor.** What matters is
  the height of the LETTER, which is what a person sees: 120 measures **67px of
  ink, 3.49% of the frame** on capitals and ~3.12% on ordinary mixed case. The
  ASS `Fontsize` is not the same unit as a PIL font size, and that cost a
  delivered clip: when the caption moved from PIL to ASS the number stayed at 73
  and the letter shrank 45%, from ~3.2% of the frame to 1.88%. Nothing caught it,
  because everything checked the number and nothing measured the ink.
- 26 characters a line still fit at that body: 21 capitals measure 664px, so 26
  are ~822px against 854px usable.
- **The highlight is white → yellow**: a word is white until it is said and
  yellow after. In ASS that is PrimaryColour for the *said* word and
  SecondaryColour for the one still coming, which is the opposite of what the
  names suggest — inverting them ships the whole caption yellow. The yellow is
  **not** from the approved corpus: that corpus is scenepacks and contains no
  running caption at all. It is the owner's call of 14/09, recorded as a
  decision and not as a measurement.
- The caption and the hook must be **in the same language**. `cut` compares them
  and refuses to burn on a mismatch. A Portuguese hook over an English caption
  went out once.
- **Nothing burns without approval.** `warden captions review <srt> --start
  <s> --end <s>` prints the lines that fall inside the window; `--approve` signs
  them. The approval is of the file's content, so editing the srt voids it. With
  no approval the clip renders with **no caption**, which is right: a clip
  without captions can be fixed, a misheard word in a published video cannot.

## The hook's accent colour

The hook is **white**, and one or two key words carry an accent. Mark them in
the hook text with asterisks:

```
--hook "COPILOTO GANHA *70 MIL* POR MES"
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
is **studio.youtube.com > Áudio**, because YouTube does not claim its own
catalogue. `tracks add` writes down where each track came from and what that
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
  bands than we drew, in most frames, with two to spare, is a reject. Two to
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

`check` reports every metric with the approved range beside it, and rejects on
three things only. Be clear about why the list is short:

- The corpus is twenty scenepack edits — one sustained anchor phrase, no running
  caption. A podcast cut has a hook **and** captions, so it has more text bands
  than any approved clip (measured: 2.75 against a corpus range of 0.9–2.1).
  Rejecting on that range would reject a legitimate mode the corpus does not
  contain.
- Measuring text width from pixels is unreliable. It catches the extreme case
  (1.22 on a deliberately cropped control, against 0.67–0.98 on most approved
  clips) but it read 1.14 on an approved clip whose text is small and clearly
  inside the frame — neon signs and high-contrast art look like white-on-black
  letters. A metric that would reject a clip the owner approved cannot reject
  alone. It was **not** deleted, because it sees something the `-estilo.json`
  cannot: the sidecar knows the hook, which PIL drew, and knows nothing about
  text that was already in the frame — the archive's burned captions, another
  clipper's mark. Two instruments, different blind spots. So `check` crosses
  them: it rejects when both say the text is too wide, and when they disagree it
  names which is which instead of picking one and silencing the other.
- The spec is called `estilo-aprovado-scenepack.json` and not
  `estilo-aprovado.json` for the same reason. A generic name over a specific
  corpus is how a range measured on one format ends up rejecting another. When
  you `check` a clip that carries burned speech captions, the command says out
  loud that this corpus does not contain that format. The speech corpus is
  Bloco E and does not exist yet.

What **is** enforced is exact, because it comes from the renderer rather than
from pixels: `cut` writes a `-estilo.json` beside every clip with the hook's
real drawn width against the usable width, the seconds the hook stayed on, the
lines per cue, the longest cue, whether the scale moved, and — when `--seconds`
was passed — the duration the person asked for against the duration delivered.
Those are the numbers the Definition of Done is checked against, and `cut`
refuses to print `MEDIA:` when one of them fails, which is also why a clip that
fails one of them has no path to put in a final message.

## And none of it replaces looking

Every number here can pass on a clip that is still wrong. The contact sheet is
the gate; this file is how you avoid needing a second one.
