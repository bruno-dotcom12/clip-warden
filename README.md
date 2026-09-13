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
- Take footage from anywhere but the links the brief publishes, and those have
  to be http or https: a brief is a stranger's text, and anything else in it is
  an instruction rather than a link.
- Post, schedule, or touch your social accounts. There is no login in this agent.

## Where it connects

You are running a stranger's agent on your machine, so here is every place it
reaches out, and why:

- **Plow**, for its chat line. This is how you talk to it.
- **The campaign links you send it**, and only those, to read a brief.
- **The archive links published inside a brief**, to pull footage, `http` and
  `https` only.
- **Hugging Face**, once per install, for the two transcription models.
- **The AI Worth Using Agent Index**, hourly, with day and model token counts
  and nothing else — no prompts, no file paths, no costs. It has no switch;
  an owner who does not want it builds the image without that service.
- **Four public campaign directories** — `clipmap.gg`, `whop.com`,
  `clipradar.co`, `realoficial.com.br` — only when you ask it to go find a
  campaign, and only through `warden discover`.

It does not reach anywhere else. There is no social login, and it never posts.

## Install

`plow-agents` is a checkout you put on your PATH, not something Docker brings:

```sh
git clone https://github.com/plow-pbc/plow-agents.git
export PATH="$PWD/plow-agents/bin:$PATH"
```

Then, from this repository:

```sh
plow-agents login           # text the printed phrase from the phone that owns the account
plow-agents lines           # the line UIDs you own
plow-agents mint ln_xxx     # that line's credential -> ./plow-credentials
docker compose up --build -d
```

`mint` takes the line as an argument; there is no default. `docs/INSTALL.md` is
the same path with the places you would otherwise have to guess written down.

Then text the agent a campaign link. Nothing else to configure: no API keys, no
OAuth, no accounts.

The transcription models are not in the image. A supervised service pulls them
in the background on first boot, while you are reading the agent's first reply,
and they stay in the agent's volume. `warden status` says whether they arrived.

## The commands under it

Everything a skill runs is `warden`, and every number the agent states comes from
it rather than from the model's reading.

| | |
| --- | --- |
| `warden schema` | the rule set a campaign fills |
| `warden campaign save --json '<json>'` | store one, verified by reading it back |
| `warden fetch <url>` | a brief as readable text |
| `warden archive --campaign <id>` | pull the authorised footage, and only that |
| `warden authorize <url> --campaign <id>` | is this link in the campaign's archive? exit 1 = no |
| `warden trusted add\|check\|list <x>` | the owner's trusted channels and domains, for clipping without a campaign |
| `warden transcribe <file>` | published subtitles when they exist, whisper when they do not |
| `warden digest <transcript>` | the transcript a model can afford to read |
| `warden signals <transcript>` | the moments the words and sound point at, for viral cuts |
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
