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
    # Dois arquivos FICAM, de propósito: são credenciais suas, não do agente, e
    # apagar credencial de alguém sem perguntar é pior do que deixar. Mas ficar
    # em silêncio sobre elas também é ruim -- quem "desinstalou" acha que não
    # sobrou nada. Então: não apaga, avisa onde estão.
    if [ -f "$RAIZ/.env" ] || [ -f "$RAIZ/cookies.txt" ]; then
        printf '\n  \033[33matenção:\033[0m estes arquivos NÃO foram apagados, porque são seus:\n'
        if [ -f "$RAIZ/.env" ]; then
            printf '    %s/.env\n' "$RAIZ"
            printf '      a chave que publica (WARDEN_POST_API_KEY) e o segredo do\n'
            printf '      seu cliente OAuth do Google.\n'
        fi
        if [ -f "$RAIZ/cookies.txt" ]; then
            printf '    %s/cookies.txt\n' "$RAIZ"
            printf '      uma sessão do YouTube já logada, viva enquanto existir.\n'
        fi
        printf '  Se não vai mais usar este agente, apague à mão o que listamos acima.\n'
    fi
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
SUBIU=0
while [ "$i" -lt 90 ]; do
    if docker compose logs agent 2>/dev/null | grep -q 'plow-init: configured'; then
        printf ' -- pronto\n'
        SUBIU=1
        break
    fi
    printf '.'
    sleep 2
    i=$((i + 1))
done
printf '\n'

# Estourar os 180s é FALHA, e antes não era: o laço imprimia noventa pontos e
# seguia em frente, então uma instalação em que o container nunca subiu chegava
# até a última tela e lia `pronto`. Quem espera três minutos merece saber que
# esperou em vão.
if [ "$SUBIU" -ne 1 ]; then
    printf '\n  as últimas linhas do log do agente:\n'
    docker compose logs --tail 25 agent 2>&1 | sed 's/^/    /' || true
    pare "passaram 180 segundos e o agente não reivindicou a linha.
Ele não está de pé. Nada abaixo disto foi conferido.
Veja o log inteiro com 'docker compose logs agent'; se ele fala em credencial,
rode 'docker compose down', apague plow-credentials e rode este script de novo."
fi

# `-u 10000:10000`: `exec` entra como root, e root criando estado num volume
# nomeado passa a ser dono dele para sempre. O agente perderia a escrita no
# próprio diretório, e `warden status` ainda diria `writable: yes`.
#
# E o `|| true` que estava aqui transformava "o comando inteiro falhou" em
# "nada faltando": o container fora do ar devolvia a mensagem de erro do docker,
# nenhum `grep` casava, e o script imprimia `pronto` para uma instalação morta.
# O código de saída é a primeira coisa que se olha.
if ESTADO="$(docker compose exec -u 10000:10000 -T agent warden status 2>&1)"; then
    printf '%s\n' "$ESTADO"
else
    printf '%s\n' "$ESTADO"
    pare "o 'warden status' não chegou a rodar dentro do container.
Isso não quer dizer que está tudo certo -- quer dizer que não deu para conferir
nada. Veja 'docker compose ps' e 'docker compose logs agent'."
fi

# O que não pode faltar. Quase todas estas linhas já degradaram um clipe em
# silêncio -- a de detecção de rosto entregou um corte com o rosto na borda do
# quadro. libass é a exceção: sem o filtro `ass` o `cut` recusa queimar legenda
# em voz alta, e é por isso que ele está aqui -- para a recusa acontecer nesta
# tela, no install, e não no meio do primeiro corte de alguém.
#
# E cada linha tem de ESTAR PRESENTE, não só "não dizer MISSING". Procurar a
# palavra MISSING e concluir que está tudo bem quando ela não aparece é tomar
# ausência de prova por prova de ausência: uma saída vazia, truncada, ou com os
# rótulos renomeados passava limpa. Agora a linha que não apareceu reprova
# igual à que apareceu faltando.
FALTA=0
for CHAVE in "ffprobe" "ffmpeg" "face detection" "pillow" "style font" "measured style spec" "libass"; do
    SAIDA_CH="$(printf '%s\n' "$ESTADO" | grep -i "^$CHAVE" | head -1)" || SAIDA_CH=""
    if [ "$SAIDA_CH" = "" ]; then
        printf '\n\033[31mnão apareceu:\033[0m %s -- o `warden status` não imprimiu\n' "$CHAVE"
        printf '  essa linha, então ninguém conferiu se está aqui.\n'
        FALTA=1
    elif printf '%s\n' "$SAIDA_CH" | grep -q "MISSING"; then
        printf '\n\033[31mfalta:\033[0m %s\n' "$CHAVE"
        FALTA=1
    fi
done
[ "$FALTA" -eq 0 ] || pare "o agente subiu sem algo de que precisa para cortar.
Isso é um defeito da imagem, não da sua máquina: abra uma issue com a saída acima."

# Credencial nenhuma vem na imagem, e não pode vir: a imagem é pública e um `ENV`
# nela é lido por qualquer um com um pull anônimo -- foi o que aconteceu com o
# par OAuth antigo deste projeto, em 15/09/2026. Então elas entram da máquina de
# quem instala, por `.env`, e a ausência delas é o estado NORMAL.
#
# Um aviso, nunca um erro: sem chave nenhuma o agente corta, legenda e entrega
# igual, com título e descrição prontos.
#
# E são DUAS estradas, que fazem coisas DIFERENTES. A nota antiga citava só
# `warden youtube connect` -- a que NÃO publica, porque o vídeo sobe trancado
# como privado e não há recurso. Quem instalava saía daqui achando que publicar
# sozinho no próprio canal não era possível, e é: por `warden post youtube`,
# medido público em 15/09/2026. Uma linha para cada, dizendo qual é qual.
TEM_POST=0
TEM_YT=0
if [ -f "$RAIZ/.env" ]; then
    if grep -q '^[[:space:]]*WARDEN_POST_API_KEY=.' "$RAIZ/.env" 2>/dev/null; then TEM_POST=1; fi
    if grep -q '^[[:space:]]*WARDEN_YT_CLIENT_ID=.'  "$RAIZ/.env" 2>/dev/null; then TEM_YT=1; fi
fi

printf '\n  \033[33mpublicar no YouTube: são duas estradas, e só UMA publica.\033[0m\n'
if [ "$TEM_POST" -eq 1 ]; then
    printf '  1. `warden post youtube` -- PUBLICA. \033[32mligada\033[0m (achei WARDEN_POST_API_KEY no .env).\n'
else
    printf '  1. `warden post youtube` -- PUBLICA: o vídeo sai PÚBLICO no seu canal.\n'
    printf '     Desligada. Quer ligar? Precisa de WARDEN_POST_API_KEY no .env, que é\n'
    printf '     uma conta sua em upload-post.com (plano grátis, 10 envios por mês,\n'
    printf '     não pede cartão).\n'
fi
if [ "$TEM_YT" -eq 1 ]; then
    printf '  2. `warden youtube connect` -- NÃO publica. ligada (achei WARDEN_YT_CLIENT_ID no .env).\n'
else
    printf '  2. `warden youtube connect` -- NÃO publica: o vídeo sobe TRANCADO como\n'
    printf '     privado e não há como destrancar depois. Serve para conferir o\n'
    printf '     caminho, não para publicar. Precisa de WARDEN_YT_CLIENT_ID e\n'
    printf '     WARDEN_YT_CLIENT_SECRET no .env.\n'
fi
printf '  O .env é um arquivo de texto em %s/.env, ao lado do compose.yml.\n' "$RAIZ"
printf '  O passo a passo das duas está em docs/INSTALL.md, seção\n'
printf '  "Handing a clip to YouTube or TikTok".\n'
printf '  Sem nenhuma das duas o agente corta, legenda e entrega igual, com título e\n'
printf '  descrição prontos para você subir pelo app -- isto aqui é opcional.\n'

diga "pronto"
cat <<'FIM'
  Mande uma mensagem para a linha do agente com um link de campanha, um link de
  vídeo, ou só "oi". Ele responde e conduz a partir daí.

  Ele não vai te entrevistar antes do primeiro clipe. O som sai ORIGINAL por
  padrão, e ele ANUNCIA isso em vez de perguntar -- a linha "som: original" vem
  junto com o clipe. Para sair mudo, e pôr a trilha no app: `--sound platform`.
  Quando a campanha proíbe o áudio da fonte, é a campanha que manda, e a linha
  diz que foi ela.

  Para acompanhar:   docker compose logs -f agent
  Para desinstalar:  ./install.sh --remover
FIM
