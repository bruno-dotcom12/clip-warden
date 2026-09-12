# Clip Warden

Paid clipping campaigns reject submissions **after** the views have accrued, and
a rejected clip is unpaid work. The reasons are almost always mechanical: a
second over the limit, a missing hashtag, a scratch audio track on a campaign
that adds its own sound on the platform, footage that did not come from the
published archive.

Send this agent a campaign link. It reads the brief, writes down what the
campaign demands, pulls the footage the brief authorises, picks the moments from
the transcript, renders vertical clips that already sit inside the rules, and
sends the files back in the chat with the caption to paste.

You post. It does not post for you, and that is on purpose: the platform's
posting API locks an unaudited app's uploads to private, so a clip it published
would be a clip nobody sees.

## The promise, and its edge

It promises that **nothing mechanical** disqualifies the post. Duration,
resolution, aspect, audio policy, required hashtags and mentions, banned terms,
the campaign's per-clipper cap, the deadline.

It does not promise views, and it never claims to have checked what it cannot
see. Whether the footage truly came from the authorised archive, whether official
material fills the share of screen the brief demands, anything the brief left
open: those come back on every verdict as lines only you can confirm. A tool that
hides its blind spots is how somebody learns a rule from a rejection notice.

## What it will not do

- Invent a limit the brief did not state. An unstated field is reported as
  unchecked, never filled with what is usual for the platform.
- Take footage from anywhere but the links the brief publishes. If the campaign
  published no archive, it stops and asks.
- Post, schedule, or touch your social accounts. There is no login in this agent.

## Install

```sh
plow-agents login
plow-agents mint            # writes ./plow-credentials
docker compose up --build -d
```

Then text the agent a campaign link. Nothing else to configure: no API keys, no
OAuth, no accounts. The first build is long because the transcription model is
baked in rather than downloaded during your first request.

## The commands under it

Everything a skill runs is `warden`, and every number the agent states comes from
it rather than from the model's reading.

| | |
| --- | --- |
| `warden schema` | the rule set a campaign fills |
| `warden campaign save --file -` | store one, refusing a malformed shape |
| `warden fetch <url>` | a brief as readable text |
| `warden archive --campaign <id>` | pull the authorised footage, and only that |
| `warden transcribe <file>` | published subtitles when they exist, whisper when they do not |
| `warden digest <transcript>` | the transcript a model can afford to read |
| `warden beat <track>` | tempo, grid and drop, so a cut can land on a bar |
| `warden cut <src> --campaign <id> --start --end --out` | render inside the rules, then check the render |
| `warden check <clip> --campaign <id> --caption -` | the gate: exit 1 means do not post |
| `warden package --campaign <id> --hook "..."` | the caption the campaign requires |
| `warden log --campaign <id> ...` | this install's own count, which the cap reads |

## Tests

```sh
python3 -m unittest discover -s tests
```

Every case in there is a way a real submission has been thrown out.

## License

MIT. See LICENSE and NOTICE.
