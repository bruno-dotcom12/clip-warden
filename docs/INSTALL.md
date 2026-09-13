# Installing Clip Warden

Twenty minutes, most of it a build you can walk away from, and nothing of yours
goes into it.

## What you need

- Docker, running, with Docker Compose.
- Python 3. Standard library only; nothing to `pip install`.
- Git.
- A Plow account, for the agent's chat line, and the phone that owns it.
- Roughly 5 GB of disk, most of it the Plow base image that every agent in
  this hackathon shares, so if you have run another one you already have it.
  Footage you pull lands in a Docker volume, not in your folders.

## 1. The `plow-agents` command

Nothing else here works without it, and it does not come with Docker or with
this repository. It is a checkout you put on your PATH:

```sh
git clone https://github.com/plow-pbc/plow-agents.git
export PATH="$PWD/plow-agents/bin:$PATH"
```

That `export` lasts as long as the terminal window. If you open a new one, run
it again, or add the line to your shell profile. `plow-agents --help` printing
a usage block is how you know this step worked.

## 2. A line for the agent

```sh
plow-agents login          # prints an activation phrase
```

Text that phrase to Plow from the phone that owns your account. Then:

```sh
plow-agents lines          # prints the line UIDs you own, as ln_...
```

Pick a free one. The agent answers on that number, so it should not be a line
you already use for something else.

## 3. The credential

```sh
git clone <this repo>
cd clip-warden
plow-agents mint ln_xxx    # the UID from the step above, not the phone number
```

`mint` takes the line as an argument — there is no default and no prompt, and
running it bare only prints a usage error. It writes `./plow-credentials`, which
is a live token: never commit it, never paste it anywhere.

## 4. Build and start

```sh
docker compose up --build -d
```

The first build takes ten to fifteen minutes on an Apple Silicon Mac and less
on an Intel one. Almost all of it is pulling the base image, and the emulation
warning about `linux/amd64` is expected — that base publishes one architecture,
and the Dockerfile says so on purpose.

If the pull fails with a `403`, it is a stale registry credential rather than a
permission you are missing:

```sh
docker logout public.ecr.aws
docker compose up --build -d
```

## 5. Check it came up

```sh
docker compose logs -f agent     # ctrl-c to leave
```

Wait for `plow-init: configured ... as cht_`. That line means the agent has
claimed its line and is listening. Then:

```sh
docker compose exec agent warden status
```

It prints where state lives, which campaigns are stored, and whether ffmpeg and
ffprobe are on the path. Both must say a path, never MISSING.

The transcription models are not in the image: a background service pulls them
on first boot while you are reading the agent's first reply. `warden status`
also says whether they have arrived. It does not block on them — a request that
arrives first falls back to fetching a model then, which is slower but correct.

## 6. First run

Text the agent's line a campaign link, or paste a brief. It answers with what it
read out of the brief and what the brief left open. From there, ask it for clips.

If the campaign publishes no archive link, the agent will say so and stop. Send
it the link to the authorised footage, or the footage itself.

## Where it connects

Worth knowing before you run a stranger's agent on your machine. It reaches:

- **Plow**, for its chat line. This is how you talk to it.
- **The campaign links you send it**, and only those, to read a brief.
- **The archive links published inside a brief**, to pull footage. `http` and
  `https` only; anything else in a brief is treated as text, not a link.
- **Hugging Face**, once per install, for the two transcription models.
- **The AI Worth Using Agent Index**, hourly, described below.
- **Four public campaign directories**, when you ask it to go find a campaign:
  `clipmap.gg`, `whop.com`, `clipradar.co` and `realoficial.com.br`.
  `warden discover` is the only command that reaches them, it runs only when you
  ask for a campaign, and `WARDEN_DIRECTORIES` replaces the list if you would
  rather it looked somewhere else.

There is no login to any social platform in this agent, and no posting.

## Usage reporting

This image reports token counts to the AI Worth Using Agent Index, hourly, under
the `AGENT_ID` in `compose.yml`. Day and model token counts, and nothing else:
no prompts, no file paths, no costs. There is no switch, because an agent whose
owner does not want that is one built without the service in its Dockerfile.

## Uninstall

```sh
plow-agents revoke               # retires the agent's credential at Plow
docker compose down -v
```

`-v` takes the volume with it, which is the campaigns, the ledger, the clips and
any footage that was pulled.
