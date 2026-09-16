---
name: warden-clip
description: Go from a campaign's authorised archive to finished vertical clips. Use when the person wants clips made, sends a campaign link and asks for cuts, or points at footage and asks what would work.
---

# Archive to clips

The order is fixed and it is not a preference.

```
warden lote prep  ->  you choose the windows  ->  warden lote render  ->  deliver
```

`lote` is the short road and it is the default one: `prep` does the gate, the
words and the signals in one output, and `render` does the windows, the clips,
one combined contact sheet and every `MEDIA:` line together at the end. The long
road is the same order by hand, and it is what the numbered sections below
describe:

```
gate -> WORDS (--text-first) -> chosen windows -> --windows -> render -> deliver
```

The sheet is written and it is not a step you wait on: since 16/09/2026 you
deliver without opening it. Section 6 has the owner's decision and what it cost.

**This is the only copy of the order of work in this repository.** The persona
points here, `warden-run` points here, and neither carries a second version. If
you find one somewhere, it is stale and this section wins.

## Run `prep` and `render` IN THE BACKGROUND. Always. No exception.

Your terminal kills any foreground command at **300 seconds**, and both of these
routinely pass it: `prep` downloads and transcribes, `render` cuts and burns.

Measured 16/09/2026, on a real request: `lote prep` was run in the foreground,
was killed at 301.58s with `[Command timed out after 300s]`, and produced
nothing. The same command, re-run in the background straight after, finished in
about twenty seconds — because the download the killed run had already paid for
was in the cache. **Five minutes of the person's eleven were that one mistake,
and the work was done the whole time.**

So: `background=true` on the call, then wait on the session id, then read the
log file. Never the foreground, not even "just this once because the video looks
short" — you cannot see the length before you pull it, and the cost of being
wrong is five minutes of somebody watching a screen with nothing on it.

**The video is the last thing you pull.** Any instruction anywhere that reads
"pull the footage, then find the moment" is the old order and it is wrong. How
MUCH of the video to pull, once the moment is chosen, is decided in 4c and
nowhere else. Measured on the 18-minute source of
15/09: the published subtitle is **5s and 89 KB** and covers the whole video,
while transcribing that same video costs **3min46s**. That is the whole reason
this order exists.

## 0. The link may be one message behind you

When the words point at a link that is not in the text -- "esse vídeo", "esse
link", "abaixo", "essa música" -- do not answer "faltou o link". Run:

```
warden inbox --wait 12
```

Measured 15/09, four for four: the link comes as its OWN message, a moment
after the one that asks for the clip -- the person's app sends it that way even
when they typed it together. In the last run the wait was 20s, the question went
out, and the link landed one second later. The card the person sees is drawn by their app and never reaches you.
Exit 0 gives you the URL; exit 1 after the wait is when asking becomes fair.

## 1. The gate, and the rule set when there is one

**A link someone sent you is authorised by their having sent it.** That is the
owner's decision of 15/09 and it holds for anyone in the conversation. There is
no list to check, nothing to ask, and no licence to request.

```
warden archive --trusted <url> ...      a bare link. `--link` is the same flag
warden archive --campaign <id> ...      the campaign's own archive
```

With a campaign, store it first -- `warden-campaign` does that, and the renderer
takes its duration and resolution from the rule set, so `warden campaign show
<id>` coming back empty means you are not ready to cut.

`warden cut` no longer needs `--campaign`. A bare link is cut against a blank
rule set, where every field is `unchecked` rather than invented. Do not make up a
campaign to get past a required flag; that was the road into the caption question
that stopped every first clip.

## 2. Pull the words. Never the video.

```
warden archive --campaign <id> --text-first     subtitles, or audio if none
warden archive --trusted <url> --text-first     the same, for a bare link
```

This is the first command you run. It pulls what gives you the WORDS and no
video at all: the published subtitle if the source has one, the audio track if
it does not.

Measured on the 18-minute source of 15/09: the published subtitle comes down in
**5s and 89 KB** and covers the whole video with 1007 lines; the whole file is
**13s and 382 MB**, and transcribing it costs **3min46s**. Choosing a window is
work on TEXT. Transcribing 18 minutes of audio to use 40 seconds of it is paying
four minutes for something that was already written down.

`warden archive --campaign <id>` downloads from `sources.archive_urls` and from
nowhere else, with or without the flag. If it says the rule set publishes no
archive, stop and ask for the link. Do not go looking for the footage. A clip
built from material the campaign did not authorise is rejected after the views,
which is the loss this agent exists to prevent. If a download fails, `archive`
says which link and why on its own line; pulling footage by hand from outside
the archive rebuilds the exact failure this skill exists to prevent. Send the
owner the failing link instead.

### A specific link the owner sends

With a campaign, `warden authorize <link> --campaign <id>` answers ONE question:
does a submission with this video risk rejection? It expands the playlists,
matches the id and prints the title. Exit 0, nothing to say. **Exit 1, you cut it
anyway** and add one clause to that clip's message naming the video: "esse vídeo
não está no acervo da campanha, então a submissão pode ser recusada." You do not
ask, you do not wait for a yes, and you do not refuse.

Without a campaign there is no question at all.

### When the source refuses to come down at all

When `archive` prints the bot-check diagnosis ("Sign in to confirm you're not a
bot"), it is **every link and not this one**: the refusal is on the request, so
it fires on `--text-first` too, before any window exists, and another YouTube
link comes back the same way. Asking for a different link costs a round trip and
lands here again.

What you say is one message offering their own file, and it is written in the
persona under "The failure you repass". Not repeated here.

`archive` prints the diagnosis itself, including whether a cookies file was
configured and used. **Repass what it printed and do not put a cause of your own
in front of it.** The two paths it names are a different outgoing address, or a
cookies file at `/var/lib/hermes/warden/cookies.txt` from a throwaway logged-in
account -- never the owner's own, because yt-dlp's own warning is that this can
get the account banned.

**Measured 15/09: a different outgoing address fixes it, and that is now a
measurement rather than a promise from a manual.** The residential address was
refusing every link; through a phone's connection the same container, with no
cookies and nothing else changed, pulled the published subtitle and a window
download normally. What is NOT measured is whether a marked address ever clears
on its own, so you never say it will, and you never say it will not. Say what the
tool measured, offer the file, and if they send one you are cutting in seconds
instead of waiting.

**The one case where it IS that video**, and the tool tells the two apart so you
do not have to: an age-restricted, private, members-only or removed video comes
back as an ordinary yt-dlp error in yt-dlp's own words, NOT as that diagnosis.
Repeat whichever of the two the tool handed you, in one line, and never upgrade
one into the other.

## 3. Read the words and choose the windows

What `--text-first` wrote is what you read. When it brought the published
subtitle, the words are already there. When it brought the audio instead,
`warden transcribe <audio>` writes words with timing from it -- the audio file,
never a video, because there is no video yet. Then `warden digest <transcript>`
gives you a version you can afford to read.

Never read the raw transcript JSON. Word-level timing is for the renderer, and
on a long source the raw artefact does not fit in a window worth paying for.

### Finding the moments that travel

`warden signals <transcript> --source <audio>` is where you start on a long
source -- a podcast, a live, an interview. `--source` is the audio `--text-first`
brought down; it reads loudness, and audio is all it needs. It reads the words and the sound and
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

## 4. Pull ONLY the windows you chose

```
warden archive --trusted <url> --windows 181-201.6,745.5-765      the whole batch
warden archive --campaign <id> --window 181-201.6                 one window
→ /var/lib/hermes/warden/footage/<id>/janela-....mp4
→ IN_POINT:2.000
```

**Ask for the whole batch at once.** `--windows` takes every window of the batch
comma separated and pulls them in ONE yt-dlp run: measured 15/09, **31s for two
windows against 38s** as two separate commands. One `IN_POINT:` line comes back
per file, in the order you asked.

It prints `IN_POINT:` because the file it wrote is **not** the source: it is
that window plus a couple of seconds of keyframe slack. Cut it with
`--start <IN_POINT> --end <IN_POINT + length>`, never with the source's own
timestamps, which that file no longer has.

Two things about this path, both measured:

- **It costs disk, not time, and on a short source it costs time too.** Measured
  15/09 on the 18-minute video: the whole file is **13s and 382 MB**; two
  windows are **31s and 14 MB**. The reason is in yt-dlp's own documentation --
  `--download-sections` "needs ffmpeg", and ffmpeg remuxes the section in real
  time at about 1.8x, so a 28-second window costs ~17s of ffmpeg no matter how
  fast the connection is. **The rule that comes out of those two numbers is in
  4c.**
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
tests green. The contact sheet caught it, back when opening it was mandatory,
because a person looked.

So the window carries a `.origem.json` beside it, and `cut` **requires** it:
given a window-derived file without that file, it REFUSES to burn captions
rather than burn the wrong ones. If you ever build another path that produces a
cut-out of a video, it has to write the same origin file. That refusal is the
feature, and since 16/09 it is also the only thing standing there: nobody opens
the sheet before a delivery any more (section 6).

The format selector on this path pins `[protocol^=http]` on purpose. Without it
yt-dlp picks the HLS stream, and `--download-sections` over HLS writes an mp4
**with no video track, silently**. The tool probes the file and deletes it
rather than handing it on.

### 4c. How much of the video to pull, decided once

```
warden archive --campaign <id>                  the WHOLE file
```

**Under about half an hour of source, pull it whole. Over that, pull the
windows.** That is the whole rule, and it comes from the two numbers in section
2: on the 18-minute source the whole file was 13s and two windows were 31s, so
below that size the windows cost time and save only disk. On a two-hour stream
they are the only sane way.

Pull it whole regardless of length when: the source publishes no subtitle **and**
`--text-first` could not get the audio either; or the owner asked for the whole
thing; or you need more of the source than the windows -- a montage across the
hour, a search for a shot you cannot place on the text.

Do not announce which of those it was. Each file it prints
is a path you pass straight to `warden cut`, on the SOURCE's own clock -- it
names them itself and pulls one video per playlist link, so there is nothing to
rename and no stray file to sort through.

## 5. Render inside the rules

`warden cut <source> --campaign <id> --start <s> --end <s> --out <file>`,
adding `--subtitles <srt>` to burn the words and `--hook "<line>"` for the
overlay at the top.

`warden transcribe` writes an `.srt` beside the transcript JSON, and that is
what `--subtitles` takes -- so the words the tool heard can go on the screen even
when the archive shipped no subtitles of its own.

### Who signs the captions, on each road

**This is the only place this is written.** The persona, `warden-run` and
`warden-style` point here; none of them carries a second version.

The srt never burns on its own. Something has to SIGN the window, and with no
signature `cut` renders the clip with **no caption at all** rather than burn
words nobody read. Who signs depends on which road you took:

| road | who signs |
|---|---|
| `warden lote render` | the tool signs the clean lines itself, per window. You do not run `captions review`, and nobody is asked |
| `warden cut` on its own | YOU sign, first: `warden captions review <srt> --start <s> --end <s> --approve` |

**A suspect line is a warning now, not a refusal.** A number, a repeated word,
an auto-caption `>>` marker: the tool names the line, prints it as it appears on
screen, and burns the caption anyway. You read the line the tool printed -- in
its output, not on a mosaic you open -- and if it is wrong you say so in ONE
line when you hand the clip over.

Until 16/09 one suspect line rendered the whole clip WITHOUT captions until the
line was repeated back in `--keep`. Measured that day on a real YouTube live:
`>>` in 184 of 706 cues, and **2 clips of 2 came out with no words on screen,
twice** -- which is the owner's complaint, word for word. A check that fires on
every clip is not protecting the clip, it is switching off the product, and the
owner's rule is that captions are the one thing he waits for.

So: if a burned line is wrong, say so in ONE line when you hand the clip over.
Do not re-render for it unless he asks. `--keep "<the line>"` still exists and
now means only "I read this one, stop warning me" -- it no longer decides
whether the clip has captions at all.

Reading is still the point: whisper mishears, and "Em 1826" went to the screen
on 15/09 because a line was flagged and signed in the same breath. The place you
read it is the tool's own output, which prints each flagged line as it appears
on screen -- that costs you no vision call and no extra turn. This is never a
question to the person: you repass what was flagged, you never ask permission to
caption.

### What the footage already carries

Second, the tool opens eight frames of the window
before it renders and measures the edge density of the bottom band, which finds
text the footage already carries -- a lower-third, a channel's own burned
captions, the Prime archive's own subtitles. Burn over those and you have two.

It finds them in three states, not two. A fixed band (a permanent disclaimer)
trips the hard boolean and `cut` covers it with the gradient footer. A thin,
centred, intermittent caption -- a vlog's own subtitles, which vanish between
sentences -- often does not trip it: measured on the cut that shipped two
captions, one frame in eight voted and the average was 1.7x the middle of the
frame, under the 1.9x the boolean asks for. That is now a third state, **suspect**,
and `cut` treats it like the first one: it covers the footer anyway, because a
gradient over clean footage costs a gradient and a second caption costs the clip.
Then `warden style check` reads the sidecar and rejects the file if a suspect
bottom was left uncovered under our caption.

`cut` prints a line about the footage **every time it burns a caption**, with the
numbers, including when it found nothing -- before this, "clean" and "nobody
looked" were the same empty output. Read that line. When it says it found or
suspected text, that is a warning you repass in ONE clause beside the clip and
the person decides; you do not open a frame to settle it. Only if HE says the
source's own text should stay do you re-cut without `--subtitles`.

A landscape source does not fit 9:16, so a vertical band of it is kept and the
rest is dropped. `--crop` chooses the band, and a side name follows the subject's
face rather than a fixed fraction: `--crop left` centres on the face in the left
half, `right` on the right, `auto` on the most prominent face anywhere. A
percentage (0 far left to 100 far right) overrides detection and places the band
exactly. `cut` prints which pixels it kept and whether a face chose them or it
fell back to the centre; read that line.

Detection is not sight. It follows a face, so a shot with no clear face -- a wide
plate, a creature, an object -- falls back to the centre and says so, and there
`--crop <percentage>` is how you place the band by hand. When `cut` prints that
it fell back to the centre, that is the warning you repass in one clause -- a
comparison video with the subject off to one side is exactly what a fixed centre
crop gets wrong, and the person is the one who knows which video that is.

### The edit, and what it actually is

An edit is not a window with music over it. Measured on the reference the owner
gave (`youtube.com/shorts/QmrLImO6fus`), 129.2 BPM:

| | The reference | The "edit" of 15/09 |
|---|---|---|
| Scene changes | 10 to 13 in 15.8s = **12.6 to 16.4 per 20s** | 12 in 20.8s |
| Error against the beat | **median 33ms**, 8 of 10 under 80ms | median 122ms, 4 of 12 |
| Where the cuts came from | chosen | **the source video's own cuts** |
| Caption | none -- a fixed score graphic | none |

4 of 12 under 80ms is what chance gives you on a 650ms grid. So the old edit was
a continuous window with music on top, and the scene changes in it were the ones
the footage already had.

```
warden cut <src> --campaign <id> --shots 71.4-74.0,77.0-79.5,80.5-83.0 \
  --track <name> --subtitles <srt> --any-length --out edit.mp4
```

`--shots` is the list of pieces to splice, **on the file's own clock**, like
`--start`. Each one's length is snapped to a whole number of beats of the track,
so every scene change lands on the music: measured 0ms of error on a 7-shot
edit. Two beats is the short shot, four the long one, which is the reference's
own grammar. The sidecar records every cut and its error, so "it cuts on the
beat" is a number and not an impression.

Each shot gets its own light zoom, alternating in and out. The framing is done
once, after the splice: cropping shot by shot would give the same scene a
different frame each time.

**Two things the renderer will refuse, and both are the point:**

- **The caption becomes a collage.** With shots taken from all over the source,
  each one opens in the middle of a sentence and the caption reads "portas, a
  que vai pra sala / e a que vai Você lembra," -- measured, from a real render.
  When more than half the spliced stretches open mid-sentence, it ships with no
  caption and says so. Fix it by starting each shot where a line of speech
  starts, or drop `--subtitles` and put a written line on it with `--hook`,
  which is what the reference does.
- **The source already burns its own captions in one of the shots.** The frames
  it samples come from the SHOTS, not from a continuous stretch -- sampling
  `start..start+length` on an edit looks at footage the clip never shows, and
  that is how a second caption got into a render on 15/09 and was only caught in
  the full-resolution frame.

The source's speech is dropped on a spliced edit: continuous speech over a
picture that jumps is talking over the wrong scene. The track is the audio, and
what the speech had to say is in the caption.

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

## 5b. The duration the person said, and the batch

### The number the person said is the request

```
warden cut … --seconds 20
```

Pass `--seconds` whenever a person said a duration. The cut is moved to it from
the same start, the `-estilo.json` records what was asked, and the delivery gate
rejects a file that misses it.

On 14/09 two clips of "20 segundos" were asked for and 24,5s and 15,4s came
back -- 22% over and 23% under, in the same batch -- because the number never
became a parameter and nothing compared the result to it. Those are the numbers;
they are measured once, here, and no other file repeats them. What was delivered. The renderer was not wrong — it cut exactly the window it was given.
What did not exist was any way for the person's number to reach it: the windows
had been aligned to transcript boundaries and nobody went back to the number.

Only a campaign rule may override it. When one does, `cut` says which rule, and
you repeat that to the person. "It came out a bit longer" is not a reason.

### The batch renders ONE at a time and reports in order

`warden cut --plan` renders **one clip at a time** and prints each one the moment
it is ready. `WARDEN_RENDER_PARALELO=2` turns on two, and it is deliberately off:
two in parallel was measured at 64s against 70s sequential -- a 9% gain -- with
the peak at 3035 MiB of the container's 3072 MiB limit. Speed the OOM killer
interrupts is not speed.

It prints a line every 15s while a render is running. That line is for YOU, in
the command output; it is not something to relay. The person heard a number in
your first message and that is the whole of what they hear until a clip exists.

When the batch finishes, end the turn with ALL of the `MEDIA:` lines in one
final message -- the block `--plan` prints at the end is exactly that message.

### Do not re-cut four times to find the window

That same session rendered clip 1 **four times** — 128-148, then 157.8-178.3,
then 178.3-201.6, then 181-201.6 — at ~30s a render. Two minutes went into
changing your mind with the video open. Choose the window on the text, in
`--text-first`, where changing your mind is free.

## 6. The mosaic stopped being the gate on 16/09/2026

**Do not open the contact sheet before you deliver.** Your obligation is two
things, the hook and the caption, and `cut` refuses on both of them by itself.
Everything else, you deliver and repass.

### Why this rule existed, so nobody rebuilds it by accident

A clip was once delivered with the hook cropped off at both edges, a six-line
English caption covering the speaker's face, and the source's own disclaimer
sliced in half along the bottom -- and it was reported as having passed
verification, because verification counts pixels and seconds and every one of
those defects is invisible to arithmetic. That is real, it is why `warden cut`
started writing a contact sheet of its own render, and until 16/09/2026 opening
that image was a condition of sending: `deliver` refused to print `MEDIA:`
without it.

### Why it changed, and what it cost

Then the price was measured. **16/09/2026, on a real 706-second request: the two
vision calls of that request cost 31 seconds**, and the owner runs this on a
screen-share call while somebody watches. Asked directly whether the mosaic
should stay mandatory before a delivery, his answer was:

> "2 = entrega sem olhar, sua unica obrigacao = hook e legenda"

Same day, same person: *"quero o mais rapido possivel com o minimo de
qualidade"*, *"Velocidade acima de tudo"*.

**What is lost is the defect in the paragraph above.** A fault that only the
picture shows -- a cropped hook on a bright plate, a second caption from the
source -- is no longer caught here. It reaches the owner's hands, and he is the
one who sees it. He decided that with the 31 seconds in front of him. Do not
quietly put the gate back because a clip came out wrong: that is his call to
reopen, not yours, and if it is reopened it gets a new date and a new
measurement in this section.

### What the tool does now

`warden cut` still writes and prints the sheet, and `lote render` still stacks
one combined sheet for the whole batch:

```
SHEET:/var/lib/hermes/cache/videos/clip-01-contato.jpg
MEDIA:/var/lib/hermes/cache/videos/clip-01.mp4
```

`MEDIA:` is printed with or without it. The sheet costs about 1.3s to write and
it stays because it is the only visual evidence that exists later, when
something comes back wrong and somebody asks what shipped. **You do not spend a
vision call on it**, and a render with no sheet is delivered like any other.

**A warning in the output is repassed, not investigated.** When `cut` says a
caption line is suspect, that the footage may already carry burned text, or that
the crop fell back to the centre with no face found, that goes to the person in
ONE clause beside the clip -- *"pode ter texto queimado no topo desse, olha
antes de subir"* -- and he decides. Pulling a frame or re-rendering to settle it
yourself is the ten minutes he told you not to spend.

### If somebody does look, this is the list

This is no longer a gate and no item here rejects a clip on its own. It is what
to check when the owner asks you to look at a clip, or when one comes back
wrong and you are finding out why:

- [ ] the hook fits inside the frame, uncropped, in at most two lines
- [ ] the hook **left**: it is on the first tiles of the sheet and not the last
- [ ] the caption is at most two lines
- [ ] no cue ends mid-sentence — on a preposition, article or conjunction
- [ ] the yellow highlight tracks the speech, instead of a whole cue lit at once
- [ ] there are not two captions in the same frame
- [ ] no frame edge, source border or third party's text is sliced at the margin
- [ ] the subject's face is not covered by text
- [ ] no black bar along any edge -- `warden check` rejects one now

A tile is 300px wide: enough to notice, never enough to conclude, so a tile that
makes you suspicious is settled with `ffmpeg -ss <t> -i <clip> -frames:v 1
-update 1 /tmp/q.png`. Reading the `.ass` to settle a question about two
captions is the one thing that cannot work -- only ours is in it. And **you
never re-render a clip that passed**: five vision calls and three renders of one
correct cut cost ten minutes on 15/09, and nothing bad had shipped.

What you still never write is "it passed verification" about a file nobody has
seen. Nobody has seen it, and that is now the normal case.

## 7. Send, and deliver the number that was asked for

**The delivery rule lives in the persona, under "Handing the file over", and it
is written there and nowhere else.** In one line: render every clip they asked
for -- up to three -- before you answer, then ALL of the `MEDIA:` lines go in the
LAST message of the turn, with no tool call after it. What follows is only the
evidence and what it means for a batch.

`MEDIA:` mid-turn attaches nothing, in silence, and the prose around it still
arrives -- the person reads "aqui está o primeiro corte" and no file comes. That
is 5 occurrences out of 5 requests for more than one clip. Every `MEDIA:` in a
final message was attached: 8 out of 8, including twice with two lines together.

| where the `MEDIA:` line was | attached |
|---|---|
| in a message that still called a tool | 0 of 5 |
| in the final message of the turn | 8 of 8 |

`warden delivered` runs at the **START of the next turn**, never before ending
the one that delivers: the attachment leaves after the final message, so a check
inside that turn can only ever say "not yet". It reads each path and answers for
that exact file; when it cannot verify, it says it could not, and you repeat that
rather than guessing.

Do not write that the batch is ready, or done, or delivered, while a clip is
unconfirmed. **Rendered is not delivered.** The count you report is the count of
attachments that left, never the count of files on disk.

> "Entreguei os 2: corte 1 (Emirates) e corte 2 (o cartão)."

and if one did not make it, that is the sentence instead:

> "Entreguei 1 de 2. O corte 2 não foi anexado, estou reenviando."

### The plan, for more than one clip

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

`seconds` is `--seconds` inside the plan and carries the number the person said:
at the top when they named one for the batch, on a clip when that clip was asked
for at a different one. Leave it out and the stored default (20s) applies.

The `_` field is what that cut is *for* -- the hook, what sustains it, what closes
it. Write it before you render. A window you cannot justify in a sentence is not
a clip yet.

`warden cut --plan lote.json` renders one clip at a time; the numbers behind that
choice are in 5b and nowhere else. It **renders and clears; it does not
deliver**, and at the end it prints the final message for you to copy,
with every `MEDIA:` line in it. Copy that block and end your turn with it.

**Above three clips, use `warden lote render`, not `cut --plan`.** Only `lote
render` splits the batch: it delivers the first three in the final message and
names the ones it did not render. **Those are not rendering anywhere and nothing
will wake you for them** — the command says so itself and prints the exact
`warden cut --plan <file>` that brings them. Do not write that they are on the
way; say they come when the person asks. `cut --plan` prints every `MEDIA:` line
it rendered, however many, and a final message with six attachments is not what
the persona's rule asks for.

`--plan` exits non-zero when a clip is missing and names which and why. A clip it
did not clear has no `MEDIA:` path, so it is not sent and it does not count: say
exactly which one failed and for what reason. Do not report a batch as finished
while it is short.

The number you give in your FIRST message is the whole of what they hear before
the first clip. You say it once, there, and never again.
