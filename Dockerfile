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
FROM --platform=linux/amd64 public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-51f83158a70a383f03a4d03dbd8b6ea102cf0361@sha256:253d7ed3409effa7fa59113d93b4b79bb731d8264cdaf4cd60294924d0110a2e

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
# E `pytest`, que não é dependência do agente e está aqui pelo PORTÃO.
#
# Medido em 18/09/2026: `tests/test_persona.py` (32 testes) e
# `tests/test_ponteiros.py` (42) são pytest puro -- funções de módulo, sem
# `TestCase` --, então o `unittest discover` colhe ZERO deles e ainda erra ao
# importar, porque os dois fazem `import pytest`. Setenta e quatro testes não
# rodavam dentro da imagem, e entre eles estão os que pegam o truncamento do
# SOUL.md e os ponteiros que apontam para fora da imagem: exatamente a classe
# de regressão que esses arquivos foram escritos para pegar, cega justamente
# onde ela acontece.
#
# São ~5 MB. O venv da imagem não tem `pip` nem `ensurepip`, então instalar na
# hora do teste não é uma saída -- ou entra aqui, ou não roda.
#
# E SÓ ISTO NÃO BASTA, o que uma auditoria pegou em 18/09/2026: o portão
# documentado era `unittest discover`, que colhe zero daqueles arquivos MESMO
# com pytest instalado. Instalar sem trocar o comando seria pior do que não
# instalar -- os dois erros de import ficariam quietos e os 74 continuariam
# sem rodar, agora em silêncio. A receita de `docs/INSTALL.md` foi trocada
# para `-m pytest tests -q` no mesmo commit.
RUN set -eu; \
    PY=/opt/hermes/.venv/bin/python3; \
    PKGS="yt-dlp>=2026.08.19 gdown>=5.2 faster-whisper>=1.1 opencv-python-headless>=4.9 pillow>=10.0 numpy>=1.24 curl_cffi>=0.7 pytest>=8.0"; \
    if "$PY" -m pip --version >/dev/null 2>&1; then \
      "$PY" -m pip install --no-cache-dir $PKGS; \
    elif command -v uv >/dev/null 2>&1; then \
      uv pip install --python "$PY" --no-cache-dir $PKGS; \
    elif "$PY" -m ensurepip --version >/dev/null 2>&1; then \
      "$PY" -m ensurepip --upgrade && "$PY" -m pip install --no-cache-dir $PKGS; \
    else \
      echo "no pip, no uv and no ensurepip in this base image" >&2; exit 1; \
    fi; \
    "$PY" -c "import yt_dlp, gdown, faster_whisper, cv2, PIL, numpy, pytest"

# curl_cffi above, the yt-dlp floor above, and the pinned PO token plugin below
# are one fix for one measured failure, not housekeeping. Measured 15/09/2026:
#
# The agent could not pull ANY YouTube link and told the owner, on two different
# days, two different invented reasons -- "that specific video", then "this
# server's IP". What `yt-dlp -v` actually printed was `JS runtimes: none` and
# `PO Token Providers: none`.
#
# - bgutil-ytdlp-pot-provider (installed from the pin below, not from the index)
#   mints the Proof-of-Origin token YouTube now wants.
#   It does NOT rescue an address already flagged -- that was measured here, with
#   a valid freshly minted token bound to matching visitor data, and the refusal
#   was identical. It is carried because it is what stops an install being
#   flagged in the FIRST place, and this agent is published: the address it
#   burns belongs to whoever installed it. 2.0.0 is a floor, not a preference --
#   1.3.x binds its server to 0.0.0.0 and that was a remote code execution hole
#   (GHSA-qpv9-8xfj-xx9m).
# - curl_cffi is what gives yt-dlp `--impersonate`, so its TLS handshake is not
#   a bare Python one. On its own it did not lift the block here either; it is
#   carried for the same prophylactic reason.
# - The yt-dlp floor is the newest stable release as of this date, and the one
#   every measurement in this file was taken on. It is a floor rather than a pin
#   because YouTube breaks extractors on its own schedule and an install pinned
#   to a past release is an install that stops working; checked 15/09, there is
#   no stable newer than this and no bot-check fix landed after it.

# The PO token plugin, fetched at build from the exact wheel vendor/potprovider.pin
# names and checked against the hash beside it -- NOT resolved from the index.
# The pin file carries the measurement explaining why; the short version is that
# this base's resolver silently falls back to 1.3.2, and 1.3.x is a known RCE.
COPY vendor/potprovider.pin /opt/plow/potprovider.pin
RUN set -eu; \
    url="$(sed -n 's/^url=//p' /opt/plow/potprovider.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/potprovider.pin)"; \
    ver="$(sed -n 's/^version=//p' /opt/plow/potprovider.pin)"; \
    whl="/tmp/$(basename "$url")"; \
    curl -fsSL --max-time 120 -o "$whl" "$url"; \
    got="$(sha256sum "$whl" | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "pot provider wheel is $got, pin says $want" >&2; exit 1; }; \
    uv pip install --python /opt/hermes/.venv/bin/python3 --no-cache-dir "$whl"; \
    rm -f "$whl"; \
    /opt/hermes/.venv/bin/python3 -c "\
import importlib.metadata as m; \
v = m.version('bgutil-ytdlp-pot-provider'); \
print('pot provider installed:', v); \
exit(0 if v == '$ver' else 1)"

# A JavaScript runtime, checked at BUILD rather than discovered by a stranger.
#
# This base carries node, and yt-dlp accepts it -- but yt-dlp enables only deno
# on its own, so `warden` passes `--js-runtimes` explicitly (see `_js_runtimes`
# in warden_media.py). Without a runtime yt-dlp drops the `web` client from its
# default set and cannot decipher n/sig, which is a permanent handicap that
# shows up as a download failure rather than as a missing dependency. deno is
# deliberately NOT added: node is already here and officially supported, and a
# second runtime would be ~100 MB and another supply chain for nothing.
#
# If a future base stops carrying node, this is where that should be found out.
RUN set -eu; \
    command -v node >/dev/null || { \
      echo "this base image has no node, and without a JS runtime yt-dlp cannot" >&2; \
      echo "extract YouTube: it drops the web client and cannot decipher n/sig" >&2; \
      exit 1; }; \
    /opt/hermes/.venv/bin/python3 -m yt_dlp --list-impersonate-targets >/dev/null; \
    /opt/hermes/.venv/bin/python3 -c "\
import yt_dlp.plugins as P; \
from yt_dlp.globals import plugin_specs; \
[P.load_plugins(s) for s in plugin_specs.value.values()]; \
from yt_dlp.extractor.youtube.pot._registry import _pot_providers as r; \
n = sorted(r.value.keys()); \
print('PO token providers yt-dlp can see:', n); \
exit(0 if any('bgutil' in x.lower() for x in n) else 1)"

# O GERADOR de PO token, dentro desta imagem, em modo SCRIPT.
#
# Até 16/09/2026 quem emitia o token era um segundo container escutando a porta
# 4416. A nuvem da Plow roda UM container por pessoa e o contrato dela proíbe um
# listener de entrada, com essas palavras: "no inbound listener". O modo script
# do mesmo projeto resolve os dois lados: o plugin executa um processo Node
# quando precisa de um token, nada escuta porta, e quem instala localmente passa
# a precisar de uma imagem a menos.
#
# `npm ci` e não `npm install`: o lockfile deles é o que fixa a árvore inteira,
# e resolver de novo no build seria trocar um pin auditado por o que o registro
# servir hoje. `npm prune --omit=dev` depois do `tsc` porque o TypeScript não é
# preciso para RODAR o que ele gerou.
COPY vendor/potprovider.pin /opt/plow/potprovider.pin
RUN set -eu; \
    sha="$(sed -n 's/^server_sha=//p' /opt/plow/potprovider.pin)"; \
    want="$(sed -n 's/^server_sha256=//p' /opt/plow/potprovider.pin)"; \
    curl -fsSL --max-time 180 -o /tmp/bgutil.tar.gz \
      "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/${sha}.tar.gz"; \
    got="$(sha256sum /tmp/bgutil.tar.gz | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "bgutil server tarball is $got, pin says $want" >&2; exit 1; }; \
    mkdir -p /opt/plow/bgutil; \
    tar -xzf /tmp/bgutil.tar.gz --strip-components=1 -C /opt/plow/bgutil; \
    rm -f /tmp/bgutil.tar.gz; \
    cd /opt/plow/bgutil/server; \
    npm ci --no-audit --no-fund; \
    npx tsc; \
    npm prune --omit=dev --no-audit --no-fund; \
    npm cache clean --force; \
    test -f /opt/plow/bgutil/server/build/generate_once.js; \
    chown -R root:root /opt/plow/bgutil; \
    find /opt/plow/bgutil -type d -exec chmod 0755 {} + ; \
    find /opt/plow/bgutil -type f -exec chmod 0644 {} +

# E a prova de que ele EMITE, no build, em vez de ser descoberta por um estranho
# no primeiro link. Uma execução do gerador; se ela não devolver um token, a
# imagem não sai.
#
# `--content-binding` e não `-v`: em 2.0.0 o `-v` é `--visitor-data` e está
# deprecado ("Visitor data is deprecated, use --content-binding instead"), o que
# fez esta verificação falhar na primeira tentativa. O valor é qualquer coisa
# que amarre o token a um contexto; aqui é um literal, porque o que se prova é
# que o gerador EMITE, não qual vídeo ele viu.
RUN set -eu; \
    node /opt/plow/bgutil/server/build/generate_once.js \
      --content-binding clip-warden-build-check \
      > /tmp/pot.json 2>/tmp/pot.err \
      || { echo "the PO token generator did not run:" >&2; cat /tmp/pot.err >&2; exit 1; }; \
    grep -q poToken /tmp/pot.json \
      || { echo "the PO token generator ran and produced no token:" >&2; \
           cat /tmp/pot.json /tmp/pot.err >&2; exit 1; }; \
    echo "PO token generator: emits, in script mode, no listener"; \
    rm -f /tmp/pot.json /tmp/pot.err

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
    chmod 0644 /opt/plow/yunet.onnx; \
    /opt/hermes/.venv/bin/python3 -c "import cv2; cv2.FaceDetectorYN_create('/opt/plow/yunet.onnx','',(320,320),0.6,0.3,5000)"

# O detector construído, não só os dois arquivos presentes. Um OpenCV que importa
# com um modelo cujo hash bate ainda pode não montar o detector -- e essa falha
# aparecia na primeira renderização de um host, como um rosto na borda do quadro,
# não aqui. `warden status` faz a mesma pergunta em tempo de execução, e `cut`
# para em vez de enquadrar no centro quando a resposta é não.

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

# Os dois padrões que vinham do `compose.yml` e que precisam valer num
# `docker run` solto -- que é como a nuvem da Plow sobe um agente.
#
# Dois renders ao mesmo tempo: medido em 16/09/2026 neste container, o mesmo
# lote de dois clipes de 20s levou 105s em fila e 54,7s em paralelo, com pico de
# 1584 MiB contra 1815 MiB. Metade do tempo, e o pico ABAIXO do sequencial.
#
# `AGENT_ID` é o id no Agent Index. Sem ele o reporter de uso fica parado, e
# ficar parado por falta de um padrão que o repositório inteiro já conhece é
# uma instalação que não conta.
#
# Os dois continuam podendo ser trocados pelo ambiente: o compose sobrescreve, e
# a Plow também.
ENV WARDEN_RENDER_PARALELO=2
ENV AGENT_ID=clip-warden
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
COPY warden-style/    /opt/hermes/skills/warden-style/
COPY warden-shared/   /opt/hermes/skills/warden-shared/
# A faixa medida do corpus aprovado: `warden style check` compara com ela.
COPY SPECS/           /opt/hermes/skills/SPECS/

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

# O CLIENTE OAUTH DO GOOGLE NÃO ENTRA NESTA IMAGEM. Nem por ARG, nem por ENV,
# nem por COPY. Isto é a correção de um defeito, não uma preferência.
#
# O que estava aqui até 15/09/2026:
#
#   ARG WARDEN_YT_CLIENT_ID=""
#   ENV WARDEN_YT_CLIENT_ID=${WARDEN_YT_CLIENT_ID}
#   (e o mesmo par para o SECRET)
#
# alimentado pelos segredos do repositório no `publish.yml`. O raciocínio era
# "o par não está no código-fonte, então não está exposto". Está: um `ARG` que
# vira `ENV` é gravado na configuração da imagem, e a imagem é PÚBLICA no ghcr.
# Ler não exige nem baixar as camadas -- o blob de configuração vem por HTTP
# com um token anônimo de pull. MEDIDO no manifesto publicado de `:latest` em
# 15/09/2026: `WARDEN_YT_CLIENT_ID` e `WARDEN_YT_CLIENT_SECRET` estavam no
# `Env`, com valor.
#
# A política III.D.1 dos Serviços de API do YouTube proíbe literalmente
# "embed your API Credentials in open source projects". Um repositório público
# MIT que publica uma imagem pública é exatamente isso, e o argumento de que o
# Google não trata o secret de cliente "TV e entrada limitada" como
# confidencial responde a outra pergunta: ele fala do RISCO daquele par, não da
# regra do programa, e a auditoria da API é lida contra a regra.
#
# COMO A CREDENCIAL CHEGA AGORA: pelo ambiente de quem instala, no
# `environment:` do compose.yml, que a lê do shell ou do `.env` ao lado dele --
# o mesmo caminho do `plow-credentials`, que também nunca esteve na imagem, e
# pelo mesmo motivo: é de uma pessoa, não da imagem.
#
# AUSENTE, DEGRADA E DIZ: `warden_youtube.py` lê as duas do ambiente e
# `_cliente_configurado()` devolve falso quando faltam. O boot não muda, nenhum
# outro comando muda, e só `warden youtube` recusa, nomeando o que falta. Ver
# docs/INSTALL.md, "Handing a clip to YouTube or TikTok".

COPY image/s6-overlay/ /etc/s6-overlay/

# O passo que copia a credencial da Plow do bind para o caminho que o runtime
# lê. Ele não existia -- nem aqui nem na imagem base -- e sem ele uma instalação
# LOCAL nova sobe inteira e não responde a ninguém. O porquê está escrito no
# próprio arquivo; aqui fica só o que decide a POSIÇÃO: `cont-init.d` roda como
# root antes de qualquer serviço, e `005-` ordena depois do `00-plow-sanitize`
# da base e antes do `plow-init`, que é quem espera o arquivo.
COPY --chmod=0755 image/cont-init.d/005-plow-credential /etc/cont-init.d/005-plow-credential

# State and working room: agent-owned, 0700, empty until a campaign is stored.
# Footage lands under here, so the host's disk is where a long source goes.
#
# cache/videos is not more state: it is the one directory the runtime will
# attach a file from. Its media validator denies /var/lib outright and
# allowlists that path back in ahead of the denial, so a finished clip has to
# land there or it cannot be handed over at all. Created here, agent-owned, for
# the same reason as the rest -- whatever creates it first in a named volume
# owns it forever, and root creating it locks the agent out of its own delivery.
# `warden/campaigns` entra nesta lista porque `state_dir()` faz mkdir dele na
# primeira chamada, e um `docker compose exec` -- que entra como root -- o criaria
# root:root dentro de um diretório 0700 do uid 10000. O agente perderia a escrita
# e `campaign save` passaria a falhar; pior, `warden status` diria `writable: yes`,
# porque root escreve, e o diagnóstico esconderia o dano.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden/footage \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/warden/campaigns \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/models \
 && install -d -o 10000 -g 10000 -m 0755 /var/lib/hermes/cache \
 && install -d -o 10000 -g 10000 -m 0755 /var/lib/hermes/cache/videos
