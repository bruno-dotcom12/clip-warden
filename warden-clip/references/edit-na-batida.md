# The edit, on the beat

`warden-clip/SKILL.md` section 5 points here and tells you to read this before
you cut one. An edit is **not a window with music over it** — it is a different
product, and every rule it has is on this page.

## The measurement that says what an edit is

Measured against the reference the owner gave (129.2 BPM):

| | the reference | the "edit" of 15/09 |
|---|---|---|
| scene changes | 10 to 13 in 15.8s = **12.6 to 16.4 per 20s** | 12 in 20.8s |
| error against the beat | **median 33 ms**, 8 of 10 under 80 ms | median 122 ms, 4 of 12 |
| where the cuts came from | chosen | **the cuts the source already had** |
| caption | none — one fixed scoreboard graphic | none |

4 of 12 under 80 ms is what chance gives on a 650 ms grid. So the old edit was a
continuous window with music on top, and its scene changes were the ones the
picture already carried.

## The command

```sh
warden cut <src> --campaign <id> --shots 71.4-74.0,77.0-79.5,80.5-83.0 \
  --track <name> --subtitles <srt> --any-length --out edit.mp4
```

**`--shots` is the list of pieces to splice, on the file's own clock**, exactly
like `--start`. Each one's length is snapped to whole beats of the track, so
every scene change lands on the music: measured **0 ms of error on a 7-shot
edit**. **Two beats is the short shot, four the long** — that is the grammar of
the reference itself. The render records each cut and its error, so "cuts on the
beat" is a number and not an impression.

Each shot gets its own slow zoom, alternating in and out. **The clip is framed
ONCE, after the splice**: framing shot by shot would give the same scene a
different frame every time it came back.

## Two things the renderer REFUSES, and both are the point

- **The caption turns into a collage.** With shots taken from all over the
  source, each one opens mid-sentence, and a real render read as two half
  sentences glued together. **When more than half the spliced pieces open
  mid-sentence, it ships with no caption and says so.** The fix is to start each
  shot where a phrase starts, or to drop `--subtitles` and put one written line
  in `--hook`, which is what the reference does.
- **The source burns its own caption into one of the shots.** The frames it
  checks for that are sampled from the SHOTS, never from a continuous stretch:
  sampling `start..start+length` on an edit looks at picture the clip never
  shows, and that is how a second caption got into a render on 15/09 and was
  only caught on the full-resolution frame.

## The audio

On a spliced edit **the source's speech is discarded**: continuous speech over
jumping picture is talking across the wrong scene. The track is the audio, and
what the speech had to say is in the caption.

## The track and the grid

Add `--track <name|file>`; a track already stored by `warden tracks add` is
found by name. `warden beat <track>` prints what it found: tempo, where the grid
starts, how long a bar is, and where the track gains body. The cut is then a
whole number of bars, and the track comes in on the drop instead of the intro.

**An edit is cut to the bar even when the file ships mute.** A campaign that
requires the sound to be added on the platform still gets an edit, because a
clip whose length is a whole number of bars lands on the beat the moment they
pick the track in the app. **Tell them which second to start the sound on — that
is `drop_s` from `warden beat`.** On a mute edit it is the only deliverable
besides the file.

## The rest

It clamps the duration to the campaign's window and says when it clamped. It
drops the audio when the campaign puts its own sound on the platform. And it
keeps burned text inside the safe area, because the platform's own furniture
covers the bottom strip and the right-hand column, and no brief ever mentions
that.
