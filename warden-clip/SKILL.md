---
name: warden-clip
description: Go from a campaign's authorised archive to finished vertical clips. Use when the person wants clips made, sends a campaign link and asks for cuts, or points at footage and asks what would work.
---

# Archive to clips

The order is fixed and it is not a preference.

```
rule set -> archive -> text -> chosen windows -> render -> sheet -> send_message
```

## 1. The rule set first

No clip is rendered before the campaign is stored, because the renderer takes
its duration and its resolution from the rule set. Run `warden-campaign` first
if `warden campaign show <id>` comes back empty.

## 2. Pull only what the brief published

`warden archive --campaign <id>` downloads from `sources.archive_urls` and from
nowhere else. If it says the rule set publishes no archive, stop and ask for the
link. Do not go looking for the footage. A clip built from material the campaign
did not authorise is rejected after the views, which is the loss this agent
exists to prevent.

Each file it prints is a path you pass straight to `warden cut`. It names them
itself and pulls one video per playlist link, so there is nothing to rename and
no stray file to sort through. If a download fails, `archive` says which link
and why on its own line; pulling footage by hand from outside the archive
rebuilds the exact failure this skill exists to prevent. Send the owner the
failing link instead.

### A specific link the owner sends

Do not reason about whether it belongs; ask the tool. With a campaign, run
`warden authorize <link> --campaign <id>`: it expands the playlists and matches
the id, and only it can, so a "not in the archive" from you without it is a
guess. It also prints the video's title. Authorised, you cut it. Not authorised,
you do not refuse -- you ask, naming the video by that title: "the video
'<title>' is outside the campaign's archive and a submission may be rejected --
cut it anyway?" and you wait for a yes before cutting. Their call, not yours, and
a flat no is not the answer.

Without a campaign, run `warden trusted check <link>`. From a source in their
trusted list, cut it; otherwise say it is not a source they have vouched for and
offer `warden trusted add <channel|domain>`. A channel is an `@handle` or a
`UC…` id; a domain is `youtube.com`. This is the only footage you cut that no
campaign authorised, and the trusted list is what stands in for the brief.

## 3. Choose on the text, never on the video

`warden transcribe <file>` writes words with timing, preferring subtitles the
archive published over transcribing. Then `warden digest <transcript>` gives you
a version you can afford to read.

Never read the raw transcript JSON. Word-level timing is for the renderer, and
on a long source the raw artefact does not fit in a window worth paying for.

## Finding the moments that travel

`warden signals <transcript> --source <file>` is where you start on a long
source -- a podcast, a live, an interview. It reads the words and the sound and
returns, in time order, only the moments that carry a signal: a question, an
absolute claim, a named fight, a burst of laughter, a spike in loudness where
the room reacted. It does not rank them and it cannot: what is funny or damning
is yours to see, and a tool that scored "viral" without watching would be
guessing. What it gives you is where to look.

Read those against the digest and build clips from the clusters, not the single
lines. A clip that travels has a shape: a **hook** in its first seconds -- a
question, a claim, the loud line -- then a **middle** that pays it off, then a
**close** that lands. The signals mark the spikes; you decide which spike opens
a clip and where it ends. A polemic is a hook followed by the reaction to it;
a laugh is a close; a question with no answer in the window is not a clip yet.

For each candidate window, be able to say three things before it becomes a clip:
what the hook is, what sustains the middle, and what closes it. If you cannot,
it is not a moment yet. Go back to the text.

When the campaign ships silent -- the sound is added on the platform, or embedded
audio is forbidden -- the clip has to work with no sound, because that is how it
first plays in the feed. A moment that is only a voice line, a joke that lands on
the delivery, a reveal carried by a change in tone, is nothing muted. Choose a
window that reads on the picture: an expression, an action, a piece of on-screen
text. `warden prefs show --campaign <id>` tells you whether this campaign is one
of those before you pick, not after you render.

## 4. Render inside the rules

`warden cut <source> --campaign <id> --start <s> --end <s> --out <file>`,
adding `--subtitles <srt>` to burn the words and `--hook "<line>"` for the
overlay at the top.

`warden transcribe` writes an `.srt` beside the transcript JSON, and that is
what `--subtitles` takes -- so the words the tool heard can go on the screen even
when the archive shipped no subtitles of its own. Two things it cannot decide for
you. First, whisper mishears, and a wrong word burned on the screen is worse than
no caption: read the transcript before you burn, and if a line is wrong, fix the
srt or leave captions off. The srt does not burn on its own, and that is the same
rule enforced: `warden captions review <srt> --start <s> --end <s>` prints the
lines inside the window and `--approve` signs them, and with no signature `cut`
renders the clip with **no caption at all** rather than burning words nobody
read. So sign after reading, never before, and editing the srt voids it. Second, the tool measures pixels, not meaning, so it
cannot see text the footage already carries -- a lower-third, a channel's own
burned captions, the Prime archive's own subtitles. Burn over those and you have
two. The cut says so every time it burns; look at the first clip, and if the
source already shows text, do not pass `--subtitles`.

A landscape source does not fit 9:16, so a vertical band of it is kept and the
rest is dropped. `--crop` chooses the band, and a side name follows the subject's
face rather than a fixed fraction: `--crop left` centres on the face in the left
half, `right` on the right, `auto` on the most prominent face anywhere. A
percentage (0 far left to 100 far right) overrides detection and places the band
exactly. `cut` prints which pixels it kept and whether a face chose them or it
fell back to the centre; read that line.

Detection is not sight. It follows a face, so a shot with no clear face -- a wide
plate, a creature, an object -- falls back to the centre and says so, and there
`--crop <percentage>` is how you place the band by hand. Look at the first clip
before a batch: a comparison video with the subject off to one side is exactly
what a fixed centre crop gets wrong.

For an edit, add `--track <name|file>` -- a track already kept by
`warden tracks add` is found by its name. `warden beat <track>` shows what it found:
tempo, where the grid starts, how long a bar is, and where the track gains body.
The cut is then a whole number of bars, and the track enters at its drop rather
than at its intro.

An edit is cut to the bar **even when the file ships silent**. A campaign that
requires the sound to be added on the platform still gets an edit, because a
clip that is a whole number of bars long lands on the beat once the owner picks
the track in the app. Tell them which second to start the sound at, which is the
`drop_s` from `warden beat`.

It clamps the length to the campaign's window and tells you when it did. It
strips the audio when the campaign adds its own sound on the platform. It keeps
burned text inside the safe area, because the platform's own furniture covers
the bottom and the right rail and no brief mentions that.

It runs the check on its own output and exits non-zero if the render still does
not clear the campaign. Do not send a clip whose cut exited non-zero.

## 4b. Text before pixels, and the duration the person said

### Pull the words first. Never the video.

```
warden archive --campaign <id> --text-first     subtitles, or audio if none
```

Measured on the 18-minute source of 14/09: the published subtitle comes down in
**5s and 76 KB**; the whole video is **25s and 345 MB**; transcribing it costs
**195s**. Choosing a window is work on TEXT. Pulling 345 MB to find out where to
cut is paying for the video before knowing whether you want it.

So the order is: `--text-first`, read, choose the windows, then
`warden archive` without the flag to pull the video, then cut. That took the
time-to-first-window from **211s to 30s** on the same source.

When the source publishes no subtitle, `--text-first` pulls the **audio** — 15 MB
against 345 MB for the same words — and you transcribe that.

### Then pull only the windows you chose

```
warden archive --campaign <id> --window 181-201.6
→ /var/lib/hermes/warden/footage/<id>/janela-....mp4
→ IN_POINT:2.000
```

It prints `IN_POINT:` because the file it wrote is **not** the source: it is
that window plus a couple of seconds of keyframe slack. Cut it with
`--start <IN_POINT> --end <IN_POINT + length>`, never with the source's own
timestamps, which that file no longer has.

Two things about this path, both measured:

- **It costs disk, not time.** Two windows download in 37s against 25s for the
  whole file, because each section renegotiates the connection. What it saves is
  ~340 MB per source. On an 18-minute video the whole file is fine; on a
  two-hour stream the windows are the only sane way.
- **It goes through the same archive gate.** `archive_window` refuses a link
  that is not in `sources.archive_urls` before a single byte comes down, and
  says so: downloading a slice is still downloading. A fast clip made of
  unauthorised footage is worse than a slow one — it is rejected *after* the
  views, and the clipper is the one who loses the work.

**The window file has its own clock, and that is the trap this path carries.**
What was 179s of the source is 0s in that file; the SRT is still on the
source's clock. The two do not line up on their own, and when they did not,
two clips went out with the OPENING of the video as their caption — hook about
one thing, caption about another, in both — while every gate said fine: hook
fitting, karaoke on, no hanging cue, letter in range, duration as asked, 273
tests green. The contact sheet caught it, because a person looked.

So the window carries a `.origem.json` beside it, and `cut` **requires** it:
given a window-derived file without that file, it REFUSES to burn captions
rather than burn the wrong ones. If you ever build another path that produces a
cut-out of a video, it has to write the same origin file. That refusal is the
feature.

The format selector on this path pins `[protocol^=http]` on purpose. Without it
yt-dlp picks the HLS stream, and `--download-sections` over HLS writes an mp4
**with no video track, silently**. The tool probes the file and deletes it
rather than handing it on.

### The number the person said is the request

```
warden cut … --seconds 20
```

Pass `--seconds` whenever a person said a duration. The cut is moved to it from
the same start, the `-estilo.json` records what was asked, and the delivery gate
rejects a file that misses it.

On 14/09 two clips of "20 segundos" were asked for and 20.6s and 22.2s were
delivered. The renderer was not wrong — it cut exactly the window it was given.
What did not exist was any way for the person's number to reach it: the windows
had been aligned to transcript boundaries and nobody went back to the number.

Only a campaign rule may override it. When one does, `cut` says which rule, and
you repeat that to the person. "It came out a bit longer" is not a reason.

### The batch renders in parallel and reports in order

`warden cut --plan` renders two clips at a time and prints each one **the moment
it is ready** — clip 2 is already rendering while you are looking at clip 1's
contact sheet. Two at a time, not more: the container has 3 GB and a single
render with transcription peaked at 1815 MiB. It beats the clock every 15s while
a render is running, because a minute of silence reads as a dead agent.

That order matters for delivery: look at clip 1's sheet and `send_message` it
straight away. Do not hold the first clip hostage to the last one.

### Do not re-cut four times to find the window

That same session rendered clip 1 **four times** — 128-148, then 157.8-178.3,
then 178.3-201.6, then 181-201.6 — at ~30s a render. Two minutes went into
changing your mind with the video open. Choose the window on the text, in
`--text-first`, where changing your mind is free.

## 5. Look at the clip before you send it

**This is not optional and it is not a review step you may skip when the checks
are green.** A clip was once delivered with the hook cropped off at both edges,
a six-line English caption covering the speaker's face, and the source's own
disclaimer sliced in half along the bottom -- and it was reported as having
passed verification, because verification counts pixels and seconds and every
one of those defects is invisible to arithmetic.

So `warden cut` now writes a contact sheet of its own render and prints it:

```
SHEET:/var/lib/hermes/cache/videos/clip-01-contato.jpg
MEDIA:/var/lib/hermes/cache/videos/clip-01.mp4
```

**Open that image with the Read tool before you call `send_message` for that
clip** -- the sheet is what stands between a render and a delivery, and section 6
is where the delivery happens. Then say, in your own words, what you saw. Eight
things, and every one of them rejects the clip on its own:

- [ ] the hook fits inside the frame, uncropped, in at most two lines
- [ ] the hook **left**: it is on the first tiles of the sheet and not the last
- [ ] the caption is at most two lines
- [ ] no cue ends mid-sentence — on a preposition, article or conjunction
- [ ] the yellow highlight tracks the speech, instead of a whole cue lit at once
- [ ] there are not two captions in the same frame
- [ ] no frame edge, source border or third party's text is sliced at the margin
- [ ] the subject's face is not covered by text

A clip that fails any of them is rejected **even if every check passed**. Re-cut
it -- a different window, `--crop`, no `--subtitles` -- and look again. Never
send a clip whose sheet you did not open, and never write "it passed
verification" about a file you have not seen.

If `warden cut` says it could not build the sheet, that is not a detail to
mention in passing: nothing has looked at that clip, so it does not ship. It
withholds the `MEDIA:` path itself in that case, so there is no path to put in a
`send_message` and that clip is not one of the ones you deliver.

## 6. Send, and deliver the number that was asked for

### Deliver each clip with `send_message`. Writing MEDIA: in your narration does nothing.

**The rule itself lives in the persona, under "Handing the file over". It is
written there and nowhere else; what follows is the evidence behind it and what
it means for a batch.**

It was written from a measurement.

On 14/09 two clips were asked for, both rendered, both announced as ready, and
**one arrived**. The agent wrote `MEDIA:/…/clip-piloto-emirates.mp4` at 21:57:59
in the middle of a turn and moved on to the next clip. The gateway log for that
turn is one line: at 21:59:18 it sent one response and delivered one attachment
— the second clip's. Between 21:46:59 and 21:59:18 nothing at all left the
machine. Four assistant messages from that turn, including the one carrying the
first clip, are in the database and were never sent.

**Only the last message of a turn is delivered.** Text you write between tool
calls is narration: it stays on this side. A `MEDIA:` line inside it attaches
nothing, and nothing anywhere reports that it attached nothing.

So a clip is delivered by CALLING A TOOL, never by writing a line and hoping:

```
send_message(target="plow_chat",
             message="Corte 1: <caption>\n\nMEDIA:/var/lib/hermes/cache/videos/clip-01.mp4")
```

`send_message` is what the file rides on — its own description says so: "To send
an image or file, include MEDIA:<local_path> in the message — the platform will
deliver it as a native media attachment." One call per clip, and the call
happens **immediately after you looked at that clip's contact sheet**, not at
the end of the batch. `--plan` batches the renders; it does not batch the sends.

### Read the result. A send you did not confirm is not a delivery.

`send_message` returns a result. Read it. If it did not succeed, say so in the
conversation, in words, and call it again:

> "O corte 1 não foi anexado na primeira tentativa. Reenviando."

Going on to the next clip without looking at the result is how the first one
disappeared with nobody — not even the agent — noticing.

### "Os N clipes estão prontos" is a forbidden sentence until every file is confirmed

Do not write that the batch is ready, or done, or delivered, in any wording,
while a single clip is still unconfirmed. **Rendered is not delivered. Pronto is
in the person's hand, not on the disk.** The count you report is the count of
`send_message` calls that came back successful — never the count of files you
rendered.

The last message of the turn says what actually arrived, by name:

> "Entreguei os 2: corte 1 (Emirates) e corte 2 (chave de buceta). Ambos
> confirmados."

and if one did not make it, that is the sentence instead:

> "Entreguei 1 de 2. O corte 2 falhou no envio três vezes; o arquivo está em
> <caminho>. Não está entregue."

### Two clips asked for means two clips delivered

**Do not end your turn while the number of files you have delivered is smaller
than the number you were asked for.** "One good clip" is not a batch of two, and
stopping at the first is the exact failure this rule exists for: two cuts were
asked for, one arrived, and nothing accused the shortfall.

For more than one clip, write a plan. The tool holds the count of renders; you
hold the count of sends, and only the second one is the count you report:

```json
{
  "campaign": "acme-set", "source": "/path/source.mp4", "sound": "platform",
  "seconds": 20,
  "clips": [
    {"out": "corte-01.mp4", "start": 312.0, "end": 332.0,
     "hook": "ele apostou contra o favorito",
     "_": "gancho: a claim absurda, e a reacao da mesa fecha"},
    {"out": "corte-02.mp4", "start": 745.5, "end": 765.0, "seconds": 25,
     "hook": "o numero que ninguem esperava",
     "_": "gancho: a pergunta; o meio paga; fecha na risada"}
  ]
}
```

`seconds` is `--seconds` inside the plan and carries the same number the person
said: put it at the top when they named one duration for the batch, and on a
clip when that clip was asked for at a different one. Leave it out only when
nobody named a duration -- a plan without it is the 14/09 defect written down,
where the windows are transcript boundaries and the person's number never
reaches the renderer.

`warden cut --plan lote.json` **renders and clears; it does not deliver**, and
it says so itself on its first line:

```
# 2 clips asked for. This command RENDERS and clears them; it does not deliver.
...
# 2 of 2 cleared for delivery. NONE of them has been sent by this command.
# now send the 2 of them, one send_message each, and read every result.
```

That last line is your instruction, not a summary: N clips cleared on disk are N
`send_message` calls still owed. Work down the list in order -- read that clip's
sheet, send that clip, read the result, then the next -- and send the first one
as soon as its sheet is clear instead of holding the batch until the last one is
open.

`--plan` exits non-zero when a clip is missing and names which and why. A clip it
did not clear has no `MEDIA:` path, so it is not sent and it does not count: say
exactly which one failed and for what reason. Do not report a batch as finished
while it is short.

The `_` field on each clip is what that cut is *for* -- the hook, what sustains
it, what closes it. Write it before you render. A window you cannot justify in a
sentence is not a clip yet.

Tell them before you start that a source of an hour takes ten to thirty minutes.
The first clip leaves on its own confirmed `send_message` as soon as its sheet is
clear; nothing waits for the last render. "As soon as it exists" means sent and
confirmed, not rendered -- a file on disk that nobody called `send_message` for
is the clip that vanished on 14/09.
