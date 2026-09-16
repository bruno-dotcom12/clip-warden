# Clip Warden

Paid clipping campaigns reject submissions **after** the views have accrued, and
a rejected clip is unpaid work. The reasons are almost always mechanical: a
second over the limit, a missing hashtag, a scratch audio track on a campaign
that adds its own sound on the platform, footage that did not come from the
published archive.

Send this agent a campaign link. It reads the brief, writes down what the
campaign demands, pulls the WORDS the archive publishes, picks the moments from
those words, pulls only the seconds it picked, renders vertical clips that
already sit inside the rules, and sends the files back in the chat with the
caption to paste. The video is the last thing it downloads, and it downloads
only the seconds it is going to use.

The clips come back **in the chat**, as files. That is the delivery, and for
most people it is the whole flow: you post them yourself, from the app you
already post from.

## Does it post to YouTube for me?

**Not on a fresh install, and that is the honest answer to the first question
everybody asks.** Out of the box this agent publishes nowhere. It hands you the
finished MP4 in the chat, with the title, description and caption ready to
paste, and you post it from the app you already post from. That path needs no
account, no API key and no OAuth, and it is what "it works" means here.

Publishing straight from the chat is a **real feature and it is opt-in**: about
**three minutes**, no programming, setting up an account that is yours rather
than this project's. It is typed out step by step in `docs/INSTALL.md`, under
**"Setting it up — five steps, about three minutes"**. Until you do that, every
delivery command below is off, and nothing else about the agent changes — the
cut, the caption and the chat delivery are identical either way.

**Why it cannot ship switched on:** a credential that publishes to *your*
channel would have to live inside a public image — the paragraph below is what
happened the one time something like that did. And the direct YouTube API path
cannot be switched on for you at all: this project's own API project has **not**
been audited by Google, so an upload through it comes out **locked private, with
no appeal**. That is the difference between the first command below and the
second, and it is the whole reason there are two.

Three delivery commands exist beyond the chat. **Every one of them is off until
you supply a credential yourself**, and they are not interchangeable — one of
them publishes, two of them do not:

- **`warden post youtube`** — **this is the one that actually publishes, once
  you have switched it on.** It goes through a publishing intermediary whose
  app has already been audited by YouTube, so the video comes out **public** on
  your channel. Measured on 15/09/2026 with a real send: the YouTube API
  reported `privacyStatus: public` and the page opens signed out. It needs a key
  from an account **you** hold with that intermediary — your quota, your bill —
  and there are two ways in: `WARDEN_POST_API_KEY` in the environment, which is
  how you supply it when you run the container yourself, or
  `echo "<key>" | warden post setkey`, which stores it in this machine's own
  volume for the case where there is no `.env` to write, as in the Plow cloud.
  Their free plan is 10 uploads a month and asks for no card. With neither the
  variable nor the stored file the command is off, which is the state of every
  fresh install. Switching it on is five steps, no programming, about three
  minutes: `docs/INSTALL.md`, "Handing a clip to YouTube, TikTok or Instagram" →
  "Setting it up — five steps, about three minutes".
  The same key and the same upload also reach **`warden post tiktok`** and
  **`warden post instagram`**, and `--also tiktok,instagram` sends to all of
  them in one go, with the file leaving this machine once. **Only the YouTube
  road has been measured end to end from here**: the other two are built from
  the intermediary's official API spec, read on 16/09/2026, with no real send
  behind them — and the command says so on every send that includes one.
- **`warden youtube connect` / `warden youtube publish`** — the direct YouTube
  Data API path, using this project's own API project, which Google has **not**
  audited. An upload from an unaudited project is **locked as private**, and
  locked is the word: YouTube's own help page says the restriction cannot be
  appealed and the owner cannot make the video public afterwards. Useful for
  getting a file onto the right channel; **not** a way to publish. To publish
  that clip, upload the same file again from the YouTube app or youtube.com.
  `docs/AUDITORIA-YOUTUBE.md` is the audit request that would change this;
  nobody here can promise it will be granted, and Google publishes no timeline.
- **`warden tiktok`** puts the file in the **inbox** of your own TikTok account,
  where it waits as a draft you open, caption and post in the app. It needs an
  access token for that account. The image carries none, and never will: that
  token belongs to a person, not to a published image.

**Why none of these ship with a credential in them:** what goes into the image
is public, because this agent is published. That is not a precaution written
after reading a policy — until 15/09/2026 this project's Google OAuth secret
was baked into the published image, and an **anonymous** pull from ghcr handed
it over. It has leaked once already. Credentials, cookies and API keys now come
from the machine of whoever installs, at run time, and a missing one switches
off its own feature and nothing else.

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
- Post anything on its own. The three delivery commands above run only when you
  run them, only with a credential you supplied, and on a fresh install none of
  them is switched on. Nothing here schedules a post, nothing here posts to an
  account you did not connect, and nothing here logs in with a password — the
  direct YouTube path is a code you approve on Google's own screen, and the
  other two are keys you paste in.

## Where it connects

You are running a stranger's agent on your machine, so here is every place it
reaches out, and why:

- **Plow**, for its chat line. This is how you talk to it.
- **The campaign links you send it**, and only those, to read a brief.
- **The archive links published inside a brief**, to pull footage, `http` and
  `https` only.
- **GitHub Container Registry (`ghcr.io`)**, once per install, to pull the
  published image — the only image there is. Building locally instead reaches
  `public.ecr.aws`, `media.githubusercontent.com`, `raw.githubusercontent.com`
  and `github.com` — see `docs/INSTALL.md`. Until 16/09/2026 an install also
  pulled a second image from **Docker Hub**, the Proof-of-Origin token
  provider; it is now inside the agent's own image, so Docker Hub is no longer
  contacted at all.
- **Hugging Face**, once per install, for the two transcription models.
- **The AI Worth Using Agent Index**, hourly, with day and model token counts
  and nothing else — no prompts, no file paths, no costs. It has no switch;
  an owner who does not want it edits the Dockerfile and builds their own with
  `docker compose -f compose.yml -f compose.build.yml up --build -d`.
- **Four public campaign directories** — `clipmap.gg`, `whop.com`,
  `clipradar.co`, `realoficial.com.br` — only when you ask it to go find a
  campaign, and only through `warden discover`.

It reaches nowhere else **on its own**. Two more hosts become reachable only
after you have supplied a credential for them yourself, and only while a
delivery command is running:

- **`api.upload-post.com`**, for every `warden post` command — YouTube, TikTok
  and Instagram all go through that one host. Off unless
  `WARDEN_POST_API_KEY` is set or a key was stored here with
  `warden post setkey`.
- **`googleapis.com` / `oauth2.googleapis.com`**, for `warden youtube`. Off
  unless `WARDEN_YT_CLIENT_ID` and `WARDEN_YT_CLIENT_SECRET` are set.
- **`open.tiktokapis.com`**, for `warden tiktok`. Off unless your account's
  token file is there.

All of them come from the `environment:` block of `compose.yml`, which reads
your shell or your `.env` — except the publishing key, which has a second
entrance for the case where there is no `.env` to write: `warden post setkey`
reads it from standard input and stores it in this install's own volume, mode
`0600`. **None of them is in the published image**, and a volume is not the
image: it is written after the pull and it belongs to one install. With no
credential present, no command runs and no host is contacted.

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

Docker Desktop needs at least 5 GiB — 5120 MiB — of RAM in its VM:
`compose.yml` caps the agent at 4 GiB (4096 MiB) — it renders two clips at once
since 16/09/2026, which halved a two-clip batch from 105s to 54.7s at a measured
peak of 1584 MiB — and the remaining 1024 MiB is the slack the rest of the VM
needs. On a smaller VM the cap is not tighter, it is absent: Docker accepts a
ceiling above the VM and never enforces it, and what runs out of memory is then
the VM rather than the container. The script measures it and says so, and
`docs/AMBIENTE.md` has the measurement.

**One `plow-credentials` file is one chat line.** Every container that mounts
that file answers the same chat, and there is no way for one of them to find out
that another is up: that would need a lock or a handshake, and neither exists.
On 16/09/2026 three came back together when the Docker VM restarted — two had
been stopped on purpose — and a request to publish was answered by the wrong
one, the only one with a publishing key, which put a clip on a real channel
nobody had asked it about. So the boot now says what it can: a line starting
`warden-linha:` names the chat this install answers and whether this install can
publish at all. Run more than one agent on purpose and each needs its own
credential file. The whole account is in `docs/AMBIENTE.md`, section 5.

`docker compose up` starts **one** container. Between 15/09 and 16/09/2026 it
started two: a second one sat beside the agent, capped at 512 MiB, minting the
Proof-of-Origin token YouTube asks for on logged-out requests. It answered on a
port, and the Plow cloud — one container per person — forbids an inbound
listener, so on 16/09/2026 the token generator moved inside the agent's image
and now runs as a script: the yt-dlp plugin starts a Node process when it needs
a token, and nothing listens anywhere. The token still matters for the same
reason it always did — an address that keeps asking without one gets flagged —
and it still keeps that from happening rather than fixing it, see "I can't
download anything from YouTube" below.

Then text the agent a campaign link. Nothing else to configure **to get clips**:
no API keys, no OAuth, no accounts. The three delivery commands are the only
exception and all three are opt-in — the one that publishes takes about three
minutes to switch on, and `docs/INSTALL.md` has the five steps, plus what you
still do by hand afterwards for the other two. It does not open with a
questionnaire: clips keep the **original sound** by default and the agent
announces that with the clip instead of asking for it. `--sound platform` ships
them silent for you to add a track in the app, and a campaign that forbids the
source's audio overrides both — the announced line names the rule that decided.

The transcription models are not in the image. A supervised service pulls them
in the background on first boot, while you are reading the agent's first reply,
and they stay in the agent's volume. `warden status` reports how far along each
one is, in megabytes -- and a cut asked for before they land says the same thing
rather than starting a silent five-minute download.

## I can't download anything from YouTube

Measured 15/09/2026, from this project's own outgoing address: YouTube refused
every logged-out request with `Sign in to confirm you're not a bot`. Not one
link — all of them.

The agent now says that, in those words. It had guessed twice before, on two
different days, with two contradictory stories — "that specific video", then
"this server's IP" — and neither was something the owner could check.

**What it is not**: not that video, not the archive-authorisation gate (which
had already passed), and not something that clears up on its own in a few
minutes. Nobody here can tell you the refusal is temporary.

**What is already in place**, so it is not the fix — `warden status` names all
three:

- a **JavaScript runtime** (`node`, already in the image). Without one yt-dlp
  drops the `web` client from its default set and cannot decipher n/sig. Every
  install before this one was running that way in silence.
- a **Proof-of-Origin token**, minted inside the agent's own image since
  16/09/2026 — a Node process the yt-dlp plugin runs on demand, where until
  then it was a second container answering on a port.
- a **pace**. One link used to cost four extractions fired back to back; it now
  costs two, with sleeps between requests and retries capped at three.

The token is **prophylactic**: it keeps an address from being flagged, and it
does not lift a flag. That was measured, not assumed — a valid token, freshly
minted, bound to the right visitor data, in the player context, got the
identical refusal.

So for an address that is already refused there are two options, and the agent
can do neither on its own:

1. **A different outgoing address** — another network, or a VPN.
2. **A cookies file from a signed-in YouTube session.** yt-dlp's own warning
   about this is that it can get the account blocked, so it has to be a
   throwaway account, never the one you care about.

`docs/INSTALL.md` has both typed out, with where the cookies file goes.

## The commands under it

Everything a skill runs is `warden`, and every number the agent states comes from
it rather than from the model's reading.

| | |
| --- | --- |
| `warden schema` | the rule set a campaign fills |
| `warden campaign save --json '<json>'` | store one, verified by reading it back |
| `warden fetch <url>` | a brief as readable text |
| `warden archive --campaign <id> --text-first` | **the first download.** Only what gives the words — the published subtitle, or the audio — and no video. Choose the windows on that |
| `warden archive --campaign <id> --window <a>-<b>` | **the second.** Only the seconds you chose, through the same archive gate, with the origin card beside it |
| `warden archive --campaign <id>` | the whole file. The exception, not the flow: it cost 12min26s to the first clip against 62s |
| `warden cut … --seconds <n>` / `--any-length` | the duration the person asked for. One of the two is required — a cut with no duration decision does not render |
| `warden delivered [<clip>]` | strike a confirmed send off the list; with no path, asks what is still owed and exits 1 while anything is |
| `warden authorize <url> --campaign <id>` | is this link in the campaign's archive? exit 1 = no |
| `warden trusted add\|check\|list <x>` | the owner's trusted channels and domains, for clipping without a campaign |
| `warden transcribe <file>` | published subtitles when they exist, whisper when they do not |
| `warden captions review <srt> --start --end [--approve]` | the words before they burn. Approval is per window: a cut outside it renders with no caption rather than a wrong one |
| `warden digest <transcript>` | the transcript a model can afford to read |
| `warden signals <transcript>` | the moments the words and sound point at, for viral cuts |
| `warden tracks list\|add <file\|url>` | the owner's own audio, kept by name and measured once. None is shipped in this repo |
| `warden beat <track>` | tempo, grid and drop, so a cut can land on a bar |
| `warden cut <src> --campaign <id> --start --end --out` | render inside the rules, then check the render (needs `--crop` when the image has no face detector) |
| `warden cut … --seconds <n>` | the duration the PERSON asked for. The delivery gate rejects a file that misses it with no campaign rule to blame |
| `warden cut … --track <file>` | cut to that audio; `--track-start` says where it enters, its drop by default |
| `warden style extract\|check <clip>` | measure what already looked right, and reject a render outside the measured range |
| `warden check <clip> --campaign <id> --caption -` | the first gate: exit 1 means do not post |
| `warden package --campaign <id> --hook "..."` | the caption the campaign requires |
| `warden log --campaign <id> ...` | this install's own count, which the cap reads |
| `warden post setkey` | the key for the commands below, read from **standard input** so it never reaches `ps` or a shell history, stored in this install's volume at `0600`. The way in where there is no `.env`: in the Plow cloud each person has their own machine and the environment carries only what Plow puts there. `WARDEN_POST_API_KEY` still wins over it |
| `warden post connect` | one address for the person to open, where they pick the channel on Google's own screen and press Allow. Run it again and it says `already connected` |
| `warden post youtube <clip> --title "..."` | **the one that publishes — after you switch it on.** Through an intermediary whose app YouTube already audited, so the video comes out public — measured 15/09/2026, `privacyStatus: public`. Off on a fresh install: it needs a key from your own account with that intermediary, about three minutes to create. Title at most 100 characters, and it refuses a longer one rather than truncating it |
| `warden post tiktok\|instagram <clip> --title "..."` | the same key, the same single upload, the other two networks; `--also tiktok,instagram` adds them to the YouTube send instead of a second upload. `--draft` puts the TikTok in the drafts, `--stories` makes the Instagram a Story. **No real send from here has ever gone to either**: the fields come from the intermediary's official API spec read on 16/09/2026, and every send that includes one says so. Their TikTok needs a paid plan there; Instagram needs a Business or Creator account |
| `warden post status [<request_id>]` | with no id, whose account this is and which connected accounts will actually publish — exit 3 when the key is valid and nothing will. With an id, that one upload, once it went into the intermediary's queue |
| `warden youtube connect\|status\|publish <clip>` | the direct API path, on this project's unaudited API project. **The upload lands locked as private**, and that lock takes no appeal: to publish, upload the file again from the YouTube app or site. Needs a client you supply |
| `warden tiktok <clip>` | the file into your TikTok **inbox**, as a draft you finish in the app. Needs that account's token; the image has none |

## Tests

```sh
python3 -m unittest discover -s tests
```

Every case in there is a way a real submission has been thrown out.

**On a Mac that run is green and incomplete, and the output does not say so.**
Measured 15/09/2026: `Ran 877 tests` … `OK (skipped=27)` — **24** of those skips
are the burned-caption path (`this ffmpeg has no subtitles filter (no libass)`;
libass is an ffmpeg compile option the image has and a Homebrew ffmpeg usually
does not) and **3** need faster-whisper. A skip is not a pass, and those 27 are
the part you can see in the clip. The command that runs them where they are not
skipped is in `docs/INSTALL.md`, under **"Running the tests"**.

## License

MIT. See LICENSE and NOTICE.
