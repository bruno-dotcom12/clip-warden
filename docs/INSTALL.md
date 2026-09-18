# Installing Clip Warden

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

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

The same key and the same upload also reach TikTok and Instagram, as
`warden post tiktok` and `warden post instagram`. They are in the same section,
under a heading that says out loud what has **not** been measured about them:
only the YouTube road has ever been driven end to end from here.

The other two delivery commands do **not** publish, however you configure them,
and it is worth knowing which is which before you spend time on one:
`warden youtube` uploads to your channel but the upload lands **locked private
with no appeal**, because this project's API project has not been audited by
YouTube; `warden tiktok` leaves a **draft in your inbox** that you finish in the
app. Both are covered in
[Handing a clip to YouTube, TikTok or Instagram](#handing-a-clip-to-youtube-tiktok-or-instagram).

## What you need

- **Docker, running, with Docker Compose**, and its VM set to **at least
  5 GiB — 5120 MiB — of RAM**. `compose.yml` caps the agent at 4 GiB
  (4096 MiB) — it renders two clips at the same time since 16/09/2026, which
  took a two-clip batch from 105s to 54.7s at a measured peak of 1584 MiB —
  and that is the only ceiling there is, leaving 1 GiB of slack for the rest
  of the VM. Between 15/09 and 16/09/2026 a second container ran beside it,
  the Proof-of-Origin token provider, capped at 512 MiB, and the two ceilings
  added up to 4.5 GiB; that container is gone, for the reason given under
  [Where it connects](#where-it-connects). Docker Desktop →
  Settings → Resources → Memory. `install.sh` measures this and warns you.

  <!-- warden-agent-mem-limit-mib: 4096 -->
  <!-- warden-min-docker-vm-mib: 5120 -->
  <!-- Os dois números acima também estão em compose.yml, junto do `mem_limit`.
       tests/test_ambiente_7a.py lê os dois arquivos e falha se discordarem. -->

  **A smaller VM does not give you a tighter agent — it gives you no ceiling
  at all.** Docker accepts a `mem_limit` larger than the VM and never enforces
  it. Measured on 16/09/2026 with the VM at 3.826 GiB (3918 MiB) against the
  4096 MiB cap: `docker stats` printed `MEM USAGE / LIMIT 1.024GiB /
  3.826GiB` — the limit it shows is the VM, because the cgroup can never be
  reached before the VM runs out. What dies then is the VM, not the agent, and
  the first clip comes back as a frozen machine rather than an error. That is
  the exact state the container's ceiling exists to prevent, so treat the
  5120 MiB as the requirement it is.

  `docs/AMBIENTE.md` carries that measurement in full — and, next to it, the
  orange platform warning Docker Desktop shows for this container on an Apple
  Silicon Mac, which is amd64 emulation, is expected, and is not a fault.
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
PO token provider (script, nesta imagem (/opt/plow/bgutil/server)): ready
yt-dlp cookies file (/var/lib/hermes/warden/cookies.txt): absent (only needed if this address is already refused)
```

`MISSING` on the first means yt-dlp is running with the `web` client dropped
and no n/sig deciphering, which fails as a download error rather than as a
missing dependency. The second line names **which** of the two token paths is
standing: `script, nesta imagem (…)` is the ordinary one since 16/09/2026, the
generator that lives in the agent's own image; `HTTP, …` appears instead only
if you set `WARDEN_POT_URL` at a token server of your own. Until 16/09/2026
that line always read `http://pot:4416`, the second container — it no longer
exists, so an install still printing it is reading an old `compose.yml`.
`NOT available` means neither path is standing and this install's address is
being spent without the token that keeps it from being flagged. `absent` on the
third is the ordinary state; the next section is what it is for.

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
- **A Proof-of-Origin token.** Both halves are in the agent's image: the yt-dlp
  plugin, and the generator it drives. The generator runs in **script mode** —
  the plugin starts a Node process when it needs a token, and nothing listens
  on any port. Both are pinned in `vendor/potprovider.pin`, plugin and
  generator on the same major, because the provider's own README says a mismatch
  there is fatal and yields no token at all.

  Until 16/09/2026 the generator was a **second container**, the `pot` service
  in `compose.yml`, a Node server on port 4416 that the agent reached at
  `WARDEN_POT_URL=http://pot:4416`. It moved into the image because the Plow
  cloud runs one container per person and its contract forbids, in those words,
  "no inbound listener" — and a generator answering on a port is exactly that.
  Whoever installs locally gets the side benefit: one image fewer to pull.
- **A pace.** A single link used to cost four separate extractions — channel,
  title, subtitles, download — fired back to back with no sleep. It now costs
  two, with `--sleep-requests` between them and retries capped at three.

**The token is prophylactic.** It keeps an address from being flagged; it does
not lift a flag. Measured here: a valid token, freshly minted, bound to
matching visitor data, in the player context, got the identical refusal. That
is why it is in the image and working from the first install rather than a step
in this section.

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
empty by default: `WARDEN_YT_COOKIES`, the pair
`WARDEN_YT_CLIENT_ID` / `WARDEN_YT_CLIENT_SECRET`, and the trio
`WARDEN_POST_API_KEY` / `WARDEN_POST_PROVIDER` / `WARDEN_POST_PROFILE` — see
"Handing a clip to YouTube, TikTok or Instagram" for what the credentials are,
and why none of them lives in the image. The publishing key is the one knob with
a second entrance that is not an environment variable at all: `warden post
setkey` writes it into the agent's own volume, which is how it gets in where
there is no `.env` to write. The others below have to be added to the
`environment:` block there to reach the container — it does not inherit your
shell, the same as `WARDEN_DIRECTORIES`.

| | |
| --- | --- |
| `WARDEN_POST_DIR` | where `warden post` keeps the key it was given and this machine's profile name. Defaults to `/var/lib/hermes/warden`, the agent's own directory inside the volume, which is where you want it: it is `0700` and it survives a restart. Point it elsewhere only to run the command against a throwaway directory |
| `WARDEN_POT_URL` | the address of a PO token server of **your own**, if you run one. Optional, and there is no default: leave it unset and the generator inside the image is used. It was set to `http://pot:4416` until 16/09/2026, when the second container it pointed at went away |
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

docker compose up -d           # pulls the published image; one container, not two
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
- **One `plow-credentials` file is one chat line, however many containers hold
  it.** Every container that bind-mounts that same file answers the same chat.
  On 16/09/2026 three of them came back together when the Docker VM restarted —
  two had been stopped on purpose, and `restart:` brought them back — and a
  request to publish was served by the wrong one, the only one holding a
  publishing key and a pinned profile. It went to a real channel. There is no
  way to detect this live from inside a container, so the boot says what it can
  instead: a line starting `warden-linha:` names the chat this install answers
  and says whether this install can publish at all. See `docker compose logs
  agent | grep warden-linha`, and `docs/AMBIENTE.md` section 5 for the whole
  account. If you run more than one agent on purpose, give each its own
  credential file.

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

**That run is incomplete, and it does not say so.** Measured 18/09/2026:
`tests/test_persona.py` (32 cases) and `tests/test_ponteiros.py` (42) are plain
pytest functions with no `TestCase`, so `unittest discover` collects **zero** of
them and reports two import errors instead — `ModuleNotFoundError: pytest`.
Seventy-four cases, including the ones that catch the SOUL.md truncation and
pointers that leave the image, are **not exercised in the container at all**.

Installing pytest in the image would fix it and was deliberately not done: the
image is public and dev tooling costs every person who pulls it. **Run those 74
on the host** (`python3 -m pytest tests -q`) and treat the container run as the
gate for everything else. The two import errors above are expected; a third
error, or any `FAIL`, is not.

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

**While installing**, it downloads the published image from **`ghcr.io`**.
Nothing else — one image, because there is one container. Until 16/09/2026 it
also pulled the Proof-of-Origin token provider image from **Docker Hub**: that
provider was a second container, a Node server on port 4416, and it is now part
of the agent's image, running as a script with no port of its own. The Plow
cloud runs one container per person and its contract forbids, in those words,
"no inbound listener", which is what forced the move; the install got shorter as
a side effect, since that image had its own 512 MiB ceiling and its own pull.

**Only if you build locally** (`WARDEN_BUILD=1`, or `-f compose.build.yml`) it
reaches four more hosts, all pinned by digest or sha256 in the repository:

- `public.ecr.aws` — the Plow base image, pinned by digest.
- `media.githubusercontent.com` — the YuNet face-detection model, pinned by
  commit and checked against `vendor/yunet.pin`. A model file is code the
  detector runs, so it gets the same discipline as a binary.
- `raw.githubusercontent.com` — the Agent Index client, pinned in
  `vendor/client.pin`.
- `github.com` — the PO token generator's source, pinned by commit and checksum
  in `vendor/potprovider.pin`. This is the half that used to arrive as the
  Docker Hub image; building it into the agent is what let the second container
  go. The plugin that drives it comes from the wheel pinned in the same file.

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

- **`api.upload-post.com`**, for every `warden post` command — `youtube`,
  `tiktok`, `instagram`, `connect` and `status` all speak to that one host, and
  a send to three networks is still one upload to it.
- **`oauth2.googleapis.com` and `googleapis.com`**, for `warden youtube`.
- **`open.tiktokapis.com`**, for `warden tiktok`.

None of those credentials is in the image, so on a fresh install none of those
hosts is ever contacted. The next section is what they are and what they do
not do.

## Handing a clip to YouTube, TikTok or Instagram

The ordinary delivery is the chat: the agent sends you the MP4 and you post it
from the app you already post from. That is the default, it needs nothing
configured, and everything below is optional on top of it.

### The rule that governs this whole section

**What goes into the image is public, because this agent is published.** Every
credential below therefore comes from your own machine, at run time — through
the `environment:` block in `compose.yml`, or, for the publishing key, through
a file that `warden post setkey` writes into this machine's own volume. Never
from the image. A volume is not the image: it belongs to one install, it is
written after the pull, and it does not travel to anybody who pulls the same
tag.

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

**This is optional and nothing is broken without it.** With no key on this
install — none in `WARDEN_POST_API_KEY` and none in this machine's own file —
the command is simply off, and that is the normal state of a fresh install: the
agent cuts, captions and delivers exactly the same, hands you the finished file
with a title and a description ready to paste, and you post that from the
YouTube app the way you already do. The key buys you one step fewer, not a
feature you are missing.

The key is an account **you** hold with that intermediary — your quota, your
bill, your connected channel. It is not something the image can carry on your
behalf, for the reason in the rule above. Here is how you get one.

#### Setting it up — five steps, about three minutes

No programming, and nothing to install. You need the Google account that
**owns the channel** you want to publish to, signed in in the same browser.

1. **Create an account** at `https://www.upload-post.com/`. The free plan is
   **10 uploads per month and does not ask for a card** — that is what their
   pricing page says.

2. **Generate the API key** at `https://app.upload-post.com/api-keys` and copy
   it. It is shown **once**; if you lose it you generate another. It is a
   password to your channel: never put it in an issue, a commit or a
   screenshot.

3. **Hand the key to this install.** There are two doors, and which one you use
   depends on where the agent is running. They are read in this order —
   **the environment wins over the file** — because the variable is the lever of
   whoever is standing up the machine right now, and the file may have been
   written weeks ago.

   **Running it yourself with `docker compose`, from this folder:** write it into
   `.env`, a plain text file beside `compose.yml`.

   ```
   WARDEN_POST_API_KEY=<the key from step 2>
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
   repository or the image. This is the door that keeps the key out of the
   conversation entirely, so prefer it when you have it.

   **Running it in the Plow cloud:** there is no `.env` and no `compose.yml`
   there — each person gets their own machine and the environment carries only
   what Plow puts in it. So the key has no way in as a variable, and the command
   that opens the other door is:

   ```sh
   echo "<the key from step 2>" | warden post setkey
   ```

   It reads the key from **standard input**, never from an argument: an argument
   is left behind in `ps`, in the shell history and in the runtime's command log,
   and all three are read by people who should not read the key to somebody's
   channel. It writes the key to `/var/lib/hermes/warden/post-api-key`, mode
   `0600`, owned by the agent's own uid `10000`, and it prints back the path and
   nothing else — the key is never printed, never logged and never repeated.
   In that setting you do not open a terminal at all: you paste the key to the
   agent in the chat and it runs that command for you. So the key does pass
   through the conversation once, because that is the only entrance there is —
   on your own machine, with an `.env`, it never has to.

   The same command works locally if you would rather not keep the key in a
   file on the host — `-T` because the key arrives on standard input and
   `docker compose exec` allocates a terminal by default, and `-u 10000:10000`
   for the reason under **By hand**:

   ```sh
   printf '%s\n' '<the key>' | docker compose exec -T -u 10000:10000 agent warden post setkey
   ```

4. **Connect the channel.** One command, and it prints **one address**:

   ```sh
   docker compose exec -u 10000:10000 agent warden post connect
   ```

   Open that address, pick the account that owns the channel on Google's own
   screen, and press **Allow**. Your Google password is typed on Google's page
   and nowhere else. Then run the same command again: it answers
   `already connected`, with the channel name, when it worked. Generating that
   address was measured against the real API on 16/09/2026.

   > **The mistake everybody makes, and how to spot it:** if any screen at this
   > point asks you for a **Client ID** or a **Client Secret**, you are on the
   > wrong road — that is the Google Cloud console, which belongs to the *other*
   > command, the one that cannot publish. **Stop and go back.** Connecting here
   > is three presses and never asks you for a string to paste.

5. **Publish one clip**, and then check it the way the next section says:

   ```sh
   docker compose exec -u 10000:10000 agent \
     warden post youtube <clip> --title "..."
   ```

   `warden post status` with no argument answers the other question — whose
   account this is, which profile it will use, where the key came from, and
   which connected accounts will actually publish. It exits `3`, not `0`, when
   the key is valid and **nothing** is connected, so a green exit code is never
   the reason to promise a publication.

There is a knob you almost never touch:
`WARDEN_POST_PROVIDER=upload-post` is the default and naming anything else is
an error, not a switch.

#### The profile, and why it is now one per machine

A profile at the intermediary is a nickname for one set of connected accounts —
in practice, one YouTube channel. The name matters more than it looks like it
should, because **the profile is the channel**: send to the wrong profile and
the video lands on somebody else's channel, and that does not come back.

Until 16/09/2026 the default name was `clip-warden`, **the same on every
install**. That was a defect with a date on it. A key can arrive through the
environment of a machine somebody else set up — that is how the owner of this
agent supplies theirs, and then several machines share one account — and with a
fixed name every one of those machines would resolve to the same profile, which
is the same channel. The first person would connect their channel and the second
would publish into it, without either of them asking for that.

So the name is now decided **once per machine**: with no `WARDEN_POST_PROFILE`
set, the install generates `clip-warden-<8 hex digits>` the first time it needs
one, writes it to `/var/lib/hermes/warden/post-profile`, and reuses it from then
on. It is in the volume, so it survives a restart; a new name on every boot
would mean a new channel on every boot.

There is one exception, and it exists to protect a channel you already
connected: when the key came from **the file on this machine** — meaning
somebody typed it here, so the account is theirs — and that account has exactly
**one** profile, the install adopts that profile instead of inventing a name.
When the key comes from the environment it never adopts, because that key may
belong to the agent's owner and the profile in it to somebody else.

**If you already had a channel connected on an earlier install, name the
profile and keep it named.** In the owner's own local install the key is in
`.env` and `.env` carries:

```
WARDEN_POST_PROFILE=Clip-Warden
```

Put the same line in yours, with the profile name **you** already use, if you
connected a channel before this change. `WARDEN_POST_PROFILE` beats both the
generated name and the adoption rule, so it is what keeps you on the profile
you have. Without it, this machine generates a fresh `clip-warden-<hex>` name,
that profile has no channel attached to it, and you would have to run
`warden post connect` and approve on Google's screen again to get back to where
you already were. Nothing is lost when that happens — the old profile is still
in your dashboard — but it is a reconnection you did not need to do.

#### `warden post tiktok` and `warden post instagram` — same road, no measurement behind them

The same key, the same host and the same single upload also reach two more
networks:

```sh
warden post tiktok <clip> --title "<caption>"        # --draft sends it to the drafts
warden post instagram <clip> --title "<caption>"     # --stories posts a Story, not a Reel
warden post youtube <clip> --title "..." --also tiktok,instagram
```

`--also` adds networks to the **same** send: the file is uploaded once and the
intermediary fans it out, rather than the clip leaving this machine three times.

**Only the YouTube road has ever been driven from here.** The field names for
TikTok and Instagram come from the intermediary's official OpenAPI spec
(`docs.upload-post.com/openapi.json`, read on 16/09/2026) and from nothing else:
no real send to either network has been made by this code, and there is no
measurement to quote. The command says so in its own output on every send that
includes one of them, and this page is not going to say less. Read what the API
returns; do not read a `200` from those two as the same kind of evidence as the
YouTube one.

Two things are the intermediary's own terms rather than ours, and you find out
about them at the worst moment if you do not know them first: **posting to
TikTok requires a paid plan there**, and **Instagram requires a Business or
Creator account**. Both are from their official documentation.

Three limits are enforced before anything is uploaded, and all three **refuse**
rather than trim — a caption truncated in silence publishes a sentence nobody
wrote:

- the YouTube title is at most **100 characters**;
- the TikTok and Instagram caption at most **2,200**, which is their documented
  limit;
- `--privacy unlisted` is **refused for TikTok**. TikTok has no "public to
  anyone with the link"; its own values are `PUBLIC_TO_EVERYONE`,
  `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR` and `SELF_ONLY`, and picking
  one of those on your behalf would be deciding who sees the video. Instagram
  has no per-post privacy at all — the profile is public or private as a whole —
  so anything but `--privacy public` is refused there too.

`--draft` carries one more caveat, from TikTok's own documentation: in draft
mode TikTok **ignores** the caption and the privacy sent through the API. The
person writes the caption in the app.

#### How you check that yours came out public

**Open the video's URL in a private / incognito window.** That window is not
signed into anything, so it sees the video the way a stranger does. If it plays,
it is public. If it says the video is private or unavailable, it is not.

A `200 OK`, a green test, or the agent saying it worked are none of them proof:
they say the request was accepted, not that anybody can watch the result. The
measurement quoted above was made that way, on a real channel, on 15/09/2026 —
and it is how yours should be confirmed too.

**When the send goes into the intermediary's queue**, the command hands back a
`request_id` instead of an address and stops waiting — the upload has already
left this machine and waiting here does not make it finish sooner. The command
that asks again is:

```sh
warden post status <request_id>
```

That is the same verb as the bare `warden post status`, which reports the
account. Until 16/09/2026 the id was **ignored in silence**: the branch that
reads the account came first and always returned, so asking about a send printed
the account listing and no address at all. It reads the id now.

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

**This is not `warden post tiktok`, and the two do not share anything.** This
one talks to TikTok directly with your own token and leaves a draft. The other
goes through the publishing intermediary with the key from the section above,
and can publish rather than only fill the inbox — with the warning that nothing
of that road has been measured from here, and that the intermediary requires a
paid plan for TikTok. Neither one makes the other unnecessary: this one needs no
paid plan, and that one needs no TikTok developer token.

## Keeping a long conversation cheap

A conversation with this agent never resets on its own, and a long one carries
every big command output it has ever produced back to the model on every single
message — which is slow and is billed every time. At boot the container turns on
a setting that throws away **old tool results** once the conversation gets
large, keeping the recent ones. Nothing you said is touched, and no summary is
written by a model.

**It is not only command output, and this has cost a delivery.** Every old tool
result is fair game, including the text of a skill the agent loaded with
`skill_view` — which is where its operating instructions live. On 16/09/2026, at
14:52:50, five seconds after the agent started rendering, the prune reclaimed
the 34,983 characters of `warden-clip` and the 8,575 of `warden-run`, and the
two clips that finished two minutes later were never handed over. The sign of it
in the history is the line the agent sees in place of the skill:

```
[SKILL_PRUNED: content lost in compression; reload with skill_view(name=warden-clip)]
```

So: it cannot garble anything, but it can take away instructions the agent is
still going to need. Anything the agent must not forget belongs in the output of
the command it is running at that moment, not only in a skill it read earlier.

To turn it off, open `compose.yml`'s volume — the setting lives in the agent's
own `config.yaml`, at `compression: proactive_prune_tokens`. Set it to `0` and
it stays `0`: the container only writes that setting when it is not already
there, so a value you chose is never overwritten.

## The agent does not review itself, and that is on purpose

A stock Hermes runs a **background curator**: after a turn, it starts a second
model turn of its own, reads the whole conversation back, and rewrites the skill
library. Nobody asks for it, and you are billed for it.

This image turns it off, for every install, by writing
`auxiliary: background_review: enabled: false` into the agent's own
`config.yaml` at boot — `image/s6-overlay/scripts/warden-curador`, a oneshot
that runs as the agent, never as root.

**Declaring it `false` is the whole point: leaving it out does not turn it
off.** The runtime reads the key fail-open —
`/opt/hermes/agent/background_review.py:187-199` returns
`is_truthy_value(task.get("enabled"), default=True)` — so an absent key is an
**enabled** curator. Measured on 16/09/2026 on this install: `config.yaml` had
no `auxiliary` section at all, and the curator was running during a recording.
It opened a turn at 18:22:41,518 (`agent.log:219`,
`msg='Review the conversation above and update the skill library...'`) and at
18:23:37,833 it tried to patch the bundled `warden-clip` skill and was refused
by the runtime (`agent.log:247`).

It was **not** what lost the two clips that day — that was the Plow adapter's
anti-duplicate gate, `plow_chat/__init__.py:2062-2065`, returning
`SendResult(success=True)` without sending. The curator is switched off for its
own reasons: an unrequested model turn that writes to the skill library.

To turn it back on, open the same `config.yaml` and set that `enabled:` to
`true`. It stays `true`: the container only writes the key when it is not
already on record, so a value you chose is never overwritten.

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
