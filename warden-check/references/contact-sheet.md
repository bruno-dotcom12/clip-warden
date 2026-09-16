# The mosaic stopped being a gate on 16/09/2026

`warden-check/SKILL.md`, `warden-clip/SKILL.md` section 6 and
`warden-style/SKILL.md` all point here. The short rule is in each of them; the
decision, its price, and the list for when somebody does look are on this page.

## The decision, in one line

**Do not open the contact sheet before you deliver.** Your obligation is two
things — the hook and the caption — and `warden cut` refuses on both by itself.
The rest is delivered and repassed.

## Why the gate existed, so nobody rebuilds it by accident

A clip once shipped with the hook cropped at both edges, a six-line caption
covering the speaker's face, and the source's disclaimer sliced in half at the
bottom — and it was **reported as having passed verification**, because
verification counts pixels and seconds and every one of those defects is
invisible to arithmetic. That is real, and it is why `cut` started writing a
contact sheet of its own render. Until 16/09/2026 opening that image was a
condition of sending: `deliver` refused to print `MEDIA:` without it.

## Why it changed, and what it cost

The price was measured. **16/09/2026, on a real request of 706 seconds: the two
vision calls of that request cost 31s of a 706s request** — and the owner runs
this on a screen share, with somebody watching. Asked directly whether the
mosaic should stay mandatory before a delivery, he answered that a clip ships
without being looked at, and that the only obligation left is the hook and the
caption. The same day, from the same person: speed above everything, the minimum
quality that ships.

## What is lost

Exactly the defect in the first paragraph. A fault only the picture shows — a
hook cropped against a light background, a second caption coming from the source
— is no longer caught here. It reaches the owner's hands, and he is the one who
sees it.

He decided that with the 31 seconds in front of him. **Do not put the gate back
in silence because one clip came out wrong**: reopening it **is the owner's to
reopen**, not the agent's, and if it is reopened it gets a new date and a new
measurement on this page.

## What the tool does today

`cut` still writes and prints the sheet, and `lote render` still stacks one
combined mosaic of the whole batch:

```
SHEET:/var/lib/hermes/cache/videos/clip-01-contato.jpg
MEDIA:/var/lib/hermes/cache/videos/clip-01.mp4
```

`MEDIA:` is printed with it or without it. The sheet costs about 1.3s to write
and stays because it is the only visual evidence that exists later, when
something comes back wrong and somebody asks what went up. **No vision call is
spent on it**, and a render with no sheet is delivered like any other.

## If somebody DOES look, this is the list

This is no longer a gate and no item here rejects a clip on its own. It is what
to check when the owner asks you to look at a clip, or when one comes back wrong
and you are finding out why:

- [ ] the hook fits inside the frame, uncropped, in at most two lines
- [ ] the hook **left**: it is on the first tiles of the sheet and not the last
- [ ] the caption is at most two lines
- [ ] no cue ends mid-sentence — on a preposition, article or conjunction
- [ ] the yellow highlight tracks the speech, instead of a whole cue lit at once
- [ ] there are not two captions in the same frame
- [ ] no frame edge, source border or third party's text is sliced at the margin
- [ ] the subject's face is not covered by text
- [ ] no black bar along any edge — `warden check` rejects one now

A tile is 300px wide: enough to notice, never enough to conclude, so a tile that
makes you suspicious is settled with `ffmpeg -ss <t> -i <clip> -frames:v 1
-update 1 /tmp/q.png`. **Reading the `.ass` to settle a question about two
captions is the one thing that cannot work — only ours is in it.** And **you
never re-render a clip that passed**: five vision calls and three renders of one
correct cut cost ten minutes on 15/09, and nothing bad had shipped.

What you still never write about a file nobody has seen is that it passed
verification. Nobody has seen it, and that is now the normal case.
