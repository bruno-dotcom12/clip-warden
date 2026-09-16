# Installing Clip Warden

Two commands and a text message. Most of the time is a download you can walk
away from, and nothing of yours goes into it.

```sh
git clone https://github.com/bruno-dotcom12/clip-warden.git && cd clip-warden
./install.sh
```

That is the whole install. The script clones the `plow-agents` command, logs
you in, mints the agent's credential, pulls the published image, starts it, and
then checks that the container came up with everything a clip needs — refusing to call itself done
if anything is missing. It prints every command it runs, so nothing here is
hidden; if you would rather type them yourself, they are all in **By hand**
below.

It stops and tells you what to do when something is missing. It does not
continue past a problem.

## What you have when it finishes, and what is still switched off

**Clips, in the chat.** The agent sends you the finished MP4 with the title,
description and caption ready to paste, and you post it from the app you already
post from. Nothing else to configure for that: no API key, no OAuth, no account
beyond Plow.

**Publishing straight from the chat is off, and turning it on is about three
minutes.** That is not missing work and nothing is broken — what goes into a
published image is public, so a credential that posts to *your* channel cannot
ship inside it. The command that actually puts a **public** video on your
channel is `warden post youtube`, and the five steps that switch it on are here:

> → **[`warden post youtube` — the one that actually publishes](#warden-post-youtube--the-one-that-actually-publishes)**,
> and inside it
> **[Setting it up — five steps, about three minutes](#setting-it-up--five-steps-about-three-minutes)**.

The other two delivery commands do **not** publish, however you configure them,
and it is worth knowing which is which before you spend time on one:
`warden youtube` uploads to your channel but the upload lands **locked private
with no appeal**, because this project's API project has not been audited by
YouTube; `warden tiktok` leaves a **draft in your inbox** that you finish in the
app. Both are covered in
[Handing a clip to YouTube or TikTok](#handing-a-clip-to-youtube-or-tiktok).

## What you need

- **Docker, running, with Docker Compose**, and its VM set to **at least
  5.5 GiB of RAM**. `compose.yml` caps the agent at 4 GiB — it renders two
  clips at the same time since 16/09/2026, which took a two-clip batch from
  105s to 54.7s at a measured peak of 1584 MiB; since 15/09/2026 it also runs a
  second container, the Proof-of-Origin token provider, capped at 512 MiB —
  4.5 GiB of ceiling between them. On a smaller VM the first clip
  dies as an out-of-memory kill rather than an error. Docker Desktop →
  Settings → Resources → Memory. `install.sh` measures this and warns you.
- **Git.**
- **A Plow account**, for the agent's chat line, and the phone that owns it.
- **About 5 GB of free disk.** You download 1.06 GB (the published image,
  compressed) and it unpacks to 4.68 GB. Footage the agent pulls lands in a
  Docker volume, not in your folders, and a 20-minute source is another ~600 MB
  there.

  You do **not** build the image. It is published at
  `ghcr.io/bruno-dotcom12/clip-warden` and `install.sh` pulls it. Building
  locally used to cost a second 4.68 GB of build cache on top of the image, and
  turned any hiccup during the build — an `apt-get` that dropped, a wheel pulled
  from PyPI — into a failed install for a reason that was never yours. To build
  anyway, for development: `WARDEN_BUILD=1 ./install.sh`.

You do **not** need Python on the host. Everything runs in the container. Python
3 is only for running the test suite, which is not part of installing — and if
you do run it, read [Running the tests](#running-the-tests) first, because on a
Mac that suite passes while skipping the caption tests in silence.

## What the script will ask you

1. **A login phrase.** `plow-agents login` prints one; text it to Plow from the
   phone that owns your account.
2. **Which line.** It lists the lines you own and asks for one, as its `ln_…`
   UID rather than the phone number. Pick a free one — the agent answers on that
   number, so it should not be a line you already use for something else.

Nothing else. You can also skip the prompt by setting `LINHA=ln_xxx` in the
environment before running it.

## What "done" means

The last thing `install.sh` does is run `warden status` inside the container and
read it. These lines must all name something, never `MISSING`:

```
ffprobe: /usr/bin/ffprobe
ffmpeg:  /usr/bin/ffmpeg
face detection: YuNet on OpenCV 5.0.0
pillow (the hook, drawn as a PNG): 12.3.0
style font: /opt/hermes/skills/warden-shared/assets/Anton-Regular.ttf
libass (burned captions): yes, the `ass` filter is here
measured style spec: /opt/hermes/skills/SPECS/estilo-aprovado-scenepack.json
```

The script fails if any of them says `MISSING` — **and it fails just the same if
one of them does not appear at all.** Those are two different failures and only
the first used to be caught: a `warden status` that never ran, because the
container was not up, printed no line saying `MISSING`, so the old check found
nothing wrong and the install announced itself done. Not finding the word
`MISSING` is not the same as finding the thing. The script now checks the exit
code of the command as well, and requires each of the seven lines to be present
by name.

That gate is deliberate: every one of those lines is a dependency whose absence
used to degrade a clip in silence rather than stop it. The face detector is the sharpest example — without
it the vertical crop falls back to the middle of the frame, and a video where
the speaker sits to one side ships with their face sliced off at the edge. That
happened. Now `warden cut` refuses to frame rather than guess: with no detector it stops
unless you say where the subject is with `--crop left|right|center|<0-100>`, and
then it honours that literally.

`libass` is the exception on that list: it does not degrade anything, it refuses
out loud. The caption is ASS burned by libass, with word-by-word highlight, and
libass is an ffmpeg **compile** option — the image's ffmpeg is built with
`--enable-libass`, a Homebrew ffmpeg on a Mac usually is not. Without the `ass`
filter `warden cut` stops rather than hand back a podcast clip with no caption.
The line is on this list so that refusal lands here, at install, instead of in
the middle of someone's first cut.

The `pillow` line says what it does: since the caption became ASS, PIL draws the
hook and nothing else. Copy the labels above exactly as they are written — they
are what the tool prints, and this list is what you check your own screen
against.

Three more lines come from the downloader, and the script does **not** fail on
them — they are a warning, not a gate:

```
yt-dlp JS runtime: node
PO token provider (http://pot:4416): answering
yt-dlp cookies file (/var/lib/hermes/warden/cookies.txt): absent (only needed if this address is already refused)
```

`MISSING` on the first means yt-dlp is running with the `web` client dropped
and no n/sig deciphering, which fails as a download error rather than as a
missing dependency. `NOT answering` on the second means the `pot` container is
not up and this install's address is being spent without the token that keeps
it from being flagged. `absent` on the third is the ordinary state; the next
section is what it is for.

`warden status` prints more than those seven lines — where state lives, whether it
is writable, which campaigns are stored, and how far along the transcription
models are:

```
whisper model base:  ready (145 MB)
whisper model small: downloading, 210 of 484 MB (43%)
whisper model small: not here yet (484 MB to download; it runs in the background after install)
whisper model small: FAILED at boot -- the first cut would fetch 484 MB itself
```

`not here yet` is normal in the first minute. `FAILED at boot` means the
background fetch died and every request will refuse until it is retried.

The models are the one thing allowed to be missing at this point. They are not
in the image — baking them cost every installer 600 MB before the agent had said
a word — so a background service pulls them at first boot while you read the
agent's first reply.

They arrive in a deliberate order: **`base` (145 MB) first, `small` (484 MB)
second**, because `base` is the one the first command needs. Reading a whole
source to choose a moment in it uses `base`; `small` is for the window you
already chose, whose words get burned into the caption. So the first clip is
waiting on 145 MB, not on all 629 MB, and `small` still downloading is not
something in your way.

**This is the window the first clip falls into.** Ask for a cut while `small` is
still at 43% and the agent tells you so, with the megabytes left and what to do
about it, instead of starting a silent five-minute download that reads like a
hung agent:

> the small transcription model is still downloading: 210 of 484 MB (43%),
> 274 MB to go. It runs in the background at boot. Wait and try again, or
> transcribe with a model that is already here
> (`warden transcribe <file> --model tiny`), or take the wait on purpose with
> `WARDEN_WAIT_FOR_MODEL=1`.

That is a **refusal**, not a warning: the command exits non-zero. `--model` is a
`warden transcribe` flag — `warden cut` has none — so for a cut you either wait,
or transcribe the window separately first and pass the srt.

## First run

Text the agent's line a campaign link, a video link, or just "oi". It answers
with what it read and what is still open.

**It does not interview you before the first clip.** Sound used to be a question
it had to ask before it would cut anything; it is now a default it announces.
Clips keep the **original audio**, and the clip arrives with the line that says
so — `som: original (padrão; a campanha não decide isso)` — rather than a
question you have to answer first. Measured 15/09/2026: of the 26m22s from the
link to the one clip, 10m34s were questions like that one.

To ship silent instead, so you add a track in the app, pass
`--sound platform` to `warden cut`. And when the campaign forbids the source's
audio, the campaign wins — the clip comes out silent and the announced line
names the rule that decided it.

If a campaign publishes no archive link, the agent says so and stops. Send it
the link to the authorised footage, or the footage itself. It does not go
looking — footage the campaign did not publish is what gets a submission thrown
out after the views.

## I can't download anything from YouTube

Measured 15/09/2026, from this project's own outgoing address: YouTube refused
every logged-out request with `Sign in to confirm you're not a bot`. Every
link, not one of them. `yt-dlp -v` printed `JS runtimes: none` and
`PO Token Providers: none` at the same time.

The agent had already told the owner two different causes on two different days
— "that specific video", then "this server's IP". Both were invented. It now
reads the refusal and says what it is, and the tool prints the same three facts
`warden status` prints, so nothing about it has to be taken on the model's word.

### What it is not

- **Not that video.** Every link gets the same refusal.
- **Not the archive gate.** `warden authorize` had already passed; this happens
  afterwards, at the download.
- **Not temporary.** Nobody here can tell you it clears up on its own, and
  nothing in this repository will promise it.

### What is already in place, so do not go looking for it

- **A JavaScript runtime.** The image carries `node`, and since yt-dlp enables
  only `deno` by itself, `warden` passes `--js-runtimes` explicitly. Without a
  runtime yt-dlp drops the `web` client from its default set and cannot
  decipher n/sig — a handicap on every link that surfaces as a download
  failure. The build now fails if the base image has no `node`.
- **A Proof-of-Origin token.** The `pot` service in `compose.yml`, image
  `brainicism/bgutil-ytdlp-pot-provider:2.0.0` pinned by digest, with no
  `ports:` key so it answers only on the compose network. The agent finds it at
  `WARDEN_POT_URL`, which `compose.yml` sets to `http://pot:4416`.
- **A pace.** A single link used to cost four separate extractions — channel,
  title, subtitles, download — fired back to back with no sleep. It now costs
  two, with `--sleep-requests` between them and retries capped at three.

**The token is prophylactic.** It keeps an address from being flagged; it does
not lift a flag. Measured here: a valid token, freshly minted, bound to
matching visitor data, in the player context, got the identical refusal. That
is why it is a service that runs from the first install rather than a step in
this section.

### The two things that change the answer

Neither is something the agent can do by itself.

1. **A different outgoing address.** Another network, or a VPN in front of
   Docker's.
2. **A cookies file from a signed-in YouTube session.** yt-dlp's own warning is
   that passing logged-in cookies can get that account blocked, so it has to be
   a throwaway account — never the one you post from, never your main one.

### Putting the cookies file in

`warden` reads it at `/var/lib/hermes/warden/cookies.txt` inside the container
and uses it on every yt-dlp call when it is there. That path is inside the
agent's volume, so the way in is a bind — and it has the same trap as
`plow-credentials`: a bind whose source does not exist makes a **directory**
there, and the agent then finds no file. Create it first, with the cookies
already in it:

```sh
touch cookies.txt     # export the session into it from the throwaway account
```

then, under the agent's `volumes:` in `compose.yml`:

```yaml
      - ./cookies.txt:/var/lib/hermes/warden/cookies.txt:ro
```

`docker compose up -d`, and
`docker compose exec -u 10000:10000 agent warden status` should then read
`present` on the cookies line. That file is a live session: never commit it,
and `./install.sh --remover` deliberately does **not** delete it — it is your
session, not the agent's, and deleting somebody's credential without asking is
worse than leaving it. The uninstall names the file and its path on the way out;
deleting it is yours to do. The same goes for `.env`.

### The knobs

`compose.yml` already passes these through from your shell or your `.env`, all
empty by default: `WARDEN_POT_URL`, `WARDEN_YT_COOKIES`, the pair
`WARDEN_YT_CLIENT_ID` / `WARDEN_YT_CLIENT_SECRET`, and the trio
`WARDEN_POST_API_KEY` / `WARDEN_POST_PROVIDER` / `WARDEN_POST_PROFILE` — see
"Handing a clip to YouTube or TikTok" for what the credentials are, and why
none of them lives in the image. The others below have to be added to the
`environment:` block there to reach the container — it does not inherit your
shell, the same as `WARDEN_DIRECTORIES`.

| | |
| --- | --- |
| `WARDEN_POT_URL` | where the token provider answers, `http://pot:4416` |
| `WARDEN_YT_COOKIES` | the cookies path, when it is not the default below. It says which service the cookies are for; a YouTube `cookies.txt` is a whole signed-in session, so it is a file in a volume and never anything the image carries |
| `WARDEN_COOKIES` | the older name for the same thing; `WARDEN_YT_COOKIES` wins when both are set |
| `WARDEN_SLEEP_REQUESTS` | seconds between requests, `1.5` |
| `WARDEN_SLEEP_INTERVAL` | the minimum wait before a download, `1` |
| `WARDEN_SLEEP_MAX` | the ceiling on that wait, `5` |

Lowering the sleeps is how an address gets flagged; they are seconds, not a
preference.

## By hand

The script is a convenience, not a black box. This is what it does:

```sh
git clone --depth 1 https://github.com/plow-pbc/plow-agents.git .plow-agents
export PATH="$PWD/.plow-agents/bin:$PATH"

plow-agents login            # prints a phrase; text it to Plow from your phone
plow-agents lines            # prints your line UIDs, as ln_...
plow-agents mint ln_xxx      # writes ./plow-credentials

docker compose up -d           # pulls the published image and the pot sidecar
docker compose exec -u 10000:10000 agent warden status
```

Three things that bite, in the order they bite:

- **`mint` before `up`, always.** `compose.yml` bind-mounts `./plow-credentials`
  into the container. A bind whose source does not exist does not fail — Docker
  creates a **directory** with that name. The agent then starts with no
  credential, and the `mint` you run afterwards fails trying to write a file
  where a directory is. If that happened: `docker compose down`, then
  `rmdir plow-credentials`, then start again.
- **`mint` takes the line as an argument.** There is no default and no prompt;
  running it bare only prints a usage error. It writes `./plow-credentials`,
  which is a live bearer token: never commit it, never paste it anywhere.
- **`-u 10000:10000` on every `exec`.** `docker compose exec` enters as root, and
  whatever creates a path first inside a named volume owns it forever. A `warden`
  run as root creates the agent's own state directories root-owned and locks the
  agent out of them — and `warden status` would still print `writable: yes`,
  because root can write, so the diagnostic hides the damage.

To build it yourself instead of pulling:

```sh
docker compose -f compose.yml -f compose.build.yml up --build -d
```

The build is 24 steps and 110 layers. Three numbers get quoted for the size of
one image and they measure different things: **1.06 GB** is what you download
(compressed layers), **3.53 GB** is the sum of the uncompressed layers, and
**4.68 GB** is what `docker images` reports once unpacked, which includes the
snapshot overhead. Of the 3.53 GB, **84% is the Plow base** — one `apt-get` layer
alone is 1.1 GB. What this repository adds is 579 MB, essentially all of it one
`pip install` of six packages (with `uv`, because the base's virtualenv has no
`pip`): OpenCV for face detection, ctranslate2 and onnxruntime and PyAV for
transcription, numpy, pillow. Everything else of ours — the skills, the measured
spec, the Anton font, the YuNet model — is about 1 MB.

The `linux/amd64` emulation warning is expected, and it is not a choice: the
Plow base publishes a single-architecture manifest, so there is no arm64 to
build against. On Apple Silicon the agent runs emulated. It does not slow the
build; it only matters when the agent runs.

If the **image** pull fails with `denied` from `ghcr.io`, the likely cause is not
on your machine: a package published to GHCR is **private by default**, even from
a public repository, and has to be switched to public once by its owner. Nothing
you type fixes that — tell us. (`docker logout ghcr.io` is the wrong reflex here:
if you happen to be logged in, it removes the one credential that was working.)

The rest of this block is for the local-build path only. If the **base** pull
fails with a `403`, it is a stale registry credential rather than a permission
you are missing:

```sh
docker logout public.ecr.aws
docker compose -f compose.yml -f compose.build.yml up --build -d
```

To watch it come up: `docker compose logs -f agent`, and wait for
`plow-init: configured … as cht_`. That line means the agent has claimed its
line and is listening.

## Running the tests

```sh
python3 -m unittest discover -s tests
```

Measured on a Mac on 15/09/2026: `Ran 877 tests` … `OK (skipped=27)`.

**Read the `skipped=27`. It is not a footnote — it is the caption.** A skipped
test is not a passing test, and on a host those 27 are the part of the product
you can actually see:

| how many | why it skipped | what is not being exercised |
| --- | --- | --- |
| **24** | `this ffmpeg has no subtitles filter (no libass)` | **the burned caption** — the whole ASS path, word-by-word highlight included |
| **3** | `faster-whisper não está instalado` | transcription with the real engine |

`libass` is an ffmpeg **compile** option, not a package: the image's ffmpeg is
built with `--enable-libass`, and a Homebrew ffmpeg on a Mac usually is not. So
a green `OK` on your laptop means 850 of 877 ran, and the 27 that did not are
the two things this agent is for. Nothing in the output shouts about it — the
suite exits `0` and prints `OK` — which is exactly why it is written down here.

`warden status` reports the same fact from the other side, and that one is a
gate: `libass (burned captions): yes, the ass filter is here` inside the
container, versus a Mac where the filter is absent.

### Running them where they are not skipped

**Inside the container**, which has both libass and faster-whisper. The suite is
not in the published image — `tests` is listed in `.dockerignore`, on purpose,
because it is not something an install needs — so the repository is bind-mounted
in for the run:

```sh
docker run --rm \
  --entrypoint /opt/hermes/.venv/bin/python3 \
  -u 10000:10000 \
  -v "$PWD:/src" -w /src \
  ghcr.io/bruno-dotcom12/clip-warden:latest \
  -m unittest discover -s tests
```

`--entrypoint` is not optional: the image's own entrypoint starts the s6
supervision tree and the agent, not a test run. `-u 10000:10000` is the same
rule as every `docker compose exec` above — root inside a bind mount leaves
root-owned files in your own checkout.

What to look for is the **`skipped=` count, not the `OK`**: in there it should
be **0**, because every dependency the suite skips on is present in the image.
A skip that survives that run is a finding rather than a nuisance — it means the
image is missing something `warden status` is supposed to catch at install.
(The 877/27 above were measured on the host; the in-container count has not been
measured on this machine, so treat the 0 as what to check rather than as a
number to quote.)

To run them against a locally built image instead, build first
(`docker compose -f compose.yml -f compose.build.yml build`) and put that image's
tag in place of the `ghcr.io/...` one.

## Where it connects

Worth knowing before you run a stranger's agent on your machine.

**While installing**, it downloads the published image from **`ghcr.io`**, and
the Proof-of-Origin token provider image from **Docker Hub**, pinned by digest
in `compose.yml`. Nothing else.

**Only if you build locally** (`WARDEN_BUILD=1`, or `-f compose.build.yml`) it
reaches three more hosts, all pinned by digest or sha256 in the repository:

- `public.ecr.aws` — the Plow base image, pinned by digest.
- `media.githubusercontent.com` — the YuNet face-detection model, pinned by
  commit and checked against `vendor/yunet.pin`. A model file is code the
  detector runs, so it gets the same discipline as a binary.
- `raw.githubusercontent.com` — the Agent Index client, pinned in
  `vendor/client.pin`.

**While running**, it reaches:

- **Plow**, for its chat line. This is how you talk to it.
- **The campaign links you send it**, and only those, to read a brief.
- **The archive links published inside a brief**, to pull footage. `http` and
  `https` only; anything else in a brief is treated as text, not a link, and a
  link that resolves to this machine or its private network is refused.
- **Hugging Face**, once per install, for the two transcription models.
- **The AI Worth Using Agent Index**, hourly, described below.
- **Four public campaign directories**, when you ask it to go find a campaign:
  `clipmap.gg`, `whop.com`, `clipradar.co` and `realoficial.com.br`.
  `warden discover` is the only command that reaches them, and it runs only when
  you ask for a campaign. To point it somewhere else, add
  `WARDEN_DIRECTORIES: ${WARDEN_DIRECTORIES:-}` to the `environment:` block in
  `compose.yml` and set it — the container does not inherit your shell, so
  exporting it on the host alone does nothing.

Three more hosts exist, and they are reached **only** when you have supplied a
credential for them yourself and you run the command that uses it:

- **`api.upload-post.com`**, for `warden post youtube`.
- **`oauth2.googleapis.com` and `googleapis.com`**, for `warden youtube`.
- **`open.tiktokapis.com`**, for `warden tiktok`.

None of those credentials is in the image, so on a fresh install none of those
hosts is ever contacted. The next section is what they are and what they do
not do.

## Handing a clip to YouTube or TikTok

The ordinary delivery is the chat: the agent sends you the MP4 and you post it
from the app you already post from. That is the default, it needs nothing
configured, and everything below is optional on top of it.

### The rule that governs this whole section

**What goes into the image is public, because this agent is published.** Every
credential below therefore comes from your own machine, at run time, through
the `environment:` block in `compose.yml` — never from the image.

That is not a precaution copied out of a policy document. Until 15/09/2026 this
project's own Google OAuth secret was baked into the published image as an
`ENV`, and an **anonymous** pull from ghcr handed it over to anyone who asked.
It has leaked once already, which is why it now works the way `plow-credentials`
always has.

A missing credential switches off its own command and nothing else. The boot,
the cut, the caption and the delivery do not change.

### `warden post youtube` — the one that actually publishes

This is the command that puts a **public** video on your channel. It does not
use this project's API credentials at all: it hands the file to a publishing
intermediary (Upload-Post) whose own app has already passed YouTube's audit, so
the upload arrives with the standing of an audited app instead of an unaudited
one.

**Measured on 15/09/2026, with a real send:** the YouTube Data API reported
`privacyStatus: public` for the resulting video and the watch page opens in a
signed-out browser. That is the whole difference from the command in the next
section.

**This is optional and nothing is broken without it.** Without
`WARDEN_POST_API_KEY` the command is simply off, and that is the normal state of
a fresh install: the agent cuts, captions and delivers exactly the same, hands
you the finished file with a title and a description ready to paste, and you
post that from the YouTube app the way you already do. The key buys you one step
fewer, not a feature you are missing.

The key is an account **you** hold with that intermediary — your quota, your
bill, your connected channel. It is not something the image can carry on your
behalf, for the reason in the rule above. Here is how you get one.

#### Setting it up — five steps, about three minutes

No programming, and nothing to install. You need the Google account that
**owns the channel** you want to publish to, signed in in the same browser.

1. **Create an account** at `https://www.upload-post.com/`. The free plan is
   **10 uploads per month and does not ask for a card** — that is what their
   pricing page says.

2. **Open the dashboard** at `https://app.upload-post.com/` and **create a
   profile**. A profile is just a nickname for one set of connected accounts;
   `clip-warden` is a fine name. Write down exactly what you typed — it is the
   value of `WARDEN_POST_PROFILE` in step 5.

3. **Connect your YouTube account to that profile.** Inside the profile, press
   **Connect** on YouTube. Google's own screen opens, you pick the account that
   owns the channel, and you press **Allow**. Your Google password is typed on
   Google's page and nowhere else.

   > **The mistake everybody makes, and how to spot it:** if any screen at this
   > point asks you for a **Client ID** or a **Client Secret**, you are on the
   > wrong road — that is the Google Cloud console, which belongs to the *other*
   > command, the one that cannot publish. **Stop and go back.** Connecting here
   > is three presses and never asks you for a string to paste.

4. **Generate the API key** in the dashboard and copy it. It is shown **once**;
   if you lose it you generate another. It is a password to your channel:
   never paste it into a chat, an issue, or a screenshot — not even into the
   conversation with this agent.

5. **Write it into `.env`**, a plain text file in this folder, beside
   `compose.yml`. Two lines, with your own values in place of the angle
   brackets:

   ```
   WARDEN_POST_API_KEY=<the key from step 4>
   WARDEN_POST_PROFILE=<the profile name from step 2>
   ```

   Then, from a terminal in this folder:

   ```sh
   chmod 600 .env
   docker compose up -d
   ```

   `chmod 600` makes the file readable only by your own user account.
   `docker compose up -d` recreates the container so it reads the new
   environment — **a running container does not pick up a changed `.env` on its
   own**, which is the second most common way this ends in confusion. The file
   is already in `.gitignore` and `.dockerignore`, so it never reaches the
   repository or the image.

   `WARDEN_POST_PROFILE` is **only required when your account has more than one
   profile**. With exactly one, the agent uses it. With several and no name
   given, it refuses and lists them rather than guessing — a video posted to the
   wrong channel does not come back.

There is a third knob you almost never touch:
`WARDEN_POST_PROVIDER=upload-post` is the default and naming anything else is
an error, not a switch.

#### How you check that yours came out public

**Open the video's URL in a private / incognito window.** That window is not
signed into anything, so it sees the video the way a stranger does. If it plays,
it is public. If it says the video is private or unavailable, it is not.

A `200 OK`, a green test, or the agent saying it worked are none of them proof:
they say the request was accepted, not that anybody can watch the result. The
measurement quoted above was made that way, on a real channel, on 15/09/2026 —
and it is how yours should be confirmed too.

### `warden youtube` — it uploads, and the upload is locked private

`warden youtube connect` prints a code you type into Google's own screen, on a
browser already signed into the channel. Your Google password never reaches
this agent. `warden youtube publish <clip>` then uploads to that channel.

**Read this before you rely on it.** YouTube restricts every upload made
through `videos.insert` by an API project it has not audited to private
viewing. This project has not been audited. That state has three properties
people get wrong:

- The video is **locked** as private. YouTube's help page
  (`support.google.com/youtube/answer/7300965`) says the restriction is not
  appealable.
- **You cannot flip it public yourself afterwards** — not in YouTube Studio,
  not in the app. Anything that tells you it is one tap in Studio is wrong,
  including, today, the wording the agent itself prints after an upload.
- The way to actually publish that clip is to **upload the same file again**
  from the YouTube app or from youtube.com, by hand.

So today this command is useful for getting a file onto the right channel and
for checking that the chain works end to end — **not for publishing**. If what
you want is a public video, the command is `warden post youtube`, one section
up. The request that would change this one is written out in
`docs/AUDITORIA-YOUTUBE.md`; Google publishes no timeline for it and this
repository will not invent one.

**Supplying the credential.** The image does not carry a Google OAuth client
and will not: a published image is public, and the YouTube API Services policy
(III.D.1) forbids embedding API credentials in open-source projects. It is
your client, from your own Google Cloud project, the same way `plow-credentials`
is your Plow token.

1. Go to `https://console.cloud.google.com/`, create a project, and turn on
   **YouTube Data API v3** (under APIs & Services → Library).
2. In the same place, under **APIs & Services → Credentials**, click **Create
   credentials → OAuth client ID** and pick the type **TVs and Limited Input
   devices**. Google then shows you two strings: a **Client ID**, which ends in
   `.apps.googleusercontent.com`, and a **Client secret**. Copy both now — the
   secret is shown once.
3. Write them into a file called **`.env`**, in this folder, next to
   `compose.yml`. From a terminal in this folder, with your own two values
   pasted in place of the dots:

   ```sh
   printf 'WARDEN_YT_CLIENT_ID=...\nWARDEN_YT_CLIENT_SECRET=...\n' > .env
   chmod 600 .env
   ```

   `chmod 600` means only your user account can read the file. **That file never
   goes into the repository and never goes into the image** — it is already
   listed in `.gitignore` and `.dockerignore`, and `compose.yml` reads it at the
   moment the container starts. Exporting the two in your shell works too. What
   does **not** work is putting them anywhere else: the container does not
   inherit your shell.
4. `docker compose up -d` to recreate the container with them, then
   `docker compose exec -u 10000:10000 agent warden youtube connect`.

**Without them, nothing breaks.** The container boots the same, every cut,
caption and delivery works the same, and only `warden youtube connect` refuses —
naming both variables, rather than failing halfway through an upload.

**If you ever replace the OAuth client, reconnect.** The agent stores the
approval it got from Google in `/var/lib/hermes/warden/youtube.json`, and that
approval belongs to the client that issued it. Create a new client — or delete
the old one — and the stored approval stops working: the next
`warden youtube publish` fails while renewing, and today it fails with Google's
own wording about a revoked grant, which is **not** what happened. Run
`warden youtube connect` again after any client change and the problem goes
away.

**Why this is not shipped for you.** Until 15/09/2026 this project baked its own
Google client into the published image. An **anonymous** pull from ghcr — no
account, no login — handed the secret to anyone who looked. That client has since
been replaced, and the rule that replaced it is the one at the top of this
section: what goes into the image is public.

### `warden tiktok` — it fills your inbox, it does not post

`warden tiktok <clip>` sends the file to the **inbox** of one TikTok account,
where it sits as a draft. You open TikTok, find it under notifications, write
the caption and post it. The agent does not choose the caption on the platform,
does not schedule, and cannot publish.

It needs an access token for **that account**, written to
`/var/lib/hermes/warden/tiktok.json` inside the container. The image carries no
token and never will — it belongs to a person, not to an image anyone can pull.
The API also does not accept a caption on this endpoint, so the caption
`warden package` prints is something you paste in the app.

## Keeping a long conversation cheap

A conversation with this agent never resets on its own, and a long one carries
every big command output it has ever produced back to the model on every single
message — which is slow and is billed every time. At boot the container turns on
a setting that quietly throws away the **old, bulky command outputs** once the
conversation gets large, keeping the recent ones. Nothing you said is touched,
and no summary is written by a model, so there is nothing to get wrong.

To turn it off, open `compose.yml`'s volume — the setting lives in the agent's
own `config.yaml`, at `compression: proactive_prune_tokens`. Set it to `0` and
it stays `0`: the container only writes that setting when it is not already
there, so a value you chose is never overwritten.

## "MCP server 'plow' failed initial connection"

If `docker compose logs agent` repeats this every five minutes:

```
WARNING tools.mcp_tool: MCP server 'plow' failed initial connection after 3
attempts, parking until a reconnect is requested
```

nothing in this repository is broken, and nothing here can fix it. This is what
it is, measured rather than guessed:

- That MCP server is **Plow Latch** — a separate Mac app that lets an agent act
  on its owner's Mac. It is not part of Clip Warden and Clip Warden does not
  use it for anything.
- It is configured **by the base image, not by this repository**. `plow-init`
  asks Plow who this credential belongs to; if that answer carries a relay URL
  — which it does when the Plow **account** has a Mac registered — it exports
  `PLOW_MCP_URL` and turns the `plow` MCP server on in the agent's
  `config.yaml`. That key is rewritten from the same answer on **every boot**,
  so editing `config.yaml` by hand does not stick.
- The 503s mean the account has a Mac **registered** but the Latch app is not
  **connected**. A registered-and-offline Mac is the worst of the two states:
  the base image's chat plugin injects a paragraph into the agent's system
  prompt telling it that "your owner's Mac is connected through Latch", with a
  signed-in browser and accounts, while the relay answers 503 and zero `plow_`
  tools exist. The agent is told it has hands it does not have, and it will
  occasionally answer as if it did.

**The fix is on the Plow account, not here.** Unregister the Mac from the Plow
account (or, if you want Latch, install it and keep it open). Once Plow stops
returning a relay URL, `PLOW_MCP_URL` is not exported, the `plow` MCP server is
switched off on the next boot, the 503s stop, and the paragraph disappears from
the system prompt. Clip Warden loses nothing: it has never used a `plow_` tool,
and the chat line you talk to it on is a different mechanism entirely — the
`plow-chat-platform` plugin — which has been working this whole time with that
MCP server at zero tools.

**Two things that look like fixes and are not:**

- Setting `enabled: false` under `mcp_servers.plow` in the agent's
  `config.yaml` does not last. `plow-init` rewrites that exact key from Plow's
  answer on every boot.
- Even if it did last, it would not remove the paragraph. The paragraph is
  gated on `PLOW_MCP_URL` being exported, not on whether the MCP server is
  switched on.

Do not expect Latch to post clips for you either way. Its browser tool has no
file-upload action (`fill` takes text and select fields only), and persistent
sign-in was closed as "not planned" upstream. Uploading to YouTube Studio or
TikTok through it is not supported, this project does not attempt it, and the
command that does publish — `warden post youtube` — has nothing to do with it.

## Usage reporting

This image reports token counts to the AI Worth Using Agent Index, hourly, under
the `AGENT_ID` in `compose.yml`. Day and model token counts, and nothing else:
no prompts, no file paths, no costs. There is no switch, because an agent whose
owner does not want that is one built without the service in its Dockerfile.

## Uninstall

```sh
./install.sh --remover
```

It retires the credential at Plow, takes the container and its volume down, and
deletes `./plow-credentials` — which the install step called a live token, so
leaving it behind on the disk would not be an uninstall. The volume is the
campaigns, the ledger, the clips and any footage that was pulled; it goes with
`-v` and does not come back.

By hand:

```sh
plow-agents revoke
docker compose down -v
rm -f plow-credentials
```
