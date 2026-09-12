# Installing Clip Warden

Ten minutes, and nothing of yours goes into it.

## What you need

- Docker, running.
- A Plow account, for the agent's chat line.
- Roughly 5 GB of disk, most of it the Plow base image that every agent in
  this hackathon shares, so if you have run another one you already have it.
  Footage you pull lands in a Docker volume, not in your folders.

## Steps

```sh
git clone <this repo>
cd clip-warden
plow-agents login          # opens a browser, once
plow-agents mint           # writes ./plow-credentials, a live token: never commit it
docker compose up --build -d
```

The first build takes a few minutes, almost all of it the base image. The
transcription models are not in the image: a background service pulls them on
first boot while you are reading the agent's first reply. `warden status` says
whether they have arrived.

## Check it came up

```sh
docker compose logs -f agent     # ctrl-c to leave
docker compose exec agent warden status
```

`warden status` prints where state lives, which campaigns are stored, and
whether ffmpeg and ffprobe are on the path. Both must say a path, never MISSING.

## First run

Text the agent a campaign link, or paste a brief. It answers with what it read
out of the brief and what the brief left open. From there, ask it for clips.

If the campaign publishes no archive link, the agent will say so and stop. Send
it the link to the authorised footage, or the footage itself.

## Usage reporting

This image reports token counts to the AI Worth Using Agent Index, hourly, under
the `AGENT_ID` in `compose.yml`. Day and model token counts, and nothing else:
no prompts, no file paths, no costs. There is no switch, because an agent whose
owner does not want that is one built without the service in its Dockerfile.

## Uninstall

```sh
docker compose down -v
```

`-v` takes the volume with it, which is the campaigns, the ledger and any
footage that was pulled.
