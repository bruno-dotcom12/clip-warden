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

**Write the hook to about nine words.** The measured fact: at the 76px body,
roughly 44 characters fit on a line, so two lines is about 88 characters. Past
that the body drops, and past ~150 characters it will not fit even at the floor
and `warden cut` refuses the render rather than silently dropping words.

A hook that only fits at 34px is a hook nobody reads on a phone. The tool says
so; shorten it rather than shipping it.

## The caption

- **Two lines maximum per cue, about 26 characters a line.**
- **No cue over 2.2s.** A whisper segment is not a cue: a seven-second segment
  with thirty words becomes a six-line block parked on the speaker's face. `cut`
  reflows them, splitting the segment's time in proportion to each piece's word
  count.
- Body `height // 26`, which is 73px on a 1920 frame. Not `// 22` — that is 87px
  and it pushes the speech over the face.
- The caption and the hook must be **in the same language**. `cut` compares them
  and refuses to burn on a mismatch. A Portuguese hook over an English caption
  went out once.
- **Nothing burns without approval.** `warden captions review <srt> --start
  <s> --end <s>` prints the lines that fall inside the window; `--approve` signs
  them. The approval is of the file's content, so editing the srt voids it. With
  no approval the clip renders with **no caption**, which is right: a clip
  without captions can be fixed, a misheard word in a published video cannot.

## The footage

- If the footage already carries burned text along the bottom — an archive's own
  subtitles, a disclaimer — `cut` detects it and covers it with the gradient
  footer so there is one caption in frame instead of two. It says so every time.
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

`extract --consolidate` writes `SPECS/estilo-aprovado.json`: min, median and max
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
  anything, so it is reported and not enforced.

What **is** enforced is exact, because it comes from the renderer rather than
from pixels: `cut` writes a `-estilo.json` beside every clip with the hook's
real drawn width against the usable width, the lines per cue, the longest cue,
and whether the scale moved. Those are the numbers the Definition of Done is
checked against, and `cut` refuses to print `MEDIA:` when one of them fails.

## And none of it replaces looking

Every number here can pass on a clip that is still wrong. The contact sheet is
the gate; this file is how you avoid needing a second one.
