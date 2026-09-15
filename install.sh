#!/bin/sh
# Do clone ao agente de pé, em um comando.
#
# O INSTALL pedia nove comandos digitados na ordem certa, e a ordem importava de
# um jeito que não estava escrito: `docker compose up` antes do `mint` faz o
# Docker criar um DIRETÓRIO chamado plow-credentials, o agente sobe sem
# credencial, e o `mint` seguinte falha ao escrever um arquivo onde há uma pasta.
# Nove chances de errar uma.
#
# Este script faz a ordem, e checa antes de cada passo o que aquele passo precisa.
# Ele não esconde nada: imprime cada comando que roda, e qualquer um deles pode
# ser rodado à mão -- o docs/INSTALL.md continua listando todos.
#
# POSIX sh de propósito: a máquina de um host não é a nossa.
set -eu

RAIZ="$(cd "$(dirname "$0")" && pwd)"
cd "$RAIZ"

diga()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
rode()  { printf '  $ %s\n' "$*"; "$@"; }
pare()  { printf '\n\033[31mparou:\033[0m %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
    ""|--remover) ;;
    *) printf 'uso: ./install.sh            instala\n'
       printf '     ./install.sh --remover  desinstala\n'; exit 2 ;;
esac

if [ "${1:-}" = "--remover" ]; then
    diga "removendo"
    # revoke primeiro: derrubar o container antes deixa o token vivo no Plow.
    [ -x "$RAIZ/.plow-agents/bin/plow-agents" ] && \
        "$RAIZ/.plow-agents/bin/plow-agents" revoke || true
    rode docker compose down -v
    # O token é um bearer vivo; deixá-lo no disco depois de "desinstalar" é a
    # incoerência que o próprio INSTALL avisava não cometer.
    rm -f plow-credentials
    diga "removido -- some o container, o volume (campanhas, ledger, clipes e
material baixado) e a credencial."
    exit 0
fi

# ---------------------------------------------------------------- 1. o que precisa

diga "1/5  conferindo o que esta maquina tem"

command -v git >/dev/null 2>&1 || pare "git não está instalado."
command -v docker >/dev/null 2>&1 || pare "docker não está instalado."
docker info >/dev/null 2>&1 || pare "o Docker não está rodando. Abra o Docker Desktop e tente de novo."
docker compose version >/dev/null 2>&1 || pare "este Docker não tem 'docker compose'. Atualize o Docker Desktop."

# A RAM da VM do Docker, que não estava em requisito nenhum e derrubava o
# primeiro corte por OOM. compose.yml fixa mem_limit em 3g no agente e o agente
# precisa de ~2 GiB para transcrever e renderizar ao mesmo tempo.
#
# Desde 15/09/2026 o compose sobe um segundo container ao lado: o `pot`, que
# emite o PO token que o YouTube exige de quem pede deslogado. São mais 512m de
# teto, e a soma passou de 3g para 3,5g. Os 4 GiB que este aviso pedia deixavam
# 1 GiB para o resto da VM; a mesma folga com o sidecar são 4,5 GiB -- por isso
# a conta virou MiB, que "GiB inteiro" não expressa.
MEM_BYTES="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
MEM_MIB=$(( MEM_BYTES / 1048576 ))
if [ "$MEM_MIB" -lt 4608 ]; then
    printf '  aviso: a VM do Docker tem %s MiB. O compose limita 3g no agente\n' "$MEM_MIB"
    printf '         mais 512m no sidecar pot: 3,5 GiB de teto somado, e só o\n'
    printf '         agente já precisa de ~2 GiB para transcrever e renderizar junto.\n'
    printf '         Docker Desktop > Settings > Resources > Memory: suba para 4,5 GiB.\n'
    printf '         Seguindo assim mesmo -- o primeiro corte pode morrer por OOM.\n'
fi
printf '  ok: git, docker, compose, %s MiB de RAM na VM\n' "$MEM_MIB"

# ---------------------------------------------------------------- 2. plow-agents

diga "2/5  o comando plow-agents"

# Dentro do repo e ignorado pelo git: um checkout solto no diretório de cima é
# uma coisa a mais para o host lembrar de apagar depois.
if [ ! -d .plow-agents ]; then
    rode git clone --depth 1 --quiet https://github.com/plow-pbc/plow-agents.git .plow-agents
else
    printf '  já está aqui: .plow-agents\n'
fi
PA="$RAIZ/.plow-agents/bin/plow-agents"
[ -x "$PA" ] || pare "o clone do plow-agents não trouxe bin/plow-agents."

# ---------------------------------------------------------------- 3. a linha

diga "3/5  a linha do agente"

if [ -s plow-credentials ]; then
    printf '  plow-credentials já existe -- pulando login e mint.\n'
    printf '  (para trocar de linha: rm plow-credentials e rode de novo)\n'
elif [ -d plow-credentials ]; then
    # A armadilha exata: `up` antes do `mint` deixa isto aqui.
    pare "plow-credentials é um DIRETÓRIO. Foi o 'docker compose up' rodando
antes do 'mint': o bind do compose cria uma pasta quando o arquivo não existe.
Rode 'docker compose down' e 'rmdir plow-credentials', depois este script."
else
    if [ "${LINHA:-}" = "" ]; then
        printf '  o login manda uma frase de ativação para o seu telefone.\n'
        rode "$PA" login
        printf '\n  suas linhas:\n'
        "$PA" lines
        printf '\n  qual linha o agente usa? (o UID ln_..., não o número): '
        read -r LINHA
    fi
    case "$LINHA" in
        ln_*) ;;
        *) pare "'$LINHA' não parece um UID de linha. É o ln_... que 'plow-agents lines' imprime." ;;
    esac
    rode "$PA" mint "$LINHA"
    [ -s plow-credentials ] || pare "o mint não escreveu plow-credentials."
fi

# ---------------------------------------------------------------- 4. subir

# Puxa a imagem publicada. `--build` só a pedido, para quem desenvolve: um
# build na máquina de quem instala custa 4,68 GB de imagem mais 4,68 GB de cache,
# e faz de qualquer tropeço de build uma instalação falha por um motivo que não é
# dele.
if [ "${WARDEN_BUILD:-}" = "1" ]; then
    diga "4/5  build local (WARDEN_BUILD=1) e start"
    rode docker compose -f compose.yml -f compose.build.yml up --build -d
else
    diga "4/5  baixando a imagem (~1 GB) e subindo"
    rode docker compose up -d
fi

# ---------------------------------------------------------------- 5. conferir

diga "5/5  conferindo o agente de pé"

printf '  esperando o agente reivindicar a linha'
i=0
while [ "$i" -lt 90 ]; do
    if docker compose logs agent 2>/dev/null | grep -q 'plow-init: configured'; then
        printf ' -- pronto\n'
        break
    fi
    printf '.'
    sleep 2
    i=$((i + 1))
done
printf '\n'

# `-u 10000:10000`: `exec` entra como root, e root criando estado num volume
# nomeado passa a ser dono dele para sempre. O agente perderia a escrita no
# próprio diretório, e `warden status` ainda diria `writable: yes`.
ESTADO="$(docker compose exec -u 10000:10000 -T agent warden status 2>&1 || true)"
printf '%s\n' "$ESTADO"

# O que não pode faltar. Quase todas estas linhas já degradaram um clipe em
# silêncio -- a de detecção de rosto entregou um corte com o rosto na borda do
# quadro. libass é a exceção: sem o filtro `ass` o `cut` recusa queimar legenda
# em voz alta, e é por isso que ele está aqui -- para a recusa acontecer nesta
# tela, no install, e não no meio do primeiro corte de alguém.
FALTA=0
for CHAVE in "ffprobe" "ffmpeg" "face detection" "pillow" "style font" "measured style spec" "libass"; do
    if printf '%s\n' "$ESTADO" | grep -i "^$CHAVE" | grep -q "MISSING"; then
        printf '\n\033[31mfalta:\033[0m %s\n' "$CHAVE"
        FALTA=1
    fi
done
[ "$FALTA" -eq 0 ] || pare "o agente subiu sem algo de que precisa para cortar.
Isso é um defeito da imagem, não da sua máquina: abra uma issue com a saída acima."

diga "pronto"
cat <<'FIM'
  Mande uma mensagem para a linha do agente com um link de campanha, um link de
  vídeo, ou só "oi". Ele responde e conduz a partir daí.

  A primeira pergunta dele vai ser sobre o som: se o clipe mantém o áudio
  original ou sai mudo para você pôr a trilha no app. Ele guarda a resposta.

  Para acompanhar:   docker compose logs -f agent
  Para desinstalar:  ./install.sh --remover
FIM
