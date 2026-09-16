# The plan file, for several clips in one run

`warden-clip/SKILL.md` section 7 points here. The rules about `--plan` are
there; this is the shape of the file it reads.

```json
{"campaign": "acme-set", "source": "/path/source.mp4", "sound": "platform",
 "seconds": 20,
 "clips": [{"out": "clip-01.mp4", "start": 312.0, "end": 332.0, "seconds": 25,
            "hook": "<the written line>", "_": "hook / middle / close"}]}
```

- `campaign` is the stored rule set's id; leave it out for a bare link and the
  cut runs against a blank rule set.
- `source` is the file on disk, never a URL.
- `sound` is `platform` when the campaign puts its own track on after upload,
  which is what drops the audio from the render.
- `seconds` at the top is the default for the batch; inside a clip it overrides
  that one. **It is the number the person said**, and the delivery gate rejects
  a file that misses it.
- `start` and `end` are on the clock of the file in `source`. When that file
  came from `warden archive --window`, they start at its `IN_POINT:`, never at
  the original video's timestamps.
- `hook` is the line you wrote, with the one or two key words marked by
  asterisks — `warden-style` has how it is chosen.
- `_` is what that cut is FOR, in one sentence: hook, middle, close. A window
  you cannot justify in a sentence is not a clip yet, and this field is the
  place that check happens.

`--plan` renders and clears but **does not deliver**: it prints the final
message and exits non-zero naming any clip that is missing.
