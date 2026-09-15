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
- **GitHub Container Registry (`ghcr.io`)**, once per install, to pull the
  published image. Building locally instead reaches `public.ecr.aws`,
  `media.githubusercontent.com` and `raw.githubusercontent.com` — see
  `docs/INSTALL.md`.
- **Hugging Face**, once per install, for the two transcription models.
- **The AI Worth Using Agent Index**, hourly, with day and model token counts
  and nothing else — no prompts, no file paths, no costs. It has no switch;
  an owner who does not want it edits the Dockerfile and builds their own with
  `docker compose -f compose.yml -f compose.build.yml up --build -d`.
- **Four public campaign directories** — `clipmap.gg`, `whop.com`,
  `clipradar.co`, `realoficial.com.br` — only when you ask it to go find a
  campaign, and only through `warden discover`.

It does not reach anywhere else. There is no social login, and it never posts.

## Install

```sh
git clone https://github.com/bruno-dotcom12/clip-warden.git && cd clip-warden
./install.sh
```

The script clones the `plow-agents` command, logs you in, asks which line the
agent should answer on, mints its credential, pulls the published image (~1 GB),
starts it, and then checks the container came up with everything a clip needs.
To build locally instead, for development: `WARDEN_BUILD=1 ./install.sh`. It prints every command it
runs and stops at the first thing it cannot do. `docs/INSTALL.md` has the same
path typed out by hand, and the three ordering traps that bite when you do.

Docker Desktop needs at least 4 GiB of RAM in its VM: the agent uses about 2 GiB
to transcribe and render at once, and a smaller VM turns the first clip into an
out-of-memory kill. The script measures it and says so.

Then text the agent a campaign link. Nothing else to configure: no API keys, no
OAuth, no accounts. Its first question will be whether clips keep the original
sound or ship silent for you to add a track in the app -- it asks once and
stores the answer.

The transcription models are not in the image. A supervised service pulls them
in the background on first boot, while you are reading the agent's first reply,
and they stay in the agent's volume. `warden status` reports how far along each
one is, in megabytes -- and a cut asked for before they land says the same thing
rather than starting a silent five-minute download.

## The commands under it

Everything a skill runs is `warden`, and every number the agent states comes from
it rather than from the model's reading.

| | |
| --- | --- |
| `warden schema` | the rule set a campaign fills |
| `warden campaign save --json '<json>'` | store one, verified by reading it back |
| `warden fetch <url>` | a brief as readable text |
| `warden archive --campaign <id>` | pull the authorised footage, and only that |
| `warden archive --campaign <id> --text-first` | pull only what gives the words — the published subtitle, or the audio — and no video. Choose the windows on that first |
| `warden archive --campaign <id> --window <a>-<b>` | pull ONLY that window of the source, through the same archive gate |
| `warden authorize <url> --campaign <id>` | is this link in the campaign's archive? exit 1 = no |
| `warden trusted add\|check\|list <x>` | the owner's trusted channels and domains, for clipping without a campaign |
| `warden transcribe <file>` | published subtitles when they exist, whisper when they do not |
| `warden captions review <srt> --start --end [--approve]` | the words before they burn. Approval is per window: a cut outside it renders with no caption rather than a wrong one |
| `warden digest <transcript>` | the transcript a model can afford to read |
| `warden signals <transcript>` | the moments the words and sound point at, for viral cuts |
| `warden tracks list\|add <file>` | the owner's own audio, kept by name. This agent ships none and downloads none |
| `warden beat <track>` | tempo, grid and drop, so a cut can land on a bar |
| `warden cut <src> --campaign <id> --start --end --out` | render inside the rules, then check the render (needs `--crop` when the image has no face detector) |
| `warden cut … --seconds <n>` | the duration the PERSON asked for. The delivery gate rejects a file that misses it with no campaign rule to blame |
| `warden cut … --track <file>` | cut to that audio; `--track-start` says where it enters, its drop by default |
| `warden style extract\|check <clip>` | measure what already looked right, and reject a render outside the measured range |
| `warden check <clip> --campaign <id> --caption -` | the first gate: exit 1 means do not post |
| `warden package --campaign <id> --hook "..."` | the caption the campaign requires |
| `warden log --campaign <id> ...` | this install's own count, which the cap reads |

## Tests

```sh
python3 -m unittest discover -s tests
```

Every case in there is a way a real submission has been thrown out.

## License

MIT. See LICENSE and NOTICE.
