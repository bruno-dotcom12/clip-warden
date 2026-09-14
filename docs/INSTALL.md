# Installing Clip Warden

Two commands and a text message. Most of the time is a download you can walk
away from, and nothing of yours goes into it.

```sh
git clone https://github.com/bruno-dotcom12/clip-warden.git && cd clip-warden
./install.sh
```

That is the whole install. The script clones the `plow-agents` command, logs
you in, mints the agent's credential, builds, starts, and then checks that the
container came up with everything a clip needs — refusing to call itself done
if anything is missing. It prints every command it runs, so nothing here is
hidden; if you would rather type them yourself, they are all in **By hand**
below.

It stops and tells you what to do when something is missing. It does not
continue past a problem.

## What you need

- **Docker, running, with Docker Compose**, and its VM set to **at least 4 GiB
  of RAM**. `compose.yml` caps the container at 3 GiB and the agent needs about
  2 GiB to transcribe and render at the same time; on a smaller VM the first
  clip dies as an out-of-memory kill rather than an error. Docker Desktop →
  Settings → Resources → Memory. `install.sh` measures this and warns you.
- **Git.**
- **A Plow account**, for the agent's chat line, and the phone that owns it.
- **About 5 GB of free disk for the build.** The image is roughly 4.7 GB on
  disk, of which the Plow base — 0.95 GB compressed, shared by every agent in
  this hackathon — is the part you only download once. Footage the agent pulls
  lands in a Docker volume, not in your folders, and a 20-minute source is
  another ~600 MB there.

You do **not** need Python on the host. Everything runs in the container. Python
3 is only for running the test suite (`python3 -m unittest discover -s tests`),
which is not part of installing.

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
ffprobe:             /usr/bin/ffprobe
ffmpeg:              /usr/bin/ffmpeg
face detection:      YuNet on OpenCV 5.0.0
pillow:              12.3.0
style font:          /opt/hermes/skills/warden-shared/assets/Anton-Regular.ttf
measured style spec: /opt/hermes/skills/SPECS/estilo-aprovado.json
```

The script fails if any of them says `MISSING`, and that is deliberate: every
one of those lines is a dependency whose absence used to degrade a clip in
silence rather than stop it. The face detector is the sharpest example — without
it the vertical crop falls back to the middle of the frame, and a video where
the speaker sits to one side ships with their face sliced off at the edge. That
happened. Now `warden cut` refuses to frame at all rather than guess, and tells
you to pass `--crop`.

`warden status` prints more than those six lines — where state lives, whether it
is writable, which campaigns are stored, and whether the transcription models
have arrived. The models are the one thing that is allowed to be missing at this
point: they are not in the image, and a background service pulls them on first
boot while you are reading the agent's first reply. A request that arrives first
falls back to fetching a model then, which is slower but correct.

## First run

Text the agent's line a campaign link, a video link, or just "oi". It answers
with what it read and what is still open.

**Its first question will be about sound**: whether the clip keeps the original
audio or ships silent for you to add a track in the app. It has to ask, because
a clip that ships silent by accident is a wasted post, and it stores the answer
so it only asks once. Until that is answered, `warden cut` stops rather than
guess.

If a campaign publishes no archive link, the agent says so and stops. Send it
the link to the authorised footage, or the footage itself. It does not go
looking — footage the campaign did not publish is what gets a submission thrown
out after the views.

## By hand

The script is a convenience, not a black box. This is what it does:

```sh
git clone --depth 1 https://github.com/plow-pbc/plow-agents.git .plow-agents
export PATH="$PWD/.plow-agents/bin:$PATH"

plow-agents login            # prints a phrase; text it to Plow from your phone
plow-agents lines            # prints your line UIDs, as ln_...
plow-agents mint ln_xxx      # writes ./plow-credentials

docker compose up --build -d
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

The build is 24 steps. Almost all of the wall clock is one download: the base
image. What this repository adds on top is six packages (installed with `uv` —
the base's virtualenv has no `pip` — plus the YuNet face-detection model, 
verified against the hash in `vendor/yunet.pin`), and under a minute of work
once the base is local. The `linux/amd64` emulation warning is expected: that
base publishes one architecture and the Dockerfile says so on purpose. It does
not slow the build; it only matters when the agent runs.

If the base pull fails with a `403`, it is a stale registry credential rather
than a permission you are missing:

```sh
docker logout public.ecr.aws
docker compose up --build -d
```

To watch it come up: `docker compose logs -f agent`, and wait for
`plow-init: configured … as cht_`. That line means the agent has claimed its
line and is listening.

## Where it connects

Worth knowing before you run a stranger's agent on your machine.

**While building**, it downloads from three hosts, all pinned by digest or
sha256 in the repository:

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

There is no login to any social platform in this agent, and no posting.

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
