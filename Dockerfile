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
# opencv-python-headless is here for one job: pointing the vertical crop at the
# subject instead of the middle. The footage is animation and game capture, where
# a Haar cascade trained on photographs finds nothing, so the crop landed on
# whatever sat at the centre -- the creature behind the man, the divider of a
# side-by-side. cv2's YuNet DNN detector finds those faces (measured: 7 of 7
# sampled frames on the test footage). headless because the container has no
# display, which drops the GUI libs and most of the weight.
RUN set -eu; \
    PY=/opt/hermes/.venv/bin/python3; \
    PKGS="yt-dlp>=2025.1.1 gdown>=5.2 faster-whisper>=1.1 opencv-python-headless>=4.9"; \
    if "$PY" -m pip --version >/dev/null 2>&1; then \
      "$PY" -m pip install --no-cache-dir $PKGS; \
    elif command -v uv >/dev/null 2>&1; then \
      uv pip install --python "$PY" --no-cache-dir $PKGS; \
    elif "$PY" -m ensurepip --version >/dev/null 2>&1; then \
      "$PY" -m ensurepip --upgrade && "$PY" -m pip install --no-cache-dir $PKGS; \
    else \
      echo "no pip, no uv and no ensurepip in this base image" >&2; exit 1; \
    fi; \
    "$PY" -c "import yt_dlp, gdown, faster_whisper, cv2"

# The YuNet face-detection model, fetched at build from the commit vendor/yunet.pin
# names and checked against the hash beside it -- the same discipline as the
# agent-index client, because a model file is code the detector runs. It is a
# git-lfs object, so the fetch goes through the media host that serves lfs
# content at a pinned commit rather than the raw host, which serves the pointer.
COPY vendor/yunet.pin /opt/plow/yunet.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/yunet.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/yunet.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/yunet.pin)"; \
    curl -fsSL --max-time 120 -o /opt/plow/yunet.onnx \
      "https://media.githubusercontent.com/media/opencv/opencv_zoo/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/yunet.onnx | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "yunet model is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/yunet.onnx

# The transcription models are NOT baked. They used to be, and it cost about
# 600 MB of download on every install of this agent.
#
# The reason they were baked still stands: a model fetched during a stranger's
# first request is a five-minute silence that reads as a hung agent. So the
# fetch moved rather than disappearing. A supervised service pulls both models
# in the background at boot, into the agent's own volume, while the owner is
# still reading the first reply. The image stays small, the wait lands where
# nobody is watching, and a second install on the same machine keeps the models
# it already pulled.
ENV HF_HOME=/var/lib/hermes/models
COPY --chmod=0755 image/bin/warden-models /usr/local/bin/warden-models

# Identity, and the licence. Late on purpose: the persona is the file that gets
# reworded most, and copying it before the package install and the model download
# meant every wording fix paid for both again.
COPY --chmod=0644 runtime/persona.md /opt/hermes/plow-seed/persona.md
COPY LICENSE /usr/share/doc/clip-warden/

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
#
# cache/videos is not more state: it is the one directory the runtime will
# attach a file from. Its media validator denies /var/lib outright and
# allowlists that path back in ahead of the denial, so a finished clip has to
# land there or it cannot be handed over at all. Created here, agent-owned, for
# the same reason as the rest -- whatever creates it first in a named volume
# owns it forever, and root creating it locks the agent out of its own delivery.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden/footage \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/models \
 && install -d -o 10000 -g 10000 -m 0755 /var/lib/hermes/cache \
 && install -d -o 10000 -g 10000 -m 0755 /var/lib/hermes/cache/videos
