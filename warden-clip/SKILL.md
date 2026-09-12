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

## 3. Choose on the text, never on the video

`warden transcribe <file>` writes words with timing, preferring subtitles the
archive published over transcribing. Then `warden digest <transcript>` gives you
a version you can afford to read.

Never read the raw transcript JSON. Word-level timing is for the renderer, and
on a long source the raw artefact does not fit in a window worth paying for.

For each candidate window, be able to say three things before it becomes a clip:
what the hook is, what sustains the middle, and what closes it. If you cannot,
it is not a moment yet. Go back to the text.

## 4. Render inside the rules

`warden cut <source> --campaign <id> --start <s> --end <s> --out <file>`,
adding `--subtitles <srt>` to burn the words and `--hook "<line>"` for the
overlay at the top.

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

One message per clip: the file, the caption from `warden-package`, and the one
thing they must do on the platform, which is usually the sound.

Tell them before you start that a source of an hour takes ten to thirty minutes,
and give them the first clip as soon as it exists rather than the batch at the
end.
