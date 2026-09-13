---
name: warden-clip
description: Go from a campaign's authorised archive to finished vertical clips. Use when the person wants clips made, sends a campaign link and asks for cuts, or points at footage and asks what would work.
---

# Archive to clips

The order is fixed and it is not a preference.

```
rule set  ->  archive  ->  text  ->  chosen windows  ->  render  ->  check  ->  send
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
srt or leave captions off. Second, the tool measures pixels, not meaning, so it
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

For an edit, add `--track <audio>`. `warden beat <track>` shows what it found:
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

## 5. Send

On a render that clears, `warden cut` prints the path and then the line that
delivers it:

```
/var/lib/hermes/cache/videos/clip-01.mp4
MEDIA:/var/lib/hermes/cache/videos/clip-01.mp4
```

The second line goes into your reply on a line of its own, copied exactly. That
is what attaches the file; without it the person gets prose about a clip they
cannot open. It is stripped from what they read, so it costs nothing to include
and everything to leave out.

One message per clip: that line, the caption from `warden-package`, and the one
thing they must do on the platform, which is usually the sound.

Tell them before you start that a source of an hour takes ten to thirty minutes,
and give them the first clip as soon as it exists rather than the batch at the
end.
