# A suspect caption line is a warning, not a refusal

`warden-clip/SKILL.md` section 5 points here. The short rule is there. What is
on this page is the measurement that changed the rule on 16/09/2026 — which is
also the reason nobody puts the refusal back on their own.

## The measurement that changed it

Until 16/09, **one** suspect line rendered the whole clip WITH NO CAPTION until
that line was typed back in `--keep`. Measured that day on a real YouTube live:
`>>` in **184 of 706 cues**, and **2 clips of 2 shipped with no words on screen,
twice over** — which is the owner's complaint, word for word.

A check that fires on every clip is not protecting the clip, it is switching the
product off. And the owner's rule is that the caption is the one thing he
expects.

So: the tool **names the line, prints it as it will appear on screen, and burns
the caption anyway**. You read that line in the tool's output — not in a mosaic
you open — and if it is wrong you say so in ONE clause when you hand the clip
over. Do not re-render for it unless they ask. `--keep "<the line>"` still
exists and now means only "I read this one, stop warning me"; it no longer
decides whether the clip has a caption.

## Reading is still the point

Whisper mishears. A wrong year went on screen on 15/09 because a line was
flagged and signed in the same breath. The place to read is the tool's own
output, which prints every flagged line as it will appear — that costs no vision
call and no extra turn.

This is **never** a question for the person: you repass what was flagged, and
you never ask permission to caption.

## The other text: what the picture already carries

A different thing from a suspect line, and `cut` warns about it too. Before
rendering it opens eight frames of the window and measures the edge density of
the bottom strip, which is what finds text the picture **already** has: a
lower-third, the channel's burned-in caption, the archive's own subtitles.
Burning over that gives two captions.

It finds three states, not two:

- **A fixed strip** (a permanent disclaimer): trips the hard boolean, and `cut`
  covers it with a gradient footer.
- **A thin, centred, intermittent caption** (a vlog's own subtitle, which
  disappears between phrases): often does not trip. Measured on the cut that
  shipped with two captions: **one frame in eight voted, and the mean was 1.7x
  the middle of the frame**, under the 1.9x the boolean asks for.
- That case became a third state, **suspect**, and `cut` treats it like the
  first: it covers the footer anyway, because a gradient over clean picture
  costs a gradient and a second caption costs the clip. `warden style check`
  then reads the sidecar and rejects the file if a suspect background was left
  uncovered under our caption.

`cut` prints one line about the picture **every time it burns a caption**, with
the numbers, including when it found nothing — before that, "clean" and "nobody
looked" were the same empty output. Read that line. When it says it found or
suspected text, that is a warning you repass in ONE clause beside the clip and
they decide; you do not open a frame to settle it. Only if IT says the source's
text should stay do you re-cut without `--subtitles`.
