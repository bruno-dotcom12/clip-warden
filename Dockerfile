# check=skip=FromPlatformFlagConstDisallowed
# The constant platform below is deliberate and the linter's advice does not
# apply: this base publishes one architecture, so resolving it from the build
# host would only ask for an image that does not exist.
# Clip Warden, built for the Plow cloud image.
#
# The base tag is an immutable `base-<sha>` naming one commit of
# plow-pbc/plow-hermes-agent, pinned by digest as well: every install of this
# agent runs while holding that owner's Plow credential, so a moving tag would
# substitute code underneath them.
#
# `--platform` is explicit because this base publishes linux/amd64 only. On an
# Apple Silicon host the build then runs emulated, which is slow but correct;
# without the flag the same thing happens with a warning that reads like a
# problem somebody should fix, and there is nothing to fix until the base ships
# arm64.
FROM --platform=linux/amd64 public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-4747960eaa8a44ac24424bf0cc6c22559af61f43@sha256:fe9b0f428f9ed2da1698ecf0b504c79eceb9e016e770291ff6b3418b9f65449d

# Identity. plow-init composes SOUL.md at boot as the base persona followed by
# this file; nothing here restates what the base already carries.
COPY --chmod=0644 runtime/persona.md /opt/hermes/plow-seed/persona.md
COPY LICENSE /usr/share/doc/clip-warden/

# ffmpeg decides every number this agent states about a clip. The base carries
# it; if a future base stops carrying it, the build is where that should be
# found out, not a tenant's first cut.
RUN command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null \
 || { echo "this base image has no ffmpeg, and the whole agent measures video" >&2; exit 1; }

# Pulling footage and turning it into words. Installed into the agent's own venv,
# which is the interpreter every skill command runs under.
#
# faster-whisper rather than openai-whisper: this runs on a container CPU with no
# GPU passthrough on any host, and int8 CTranslate2 is the difference between
# eight minutes per source hour and forty.
# The venv in this base has no pip: it was built with uv, which installs into a
# virtualenv without leaving pip inside it. So ask what is actually there rather
# than assuming, and fail with the reason if none of the three ways exist.
RUN set -eu; \
    PY=/opt/hermes/.venv/bin/python3; \
    PKGS="yt-dlp>=2025.1.1 gdown>=5.2 faster-whisper>=1.1"; \
    if "$PY" -m pip --version >/dev/null 2>&1; then \
      "$PY" -m pip install --no-cache-dir $PKGS; \
    elif command -v uv >/dev/null 2>&1; then \
      uv pip install --python "$PY" --no-cache-dir $PKGS; \
    elif "$PY" -m ensurepip --version >/dev/null 2>&1; then \
      "$PY" -m ensurepip --upgrade && "$PY" -m pip install --no-cache-dir $PKGS; \
    else \
      echo "no pip, no uv and no ensurepip in this base image" >&2; exit 1; \
    fi; \
    "$PY" -c "import yt_dlp, gdown, faster_whisper" 

# The transcription models, fetched at build. Lazily downloading one would put a
# silent five-minute wait inside a stranger's first request, which reads as a
# hung agent and is how a verified install gets uninstalled.
#
# Two of them, because one size cannot serve both shapes of source. Measured on
# this image, emulated amd64 on Apple Silicon: `small` runs at 0.62x realtime,
# which is right for a campaign archive of short clips and is 37 minutes for an
# hour-long podcast. `base` is roughly three times faster and still good enough
# to choose a moment from, so the runtime picks it past fifteen minutes of
# source and says why.
ENV HF_HOME=/opt/plow/models
RUN mkdir -p /opt/plow/models \
 && /opt/hermes/.venv/bin/python3 -c \
      "from faster_whisper import WhisperModel; \
       WhisperModel('small', device='cpu', compute_type='int8'); \
       WhisperModel('base', device='cpu', compute_type='int8')" \
 && chmod -R a+rX /opt/plow/models

# The skills, outside every home, so a bind-mounted home still gets them and an
# image update still reaches a skill the agent has not customised.
COPY warden-run/      /opt/hermes/skills/warden-run/
COPY warden-campaign/ /opt/hermes/skills/warden-campaign/
COPY warden-check/    /opt/hermes/skills/warden-check/
COPY warden-clip/     /opt/hermes/skills/warden-clip/
COPY warden-package/  /opt/hermes/skills/warden-package/
COPY warden-shared/   /opt/hermes/skills/warden-shared/

RUN find /opt/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} +

# The usage reporter, fetched at build from the commit vendor/client.pin names
# and checked against the hash beside it. A sha in a URL is only as good as the
# host serving it, and this file runs inside an agent holding a live credential.
COPY vendor/client.pin /opt/plow/agent-index-client.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/agent-index-client.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/agent-index-client.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/agent-index-client.pin)"; \
    curl -fsS --max-time 60 -o /opt/plow/agent-index-client.py \
      "https://raw.githubusercontent.com/plow-pbc/agent-index-client/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/agent-index-client.py | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "agent-index client is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/agent-index-client.py

# The command the skills and the persona call. Installed by the image, on the
# PATH in two standard places, running the image's own copy: the runtime strips
# the executable bit off skill files on their way into the agent's home, and a
# .py under that home is a file a turn could rewrite.
COPY --chmod=0755 image/bin/warden /usr/local/bin/warden
RUN ln -sf /usr/local/bin/warden /usr/bin/warden

COPY image/s6-overlay/ /etc/s6-overlay/

# State and working room: agent-owned, 0700, empty until a campaign is stored.
# Footage lands under here, so the host's disk is where a long source goes.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden/footage
