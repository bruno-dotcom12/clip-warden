---
name: warden-clip
description: Go from a campaign's authorised archive to finished vertical clips. Use when the person wants clips made, sends a campaign link and asks for cuts, or points at footage and asks what would work.
---

# Archive to clips

Write every message in the language the person wrote to you in. Nothing here is
a sentence to copy: examples show the SHAPE, you supply the words.

## The order — the only copy of it

```
warden lote prep  ->  you choose the windows  ->  warden lote render  ->  deliver
gate -> WORDS (--text-first) -> chosen windows -> --windows -> render -> deliver
```

`lote` is the default road; the second line is that order by hand. **Anything
reading "pull the footage, then find the moment" is the old order and is
wrong.**

## Deliver first, cut second

Before rendering anything, run `warden delivered`. It exits 1 while a clip of
THIS conversation sits on disk with `"sent": false` in `entregas.json`.

**Exit 1 means send what already exists.** Put its `MEDIA:` line in the final
message of this turn. Do not render it again, do not render a different window
"to be safe", do not open it to check.

**Never re-cut a window the person did not ask you to re-cut.** A warning in a
tool's output is repassed, not acted on. If they ask for another attempt, END a
turn saying what you are changing and why, and start it on the next — never two
renders in a row in silence.

## Run `prep` and `render` IN THE BACKGROUND. Always.

Your terminal kills a foreground command at **300s**; both pass it.

1. `background=true` and `notify=true` on the call, the command carrying its log
   redirect and **no `&` and no `nohup`** — the call already IS the background.
   Exactly this shape, and nothing else:

       terminal(command="warden lote prep <url> --n 2 > /tmp/prep.log 2>&1",
                background=true, notify=true)

   With `&` or `nohup` the terminal holds a shell that already exited:
   16/09 `process_manage` said `exited` in 4s on a prep still running.
2. **One `process_manage` wait on that session id.** It is **clamped to 180s**
   and a render runs longer, so a wait that comes back still running is waited
   on AGAIN, never replaced by something else.
3. Then read the log.

Never search the process list for the command's name: it matches the shell
running it, so it never ends.

**The wait step is still NOT MEASURED clean**: every run so far fell back to
polling. If it misbehaves, say so in one line and read the log.

## 0. The link may be one message behind

When the words point at a link that is not in the text — a demonstrative, "this
video", "below", in any language — do not say the link is missing: `warden
inbox`, with no `--wait`. It arrives as its OWN message a moment later. Exit 0
gives the URL; exit 1 is when asking is fair.

## 1. The gate

**A link someone sent you is authorised by their having sent it.** No list to
check, nothing to ask, no licence to request.

```
warden archive --trusted <url> ...      a bare link (`--link` is the same flag)
warden archive --campaign <id> ...      the campaign's own archive
```

With a campaign, store it first (`warden-campaign`): the renderer takes duration
and resolution from the rule set, so `campaign show <id>` coming back empty
means you are not ready to cut. A bare link cuts against a blank rule set, and
you never invent a value to fill a flag.

## 2. Pull the words. Never the video.

```
warden archive --campaign <id> --text-first     subtitles, or audio if none
warden archive --trusted <url> --text-first     the same, for a bare link
```

The first command you run: choosing a window is work on TEXT. An 18-minute
source costs 5s and 89 KB as subtitle, 13s and 382 MB as video.

`archive --campaign <id>` downloads from `sources.archive_urls` and nowhere
else; with no archive published, ask for the link, because unauthorised footage
is rejected AFTER the views. When a download fails, repass the failing link and
never go find the footage yourself.

**A specific link, with a campaign:** `warden authorize <link> --campaign <id>`.
Exit 0, say nothing. **Exit 1, cut it anyway**, adding one clause naming the
video and saying the submission may be refused for being outside the archive.

**When a source refuses to come down**, repass `archive`'s diagnosis with no
cause of your own in front of it, and offer them their own file (the persona's
"The failure you repass"). A bot-check is about **every link, not this one**; a
plain yt-dlp error is about a private, removed or age-restricted video, and one
is never upgraded into the other. The two roads it names are a different
outgoing address, or a cookies file at `/var/lib/hermes/warden/cookies.txt` from
a throwaway account, never theirs. Why that, and what is and is not measured:
`warden-clip/references/fonte-bloqueada.md`.

## 3. Read the words and choose the windows

When `--text-first` brought audio, `warden transcribe <audio>` writes words with
timing and `warden digest <transcript>` a version you can afford to read. Never
read the raw transcript JSON: word-level timing is for the renderer.

`warden signals <transcript> --source <audio>` is where you start on a long
source: in time order, the moments carrying a question, an absolute claim, a
named fight, laughter, a loudness spike. It does not rank them. Build clips from
the clusters, not the single lines.

A clip that travels has a **hook**, a **middle** that pays it off and a **close**
that lands: name all three before a window becomes a clip. A campaign that ships
silent (`warden prefs show --campaign <id>`, before you pick) needs a window
that reads on the picture.

## 4. Pull ONLY the windows you chose

```
warden archive --trusted <url> --windows 181-201.6,745.5-765    the whole batch
warden archive --campaign <id> --window 181-201.6               one window
-> /var/lib/hermes/warden/footage/<id>/janela-....mp4   -> IN_POINT:2.000
```

**Ask for the whole batch at once**, one `IN_POINT:` per file. The file
is the window plus keyframe slack, so it is **not** the source: cut it with
`--start <IN_POINT> --end <IN_POINT + length>`, never the source's own
timestamps. It carries a `.origem.json` that `cut` **requires**, refusing to
burn captions rather than wrong ones.

**Under about half an hour of source, pull it whole. Over that, pull the
windows.** Whole regardless when `--text-first` got neither subtitle nor audio,
when they asked for the whole thing, or when you need more than the windows.

## 5. Render inside the rules

`warden cut <source> --start <s> --end <s> --out <file>`, plus `--subtitles
<srt>`, `--hook "<line>"` and `--crop left|right|auto|<pct>`. The `.srt` is the
one `warden transcribe` wrote.

The frame is always **9:16**: a landscape source keeps a band of itself, and
`--crop` is which band survives.

### Who signs the captions — the only copy of this

Unsigned, `cut` renders **no caption** rather than burn words nobody read.

| road | who signs |
|---|---|
| `warden lote render` | the tool signs the clean lines per window; nobody is asked |
| `warden cut` alone | YOU: `warden captions review <srt> --start <s> --end <s> --approve` |

**A suspect line is a warning, not a refusal**: the tool names it and burns the
caption anyway. If it is wrong, say so in ONE clause beside the clip, and do not
re-render unless they ask. `--keep "<line>"` means only "I read this one, stop
warning me". Why: `warden-clip/references/legenda-suspeita.md`.

### The two lines `cut` prints

**Every time it burns a caption** `cut` says what it found in the footage's own
bottom band, including nothing, and which pixels the crop kept. Repass either
warning in ONE clause beside the clip. When the crop fell back to centre for
want of a face, `--crop <percentage>` places the band by hand.

### The edit, on the beat

An edit is not a window with music over it: `--shots <a>-<b>,<c>-<d>` splices
pieces you CHOSE, each snapped to whole beats of `--track <name>`, with
`--any-length`. A different product, and its rules are all in
`warden-clip/references/edit-na-batida.md`. Read that before you cut one.

`cut` runs the check on its own output: never send a clip whose cut exited
non-zero.

## 5b. Duration, and the batch

Pass `--seconds <n>` whenever a person said a duration: the cut moves to it from
the same start, and a file that misses it comes back with a `LOOK:` saying so
-- since 16/09 that is an observation and the clip is delivered anyway. Without
the flag, two clips asked for at 20s came back 22% over and 23% under. Only a
campaign rule overrides it, and then `cut` names it and you repeat it.

A batch renders one clip at a time and its progress line is for YOU, not to
relay. **Do not re-cut to find the window**: choose it on the text, where
changing your mind is free.

## 6. The mosaic is not a gate

**Do not open the contact sheet before you deliver.** Your obligation is the
hook and the caption; the sheet is `SHEET:` and nobody waits on it. Never write
that a clip "passed verification" when nobody has seen it. The list for when
somebody does look: `warden-check/references/contact-sheet.md`.

## 7. Send

**The delivery rule lives in the persona, under "Handing the file over".** In
one line: render every clip they asked for — up to three — before you answer,
then ALL of the `MEDIA:` lines go in the LAST message of the turn, with no tool
call after it. Mid-turn a `MEDIA:` line attaches nothing, in silence.

**`lote render` ends by printing that message for you.** It opens with `END YOUR
TURN NOW with exactly this as your final message (no tool call after it):` and
closes with `send these lines in your FINAL reply, nothing else pending`. What
stands between those two lines IS your final message. Obey the printout over
your memory of this file: tool output reaches you whole, and this file was
pruned from memory in the same second a render started.

**Rendered is not delivered**: you report the `MEDIA:` lines you sent, never
that they landed. If one did not go, say which, and that it is going again.

### The plan, for several clips

The JSON is in `warden-clip/references/plano.md`. Two fields decide it:
`seconds` is the number they said; `_` is what that cut is FOR — a window you
cannot justify in a sentence is not a clip yet. `--plan` renders and clears but
**does not deliver**: it prints the final message and exits non-zero naming any
clip that is missing.

**Above three clips, use `warden lote render`.** Only it splits the batch: the
first three are delivered, the rest named. **Those are not rendering anywhere
and nothing will wake you** — it prints the `warden cut --plan <file>` that
brings them. Never say they are on the way.
