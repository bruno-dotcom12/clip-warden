#!/usr/bin/env python3
"""From the campaign's own archive to a clip that already obeys the campaign.

The order here is not an implementation detail, it is the point. Footage comes
only from the links the brief publishes, the cut is chosen on text before any
video is opened, and the render takes its numbers from the rule set rather than
from a house default. A pipeline that picks its own footage or its own duration
produces a file that looks finished and gets the submission thrown out.

  warden fetch <url>                       a page as text, for reading a brief
  warden archive --campaign <id> --text-first   the WORDS: subs, or audio. First.
  warden transcribe <file>                 words with timing, subs if published
  warden digest --source <file>            the transcript a model can afford
  warden archive --campaign <id> --window <a>-<b>   only the seconds chosen
  warden cut <file> --start --end          one clip, inside the campaign's rules

The two downloads are in that order on purpose, and the order is the whole
point: `--text-first` costs 73 KB, `--window` a few MB, the whole file 361 MB
and 195s of transcription on top. Pulling the file first took 12min26s to the
first clip against 62s. `warden archive` with neither flag is the exception.
"""
import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warden_beat
import warden_rules as R
import warden_style as S

# Quanto o corte pode andar para fechar a frase que ele parte ao meio, e o
# menor clipe que vale a pena entregar quando ele anda para trás. Seis segundos
# porque o caso medido em 16/09/2026 pedia 4,9s; acima disso já não é fechar uma
# frase, é entregar outro clipe. Oito porque abaixo disso não há hook (3s) mais
# fala que sustente o corte.
TETO_DA_FRASE_S = 6.0
MIN_DA_FRASE_S = 8.0

TIMEOUT_DOWNLOAD = 900
TIMEOUT_RENDER = 900
TIMEOUT_FETCH = 60
MAX_DIRECT_BYTES = 4 * 1024 * 1024 * 1024      # a source file nobody asked for

# The only two schemes an archive link may use.
#
# Every url here was typed by a stranger into a campaign brief and read out of a
# web page by a model. file:// would copy the host's own files into the archive,
# http to a link-local address is a request to a cloud metadata service, and a
# bare word becomes an option the moment it reaches a command line.
ALLOWED_SCHEMES = ("http", "https")


def _host_is_public(host):
    """False for a name that resolves to this machine or its private network.

    A brief is a stranger's text. `http://169.254.169.254/` is the cloud
    metadata service, `http://127.0.0.1/` and `http://10.x` are whatever else
    runs on the host, and a name under the attacker's own DNS can point at any
    of them. So the host is resolved and every address it resolves to is
    checked; one private answer is enough to refuse. A determined DNS-rebind
    could still differ between this check and urllib's own resolve on connect --
    that is out of a brief's reach and noted rather than closed here.
    """
    if not host:
        return False
    host = host.strip("[]")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # A name that does not resolve is not this machine's secret to leak;
        # let the request proceed and fail as an ordinary unreachable link.
        return True
    for info in infos:
        ip = _desembrulha(ipaddress.ip_address(info[4][0]))
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


# Os dois prefixos IPv6 que CARREGAM um endereço IPv4 dentro de si.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")          # RFC 6052, well-known
_V4MAPEADO = ipaddress.ip_network("::ffff:0:0/96")     # RFC 4291


def _desembrulha(ip):
    """Um IPv6 que embrulha um IPv4 vira o IPv4 que ele embrulha.

    Medido em 15/09/2026, e é um defeito que estava de pé desde sempre: numa
    rede NAT64/DNS64 -- que é o que um iPhone em modo roteador serve, e o que
    toda rede IPv6-only serve -- o resolvedor responde a QUALQUER host só-IPv4
    com um endereço dentro de `64:ff9b::/96`. `vimeo.com` resolvia para
    `64:ff9b::a29f:803d`, o `ipaddress` do Python classifica esse bloco como
    `is_reserved` (ele está no registro de uso especial da IANA), e `safe_url`
    então recusava o link dizendo:

        "that points inside this machine or its private network"

    O que é falso, e é falso da pior maneira possível: uma causa inventada com
    confiança, na voz do arquivo escrito para não inventar causas. Numa rede
    dessas o agente recusaria TODO link só-IPv4 -- a suíte inteira virou
    vermelha ao trocar de rede no meio do dia, o que foi a medição.

    O conserto é ler o que o embrulho carrega. `64:ff9b::7f00:1` vira
    `127.0.0.1` e continua recusado, que é o ponto: a proteção não afrouxa, ela
    passa a olhar o endereço certo. O mesmo vale para `::ffff:a.b.c.d`, a forma
    IPv4-mapeada, que é o caminho clássico de burlar uma checagem assim.
    """
    try:
        if ip.version == 6 and (ip in _NAT64 or ip in _V4MAPEADO):
            return ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
    except Exception:
        pass
    return ip


def safe_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise RuntimeError(
            f"refusing {url!r}: an archive link has to be http or https. "
            "Anything else in a brief is not a link, it is an instruction.")
    if not parsed.netloc:
        raise RuntimeError(f"refusing {url!r}: no host in that link")
    if not _host_is_public(parsed.hostname):
        raise RuntimeError(
            f"refusing {url!r}: that points inside this machine or its private "
            "network, not at published footage. A brief cannot send this agent "
            "to read its own host.")
    return str(url)


# Cabeçalhos que NUNCA atravessam uma troca de host num redirecionamento.
#
# Este agente tem três módulos que falam com API autenticada -- `warden_post`
# (chave do intermediário), `warden_tiktok` e `warden_youtube` (token OAuth) --
# e os três mandavam o segredo por um `urlopen` cru. O urllib monta o pedido
# seguinte de um 302 COPIANDO os cabeçalhos do anterior, tirando só
# `Content-Length` e `Content-Type`: um 302 para outro host levava
# `Authorization` junto, e quem respondesse a esse endereço ficava com a chave.
# Nenhuma versão do Python em que este agente roda pode ser assumida como a que
# corrige isso sozinha, então a regra fica escrita aqui.
_CABECALHOS_DE_SEGREDO = ("authorization", "proxy-authorization", "cookie")


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect the way the first URL was checked.

    urllib already refuses a redirect that changes scheme to file://, but it
    follows one to http://169.254.169.254 without a word. safe_url is the same
    gate the first hop passed, so a redirect that lands on a private host or a
    non-http scheme is refused with the same message rather than followed.

    E o segredo NÃO viaja para outro host. Um endereço público que redireciona
    para outro endereço público passa por `safe_url` sem uma queixa -- é um
    redirecionamento legítimo para qualquer instrumento aqui -- e é exatamente
    a forma de um servidor comprometido, ou de um `WARDEN_POST_BASE_URL`
    hostil, colher a chave de API que ia no cabeçalho. Mesmo esquema e mesmo
    host: o cabeçalho segue, porque é o mesmo servidor a que ele já foi
    mostrado. Qualquer um dos dois diferente -- inclusive um `https` que vira
    `http` no mesmo host, que é o segredo descendo em texto claro -- e o
    cabeçalho fica para trás; quem chamou recebe o 401 do outro lado em vez de
    um vazamento silencioso.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        novo = super().redirect_request(req, fp, code, msg, headers, newurl)
        if novo is None:
            return None
        origem = ((req.type or "").lower(), (req.host or "").lower())
        destino = ((novo.type or "").lower(), (novo.host or "").lower())
        if origem != destino:
            for nome in _CABECALHOS_DE_SEGREDO:
                # As duas grafias: `Request.headers` guarda a chave
                # capitalizada (`Authorization`) e `unredirected_hdrs` a que
                # quem chamou escreveu. Remover só uma delas não remove nada.
                novo.headers.pop(nome.capitalize(), None)
                novo.unredirected_hdrs.pop(nome.capitalize(), None)
                for chave in [k for k in list(novo.headers)
                              if k.lower() == nome]:
                    novo.headers.pop(chave, None)
                for chave in [k for k in list(novo.unredirected_hdrs)
                              if k.lower() == nome]:
                    novo.unredirected_hdrs.pop(chave, None)
        return novo


# One opener for both readers, so the redirect guard cannot be forgotten at a
# call site. Built once; urllib openers are thread-safe for our use.
_OPENER = urllib.request.build_opener(_GuardedRedirect())


def opener_guardado():
    """O opener com o guarda de redirecionamento. A porta para fora, para TODOS.

    Existe como função pública e não como constante importada porque os três
    módulos de publicação são carregados sozinhos nos testes, e porque ler o
    global na hora da chamada é o que deixa a suíte trocar o opener por um
    dublê sem abrir socket nenhum.
    """
    return _OPENER

# The name this agent answers to, on EVERY request the opener makes.
#
# Measured 15/09/2026, and it is a real source failing rather than a courtesy:
# `fetch_text` set a User-Agent, the direct video download did not, and urllib's
# default is `Python-urllib/3.x`. Wikimedia refuses that outright -- the same
# link is 403 through this opener and 200 through curl with any real name -- and
# it is far from alone; a CDN blocking the default Python agent is ordinary.
#
# It reached the owner as `HTTPError: HTTP Error 403: Forbidden`, with no link
# and no cause, on the exact path an archive of "a Google Drive, or any other
# site" depends on. Set on the opener rather than at the call site, because the
# call site is what was forgotten.
_OPENER.addheaders = [
    ("User-Agent", "clip-warden/1.0 (+https://github.com/plow-pbc/plow-agents)")]


def have(binary):
    return shutil.which(binary) is not None


# The escape hatch, and the only one. An install whose address YouTube has
# already flagged cannot be rescued by anything in this file -- that was
# measured, not assumed -- and a cookies file is what yt-dlp itself points at.
# It is never shipped and never the owner's by default: whoever installs this
# agent drops their own file here if they need it.
#
# Duas variáveis, e a nova vem primeiro. `WARDEN_YT_COOKIES` diz de QUAL serviço
# o arquivo é -- o bloqueio medido em 15/09/2026 é do YouTube, e só dele -- e é
# o nome que o dono vai ver escrito na mensagem de erro e no compose. A antiga
# fica porque uma instalação que já a definiu não pode parar de funcionar por
# causa de um nome melhor.
#
# O ARQUIVO NUNCA ENTRA NO REPOSITÓRIO e o conteúdo dele nunca é impresso: um
# cookies.txt do YouTube é a sessão inteira da conta de quem o exportou, e este
# agente é publicado. Só o CAMINHO aparece em mensagem, nunca uma linha de
# dentro. Se isto algum dia precisar depurar, depure o `os.access`, não o texto.
COOKIES_FILE = (os.environ.get("WARDEN_YT_COOKIES")
                or os.environ.get("WARDEN_COOKIES")
                or "/var/lib/hermes/warden/cookies.txt")


def _cookies_usaveis():
    """O arquivo de cookies existe E dá para ler? Um sim ou um não, nunca exceção.

    A pergunta é `isfile` E `access`, não só `isfile`. Medido em 15/09/2026: um
    `--cookies` apontando para arquivo sem permissão de leitura faz o yt-dlp
    abortar ANTES de tentar o link, e a mensagem que chega fala de permissão de
    arquivo no meio de uma frase sobre baixar vídeo. Um arquivo ilegível é o
    mesmo que arquivo ausente para efeito de decisão -- a diferença é que o
    dono precisa saber qual dos dois é, e é isso que `_porque_bloqueou` diz.
    """
    try:
        return os.path.isfile(COOKIES_FILE) and os.access(COOKIES_FILE, os.R_OK)
    except OSError:
        return False

# O gerador de PO token, em modo SCRIPT, dentro desta imagem.
#
# Até 16/09/2026 ele era um SEGUNDO CONTAINER escutando a porta 4416. A nuvem da
# Plow roda um container por pessoa e o contrato dela proíbe um listener de
# entrada, com essas palavras: "no inbound listener". O modo script do mesmo
# projeto emite o token executando um processo Node quando o yt-dlp precisa, e
# não abre porta nenhuma -- o que também tira uma imagem da conta de quem
# instala localmente.
#
# O caminho aponta para o diretório `server/`, não para o .js: é o que o
# `server_home` do plugin espera.
POT_SCRIPT_HOME = os.environ.get("WARDEN_POT_SCRIPT", "/opt/plow/bgutil/server")

# E o servidor HTTP continua atendido, para quem já tem um de pé.
#
# `WARDEN_POT_URL` só entra quando alguém a DECLARA no ambiente. Sem ela, nada
# é perguntado a um socket que não existe -- que era o que acontecia num
# `docker run` sem o compose: o argumento ia junto apontando para
# `127.0.0.1:4416`, onde nunca houve ninguém.
#
# Quando as duas existem, o plugin dá prioridade ao HTTP. É a ordem certa: quem
# subiu um servidor quis usá-lo.
POT_BASE_URL = (os.environ.get("WARDEN_POT_URL") or "").strip()

# yt-dlp's own wiki puts a logged-out session at roughly a thousand player and
# webpage requests an hour, and names bursts as what gets an address flagged.
# These are seconds between requests, not a preference.
SLEEP_REQUESTS = os.environ.get("WARDEN_SLEEP_REQUESTS", "1.5")
# `--sleep-interval`/`--max-sleep-interval` dormem ANTES de cada download, e o
# yt-dlp sorteia uniformemente entre os dois: 1 e 5 davam 3,0s de média. Pior,
# `YoutubeDL.py` itera formatos × seções, então `--windows` com três janelas
# pagava ~9s de `time.sleep` puro.
#
# Baixado para 0,5-1 (média 0,75s) em 15/09/2026, e NÃO para zero. O que marca
# um endereço, pela wiki do yt-dlp, é rajada de REQUISIÇÃO ao innertube -- que
# é o que `--sleep-requests` cobre e continua em 1,5s. Um sono antes de puxar
# bytes de um CDN é outra coisa, e 3s dele por janela era preço sem prova.
# Ainda assim não é zero: este agente é publicado e o endereço queimado é o de
# quem instalou, então a margem fica do lado caro.
SLEEP_INTERVAL = os.environ.get("WARDEN_SLEEP_INTERVAL", "0.5")
SLEEP_MAX = os.environ.get("WARDEN_SLEEP_MAX", "1")


def _js_runtimes():
    """The JS engines yt-dlp may use here, best first, or None if there are none.

    deno leads because it is the only one yt-dlp enables on its own; node is
    named because this image already carries it and a host that has only node
    should still work.
    """
    achados = [n for n in ("deno", "node", "bun", "qjs") if have(n)]
    return ",".join(achados) or None


def _gdown():
    """Como invocar o gdown aqui, pela mesma razão que `_ytdlp` existe.

    Medido em 15/09/2026: `have("gdown")` perguntava pelo BINÁRIO no PATH. O
    venv da imagem instala o gdown em `/opt/hermes/.venv/bin/gdown`, que **não**
    está no PATH que um subprocesso herda da árvore de supervisão -- então TODO
    link do Google Drive morria dizendo "this is a Google Drive link and gdown
    is not installed", com o gdown instalado e importável.

    É a mesma armadilha que `_ytdlp` já resolvia, no arquivo que já a descrevia,
    deixada de pé no caminho ao lado. O Drive é o acervo que mais aparece depois
    do YouTube, e desde que o YouTube passou a recusar este endereço ele é o
    caminho principal -- o defeito ficou invisível porque ninguém tinha chegado
    nele.
    """
    # O BINÁRIO ao lado deste interpretador, não `-m gdown`.
    #
    # Medido em 15/09/2026, e é um conserto em cima de um conserto: trocar o
    # PATH por `-m gdown` resolvia o "não está instalado" e quebrava tudo em
    # seguida, porque `python -m gdown` expõe uma CLI DIFERENTE da do script --
    # ela não aceita `--fuzzy`, e o erro que chegava era o usage do argparse.
    # O bin do venv fica ao lado do executável que está rodando, então é daí
    # que se pega, sem depender do PATH e sem trocar de CLI.
    lado = os.path.join(os.path.dirname(sys.executable), "gdown")
    if os.path.isfile(lado) and os.access(lado, os.X_OK):
        return [lado]
    return ["gdown"]


def _tem_gdown():
    """Dá para usar o gdown? A pergunta é se dá para IMPORTAR, não se está no PATH."""
    lado = os.path.join(os.path.dirname(sys.executable), "gdown")
    if os.path.isfile(lado) and os.access(lado, os.X_OK):
        return True
    return have("gdown")


def _ytdlp(*, paced=True):
    """How to invoke yt-dlp here, with the three things every call must carry.

    The invocation itself: a skill command runs under this interpreter, but a
    subprocess inherits the supervision tree's PATH, which need not hold the
    venv's bin. Calling it as a module of this interpreter sidesteps that, and
    falls back to the binary for a dev machine that installed yt-dlp on its own.

    The three additions are a fix, not a style, and each was measured on
    15/09/2026 after the agent told the owner twice -- on two different days,
    with two different and contradictory stories -- that YouTube was blocking
    "this specific video" and then "this server's IP". Both were invented.

    1. A JS RUNTIME. `yt-dlp -v` printed `JS runtimes: none` while node 26.5.1
       sat in /usr/local/bin, because yt-dlp enables only deno by itself. Its
       README for this version: "By default, `visionos,web` is used. If no
       JavaScript runtime/engine is available, then `web` is omitted." So every
       install so far has run with the web client silently dropped and no n/sig
       deciphering -- a permanent, invisible handicap on every link.

    2. A PO TOKEN PROVIDER. It does NOT rescue an address YouTube has already
       flagged: measured here with a freshly minted token bound to matching
       visitor data, in the player context, and the refusal was identical. It
       is carried because it is what keeps an install from being flagged in the
       FIRST place, and this agent is published -- the address it burns belongs
       to a stranger.

    3. A PACE. This one was ours. A single link used to cost FOUR separate
       extractions -- channel lookup, title lookup, subtitle pass, download --
       fired back to back with no sleep at all. See `_facts`, which turned the
       first two into one, and these sleep options, which space the rest.
    """
    try:
        import yt_dlp  # noqa: F401
        base = [sys.executable, "-m", "yt_dlp"]
    except Exception:
        base = ["yt-dlp"]

    if rt := _js_runtimes():
        base += ["--js-runtimes", rt]
    if _cookies_usaveis():
        base += ["--cookies", COOKIES_FILE]
    if os.path.isdir(POT_SCRIPT_HOME):
        base += ["--extractor-args",
                 f"youtubepot-bgutilscript:server_home={POT_SCRIPT_HOME}"]
    if POT_BASE_URL:
        base += ["--extractor-args", f"youtubepot-bgutilhttp:base_url={POT_BASE_URL}"]
    # Retries are bounded on purpose. yt-dlp's default is ten, and ten retries
    # against an address that is being refused is how a flagged address stays
    # flagged: it reads as exactly the burst the wiki warns about.
    base += ["--retries", "3", "--extractor-retries", "2"]
    # Sem isto, uma conexão que trava fica sentada até o teto de 900s antes de
    # alguém saber. 20s é folgado para um CDN do Google e curto o bastante para
    # a falha aparecer enquanto a pessoa ainda está esperando.
    base += ["--socket-timeout", "20"]
    if paced:
        base += ["--sleep-requests", SLEEP_REQUESTS,
                 "--sleep-interval", SLEEP_INTERVAL,
                 "--max-sleep-interval", SLEEP_MAX]
    return base


# The logged-out bot check, in the words yt-dlp actually prints. Matched so the
# tool can say what is true instead of handing the model a stack of English to
# improvise a cause from -- which is precisely what happened, twice.
#
# The two regexes are separate because they are OPPOSITE claims, and a first
# draft of this file collapsed them and so told the same lie it was written to
# stop. `Sign in to confirm you're not a bot` is about this ADDRESS and every
# link gets it. `Sign in to confirm your age` and `Private video` are about
# THIS VIDEO and no other link gets them -- and they open with the same four
# words, so a regex on `sign in to confirm` alone matches both and then
# announces, in the address message, "not this video: every link gets the same
# refusal". That sentence would be false exactly when it mattered.
_BOT_CHECK = re.compile(r"not a bot|HTTP Error 429|Too Many Requests", re.I)
_ESTE_VIDEO = re.compile(
    r"confirm your age|age[- ]restricted|private video|members[- ]only|"
    r"video is unavailable|removed by the uploader", re.I)


class FonteBloqueada(RuntimeError):
    """A refusal that is about this outgoing address, not about this link.

    It subclasses RuntimeError on purpose: every caller in this file already
    catches RuntimeError, so nothing has to learn a new exception to keep
    working -- but the paths that must NOT flatten this into "no result" can
    now tell it apart. `_facts` is the one that must not, and did.
    """


def _porque_bloqueou(label):
    """The honest reading of a bot-check refusal, with the agent's script.

    Every sentence here is a measurement, and the last paragraph exists because
    the failure mode of this error is not the download -- it is the agent
    inventing a cause the owner cannot check.
    """
    rt = _js_runtimes() or "none"
    pot = "reachable" if _pot_alive() else "NOT reachable"
    # Três estados, não dois. "existe mas não dá para ler" é um conserto de uma
    # linha do lado do dono e virava "absent", que manda ele exportar de novo um
    # arquivo que já está lá.
    if _cookies_usaveis():
        cook = "present"
    elif os.path.isfile(COOKIES_FILE):
        cook = "present but NOT readable by this process"
    else:
        cook = "absent"
    # A CAUSA MEDIDA, numa frase, antes de qualquer parágrafo.
    #
    # Medido em 15/09/2026: a explicação honesta tem cinco parágrafos, e cinco
    # parágrafos é o que o modelo resume -- e resumir foi exatamente como as
    # causas inventadas nasceram. Então a primeira linha depois do cabeçalho diz
    # a coisa toda sozinha, inclusive se havia cookies configurados, porque essa
    # é a única variável que o dono controla e a resposta muda com ela: sem
    # arquivo, o conserto é exportar um; com arquivo, o conserto é outra rede.
    if _cookies_usaveis():
        frase = (f"In one sentence: YouTube is demanding a login to download "
                 f"from this address (bot check), and the cookies file "
                 f"configured at {COOKIES_FILE} did not clear it.")
    elif os.path.isfile(COOKIES_FILE):
        frase = (f"In one sentence: YouTube is demanding a login to download "
                 f"from this address (bot check), and the cookies file at "
                 f"{COOKIES_FILE} exists but this process cannot read it, so it "
                 f"was never sent.")
    else:
        frase = (f"In one sentence: YouTube is demanding a login to download "
                 f"from this address (bot check), and no cookies file is "
                 f"configured (WARDEN_YT_COOKIES, looked for at "
                 f"{COOKIES_FILE}).")
    return (
        f"{label}: YouTube refused this from this machine's outgoing address "
        f"with its logged-out bot check.\n"
        f"\n"
        f"  {frase}\n"
        f"\n"
        f"  What this is NOT, and you may not say otherwise:\n"
        f"    - not this video: every link gets the same refusal\n"
        f"    - not the source's authorisation: that gate already passed\n"
        f"    - not something that clears up on its own in a few minutes\n"
        f"\n"
        f"  What IS measured, all on 15/09/2026, same container, no cookies:\n"
        f"    - morning: the home address was refused with this bot check\n"
        f"    - 15:33, AS26599 Telefonica (189.98.253.32): title read,\n"
        f"      published subtitle pulled, window downloaded, 830 KiB in 6s\n"
        f"    - 19:00, AS17222 Mundivox (67.159.227.250), the home provider:\n"
        f"      title read, window downloaded, 830 KiB in 7s\n"
        f"  What those three lines measure, exactly and no more: the same\n"
        f"  container downloaded this footage over TWO different outgoing\n"
        f"  addresses, at two later times of day, after being refused in the\n"
        f"  morning. One of the two was the HOME provider -- the same one that\n"
        f"  was refused that morning.\n"
        f"\n"
        f"  What is NOT measured, and you may not pick a side: WHY any of it\n"
        f"  happened -- nobody wrote\n"
        f"  down the IP that was refused in the morning, and a residential IP\n"
        f"  changes on its own -- a modem reconnect, a new lease. So the\n"
        f"  evening success could mean the flag was lifted, or it could mean\n"
        f"  the address simply changed. BOTH explanations fit these numbers\n"
        f"  exactly, which is why neither may be stated. Do not say this\n"
        f"  clears up on its own, and do not say it is permanent -- and do not\n"
        f"  read the home address working at 19:00 as evidence FOR option 1\n"
        f"  below: if that address never changed, that line is evidence\n"
        f"  AGAINST it. It is the same ambiguity a second time, not a second\n"
        f"  measurement.\n"
        f"\n"
        f"  So option 1 is not a promise from yt-dlp's documentation, and it is\n"
        f"  not a proven cause either: it is a thing that was tried here and\n"
        f"  came back with the video, on an address that was not the one\n"
        f"  refused in the morning. That is the whole claim.\n"
        f"\n"
        f"  The one rule that survives all of it, and the only actionable\n"
        f"  one: the refusal travels with the outgoing address, so the only\n"
        f"  lever anyone here has is to change it -- and what gets them a clip\n"
        f"  today is the next line, not that lever.\n"
        f"\n"
        f"  What gets them a clip TODAY: ask for the file itself, or a Google\n"
        f"  Drive link. Neither goes anywhere near this refusal.\n"
        f"\n"
        f"  What is already in place, so do not offer it as the fix:\n"
        f"    - JS runtime: {rt}\n"
        f"    - PO token provider: {pot}\n"
        f"    - requests are paced, and retries are capped\n"
        f"    - cookies file ({COOKIES_FILE}): {cook}\n"
        f"\n"
        f"  Two things change this answer, and the agent can do neither alone:\n"
        f"    1. a different outgoing address -- another network, a phone\n"
        f"       hotspot, or a VPN. On 15/09/2026 a download came back over an\n"
        f"       address that was not the refused one; whether the address is\n"
        f"       what changed the answer is NOT measured. See above.\n"
        f"    2. a cookies file from a signed-in YouTube session at\n"
        f"       {COOKIES_FILE}, or anywhere else with WARDEN_YT_COOKIES\n"
        f"       pointing at it. yt-dlp warns this can get that account\n"
        f"       blocked, so it should be an account nobody minds losing.\n"
        f"       The file stays on the owner's machine: it is never committed\n"
        f"       and its contents are never printed, here or anywhere.\n"
        f"\n"
        f"  Tell the owner the address is refused and give them those two "
        f"options. Do not guess at a cause, do not blame the video, and do not "
        f"promise it will work later.")


def _pot_alive():
    """Há quem emita PO token? Um sim ou um não, nunca uma exceção.

    Duas respostas possíveis e as duas contam: o gerador em modo script nesta
    imagem, que é o caminho desde 16/09/2026, e um servidor HTTP que alguém
    tenha declarado em `WARDEN_POT_URL`.

    O script não é "perguntado": ou o diretório está lá com o `generate_once.js`
    dentro, ou não está. Executá-lo aqui só para conferir custaria um processo
    Node e até 20s -- e quem confere de verdade é o build, que não deixa a
    imagem sair sem uma emissão real.
    """
    try:
        if os.path.isfile(os.path.join(POT_SCRIPT_HOME, "build",
                                       "generate_once.js")):
            return True
    except OSError:
        pass
    if not POT_BASE_URL:
        return False
    try:
        with urllib.request.urlopen(POT_BASE_URL + "/ping", timeout=3):
            return True
    except Exception:
        return False


def run(args, timeout, label):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Raised as our own error on purpose: every caller catches RuntimeError,
        # and a timeout that escapes as itself reaches the owner as a traceback.
        raise RuntimeError(f"{label} gave up after {timeout}s")
    if done.returncode != 0:
        saida = (done.stderr or done.stdout or "")
        # The bot check is answered here, at the one place every yt-dlp call
        # passes through, rather than at each call site -- there are nine, and
        # the one that reached the owner was whichever ran first.
        #
        # Scoped to yt-dlp by label, because `run` also carries ffmpeg and
        # gdown: a 429 from Google Drive would otherwise be answered with a
        # paragraph about YouTube refusing this address, which is the same
        # crime -- a confident cause that was never observed -- committed by
        # the code meant to prevent it.
        # Por LINHA, e não sobre o buffer inteiro. Medido em 15/09: numa
        # playlist o yt-dlp imprime um item por linha, então "Private video"
        # do item 1 e "not a bot" do item 2 chegam juntos -- e a checagem sobre
        # o texto todo via as duas, concluía "é sobre o vídeo" e devolvia ao
        # modelo a pilha de inglês que este arquivo existe para não devolver.
        # A pergunta certa é se ALGUMA linha é bloqueio e não é sobre o vídeo.
        linhas_bloqueio = [l for l in saida.splitlines()
                           if _BOT_CHECK.search(l) and not _ESTE_VIDEO.search(l)]
        if label.startswith("yt-dlp") and linhas_bloqueio:
            erro = FonteBloqueada(_porque_bloqueou(label))
            # 429 e "not a bot" chegam pelo mesmo `_BOT_CHECK` e NÃO são a mesma
            # coisa, e quem precisa distingui-los é a corrida da legenda.
            #
            # Medido em 15/09/2026, 15:33 BRT: pedir `--sub-langs "pt.*"` fez o
            # yt-dlp escrever DUAS variantes e levar 429 na terceira. Isso não é
            # o endereço recusado -- é o endereço dizendo "devagar", com a
            # legenda já em disco. Tratar como bloqueio derrubava o `archive`
            # inteiro com um parágrafo sobre o YouTube recusar a máquina, tendo
            # a legenda ali do lado.
            #
            # A marca vai no OBJETO porque a mensagem é `_porque_bloqueou`, que
            # não carrega a saída original: sem isto, quem pega a exceção não
            # tem como perguntar qual dos dois foi.
            erro.apenas_429 = not any(re.search(r"not a bot", l, re.I)
                                      for l in linhas_bloqueio)
            raise erro
        tail = saida.strip().splitlines()[-6:]
        raise RuntimeError(f"{label} failed:\n  " + "\n  ".join(tail))
    return done.stdout


# ---------------------------------------------------------------- reading

def fetch_text(url, limit=200_000):
    """A web page as readable text.

    The brief is usually a page, and the model that fills the rule set has to
    read it. Scripts and styles go, tags go, entities are decoded; what is left
    is what a person would have read. Truncated, because a rule set is extracted
    from the top of a brief and an unbounded page is an unbounded prompt.
    """
    url = safe_url(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": "clip-warden/1.0 (+https://github.com/plow-pbc/plow-agents)"})
    with _OPENER.open(request, timeout=60) as response:
        raw = response.read(limit * 4)
        # O charset que a resposta declara, não um palpite. Uma página latin-1
        # decodificada como utf-8 vira mojibake, e daqui saem hashtag
        # obrigatória e termo banido: uma banida corrompida nunca casa, e a
        # palavra proibida passa na verificação.
        charset = response.headers.get_content_charset()
    if not charset:
        head = raw[:4096].decode("ascii", errors="ignore").lower()
        m = re.search(r'charset=["\']?([\w-]+)', head)
        charset = m.group(1) if m else "utf-8"
    try:
        body = raw.decode(charset, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    # A cauda que o corte deixou aberta. `read(limit * 4)` pode cair NO MEIO de
    # um <script>, e sem a tag de fecho o padrão acima não casa: o bloco inteiro
    # sobrevive. Medido em 15/09 numa página de campanhas -- 150.181 caracteres
    # de CSS do tailwind chegaram como se fossem o briefing, no primeiro comando
    # que um estranho roda.
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>(?:(?!</\1>).)*$", " ", body)
    body = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n\s*", "\n\n", body).strip()
    return body[:limit]


# ---------------------------------------------------------------- finding

# Where open campaigns are listed in public. Fetched as text and handed to the
# model to read, rather than parsed here: these are marketing pages that get
# redesigned, and a scraper that silently stops matching is worse than no
# scraper, because it reports "no campaigns" instead of "I could not read this".
#
# Editable without touching the image: WARDEN_DIRECTORIES takes a comma
# separated list and replaces this one.
DIRECTORIES = [
    ("clipmap", "https://clipmap.gg/"),
    ("whop", "https://whop.com/discover/content-rewards/"),
    ("clipradar", "https://clipradar.co/"),
    ("realoficial", "https://realoficial.com.br/"),
]


def directories():
    custom = os.environ.get("WARDEN_DIRECTORIES")
    if not custom:
        return DIRECTORIES
    out = []
    for item in custom.split(","):
        item = item.strip()
        if item:
            out.append((urlparse(item).netloc or item, item))
    return out


def discover(limit_chars=12_000):
    """Every listing page, as text, with whatever failed named out loud.

    The agent reads these and tells the owner what is open and what it pays.
    Nothing here decides which campaign is good: that depends on what the owner
    can actually make, and a ranking invented by a downloader would be noise
    wearing the clothes of advice.
    """
    pages, failed = [], []
    for name, url in directories():
        try:
            pages.append((name, url, fetch_text(url, limit=limit_chars)))
        except Exception as exc:
            failed.append((name, url, f"{type(exc).__name__}"))
    return pages, failed


# ---------------------------------------------------------------- authorising

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(url):
    """The YouTube video id in a link, or None. Handles the forms a brief uses.

    watch?v=, youtu.be/, /shorts/, /embed/, and a bare 11-character id. The id
    is what a playlist lists and what a watch link carries, so it is the thing
    two links are the same video by.
    """
    raw = str(url or "").strip()
    if _YT_ID.match(raw):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    # `"youtube" in host` era o teste, e ele dizia sim para
    # `youtube.com.evil.example` e para `myyoutube.example`. Como `authorize`
    # decide pelo ID, bastava ao atacante pôr um id autorizado numa URL do
    # host dele para o agente baixar de lá e reportar "autorizado". O host é
    # comparado inteiro agora, normalizado pela mesma função que o resto do
    # arquivo já usava e que este caminho não usava.
    if _norm_host(host) == "youtube.com" and "youtu.be" in host:
        cand = parsed.path.lstrip("/").split("/")[0]
        return cand if _YT_ID.match(cand) else None
    if _norm_host(host) in ("youtube.com", "youtube-nocookie.com"):
        from urllib.parse import parse_qs
        v = parse_qs(parsed.query or "").get("v", [None])[0]
        if v and _YT_ID.match(v):
            return v
        for seg in ("shorts/", "embed/", "live/"):
            if seg in parsed.path:
                cand = parsed.path.split(seg, 1)[1].split("/")[0]
                return cand if _YT_ID.match(cand) else None
    return None


def _is_playlist(url):
    parsed = urlparse(str(url or ""))
    q = parsed.query or ""
    return (parsed.path.rstrip("/").endswith("/playlist")
            or ("list=" in q and "v=" not in q))


# As abas de um canal. Nenhuma delas é um vídeo: são listas, e o yt-dlp trata
# cada uma como playlist. `/shorts/<id>` e `/live/<id>` são vídeos e NÃO caem
# aqui, porque o que termina o caminho deles é o id e não a aba.
_ABAS_DE_CANAL = ("/videos", "/shorts", "/streams", "/live", "/featured",
                  "/playlists", "/podcasts", "/releases")


def _e_colecao(url):
    """O link aponta para uma LISTA de vídeos em vez de um vídeo?

    Playlist, canal, ou uma aba de canal. A pergunta existe porque o yt-dlp
    responde a um link desses baixando TUDO o que está atrás dele, e o nome de
    saída deste projeto não tem índice nenhum: um canal varrido por cima de um
    template só escreve um arquivo em cima do outro e devolve o último que
    sobrou, que não é um vídeo que alguém escolheu.
    """
    if _is_playlist(url):
        return True
    parsed = urlparse(str(url or ""))
    if _norm_host(parsed.netloc or "") not in ("youtube.com",
                                               "youtube-nocookie.com"):
        return False
    caminho = (parsed.path or "").rstrip("/")
    if caminho.startswith(("/@", "/c/", "/channel/", "/user/")):
        # `/@canal/videos` e `/@canal` são os dois uma lista; `/@canal` sozinho
        # é a aba inicial, que o yt-dlp também expande.
        return True
    return caminho.endswith(_ABAS_DE_CANAL)


def _playlist_args(url):
    """Os argumentos de playlist do yt-dlp para este link. UMA resolução, uma só.

    ESTE É O CONSERTO DE UM DEFEITO SILENCIOSO, e ele não tem sintoma: o clipe
    sai de outro vídeo e parece certo.

    Antes daqui havia duas resoluções. O `archive` -- o preparo, que baixa a
    fonte e é quem o dono vê escolher -- separava playlist de vídeo e pedia
    `--yes-playlist --playlist-items 1` numa playlist. O `archive_window` e o
    `archive_windows` -- o render, que baixa só a janela que vira o corte --
    fixavam `--no-playlist`, sempre. `--no-playlist` num link que é playlist e
    não vídeo não é "pegue o primeiro": é indefinido, e o yt-dlp resolve
    expandindo a lista inteira por cima do MESMO template de saída, que não
    carrega índice. O arquivo que sobra é o último que terminou de escrever.
    Preparo e render podiam escolher vídeos diferentes do mesmo link, e nada em
    lugar nenhum diria isso -- o corte sai com duração certa, hook certo,
    legenda casada pela ficha `.origem.json`, e a imagem de outro vídeo.

    Um link de canal era pior ainda: nenhum dos dois caminhos o reconhecia, e
    os dois varriam o canal inteiro.

    Agora a pergunta é feita uma vez e respondida igual nos dois lados. O link
    que nomeia um vídeo continua com `--no-playlist`, exatamente como antes. O
    link que nomeia uma coleção pega o item 1 dela, nos dois caminhos: item 1
    do preparo é item 1 do render, porque é a mesma conta sobre a mesma URL.
    """
    if video_id(url):
        # O link NOMEIA um vídeo (`watch?v=`, `youtu.be/`, `/shorts/<id>`),
        # mesmo quando traz um `&list=` junto. `--no-playlist` é literalmente a
        # opção para este caso, e é o que os dois caminhos já faziam.
        return ["--no-playlist"]
    if _e_colecao(url):
        return ["--yes-playlist", "--playlist-items", "1"]
    return ["--no-playlist"]


def playlist_video_ids(url):
    """Every video id in a playlist, as a set, read without downloading a byte.

    yt-dlp's flat listing is what makes 'is this video in the authorised
    playlist?' a question the tool answers instead of the model guessing.
    """
    out = run(_ytdlp(paced=False) + ["--flat-playlist", "--no-warnings",
               "--print", "%(id)s", "--", safe_url(url)],
              TIMEOUT_DOWNLOAD, "yt-dlp playlist listing")
    return {line.strip() for line in out.splitlines() if _YT_ID.match(line.strip())}


# One link, one metadata extraction, remembered for the length of the command.
#
# This dictionary is the whole of fix 3 from `_ytdlp`. `video_title` and
# `_channel_facts` each ran a full extraction, and a single trusted link calls
# both -- then the subtitle pass and the download make four. Four logged-out
# extractions per link, back to back, against a wiki that says bursts are what
# get an address flagged. They ask for different fields of the same response,
# so they are now one request, printed tab separated and kept.
_FACTS = {}
#
# `language` entrou em 15/09/2026 e é o campo mais barato deste arquivo: ele já
# vem na MESMA resposta do `--print` que traz título e canal, então descobrir a
# língua original do vídeo custa zero requisição a mais quando `_facts` já rodou
# -- e uma só, cacheada, quando não rodou. Ver `_sub_langs_para`.
_FACTS_CAMPOS = ("title", "channel_id", "uploader_id", "channel_url",
                 "uploader_url", "language")


def _facts(url):
    """Title and channel of a link in ONE extraction, or {} if it gives none."""
    url = safe_url(url)
    if url in _FACTS:
        return _FACTS[url]
    try:
        # `paced=False`: `--print` implica `--simulate`, então esta chamada não
        # baixa byte nenhum e o sono de download é espera pura. O
        # `--sleep-requests` continua valendo, que é o que protege o endereço.
        # A MESMA resolução que o download vai usar, e limitada a um item de
        # qualquer jeito: o título e o canal que o fiscal confere têm de ser os
        # do vídeo que vai descer, não os de outro item da mesma lista.
        quais = _playlist_args(url)
        if "--playlist-items" not in quais:
            quais = quais + ["--playlist-items", "1"]
        out = run(_ytdlp(paced=False) + ["--no-warnings", *quais,
                   "--print", "\t".join("%%(%s)s" % c for c in _FACTS_CAMPOS),
                   "--", url],
                  TIMEOUT_DOWNLOAD, "yt-dlp metadata lookup")
    except FonteBloqueada:
        # Propagated, never flattened. Flattening it here is what made the very
        # first version of this fix repeat the original bug with the sign
        # reversed: `_channel_facts` saw an empty dict and announced that the
        # address was refused -- for a deleted video, a timeout, a DNS failure,
        # anything. A tool that states a cause it did not observe is the defect
        # this whole file is about.
        raise
    except Exception:
        # NOT cached: a refusal is a state of the network, not a fact about the
        # link, and remembering it would outlive the reason for it.
        return {}
    linha = (out.strip().splitlines() or [""])[0]
    partes = linha.split("\t")
    achado = {c: (partes[i].strip() if i < len(partes) else "")
              for i, c in enumerate(_FACTS_CAMPOS)}
    _FACTS[url] = achado
    return achado


def video_title(url):
    """The title of a video, read off the link, or None if it will not give one.

    So the agent can name what it is asking about: 'the video "..." is outside
    the archive -- cut it anyway?' is a question the owner can answer; 'that link'
    is not.

    A blocked address gives None here, like any other unreadable link, and the
    first draft of this fix had it raise instead. That was wrong, and measured
    wrong: `authorize` calls this on its first line, so the refusal escaped to
    `cmd_authorize`, which exits 1 -- and exit 1 is the documented signal for
    "NOT in the campaign's archive" (README, warden-shared/SKILL.md). A network
    refusal was being reported as a verdict about the archive, and the persona
    then asks the owner for permission to cut outside it. The same invented
    cause, one layer up.

    Whether a link is authorised is decided LOCALLY, by matching ids against the
    campaign's own list; the title is how the agent names the video, not how it
    decides. So a title that cannot be read costs a name, not a verdict, and the
    refusal is reported where it is unambiguous -- the download.
    """
    try:
        achado = _facts(url)
    except FonteBloqueada:
        return None
    titulo = (achado.get("title") or "").strip()
    return titulo if titulo and titulo != "NA" else None


def authorize(rules, url):
    """Is this link in the campaign's archive? A yes or no, the reason, the title.

    A clipper's whole risk is footage the brief did not publish, so whether a
    link is authorised must be decided, not reasoned: a playlist is expanded and
    the id looked for, a direct link is matched by id. Returns (ok, reason,
    title) -- the title so a caller can name the video it is asking about. A
    playlist that cannot be listed is reported as unknown, never as authorised --
    a maybe is a no when someone's unpaid work is on the line.
    """
    url = safe_url(url)
    title = video_title(url)
    urls = R.get(rules, "sources.archive_urls", [])
    if not urls:
        return False, "this campaign publishes no archive, so nothing is authorised", title
    vid = video_id(url)
    unlisted = []
    for allowed in urls:
        if _is_playlist(allowed):
            try:
                ids = playlist_video_ids(allowed)
            except Exception as exc:
                unlisted.append(f"{allowed} ({type(exc).__name__})")
                continue
            if vid and vid in ids:
                return True, f"in the authorised playlist {allowed}", title
        else:
            other = video_id(allowed)
            if vid and other and vid == other:
                return True, f"the authorised link {allowed}", title
            if str(allowed).strip() == url:
                return True, f"the authorised link {allowed}", title
    if unlisted:
        return False, ("not found in the archive, and these playlists could not "
                       "be read to be sure: " + "; ".join(unlisted)), title
    return False, "not in any authorised playlist or direct link of this campaign", title


# ---------------------------------------------------------------- trusted sources

def _norm_host(host):
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in ("youtu.be", "m.youtube.com", "music.youtube.com"):
        host = "youtube.com"
    return host


def _is_channel_entry(entry):
    e = str(entry or "").strip()
    return (e.startswith("@") or e.startswith("UC")
            or "/@" in e or "/channel/" in e or "/c/" in e or "/user/" in e)


def _channel_facts(url):
    """The channel a video belongs to, read without downloading it.

    yt-dlp prints the uploader handle and the channel id, which is what a
    trusted-channel entry is matched against -- so 'is this from a channel I
    trust?' is answered, not assumed.
    """
    achado = _facts(url)
    if not achado:
        # The lookup is what decides whether a link is trusted, so a failure
        # here must not read as "not trusted": an empty list would match no
        # trusted entry and the owner would be told their own channel is not
        # vouched for. It raises instead -- and it raises the HONEST thing,
        # which is that the lookup did not answer. It does NOT say the address
        # was refused: if that were true, `run` would already have raised
        # FonteBloqueada and this line would never be reached.
        raise RuntimeError(
            "could not read the channel behind that link, so whether it is a "
            "trusted source is unknown -- and unknown is not the same as no. "
            "Say that you could not check, and do not rule the link out.")
    return [achado[c].lower() for c in
            ("channel_id", "uploader_id", "channel_url", "uploader_url")
            if achado.get(c) and achado[c] != "NA"]


def trusted_check(url, entries):
    """Is this link from a source the owner trusts? A yes or no with the reason.

    A domain entry matches the link's host with no network. A channel entry --
    an @handle or a UC… id -- is matched against the video's real channel, read
    off the link. Empty list means nothing is trusted yet, which is a no.
    """
    url = safe_url(url)
    entries = [str(e).strip() for e in (entries or []) if str(e).strip()]
    if not entries:
        return False, "no trusted sources are set; add one with `warden trusted add`"
    host = _norm_host(urlparse(url).netloc)
    for entry in entries:
        if _is_channel_entry(entry):
            continue
        dom = _norm_host(entry)
        if host == dom or host.endswith("." + dom):
            return True, f"from the trusted domain {entry}"
    channels = [e for e in entries if _is_channel_entry(e)]
    if channels and "youtube" in host:
        try:
            facts = _channel_facts(url)
        except FonteBloqueada:
            # Sobe inteira. Achatada aqui, ela virava `ok=False`, e `cmd_trusted`
            # imprimia "NOT trusted: ... FonteBloqueada" e saía 1 -- então o
            # agente dizia ao dono que o canal DELE não é de confiança e se
            # oferecia para adicioná-lo, por causa de uma recusa de rede.
            # Um veredito inventado sobre a fonte é o defeito que este arquivo
            # inteiro existe para impedir; produzi-lo aqui seria o mesmo erro
            # com outra fantasia.
            raise
        except Exception as exc:
            return False, ("could not read the video's channel to check it against "
                           f"your trusted list: {type(exc).__name__}")
        for entry in channels:
            token = entry.lower().rstrip("/").split("/")[-1]  # @handle or UC… id
            if token and any(token == f or token == "@" + f or "@" + token == f
                             or token in f.split("/") for f in facts):
                return True, f"from the trusted channel {entry}"
    return False, "not from any source in your trusted list"


# ---------------------------------------------------------------- footage

def fiscal(url, rules=None, trusted=None):
    """O portão do download, nas DUAS formas que existem. (ok, porque, titulo).

    Com campanha, o acervo decide. Sem campanha, a lista de fontes do dono
    decide. Não há terceira forma e não há caminho de download que não passe por
    uma das duas -- e é por isso que a pergunta mora aqui, numa função só, em
    vez de cada chamador escolher o seu portão.

    Medido em 15/09, e é o motivo de esta função existir: o caminho barato
    (`--text-first`, `--window`) exigia `--campaign`, então quem mandava um link
    solto -- que é o caso comum -- não tinha caminho barato nenhum. O agente
    baixava o vídeo inteiro porque era a única porta aberta, e pagava 3min46s de
    transcrição por um texto que a legenda publicada entrega em 5s. Abrir o
    caminho barato para link confiável sem abrir uma porta lateral significa
    exatamente isto: o portão continua existindo, só muda quem responde.
    """
    if trusted is not None:
        # Decisão do dono, 15/09/2026: quem manda o link já afirmou que pode
        # usar o material. O agente nunca pede licença.
        #
        # `safe_url` FICA, e fica antes de qualquer sim. A decisão é sobre
        # LICENÇA, não sobre segurança: um link que aponta para 127.0.0.1, para
        # a rede privada da máquina ou para um esquema que não é http continua
        # recusado, porque isso nunca foi uma pergunta sobre direitos autorais.
        #
        # E isto apaga uma chamada de REDE, não só uma pergunta. O caminho
        # antigo passava por `trusted_check`, que para um link do YouTube com
        # entrada de canal chamava `_channel_facts` -> `_facts` -> yt-dlp. Ou
        # seja: o portão de licença sofria o mesmo bloqueio de bot que o
        # download, e um link perfeitamente autorizado era recusado -- ou virava
        # "não consegui checar" -- por uma recusa de rede que nada tinha a ver
        # com permissão. Um link solto agora custa zero requisição até o
        # download de verdade começar.
        safe_url(url)
        return True, "sent by the person in this conversation", None
    return authorize(rules, url)


def baixa_trilha(url, out_dir):
    """Baixa o ÁUDIO de um link que o dono mandou, para a pasta de trilhas dele.

    Isto não é o acervo e não passa pelo fiscal de acervo, de propósito: uma
    trilha não é material de clipe. O que a governa é a escolha do dono, e a
    recusa que este comando dava -- medida em 15/09, duas vezes, com uma aula de
    direitos autorais em cima -- custou uma ida e volta e não protegeu nada.

    O que fica registrado é de ONDE ela veio, porque isso é o que decide o risco
    de reivindicação, e o dono já perdeu um vídeo por causa disso.

    Nada é embarcado no repositório: o arquivo vai para o estado do agente, na
    máquina do dono, sob a conta dele.
    """
    url = safe_url(url)
    os.makedirs(out_dir, exist_ok=True)
    antes = set(os.listdir(out_dir))
    stem = "trilha-" + hashlib.sha256(url.encode()).hexdigest()[:10]
    template = os.path.join(out_dir, stem + ".%(ext)s")
    # `_playlist_args`, pelo mesmo motivo do vídeo: um link de rádio ou de
    # álbum é uma coleção, e um `--no-playlist` nele baixa a coleção inteira
    # por cima de um template sem índice -- a trilha que sobra não é a que o
    # dono ouviu antes de mandar o link.
    run(_ytdlp() + [*_playlist_args(url), "--restrict-filenames", "-x",
                    "--audio-format", "mp3", "--audio-quality", "0",
                    "-o", template, "--", url],
        TIMEOUT_DOWNLOAD, "yt-dlp (track)")
    novos = [f for f in sorted(os.listdir(out_dir))
             if f not in antes and os.path.splitext(f)[1].lower() in
             (".mp3", ".m4a", ".wav", ".opus", ".ogg", ".aac", ".flac")]
    if not novos:
        raise RuntimeError(
            f"nothing audio came down from {url}. If it is a page rather than a "
            f"track, ask the owner for the file itself.")
    baixado = os.path.join(out_dir, novos[0])
    # O NOME que o dono vai usar vem do título, não do hash: `--track` é chamado
    # por nome e um hash não é um nome que alguém digita.
    titulo = None
    try:
        # O título tem de ser o do arquivo que DESCEU, então a mesma resolução.
        saida = run(_ytdlp() + [*_playlist_args(url), "--no-warnings", "--print",
                                "%(title)s", "--skip-download", "--", url],
                    TIMEOUT_FETCH, "yt-dlp (title)")
        titulo = (saida or "").strip().splitlines()[0] if saida else None
    except Exception:
        titulo = None
    if titulo:
        # NFC e depois ASCII, pela mesma razão que `nome_no_disco` existe: um
        # título com acento em NFD deixava o til para trás como caractere solto,
        # e `re.sub` o trocava por um hífen -- "Refrão" virava `refra-o`, não
        # `refrao`. O dono chama a trilha por este nome no `--track`, então o
        # nome tem de ser o que ele digitaria.
        achatado = unicodedata.normalize("NFKD", unicodedata.normalize("NFC", titulo))
        achatado = "".join(c for c in achatado if not unicodedata.combining(c))
        limpo = re.sub(r"[^A-Za-z0-9._-]+", "-", achatado).strip("-").lower()[:60]
        if limpo:
            alvo = os.path.join(out_dir, limpo + os.path.splitext(baixado)[1])
            if not os.path.exists(alvo):
                os.replace(baixado, alvo)
                baixado = alvo
    # E o caminho devolvido é um caminho que existe, com o nome que ele tem.
    return nome_no_disco(baixado)


def archive_trusted(url, out_dir, entries, mode="video"):
    """O acervo de uma fonte que o dono avalizou, sem campanha. Um link, um arquivo.

    Mesma função que `archive()` cumpre para a campanha, com o outro portão.
    `mode="text"` é o caminho barato e é o padrão do fluxo: legenda publicada se
    houver, áudio se não houver, vídeo nunca.

    Desde a decisão do dono de 15/09/2026, `fiscal(trusted=...)` não recusa por
    licença: quem mandou o link já afirmou que pode usar o material. `entries`
    continua na assinatura porque `warden trusted` ainda existe e ainda lista as
    fontes do dono -- ela só deixou de ser um portão.
    """
    ok, porque, _ = fiscal(url, trusted=entries)
    if not ok:
        # Alcançável hoje só se `fiscal` voltar a recusar por alguma razão que
        # não seja `safe_url` (essa levanta sozinha, com a própria frase). A
        # mensagem antiga mandava o dono rodar `warden trusted add`, e depois de
        # 15/09 isso seria uma instrução falsa: adicionar a fonte não mudaria
        # resposta nenhuma. Então ela diz o que `fiscal` disse, e nada mais.
        raise RuntimeError(f"refusing to pull {url}: {porque}")
    os.makedirs(out_dir, exist_ok=True)
    return _download_one(url, out_dir, mode=mode)


def archive(rules, out_dir, limit=None, mode="video"):
    """Download what the brief authorised, and refuse to improvise.

    An empty archive list is not a reason to go looking. A clip built from
    footage the campaign did not publish is rejected on submission, after the
    views, which is the exact loss this agent exists to prevent.
    """
    urls = R.get(rules, "sources.archive_urls", [])
    if not urls:
        raise RuntimeError(
            "this campaign's rule set publishes no archive link, so there is no "
            "authorised footage to pull. Add the links the brief gives under "
            "sources.archive_urls, or send the footage yourself.")
    os.makedirs(out_dir, exist_ok=True)
    got, failed = [], []
    for url in urls[: limit or len(urls)]:
        try:
            got.append(_download_one(url, out_dir, mode=mode))
        except Exception as exc:                       # one bad link, not a dead run
            failed.append((url, str(exc).splitlines()[0]))
    return got, failed


def _carimbo(segundos):
    """HH:MM:SS.mmm, que é o formato que `--download-sections` entende."""
    t = max(0.0, float(segundos))
    h, resto = divmod(t, 3600)
    m, seg = divmod(resto, 60)
    return f"{int(h):02d}:{int(m):02d}:{seg:06.3f}"


def archive_window(rules, out_dir, url, start, end, folga=2.0, trusted=None):
    """Baixa SÓ a janela pedida de um link do acervo. (caminho, in_point).

    LEIA ISTO ANTES DE MEXER NO DOWNLOAD. Esta função é as duas coisas ao mesmo
    tempo: ela derrubou o tempo até o primeiro clipe de 12min26s para 62s, e ela
    criou o pior defeito que este projeto entregou.

    O ganho: baixar 5 MB de uma janela em vez de 361 MB de um vídeo de 18
    minutos, e pular os 195s de transcrição usando a legenda publicada.

    O defeito: o arquivo que ela escreve tem RELÓGIO PRÓPRIO. O que era 179s da
    fonte virou 0s aqui. O SRT continua no relógio da fonte, e o `cut` filtrava
    as cues pelo tempo do arquivo -- então dois cortes do minuto três saíram com
    a legenda da ABERTURA do vídeo. Hook de um assunto, legenda de outro, nos
    dois. E cada portão disse que estava certo: hook cabendo, karaoke ligado,
    nenhuma cue pendurada, letra na faixa, duração igual à pedida, 273 testes
    verdes. Só o contact sheet pegou, porque uma pessoa olhou.

    Por isso a ficha `.origem.json` é escrita ao lado do arquivo e `cut` a
    EXIGE: um arquivo com relógio implícito não circula neste sistema. Se você
    criar outro caminho que produza recorte de vídeo -- outro formato, outra
    fonte, um cache -- ele tem de escrever a mesma ficha, ou `cut` vai recusar
    queimar legenda nele. Essa recusa é o recurso, não o obstáculo.
    

    O FISCAL VEM PRIMEIRO, e este parágrafo é o motivo de esta função existir
    em vez de um `--download-sections` solto em algum lugar. O caminho de
    download é onde a autorização de acervo é aplicada: `archive()` só puxa o
    que está em `sources.archive_urls`. Um atalho novo que aceitasse uma URL e
    baixasse um pedaço dela seria uma porta lateral por onde material não
    autorizado entra -- e um clipe rápido feito de material não autorizado é
    pior que um clipe lento, porque ele é reprovado DEPOIS das visualizações.
    Então a mesma pergunta que `archive()` faz é feita aqui, antes de qualquer
    byte descer.

    `folga` existe porque `--download-sections` corta em keyframe: pedir com
    dois segundos de sobra dos dois lados garante que a janela pedida está
    inteira dentro do arquivo. Devolve junto o `in_point`, que é onde a janela
    original começa DENTRO do arquivo baixado -- sem ele, quem cortar depois
    usa o tempo da fonte num arquivo que já não tem esse tempo.
    """
    ok, porque, titulo = fiscal(url, rules=rules, trusted=trusted)
    if not ok:
        raise RuntimeError(
            f"refusing to pull a window of {url}: {porque}. "
            + (f"(the video is {titulo!r}) " if titulo else "")
            + "Downloading a slice is still downloading: the archive gate is "
              "the same one, and a fast clip made of unauthorised footage is "
              "worse than a slow one.")
    os.makedirs(out_dir, exist_ok=True)
    de = max(0.0, float(start) - folga)
    ate = float(end) + folga
    # O nome carrega as DUAS pontas. Com só o início, `--window 181-201` e
    # `--window 181-300` davam o mesmo arquivo -- e o yt-dlp não rebaixa um
    # destino que já existe, então a segunda chamada devolvia o arquivo de 24s
    # da primeira e o comando imprimia "corte com --end 121" sobre ele.
    stem = "janela-%s-%d-%d" % (hashlib.sha256(url.encode()).hexdigest()[:10],
                                int(de * 1000), int(ate * 1000))
    template = os.path.join(out_dir, stem + ".%(ext)s")
    # `[protocol^=http]` NÃO é decoração. Sem ele o yt-dlp escolhe o HLS deste
    # vídeo, e `--download-sections` sobre m3u8 entrega um mp4 SEM FAIXA DE
    # VÍDEO, sem erro nenhum -- medido. Era o defeito que este caminho traria
    # de brinde, e ele seria descoberto num clipe entregue.
    # A MESMA resolução de playlist que o preparo usou. Ver `_playlist_args`:
    # com `--no-playlist` fixo aqui, um link de playlist ou de canal escolhia
    # um vídeo no preparo e outro neste download, em silêncio.
    run(_ytdlp() + [*_playlist_args(url), "--restrict-filenames",
                    "--download-sections", f"*{_carimbo(de)}-{_carimbo(ate)}",
                    "-f", "bv*[height<=1080][protocol^=http]+ba[protocol^=http]/"
                          "b[height<=1080][protocol^=http]/b[protocol^=http]",
                    "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD, "yt-dlp (window)")
    saiu = sorted(f for f in os.listdir(out_dir)
                  if f.startswith(stem)
                  and os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm"))
    if not saiu:
        raise RuntimeError(f"yt-dlp returned no file for the {de:.0f}-{ate:.0f}s "
                           f"window of that link")
    caminho = os.path.join(out_dir, saiu[0])
    _confere_janela(caminho, de, ate)
    # A ORIGEM VIAJA COM O ARQUIVO, e este é o conserto de um defeito que
    # chegou a dois clipes entregues.
    #
    # O arquivo de janela tem relógio PRÓPRIO: o que era 179s da fonte virou 0s
    # aqui. O SRT continua no relógio da fonte. Nada casava os dois, e o `cut`
    # filtrava as cues pelo tempo do ARQUIVO -- então um corte do minuto três
    # saiu com a legenda da abertura do vídeo, nos dois clipes, e cada portão
    # que existe disse que estava tudo certo: hook cabendo, karaoke ligado,
    # nenhuma cue pendurada, letra na faixa, duração igual à pedida.
    #
    # Um arquivo com relógio implícito não pode circular neste sistema. Então
    # ele passa a carregar de onde veio, ao lado dele, e quem consome exige
    # isso: `cut` que receba um arquivo de janela SEM esta ficha recusa queimar
    # legenda, em vez de queimar a legenda errada.
    _escreve_origem(caminho, url, de, ate, start, end)
    return caminho, max(0.0, float(start) - de)


def _confere_janela(caminho, de, ate):
    """A faixa de vídeo existe mesmo? Escrita uma vez, para os dois caminhos.

    A resposta já foi NÃO, calada: `--download-sections` sobre HLS entrega um
    mp4 sem faixa de vídeo e sem erro. Um arquivo assim atravessa todo o resto
    do sistema e vira um clipe entregue. Apagar é higiene; a mensagem é o que
    importa.
    """
    sonda = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", caminho],
        capture_output=True, text=True)
    if sonda.returncode != 0 or not sonda.stdout.strip():
        try:
            os.remove(caminho)
        except OSError:
            pass
        raise RuntimeError(
            f"the {de:.0f}-{ate:.0f}s window that came down has no video track "
            f"(ffprobe found none). That is what `--download-sections` does over "
            f"an HLS stream, and it does it silently. The file was deleted "
            f"rather than handed on.")


def _escreve_origem(caminho, url, de, ate, start, end):
    """A ficha que faz o relógio do recorte ser explícito. Ver `archive_window`."""
    with open(caminho + ".origem.json", "w", encoding="utf-8") as fh:
        json.dump({"url": url,
                   "source_start": round(de, 3),
                   "source_end": round(ate, 3),
                   "in_point": round(max(0.0, float(start) - de), 3),
                   "asked_start": round(float(start), 3),
                   "asked_end": round(float(end), 3)}, fh, indent=1)


def archive_windows(rules, out_dir, url, janelas, folga=2.0, trusted=None):
    """Várias janelas do mesmo link, numa execução só do yt-dlp. [(caminho, in_point)].

    Medido em 15/09, no vídeo de 18 minutos: duas janelas em duas execuções
    custam 38s; as mesmas duas numa execução custam **30s**. O ganho é de uma
    negociação de conexão a menos, e é o único ganho de download que sobrou
    depois de medir.

    E aqui está o motivo de o resto não ter ganho: `--download-sections` obriga
    o yt-dlp a usar o ffmpeg como downloader, e o ffmpeg **remuxa a seção em
    tempo real**, a cerca de 1,8x. Uma janela de 28s custa uns 17s de ffmpeg
    independentemente da banda. Por isso duas janelas (30s) custam mais que o
    arquivo inteiro deste vídeo (13s): a janela economiza DISCO -- 14 MB contra
    382 MB -- e só economiza tempo quando a fonte é longa o bastante para o
    download inteiro passar dos 30s.

    `--download-sections` aceita ser repetido -- está na documentação do
    yt-dlp -- mas o nome de saída então colide entre as seções, e a
    documentação não trata desse caso. Por isso o template carrega
    `%(section_start)s`, que é o campo que o próprio yt-dlp expõe para isto.
    """
    ok, porque, titulo = fiscal(url, rules=rules, trusted=trusted)
    if not ok:
        raise RuntimeError(
            f"refusing to pull windows of {url}: {porque}. "
            + (f"(the video is {titulo!r}) " if titulo else "")
            + "Downloading slices is still downloading: the gate is the same "
              "one, and a fast clip made of unauthorised footage is worse than "
              "a slow one.")
    if not janelas:
        raise RuntimeError("archive_windows was given no window to pull")
    os.makedirs(out_dir, exist_ok=True)
    pedidos = []
    secoes = []
    for start, end in janelas:
        de = max(0.0, float(start) - folga)
        ate = float(end) + folga
        pedidos.append((float(start), float(end), de, ate))
        secoes += ["--download-sections", f"*{_carimbo(de)}-{_carimbo(ate)}"]
    marca = hashlib.sha256(url.encode()).hexdigest()[:10]
    template = os.path.join(
        out_dir, f"janela-{marca}-%(section_start)s-%(section_end)s.%(ext)s")
    # A MESMA resolução de playlist que o preparo usou. Ver `_playlist_args`.
    run(_ytdlp() + [*_playlist_args(url), "--restrict-filenames", *secoes,
                    "-f", "bv*[height<=1080][protocol^=http]+ba[protocol^=http]/"
                          "b[height<=1080][protocol^=http]/b[protocol^=http]",
                    "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD * max(1, len(janelas)), "yt-dlp (windows)")

    saiu = sorted(f for f in os.listdir(out_dir)
                  if f.startswith(f"janela-{marca}-")
                  and os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm"))
    resultados = []
    for start, end, de, ate in pedidos:
        # O yt-dlp arredonda o carimbo no nome, então o arquivo é casado pelo
        # início mais próximo e não por igualdade de string -- casar por string
        # devolveria "nenhum arquivo" com o arquivo no disco.
        melhor, dist = None, None
        for nome in saiu:
            partes = os.path.splitext(nome)[0].split("-")
            try:
                inicio = float(partes[-2])
            except (ValueError, IndexError):
                continue
            d = abs(inicio - de)
            if dist is None or d < dist:
                melhor, dist = nome, d
        if melhor is None or dist > 1.5:
            raise RuntimeError(
                f"yt-dlp returned no file for the {start:.0f}-{end:.0f}s window "
                f"of that link (it wrote {saiu or 'nothing'})")
        caminho = os.path.join(out_dir, melhor)
        _confere_janela(caminho, de, ate)
        _escreve_origem(caminho, url, de, ate, start, end)
        resultados.append((caminho, max(0.0, start - de)))
    return resultados


def origem_da_janela(source):
    """(ficha, motivo). A origem de um arquivo que é recorte de outro.

    `None, None` para um arquivo comum. `None, motivo` quando o arquivo PARECE
    ser uma janela e a ficha não está lá ou não dá para ler -- que é o caso em
    que quem consome tem de parar, não adivinhar.
    """
    caminho = str(source or "")
    ficha = caminho + ".origem.json"
    if os.path.isfile(ficha):
        try:
            with open(ficha, encoding="utf-8") as fh:
                dados = json.load(fh)
            if "source_start" in dados:
                return dados, None
            return None, f"{os.path.basename(ficha)} has no source_start"
        except Exception as exc:
            return None, (f"{os.path.basename(ficha)} is not readable "
                          f"({type(exc).__name__})")
    if os.path.basename(caminho).startswith("janela-"):
        return None, (f"{os.path.basename(caminho)} was cut out of a longer "
                      f"source -- its clock starts at that window, not at the "
                      f"source's zero -- and the .origem.json that says where "
                      f"it came from is missing")
    return None, None


# As línguas de legenda que valem a pena pedir, e por que são estas.
#
# Cada língua na lista é uma requisição a mais, e cada requisição a mais é uma
# chance a mais de 429. `en` sozinho já derrubou o `archive` duas vezes num
# vídeo em português -- por isso a lista era só `pt,pt-BR`.
#
# Mudou em 15/09/2026, e por uma medição: um vídeo EM INGLÊS caía sempre na
# transcrição (220s medidos) mesmo publicando legenda, porque a única língua que
# o agente sabia pedir era português. Pagar 220s por um texto que já está
# publicado é o defeito exato que este caminho existe para evitar, e ele
# acontecia em toda fonte que não fosse brasileira.
#
# O que tornou isso seguro foi outro conserto que já está de pé: a legenda tem a
# própria corrida (ver `_download_one`), então um 429 no `en` custa a legenda
# daquela corrida, não a execução inteira -- que era o que o custava em 14/09.
#
# E NADA DE CURINGA. Medido no container em 15/09/2026, 15:33 BRT, e foi o que
# derrubou o primeiro desenho deste conserto:
#
#     yt-dlp --skip-download --write-auto-subs --sub-langs "pt.*" --sub-format vtt
#       -> escreveu t1.pt-BR.vtt (3822 B)
#       -> escreveu t1.pt-en.vtt (4280 B)
#       -> ERROR: Unable to download video subtitles for 'pt-PT-en':
#          HTTP Error 429: Too Many Requests
#
# Um `pt.*` sozinho expandiu para TRÊS variantes e a terceira levou 429. Um
# `pt.*,en.*` mais a língua detectada vira facilmente seis requisições, ou seja,
# o curinga transforma "pedir a legenda" na rajada que este arquivo inteiro
# existe para não disparar.
#
# Então a lista é CURTA, CONCRETA e em ordem de preferência: no máximo três
# entradas, sem `.*`. `pt-BR` antes de `pt` porque é a que mais aparece.
SUB_LANGS_PADRAO = "pt-BR,pt,en"
SUB_LANGS = os.environ.get("WARDEN_SUB_LANGS") or SUB_LANGS_PADRAO

# Quantas línguas no máximo, e é um teto de REQUISIÇÕES, não de gosto: a terceira
# variante já levou 429 na medição acima.
SUB_LANGS_MAX = 3


def lingua_do_video(url):
    """A língua em que o vídeo FOI FEITO, lida do link, ou None.

    É a pergunta que decide tudo neste caminho desde a regra do dono de
    15/09/2026: **a legenda tem de estar na língua do vídeo**. Não é "preferir
    português" -- um vídeo em inglês quer legenda em inglês, um em espanhol quer
    em espanhol. A legenda na língua da fonte é a que a fonte publicou; qualquer
    outra é tradução de máquina, em geral por cima de transcrição de máquina.

    Sai de `_facts`, que é UMA extração cacheada por link e traz título, canal e
    língua na mesma resposta. Falhar aqui devolve None, nunca exceção.
    """
    try:
        lingua = (_facts(url) or {}).get("language") or ""
    except Exception:
        # Inclusive `FonteBloqueada`. Se o endereço está recusado, quem vai
        # dizer isso é a corrida da legenda, com a mensagem honesta inteira --
        # não uma consulta de idioma vazando por baixo dela.
        return None
    lingua = lingua.strip()
    if not lingua or lingua.upper() == "NA":
        return None
    # O campo vem do lado de lá: `pt`, `en-US`, e nada garante que seja só isso.
    # Ele entra numa linha de comando, então passa por um filtro de formato de
    # tag de idioma antes -- não por confiança, por formato. E sem `.*`: um
    # curinga vindo do outro lado seria o 429 medido, entregue de graça.
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,2}", lingua):
        return None
    return lingua


_NAO_DITO = object()


def _sub_langs_para(url, lingua=_NAO_DITO):
    """As línguas a pedir para ESTE link, incluindo a original do vídeo.

    `lingua` já detectada pode ser passada, e quem chama as duas coisas DEVE
    passar. Medido em 15/09/2026: `_facts` não memoriza falha (de propósito --
    uma recusa é estado da rede, não fato sobre o link), então chamar
    `lingua_do_video` e depois `_sub_langs_para` disparava DUAS extrações contra
    um endereço que acabara de recusar a primeira. A rajada nasce assim.

    `WARDEN_SUB_LANGS` sobrescreve tudo e nem pergunta: quem definiu a variável
    decidiu, e uma detecção que passasse por cima dela seria o agente discutindo
    com o dono.

    Sem a variável, a língua original entra na FRENTE da lista padrão, e a lista
    é cortada em `SUB_LANGS_MAX`. As duas coisas importam: a frente porque o
    yt-dlp para no primeiro acerto, e o corte porque a terceira variante já
    levou 429 na medição de 15/09/2026 registrada acima.

    A língua sai de `_facts`, que é UMA extração cacheada por link e já traz
    título e canal na mesma resposta -- então isto é, no pior caso, uma
    requisição a mais no comando inteiro, e zero quando algo já perguntou pelo
    título.

    Detecção que falha não é erro: cai na lista padrão, calada. Uma legenda a
    menos é caro; uma execução derrubada por causa de uma consulta de idioma
    seria pior.
    """
    if os.environ.get("WARDEN_SUB_LANGS"):
        # O dono decidiu, inclusive sobre o teto: se ele quer seis línguas, são
        # seis. O 429 é um risco que ele escolheu, não um que o agente impôs.
        return os.environ["WARDEN_SUB_LANGS"]
    if lingua is _NAO_DITO:
        lingua = lingua_do_video(url)
    if not lingua:
        return SUB_LANGS
    pedidos = [lingua]
    base = lingua.split("-")[0].lower()
    for l in SUB_LANGS.split(","):
        l = l.strip()
        if not l or l in pedidos:
            continue
        # A raiz já pedida não volta como variante: `en` depois de `en-US` é uma
        # requisição a mais pela mesma legenda.
        if l.split("-")[0].lower() == base and len(pedidos) > 1:
            continue
        pedidos.append(l)
    return ",".join(pedidos[:SUB_LANGS_MAX])


def _fora_da_lingua_da_fonte(tag, lingua_fonte):
    """O porquê de esta legenda NÃO estar na língua da fonte, ou None.

    A regra do dono, dita por ele em 16/09/2026: *se o vídeo for inglês quero
    legenda em inglês, se o vídeo for em português, quero legenda em
    português*. É a mesma regra de 15/09 -- a língua é a da FONTE, seja ela
    qual for -- levada até a consequência que faltava: quando a legenda na
    língua da fonte não existe, a resposta não é usar a de outra língua.

    O que custou, medido hoje (16/09/2026) num pedido real: o vídeo era
    `en-US` e a única legenda que desceu foi `source-810777f3f7aa.pt.srt`, a
    tradução automática do YouTube. A ficha do lote registrou os dois fatos
    lado a lado -- `"language": "pt"`, `"source_language": "en-US"` -- e o
    clipe foi queimado assim mesmo. Ou seja: o código SABIA e seguiu. A
    escolha entre as legendas em disco já estava certa (`_prefere_idioma`); o
    defeito era aceitar o plano B como se fosse legenda.

    Compara pela RAIZ, então `pt-BR` casa com `pt` e `en-US` com `en`: uma
    variante regional é a mesma língua, e recusá-la mandaria transcrever um
    áudio que já tem legenda publicada boa.

    Sem tag no nome ou sem língua da fonte lida, devolve None: não dá para
    afirmar que está fora do que não se sabe, e parar aqui deixaria o dono sem
    clipe por causa de um fato que ninguém mediu.
    """
    if not tag or not lingua_fonte:
        return None
    raiz = str(tag).split("-")[0].split("_")[0].lower()
    raiz_fonte = str(lingua_fonte).split("-")[0].split("_")[0].lower()
    if not raiz or not raiz_fonte or raiz == raiz_fonte:
        return None
    return (f"this video is in {lingua_fonte} and the only published subtitle "
            f"is tagged {tag}, which is a machine translation on top of "
            f"machine transcription and not what was said")


def _pull_subs(url, out_dir, stem, template, playlist_args):
    """(caminho do .srt, motivo de não ter vindo). Falhar aqui não é fatal.

    Corrida própria, de propósito. Uma legenda que não existe, ou um 429 do
    YouTube, não pode impedir o vídeo de baixar -- e era exatamente isso que
    acontecia quando as duas coisas vinham no mesmo comando.
    """
    def _achadas():
        """As legendas em disco, EM ORDEM DE PREFERÊNCIA DE IDIOMA.

        `sorted()` aqui era o bug: alfabeticamente `.en.srt` vem antes de
        `.pt-BR.srt`, então o inglês ganhava sempre que conseguia baixar, e
        "sempre que conseguia" variava com o 429. Ver `_prefere_idioma`.

        E `.vtt` CONTA, o que era o segundo bug e o mais caro. Medido em
        15/09/2026 na imagem construída: a pasta de um `lote prep` real tinha

            source-0424974c6853.en.vtt
            source-0424974c6853.pt-BR.vtt
            source-0424974c6853.webm      <- o áudio, que não devia ter descido

        `--convert-subs srt` converte no FIM da execução do yt-dlp. Quando a
        execução não chega ao fim -- 429 numa variante, timeout, qualquer coisa
        -- os `.vtt` ficam em disco sem conversão. Esta função só olhava `.srt`,
        então via uma pasta vazia, respondia "este vídeo não publica legenda", e
        o caminho barato ia baixar o ÁUDIO para transcrever com duas legendas
        publicadas ali do lado. Depois `_subtitle_beside` achava os `.vtt` e
        escolhia sozinho -- sem língua anotada, em português, que é como o vídeo
        do Rick Astley virou `LANG:pt-br`.

        `_read_srt` já lê VTT (tem o tratamento dos parâmetros de cue), então
        aceitar a extensão é o conserto inteiro.
        """
        brutos = [f for f in sorted(os.listdir(out_dir))
                  if f.startswith(stem) and f.lower().endswith((".srt", ".vtt"))]
        return _prefere_idioma(brutos, prefer=[lingua] if lingua else None)

    # A língua do VÍDEO manda, e ela manda duas vezes: no que é pedido e, mais
    # importante, no que é ESCOLHIDO entre o que chegou.
    lingua = lingua_do_video(url)
    langs = _sub_langs_para(url, lingua=lingua)
    # ANOTADA AGORA, antes de qualquer coisa poder falhar.
    #
    # Ela estava sendo escrita só no fim, no caminho de sucesso -- então
    # bastava a corrida da legenda falhar (ou, pelo bug acima, PARECER ter
    # falhado) para o `.lingua` nunca existir. E é justamente aí que ele mais
    # importa: é o que impede `_subtitle_beside` de escolher no escuro depois.
    # A língua do vídeo é um fato sobre o link, não sobre o download ter dado
    # certo.
    _marca_lingua(out_dir, stem, lingua)
    try:
        run(_ytdlp() + [*playlist_args, "--restrict-filenames", "--skip-download",
             "--write-auto-subs", "--write-subs", "--sub-langs", langs,
             "--convert-subs", "srt", "-o", template, "--", url],
            TIMEOUT_FETCH * 4, "yt-dlp (subtitles)")
    except RuntimeError as exc:
        # Uma corrida que falhou não apaga o que já ESCREVEU, e é por isso que a
        # pergunta é `_achadas()` e não `ja_tinha`.
        #
        # Medido em 15/09/2026, 15:33 BRT: com `--sub-langs "pt.*"` o yt-dlp
        # escreveu `pt-BR` e `pt-en` e só então levou 429 na terceira variante.
        # A versão anterior olhava só para o que existia ANTES da corrida, não
        # via as duas legendas recém-escritas, e seguia para o download do
        # áudio -- pagando a transcrição com a legenda em disco, que é
        # exatamente o que este caminho existe para não fazer.
        agora = _achadas()
        if agora:
            return os.path.join(out_dir, agora[0]), None
        # O endereço recusado NÃO é "esta legenda não veio", e tratá-lo como
        # tal custou as duas coisas que este arquivo existe para evitar.
        # Medido em 15/09, no primeiro teste de ponta a ponta do conserto:
        #
        # 1. A mensagem honesta tem parágrafos, e ela vinha por aqui achatada
        #    em 160 caracteres e embutida numa frase de uma linha sobre baixar
        #    áudio. O dono lia "What this is NOT, and you may not say
        #    otherwise:, so pulling the audio to transcribe instead" -- o
        #    diagnóstico cortado no meio da frase que o proibia de inventar.
        # 2. E então `_pull_text_first` seguia em frente e pedia o ÁUDIO, que
        #    é outra requisição contra um endereço que acabou de recusar. É a
        #    rajada que marca o endereço, disparada pelo código escrito para
        #    parar de marcá-lo.
        #
        # Então ela sobe inteira, e a corrida seguinte não acontece.
        #
        # MENOS quando é só 429, e essa distinção é de 15/09/2026, 15:33 BRT.
        # Um 429 aqui é o YouTube pedindo calma numa variante de legenda, com
        # nenhuma ou algumas já baixadas -- não é o endereço recusado. Subir com
        # o parágrafo do bot check derrubava o `archive` inteiro e dizia ao dono
        # que a máquina dele está bloqueada, o que seria falso. Então vira
        # "não veio legenda", a transcrição segue, e a requisição NÃO é
        # repetida: repeti-la é o que transforma um "devagar" em bloqueio.
        if isinstance(exc, FonteBloqueada) and not getattr(exc, "apenas_429", False):
            raise
        if isinstance(exc, FonteBloqueada):
            return None, ("YouTube answered 429 (too many requests) while "
                          "fetching subtitles in " + langs + ", so there is no "
                          "published subtitle to use this time")
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"
    # O que EXISTE, não o que é novo. A versão anterior comparava o diretório
    # antes e depois, e na segunda corrida o `.srt` já estava lá: não aparecia
    # como novo, e a ferramenta respondia "este vídeo não publica legenda"
    # com a legenda em disco. Um arquivo que já está aqui é um arquivo que
    # temos.
    achadas = _achadas()
    if not achadas:
        # `langs`, não `SUB_LANGS`: a frase tem de dizer o que foi PEDIDO nesta
        # corrida, senão ela mente justamente quando a língua original entrou.
        return None, "this video publishes no subtitle in " + langs
    # A LEGENDA DE OUTRA LÍNGUA NÃO SERVE, e este é o portão que faltava.
    #
    # `achadas[0]` é a preferida por `_prefere_idioma`, que já põe a língua da
    # fonte na frente: se ela não está aqui, nenhuma das outras está. Devolver
    # None faz `_pull_text_first` baixar o ÁUDIO e transcrever -- o caminho que
    # já existe para a fonte que não publica legenda --, e é ele que produz
    # texto na língua da fonte em vez de tradução de máquina.
    #
    # Não apaga os arquivos: eles são a prova do que a fonte publicou, e
    # `transcribe` os recusa de novo pelo mesmo motivo se alguém os encontrar
    # ao lado do áudio. Ver `_fora_da_lingua_da_fonte` para o caso medido.
    fora = _fora_da_lingua_da_fonte(_tag_do_nome(achadas[0]), lingua)
    if fora:
        return None, fora
    _conta_a_legenda(achadas, lingua)
    return os.path.join(out_dir, achadas[0]), None


def _marca_lingua(out_dir, stem, lingua):
    """Anota a língua do vídeo ao lado do stem. Falhar aqui não custa nada."""
    if not lingua:
        return
    try:
        with open(os.path.join(out_dir, stem + ".lingua"), "w",
                  encoding="utf-8") as fh:
            fh.write(lingua.strip() + "\n")
    except OSError:
        pass


def _lingua_marcada(path):
    """A língua anotada para este arquivo, ou None. Nunca levanta.

    Procura pelo stem do arquivo e vai encurtando: `source-abc.en.srt` e
    `source-abc.mp4` têm de achar o mesmo `source-abc.lingua`.
    """
    pasta = os.path.dirname(path) or "."
    raiz = os.path.basename(path)
    for _ in range(4):
        raiz = os.path.splitext(raiz)[0]
        if not raiz:
            break
        try:
            with open(os.path.join(pasta, raiz + ".lingua"),
                      encoding="utf-8") as fh:
                marcada = fh.read().strip()
            if marcada:
                return marcada
        except OSError:
            pass
        if "." not in raiz:
            break
    return None


def _conta_a_legenda(achadas, lingua_video=None):
    """Diz em voz alta qual idioma a legenda tem e DE ONDE ele veio.

    Sai sempre, não só quando há problema, e isso é de propósito: o modelo
    precisa saber em que língua escrever o hook ANTES de renderizar, e a regra
    do dono (15/09/2026) é que o hook segue a língua do vídeo. `cut` recusa
    queimar hook e legenda em línguas diferentes -- a regra está certa e não se
    toca --, e sem esta linha o modelo só descobria a mistura ao bater no `cut`,
    depois de todo o download, com o lote inteiro perdido.

    A procedência é inferida, e só o que dá para afirmar é afirmado: quando a
    tag casa com a língua do vídeo, é a legenda da própria fonte; quando não
    casa, é tradução. Uma legenda traduzida automaticamente a partir de ASR é
    erro de reconhecimento somado a erro de tradução, e quem lê precisa saber
    que está lendo isso.
    """
    if not achadas:
        return
    escolhida = _tag_do_nome(achadas[0])
    outras = [t for t in (_tag_do_nome(a) for a in achadas[1:]) if t]
    if escolhida is None:
        print("  (the published subtitle that came down carries no language "
              "tag in its name, so the language it is in is unknown. Read a "
              "line of it before writing the hook.)", file=sys.stderr)
        return
    raiz = escolhida.split("-")[0].split("_")[0]
    raiz_video = (lingua_video or "").split("-")[0].split("_")[0].lower()
    if not raiz_video:
        origem = ("published by the source; this video does not say what "
                  "language it is in, so that is the tag, not a match")
    elif raiz == raiz_video:
        origem = "the video's own language, as published by the source"
    else:
        origem = (f"a TRANSLATION: this video is in {lingua_video!r}, so this "
                  f"caption is machine-translated, usually on top of machine "
                  f"transcription -- two layers of error")
    print(f"  (caption language: {escolhida} -- {origem}."
          + (f" Also on disk: {', '.join(outras)}." if outras else "")
          + f" Write the hook in {escolhida}: `cut` refuses to burn a hook and "
            f"a caption in different languages, and that refusal is correct.)",
          file=sys.stderr)


def _pull_text_first(url, out_dir, stem, template, playlist_args):
    """O caminho barato: legenda publicada, ou o áudio -- nunca o vídeo.

    Medido em 14/09 no vídeo do teste: a legenda desce em 4s e 73 KB; o áudio
    inteiro em 4s e 15 MB; o vídeo inteiro em 16s e 361 MB. E transcrever custa
    195s, que é o que a legenda publicada economiza.

    Escolher a janela é trabalho de TEXTO. Baixar 361 MB para descobrir onde
    cortar é pagar o vídeo antes de saber se vai usá-lo.
    """
    legenda, porque = _pull_subs(url, out_dir, stem, template, playlist_args)
    if legenda:
        return legenda
    # Sem legenda publicada, o texto ainda sai barato: transcrever 15 MB de áudio
    # dá o mesmo resultado que transcrever 361 MB de vídeo, e o whisper só ouve.
    print(f"  ({porque}, so pulling the audio to transcribe instead of the "
          f"video: measured 15 MB against 361 MB for the same words.)",
          file=sys.stderr)
    # `ba/w` e não `ba`, e o `/w` é o conserto de 15/09/2026.
    #
    # `ba` pede uma faixa SÓ de áudio, que é o que torna este caminho barato num
    # podcast do YouTube. Mas nem toda fonte publica uma: o TikTok serve apenas
    # formatos combinados -- medido, doze formatos, todos com vídeo e áudio
    # juntos --, então `ba` não casava com nada e o caminho inteiro morria
    # dizendo "neither a subtitle nor an audio track", como se a fonte não
    # tivesse fala. E a ironia: o menor combinado do TikTok tem 1,11 MiB, ou
    # seja, é mais barato que o "caminho caro" que esta função existe para
    # evitar.
    #
    # `w` é o pior formato disponível, que é exatamente o que se quer quando o
    # arquivo serve só para ser ouvido: o whisper não olha a imagem.
    run(_ytdlp() + [*playlist_args, "--restrict-filenames",
         "--max-filesize", str(MAX_DIRECT_BYTES), "-f", "ba/w",
         "-o", template, "--", url], TIMEOUT_DOWNLOAD, "yt-dlp (audio)")
    ouvivel = (".m4a", ".webm", ".opus", ".mp3", ".ogg",
               ".mp4", ".mkv", ".mov", ".aac", ".flac")
    audios = sorted(f for f in os.listdir(out_dir)
                    if f.startswith(stem)
                    and os.path.splitext(f)[1].lower() in ouvivel)
    if not audios:
        raise RuntimeError(
            "yt-dlp returned neither a subtitle nor anything with sound for "
            "that link, so there is no text to choose a window from.")
    return os.path.join(out_dir, audios[0])


def nome_no_disco(caminho):
    """Renomeia um arquivo recém-baixado para um nome digitável. Devolve o caminho QUE EXISTE.

    Este é o ponto inteiro da função, e a única coisa que ela promete: o caminho
    devolvido é um caminho que `os.path.isfile` encontra. Quem imprime este
    caminho está imprimindo algo que a pessoa -- ou o modelo -- pode abrir.

    Medido em 15/09/2026, e custou 5 minutos e 9 chamadas de ferramenta: um
    arquivo do Drive chamado "Refrão.mp4" desceu com o nome em Unicode NFD, ou
    seja, `a` + U+0303 (til combinante) em vez do único caractere `ã` (U+00E3).
    Os dois SE DESENHAM IGUAIS na tela. O agente leu o nome, digitou "Refrão"
    como qualquer um digitaria -- em NFC, que é o que um teclado produz -- e o
    arquivo não existia. Nove tentativas, nenhuma mensagem de erro útil, porque
    do ponto de vista do sistema de arquivos eram dois nomes diferentes.
    Linux compara bytes; macOS é que normaliza por conta própria.

    Então o conserto é no MOMENTO DO DOWNLOAD, não em quem lê depois: NFC
    primeiro (o til gruda na letra), e se ainda sobrar caractere que não é ASCII
    seguro, o nome desce para ASCII. Um nome de arquivo aqui também vira
    argumento de filtro do ffmpeg mais adiante no pipeline, então tirar aspas,
    dois-pontos e barras não é estética.

    Um arquivo cujo nome já é seguro não é tocado. Uma colisão não é resolvida
    sobrescrevendo: o nome ganha um sufixo, porque perder um vídeo do acervo
    para arrumar um acento seria um conserto pior que o defeito.
    """
    if not caminho or not os.path.isfile(caminho):
        return caminho
    pasta = os.path.dirname(caminho) or "."
    base = os.path.basename(caminho)
    raiz, ext = os.path.splitext(base)

    # 1. NFC: o acento combinante vira um caractere só, que é o que um teclado
    #    produz e o que o modelo escreve de volta.
    raiz_nfc = unicodedata.normalize("NFC", raiz)
    ext = unicodedata.normalize("NFC", ext)
    # 2. E se ainda não é ASCII, desce para ASCII: `ã` -> `a`, via a decomposição
    #    de compatibilidade, jogando fora as marcas. Um nome inteiro em cirílico
    #    ou japonês some nessa conta, então há uma rede embaixo, mais abaixo.
    limpo = unicodedata.normalize("NFKD", raiz_nfc)
    limpo = "".join(c for c in limpo if not unicodedata.combining(c))
    limpo = limpo.encode("ascii", "ignore").decode("ascii")
    # 3. O que sobra ainda passa por um filtro de caractere, porque isto acaba
    #    dentro de uma string de filtro do ffmpeg.
    limpo = re.sub(r"[^A-Za-z0-9._-]+", "-", limpo).strip("-._")[:80]
    ext_limpa = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:10] or ".mp4"
    if not limpo:
        # A rede: um nome que era inteiro não-ASCII não vira nome vazio. Um hash
        # curto do nome ORIGINAL é feio e é digitável, que é o requisito.
        limpo = "media-" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
    alvo = os.path.join(pasta, limpo + ext_limpa)
    if os.path.abspath(alvo) == os.path.abspath(caminho):
        return caminho
    n = 1
    while os.path.exists(alvo):
        alvo = os.path.join(pasta, f"{limpo}-{n}{ext_limpa}")
        n += 1
    try:
        os.replace(caminho, alvo)
    except OSError:
        # Renomear falhou (disco cheio, permissão, montagem read-only). O nome
        # feio é melhor que um caminho inventado: devolve o que existe.
        return caminho
    return alvo


# As extensões que contam como vídeo numa pasta de acervo. Uma lista só, porque
# ela era copiada em três lugares e uma delas já divergia.
EXT_VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def _drive_lista_pasta(url, destino):
    """Os arquivos de uma pasta do Drive SEM baixar nenhum. [(nome, id)] ou None.

    Uma requisição, a da página da pasta. `skip_download=True` do gdown devolve
    `GoogleDriveFileToDownload(id, path, local_path)` para cada item, e é o que
    permite escolher antes de gastar.

    E UMA LIMITAÇÃO MEDIDA, porque ela muda a regra de escolha: essa listagem
    NÃO traz o tamanho. O parser do gdown (`_parse_google_drive_file`) lê do
    HTML da pasta apenas id, nome e tipo -- não há campo de bytes para ler. A
    CLI também não expõe `--skip-download`, então isto passa pela API Python.
    Por isso quem chama tem de ter um plano para "vários vídeos e nenhum
    tamanho"; ver `_drive_maior_video`.

    Devolve None -- e não levanta -- quando não dá para listar. A pasta inteira
    ainda é um caminho que funciona, e ele continua de pé atrás disto.
    """
    try:
        import gdown
    except Exception:
        return None
    try:
        itens = gdown.download_folder(
            url=url, output=destino + os.sep, skip_download=True,
            quiet=True, use_cookies=False)
    except Exception:
        return None
    if not itens:
        return None
    achados = []
    for it in itens:
        nome = os.path.basename(getattr(it, "path", "") or "")
        ident = getattr(it, "id", None)
        if nome and ident:
            achados.append((nome, ident))
    return achados or None


def _drive_bytes(ident):
    """Quantos bytes tem este arquivo do Drive, sem baixá-lo, ou None.

    NÃO MEDIDO CONTRA O DRIVE DE VERDADE -- não havia pasta compartilhada para
    testar em 15/09/2026, e isto está escrito aqui em vez de ficar implícito.

    O que ela faz: um GET com `Range: bytes=0-0`, que traz um byte e o
    `Content-Range` com o total. Tudo que não for exatamente isso é tratado como
    "não sei": o Drive responde à página de confirmação de arquivo grande com
    um HTML de alguns KB e status 200, e aceitar esse `Content-Length` como
    tamanho do vídeo faria a escolha pelo maior eleger o HTML mais gordo. Um
    palpite confiante a partir de uma resposta que não foi observada é o defeito
    que este arquivo inteiro existe para não cometer, então o `None` aqui é o
    caminho normal, não a exceção.
    """
    alvo = "https://drive.google.com/uc?export=download&id=" + str(ident)
    try:
        pedido = urllib.request.Request(alvo, headers={"Range": "bytes=0-0"})
        with _OPENER.open(pedido, timeout=TIMEOUT_FETCH) as resposta:
            tipo = (resposta.headers.get("Content-Type") or "").lower()
            faixa = resposta.headers.get("Content-Range") or ""
            resposta.read(1)
    except Exception:
        return None
    if "html" in tipo or "/" not in faixa:
        return None
    total = faixa.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else None


def _drive_videos_em(destino):
    """Os vídeos que já estão em disco sob `destino`, maior primeiro."""
    videos = []
    for raiz, _, arquivos in os.walk(destino):
        for f in arquivos:
            if os.path.splitext(f)[1].lower() in EXT_VIDEO:
                videos.append(os.path.join(raiz, f))
    videos.sort(key=os.path.getsize, reverse=True)
    return videos


def _drive_conta(destino, quantos, escolhido, tamanhos=None):
    """A linha que diz ao dono o que tem na pasta e o que foi escolhido.

    Ela FICA, e fica igual ao que era: ficar calada sobre o resto do acervo era
    esconder dele o material dele. Só mudou o que está ao lado do escolhido --
    antes os outros arquivos, agora os outros nomes.
    """
    if quantos <= 1:
        return
    print(f"  (this Drive folder holds {quantos} videos. The biggest is the "
          f"one returned; the others were NOT downloaded -- ask for one by "
          f"name and it comes down on its own:", file=sys.stderr)
    for nome, bytes_ in (tamanhos or []):
        tamanho = f"{bytes_ // (1024*1024)} MB  " if bytes_ else ""
        marca = "  <- returned" if nome == escolhido else ""
        print(f"     {tamanho}{nome}{marca}", file=sys.stderr)
    print(f"   They are in {destino} once pulled.)", file=sys.stderr)


def _drive_maior_video(url, destino):
    """Baixa SÓ o vídeo que vai ser usado de uma pasta do Drive. Devolve o caminho.

    Três caminhos, do mais barato para o mais caro, e o mais caro é o que este
    arquivo fazia sempre:

      1. Um vídeo só na pasta. Não há o que escolher: desce um arquivo.
      2. Vários vídeos e os tamanhos respondem sem baixar. Mesma regra de
         sempre, o maior, e desce um arquivo.
      3. Vários vídeos e os tamanhos NÃO respondem -- que é o caso esperado,
         porque a listagem do Drive não traz bytes (ver `_drive_lista_pasta`) e
         a sonda de tamanho não foi medida contra o Drive de verdade. Aí a pasta
         inteira desce, como descia antes, e a pessoa é avisada de que foi isso
         que aconteceu. Um palpite sobre qual é o maior sem ter medido nenhum
         seria escolher o corte errado da campanha em silêncio.
    """
    itens = _drive_lista_pasta(url, destino)
    videos = [(n, i) for n, i in (itens or [])
              if os.path.splitext(n)[1].lower() in EXT_VIDEO]

    if itens is not None and not videos:
        raise RuntimeError(
            f"{url} is a Drive folder with no video in it. Check that the "
            f"folder is shared with anyone who has the link, and that the "
            f"footage is in this folder rather than a subfolder of it.")

    alvo = None
    tamanhos = []
    if len(videos) == 1:
        alvo = videos[0]
    elif len(videos) > 1:
        for nome, ident in videos:
            tamanhos.append((nome, _drive_bytes(ident)))
        if all(b for _n, b in tamanhos):
            nome = max(tamanhos, key=lambda p: p[1])[0]
            alvo = next(v for v in videos if v[0] == nome)

    if alvo is not None:
        nome, ident = alvo
        _drive_conta(destino, len(videos), nome, tamanhos)
        antes = set(os.listdir(destino))
        run(_gdown() + ["--no-cookies", "-O", destino + os.sep, "--",
                        "https://drive.google.com/uc?id=" + ident],
            TIMEOUT_DOWNLOAD, "gdown (one file from folder)")
        novos = sorted(set(os.listdir(destino)) - antes)
        if novos:
            # O nome vem do outro lado, então ele é normalizado AQUI, antes de
            # alguém tentar abri-lo: foi um nome do Drive em NFD que custou as
            # nove chamadas. Ver `nome_no_disco`.
            return nome_no_disco(os.path.join(destino, novos[0]))
        # gdown não escreveu nada por este caminho: cai para a pasta inteira em
        # vez de dizer que a pasta não tem vídeo, o que seria falso.
        print("  (pulling that one file wrote nothing, so falling back to the "
              "whole folder.)", file=sys.stderr)

    # O caminho caro, e ele é anunciado.
    if len(videos) > 1:
        print(f"  (this Drive folder holds {len(videos)} videos and Drive does "
              f"not publish their sizes in the folder listing, so there is no "
              f"way to tell which is biggest without pulling them. Pulling all "
              f"of them:", file=sys.stderr)
        for nome, _ident in videos:
            print(f"     {nome}", file=sys.stderr)
        print("  )", file=sys.stderr)
    run(_gdown() + ["--folder", "--no-cookies", "-O", destino, "--", url],
        TIMEOUT_DOWNLOAD, "gdown (folder)")
    em_disco = _drive_videos_em(destino)
    if not em_disco:
        raise RuntimeError(
            f"{url} is a Drive folder with no video in it. Check that the "
            f"folder is shared with anyone who has the link, and that the "
            f"footage is in this folder rather than a subfolder of it.")
    em_disco = [nome_no_disco(v) for v in em_disco]
    em_disco.sort(key=os.path.getsize, reverse=True)
    if len(em_disco) > 1:
        print(f"  (this Drive folder holds {len(em_disco)} videos. The "
              f"biggest is the one returned; the others are beside it in "
              f"{destino} and any of them can be cut:", file=sys.stderr)
        for v in em_disco:
            print(f"     {os.path.getsize(v) // (1024*1024)} MB  "
                  f"{os.path.basename(v)}", file=sys.stderr)
        print("  )", file=sys.stderr)
    return em_disco[0]


def _download_one(url, out_dir, mode="video"):
    """`mode="video"` baixa a fonte; `mode="text"` baixa só o que dá as palavras."""
    url = safe_url(url)
    parsed = urlparse(url)
    direct = os.path.splitext(parsed.path)[1].lower() in (
        ".mp4", ".mov", ".m4v", ".webm", ".mkv")
    if mode == "text" and (direct or "drive.google.com" in parsed.netloc):
        # Um arquivo solto não publica legenda e não tem faixa de áudio
        # separada para pedir: não há caminho barato aqui. Dizer isso é melhor
        # que baixar o arquivo inteiro fingindo que é o modo barato.
        raise RuntimeError(
            f"{url} is a plain file, not a hosted video: there is no published "
            f"subtitle to fetch and no audio-only stream to ask for. Pull it "
            f"whole with `warden archive` and transcribe that.")
    if direct:
        # The name is ours, never theirs: a remote basename lands in an ffmpeg
        # filter argument further down the pipeline, and a quote in it is enough
        # to leave the filter and name another file.
        name = "footage-%s%s" % (hashlib.sha256(url.encode()).hexdigest()[:12],
                                 os.path.splitext(parsed.path)[1].lower() or ".mp4")
        target = os.path.join(out_dir, name)
        written = 0
        try:
            resposta = _OPENER.open(url, timeout=TIMEOUT_FETCH)
        except urllib.error.HTTPError as exc:
            # O código nu -- "HTTP Error 403: Forbidden" -- não diz qual link
            # nem o que fazer, e esta é a porta por onde entra todo acervo que
            # não é YouTube. Um 403 aqui quase sempre é o host recusando um
            # cliente automático, não o dono sem permissão, e os dois têm
            # conserto diferente.
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"{url} answered {exc.code}: the host refused this "
                    f"download. If the file is private -- a Drive link shared "
                    f"with nobody, an S3 object behind a signature -- make it "
                    f"readable by link and send it again. If it is public in a "
                    f"browser, the host is refusing automated clients, and the "
                    f"way through is a direct file link rather than a page.")
            if exc.code == 404:
                raise RuntimeError(
                    f"{url} answered 404: there is no file at that address. "
                    f"Check the link, rather than assuming the source is gone.")
            raise RuntimeError(f"{url} answered {exc.code}: {exc.reason}")
        with resposta as response, \
                open(target, "wb") as fh:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DIRECT_BYTES:
                    fh.close()
                    os.remove(target)
                    raise RuntimeError(
                        f"{url} is over {MAX_DIRECT_BYTES // (1024**3)} GB; "
                        "that is not a clip source, and it would fill the host's disk")
                fh.write(chunk)
        return target
    if "drive.google.com" in parsed.netloc and "/folders/" in parsed.path:
        # Uma PASTA do Drive, que é como uma campanha real publica o acervo --
        # medido em 15/09/2026 num briefing de verdade: a pasta trazia cinco
        # cortes oficiais. Antes disto o link caía no caminho de arquivo, o
        # `--fuzzy` não casava com uma pasta, e o dono recebia o usage do gdown.
        #
        # Uma pasta não é UM arquivo, e esta função devolve um. A regra de
        # escolha não mudou -- o maior vídeo -- mas a ORDEM mudou, e é onde
        # estava o custo.
        #
        # Medido em 15/09/2026: a pasta trazia cinco cortes oficiais, a versão
        # anterior baixava os CINCO (671 MB em 87s) e usava um. Os outros 537 MB
        # desceram para serem pesados e descartados. Agora a pasta é LISTADA
        # primeiro, o alvo é escolhido, e só ele desce.
        if not _tem_gdown():
            raise RuntimeError(
                "this is a Google Drive folder and gdown is neither importable "
                "by this interpreter nor on PATH, so it cannot be pulled")
        destino = os.path.join(out_dir, "drive-" +
                               hashlib.sha256(url.encode()).hexdigest()[:12])
        os.makedirs(destino, exist_ok=True)
        return _drive_maior_video(url, destino)
    if "drive.google.com" in parsed.netloc:
        if not _tem_gdown():
            raise RuntimeError(
                "this is a Google Drive link and gdown is neither importable by "
                "this interpreter nor on PATH, so it cannot be pulled")
        before = set(os.listdir(out_dir))
        run(_gdown() + ["--fuzzy", "-O", out_dir + os.sep, "--", url],
            TIMEOUT_DOWNLOAD, "gdown")
        new = sorted(set(os.listdir(out_dir)) - before)
        if not new:
            raise RuntimeError("gdown wrote nothing, the folder may need permission")
        # gdown writes the name the remote chose -- the one download path where
        # the filename comes from the far side rather than from us. Renamed to a
        # name of ours before anything downstream touches it, for the same reason
        # the direct path never trusts the remote basename: it ends up as an
        # ffmpeg input argument. And its size is checked here, because gdown has
        # no --max-filesize and would otherwise be the one path with no size cap.
        got = os.path.join(out_dir, new[0])
        if os.path.getsize(got) > MAX_DIRECT_BYTES:
            os.remove(got)
            raise RuntimeError(
                f"{url} is over {MAX_DIRECT_BYTES // (1024**3)} GB; that is not "
                "a clip source, and it would fill the host's disk")
        ours = os.path.join(out_dir, "footage-%s%s" % (
            hashlib.sha256(url.encode()).hexdigest()[:12],
            os.path.splitext(new[0])[1].lower() or ".mp4"))
        os.replace(got, ours)
        # Já é um nome nosso e ASCII; passa por aqui assim mesmo para que o
        # contrato "o caminho devolvido é o caminho que existe" seja da FUNÇÃO,
        # não de quem se lembrou dele. Uma extensão exótica é o que sobra.
        return nome_no_disco(ours)
    # `have("yt-dlp")` perguntava pelo BINÁRIO no PATH. O venv da imagem instala
    # o yt-dlp como módulo, e o PATH que um subprocesso herda da árvore de
    # supervisão não tem o bin do venv -- então em máquina limpa o `archive`
    # morria dizendo "yt-dlp is not installed" com o yt-dlp instalado. É a
    # mesma armadilha que `_ytdlp()` já resolvia para os caminhos de consulta e
    # que os de download não usavam. Agora a pergunta é se dá para IMPORTAR.
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        if not have("yt-dlp"):
            raise RuntimeError(
                "yt-dlp is neither importable by this interpreter nor on PATH, "
                "so this link cannot be pulled.")
    # A resolução de playlist é UMA, e está em `_playlist_args`. Ver lá: a
    # versão anterior decidia aqui e o caminho da janela decidia de outro jeito,
    # e as duas decisões podiam cair em vídeos diferentes do mesmo link.
    playlist_args = _playlist_args(url)
    # --restrict-filenames, and a name we chose: the remote title is attacker
    # text and it ends up inside an ffmpeg filter string.
    stem = "source-" + hashlib.sha256(url.encode()).hexdigest()[:12]
    template = os.path.join(out_dir, stem + ".%(ext)s")
    if mode == "text":
        return _pull_text_first(url, out_dir, stem, template, playlist_args)
    # A legenda NÃO é pedida junto com o vídeo, e essa separação é um conserto,
    # não um estilo. Medido em 14/09: `--sub-langs pt,pt-BR,en` numa só execução
    # faz o YouTube responder 429 no `en`, e o 429 derruba a execução inteira
    # ANTES de baixar o vídeo. Reproduzido duas vezes. Qualquer fonte sem legenda
    # em inglês quebrava o `archive`, e a mensagem falava de legenda, não de
    # vídeo, então o defeito lia como problema da fonte.
    #
    # Agora são duas corridas: a legenda tem a sua, pode falhar, e a falha vira
    # aviso. O vídeo tem a sua e não depende de legenda nenhuma.
    legenda, porque = _pull_subs(url, out_dir, stem, template, playlist_args)
    run(_ytdlp() + [*playlist_args, "--restrict-filenames",
         "--max-filesize", str(MAX_DIRECT_BYTES),
         "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
         "--merge-output-format", "mp4", "-o", template, "--", url],
        TIMEOUT_DOWNLOAD, "yt-dlp")
    if not legenda and porque:
        print(f"  (no published subtitle came down for {stem}: {porque}. The "
              f"cut will need `warden transcribe`, which costs minutes rather "
              f"than seconds.)", file=sys.stderr)
    videos = sorted(f for f in os.listdir(out_dir)
                    if f.startswith(stem)
                    and os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm"))
    if not videos:
        raise RuntimeError("yt-dlp returned no video file for that link")
    # The merged output is stem.mp4; prefer it over any intermediate a partial
    # merge left with the same prefix, and fall back to a stable sorted choice
    # rather than to whatever os.listdir happened to return first.
    merged = stem + ".mp4"
    chosen = merged if merged in videos else videos[0]
    return os.path.join(out_dir, chosen)


# ---------------------------------------------------------------- words

# Measured on this base image, emulated amd64 on Apple Silicon, 12 Sep 2026:
# `small` transcribes at 0.62x realtime, so 2m18s of source took 1m25s. Fine for
# a campaign archive, which is clips. An hour-long podcast at that rate is 37
# minutes of silence in a chat, which reads as a dead agent.
SMALL_CEILING_S = 900


def pick_model(duration_s, janela=None):
    """The model this source can afford.

    An override always wins: someone who set WARDEN_WHISPER has a reason.

    `janela` é o PROPÓSITO, e desde 15/09/2026 ele decide antes da duração --
    porque a duração nunca foi a pergunta certa. As duas transcrições deste
    projeto querem coisas diferentes:

      - `janela=False`: transcrever a FONTE INTEIRA para escolher o momento.
        Ninguém lê esse texto e nada é queimado a partir dele; ele responde
        "onde vale a pena olhar?". `base` é **5,6x mais rápido** que `small` e
        responde essa pergunta igual de bem.

        MEDIDO em 15/09/2026, no container, os dois modelos sobre o MESMO áudio
        de 120s, `int8`, `beam_size=1`, `cpu_threads` do host, VAD desligado
        para o modelo processar tudo: `base` 15,4s (32 segmentos), `small` 86,9s
        (58 segmentos). Razão 5,64x.

        Até esta medição o comentário aqui dizia "~3x", citando "79s de `small`
        para 2min30s" -- um número que cronometrava SÓ o `small`. O `base` nunca
        tinha sido cronometrado, e a razão era chute com cara de medição, num
        arquivo cuja regra é não afirmar o que não foi medido. Agora são os dois,
        lado a lado, na mesma máquina.
      - `janela=True`: transcrever uma janela JÁ ESCOLHIDA. Este texto vira
        legenda queimada, e legenda queimada errada é um clipe refeito. `small`,
        sempre -- a janela é curta por construção, então o modelo bom cabe.

    E é por isso que o erro de palavra do `base` fica coberto: ele nunca chega à
    tela. O que a pessoa lê na legenda vem SEMPRE da segunda passada, com
    `small`, sobre os segundos que viraram clipe.

    `janela=None` é "não disseram", e aí vale a regra antiga por duração: curto
    ganha `small`, longo ganha `base`. Fica para não mudar a resposta de quem
    chama `pick_model(seconds)` como sempre chamou.
    """
    override = os.environ.get("WARDEN_WHISPER")
    if override:
        return override, "set by WARDEN_WHISPER"
    if janela is True:
        return "small", "a window already chosen, and its words get burned in"
    if janela is False:
        return "base", ("the whole source, only to choose a moment from it: "
                        "5.6x faster (measured 15/09/2026, both models over the "
                        "same 120s of audio: 15.4s against 86.9s), and the "
                        "burned caption comes from the window pass with `small`")
    if duration_s and duration_s > SMALL_CEILING_S:
        return "base", (f"source is {duration_s / 60:.0f} minutes, so the faster "
                        "model, to keep this under ten minutes")
    return "small", "short enough for the better model"


# Quantas threads o faster-whisper pode usar na CPU.
#
# Medido em 15/09/2026: 79s para transcrever 2min30s de áudio. O padrão do
# ctranslate2 quando ninguém diz nada é UMA thread por sessão em boa parte das
# construções, e a imagem tem mais de um núcleo parado. `os.cpu_count()` é o que
# o container enxerga, e o `or 4` cobre a plataforma onde ele devolve None.
#
# O ambiente pode trocar porque o dono pode querer deixar núcleo livre para o
# ffmpeg, que costuma estar rodando ao lado.
def _whisper_threads():
    do_ambiente = os.environ.get("WARDEN_WHISPER_THREADS")
    if do_ambiente:
        try:
            n = int(do_ambiente)
            if n > 0:
                return n
        except ValueError:
            pass                      # um valor sem sentido não derruba a corrida
    return os.cpu_count() or 4


def duration_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def _dimensions(path):
    """(width, height) of the first video stream, as integers.

    Um arquivo SEM stream de vídeo é o caso comum, não o exótico: é exatamente
    o que `archive --text-first` produz de propósito -- só áudio, para o whisper
    ouvir. Medido em 15/09/2026, um desses chegou ao `cut` e a resposta foi
    `ValueError: not enough values to unpack (expected 2, got 1)` embrulhado
    numa frase sobre dimensões. Um crash vazando não diz à pessoa que ela pediu
    para cortar o arquivo errado, nem qual é o certo.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    partes = [p for p in out.split(",")[:2] if p.strip()]
    if len(partes) < 2:
        nome = os.path.basename(path)
        # Três causas diferentes davam a MESMA frase, e ela acusava a errada
        # em duas delas: um arquivo de 0 byte e um mp4 truncado mandavam a
        # pessoa rodar `archive --window`, quando o problema era o download.
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError(
                f"{nome} is an empty file ({0} bytes). Whatever wrote it did "
                f"not finish -- pull it again before cutting.")
        tipos = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path], capture_output=True, text=True).stdout
        if not tipos.strip():
            raise RuntimeError(
                f"{nome} has no readable stream at all: ffprobe finds neither "
                f"picture nor sound in it. It is very likely a truncated or "
                f"corrupt download rather than footage -- pull it again.")
        raise RuntimeError(
            f"{nome} has no video stream -- it is sound only. "
            f"This is what `archive --text-first` writes on purpose, for "
            f"`transcribe` to listen to; it is not footage and there is nothing "
            f"to frame. Pull the picture with `warden archive --window <a>-<b>` "
            f"for the window you chose, and cut THAT file.")
    return int(partes[0]), int(partes[1])


# Tamanho aproximado de cada modelo já convertido para int8, em MB. Serve só
# para dizer "falta X" em vez de "aguarde"; não é usado em decisão nenhuma.
MODEL_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3100}


def model_home():
    return os.environ.get("HF_HOME", "/var/lib/hermes/models")


def _model_bytes(size):
    """Quantos bytes do modelo `size` já estão em disco."""
    home = model_home()
    if not os.path.isdir(home):
        return 0
    alvo = f"faster-whisper-{size}"
    total = 0
    for raiz, dirs, arqs in os.walk(home):
        if alvo not in raiz:
            continue
        for a in arqs:
            try:
                total += os.path.getsize(os.path.join(raiz, a))
            except OSError:
                pass
    return total


def model_status(size=None):
    """O que o baixador de modelos está fazendo, em números.

    Devolve {size: {"state": ..., "mb": n, "want_mb": n, "pct": n}}. `state` é
    `ready`, `fetching`, `failed` ou `absent`.

    Existe porque "ainda baixando" e "nunca baixou" eram a mesma linha no
    `warden status`, e porque o primeiro `warden cut` de uma instalação nova cai
    justo nessa janela: o status fica verde, o dono pede um corte, e o whisper
    começa um download de cinco minutos sem dizer nada. Um silêncio de cinco
    minutos num chat lê como agente morto.
    """
    marcado = {}
    caminho = os.path.join(model_home(), "fetch-state")
    try:
        with open(caminho, encoding="utf-8") as fh:
            for linha in fh:
                if "=" in linha:
                    k, v = linha.strip().split("=", 1)
                    marcado[k] = v          # a última linha de cada chave vence
    except OSError:
        pass
    out = {}
    for nome in ([size] if size else ["small", "base"]):
        mb = _model_bytes(nome) / 1_000_000
        quer = MODEL_MB.get(nome, 500)
        estado = marcado.get(nome)
        if mb >= quer * 0.95:
            estado = "ready"
        elif estado not in ("fetching", "failed"):
            estado = "fetching" if mb > 0 else "absent"
        out[nome] = {"state": estado, "mb": round(mb), "want_mb": quer,
                     "pct": min(99, int(mb * 100 / quer)) if estado != "ready" else 100}
    return out


def model_wait_note(size):
    """A frase que o `cut` diz em vez de travar em silêncio, ou None se pronto."""
    info = model_status(size).get(size, {})
    if info.get("state") == "ready":
        return None
    falta = max(0, info.get("want_mb", 0) - info.get("mb", 0))
    if info.get("state") == "fetching":
        return (f"the {size} transcription model is still downloading: "
                f"{info['mb']} of {info['want_mb']} MB ({info['pct']}%), "
                f"{falta} MB to go. It runs in the background at boot. Wait and "
                f"try again, or transcribe with a model that is already here "
                f"(`warden transcribe <file> --model tiny`), or take the wait "
                f"on purpose with WARDEN_WAIT_FOR_MODEL=1.")
    if info.get("state") == "failed":
        return (f"the {size} model failed to download at boot. This request "
                f"would fetch {info['want_mb']} MB now, which is a long silence "
                f"in a chat -- END A TURN saying so, and wait on the next: "
                f"prose between two tool calls does not reach them.")
    return (f"the {size} transcription model is not on this machine yet "
            f"({info.get('want_mb', 0)} MB). It downloads in the background "
            f"after install; this request would fetch it now.")


def transcribe(path, model_size=None, window=None, prefer_lang=None,
               progress=None, proposito=None):
    """Words with timing. A published subtitle beats a transcription.

    When the archive shipped subtitles, using them is not a shortcut, it is the
    better text: it is what the rights holder wrote, and it costs nothing.

    MENOS quando elas estão em OUTRA LÍNGUA que o vídeo, e aí elas não são o
    texto do detentor dos direitos: são tradução de máquina em cima de
    transcrição de máquina. Desde 16/09/2026 (decisão do dono, e um clipe em
    português queimado sobre um vídeo `en-US`) esse caso vai para o whisper, na
    língua da fonte. Ver `_fora_da_lingua_da_fonte`.

    `window` is (start, end) in source seconds: only that slice is transcribed,
    and the timings come back on the SOURCE's clock so the rest of the pipeline
    does not have to know. That is the second pass of the two-pass plan -- a
    23-minute source scanned with `tiny` to find the candidates, then the good
    model on the two minutes that actually become clips, instead of 23 minutes
    of the good model to use forty seconds of it.

    `proposito` diz PARA QUE esta transcrição serve, e é o que escolhe o modelo
    quando ninguém passou `model_size`:

      - `"fonte"`: a fonte inteira, só para escolher o momento -> `base`.
      - `"janela"`: uma janela já escolhida, cujo texto vira legenda -> `small`.
      - `None`: não disseram, e vale a regra de sempre (janela -> `small`,
        senão pela duração). Está aqui para não mudar a resposta de nenhum
        chamador que já existe.
    """
    say = progress or (lambda line: print(line, file=sys.stderr, flush=True))
    # A língua da FONTE, como fato: o `--lang` de quem pediu, ou a anotação que
    # o download deixou ao lado. É ela que decide se a legenda publicada serve
    # e, quando não serve, em que língua o whisper ouve.
    lingua_marcada = _lingua_marcada(path)
    lingua_da_fonte = None
    if prefer_lang:
        lingua_da_fonte = str(prefer_lang[0] or "") or None
    lingua_da_fonte = lingua_da_fonte or lingua_marcada
    if window is None:
        sidecar, lang = _subtitle_beside(path, prefer=prefer_lang)
        # A recusa mede contra a língua ANOTADA NO DOWNLOAD, não contra
        # `prefer_lang`: `--lang` é o dono falando, e ele decide -- inclusive
        # decidir por uma legenda que não é a da fonte. O que ele não pode é
        # receber essa troca sem ter pedido, que é o defeito de 16/09/2026.
        fora = (_fora_da_lingua_da_fonte(lang, lingua_marcada)
                if sidecar and not prefer_lang else None)
        # Traduzida, e existe áudio para ouvir: o whisper na língua da fonte
        # custa minutos e é o texto certo; a tradução custa zero e é o texto de
        # outro vídeo. Ver `_fora_da_lingua_da_fonte` para o caso medido em
        # 16/09/2026, em que o clipe saiu em português sobre um vídeo `en-US`.
        #
        # Quando o próprio `path` É a legenda (o caminho `--text-first` devolve
        # o `.srt` como fonte) não há áudio aqui para transcrever. Aí ela volta
        # como está, MARCADA -- quem chama decide, e é por isso que o campo
        # existe em vez de uma recusa: recusar aqui deixaria o comando sem
        # texto nenhum, e o texto ainda serve para escolher o momento.
        da_para_ouvir = not str(path).lower().endswith((".srt", ".vtt"))
        if fora and da_para_ouvir:
            say(f"warden: {fora}. Listening to the audio in "
                f"{lingua_da_fonte} instead of burning the translation.")
            sidecar = None
        if sidecar:
            # `language_measured` diz se a língua da FONTE foi lida do material
            # ou se saiu de desempate. Quem imprime a frase para o dono precisa
            # disto: "a legenda está em X porque essa é a língua do vídeo" só
            # pode ser dito quando alguém leu a língua do vídeo. Em 15/09/2026
            # essa frase foi impressa sobre um vídeo em inglês com legenda
            # auto-traduzida para pt-BR, e a mentira tinha cara de medição.
            medida = bool(prefer_lang) or lingua_marcada is not None
            return {"source": f"published subtitles ({lang or 'no language tag'})",
                    "path": sidecar, "language": lang,
                    "language_measured": medida,
                    "source_language": lingua_marcada,
                    # O SINAL. Presente só quando esta legenda não está na
                    # língua da fonte, e com o porquê escrito: quem for
                    # aprová-la automaticamente tem de poder perguntar antes.
                    "fora_da_lingua_da_fonte": fora,
                    "segments": _read_srt(sidecar)}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "no subtitles beside this file and faster-whisper is not installed, "
            "so there is no text to choose a moment from")
    seconds = duration_of(path)

    audio, offset, span = path, 0.0, seconds
    clean_up = None
    if window:
        lo, hi = float(window[0]), float(window[1])
        offset, span = lo, max(0.1, hi - lo)
        # Só o trecho, como wav de 16k mono: é o que o whisper quer e é uma
        # fração do arquivo. Extrair custa segundos; transcrever o resto custa
        # minutos.
        import tempfile as _tf
        fd, audio = _tf.mkstemp(suffix=".wav")
        os.close(fd)
        clean_up = audio
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{lo:.3f}", "-i", path,
             "-t", f"{span:.3f}", "-vn", "-ac", "1", "-ar", "16000", audio],
            TIMEOUT_RENDER, "ffmpeg (window for transcription)")

    if model_size:
        size, why = model_size, "asked for"
    elif proposito == "janela":
        size, why = pick_model(seconds, janela=True)
    elif proposito == "fonte":
        size, why = pick_model(seconds, janela=False)
    elif window:
        # A janela é curta por construção, então o modelo bom cabe nela sempre.
        size, why = "small", f"only the {span / 60:.1f} minutes of this window"
    else:
        size, why = pick_model(seconds)

    try:
        # O modelo pode não estar aqui ainda: numa instalação nova o baixador de
        # fundo ainda está correndo quando o `warden status` já ficou verde.
        # Antes disto, o WhisperModel() simplesmente começava um download de
        # cinco minutos sem uma linha na tela.
        espera = model_wait_note(size)
        if espera:
            if os.environ.get("WARDEN_WAIT_FOR_MODEL") == "1":
                say(f"warden: {espera}")
            else:
                raise RuntimeError(espera)
        say(f"transcribing {span / 60:.1f} minutes with faster-whisper {size} "
            f"({why})...")
        # `cpu_threads`: sem isto o ctranslate2 fica com o padrão dele, que na
        # prática é uma thread trabalhando e os outros núcleos da imagem
        # parados. Medido em 15/09/2026: 79s para 2min30s de áudio.
        model = WhisperModel(size, device="cpu", compute_type="int8",
                             cpu_threads=_whisper_threads())
        # `beam_size=1` é busca gulosa. O padrão do faster-whisper é 5, ou seja,
        # cinco hipóteses por passo -- e este projeto não usa nada que dependa
        # dessa margem: o texto da fonte serve para escolher o momento, e o
        # texto da janela é conferido por quem olha o contact sheet antes de
        # publicar.
        #
        # E NÃO ligamos `batch_size=8`. O benchmark oficial do faster-whisper
        # mede 3608 MB de pico com pipeline em lote, e o compose desta imagem
        # declara `mem_limit: 3g`. 3608 > 3072: o ganho de velocidade seria um
        # OOM kill no meio da transcrição, que é a falha mais cara que existe
        # aqui -- silenciosa, e depois de já ter gasto o tempo todo.
        # `language`: o whisper transcreve NA LÍNGUA DA FONTE quando ela foi
        # lida, em vez de detectar sozinho. Detectar não é de graça -- ele
        # decide por uns segundos de áudio, e um vídeo em inglês que começa com
        # vinheta ou com uma saudação em outra língua sai transcrito errado do
        # primeiro segmento em diante. A língua da fonte é fato sobre o link, e
        # este caminho só existe porque a legenda publicada nessa língua não
        # estava lá.
        pedida = (lingua_da_fonte or "").split("-")[0].split("_")[0].lower() or None
        try:
            segments, _info = model.transcribe(audio, vad_filter=True,
                                               word_timestamps=True,
                                               beam_size=1, language=pedida)
        except ValueError:
            # Uma tag que o whisper não conhece (o campo vem do outro lado e
            # nada garante que seja uma das línguas dele) não pode matar uma
            # transcrição que já custou o download. Ele detecta, e o campo
            # `language` abaixo deixa de afirmar o que não foi imposto.
            if not pedida:
                raise
            say(f"warden: faster-whisper does not know the language "
                f"{pedida!r}, so it will detect one instead.")
            pedida = None
            segments, _info = model.transcribe(audio, vad_filter=True,
                                               word_timestamps=True,
                                               beam_size=1)
        rows = []
        # Dez minutos de silêncio num chat lê como agente morto. O whisper
        # devolve um gerador, então dá para contar o que já saiu enquanto sai --
        # uma linha por minuto de áudio processado, nem mais nem menos.
        mark = 0.0
        for s in segments:
            rows.append({"start": round(s.start + offset, 2),
                         "end": round(s.end + offset, 2),
                         "text": s.text.strip()})
            if s.end - mark >= 60:
                mark = s.end
                say(f"  {mark / 60:.0f} of {span / 60:.0f} minutes, "
                    f"{len(rows)} segments so far")
        say(f"  done: {len(rows)} segments")
    finally:
        if clean_up:
            try:
                os.remove(clean_up)
            except OSError:
                pass
    return {"source": f"faster-whisper {size} ({why})", "path": None,
            "segments": rows, "duration_s": seconds,
            # A língua sai daqui só quando ela foi LIDA e IMPOSTA ao modelo --
            # não a detectada. Quem lê este campo escreve o gancho com ele, e
            # `cut` recusa gancho e legenda em línguas diferentes: repassar um
            # palpite seria a mesma afirmação sem medição que o `prep` teve o
            # cuidado de não fazer.
            "language": pedida,
            "language_measured": bool(pedida),
            "source_language": lingua_marcada,
            "window": list(window) if window else None}


def scan(path, progress=None):
    """A passada barata sobre a fonte inteira, só para localizar candidatos.

    `tiny` erra palavra e não presta para queimar -- e não é para isso. É para
    responder "onde neste podcast de 23 minutos vale a pena olhar?", e para essa
    pergunta ele serve, a uma fração do custo. As janelas escolhidas voltam pelo
    `transcribe(window=...)` com o modelo bom.
    """
    return transcribe(path, model_size="tiny", progress=progress)


# A ordem em que uma legenda publicada é preferida. `archive` baixa com
# `SUB_LANGS` mais a língua original do vídeo (ver `_sub_langs_para`), então
# vários arquivos podem existir lado a lado.
#
# Esta lista é de PREFERÊNCIA, não de permissão, e continua curta de propósito.
# Desde 15/09/2026 a língua original entra no pedido, então um vídeo em espanhol
# pode deixar um `.es.srt` aqui: se for o único, `_subtitle_beside` o devolve --
# é a legenda do vídeo. Se houver mais de uma e nenhuma conhecida, ele PARA e
# pede `--lang`, que é o conserto de quando o inglês foi queimado sobre um hook
# em português. Escolher por ordem alfabética é o que não pode voltar.
SUBTITLE_LANGS = ("pt-br", "pt_br", "pt-BR", "pt", "en")

# Uma tag de idioma no NOME de um arquivo de legenda: `source-abc.pt-BR.srt`.
_TAG_IDIOMA = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8}){0,2}$")


def _tag_do_nome(nome):
    """`source-abc.pt-BR.srt` -> `pt-br`. None quando não há tag no nome.

    Medido em 15/09/2026 na imagem construída: quando o caminho barato devolve
    a legenda COMO fonte -- `SOURCE_TEXT=source-abc.en.srt`, que é o que
    `--text-first` faz --, `transcribe` dizia "published subtitles (no language
    tag)" com a tag `en` escrita no nome do arquivo que ela acabara de abrir.

    Isso não é cosmético. Essa linha é a única coisa que diz ao modelo em que
    idioma está o texto que ele vai queimar, e sem ela ele monta um hook em
    português sobre legenda em inglês -- que é o que `cut` recusa, e que já saiu
    publicado uma vez.

    O filtro é de FORMATO: `orig` (quatro letras) não é tag, `0` (de `122.0`)
    não é tag, e um nome sem ponto nenhum não tem tag.
    """
    raiz = os.path.splitext(os.path.basename(nome or ""))[0]
    if "." not in raiz:
        return None
    candidato = raiz.rsplit(".", 1)[-1]
    return candidato.lower() if _TAG_IDIOMA.fullmatch(candidato) else None


def _prefere_idioma(caminhos, prefer=None):
    """Ordena legendas pela LÍNGUA DO VÍDEO, nunca por nome de arquivo.

    A regra, decidida pelo dono em 15/09/2026: *o idioma da legenda tem que ser
    o mesmo que a linguagem do vídeo disponibilizado*. Não é "preferir
    português" -- essa era a regra antiga e ela estava errada como padrão. Um
    vídeo em inglês quer legenda em inglês; em espanhol, espanhol.

    `prefer` é a língua da fonte (de `lingua_do_video` ou do `.lingua` anotado),
    e é a primeira escolha. Uma variante regional casa pela raiz: `pt` aceita
    `pt-BR`, `en` aceita `en-US`. Só depois disso entra qualquer outra que
    exista -- e `_conta_a_legenda` diz em voz alta que caiu no plano B, porque
    legenda fora da língua da fonte é tradução de máquina em cima de
    transcrição de máquina.

    Sem língua conhecida, o desempate cai em `SUBTITLE_LANGS`, que continua
    sendo uma preferência razoável para este dono -- mas é o ÚLTIMO recurso,
    não a regra.

    LEIA ANTES DE SIMPLIFICAR. Este projeto já consertou este bug uma vez, em
    `_subtitle_beside`, e deixou o mesmo defeito de pé em `_pull_subs`, ao lado
    -- onde ele voltou a custar clipe.

    Medido em 15/09/2026, seis execuções de `lote prep` no MESMO link, cada uma
    num WARDEN_DIR limpo, mesma máquina e mesma rede: o idioma da legenda
    escolhida saiu ALEATÓRIO entre as rodadas -- `pt-br` em quatro, `en` em
    duas. A causa não é sorteio: `sorted()` sobre os nomes põe
    `source-abc.en.srt` antes de `source-abc.pt-BR.srt`, então sempre que o
    `en` conseguia baixar, ele ganhava. O que variava entre as rodadas era
    QUAIS variantes chegavam antes do 429 -- e a escolha seguia a ordem de
    CHEGADA, não a preferência.

    O custo é o clipe inteiro, não um detalhe de idioma: o padrão do dono é
    hook em português, e `cut` RECUSA queimar hook e legenda em idiomas
    diferentes. Essa regra está certa e não se toca -- "A Portuguese hook over
    an English caption went out once" --, o conserto é não chegar lá misturado.
    Duas rodadas em seis entregavam zero clipe.

    Por isso a ordenação acontece DEPOIS de saber o que chegou, sobre o que
    existe em disco. O desempate final é pelo nome -- não porque o nome
    signifique alguma coisa, mas porque duas execuções iguais têm de dar o
    mesmo resultado.
    """
    pedidas = [str(p).lower() for p in (prefer or []) if p]
    ordem = list(pedidas)
    ordem += [l.lower() for l in SUBTITLE_LANGS if l.lower() not in ordem]
    n = len(pedidas)

    def _raiz(tag):
        return tag.split("-")[0].split("_")[0]

    def _chave(caminho):
        nome = os.path.basename(caminho)
        tag = _tag_do_nome(caminho)
        if tag is None:
            # Sem tag no nome: nem preferida nem recusada. Fica depois das
            # conhecidas e antes de um idioma que ninguém pediu.
            return (n + len(ordem), "", nome)
        # A LÍNGUA DA FONTE ANTES DE TUDO, e pela raiz -- inclusive antes de um
        # acerto exato no desempate. Esta separação em dois blocos é de
        # 16/09/2026 e quem a encontrou foi um teste, não um clipe: com
        # `prefer=['en-US']` e os arquivos `.en.srt` e `.pt.srt` lado a lado,
        # `en` casava exatamente com o último item de `SUBTITLE_LANGS` (índice
        # 4) e `pt` com o penúltimo (índice 3), então a TRADUÇÃO vencia a
        # legenda da própria fonte. A lista única misturava "a língua do vídeo"
        # com "a preferência deste dono", e `en-US` -- que é como o YouTube
        # marcou o vídeo do pedido de hoje -- não casava exatamente com nada.
        #
        # Importa mais agora do que antes: desde hoje uma tradução escolhida
        # aqui não é mais queimada, é RECUSADA, então perder a legenda inglesa
        # que estava em disco mandaria transcrever de graça.
        for i, pedida in enumerate(pedidas):
            if pedida == tag or _raiz(pedida) == _raiz(tag):
                return (i, tag, nome)
        if tag in ordem:
            return (n + ordem.index(tag), "", nome)
        # Variante regional conta pela raiz: `pt-PT` vale como `pt`. É assim que
        # um vídeo em espanhol usa a legenda em espanhol dele sem que `es-419`
        # precise estar escrito em lugar nenhum.
        for i, conhecido in enumerate(ordem):
            if _raiz(conhecido) == _raiz(tag):
                return (n + i, tag, nome)
        return (n + len(ordem) + 1, tag, nome)

    return sorted(caminhos, key=_chave)


def _subtitle_beside(path, prefer=None):
    """A legenda publicada ao lado do vídeo, na língua certa, ou None.

    Aqui estava um defeito silencioso e caro. A busca era `sorted(...)` e ficava
    com o PRIMEIRO arquivo em ordem alfabética: com `source-abc.en.srt` e
    `source-abc.pt.srt` no mesmo diretório -- que é o par que `archive` baixa --
    `.en` vem antes de `.pt` e o inglês ganhava sempre. É a explicação mais
    provável da legenda em inglês queimada num clipe cujo hook estava em
    português, e é um conserto de dez linhas.

    Devolve (caminho, língua) para que quem chama possa dizer qual escolheu.

    O CASO EM QUE O PRÓPRIO ARQUIVO É A LEGENDA vem primeiro, e é um conserto de
    15/09/2026. O caminho barato devolve o `.srt` COMO fonte -- é o que
    `--text-first` faz de propósito --, e aí `path` já é `source-abc.en.srt`. O
    código abaixo então montava `base = "source-abc.en"`, encontrava o próprio
    arquivo, calculava a tag como a sobra depois do `base` (vazia) e respondia
    "sem língua no nome" com `en` escrito no nome que ele acabara de ler. A
    linha que o modelo lê para saber em que idioma está o texto que vai queimar
    dizia "no language tag" justamente quando havia tag.
    """
    if str(path).lower().endswith((".srt", ".vtt")) and os.path.isfile(path):
        return path, _tag_do_nome(path)
    # A língua da FONTE decide, e ela foi anotada no download. Sem `prefer`
    # explícito (`--lang`), é ela que manda -- não uma ordem fixa.
    sabemos_a_lingua = True
    if not prefer:
        marcada = _lingua_marcada(path)
        if marcada:
            prefer = [marcada]
        else:
            sabemos_a_lingua = False
    stem = os.path.splitext(path)[0]
    folder = os.path.dirname(path) or "."
    base = os.path.basename(stem)
    found = []
    for name in sorted(os.listdir(folder)):
        if not name.startswith(base):
            continue
        if not name.lower().endswith((".srt", ".vtt")):
            continue
        # `source-abc.pt.srt` -> `pt`; `source-abc.srt` -> sem língua no nome.
        # A extensão sai primeiro: sem isso `source-abc.srt` dava a língua "srt".
        tag = os.path.splitext(name)[0][len(base):].lstrip(".").lower()
        found.append((os.path.join(folder, name), tag or None))
    if not found:
        return None, None
    # A MESMA ordenação que `_pull_subs` usa, e ela é a mesma de propósito: as
    # duas escolhem legenda, e ter duas regras de preferência foi como o defeito
    # consertado aqui continuou de pé lá. Ver `_prefere_idioma`.
    por_caminho = dict(found)
    ordenadas = _prefere_idioma([p for p, _l in found], prefer=prefer)
    if not sabemos_a_lingua and len([p for p in ordenadas if _tag_do_nome(p)]) > 1:
        # NÃO escolher português em silêncio. Medido em 15/09/2026: sem o
        # `.lingua` em disco, esta função caía em `SUBTITLE_LANGS`, cujo
        # primeiro item é `pt-br` -- e um vídeo em inglês saiu com legenda
        # auto-traduzida e a frase "that is the language of the video", que era
        # falsa. O último recurso tinha virado a regra, num lugar onde ninguém
        # olhava.
        #
        # O desempate continua existindo, porque parar aqui deixaria o dono sem
        # clipe nenhum. O que muda é que ele não passa por medição: quem consome
        # é avisado, em voz alta, de que a língua da fonte NÃO foi lida.
        print(f"  (warden did not read what language "
              f"{os.path.basename(path)} is in -- the `.lingua` note from the "
              f"download is not beside it. Subtitles on disk: "
              f"{', '.join(sorted(t for _p, t in found if t))}. Falling back to "
              f"{_tag_do_nome(ordenadas[0])!r} by preference order, NOT by "
              f"measurement. Do not tell anyone this is the video's language; "
              f"if the hook matters, pass --lang.)", file=sys.stderr)
    for caminho in ordenadas:
        lang = por_caminho.get(caminho)
        if lang and _tag_do_nome(caminho):
            return caminho, lang
    if len(found) == 1:
        return found[0]
    # Mais de uma legenda e nenhuma nas línguas que sabemos comparar. Escolher a
    # primeira em ordem alfabética é o mecanismo exato que queimou inglês sobre
    # um hook em português; o gate de idioma só distingue pt/en/es, então um
    # `.fr` passaria batido. Quem escolhe é o dono, com --lang.
    tags = ", ".join(sorted(str(l) for _p, l in found))
    raise RuntimeError(
        f"{os.path.basename(path)} has subtitles in {tags} and none in "
        f"{', '.join(SUBTITLE_LANGS)}. Pick one with --lang: choosing "
        f"alphabetically is how an English caption ended up under a Portuguese "
        f"hook.")


def _read_srt(path):
    def seconds(stamp):
        # Um timestamp de VTT vem seguido dos parâmetros da cue --
        # `00:00:02.629 align:start position:0%` -- e o `split(":")` disso
        # devolvia seis pedaços contra três esperados. `archive` baixa VTT por
        # padrão, então este era o formato mais provável de chegar aqui, e ele
        # quebrava o `transcribe` inteiro com um ValueError.
        stamp = stamp.replace(",", ".").strip().split()[0]
        hours, minutes, rest = stamp.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)
    rows, block = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for line in lines + [""]:
        if line.strip():
            block.append(line)
            continue
        stamps = [l for l in block if "-->" in l]
        if stamps:
            start, end = [s.strip() for s in stamps[0].split("-->")]
            # O filtro antigo era `not l.strip().isdigit()`, para tirar o número
            # do índice do SRT, e comia qualquer linha do corpo que fosse só
            # dígitos. Reproduzido: as cues "custou" / "5000" / "reais por mês"
            # voltavam como ['custou', 'reais por mes'] -- a cue do meio sumia
            # inteira, sem uma linha em lugar nenhum. O índice é POSIÇÃO, não
            # conteúdo: ele é o que vem antes do timestamp, e VTT nem tem
            # índice, então ali o filtro só fazia mal.
            head = block.index(stamps[0])
            body = " ".join(l for l in block[head + 1:] if "-->" not in l)
            # As entidades saem ANTES das tags: uma legenda do YouTube traz
            # `[&nbsp;__&nbsp;]` onde censurou um palavrão e `&gt;&gt;` onde
            # marcou quem fala. Queimadas cruas, é isso que aparece na tela.
            body = html.unescape(body)
            body = re.sub(r"<[^>]+>", "", body)
            # `\h` (espaço duro) vira espaço comum, senão ele fica no meio da
            # frase e a contagem de caracteres por linha mente.
            body = " ".join(body.replace("\xa0", " ").split()).strip()
            if body:
                rows.append({"start": round(seconds(start), 2),
                             "end": round(seconds(end), 2), "text": body})
        block = []
    return _undo_rollup(rows)


def _undo_rollup(rows):
    """Desfaz a legenda rolante das transcrições automáticas do YouTube.

    Elas não entregam uma cue por fala: entregam a linha anterior mais as
    palavras novas, de novo e de novo, e ainda repetem a mesma cue duas vezes
    com dez milissegundos de diferença. Lido cru, um vídeo de 23 minutos vira um
    transcript em que cada frase aparece três vezes -- e o digest que o modelo lê
    para escolher a janela fica três vezes maior e ilegível.

    Cada cue fica só com o que ela acrescenta à anterior. Uma cue que não
    acrescenta nada desaparece, e seu tempo estende a anterior, para que a fala
    não perca duração no caminho.
    """
    out = []
    for row in rows:
        text = " ".join(str(row.get("text") or "").split())
        if not text:
            continue
        if out:
            previous = out[-1]["text"]
            if text == previous:
                out[-1]["end"] = max(out[-1]["end"], row["end"])
                continue
            if text.startswith(previous + " "):
                novo = text[len(previous):].strip()
                if not novo:
                    out[-1]["end"] = max(out[-1]["end"], row["end"])
                    continue
                text = novo
            elif previous.startswith(text + " "):
                # a cue nova é um prefixo da anterior: não acrescenta nada
                out[-1]["end"] = max(out[-1]["end"], row["end"])
                continue
        out.append({"start": row["start"], "end": row["end"], "text": text})

    # A legenda rolante ainda deixa cues de 10 milissegundos: são os pontos em
    # que o YouTube "fecha" uma linha que foi falada antes, no vão silencioso
    # desde a cue anterior. Mantidas como estão, a frase pisca por 10ms e o
    # espectador nunca a lê -- é conteúdo perdido, não um detalhe de timing. O
    # começo volta para onde a anterior acabou, que é quando aquilo foi dito.
    for i, row in enumerate(out):
        if row["end"] - row["start"] >= 0.3:
            continue
        antes = out[i - 1]["end"] if i else 0.0
        if antes < row["end"]:
            row["start"] = antes
    return [r for r in out if r["end"] - r["start"] > 0.05]


def _srt_stamp(t):
    if t is None or t < 0:
        t = 0.0
    ms = int(round(float(t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_stamp(t):
    cs = int(round(max(0.0, float(t)) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


_TEM_FILTRO = {}


def ffmpeg_tem_filtro(nome):
    """Se este ffmpeg tem o filtro `nome`. Perguntado ao binário, não suposto.

    A legenda passou a ser ASS queimado pelo libass, e libass é uma opção de
    compilação: o ffmpeg da imagem tem (`--enable-libass`), o do Homebrew de um
    Mac normalmente não. Descobrir isso com o clipe pronto e sem legenda é tarde
    demais, e supor que tem é como o `force_style` nunca renderizou nada.
    """
    if nome in _TEM_FILTRO:
        return _TEM_FILTRO[nome]
    try:
        saida = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                               capture_output=True, text=True, timeout=60).stdout
    except Exception:
        saida = ""
    _TEM_FILTRO[nome] = bool(re.search(rf"^\s*\S+\s+{re.escape(nome)}\s+", saida,
                                       re.M))
    return _TEM_FILTRO[nome]


def _ff_valor(texto):
    """Um caminho seguro para ir dentro de uma opção de filtro do ffmpeg.

    O filtergraph corta em `,` `;` `[` `]` e a opção corta em `:`. Um caminho com
    qualquer um deles vira dois filtros inventados e uma mensagem de erro que não
    fala de caminho nenhum.
    """
    saida = str(texto).replace("\\", "\\\\")
    for c in (":", "'", ",", ";", "[", "]"):
        saida = saida.replace(c, "\\" + c)
    return saida


def _k_texto(cue, visivel_de=None, visivel_ate=None):
    """O texto da cue com um `\\k` por palavra, ou None se os tempos não vierem.

    `\\k<centésimos>` é o que faz o destaque andar palavra a palavra: o libass
    pinta a palavra de SecondaryColour para PrimaryColour quando o relógio dela
    chega. Os centésimos vêm do reflow, repartidos por sílaba.

    `visivel_de`/`visivel_ate` são a janela da cue que SOBRA depois do recorte,
    no tempo da própria cue, e existem por um defeito medido. Um corte que
    começa no meio de um segmento faz a primeira cue ser cortada na cabeça: ela
    fica 1,4s na tela carregando 2,9s de `\\k`. O libass conta o `\\k` a partir
    do início do Dialogue, então o amarelo andava um segundo e meio atrás da
    boca e três das oito palavras nunca acendiam antes de a cue sair.

    Palavra que já foi dita antes da janela sai com `\\k0`, que o libass pinta
    no primeiro quadro: ela JÁ está amarela quando a cue aparece, que é a
    verdade -- aquela palavra já foi falada. A soma dos `\\k` passa a ser a
    duração visível, e `soma_k` existe para um teste conferir isso.

    Devolve None -- e não um texto pela metade -- quando a contagem de palavras
    dos tempos não bate com a das linhas. Uma legenda com palavra faltando é
    exatamente o tipo de perda silenciosa que este arquivo inteiro combate, e
    quem chama trata o None dizendo o que aconteceu.
    """
    palavras = list(cue.get("words") or [])
    linhas = [l for l in (cue.get("lines") or []) if l.strip()]
    if not palavras or not linhas:
        return None
    if sum(len(l.split()) for l in linhas) != len(palavras):
        return None
    de = float(cue.get("start", 0.0)) if visivel_de is None else float(visivel_de)
    ate = float(cue.get("end", 0.0)) if visivel_ate is None else float(visivel_ate)
    saida, i = [], 0
    for linha in linhas:
        pedaco = []
        for _ in linha.split():
            p = palavras[i]
            i += 1
            # A fatia desta palavra que cai DENTRO do que vai aparecer.
            a = max(float(p["start"]), de)
            b = min(float(p["end"]), ate)
            centesimos = max(0, int(round((b - a) * 100)))
            pedaco.append("{\\k%d}%s" % (centesimos, _ass_limpo(p["w"])))
        saida.append(" ".join(pedaco))
    return "\\N".join(saida)


def soma_k(texto_ass):
    """Os centésimos de `\\k` somados, para quem precisa conferir a sincronia."""
    return sum(int(n) for n in re.findall(r"\{\\k(\d+)\}", str(texto_ass or "")))


def _ass_limpo(texto):
    """Um `\\` abre tag e um `{` abre bloco de override. Nenhum dos dois é fala."""
    return " ".join(str(texto).replace("\\", "").replace("{", "(")
                    .replace("}", ")").split())


def to_ass(segments, width, height, safe=None, offset=0.0, length=None,
           perdas=None):
    """Um transcript vira ASS: reflow primeiro, depois `ass_from_cues`.

    Continua sendo a porta de entrada para quem tem segmentos crus na mão. Quem
    já reflowou -- o `cut`, que precisa das mesmas cues para contar linhas e
    finais pendurados -- chama `ass_from_cues` direto, para não reflowar duas
    vezes e correr o risco de as duas passadas discordarem.
    """
    return ass_from_cues(S.reflow_cues(segments), width, height, safe=safe,
                         offset=offset, length=length, perdas=perdas)


def ass_from_cues(cues, width, height, safe=None, offset=0.0, length=None,
                  perdas=None):
    """The caption as an ASS file, with the style baked into the file itself.

    Not force_style on the subtitles filter: that option takes comma-separated
    fields, and a filtergraph treats a comma as the end of a filter. Measured on
    the image's ffmpeg, every escaping of a multi-field force_style either errors
    or renders NOTHING at all -- which is why a burned caption never actually
    appeared. An ASS style line carries its own commas inside the file, where the
    filtergraph never sees them, so libass reads size, outline, alignment and the
    safe-area margins with nothing to escape.

    `offset` and `length` place the words on the CLIP's timeline, not the source's.
    A clip is cut with `-ss` seeking the source, so the render starts at 0 while
    the transcript's timestamps are still the source's -- burn them unshifted and
    every line shows at the wrong second, two at once, over the wrong shot. So each
    cue is moved back by `offset` (the cut's start), kept only if it overlaps the
    window, and clamped to `[0, length]`. The words come from the transcript; when
    they show, and that they stay on screen, is decided here.
    """
    safe = safe or {}
    x0 = int(safe.get("x0", 86))
    x1 = int(safe.get("x1", width - 140))
    y1 = int(safe.get("y1", int(height * 0.83)))
    # height // 26, não // 22. O antigo dava 87px num quadro de 1920, grande
    # demais para duas linhas de fala, e foi parte do bloco que cobriu o rosto.
    fontsize = S.caption_size(height)
    outline = 3
    margin_l = max(10, x0)
    margin_r = max(10, width - x1)
    # O piso da legenda não pode depender só do `y1` das regras. Ele vem em
    # pixels da resolução DECLARADA, e num render 720x1280 com o padrão de
    # 1586 a conta dá negativa: o `max(10, ...)` então punha a legenda a 10px
    # do fundo, dentro da interface do app, e nada acusava. E mesmo em
    # 1080x1920 o padrão deixava a legenda terminando a ~60px do começo da
    # interface de baixo -- dentro da regra, encostada nela. O piso passa a
    # ser a margem MAIS a folga, escalado por este quadro; `y1` só pode subir
    # a legenda, nunca baixá-la para dentro da faixa do app.
    margin_v = max(S.caption_piso(height), height - y1)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        # WrapStyle 0: wrap long lines inside the margins. WrapStyle 2 (no wrap)
        # let a real transcript line run off the right of the frame.
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        # Anton, não Arial. `Arial` não está instalada na imagem e o libass caía
        # numa fallback genérica de peso errado -- é o defeito 2.3, e a fonte
        # está no repo (`warden-shared/assets`) justamente para acabar com isso.
        # Quem passar este ASS ao filtro `subtitles` tem de passar também
        # `fontsdir` apontando para essa pasta, senão a fallback volta.
        # A sombra sai de 0 para 1: peso separado do contorno, como no PRIME.
        # PrimaryColour é a palavra JÁ falada e SecondaryColour a que ainda
        # vem: é assim que o `\\k` pinta, e é o contrário do que os nomes
        # sugerem. Amarelo depois, branco antes -- escolha do dono em 14/09.
        f"Style: Default,Anton,{fontsize},{S.COR_FALADA},{S.COR_POR_FALAR},"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,"
        f"{margin_l},{margin_r},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n")
    offset = float(offset or 0.0)
    rows = []
    sem_k = []
    for seg in cues or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start, end = seg.get("start"), seg.get("end")
        if start is None or end is None:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0):
            continue
        start -= offset
        end -= offset
        if length is not None:
            if end <= 0 or start >= float(length):     # outside the clip window
                continue
            end = min(end, float(length))
        start = max(0.0, start)
        if end <= start:
            continue
        # O destaque palavra a palavra. Quando os tempos vieram, cada palavra
        # entra com o seu `\\k`; quando não vieram, a cue entra inteira e é
        # FORÇADA a branco -- sem `\\k` o libass pinta a linha toda de
        # PrimaryColour, que aqui é o amarelo do "já falado", e a cue sairia
        # amarela do início ao fim sem ninguém pedir.
        # O recorte acima mudou a janela da cue; o `\k` tem de ser recalculado
        # nela, senão o destaque anda atrás da voz pelo tanto que foi cortado.
        corpo = _k_texto(seg, visivel_de=start + offset, visivel_ate=end + offset)
        if corpo is None:
            sem_k.append(text[:40])
            linhas = [_ass_limpo(l) for l in (seg.get("lines") or [text])]
            corpo = ("{\\c%s&}" % S.COR_POR_FALAR.rstrip("&")
                     + "\\N".join(l for l in linhas if l))
        rows.append((start, end, corpo))
    rows.sort(key=lambda r: r[0])
    body = "".join(
        f"Dialogue: 0,{_ass_stamp(a)},{_ass_stamp(b)},Default,,0,0,0,,{t}\n"
        for a, b, t in rows)
    if sem_k and perdas is not None:
        perdas.append(f"{len(sem_k)} cue(s) came out with no word-by-word "
                      f"highlight, because the per-word times did not line up "
                      f"with the lines: {'; '.join(sem_k[:3])}")
    return header + body if rows else ""


def to_srt(segments):
    """An SRT from a transcript, carrying only lines a viewer could actually read.

    Whisper is not a court reporter. It returns empty segments, segments with no
    timing, and now and then a word it simply got wrong -- and a wrong word burned
    on the screen is worse than no caption, because the clipper cannot see it is
    wrong until a viewer does. This tool cannot tell a wrong word from a right one;
    that judgement stays with the person who can check it against the video. What
    it CAN do is refuse to burn what is structurally unusable: a blank line, a line
    with no start or end, a line whose end is not after its start. Those are
    dropped, the rest are renumbered so there is no gap, and an empty result comes
    back as an empty string -- nothing to burn, said by its emptiness.
    """
    rows = []
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start, end = seg.get("start"), seg.get("end")
        if start is None or end is None:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0):
            continue
        rows.append((start, end, text))
    rows.sort(key=lambda r: r[0])
    return "\n".join(
        f"{i}\n{_srt_stamp(a)} --> {_srt_stamp(b)}\n{t}\n"
        for i, (a, b, t) in enumerate(rows, 1))


# -------------------------------------------------------------- viral signals
#
# Where the language of a clip tends to spike, as hints -- never as a verdict.
# The tool cannot know what is funny, what is damning, what will travel. It can
# see where a question is asked, where an absolute is claimed, where a fight is
# named, where the room got loud. Those are evidence a model reads on top of the
# words; the judgement of what to cut stays with the thing that can read the
# clip, because a program scoring "viral" without watching is the ranking this
# agent refuses to be.
_SIGNAL_WORDS = {
    "superlative": {
        "pt": ["melhor", "pior", "nunca", "sempre", "jamais", "ninguém",
               "ninguem", "todo mundo", "maior", "incrível", "incrivel",
               "absurdo", "chocante", "inacreditável", "inacreditavel",
               "impossível", "impossivel", "recorde", "o mais", "a mais"],
        "en": ["best", "worst", "never", "always", "nobody", "everyone",
               "biggest", "insane", "crazy", "shocking", "unbelievable",
               "impossible", "record", "the most"],
    },
    "conflict": {
        "pt": ["mentira", "errado", "errada", "discordo", "polêmica", "polemica",
               "briga", "brigar", "contra", "ódio", "odio", "cancelado",
               "cancelada", "processo", "revoltante", "expôs", "expos",
               "acusou", "acusa"],
        "en": ["lie", "wrong", "disagree", "fight", "versus", "hate", "cancel",
               "cancelled", "controversy", "exposed", "sue", "beef", "accused"],
    },
    "reaction": {
        "pt": ["meu deus", "caramba", "nossa", "pelo amor", "que isso",
               "não acredito", "nao acredito", "surreal", "chocado", "chocada"],
        "en": ["oh my god", "wtf", "holy", "no way", "i can't", "i cant",
               "unreal", "speechless"],
    },
}
_LAUGH_RE = re.compile(r"(?:k{3,}|(?:ha){2,}|(?:he){2,}|rs{2,}|lmao|lol|haha)",
                       re.IGNORECASE)


def text_signals(text):
    """The signal tags a line of transcript carries, as a sorted list.

    A question, a laugh, an absolute claim, a named conflict, a reaction. Cheap
    keyword and punctuation matching in Portuguese and English -- a hint that a
    moment might carry, not proof that it does.
    """
    raw = str(text or "")
    # Punctuation stripped before matching, so "absurdo!" and "lie." still match
    # the word lists. The question mark and laughter are read off the raw text
    # first, before it is flattened.
    flat = re.sub(r"[^\w\s]", " ", raw.lower(), flags=re.UNICODE)
    padded = " " + " ".join(flat.split()) + " "
    tags = set()
    if "?" in raw:
        tags.add("question")
    if _LAUGH_RE.search(raw):
        tags.add("laugh")
    for tag, langs in _SIGNAL_WORDS.items():
        for words in langs.values():
            if any((" " + w + " ") in padded for w in words):
                tags.add(tag)
                break
    return sorted(tags)


def _loudness_envelope(source):
    """(second, momentary loudness) across the source, in one ffmpeg pass.

    ebur128 prints a momentary reading every tenth of a second; a spike in it is
    a laugh, a shout, a crowd -- the sound of a reaction, which is the strongest
    signal a live or a podcast gives that a moment landed. No audio, or no
    Devolve (leituras, motivo). `motivo` é None quando deu certo e uma frase
    quando não deu: um envelope vazio por falta de ffmpeg e um envelope vazio
    porque o vídeo é mudo são a mesma lista e não são a mesma coisa, e quem lê
    os sinais precisa saber qual dos dois aconteceu antes de escolher a janela
    achando que o som não tinha nada a dizer.
    """
    if not have("ffmpeg"):
        return [], "ffmpeg is not on PATH, so no loudness was read"
    try:
        done = subprocess.run(
            ["ffmpeg", "-nostats", "-hide_banner", "-i", source, "-map", "0:a:0",
             "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=TIMEOUT_RENDER)
    except Exception as exc:
        return [], f"could not read the loudness: {type(exc).__name__}"
    # ametadata prints two lines per reading: `... pts_time:X` then a line
    # `lavfi.r128.M=Y`. Pair them.
    env, cur_t = [], None
    for line in done.stderr.splitlines():
        mt = re.search(r"pts_time:([\d.]+)", line)
        if mt:
            cur_t = float(mt.group(1))
            continue
        mm = re.search(r"lavfi\.r128\.M=(-?[\d.]+)", line)
        if mm and cur_t is not None:
            env.append((cur_t, float(mm.group(1))))
            cur_t = None
    if not env:
        return [], ("no audio track to read a reaction from, so the loud "
                    "signal is missing and the words carry alone")
    return env, None


# A partir de que duração a fonte deixa de ser transcrita inteira.
#
# POR QUE ESTE NÚMERO EXISTE, e ele é o conserto de uma falha medida em
# 17/09/2026, na nuvem: um VOD de live foi mandado para `lote prep`, o passo da
# transcrição carregou a fonte inteira e o transcritor ESTOUROU A MEMÓRIA. Não
# foi lentidão -- foi o passo não escalar com a duração, e o dono ficou sem
# clipe nenhum.
#
# A conta que sustenta o limiar, com os números que este arquivo já mediu: o
# `base` faz 120s de áudio em 15,4s, ou seja ~7,8x o tempo real. Vinte minutos
# de fonte são ~2,5min de transcrição, que passa. Três horas são ~23min -- e é
# nessa faixa que a memória também acaba. 1200s é onde a espera deixa de ser
# aceitável, não onde a máquina quebra: o objetivo é nunca chegar perto.
VARREDURA_LIMIAR_S = 1200
# Quanto dura cada candidato, e quantos.
#
# DOIS minutos e QUATRO candidatos desde 17/09/2026, e o corte veio de uma
# medição: o texto da fonte serve SÓ para escolher o momento -- a legenda é
# transcrita de novo, janela a janela, na hora do render (ver `proposito`). Uma
# varredura generosa paga por texto que ninguém queima.
#
# Dois minutos ainda dão contexto de sobra para um clipe de 20 a 60 segundos;
# menos que isso entregaria frase cortada ao meio. Quatro candidatos dão escolha
# ao modelo sem virar orçamento.
#
# A conta, com o `base` medido a 7,8x o tempo real: 4 x 2 min = 8 min de áudio,
# ~1 min de transcrição num VOD de três horas. Com os padrões antigos (6 x 3)
# eram 18 min de áudio e ~2,3 min.
VARREDURA_JANELA_S = 120
VARREDURA_CANDIDATOS = 4
# Teto de quanto da fonte a varredura pode ler. Um quarto, e o número saiu de
# uma medição em 17/09/2026: com 6 janelas fixas de 3 min, uma fonte de 25
# minutos era lida em 18 -- 72% dela, economia nenhuma. Ver `picos_para_janelas`.
VARREDURA_COBERTURA_MAX = 0.15


def picos_para_janelas(env, quantos=VARREDURA_CANDIDATOS,
                       janela_s=VARREDURA_JANELA_S, duracao=None):
    """Os trechos mais altos da fonte, como (início, fim) em segundos dela.

    `env` é [(segundo, LUFS momentâneo)] -- o que `_loudness_envelope` devolve
    numa passada de ffmpeg, sem whisper e sem carregar áudio na memória. É por
    isso que esta escolha pode acontecer ANTES da transcrição: ela custa uma
    leitura do áudio, não uma inferência sobre ele.

    O QUE ISTO É, E O QUE NÃO É. O dono pediu "o pico de visualização". Isto é o
    pico de SOM, que é outra coisa e é a que dá para medir daqui: nem a Twitch
    nem o YouTube entregam audiência ao longo do vídeo por esta porta. Momento
    alto costuma ser momento bom -- grito, treta, punchline -- mas pega barulho
    de plateia e perde a fala quieta que era o melhor corte. Por isso a saída é
    uma LISTA DE CANDIDATOS e não um veredito: quem escolhe é o modelo, lendo o
    texto deles. É a mesma regra que `loud_segment_indexes` já escreve: "um
    quarto é um limiar, não uma verdade -- ele diz onde o som está, e o modelo
    diz se aquilo é um momento".

    Devolve [] quando não há envelope. Quem chama trata isso como "não deu para
    varrer" e segue pelo caminho de sempre -- nunca como "não há nada bom".
    """
    leituras = [(float(t), float(v)) for t, v in (env or [])
                if v is not None and float(v) > -70]
    if not leituras or janela_s <= 0 or quantos <= 0:
        return []
    fim_da_fonte = float(duracao) if duracao else max(t for t, _ in leituras)
    if fim_da_fonte <= janela_s:
        return []                       # cabe inteira: não há o que recortar
    # O TETO DE COBERTURA, e ele é conserto de um erro meu que só apareceu no
    # teste de ponta a ponta, em 17/09/2026: com 6 janelas FIXAS de 3 min, uma
    # fonte de 25 minutos era lida em 18 -- 72% dela. A varredura existe para
    # ler pouco, e naquele tamanho ela não economizava nada.
    #
    # O número de janelas passa a escalar com a duração: no máximo um quarto da
    # fonte. Três horas continuam em 6 janelas (18 min, 10%); 25 minutos caem
    # para 2 (6 min, 24%). O mínimo é 2 porque uma janela só não dá ao modelo
    # escolha nenhuma -- ele receberia um trecho e teria de cortar dali.
    cabe = int(fim_da_fonte * VARREDURA_COBERTURA_MAX / janela_s)
    quantos = max(2, min(int(quantos), cabe))
    # Baldes do tamanho da janela, meio balde de passo. O passo pela metade é o
    # que impede um pico bom de cair na emenda entre dois baldes e ser diluído
    # nos dois -- sem ele, um grito exatamente no minuto 3 perde para um trecho
    # morno que por acaso ficou centralizado.
    passo = janela_s / 2.0
    baldes = []
    inicio = 0.0
    import bisect
    tempos = [t for t, _ in leituras]
    while inicio < fim_da_fonte:
        fim = min(inicio + janela_s, fim_da_fonte)
        i = bisect.bisect_left(tempos, inicio)
        j = bisect.bisect_right(tempos, fim)
        vals = [v for _, v in leituras[i:j]]
        if vals:
            # A MÉDIA, não o máximo. Um estouro de microfone de um décimo de
            # segundo tem o maior máximo da fonte inteira e não é momento
            # nenhum; três minutos de sala alta têm a maior média. O que se
            # procura é energia sustentada.
            baldes.append((sum(vals) / len(vals), inicio, fim))
        inicio += passo
    if not baldes:
        return []
    escolhidos = []
    for media, ini, fim in sorted(baldes, key=lambda b: -b[0]):
        # Sem sobreposição: dois candidatos que se cruzam são o mesmo momento
        # contado duas vezes, e gastariam metade do orçamento de transcrição
        # com o mesmo áudio.
        if any(not (fim <= a or ini >= b) for _, a, b in escolhidos):
            continue
        escolhidos.append((media, ini, fim))
        if len(escolhidos) >= quantos:
            break
    return [(round(ini, 2), round(fim, 2))
            for _, ini, fim in sorted(escolhidos, key=lambda e: e[1])]


def transcreve_por_picos(path, seconds=None, quantos=VARREDURA_CANDIDATOS,
                         janela_s=VARREDURA_JANELA_S, diz=None):
    """Transcreve SÓ os trechos mais altos de uma fonte longa.

    Devolve (transcricao, janelas, porque) com a mesma forma que `transcribe`
    devolve -- `segments` em tempo da FONTE, não da janela. Isso não é cuidado
    meu: `transcribe(window=...)` já soma o offset em cada `start`/`end`
    (ver `s.start + offset`), então juntar as janelas é concatenar e ordenar.
    Se algum dia esse offset sair de lá, os clipes saem do lugar errado e
    PARECEM certos, que é o pior defeito possível aqui.

    `porque` é None quando a varredura aconteceu. Quando não deu -- sem
    envelope, fonte curta, ffmpeg ausente -- devolve (None, [], motivo), e quem
    chamou segue pelo caminho inteiro de sempre. Degradar é obrigatório: uma
    varredura que falha não pode custar o clipe.
    """
    env, porque_env = _loudness_envelope(path)
    if not env:
        return None, [], (porque_env or "no loudness envelope")
    janelas = picos_para_janelas(env, quantos=quantos, janela_s=janela_s,
                                 duracao=seconds)
    if not janelas:
        return None, [], "the source did not split into peaks"
    segmentos, linguas, falhas = [], [], []
    for i, (lo, hi) in enumerate(janelas, 1):
        if diz:
            diz(f"warden: reading the words of peak {i}/{len(janelas)} "
                f"({lo / 60:.1f}-{hi / 60:.1f} min)")
        try:
            parte = transcribe(path, window=(lo, hi), proposito="fonte")
        except Exception as erro:
            # Um pico que falha não derruba os outros: o que sobra ainda é
            # material para escolher, e um clipe é melhor que nenhum.
            #
            # A MENSAGEM VAI JUNTO, e isso é conserto de um defeito que este
            # teste pegou em 17/09/2026: a versão anterior imprimia só
            # `RuntimeError` e engolia o texto. Os seis picos falharam por falta
            # do modelo whisper -- uma causa de uma linha, com conserto óbvio --
            # e a saída não permitia saber disso. Um erro que esconde a própria
            # causa custa mais que o erro.
            falhas.append("%d: %s: %s" % (i, type(erro).__name__, erro))
            if diz:
                diz(f"warden: peak {i} could not be read -- "
                    f"{type(erro).__name__}: {erro}")
            continue
        segmentos.extend(parte.get("segments") or [])
        if parte.get("language"):
            linguas.append(parte["language"])
    if not segmentos:
        # O PORQUÊ VAI JUNTO. "no peak could be transcribed" sozinho manda quem
        # lê procurar no escuro; com as falhas, a causa comum -- o modelo que
        # ainda não baixou, por exemplo -- se lê na hora.
        return None, [], ("no peak could be transcribed -- " + "; ".join(falhas)
                          if falhas else "no peak could be transcribed")
    segmentos.sort(key=lambda s: (s.get("start") or 0.0))
    transcricao = {
        "segments": segmentos,
        "language": linguas[0] if linguas else None,
        # O SINAL, e ele precisa viajar junto: quem ler esta transcrição tem de
        # saber que ela NÃO cobre a fonte inteira. Sem isto, um modelo lendo
        # buracos entre 12 e 47 minutos concluiria que ali não se falou nada --
        # e escolheria janelas com base numa ausência que é da varredura, não
        # do material.
        "source": "peaks only (%d window(s), %.0f min of %.0f min)" % (
            len(janelas), sum(h - l for l, h in janelas) / 60.0,
            (float(seconds) / 60.0) if seconds else 0.0),
        "varredura": {"janelas": janelas, "cobertura_s":
                      round(sum(h - l for l, h in janelas), 1)},
    }
    return transcricao, janelas, None


def loud_segment_indexes(segments, source, top_fraction=0.25):
    """Which segments sit in the loudest quarter of the source.

    The reaction peaks: the model reads them as 'the room got loud here'. A
    quarter is a threshold, not a truth -- it says where the sound is, and the
    model says whether that is a moment.
    """
    env, why = _loudness_envelope(source)
    if not env or not segments:
        return set(), why
    # `env` já vem ordenado no tempo, então as pontas saem por busca binária
    # em vez de varredura. A versão anterior percorria as ~13.800 leituras da
    # envoltória INTEIRA para cada segmento: num podcast de 23 minutos com ~700
    # segmentos isso são ~10 milhões de comparações em Python, num container
    # amd64 emulado. Não aparecia nos 24s de teste e apareceria na fonte longa,
    # que é o caso de uso real deste comando.
    import bisect
    tempos = [t for t, _ in env]
    peaks = []
    for seg in segments:
        s, e = seg.get("start"), seg.get("end")
        if s is None or e is None:
            peaks.append(None)
            continue
        i = bisect.bisect_left(tempos, s)
        j = bisect.bisect_right(tempos, e)
        vals = [lufs for _, lufs in env[i:j] if lufs > -70]
        peaks.append(max(vals) if vals else None)
    got = sorted(p for p in peaks if p is not None)
    if not got:
        return set(), "no segment overlapped a loudness reading"
    thr = got[min(len(got) - 1, int(len(got) * (1 - top_fraction)))]
    return {i for i, p in enumerate(peaks) if p is not None and p >= thr}, None


def analyze_signals(segments, source=None):
    """The moments worth a second look, in time order, each with why.

    A segment surfaces when its words carry a signal or the sound spikes on it.
    Everything else is dropped, because a list that keeps every line is the
    transcript again, not a shortlist. Nothing here is ranked: it is time order
    with evidence attached, for the model to cluster into clips and judge.
    """
    loud, quiet_why = ((set(), "no source file was passed, so only the words "
                        "were read") if not source
                       else loud_segment_indexes(segments, source))
    out = []
    for i, seg in enumerate(segments or []):
        tags = text_signals(seg.get("text", ""))
        if i in loud:
            tags = sorted(set(tags) | {"loud"})
        if tags:
            out.append({"start": seg.get("start"), "end": seg.get("end"),
                        "text": (seg.get("text") or "").strip(), "signals": tags})
    return out, quiet_why


def digest(segments, window=None, seconds_per_line=12):
    """The transcript a model can afford to read.

    Word-level timing is for the renderer. A model choosing a moment needs the
    words and roughly where they are, and handing it the raw artefact is how a
    three-hour source turns into a prompt nobody can pay for.
    """
    lines, bucket, bucket_start = [], [], None
    for seg in segments:
        if window and (seg["end"] < window[0] or seg["start"] > window[1]):
            continue
        if bucket_start is None:
            bucket_start = seg["start"]
        bucket.append(seg["text"])
        if seg["end"] - bucket_start >= seconds_per_line:
            lines.append(f"[{_stamp(bucket_start)}] " + " ".join(bucket))
            bucket, bucket_start = [], None
    if bucket:
        lines.append(f"[{_stamp(bucket_start)}] " + " ".join(bucket))
    return "\n".join(lines)


def _stamp(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------- rendering

CROP_PRESETS = {"left": 0.25, "center": 0.5, "right": 0.75}
FACE_MODEL = os.environ.get("WARDEN_FACE_MODEL", "/opt/plow/yunet.onnx")

# O encode, e ele é o maior item do relógio -- não o modelo.
#
# Medido em 15/09/2026 no container, cortando 20s de
# `janela-7893834d2c-82-106.mp4` (1920x1080, 30fps) para 1080x1920. A imagem é
# amd64 EMULADA sobre Apple Silicon e não tem codificador de hardware, então o
# preset do x264 é o botão inteiro:
#
#   veryfast  crf 20                      16,8s   6,5 MB   <- o de antes
#   ultrafast crf 20                       6,8s  16,8 MB
#   ultrafast crf 26 maxrate 6M           7,1s   9,2 MB   <- este
#   superfast crf 23 maxrate 6M          12,1s   7,3 MB
#
# Os quadros de `veryfast crf20` e `ultrafast crf20` foram comparados COM OS
# OLHOS a 540px de largura, e são indistinguíveis neste material. Num vertical
# de 20s visto no telefone o preset rápido não custa qualidade que se veja.
#
# O `maxrate` NÃO é enfeite e não sai daqui: sem ele o ultrafast sozinho escreve
# 16,8 MB para 20 segundos, 2,6x o arquivo de antes. Esses arquivos vão como
# anexo pelo telefone do dono, e a faixa que nunca falhou é de 7 a 14 MB.
# Velocidade que estoura o anexo não é velocidade -- é um clipe que não chega.
#
# As variáveis de ambiente existem para o caso de uma máquina com codificador
# de verdade, ou de uma campanha que peça outro arquivo. O PADRÃO é o rápido,
# porque a máquina onde isto roda é a emulada.
X264_PRESET = os.environ.get("WARDEN_X264_PRESET", "ultrafast")
X264_CRF = os.environ.get("WARDEN_X264_CRF", "26")
X264_MAXRATE = os.environ.get("WARDEN_X264_MAXRATE", "6M")
X264_BUFSIZE = os.environ.get("WARDEN_X264_BUFSIZE", "12M")


def _crop_fraction(crop):
    """Where the vertical window sits across a wider source, 0.0 to 1.0.

    A landscape source does not fit a 9:16 frame, so a band of it is kept and
    the rest is thrown away. Centre is the default and it is a guess: it is
    wrong for a side-by-side, a two-shot, a gameplay with the face in a corner.
    `left`/`center`/`right`, or a percentage, let the caller say where the
    subject actually is instead of hoping it is in the middle.
    """
    if crop is None or crop == "" or crop == "auto":
        return 0.5
    if crop in CROP_PRESETS:
        return CROP_PRESETS[crop]
    try:
        pct = float(crop)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"crop {crop!r} is not left, center, right, auto or a number from 0 to 100")
    if not 0 <= pct <= 100:
        raise RuntimeError("crop as a percentage is 0 (far left) to 100 (far right)")
    return pct / 100.0


def face_detection_status():
    """(pronto?, motivo em uma linha). A pergunta que ninguém fazia.

    O enquadramento vertical inteiro depende disto, e até aqui a resposta
    "não tenho detector" era indistinguível de "procurei e não achei rosto":
    as duas viravam `None` e as duas caíam no centro. Numa máquina sem OpenCV
    o corte saiu com o rosto na borda e o aviso dizia, com toda a calma, que
    havia enquadrado no centro -- o que era verdade e não era o problema.
    """
    try:
        import cv2
    except Exception as exc:
        return False, ("OpenCV is not installed in this image, so there is no "
                       f"face detector ({type(exc).__name__})")
    if not os.path.exists(FACE_MODEL):
        return False, (f"the YuNet model is not at {FACE_MODEL}, so the face "
                       "detector has nothing to run")
    try:
        cv2.FaceDetectorYN_create(FACE_MODEL, "", (320, 320), 0.6, 0.3, 5000)
    except Exception as exc:
        return False, (f"OpenCV {getattr(cv2, '__version__', '?')} could not "
                       f"build the YuNet detector: {type(exc).__name__}: {exc}")
    return True, f"YuNet on OpenCV {getattr(cv2, '__version__', '?')}"


def _face_detector():
    """YuNet, ou None quando esta máquina não tem com que detectar rosto.

    A DNN face detector rather than a Haar cascade because the footage is
    stylised -- animation, game capture -- and the cascades, trained on
    photographs, miss it; YuNet was measured finding the faces in this footage
    where they did not.

    `None` aqui significa APENAS "não há detector nesta máquina", e quem chama
    tem de parar. Não significa "não achei rosto": isso é uma lista vazia, que é
    outra resposta e admite outro tratamento.
    """
    ok, _why = face_detection_status()
    if not ok:
        return None
    import cv2
    return cv2, cv2.FaceDetectorYN_create(FACE_MODEL, "", (320, 320),
                                          0.6, 0.3, 5000)


def _face_centers(source, start, length, samples=7):
    """(x-fraction, area-fraction) of every face found across the clip window.

    Sampled over several frames rather than one, because a face turns away, a
    shot changes, a detector blinks. The centres of the frames it did find are
    what a crop can be built on.
    """
    found = _face_detector()
    if found is None:
        return []
    cv2, det = found
    import subprocess, tempfile
    # UMA passada de ffmpeg para as sete amostras, não sete.
    #
    # Medido em 15/09/2026 no container: sete `-ss` separados custavam 4,1-5,4s
    # de ffmpeg; a passada única custa ~1,3s. É o mesmo conserto que
    # `sample_frames` já tinha neste projeto e que não havia chegado aqui, e é
    # o maior item da varredura de lentidão.
    #
    # O que NÃO foi feito, de propósito: reaproveitar os quadros do
    # `sample_frames`. Isso economizaria mais e mudaria quais quadros decidem o
    # enquadramento -- com `--shots` ele amostra por plano e aqui é contínuo --
    # e o enquadramento é o produto. Uma passada própria mantém a decisão
    # idêntica à de antes.
    #
    # Os que a passada não trouxer voltam um a um, pelo mesmo motivo do contact
    # sheet: `fps` não emite a última amostra quando o intervalo não cabe, e
    # quadro não lido aqui não é "não achei rosto", é "não olhei".
    out, lidos = [], 0
    instantes = [float(start) + (i + 0.5) * float(length) / samples
                 for i in range(samples)]
    passo = float(length) / samples
    tmp = tempfile.mkdtemp(prefix="warden-faces-")

    def _mede(png):
        nonlocal lidos
        img = cv2.imread(png)
        if img is None:
            return False
        h, w = img.shape[:2]
        det.setInputSize((w, h))
        _n, faces = det.detect(img)
        if faces is not None:
            for f in faces:
                cx = (float(f[0]) + float(f[2]) / 2) / w
                area = (float(f[2]) * float(f[3])) / (w * h)
                out.append((cx, area))
        lidos += 1
        return True

    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-ss", f"{instantes[0]:.3f}", "-i", source,
                        "-vf", f"fps=1/{passo:.6f}", "-frames:v", str(samples),
                        "-fps_mode", "passthrough",
                        os.path.join(tmp, "f%02d.png")],
                       capture_output=True, timeout=90)
        faltaram = []
        for i, t in enumerate(instantes):
            png = os.path.join(tmp, "f%02d.png" % (i + 1))
            if not (os.path.isfile(png) and os.path.getsize(png) > 0):
                faltaram.append(t)
                continue
            try:
                if not _mede(png):
                    faltaram.append(t)
            except Exception:
                faltaram.append(t)
        for t in faltaram:
            png = os.path.join(tmp, "fill%.3f.png" % t)
            try:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss",
                                f"{t:.3f}", "-i", source, "-frames:v", "1", png],
                               capture_output=True, timeout=30)
                _mede(png)
            except Exception:
                continue                               # a bad frame is not a failed cut
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not lidos:
        # Zero quadros lidos não é "não achei rosto": é "não olhei". As duas
        # devolviam lista vazia e as duas caíam no centro, que foi como o rosto
        # foi parar na borda do quadro sem uma linha de aviso.
        raise RuntimeError(
            f"the face detector could not read a single frame of "
            f"{os.path.basename(source)} between {float(start):.1f}s and "
            f"{float(start) + float(length):.1f}s, so the framing has nothing "
            f"to follow -- pass --crop to place the band by hand.")
    return out


def _face_crop_fraction(faces, hint, kept):
    """A crop fraction that centres the kept band on the subject's face.

    `hint` is the side the caller pointed at: left keeps faces on the left half,
    right the right, and center/auto/none takes them all. The prominent (larger,
    nearer) faces decide, so a face in the foreground wins over one in the back,
    which is the exact miss that put the crop on the monster behind the man.
    Returns None when there is no face to trust, and the caller falls back.
    """
    if not faces or not kept or kept >= 1:
        return None
    if hint == "left":
        faces = [f for f in faces if f[0] < 0.5]
    elif hint == "right":
        faces = [f for f in faces if f[0] > 0.5]
    if not faces:
        return None
    faces = sorted(faces, key=lambda f: -f[1])         # largest first
    top = faces[:max(1, len(faces) // 2)]
    xs = sorted(f[0] for f in top)
    target = xs[len(xs) // 2]                           # median x of the prominent faces
    fx = (target - kept / 2) / (1 - kept)
    return min(1.0, max(0.0, fx))


# ------------------------------------------------------ tela compartilhada
#
# O defeito de 15/09, e ele não é de enquadramento: é de PREMISSA. Todo o
# caminho acima supõe que o assunto do quadro é uma pessoa e que existe uma
# faixa vertical onde ela cabe. Numa live com TELA COMPARTILHADA essa premissa é
# falsa: o quadro é um navegador com uma webcam de canto, a faixa vertical do
# meio é o meio da página, e a pessoa não está nela.
#
# Medido nos dois clipes que o dono reprovou (janelas 82-106 e 1218-1242 de
# Yj8VrxtbHAo, o mesmo material que produziu os arquivos ruins): em 4 dos 8
# quadros do mosaico do segundo clipe a pessoa tinha sumido inteira e o que
# ficava era uma página de busca cortada ao meio, ilegível. O detector de rosto
# não errou -- ele achou o rosto do PiP, que é pequeno e fica no canto, e a
# faixa centrada nele leva junto meia página de navegador; nos quadros em que
# não achou, o corte caiu no centro, que nesse material é o pior lugar possível.
#
# A saída não é um enquadramento melhor: é outro LAYOUT. A webcam em cima, a
# tela inteira embaixo, a legenda por baixo das duas. Nada some, tudo fica
# legível, e funciona mesmo quando o detector não acha ninguém.

# Quanto do quadro não tem textura NENHUMA -- o pixel é idêntico aos oito
# vizinhos. É a assinatura de uma tela: interface é feita de áreas chapadas, e
# uma câmera não tem nenhuma, porque sensor tem ruído. Medido em 22 quadros dos
# dois arquivos reais, em 15/09:
#
#   câmera (webcam cheia, webcam + chat)  0,093 a 0,219
#   tela   (navegador, busca do Google)   0,465 a 0,780
#
# Não há sobreposição, e a folga é de mais de duas vezes. 0,32 fica no meio do
# vão. A medida é do PIL e não do OpenCV de propósito: assim a classificação
# roda -- e é testável -- na máquina de quem escreve o código, onde não há cv2.
PLANURA_TELA = 0.32
# A partir de que fração de quadros de tela o clipe vira dividido.
#
# A troca é assimétrica, e por isso são dois números e não um. Um dividido sobre
# vídeo normal é feio; um corte no meio do navegador é inutilizável. Então
# acima de 40% é decisão, entre 20% e 40% é DÚVIDA -- e na dúvida divide.
#
# Nos dois arquivos reais isto dá: janela 82-106 com 3 de 11 quadros de tela
# (0,27, dúvida) e janela 1218-1242 com 3 a 6 de 11 conforme a amostragem
# (0,27 a 0,55). As duas dividem, que é o que o dono pediu para as duas.
TELA_FRACAO_DUVIDA = 0.20
TELA_FRACAO_CERTA = 0.40
# Uma webcam de canto é PEQUENA. Uma caixa que cresceu até quase o quadro todo
# não é um PiP, é uma câmera com fundo chapado -- e nesse caso o caminho antigo
# é o certo.
PIP_AREA_MAX = 0.30
PIP_ROSTO_AREA_MAX = 0.05
# Quando a caixa vencedora é uma webcam FIXA e quando é coincidência.
#
# Estes dois números substituem `em_quantos * 2 >= max(2, quadros_de_tela)`, e a
# troca é o conserto medido do clipe 02 entregue em 17/09/2026. O defeito da
# regra antiga é o denominador: ele conta quadros de TELA, e o numerador conta
# quadros em que o detector achou rosto DENTRO da webcam. As duas coisas não
# têm relação nenhuma, e numa live de gameplay elas divergem.
#
# Medido nas duas janelas reais desta live:
#
#   janela   quadros   rosto em algum lugar   de tela   PiP achado   regra antiga
#   481-502     39              39              18       15 (38%)    passa 30>=18
#   2422-2444   40              40              33       11 (28%)    FALHA 22>=33
#
# A webcam está nas duas -- ela aparece no clipe entregue, a olho. O que a
# janela 02 tem de diferente é ser mais chapada (33 quadros de tela contra 18),
# o que ENDURECE um limite que nada tem a ver com a webcam. Medir contra o total
# amostrado tira essa perversidade: 38% e 28% passam.
#
# 0,20 e não mais: uma foto de rosto numa página aparece em 1 ou 2 quadros de 40
# (2 a 5%) -- é o defeito do Yuri Gagarin que `_agrupa_pips` documenta -- e
# continua recusada com folga de quatro vezes. O piso de 3 quadros existe porque
# numa amostragem curta (o mínimo é 8) 20% seriam 1,6 quadros, e um quadro só
# nunca é prova de que algo é fixo.
PIP_FRACAO_MIN = 0.20
PIP_QUADROS_MIN = 3
# E um rosto pequeno DEMAIS não é uma pessoa neste quadro.
#
# A live carrega um adesivo animado do Mario num canto, e o YuNet acha rosto
# nele -- com 0,79 de confiança. Medido nos dois arquivos em 15/09/2026: o
# adesivo mede 0,010 a 0,014 de largura do quadro e o rosto da webcam de canto
# mede 0,042 a 0,060, três vezes mais. Sem este piso, num quadro em que a pessoa
# está olhando para baixo e não é detectada, o adesivo vira "o maior rosto" e a
# faixa de cima passa a seguir um desenho: foi o que apareceu em 2 dos 8 quadros
# do mosaico, com a espuma acústica da parede ocupando a faixa inteira.
ROSTO_MIN_LARGURA = 0.025
PIP_CELULAS = (16, 9)
PIP_CRESCE_LIMIAR = 0.35
# Onde o rosto cai dentro da faixa da webcam, e quanto dela ele ocupa.
#
# 0,45 põe o rosto um pouco acima do meio da faixa, que é onde o hook deste
# modo NÃO está: ele vira lower-third, encostado no pé da faixa. Com a faixa de
# 616px e o rosto em 38% dela, a tinta do rosto vai de 158 a 390 e o bloco do
# hook começa em 390 -- encosta, não cobre.
ROSTO_NA_FAIXA = 0.45
ROSTO_ALTURA_NA_FAIXA = 0.38
# O teto do zoom do rastreio. Medido nesta live: o rosto do PiP tem 151px de
# altura numa fonte de 1080, e para ele chegar a 38% da faixa bastam 2,7x. 4x é
# folga para uma webcam ainda menor; acima disso é ampliação de um rosto que
# não tem pixel para dar, e aí o borrão volta por outra porta.
ZOOM_ROSTO_MAX = 4.0
# E o teto DE VERDADE, que não é um número solto: é quanto o pixel da fonte
# pode ser esticado para encher a faixa.
#
# O dono disse "saiu tudo com zoom" e apontou 4.0 como alto demais. Medido nos
# dois cortes entregues em 15/09/2026, o zoom que saiu foi 2,75 e 3,77 -- o teto
# de 4.0 quase não pegou, então baixá-lo não era o conserto. O que faltava era
# amarrar o zoom ao tamanho do rosto NA FONTE, e o vínculo que faz isso sozinho
# é a escala: a faixa mostra `ch/z` pixels da fonte em `banda_h` pixels de saída,
# então a ampliação é `banda_h*z/ch`. Um rosto grande já chega ao alvo com z=1 e
# escala < 1 (redução, sempre nítida); um rosto pequeno precisa esticar, e é aí
# que o teto morde.
#
# 1,8 sai da medição desta live: o rosto da webcam de canto tem 162px de altura
# (0,15 de 1080) e o alvo na faixa de 734px é 279px -- 1,72x. O teto deixa isso
# passar e não deixa passar muito mais. Com ele, um rosto de 0,05 do quadro, que
# pediria 8x, para em 2,6x e sai pequeno e nítido em vez de grande e borrado.
ESCALA_FONTE_MAX = 1.8
# Quanto do quadro a faixa da pessoa tem, no mínimo -- e é a partir dela que a
# TELA encolhe, não o contrário.
#
# O número não é novo: `layout_dividido` já reservava `int(height * 0.20)` para
# a faixa da pessoa, e `test_a_webcam_fica_em_cima_e_encosta_na_tela` já o
# prendia com `assertGreater(wh, 1920 * 0.2)`. O que mudou em 17/09/2026 é a
# ORDEM da conta. Antes a tela entrava por largura e a pessoa ficava com a
# sobra; agora a pessoa tem o piso e a tela encolhe até caber, porque foi essa
# a escolha do dono olhando quatro maquetes do quadro real dos 4s:
#
#   tela cheia 1080x608 -> rosto 552x320 (1,25x do PiP)
#   tela  996x560       -> rosto 663x384 (1,51x)        <- escolhida
#
# 12% menos de tela por 21% mais de rosto. A tela continua INTEIRA nas duas: o
# que encolhe é a escala, nunca o enquadramento.
ROSTO_FAIXA_MIN = 0.20


def _mapa_planura(img, cols=PIP_CELULAS[0], rows=PIP_CELULAS[1]):
    """Planura célula a célula: 1.0 é uma célula inteiramente chapada.

    Tudo em C do PIL, e não em laço de Python: são 230 mil pixels por quadro e
    dez quadros por corte. `difference(max, min)` é o alcance local em 3x3, o
    `point` marca só o zero absoluto, e o `resize` com BOX faz a média por
    célula -- que é exatamente a conta que se queria.
    """
    from PIL import Image as _Image, ImageChops, ImageFilter
    g = img.convert("L").resize((cols * 40, rows * 40))
    alcance = ImageChops.difference(g.filter(ImageFilter.MaxFilter(3)),
                                    g.filter(ImageFilter.MinFilter(3)))
    chapado = alcance.point(lambda v: 255 if v == 0 else 0)
    celulas = chapado.resize((cols, rows), _Image.BOX)
    return [[celulas.getpixel((c, r)) / 255.0 for c in range(cols)]
            for r in range(rows)]


def planura(img):
    """Quanto deste quadro é área chapada. Ver PLANURA_TELA."""
    mapa = _mapa_planura(img)
    return sum(sum(linha) for linha in mapa) / (len(mapa) * len(mapa[0]))


def _cresce_pip(mapa, linha, coluna, limiar=PIP_CRESCE_LIMIAR):
    """A caixa da webcam, crescida a partir da célula do rosto.

    Cresce enquanto a próxima fileira de células ainda for material de CÂMERA
    (planura baixa) e para quando encontra interface. É o que separa o PiP da
    página atrás dele sem precisar achar a borda desenhada do PiP -- que nem
    todo streamer desenha.
    """
    rows, cols = len(mapa), len(mapa[0])
    r0 = r1 = max(0, min(rows - 1, int(linha)))
    c0 = c1 = max(0, min(cols - 1, int(coluna)))

    def media(celulas):
        return sum(celulas) / len(celulas)

    mudou = True
    while mudou:
        mudou = False
        if r0 > 0 and media(mapa[r0 - 1][c0:c1 + 1]) < limiar:
            r0 -= 1; mudou = True
        if r1 < rows - 1 and media(mapa[r1 + 1][c0:c1 + 1]) < limiar:
            r1 += 1; mudou = True
        if c0 > 0 and media([mapa[r][c0 - 1] for r in range(r0, r1 + 1)]) < limiar:
            c0 -= 1; mudou = True
        if c1 < cols - 1 and media([mapa[r][c1 + 1] for r in range(r0, r1 + 1)]) < limiar:
            c1 += 1; mudou = True
    return (c0 / cols, r0 / rows, (c1 + 1) / cols, (r1 + 1) / rows)


def _agrupa_pips(caixas):
    """(caixa mediana, em quantos quadros, centro mediano do rosto, quais quadros).

    A caixa que aparece no MAIOR número de quadros é a que vale.

    Não basta pegar o maior rosto de cada quadro: na janela 1218-1242 a página
    de busca mostra FOTOS do Yuri Gagarin, e num quadro o rosto da foto é maior
    que o da webcam. O que distingue a webcam é ela estar sempre no mesmo lugar
    -- a foto some quando a pessoa rola a página. Então a decisão é por
    repetição entre quadros, não por tamanho dentro de um quadro.
    """
    grupos = []
    for quadro, caixa, centro in caixas:
        cx = (caixa[0] + caixa[2]) / 2
        cy = (caixa[1] + caixa[3]) / 2
        for g in grupos:
            if abs(cx - g["cx"]) < 0.08 and abs(cy - g["cy"]) < 0.08:
                g["quadros"].add(quadro)
                g["caixas"].append(caixa)
                g["rostos"].append(centro)
                g["cx"] = sum((b[0] + b[2]) / 2 for b in g["caixas"]) / len(g["caixas"])
                g["cy"] = sum((b[1] + b[3]) / 2 for b in g["caixas"]) / len(g["caixas"])
                break
        else:
            grupos.append({"cx": cx, "cy": cy, "quadros": {quadro},
                           "caixas": [caixa], "rostos": [centro]})
    if not grupos:
        return None, 0, None, set()
    grupos.sort(key=lambda g: (-len(g["quadros"]),
                               (g["caixas"][0][2] - g["caixas"][0][0])
                               * (g["caixas"][0][3] - g["caixas"][0][1])))
    melhor = grupos[0]
    lados = list(zip(*melhor["caixas"]))
    mediana = tuple(sorted(v)[len(v) // 2] for v in lados)
    # A MEDIANA do rosto, não a média: numa live ele se mexe, e uma passada em
    # que a cabeça sai do enquadramento não pode puxar a faixa junto.
    eixos = list(zip(*melhor["rostos"]))
    rosto = tuple(sorted(v)[len(v) // 2] for v in eixos)
    # E QUAIS quadros, não só quantos. É a diferença entre saber que existe uma
    # webcam de canto nesta janela e saber que ela está NESTE quadro -- e é a
    # segunda pergunta que decide se este quadro pode ser dividido.
    return mediana, len(melhor["quadros"]), rosto, set(melhor["quadros"])


def _quadros_coloridos(source, start, length, samples):
    """(pasta, [(t no relógio do clipe, caminho)]) de `samples` quadros, EM COR.

    Uma passada de ffmpeg, pelo mesmo motivo escrito em `_face_centers`: sete
    `-ss` separados custavam 4,1-5,4s contra ~1,3s da passada única. Em cor
    porque o YuNet é uma rede treinada em RGB e o cinza do `sample_frames`
    degrada a detecção -- e é o rosto do PiP, que já é pequeno, que se perde.

    E o QUANDO de cada quadro é MEDIDO, não suposto. Aqui estava o defeito que
    o dono chamou de "saiu tudo com zoom", e ele não era de zoom: era de fase.
    A versão anterior seguia `-ss start+passo/2` com `fps=1/passo` e carimbava
    os quadros em `(i+0,5)*passo`. Medido em 15/09/2026 na janela 343-369 desta
    live, com `-ss 2,5` e `passo 1`: as planuras da passada única saíram
    [0,447 0,447 0,007 0,449 0,16] e as dos mesmos instantes lidos um a um
    saíram [0,091 0,447 0,006 0,007 0,449]. A segunda lista é a verdade (um
    `-ss` por quadro e a leitura sem seek nenhum concordam), e a primeira é ela
    deslocada de MEIO PASSO: o `fps` entregou 3,0 4,0 5,0 onde o rótulo dizia
    2,5 3,5 4,5.

    Meio segundo não seria nada num vídeo parado. Nesta live a fonte troca de
    layout a cada 1 a 3 segundos -- webcam cheia, foto em tela cheia, tela com
    webcam de canto -- e meio passo fora de fase é a faixa de cima recortando o
    canto inferior direito de um quadro que já mudou: foi assim que ela saiu
    mostrando a boca e o queixo dele aos 3,8s e um pedaço do porco aos 1,2s.

    `select` no lugar de `fps` porque ele escolhe quadros DE VERDADE em vez de
    sintetizar uma grade, e `showinfo` diz o `pts_time` de cada um. O custo é o
    mesmo -- uma passada -- e o tempo deixa de ser suposição.
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="warden-tela-")
    passo = float(length) / samples
    zero = float(start) + passo / 2
    tempos = []
    try:
        saiu = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
             "-ss", f"{zero:.3f}", "-i", source,
             "-vf", (f"select='isnan(prev_selected_t)+"
                     f"gte(t-prev_selected_t,{passo:.6f})',showinfo"),
             "-frames:v", str(samples), "-fps_mode", "passthrough",
             os.path.join(tmp, "t%02d.png")],
            capture_output=True, timeout=180)
        texto = (saiu.stderr or b"").decode("utf-8", "replace")
        tempos = [float(v) for v in re.findall(r"pts_time:([0-9.]+)", texto)]
    except Exception:
        pass
    achados = [os.path.join(tmp, n) for n in sorted(os.listdir(tmp))
               if n.endswith(".png") and os.path.getsize(os.path.join(tmp, n)) > 0]
    # O `pts_time` vem no relógio do SEEK (o primeiro quadro é 0), então o
    # instante no relógio do clipe é `zero + pts - start`. Quando o showinfo não
    # disser nada -- outro ffmpeg, outra build -- vale a grade de antes, que é
    # aproximada mas nunca pior do que era.
    quadros = []
    for i, png in enumerate(achados):
        if i < len(tempos):
            t = zero + tempos[i] - float(start)
        else:
            t = (i + 0.5) * passo
        quadros.append((round(t, 3), png))
    return tmp, quadros


def trilha_do_rosto(por_quadro, pip=None):
    """[(t, cx, cy, altura_do_rosto)] -- onde a pessoa está, quadro a quadro.

    É o conserto do caso MISTO, e ele custou um clipe olhado com olhos.

    A primeira versão recortava SEMPRE o mesmo retângulo, o do PiP. Numa janela
    que alterna entre "webcam em tela cheia" e "tela compartilhada com webcam de
    canto" -- que é o que uma live é -- esse retângulo só contém a pessoa nos
    quadros de tela. Medido na janela 82-106 em 15/09/2026: em 5 dos 8 quadros
    do mosaico a faixa de cima era um close borrado de ombro, cabelo ou queixo,
    porque nesses quadros o canto inferior direito da fonte é o ombro dele.
    Avisar não bastava: o clipe saía ruim do mesmo jeito.

    Então a faixa de cima passa a SEGUIR A PESSOA em vez de uma caixa fixa, e
    com isso o caso misto deixa de precisar ser classificado: onde há PiP a
    trilha vai para o canto, onde a fonte está em tela cheia ela vai para o
    meio, e o zoom acompanha o tamanho do rosto nos dois.

    Qual rosto, quando há vários, é a pergunta que a página de busca do Google
    obrigou a responder: ela mostra FOTOS de rosto, e num quadro a foto é maior
    que a webcam. Num quadro de TELA vale o rosto que está dentro do PiP; num
    quadro de câmera vale o maior. Quadro sem rosto nenhum não vira ponto: a
    interpolação entre os vizinhos atravessa o vão, que é melhor que um salto.
    """
    escolhas = []
    for t, planura_, rostos in por_quadro or []:
        escolhido = None
        if rostos:
            if planura_ >= PLANURA_TELA and pip:
                dentro = [r for r in rostos
                          if pip[0] <= r[0] <= pip[2] and pip[1] <= r[1] <= pip[3]]
                if dentro:
                    escolhido = max(dentro, key=lambda r: r[2] * r[3])
            else:
                escolhido = max(rostos, key=lambda r: r[2] * r[3])
        escolhas.append((float(t), escolhido))
    # Sem rosto por DOIS quadros seguidos, a faixa ABRE para o quadro inteiro.
    #
    # Uma falha isolada é o detector piscando, e ali segurar o enquadramento
    # anterior é certo. Duas seguidas é a pessoa realmente fora de vista -- ela
    # se abaixou, virou de costas -- e aí segurar entrega um close de cabelo
    # ocupando um terço do clipe. Medido em 15/09/2026 na janela 82-106: era o
    # que sobrava em 2 dos 8 quadros do mosaico depois de o rastreio consertar
    # os outros seis. Abrir mostra a mesma cena que a faixa de baixo mostra, o
    # que é redundante -- e redundante é melhor que um close de nada.
    vazio = (0.5, 0.5, 1.0, 1.0)
    trilha = []
    for i, (t, escolhido) in enumerate(escolhas):
        if escolhido is None:
            vizinho = escolhas[i - 1][1] if i else None
            seguinte = escolhas[i + 1][1] if i + 1 < len(escolhas) else None
            if vizinho is not None and seguinte is not None:
                continue                        # piscada: segura o anterior
            escolhido = vazio
        trilha.append((round(t, 3), round(escolhido[0], 4),
                       round(escolhido[1], 4), round(escolhido[3], 4)))
    return trilha


# Que fatia do intervalo entre duas amostras é MOVIMENTO; o resto é parado.
#
# Uma rampa contínua entre amostras é errada aqui, e o mosaico mostrou por quê:
# entre uma amostra com a webcam no canto e a seguinte com a pessoa em tela
# cheia, a interpolação linear varre o quadro inteiro -- e no meio do caminho a
# faixa de cima para numa parede de espuma acústica, sem rosto nenhum. Medido em
# 15/09/2026 na janela 82-106: era o quadro de 8,8s do mosaico.
#
# Então cada amostra é SEGURADA e a troca acontece num terço de segundo em volta
# do ponto médio. Parado a maior parte do tempo, e quando muda, muda rápido --
# que é como um corte se move, e não como uma panorâmica.
RAMPA_DO_RASTREIO = 0.18


def _com_rampa(pontos):
    """Os pontos, com um patamar em cada um e a troca curta entre eles."""
    if len(pontos) < 2:
        return list(pontos)
    saiu = [pontos[0]]
    for (a, va), (b, vb) in zip(pontos, pontos[1:]):
        meio = (a + b) / 2.0
        meia = max(1.0, (b - a) * RAMPA_DO_RASTREIO)
        saiu.append((int(round(meio - meia)), va))
        saiu.append((int(round(meio + meia)), vb))
    saiu.append(pontos[-1])
    # Dois pontos no mesmo quadro fazem a divisão por zero virar um degrau; a
    # ordem já garante o valor certo, então basta ficar com o último.
    limpo = []
    for n, v in saiu:
        if limpo and limpo[-1][0] >= n:
            limpo[-1] = (limpo[-1][0], v)
        else:
            limpo.append((n, v))
    return limpo


def _expressao_por_quadro(pontos):
    """Os pontos, interpolados linearmente, como expressão do ffmpeg em `on`.

    Piecewise LINEAR sobre os pontos que `_com_rampa` já moldou: patamar em cada
    amostra e uma troca curta entre elas.
    """
    if not pontos:
        return None
    pontos = _com_rampa(sorted(pontos))
    if len(pontos) == 1:
        return f"{pontos[0][1]:.5f}"
    expr = f"{pontos[-1][1]:.5f}"
    for (a, va), (b, vb) in reversed(list(zip(pontos, pontos[1:]))):
        tramo = max(1, b - a)
        # Vírgula SEM escape: a expressão inteira vai dentro de aspas simples no
        # filtergraph, e é o parser do próprio ffmpeg que as trata -- é o que o
        # `zoompan` deste arquivo já faz em `z='min(a,b)'`.
        expr = f"if(lt(on,{b}),{va:.5f}+{(vb - va) / tramo:.6f}*(on-{a}),{expr})"
    return expr


def zoompan_do_rosto(trilha, banda_w, banda_h, sw, sh, fps=30, motion=True):
    """O filtro que faz a faixa de cima seguir a pessoa, ou None sem trilha.

    `crop` não serve aqui, e a razão é do ffmpeg: as expressões de LARGURA e
    ALTURA do `crop` são avaliadas uma vez, na configuração do filtro -- só x e
    y correm por quadro. E é justamente o tamanho que muda: o rosto do PiP tem
    151px de altura na fonte e o de tela cheia tem 430, quase três vezes. Um
    recorte de tamanho fixo é uma narina num caso ou um ombro no outro.

    `zoompan` avalia `z`, `x` e `y` a cada quadro, então é ele. A região que
    ele recorta tem sempre a proporção da ENTRADA, por isso a entrada é
    pré-recortada para a proporção da faixa -- senão a faixa de cima sai
    esticada.
    """
    if not trilha:
        return None, None
    # Pré-recorte para a proporção da faixa, para o zoompan não esticar.
    alvo = banda_w / float(banda_h)
    cw, ch = float(sw), float(sh)
    if cw / ch > alvo:
        cw = ch * alvo
    else:
        ch = cw / alvo
    # E ele NÃO é centrado no quadro, que era o defeito seguinte.
    #
    # Numa fonte 16:9 e faixa de 1080x734, o pré-recorte tira 331px de largura
    # -- e centrado ele tira 165 de cada lado. A webcam desta live mora em
    # x 1440-1920, então 166px dela iam embora antes de o rastreio começar: o
    # zoompan se via obrigado a parar na borda e a faixa de cima saía com um
    # terço de página preta à esquerda do rosto. Dava para ver no render.
    #
    # Então o pré-recorte é centrado no ALCANCE DO RASTREIO: o meio entre a
    # posição mais à esquerda e a mais à direita que o rosto ocupa na janela.
    # Assim ele contém as duas pontas -- a webcam de canto e o rosto de tela
    # cheia -- em vez de contentar o meio geométrico, que não é onde ninguém
    # está.
    xs_fonte = [float(cx) * sw for _t, cx, _cy, _fh in trilha]
    ys_fonte = [float(cy) * sh for _t, _cx, cy, _fh in trilha]
    meio_x = (min(xs_fonte) + max(xs_fonte)) / 2.0
    meio_y = (min(ys_fonte) + max(ys_fonte)) / 2.0
    ox = min(max(meio_x - cw / 2.0, 0.0), max(0.0, sw - cw))
    oy = min(max(meio_y - ch / 2.0, 0.0), max(0.0, sh - ch))
    pre = (f"crop={int(cw) - int(cw) % 2}:{int(ch) - int(ch) % 2}"
           f":{int(round(ox))}:{int(round(oy))},")

    # O TETO, e ele é função do tamanho do rosto na fonte por via da escala.
    #
    # `banda_h*z/ch` é quanto o pixel da fonte é esticado para encher a faixa.
    # Fixando o esticão em ESCALA_FONTE_MAX, o teto do zoom cai sozinho de
    # 4,0 para 2,6 numa faixa de 734px e fonte de 1080 -- e quem chega perto
    # dele é só o rosto pequeno, porque o rosto grande já satisfaz o alvo em
    # z=1. É a regra que o dono pediu, escrita como aritmética: rosto pequeno
    # amplia até onde o pixel dá, rosto grande quase não amplia.
    teto = max(1.0, min(ZOOM_ROSTO_MAX, ESCALA_FONTE_MAX * ch / float(banda_h)))
    zs, xs, ys = [], [], []
    for t, cx, cy, fh in trilha:
        n = int(round(float(t) * fps))
        # O zoom que põe o rosto em ROSTO_ALTURA_NA_FAIXA da altura da faixa.
        # Um rosto de tela cheia já chega nisso sozinho e o zoom fica em 1, que
        # é a faixa mostrando o quadro inteiro -- redundante com a faixa de
        # baixo, e nunca um borrão. É o piso certo para o caso de dúvida.
        altura = max(0.01, float(fh) * sh / ch)
        zs.append((n, min(teto, max(1.0, ROSTO_ALTURA_NA_FAIXA / altura))))
        xs.append((n, min(1.0, max(0.0, (float(cx) * sw - ox) / cw))))
        ys.append((n, min(1.0, max(0.0, (float(cy) * sh - oy) / ch))))
    for serie in (zs, xs, ys):
        if serie[0][0] > 0:
            serie.insert(0, (0, serie[0][1]))
    ze = _expressao_por_quadro(zs)
    if motion:
        # O empurrão lento continua por cima do rastreio: um corte sem nenhuma
        # variação de escala lê como material bruto, e num trecho em que a
        # pessoa não sai do lugar o rastreio sozinho não varia nada.
        ze = f"({ze})*min(1+0.0004*on,1.06)"
    filtro = (f"{pre}zoompan=z='clip({ze},1,{teto:.4f})'"
              f":x='clip(({_expressao_por_quadro(xs)})*iw-(iw/zoom/2),0,"
              f"iw-iw/zoom)'"
              f":y='clip(({_expressao_por_quadro(ys)})*ih-(ih/zoom)*"
              f"{ROSTO_NA_FAIXA},0,ih-ih/zoom)'"
              f":d=1:s={banda_w}x{banda_h}:fps={fps},setsar=1")
    return filtro, {"pontos": len(trilha),
                    "zoom_min": round(min(z for _n, z in zs), 2),
                    "zoom_max": round(max(z for _n, z in zs), 2),
                    "zoom_teto": round(teto, 2)}


def decide_enquadramento(medidas, tem_pip):
    """(modo, porquê) a partir do que foi medido quadro a quadro.

    Separada da medição de propósito: é a regra do dono, e uma regra que só se
    exercita com OpenCV, ffmpeg e um arquivo de vídeo é uma regra que ninguém
    confere. Aqui ela é aritmética sobre uma lista de números.

    `medidas` é a planura de cada quadro lido; `tem_pip` diz se foi encontrada
    uma webcam de canto estável.

    QUEM DECIDE É A WEBCAM DE CANTO, e a planura deixou de ser portão em
    17/09/2026. A regra antiga exigia as duas coisas -- área chapada E webcam --
    e ela foi escrita olhando uma live de navegador, onde as duas andam juntas.
    Numa live de GAMEPLAY elas não andam: o jogo é escuro e texturizado, mede
    planura abaixo do limiar, e o clipe caía em "normal" com a tela cortada nas
    duas bordas. Medido nos dois clipes reprovados desta live: na fonte lê-se
    `YOU ARE DEAD - Press Space to spectate` e no clipe entregue lê-se
    `AD - Press Space to spectate`.

    SEM webcam de canto o modo NÃO muda, e essa metade da regra fica de pé
    inteira, com o teste que a sustenta: planura alta sozinha não quer dizer
    tela compartilhada. Um `testsrc2` do ffmpeg mede 0,8 de planura, e um
    desenho animado ou uma captura de jogo também são feitos de áreas chapadas.
    A webcam de canto é o sinal que separa uma coisa da outra, porque desenho
    não tem webcam no canto.

    E COM webcam de canto a planura não é mais consultada. O que segura a
    pessoa falando de frente para a câmera -- o caso que o dono mandou não
    quebrar -- não é a planura e nunca foi: são os dois limites de tamanho de
    `tela_compartilhada`. Um rosto acima de `PIP_ROSTO_AREA_MAX` (5% do quadro)
    não vira candidato, e uma caixa que cresceu além de `PIP_AREA_MAX` (30%) é
    recusada -- numa câmera cheia `_cresce_pip` não encontra interface onde
    parar e cresce até o quadro todo. Quem fala de frente não tem webcam de
    canto por construção: ela seria o próprio rosto, grande e no meio.
    """
    if not medidas:
        return "normal", ("no frame was read, so nobody looked at this "
                          "material -- it takes the usual path")
    telas = [p for p in medidas if p >= PLANURA_TELA]
    fracao = len(telas) / len(medidas)
    quantos = f"{len(telas)} of {len(medidas)} sampled frames read as screen " \
              f"({fracao:.0%}, flatness >= {PLANURA_TELA})"
    if not tem_pip:
        if fracao < TELA_FRACAO_DUVIDA:
            return "normal", (f"{quantos}: this is ordinary video, and the "
                              f"framing follows the face as always")
        return "normal", (
            f"{quantos}, but there is NO stable corner webcam in any of them. "
            f"A flat area on its own is not a shared screen -- animation and "
            f"game capture are flat too -- so the framing does not change. If "
            f"this material IS a screen and the cut lands in the middle of it, "
            f"pass --crop to pick the band by hand")
    return "dividido", (
        f"there IS a stable corner webcam, so this is a live with a presenter "
        f"and a screen: the whole screen goes in uncropped and the person goes "
        f"below it. {quantos} -- the flatness is reported and no longer "
        f"decides, because game capture is dark and textured and would read as "
        f"ordinary video while the screen got sliced on both edges")


def tela_compartilhada(source, start, length, samples=None):
    """O que este material É, olhado quadro a quadro.

    Devolve {"modo", "porque", "pip", "planuras", "faces"} -- e `faces` no mesmo
    formato de `_face_centers`, para que o caminho normal não pague uma segunda
    passada de ffmpeg pela mesma janela.
    """
    # `quadros` começa None e não 0, e a diferença é a de sempre neste arquivo:
    # None é "nem cheguei a olhar" (não há detector nesta máquina) e 0 é "olhei
    # e não abriu um quadro sequer", que é erro de quem chamou e tem de parar.
    # UMA AMOSTRA POR SEGUNDO, e o número saiu do mosaico e não do gosto.
    #
    # Com dez amostras num clipe de vinte segundos a trilha tem um ponto a cada
    # 2,5s, e uma live troca de layout mais rápido que isso: o primeiro render
    # com rastreio ainda tinha 3 dos 8 quadros do mosaico sem rosto na faixa de
    # cima, todos em vãos entre amostras. Uma por segundo fecha esses vãos.
    # O teto de 24 é o preço: cada amostra é uma detecção de rosto, e na imagem
    # emulada elas custam ~0,2s cada.
    #
    # DUAS por segundo desde 15/09/2026, e o número saiu do mosaico outra vez.
    # Cada amostra vale meio intervalo para cada lado, então a uma por segundo
    # a fronteira entre "isto é tela" e "isto é a pessoa em tela cheia" tem meio
    # segundo de erro -- e meio segundo é o suficiente para um quadro do mosaico
    # cair do lado errado. Medido na janela 323-347: aos 11,25s a fonte já era
    # webcam cheia e a amostra de 11,5s ainda dizia tela, então o quadro saiu
    # dividido, com um close borrado de ombro em cima e a pessoa inteira
    # embaixo. A duas por segundo o erro cai para 0,25s e esse quadro sai certo.
    # O teto vai a 40: são ~8s de detecção na imagem emulada, contra ~4s.
    if samples is None:
        samples = max(8, min(40, int(round(float(length) * 2))))
    saiu = {"modo": "normal", "porque": "", "pip": None, "planuras": [],
            "faces": None, "rosto": None, "quadros_de_tela": 0, "quadros": None}
    achado = _face_detector()
    if achado is None:
        ok, porque = face_detection_status()
        saiu["porque"] = (f"cannot tell whether this is a shared screen "
                          f"without a face detector ({porque})")
        return saiu
    cv2, det = achado
    tmp, quadros_lidos = _quadros_coloridos(source, start, length, samples)
    passo = float(length) / max(1, samples)
    try:
        faces_todas, candidatos, planuras, tempos = [], [], [], []
        indices = []                       # o índice de leitura de cada quadro
        por_quadro = []                    # (t relativo, planura, [rostos])
        from PIL import Image as _Image
        for i, (quando, png) in enumerate(quadros_lidos):
            try:
                img = _Image.open(png)
                img.load()
            except Exception:
                continue
            mapa = _mapa_planura(img)
            rows, cols = len(mapa), len(mapa[0])
            p = sum(sum(l) for l in mapa) / (rows * cols)
            planuras.append(p)
            neste = []
            quadro = cv2.imread(png)
            if quadro is None:
                tempos.append(quando); indices.append(i)
                por_quadro.append((quando, p, neste))
                continue
            h, w = quadro.shape[:2]
            det.setInputSize((w, h))
            _n, achadas = det.detect(quadro)
            for f in (achadas if achadas is not None else []):
                fw, fh = float(f[2]), float(f[3])
                cx = (float(f[0]) + fw / 2) / w
                cy = (float(f[1]) + fh / 2) / h
                area = (fw * fh) / (w * h)
                faces_todas.append((cx, area))
                if fw / w < ROSTO_MIN_LARGURA:
                    continue               # adesivo, avatar do chat, miniatura
                neste.append((cx, cy, fw / w, fh / h))
                # O portão de planura saiu daqui em 17/09/2026, e o motivo é o
                # mesmo que tirou a planura de `decide_enquadramento`: uma live
                # de GAMEPLAY não é chapada, e exigir que o quadro já leia como
                # tela para só então procurar a webcam é procurar a chave
                # debaixo do poste. Medido nas duas janelas reais: com o portão
                # a webcam é achada em 13 e 10 quadros, sem ele em 15 e 11 --
                # mesma caixa, nos dois casos (0,75-1,00 x 0,00-0,33).
                #
                # O que continua segurando a pessoa falando de frente são os
                # dois limites de TAMANHO, que não dependem de planura: um rosto
                # acima de PIP_ROSTO_AREA_MAX não é webcam de canto, e uma caixa
                # que cresceu além de PIP_AREA_MAX é câmera cheia.
                if area > PIP_ROSTO_AREA_MAX:
                    continue
                caixa = _cresce_pip(mapa, cy * rows, cx * cols)
                if (caixa[2] - caixa[0]) * (caixa[3] - caixa[1]) > PIP_AREA_MAX:
                    continue
                candidatos.append((i, caixa, (cx, cy)))
            tempos.append(quando); indices.append(i)
            por_quadro.append((quando, p, neste))
        pip, em_quantos, rosto, quadros_com_pip = _agrupa_pips(candidatos)
        # Uma webcam fixa aparece em boa parte dos quadros AMOSTRADOS; uma foto
        # na página aparece em um ou dois. Ver PIP_FRACAO_MIN, que carrega a
        # medição das duas janelas e o porquê de o denominador ter deixado de
        # ser "quadros de tela".
        telas = sum(1 for p in planuras if p >= PLANURA_TELA)
        lidos = max(1, len(planuras))
        if pip is not None and (em_quantos < PIP_QUADROS_MIN
                                or em_quantos < PIP_FRACAO_MIN * lidos):
            pip = None
            rosto = None
        saiu["planuras"] = [round(p, 3) for p in planuras]
        saiu["tempos"] = list(tempos)
        saiu["quadros"] = len(planuras)
        saiu["quadros_de_tela"] = telas
        # Os trechos em que a fonte NÃO é uma tela. Ver `trechos_de_camera`:
        # neles o dividido não tem o que dividir, e insistir nele põe a mesma
        # pessoa duas vezes no mesmo quadro.
        # A webcam de canto foi achada NESTE quadro? É a mesma pergunta que
        # `candidatos` já respondeu -- rosto pequeno, caixa pequena, planura de
        # tela -- e a resposta vem de `_agrupa_pips`, que sabe quais quadros
        # entraram no grupo vencedor. Reaproveitá-la evita um segundo critério
        # que discordaria do primeiro.
        com_webcam = [n in (quadros_com_pip if pip else set()) for n in indices]
        # Há alguém GRANDE neste quadro? É a pergunta que decide se a fonte
        # saiu da tela e foi para a câmera, e ela é respondida com a mesma
        # medida que separa uma webcam de canto de uma pessoa em close:
        # `PIP_ROSTO_AREA_MAX`. `por_quadro` já traz só os rostos acima do piso
        # de tamanho, então adesivo e avatar de chat ficaram de fora antes.
        rosto_grande = [any(r[2] * r[3] > PIP_ROSTO_AREA_MAX
                            for r in (item[2] or []))
                        for item in por_quadro]
        saiu["camera"] = trechos_de_camera(tempos, planuras, float(length),
                                           com_webcam=com_webcam,
                                           rosto_grande=rosto_grande)
        saiu["faces"] = faces_todas
        saiu["rosto"] = None if rosto is None else tuple(round(v, 4) for v in rosto)
        saiu["pip"] = None if pip is None else tuple(round(v, 4) for v in pip)
        saiu["trilha"] = trilha_do_rosto(por_quadro, pip)
        saiu["modo"], saiu["porque"] = decide_enquadramento(planuras, pip is not None)
        if saiu["pip"]:
            saiu["porque"] += (f". A webcam está em x {saiu['pip'][0]:.0%}-"
                               f"{saiu['pip'][2]:.0%}, y {saiu['pip'][1]:.0%}-"
                               f"{saiu['pip'][3]:.0%} do quadro, achada em "
                               f"{em_quantos} quadro(s)")
        return saiu
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def trechos_de_camera(tempos, planuras, length, com_webcam=None,
                      rosto_grande=None):
    """[(de, ate)] -- quando a fonte NÃO é uma tela, no relógio do clipe.

    O terceiro caso que o dividido não tinha, e ele saiu do mosaico do clipe
    que o dono recebeu.

    Numa live a fonte alterna: tela com webcam de canto, webcam em tela cheia,
    foto em tela cheia. O dividido resolve o primeiro caso -- pessoa em cima,
    tela embaixo. Nos outros dois não há tela para pôr embaixo: a faixa de
    baixo recebe o MESMO quadro que a de cima, e o que sai é a pessoa duas
    vezes, grande em cima e pequena embaixo. Medido em 15/09/2026: na janela
    343-369 a fonte é câmera em 0,0-0,5s, 2,2-3,5s, 5,0-5,7s e 13,2-14,0s; na
    janela 1218-1242 em 0-2s e 10,5-14,5s -- 3 dos 8 quadros do mosaico.

    Nesses trechos o enquadramento certo é o de SEMPRE: uma faixa vertical
    centrada no rosto, ocupando a imagem inteira. A pessoa aparece uma vez, e
    grande. Aqui só se dizem os trechos; quem os desenha é `cadeia_dividida`.

    O SINAL MUDOU EM 17/09/2026, e a troca é o conserto do defeito 1 do clipe
    que o dono reprovou. Antes um quadro era "câmera" quando tinha planura
    baixa, ou quando o detector não achava a webcam nele duas vezes seguidas.
    Os dois erram na mesma direção, e erram feio numa live de GAMEPLAY:

      - planura baixa é a assinatura de um jogo escuro e texturizado, não de
        uma câmera. Medido: 21 dos 39 quadros da janela 481-502 ficam abaixo
        do limiar, e nenhum deles é câmera;
      - "não achei a webcam neste quadro" é uma falha do detector, não uma
        troca de fonte. Medido: a webcam está em todos os quadros das duas
        janelas e o rosto dentro dela só passa nos filtros em 38% e 28%.

    O resultado somado foi um trecho de câmera cobrindo 15,3s de 20,9s -- 73%
    do clipe --, e nesse trecho a imagem é recortada nas duas bordas. Foi ele
    que comeu o `YOU ARE DE` de `YOU ARE DEAD`.

    `rosto_grande` diz, quadro a quadro, se há um rosto MAIOR que
    `PIP_ROSTO_AREA_MAX` -- ou seja, alguém que é o assunto do quadro e não uma
    caixinha de canto. É a definição direta de "a fonte está mostrando a
    pessoa", sem passar por nenhuma medida que o material possa imitar. Medido
    nas duas janelas: o maior rosto de qualquer quadro é 0,0128, quatro vezes
    abaixo do limiar, e os dois trechos somam zero -- que é a resposta certa.
    E ele continua pegando o caso que criou esta função: uma pessoa que ocupa
    dois terços do quadro tem rosto muito acima de 0,05.

    As fronteiras NÃO ficam no meio entre duas amostras que discordam: o trecho
    de câmera se estende até a amostra de tela vizinha, meio passo a mais de
    cada lado. A escolha é assimétrica porque o ERRO é assimétrico, e o mosaico
    mostrou os dois lados:

      - dividir um quadro que já é câmera entrega a pessoa duas vezes e um
        close borrado de ombro em cima. É o defeito inteiro, e no mosaico de
        15/09/2026 ele aparecia a 5,0s, 0,1s antes da fronteira calculada pelo
        meio;
      - enquadrar um quadro de tela como câmera entrega uma faixa vertical da
        página por um quarto de segundo. Fica pior do que o dividido e não é
        nenhum dos três defeitos.

    Então na dúvida vale a câmera. Com amostragem a 2Hz a dúvida dura 0,25s.
    """
    if not tempos or len(tempos) != len(planuras):
        return []
    n = len(tempos)
    grande = [bool(rosto_grande is not None and i < len(rosto_grande)
                   and rosto_grande[i]) for i in range(n)]

    def camera(i):
        if rosto_grande is None:
            # Ninguém mediu os rostos: não há como afirmar que a fonte virou
            # câmera, e inventar o trecho é pior do que não tê-lo. Sem trecho,
            # o layout dividido vale o clipe inteiro -- que é o que ele já faz
            # bem. Com um trecho falso, a imagem é recortada nas bordas.
            return False
        if not grande[i]:
            return False
        # A DETECÇÃO pisca, e uma piscada sozinha não é a fonte mudando: é o
        # YuNet achando um rosto grande por um quadro. Trocar de layout por
        # meio segundo por causa disso é um piscar de tela no clipe. Duas
        # amostras seguidas é a fonte mudando. É a mesma regra que
        # `trilha_do_rosto` já usa para segurar o enquadramento numa falha
        # isolada, aplicada à outra decisão que depende do mesmo detector.
        return (i > 0 and grande[i - 1]) or (i + 1 < n and grande[i + 1])

    marcas = [(float(tempos[i]), camera(i)) for i in range(n)]
    marcas.sort(key=lambda m: m[0])
    trechos = []
    for i, (t, e_camera) in enumerate(marcas):
        if not e_camera:
            continue
        de = 0.0 if i == 0 else marcas[i - 1][0]
        ate = float(length) if i == len(marcas) - 1 else marcas[i + 1][0]
        if trechos and de - trechos[-1][1] < 1e-6:
            trechos[-1] = (trechos[-1][0], ate)
        else:
            trechos.append((de, ate))
    return [(round(max(0.0, a), 3), round(min(float(length), b), 3))
            for a, b in trechos if b > a]


def _corte_sem_webcam(sw, sh, pip):
    """(x, y, w, h) da fonte SEM a webcam de canto, ou None sem PiP.

    "Saiu com duas caras" -- as palavras do dono sobre o clipe de 15/09/2026, e
    ele estava certo. A faixa de cima leva a webcam recortada e ampliada; a de
    baixo levava o quadro 16:9 INTEIRO, e o quadro inteiro CONTÉM a webcam. O
    rosto da pessoa aparecia duas vezes no mesmo quadro, grande em cima e
    pequeno no canto de baixo -- em 6 dos 8 quadros do mosaico (6,2s, 8,8s,
    11,2s, 13,8s, 16,2s, 18,8s).

    O conserto é subtração: a faixa de baixo perde a lasca onde a webcam mora.
    Qual das quatro lascas sai é MEDIDO, não escolhido -- sai a menor, que é a
    que custa menos imagem. Medido nas três janelas reais desta live, o PiP fica
    em x 75%-100% e y 67%-100%: a lasca da direita custa 25% da largura e a de
    baixo custa 33% da altura, então sai a da direita e a página perde a coluna
    de comentários, não a metade de baixo do texto.

    Recortar em vez de cobrir por dois motivos. Cobrir com fundo borrado deixa
    um buraco retangular no canto da página, que lê como defeito; e recortar
    devolve proporção -- 1440x1080 encaixado por largura dá 810px de tela, mais
    alta e mais legível do que os 608 de antes.
    """
    if not pip:
        return None
    sw, sh = float(sw), float(sh)
    px0, py0, px1, py1 = [min(1.0, max(0.0, float(v))) for v in pip]
    if px1 <= px0 or py1 <= py0:
        return None
    # As quatro lascas que excluem o PiP, em fração do lado que perdem.
    lascas = [(1.0 - px0, "direita"), (px1, "esquerda"),
              (1.0 - py0, "baixo"), (py1, "cima")]
    quanto, qual = min(lascas)
    if quanto >= 0.95:
        return None                 # não sobra quadro nenhum: melhor não cortar
    if qual == "direita":
        x, y, w, h = 0.0, 0.0, px0 * sw, sh
    elif qual == "esquerda":
        x, y, w, h = px1 * sw, 0.0, (1.0 - px1) * sw, sh
    elif qual == "baixo":
        x, y, w, h = 0.0, 0.0, sw, py0 * sh
    else:
        x, y, w, h = 0.0, py1 * sh, sw, (1.0 - py1) * sh
    w = max(2, int(round(w)) - int(round(w)) % 2)
    h = max(2, int(round(h)) - int(round(h)) % 2)
    return (int(round(x)), int(round(y)), w, h)


def layout_dividido(width, height, sw, sh, pip=None, rosto=None, legenda=True):
    """A geometria do clipe dividido, em pixels do quadro de saída.

    Reescrita em 17/09/2026, depois de dois clipes reprovados. O layout de
    antes era webcam em cima e tela embaixo, com a tela RECORTADA para não
    levar a webcam junto. O novo é o contrário nas duas coisas, e as duas
    inversões são pedido do dono com medição atrás.

    De cima para baixo, num quadro de 1080x1920:

        0     interface do app (280px) -- só fundo borrado, nada nosso
        280   TELA: a fonte INTEIRA, encaixada por largura, NADA cortado
        840   folga
        856   ROSTO: a webcam na proporção dela, centrada
        1240  legenda queimada (2 linhas)
        1560  folga da legenda + interface do app

    A TELA NÃO É MAIS RECORTADA. `_corte_sem_webcam` tirava a lasca onde a
    webcam mora, para não pôr a mesma cara duas vezes no quadro. Nesta live a
    webcam é uma caixa POR CIMA do jogo, não um lado a lado: a lasca custaria
    um quarto da cena, com o HUD de munição dentro. O dono escolheu a cara
    repetida -- ela já está repetida no layout do próprio streamer -- e a cena
    inteira. Ver a nota `duas caras` em `_corte_sem_webcam`, que continua no
    arquivo porque continua certa para o caso lado a lado.

    O ROSTO FICA EMBAIXO, e isto é medido, não gosto. Nas maquetes do quadro
    real dos 4s, com o rosto em cima a cara grande e a webcam dentro do jogo
    ficam a 297px uma da outra -- 15% do quadro, e leem como erro de render.
    Com o rosto embaixo, 753px. E o jogo tem texto próprio no pé do quadro
    (`YOU ARE DEAD`, Health/Energy): com a tela embaixo esse texto encosta na
    nossa legenda, com a tela em cima a faixa do rosto separa os dois.

    A CONTA COMEÇA PELO ROSTO. A faixa da pessoa tem o piso de
    `ROSTO_FAIXA_MIN` e a tela fica com o resto, encolhendo se precisar. Antes
    era ao contrário -- a tela entrava por largura e a pessoa ficava com a
    sobra -- e era assim que o rosto saía pequeno.

    NADA NOSSO ENTRA NA INTERFACE DO APP, nem em cima nem embaixo. É ordem do
    dono de 17/09/2026, depois de olhar a formatação do Shorts. A imagem
    começa na margem de topo e acaba no teto da legenda; sem legenda acaba na
    margem de base, e não mais no pé do quadro. O que sobra nessas faixas é o
    fundo borrado, que desde este mesmo dia não clipa mais em preto.

    `rosto` é (cx, cy) em fração do quadro de origem e serve para escolher onde
    dentro do PiP a faixa corta, quando há o que cortar.
    """
    piso = S.caption_piso(height)
    linha = int(S.caption_size(height) * S.LEADING)
    teto_legenda = height - piso - 2 * linha
    marge = S.margens(width, height)
    topo = int(marge["top"])
    # Onde a IMAGEM acaba. Com legenda é o teto dela; sem legenda é onde a
    # interface do app começa.
    #
    # Até 17/09/2026 isto era `height` sem legenda: a imagem descia até o pé do
    # quadro porque, sem texto a proteger, não havia o que proteger. A ordem
    # nova do dono é outra e é mais simples de conferir: nada nosso atrás dos
    # botões do aplicativo, tenha ou não legenda.
    pe_da_imagem = teto_legenda if legenda else height - int(marge["bottom"])
    folga = max(6, height // 120)
    disponivel = pe_da_imagem - topo - folga
    minimo_rosto = int(height * ROSTO_FAIXA_MIN) if pip else 0
    # A tela entra por LARGURA: é isso que garante que nada dela é cortado.
    tela_w = width
    tela_h = int(round(tela_w * float(sh) / float(sw)))
    if disponivel - tela_h < minimo_rosto:
        tela_h = max(2, disponivel - minimo_rosto)
        tela_w = min(width, int(round(tela_h * float(sw) / float(sh))))
        tela_h = int(round(tela_w * float(sh) / float(sw)))
    tela_w -= tela_w % 2
    tela_h -= tela_h % 2
    tela_x = (width - tela_w) // 2
    # Sem webcam não há faixa de pessoa, e a tela fica centrada no que sobra em
    # vez de encostada no topo: encostada, ela deixaria todo o vazio de um lado
    # só, que lê como imagem fora do lugar.
    tela_y = topo if pip else topo + max(0, (disponivel + folga - tela_h) // 2)
    faixa = {"tela": (tela_x, tela_y, tela_w, tela_h),
             "legenda_topo": teto_legenda,
             "pe_da_imagem": pe_da_imagem,
             "topo_da_imagem": topo,
             "legenda_reservada": bool(legenda)}
    if not pip:
        # A chave continua existindo e continua sendo a faixa que NÃO tem
        # webcam, porque catorze ramos do `cut` e o sidecar a leem. Ela é a
        # faixa vazia abaixo da tela.
        faixa["webcam"] = (0, tela_y + tela_h, width, 0)
        return faixa
    banda_y = tela_y + tela_h + folga
    banda_h = max(2, pe_da_imagem - banda_y)
    px0, py0, px1, py1 = [min(1.0, max(0.0, float(v))) for v in pip]
    pw = max(2.0, (px1 - px0) * sw)
    ph = max(2.0, (py1 - py0) * sh)
    # A CAIXA DO ROSTO TEM A PROPORÇÃO DO PiP, e isto foi decidido renderizando
    # as duas alternativas e olhando, em 17/09/2026.
    #
    # A tentação é alargá-la até o teto de `ESCALA_FONTE_MAX` (864px nesta
    # fonte), porque isso encurtaria o recorte na vertical e deixaria de fora a
    # barra de "Último sub" que mora logo abaixo da câmera. Renderizado: sai
    # MUITO pior. Quem recorta de fato é `zoompan_do_rosto`, que pré-recorta do
    # quadro INTEIRO na proporção da faixa -- e com uma faixa de 2,25:1 e o
    # rosto em x 86%, o recorte se estende para a esquerda e traz meia tela de
    # jogo junto, com a pessoa encostada na borda direita.
    #
    # Na proporção do PiP (1,33:1 aqui) o recorte tem a forma da própria
    # webcam, a pessoa fica centrada, e o que entra a mais é a barra de sub do
    # streamer -- que é a marca dele, não um defeito nosso.
    caixa_h = banda_h
    caixa_w = int(round(caixa_h * pw / ph))
    teto_w = max(2, min(width - 2 * folga, int(pw * ESCALA_FONTE_MAX)))
    if caixa_w > teto_w:
        caixa_w = teto_w
        caixa_h = max(2, int(round(caixa_w * ph / pw)))
    caixa_w -= caixa_w % 2
    caixa_h -= caixa_h % 2
    faixa["webcam"] = ((width - caixa_w) // 2,
                       banda_y + max(0, (banda_h - caixa_h) // 2),
                       caixa_w, caixa_h)
    faixa["faixa_rosto"] = (0, banda_y, width, banda_h)
    # O recorte na fonte. Quando a caixa de saída tem a proporção do PiP -- que
    # é o caso comum -- este recorte é o PiP inteiro e não tira nada. Ele só
    # morde quando o teto de escala encolheu a caixa e mudou a proporção.
    alvo = caixa_w / float(caixa_h)
    x0, y0 = px0 * sw, py0 * sh
    if pw / ph >= alvo:
        ch = ph
        cw = ph * alvo
        alvo_x = (rosto[0] * sw - cw / 2) if rosto else (x0 + (pw - cw) / 2)
        cx = min(max(alvo_x, x0), x0 + pw - cw)
        cy = y0
    else:
        cw = pw
        ch = pw / alvo
        cx = x0
        alvo_y = (rosto[1] * sh - ch * ROSTO_NA_FAIXA) if rosto else (y0 + (ph - ch) / 2)
        cy = min(max(alvo_y, y0), y0 + ph - ch)
    faixa["corte_webcam"] = (int(round(cx)), int(round(cy)),
                             max(2, int(round(cw)) - int(round(cw)) % 2),
                             max(2, int(round(ch)) - int(round(ch)) % 2))
    return faixa


def _janela_de_tempo(trechos):
    """A expressão `enable` do ffmpeg para uma lista de (de, ate), ou None."""
    if not trechos:
        return None
    return "+".join(f"between(t,{float(a):.3f},{float(b):.3f})"
                    for a, b in trechos)


def cadeia_dividida(width, height, faixa, motion=True, length=None,
                    trilha=None, sw=None, sh=None, cheios=None, cheio_x=0.5):
    """A cadeia de filtros do clipe dividido, de uma entrada e uma saída.

    Sai como `split` + dois `overlay` sobre o fundo borrado, e não como
    `vstack` + `pad`: o pad pintaria os 472px abaixo da tela de preto, e preto
    no pé do quadro é exatamente o que `barras_pretas` reprova.

    A faixa de cima sai do RASTREIO do rosto quando há trilha (ver
    `zoompan_do_rosto`), e do recorte fixo do PiP só quando não há -- que é o
    caso em que a detecção achou a webcam pela planura e não achou rosto em
    quadro nenhum.

    A de baixo sai RECORTADA quando o layout achou a webcam (`corte_tela`): sem
    isso a pessoa aparece duas vezes no mesmo quadro. Ver `_corte_sem_webcam`.

    `cheios` são os trechos em que a fonte não é uma tela (ver
    `trechos_de_camera`). Neles o dividido não tem o que dividir, então uma
    TERCEIRA camada -- a faixa vertical de sempre, centrada no rosto e do
    tamanho da imagem inteira -- entra por cima das duas e as esconde, pelo
    tempo que durar. É uma camada só, com `enable`, e ela não existe quando não
    há trecho de câmera nenhum.
    """
    wx, wy, ww, wh = faixa["webcam"]
    tx, ty, tw, th = faixa["tela"]
    corte = faixa.get("corte_webcam")
    recorte_tela = faixa.get("corte_tela")
    quando_cheio = _janela_de_tempo(cheios)
    # O RECORTE FIXO GANHA DO RASTREIO quando há uma webcam de canto, e isto
    # foi medido renderizando os dois e olhando, em 17/09/2026.
    #
    # A webcam de canto é uma caixa PARADA na fonte. O rastreio existe para
    # seguir um rosto que anda pelo quadro, e aplicá-lo a uma caixa que não
    # anda só acrescenta tremor: ele interpola posição E zoom entre amostras, e
    # nas amostras em que o YuNet não acha o rosto `trilha_do_rosto` marca o
    # quadro inteiro, o que puxa a rampa. Na janela 481-502, comparados quadro
    # a quadro em 0,5s / 1,3s / 2,2s / 4s / 8s / 12s: com rastreio a cara SOME
    # em 1,3s (sobra a barra de sub, ampliada) e sai da borda em 2,2s; com o
    # recorte fixo os seis tempos saem estáveis e enquadrados.
    #
    # O rastreio não foi apagado: ele continua sendo o caminho quando não há
    # caixa de webcam para recortar. E o caso que o fez nascer -- a janela que
    # ALTERNA entre webcam de canto e câmera cheia -- hoje é coberto pelo
    # `cheios`, que desenha a fonte inteira por cima nesses trechos.
    seguidor, rastreio = (None, None)
    if trilha and sw and sh and not corte:
        seguidor, rastreio = zoompan_do_rosto(trilha, ww, wh, sw, sh,
                                              motion=bool(motion))
    tem_webcam = bool(seguidor or corte)
    # `split` com uma saída a mais do que se consome não é aviso: o ffmpeg
    # recusa o filtergraph inteiro ("expected to have exactly 1 input and 1
    # output"), e recusa só no caso SEM webcam -- que é o ramo raro, o pior
    # lugar para um erro esperar.
    saidas = 2 + (1 if tem_webcam else 0) + (1 if quando_cheio else 0)
    partes = ["fps=30,split=%d[dvbg]%s[dvsc]%s" % (
        saidas, "[dvwc]" if tem_webcam else "",
        "[dvfl]" if quando_cheio else "")]
    # O FUNDO NÃO PODE CLIPAR, e este é o conserto da "tarja preta" que o dono
    # reprovou em 17/09/2026 -- que não era tarja nenhuma.
    #
    # Medido no quadro dos 4s do clipe entregue: 93,1% dos pixels do rodapé são
    # exatamente 0. A fonte é uma cena de jogo escura, luma mediana 15 de 255, e
    # `eq=brightness=-0.10` subtrai ~25: a cena inteira vai a zero e encosta no
    # fundo da escala. O que se via como barra preta era o fundo borrado depois
    # de ser escurecido para baixo do fim da régua.
    #
    # `colorlevels` faz a conta certa porque ela é AFIM e não aditiva:
    # saída = 0,10 + entrada * 0,55. O preto absoluto da fonte vira 26 e não 0,
    # o branco vira 166, e a textura do borrão sobrevive em cena escura. Um
    # `eq=brightness` não tem como fazer isso: ele só desloca, e deslocar para
    # baixo é exatamente o que clipa.
    partes.append(
        f"[dvbg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=luma_radius=30:luma_power=2"
        f":chroma_radius=15:chroma_power=2,"
        f"colorlevels=romin=0.10:gomin=0.10:bomin=0.10"
        f":romax=0.65:gomax=0.65:bomax=0.65,eq=saturation=0.75,"
        f"setsar=1[dvbgb]")
    if corte:
        cx, cy, cw, ch = corte
        zoom = ""
        if motion:
            # O zoom vive DENTRO da faixa da webcam, e só nela. Passá-lo no
            # composto empurraria as duas faixas para fora do lugar -- a tela
            # sairia cortada nas beiradas, que é o defeito que este modo existe
            # para não ter. A tela fica parada de propósito: ela é texto, e
            # texto que anda não se lê.
            quadros = max(1.0, float(length or 1) * 30)
            passo = (1.06 - 1.0) / quadros
            zoom = (f",zoompan=z='min(1+{passo:.8f}*on,1.06)'"
                    f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
                    f":s={ww}x{wh}:fps=30,setsar=1")
        partes.append(
            f"[dvwc]crop={cw}:{ch}:{cx}:{cy},"
            f"scale={ww}:{wh}:force_original_aspect_ratio=increase,"
            f"crop={ww}:{wh}{zoom},setsar=1[dvwcb]")
    elif seguidor:
        partes.append(f"[dvwc]{seguidor}[dvwcb]")
    # A lasca da webcam continua suportada aqui, mas `layout_dividido` não a
    # produz mais desde 17/09/2026: nesta live a webcam está POR CIMA do jogo e
    # tirar a lasca custaria um quarto da cena. O ramo fica porque
    # `_corte_sem_webcam` continua certo para o caso lado a lado, e porque
    # apagá-lo seria apagar a única saída que existe quando ele voltar.
    sem_webcam = ""
    if recorte_tela:
        rx, ry, rw, rh = recorte_tela
        sem_webcam = f"crop={rw}:{rh}:{rx}:{ry},"
    # `increase` + `crop` e não um `scale` seco: um scale seco ESTICARIA a
    # página quando a caixa não estiver na proporção exata da fonte. Ela está,
    # por construção -- `layout_dividido` a calcula por largura -- e o que sobra
    # é o arredondamento para largura e altura pares: no máximo 1px de um lado.
    # Um pixel não é o defeito que este layout conserta; 68% da largura era.
    partes.append(f"[dvsc]{sem_webcam}scale={tw}:{th}"
                  f":force_original_aspect_ratio=increase,"
                  f"crop={tw}:{th},setsar=1[dvscb]")
    ultimo = "dvbgb"
    if tem_webcam:
        partes.append(f"[{ultimo}][dvwcb]overlay={wx}:{wy}[dvt1]")
        ultimo = "dvt1"
    partes.append(f"[{ultimo}][dvscb]overlay={tx}:{ty}[dvt2]")
    ultimo = "dvt2"
    if quando_cheio:
        # Nos trechos em que a fonte NÃO é uma tela, a imagem inteira cobre as
        # duas faixas: elas mostrariam a mesma pessoa duas vezes.
        #
        # ESTE RAMO ERA O DEFEITO 1, e ele custou o clipe que o dono reprovou
        # em 17/09/2026. Ele desenhava de y=0 até `pe_da_imagem` (1240 de 1920)
        # e deixava os 680px de baixo com o fundo e nada dentro. Naquela janela
        # os trechos de câmera cobrem 15,3s de 20,9s -- 73% do clipe --, então
        # o que se via não era "um pedaço": era o clipe inteiro com um rodapé
        # órfão de 19,8%, que numa cena escura lê como tarja preta.
        #
        # O conserto é a MOLDURA FICAR PARADA. A imagem de câmera entra na
        # mesma janela que as faixas ocupam, do topo da imagem ao pé dela, e
        # não numa caixa própria começando em zero. Assim a composição não
        # respira quando a fonte alterna, não sobra faixa órfã nenhuma, e o que
        # está fora da janela é interface do app nos dois casos.
        y0 = int(faixa.get("topo_da_imagem") or 0)
        alto = max(2, int(faixa.get("pe_da_imagem") or (ty + th)) - y0)
        fx = min(1.0, max(0.0, float(cheio_x)))
        # Cobertura, e aqui ela é a escolha CERTA: o assunto é uma pessoa, não
        # uma tela, e recortar uma pessoa numa faixa vertical centrada no rosto
        # é exatamente o que o caminho normal deste projeto já faz bem. "Nada
        # cortado" é a regra da TELA, porque tela tem texto nas bordas.
        partes.append(
            f"[dvfl]scale={width}:{alto}:force_original_aspect_ratio=increase,"
            f"crop={width}:{alto}:(in_w-out_w)*{fx:.4f}:(in_h-out_h)*0.5,"
            f"setsar=1[dvflb]")
        partes.append(
            f"[{ultimo}][dvflb]overlay=0:{y0}:enable='{quando_cheio}'[dvt3]")
        ultimo = "dvt3"
    partes.append(f"[{ultimo}]setsar=1")
    return ";".join(partes), rastreio


def cut(source, out, rules, start, end, caption_srt=None, hook=None,
        track=None, track_start=None, sound="platform", crop=None,
        shots=None, language=None, motion=True, cover_footer=None,
        asked_s=None):
    """One clip, with the campaign's numbers rather than a house style.

    Duration is clamped to the campaign's window before a frame is written: a
    render that ends one second over the limit costs the whole submission, and
    the clipper is the one who finds out. Burned text stays inside the safe area
    for the same reason the resolution comes from the rule set, which is that
    the platform's furniture is not negotiable and the brief does not mention it.
    """
    if not have("ffmpeg"):
        raise RuntimeError("ffmpeg is not on PATH")
    width = int(R.get(rules, "video.width", 1080) or 1080)
    height = int(R.get(rules, "video.height", 1920) or 1920)
    lo = R.get(rules, "video.duration_min_s")
    hi = R.get(rules, "video.duration_max_s")
    length = max(0.1, float(end) - float(start))
    notes = []
    # O número que a PESSOA pediu, e ele vem antes da campanha de propósito.
    #
    # Em 14/09 o dono pediu "20 segundos" e recebeu 20,6 e 22,2. A ferramenta
    # não errou: ela entregou exatamente a janela que lhe passaram. O que não
    # existia era um caminho para o pedido do usuário chegar até aqui -- o
    # agente alinhava a janela nas fronteiras da transcrição e nunca voltava ao
    # número. Um pedido que não vira parâmetro é um pedido que ninguém cumpre.
    #
    # Então: quando `asked_s` vem, a janela é ajustada para ele, mantendo o
    # começo. A campanha ainda manda -- os dois clamps abaixo rodam depois --
    # e quando ela obriga a sair do número, a nota diz qual regra obrigou.
    pedido = None if asked_s is None else max(0.1, float(asked_s))
    # Qual regra da campanha venceu o pedido da pessoa, quando alguma venceu.
    # Sem isto o portão de entrega reprovava um clipe que a PRÓPRIA campanha
    # mandou encurtar, dizendo que nenhuma regra justificava o desvio -- com a
    # nota logo acima nomeando a regra. A nota e o portão discordavam.
    venceu_o_pedido = None
    if pedido is not None and abs(length - pedido) > 0.05:
        notes.append(f"the person asked for {pedido:.0f}s and the window given "
                     f"was {length:.2f}s, so the cut was moved to {pedido:.0f}s "
                     f"from the same start. A number a person says is the "
                     f"request, not a suggestion.")
        length = pedido
    if hi is not None and length > hi:
        notes.append(f"trimmed from {length:.1f}s to the {hi}s maximum"
                     + (f" -- this campaign's limit overrides the {pedido:.0f}s "
                        f"that was asked for, and that is why the file is not "
                        f"{pedido:.0f}s" if pedido is not None else ""))
        if pedido is not None:
            venceu_o_pedido = f"video.duration_max_s = {hi}"
        length = float(hi)
    if lo is not None and length < lo:
        notes.append(f"extended from {length:.1f}s to the {lo}s minimum"
                     + (f" -- this campaign's floor overrides the {pedido:.0f}s "
                        f"that was asked for, and that is why the file is not "
                        f"{pedido:.0f}s" if pedido is not None else ""))
        if pedido is not None:
            venceu_o_pedido = f"video.duration_min_s = {lo}"
        length = float(lo)

    # ---- a frase que o corte parte ao meio, fechada AQUI ----
    #
    # Medido em 16/09/2026, no primeiro lote que saiu com legenda de verdade: o
    # portão de estilo REPROVA um clipe que termina no meio da frase, e reprovou
    # 2 de 2. O conserto que ele pedia era re-renderizar com outro `--end` --
    # uma volta inteira, para um dono que roda isto numa chamada de tela
    # compartilhada e cuja regra de 16/09 é "o mais rápido possível". Era também
    # a origem da frase que ele mandou nunca mais escrever: "Ends at ~106s to
    # not cut the sentence — vou usar end=106."
    #
    # A ferramenta já sabe onde a frase fecha: é o fim da cue que o `--end`
    # corta ao meio. Então ela fecha sozinha, sem perguntar e sem uma segunda
    # renderização.
    #
    # DOIS sentidos, e o segundo não é luxo: a janela do `lote` é extraída com
    # cerca de dois segundos de folga de cada lado, então esticar quase nunca
    # cabe no arquivo. Medido no caso real: a frase fechava 4,9s depois e só
    # havia 2,0s de material. Quando não dá para ir à frente, o corte VOLTA para
    # o fim da última frase inteira -- um clipe de 17s que fecha a frase é o
    # produto, e um de 20s que fecha em "…Aí se" é uma reprovação.
    #
    # `venceu_o_pedido` é o que já existia para "a campanha obrigou a sair do
    # número pedido": o portão de duração passa com a nota dizendo o porquê, em
    # vez de reprovar um desvio que a própria ferramenta escolheu.
    if (caption_srt and pedido is not None and not shots
            and os.path.isfile(caption_srt)):
        try:
            _ficha, _ = origem_da_janela(source)
            _zero = float((_ficha or {}).get("source_start") or 0.0)
            _rows = _read_srt(caption_srt)
        except Exception:
            _rows = []
        _fim = _zero + float(start) + length if _rows else None
        _parte = [r for r in _rows if r["start"] < _fim < r["end"]] if _rows else []
        if _parte:
            _cabe = duration_of(source)
            _sobra = float(_parte[0]["end"]) - _fim
            _pode = (0.05 < _sobra <= TETO_DA_FRASE_S
                     and (hi is None or length + _sobra <= hi)
                     and (not _cabe or float(start) + length + _sobra <= _cabe - 0.05))
            if _pode:
                notes.append(
                    f"extended {_sobra:.1f}s past the {pedido:.0f}s asked for, to "
                    f"{length + _sobra:.1f}s, because the cut landed in the middle "
                    f"of a sentence and that sentence closes there."
                    # O FATO, medido, fica na nota; a FRASE é do modelo, na
                    # língua da pessoa. A amostra em português que estava aqui
                    # chegou ao modelo no meio de uma conversa em inglês
                    # (state.db msgs 69 e 75 de 16/09/2026) e a resposta dele à
                    # pessoa saiu em português.
                    + (" Say that in ONE clause to the person, in "
                       "their own language, about what they will see -- "
                       "the seconds and why, never with the word cue in it."
                       if _sobra >= 1.0 else
                       " Under a second: say NOTHING about it. A person does not "
                       "want to read that a clip is 0.4s longer than they asked."))
                length += _sobra
                venceu_o_pedido = ("the cut was splitting a sentence and it "
                                   "closes there")
            else:
                # Para trás: o fim da última cue que fecha frase de verdade.
                _fecham = [r for r in _rows
                           if r["end"] <= _fim
                           and (r.get("text") or "").rstrip().endswith(
                               (".", "!", "?", "…", '."', '?"', '!"'))]
                _piso = max(float(lo) if lo is not None else 0.0, MIN_DA_FRASE_S)
                if _fecham:
                    _novo = float(_fecham[-1]["end"]) - (_zero + float(start))
                    _corta = length - _novo
                    if 0.05 < _corta <= TETO_DA_FRASE_S and _novo >= _piso:
                        notes.append(
                            f"pulled back {_corta:.1f}s from the {pedido:.0f}s "
                            f"asked for, to {_novo:.1f}s, because the cut landed "
                            f"in the middle of a sentence and there was no room "
                            f"in this window to reach its end. This one closes "
                            f"the last whole sentence instead."
                            + (" Say that in ONE clause to the person, in "
                               "their own language: the seconds and why."
                               if _corta >= 1.0 else
                               " Under a second: say NOTHING about it."))
                        length = _novo
                        venceu_o_pedido = ("the cut was splitting a sentence and "
                                           "this window had no room to reach its "
                                           "end")

    # An edit is cut to the bar, and it is cut to the bar even when the file
    # ships silent. The platform's own sound player starts where you tell it,
    # so a clip that is a whole number of bars long still lands on the beat
    # once the track is added in the app, which is the only way a campaign that
    # bans embedded audio can have an edit at all.
    grid = None
    if track:
        grid = warden_beat.analyse(track)
        snapped, bars = warden_beat.snap(length, grid["bar_s"], lo, hi)
        if snapped is None:
            notes.append(f"no whole number of bars of {grid['track']} fits between "
                         f"{lo}s and {hi}s, so this cut is not on the grid")
        else:
            notes.append(f"{bars} bars of {grid['track']} at {grid['bpm']} BPM "
                         f"({grid['bar_s']:.3f}s a bar), so {snapped:.2f}s")
            length = snapped

    safe = R.get(rules, "safe_area", {}) or {}
    x0 = int(safe.get("x0", 86)); x1 = int(safe.get("x1", width - 140))
    y1 = int(safe.get("y1", int(height * 0.83)))

    # Where the vertical band sits across a wider source. A bare percentage is
    # the owner's exact call and is honoured as given. A side name, or nothing,
    # is a hint the tool refines: it finds the faces and centres the band on the
    # one that matters, which is what keeps the crop off the creature behind the
    # man. Detection needs the source's real dimensions, so they are measured
    # first; the kept-band fraction they give also names, in the note, exactly
    # which pixels survived.
    manual_pct = crop is not None and crop != "auto" and crop not in CROP_PRESETS
    sw = sh = kept = None
    try:
        sw, sh = _dimensions(source)
    except Exception as exc:
        # Sem as dimensões da fonte não se sabe sequer se ela é mais larga que o
        # quadro -- e `kept=None` desligava, de uma vez, o portão de detecção de
        # rosto, o recorte de moldura E a nota que diria qual faixa ficou. Era a
        # porta lateral por onde o defeito de 14/09 voltava inteiro, e calado.
        raise RuntimeError(
            f"could not read the dimensions of {os.path.basename(source)} "
            f"({type(exc).__name__}: {exc}) -- without them this cut cannot "
            f"tell whether it needs to choose a vertical band at all.")
    if sw / sh > (width / height) * 1.05:          # wider than the target frame
        kept = width / (sw * height / sh)          # fraction of source width kept

    # --------------------------------------------------- que material é este
    #
    # A pergunta que faltava, e ela vem ANTES de escolher a faixa vertical:
    # existe uma faixa vertical onde o assunto cabe? Numa tela compartilhada
    # não existe, e toda a aritmética abaixo -- rosto, kept, fx -- responde
    # bem a uma pergunta que não é a do material. Ver `tela_compartilhada`.
    enq = {"modo": "normal", "pip": None, "faces": None, "quadros": None,
           "porque": ("the source is no wider than the frame, so there is no "
                      "vertical band to choose")}
    if manual_pct:
        enq["porque"] = (f"crop {crop} came in by hand, and a number from the "
                         f"owner is not up for debate: not even the "
                         f"shared-screen detection runs")
    elif kept:
        enq = tela_compartilhada(source, start, length)
        if enq.get("quadros") == 0:
            # Mesma regra de `_face_centers`: zero quadros lidos não é "olhei e
            # é vídeo normal", é "não olhei". As duas davam o caminho de
            # sempre, e foi assim que o rosto foi parar na borda sem um aviso.
            raise RuntimeError(
                f"could not read a single frame of {os.path.basename(source)} "
                f"between {float(start):.1f}s and "
                f"{float(start) + float(length):.1f}s, so nothing here knows "
                f"whether this is a shared screen or a normal video -- and the "
                f"two need opposite framings. Check the window is inside the "
                f"file, or pass --crop to place the band by hand.")
    dividido = enq["modo"] == "dividido"

    fx = _crop_fraction(crop)
    framed_by = "crop %s" % (crop if crop is not None else "centre (default)")
    if dividido:
        pass                                  # o layout é outro; ver `chain`
    elif not manual_pct and kept:
        # A fonte é mais larga que o quadro, então alguém tem de escolher qual
        # faixa fica -- e essa escolha é o rosto. Sem detector, essa escolha não
        # existe: o centro é um chute, e num vídeo em que o sujeito senta à
        # esquerda o chute entrega o rosto cortado na borda. Foi o que saiu na
        # rodada de 14/09, e o aviso de então dizia "enquadrei no centro", que
        # era verdadeiro e inútil.
        #
        # Então para aqui. O dono resolve em um argumento; um clipe com o rosto
        # na borda não se resolve depois de publicado.
        ready, why = face_detection_status()
        if not ready:
            # Um lado nomeado é o dono dizendo onde o sujeito está, e isso é
            # honrado sem detector nenhum -- o detector só refinaria. O que não
            # se pode é inventar: sem instrução e sem detector, não há de onde
            # tirar a faixa, e recusar aqui é a diferença entre um argumento a
            # mais e um rosto cortado no arquivo publicado.
            #
            # A RECUSA FICA, e a decisão é esta, escrita para quem vier depois
            # achando que ela contradiz a promessa de uma pergunta só:
            #
            #   - a IMAGEM TEM o detector. O OpenCV e o YuNet são instalados no
            #     Dockerfile e conferidos no build. No container -- que é onde
            #     este agente atende o dono -- `face_detection_status()` diz
            #     sim e este ramo NUNCA roda. A segunda pergunta não existe no
            #     caminho normal.
            #   - o caso sem detector é a máquina de quem roda a suíte ou o
            #     script fora do container. Ali um argumento a mais é barato, e
            #     é a pessoa que já está no terminal quem o passa.
            #   - cair no centro calado foi o que se fez até 14/09, e o
            #     resultado foi um clipe entregue com o rosto na borda. O aviso
            #     de então dizia "enquadrei no centro": verdadeiro, e inútil,
            #     porque ninguém lê um aviso num clipe que parece pronto.
            #
            # O `warden-clip/SKILL.md` fala em "falls back to the centre", e
            # esse texto é sobre OUTRA coisa -- detector presente que não achou
            # rosto, que de fato cai no centro logo abaixo. Ele não distingue
            # os dois casos, e é de outro dono; fica registrado aqui.
            # 16/09/2026: isto ERA um `raise`, e um `raise` aqui é um clipe que
            # não sai por causa de ENQUADRAMENTO -- que o dono pôs, com essa
            # palavra, na lista do que só pode AVISAR. A auditoria do mesmo dia
            # o encontrou: era a única coisa que não é gancho, legenda, arquivo
            # ilegível nem regra de campanha e ainda assim impedia um `MEDIA:`.
            #
            # O argumento de 14/09 continua de pé e é bom: cair no centro CALADO
            # entregou um clipe com o rosto na borda, e "enquadrei no centro" é
            # um aviso que ninguém lê num clipe que parece pronto. O que mudou
            # não é a leitura do risco, é quem decide. Um clipe torto que a
            # pessoa recebe e descarta custa um descarte; um clipe que não sai
            # custa o pedido inteiro, e foi o que aconteceu duas vezes.
            #
            # Então: cai no centro, e o aviso vai no lugar onde ele é lido --
            # `LOOK:`, que o agente repassa em uma frase à pessoa, na língua
            # dela. Não é o mesmo que a nota calada de 14/09.
            if crop in (None, "auto"):
                crop = "center"
                notes.append(
                    f"LOOK: no face detector on this machine ({why}), so this "
                    f"cut was framed on the CENTRE without measuring anything: "
                    f"nothing checked where the subject actually is, and a "
                    f"person standing off to one side would be cut in half. "
                    f"Say that to them in one clause. In the agent's own "
                    f"container the detector ships with the image and the face "
                    f"picks the band, so this line does not appear there.")
            # Um lado nomeado é o dono dizendo onde o sujeito está, e isso é
            # honrado. Mas continua sendo um lado, não uma medição: a nota diz
            # isso em vez de deixar parecer que alguém conferiu.
            notes.append(f"framed by crop {crop} as given, NOT measured: there "
                         f"is no face detector on this machine ({why}), so "
                         f"nothing checked that the subject is on that side. "
                         f"The contact sheet is the only check this clip got.")
        else:
            # Os rostos já foram medidos pela detecção de tela, na mesma janela
            # e na mesma passada de ffmpeg. Chamar `_face_centers` aqui seria
            # decodificar a janela uma segunda vez para responder à mesma
            # pergunta -- é o conserto de "uma passada, não uma por quadro"
            # que este arquivo já fez duas vezes.
            faces = enq.get("faces")
            if faces is None:
                faces = _face_centers(source, start, length)
            face_fx = _face_crop_fraction(faces, crop, kept)
            if face_fx is not None:
                fx = face_fx
                framed_by = "face" + ("" if crop in (None, "auto")
                                      else f" on the {crop}")

    # ------------------------------------------------------- olhar o material
    #
    # A ferramenta passa a abrir alguns quadros da janela antes de montar a
    # cadeia. Até aqui ela media pixels sem nunca olhar nenhum, e foi assim que
    # a borda do material entrou no enquadramento e a legenda do acervo ficou
    # cortada ao meio embaixo da nossa.
    # ------------------------------------------------------ decupagem
    #
    # A lista de planos, encaixada na batida. `planos` é [(in, out, batidas)]
    # no relógio da FONTE; `length` passa a ser a soma das durações, porque um
    # edit dura o que os planos dele somam e não o que a janela media.
    planos = []
    if shots:
        pedidos = []
        for plano in shots:
            if isinstance(plano, dict):
                a = plano.get("in", plano.get("start"))
                b = plano.get("out", plano.get("end"))
            else:
                a, b = plano[0], plano[1]
            if a is None or b is None or float(b) <= float(a):
                raise RuntimeError(
                    f"a shot needs an in and an out, and out after in; got "
                    f"{plano!r}. A shot list that does not describe a shot is "
                    f"not something to render past.")
            pedidos.append((float(a), float(b)))
        planos = warden_beat.snap_shots(pedidos, (grid or {}).get("beat_s"))
        length = round(sum(b - a for a, b, _ in planos), 3)
        bordas = []
        acc = 0.0
        for a, b, _ in planos[:-1]:
            acc += (b - a)
            bordas.append(round(acc, 4))
        notes.append(
            f"{len(planos)} shots spliced in order, {length:.2f}s in total"
            + (f", each a whole number of beats ("
               + ", ".join(str(n or "?") for _, _, n in planos) + ")"
               if (grid or {}).get("beat_s") else
               ", with no beat grid to snap to -- durations are as asked"))

    try:
        # Com planos, os quadros vêm DOS PLANOS. Amostrar start..start+length
        # num edit olha um trecho contínuo que o clipe não mostra -- e foi
        # exatamente assim que uma legenda do próprio vídeo passou pela
        # detecção e apareceu atrás da nossa.
        looked = (S.sample_shots(source, planos, count=8) if planos
                  else S.sample_frames(source, start, length, count=8))
    except Exception as exc:
        raise RuntimeError(
            f"could not open any frame of {os.path.basename(source)} between "
            f"{float(start):.1f}s and {float(start) + length:.1f}s "
            f"({type(exc).__name__}: {exc}), so nothing here has seen this "
            "footage -- refusing to guess that it is clean.")
    if not looked:
        # O silêncio caro: sem quadros, `bottom` sai False, e False aqui lê como
        # "material limpo, não precisa cobrir". É uma resposta que a ferramenta
        # não tem, com a cara de uma que ela tem.
        raise RuntimeError(
            f"could not open any frame of {os.path.basename(source)} between "
            f"{float(start):.1f}s and {float(start) + length:.1f}s, so this "
            "cut cannot tell whether the footage carries its own burned text "
            "or a border. Check the window is inside the file.")
    source_text = S.burned_text_bands(looked)
    source_text["looked"] = True
    border = S.frame_border(looked)

    # A moldura sai do enquadramento antes de qualquer outra conta. `crop` no
    # source, não no destino: recua a faixa para dentro do conteúdo, e o scale
    # seguinte trabalha só com o que sobrou.
    pre = ""
    corte_l = corte_r = 0
    if (border["left"] or border["right"]) and sw:
        l, r = int(border["left"]), int(border["right"])
        corte_l, corte_r = l, r
        pre = f"crop=in_w-{l + r}:in_h:{l}:0,"
        notes.append(f"trimmed {l}px off the left and {r}px off the right before "
                     f"framing: those columns are source furniture (a bar, a "
                     f"capture border), not picture. Left in, they sit frozen at "
                     f"the edge of the whole clip.")
        if kept:                             # a faixa útil encolheu junto
            kept = width / ((sw - l - r) * height / sh)
            kept = min(1.0, kept)

    # ------------------------------------------------- a geometria, parte um
    #
    # Aqui sai o layout PROVISÓRIO, com a faixa da legenda reservada, e ele
    # existe por uma razão só: as faixas de texto do acervo são medidas em
    # fração da FONTE e precisam da faixa da tela para virar fração do QUADRO.
    #
    # A geometria de verdade sai lá embaixo, depois que a legenda foi decidida,
    # porque ela DEPENDE dessa decisão: sem legenda queimada a imagem desce
    # até o piso do app e a tela cresce 320px. Quando a legenda queima os dois
    # layouts são idênticos, então o provisório é exato justamente no caso em
    # que alguém o usa para comparar réguas.
    faixas_do_layout = rastreio = chain = None
    sw_util = sw
    pip = rosto = trilha = cheios = None
    cheio_x = 0.5
    if dividido:
        # A moldura já saiu em `pre`, então a webcam tem de ser recortada no
        # quadro JÁ recortado: as frações do PiP foram medidas no quadro
        # inteiro e são levadas para esse sistema aqui. Sem isto o `crop` da
        # webcam pediria pixels que não existem mais e o ffmpeg pararia -- e
        # pararia só no material que tem moldura, que é o pior lugar para
        # descobrir.
        sw_util = max(2, sw - corte_l - corte_r)
        pip = enq.get("pip")
        rosto = None
        if pip:
            pip = ((pip[0] * sw - corte_l) / sw_util, pip[1],
                   (pip[2] * sw - corte_l) / sw_util, pip[3])
            pip = (min(max(pip[0], 0.0), 1.0), pip[1],
                   min(max(pip[2], 0.0), 1.0), pip[3])
            rosto = enq.get("rosto")
            if rosto:
                rosto = (min(max((rosto[0] * sw - corte_l) / sw_util, 0.0), 1.0),
                         rosto[1])
        # A trilha vem em fração do quadro INTEIRO e o `pre` já tirou a
        # moldura, então ela é levada para o mesmo sistema do PiP -- a mesma
        # conta, pelo mesmo motivo, no mesmo lugar.
        trilha = [(t, min(1.0, max(0.0, (cx * sw - corte_l) / sw_util)), cy, fh)
                  for t, cx, cy, fh in (enq.get("trilha") or [])]
        # Os trechos de câmera, e onde o rosto está NELES. A faixa vertical que
        # vai cobrir esses trechos é centrada na mediana desses rostos: usar a
        # mediana de TODOS puxaria a faixa para o canto da webcam, que é onde a
        # pessoa não está quando a fonte está em tela cheia.
        cheios = [] if planos else (enq.get("camera") or [])
        # `fh >= 0.99` é a marca de "não achei rosto" que `trilha_do_rosto`
        # deixa (o quadro inteiro), e não uma medida de rosto. Contá-la puxaria
        # a faixa para o meio do quadro, que é onde a pessoa não está quando a
        # webcam divide espaço com um post à esquerda.
        dentro = [cx for t, cx, _cy, fh in (trilha or [])
                  if fh < 0.99 and any(a <= t <= b for a, b in cheios)]
        if dentro:
            cheio_x = sorted(dentro)[len(dentro) // 2]
        faixas_do_layout = layout_dividido(width, height, sw_util, sh,
                                           pip=pip, rosto=rosto, legenda=True)

    # A legenda do acervo não fica na borda: no vlog de 14/09 ela está a 69% da
    # altura da fonte, que é o meio do quadro e é exatamente onde a nossa cai
    # depois do recorte. `burned_text_bands` olha os 16% de cima e os 16% de
    # baixo e por construção não podia vê-la -- a conclusão de que o limiar
    # estava alto demais era falsa. Esta varredura olha a faixa inteira que o
    # corte vai manter.
    if dividido:
        # No dividido a largura inteira fica, então não há recorte a passar --
        # e as faixas voltam em fração da altura da FONTE, que aqui não é mais
        # a altura do quadro: a fonte inteira mora dentro da faixa da tela.
        # Sem esta conversão o portão de legenda dupla compararia a legenda do
        # acervo com a nossa em réguas diferentes, o que é o mesmo que não
        # comparar.
        _tx, _ty, _tw, _th = faixas_do_layout["tela"]
        acervo_faixas = [dict(f, y0=(_ty + f["y0"] * _th) / height,
                              y1=(_ty + f["y1"] * _th) / height)
                         for f in S.legenda_do_acervo(looked)]
    elif kept and sw:
        _e = fx * (1 - kept)
        acervo_faixas = S.legenda_do_acervo(
            looked, recorte=(int(_e * sw), int((_e + kept) * sw)))
    else:
        acervo_faixas = S.legenda_do_acervo(looked)
    if dividido:
        pass                       # a linha do modo sai logo abaixo, para todos
    elif kept:
        left_edge = fx * (1 - kept)
        note_band = f"{int(left_edge * sw)}–{int((left_edge + kept) * sw)} of {sw}px"
        if framed_by.startswith("face"):
            notes.append(f"framed by {framed_by}: keeping x {note_band}")
        elif crop is None or crop == "auto":
            notes.append(
                f"framed on the centre (no face found to follow): keeping x "
                f"{note_band}. If the subject is a side-by-side, a two-shot or a "
                "corner cam, re-cut with --crop left|right or a percentage.")
        else:
            notes.append(f"{framed_by}: keeping x {note_band}")

    if not dividido:
        notes.append(f"FRAMING: vertical band ({framed_by}) -- {enq['porque']}")
    # ---------------------------------------------------------------- texto
    #
    # Todo texto daqui para baixo é PNG do PIL, não `drawtext` e não `subtitles`.
    # Três motivos, e nenhum deles é preferência:
    #
    #  - `drawtext` não quebra linha e não ajusta corpo. É o defeito 2.1: com
    #    1080px de quadro o corpo saía 49px, 44 caracteres passavam de 1100px, o
    #    `x=(w-text_w)/2` ficava negativo e o ffmpeg cortava os dois lados sem
    #    erro nenhum. Qualquer hook com mais de uns 35 caracteres saía cortado.
    #  - o PIL mede o texto ANTES de desenhar, que é a única forma de garantir
    #    que ele cabe. É o que o PRIME faz, e é o que já produziu clipe aprovado.
    #  - nem todo ffmpeg tem libass ou drawtext -- este aqui não tem nenhum dos
    #    dois. Um renderizador que só funciona numa build é um renderizador que
    #    ninguém consegue conferir na máquina onde escreve o código.
    #
    # Cada texto vira um PNG transparente do tamanho do quadro e entra como
    # `overlay` com `enable='between(t,a,b)'`, que é a montagem do PRIME.
    overlays = []                     # (png, y, de, ate, fade_de_saida_s)
    art_dir = os.path.dirname(os.path.abspath(out)) or "."
    stem = os.path.splitext(os.path.basename(out))[0]
    # O que este render fez consigo mesmo, gravado ao lado dele. `style check`
    # lê daqui em vez de tentar recuperar dos pixels: a largura do hook aqui é a
    # que o PIL mediu com a fonte real, e não uma estimativa que confunde letra
    # com letreiro de neon.
    style_facts = {"frame": {"w": width, "h": height},
                   "margins": S.margens(width, height),
                   "source_caption_bands": [
                       {"y0": round(f["y0"], 3), "y1": round(f["y1"], 3),
                        "span": round(f["span"], 3), "frames": f["frames"]}
                       for f in acervo_faixas],
                   "hook": None, "caption": None, "footer_covered": False,
                   "motion": bool(motion), "source_text": source_text,
                   "duration_s": round(float(length), 2),
                   "asked_s": None if pedido is None else round(pedido, 2),
                   "asked_overridden_by": venceu_o_pedido,
                   # Qual enquadramento saiu, com os números que o decidiram.
                   # Fica no sidecar pelo mesmo motivo que a largura do hook
                   # fica: uma vez gravado, "enquadrou assim porquê" deixa de
                   # ser memória de quem rodou e vira coisa que se confere.
                   "framing": {
                       "mode": enq["modo"], "why": enq["porque"],
                       "screen_frames": enq.get("quadros_de_tela"),
                       "frames": enq.get("quadros"),
                       "flatness": enq.get("planuras"),
                       "webcam_box": enq.get("pip"),
                       # `face_track` e `bands` só se sabem depois da legenda:
                       # a geometria depende dela. São preenchidos lá embaixo,
                       # onde o layout definitivo é montado.
                       "face_track": None,
                       "camera_stretches": None if not dividido else list(
                           cheios or []),
                       "bands": None}}

    footer = None
    # A decisão de COBRIR o rodapé do acervo mudou de lugar, e o lugar é o
    # conserto. Ela era tomada aqui em cima, com `caption_srt` existindo no
    # disco como única prova de que haveria legenda nossa -- e os três portões
    # abaixo (arquivo aprovado, idioma, libass) ainda podiam derrubar a legenda
    # DEPOIS. Quando derrubavam, o degradê já tinha apagado a legenda do acervo
    # em troca de nada, e a nota afirmava uma disputa que não houve. Agora ela
    # roda depois dos portões, onde `cues`/`ass_path` dizem o que de fato foi
    # queimado. A ordem das CAMADAS não muda com isso: a montagem lá embaixo
    # separa o degradê por identidade (`o[0] == footer`) e não por posição.
    if source_text.get("top") and hook and dividido:
        # No dividido o topo da FONTE não é o topo do QUADRO: ele desce para
        # dentro da faixa da tela, e o hook fica na faixa da webcam, que é
        # outro pedaço do quadro. Os dois textos existem e não se encontram --
        # então isto vira nota, e não recusa. Recusar aqui mataria justamente
        # os clipes que este modo existe para salvar.
        # A NOTA MUDOU EM 17/09/2026, junto com o layout, e ela dizia o
        # contrário do que acontece agora. Ela afirmava "dois textos, dois
        # lugares", o que era verdade quando a faixa da webcam ficava EM CIMA
        # e o hook virava lower-third dentro dela: o texto do acervo caía na
        # tela, lá embaixo, e os dois nunca se encontravam.
        #
        # Agora a tela é que está em cima, encostada na margem do app, e o
        # hook voltou para o topo. Os dois ocupam a MESMA faixa nos primeiros
        # três segundos. Continua sendo nota e não recusa -- o hook tem scrim
        # e sai aos 3s --, mas ela tem de dizer a verdade, que é o oposto.
        notes.append(
            f"this footage carries burned text along the TOP "
            f"({source_text['evidence']}), and in this layout the screen strip "
            f"starts right at the app margin, which is where the hook sits for "
            f"its first 3s. The two OVERLAP: the hook's scrim covers the "
            f"source's own top text until it fades. Look at the contact sheet "
            f"and drop the hook if it buries something that matters.")
    elif source_text.get("top") and hook:
        # NÃO mata mais o clipe, e o motivo é uma medição.
        #
        # Isto levantava RuntimeError: texto do acervo no topo mais o nosso hook
        # são dois textos no mesmo lugar, e isso é ilegível quando é verdade. O
        # problema é o "quando é verdade". Medido em 16/09/2026, num pedido
        # real: uma janela de rua -- fila de telhados escuros contra céu branco,
        # com esquadrias de janela -- votou em 4 dos 8 quadros e o clipe inteiro
        # foi RECUSADO. Não havia texto nenhum lá.
        #
        # E não existe limiar que separe os dois. Na mesma escala: letras de
        # verdade dão 5,71x, aqueles telhados deram 1,96x e 2,43x, e UMA linha
        # de texto no topo dá 2,71x. Telhado e legenda dividem a faixa.
        #
        # Então a consequência vira a do lado de baixo, que este mesmo arquivo
        # já escolheu: o clipe SAI, com o aviso alto, e quem decide é o mosaico
        # -- que é obrigatório abrir antes de entregar. O preço de errar para
        # este lado é um clipe com dois textos no topo, visível na imagem que
        # ninguém pula. O preço de errar para o outro era o clipe não existir, e
        # foi o que aconteceu: o agente teve de renderizá-lo de novo sozinho,
        # 100 segundos a mais num pedido que o dono já achava longo demais.
        notes.append(
            f"this footage MAY already carry burned text along the TOP "
            f"({source_text['evidence']}), and this cut puts a hook there too. "
            f"The clip is rendered anyway -- this detection also fires on a "
            f"roofline against a bright sky, measured 16/09 -- so LOOK AT THE "
            f"TOP BAND of the contact sheet before you deliver. If there really "
            f"are two texts up there, re-cut without --hook or on another "
            f"window, and say in one line which you did.")
    if source_text.get("top"):
        notes.append("this footage carries burned text along the TOP "
                     f"({source_text['evidence']}). No hook was asked for, so "
                     "nothing of ours lands on it.")
    elif source_text.get("top_suspect"):
        # Em cima não existe o lado barato que existe embaixo. O degradê do
        # rodapé cobre a faixa de baixo por quase nada; no topo não há degradê
        # nenhum, e parar o corte por uma SUSPEITA seria matar o clipe para
        # resolver um talvez. Então aqui a suspeita vira o que ela é -- uma
        # linha que manda olhar o mosaico -- e não um portão.
        notes.append(
            "this footage MAY carry burned text along the TOP (suspect, not "
            f"the hard boolean: {', '.join(source_text.get('top_why') or ['-'])}"
            f"; {source_text['evidence']})."
            + (" The hook goes there too: look at the top band on the contact "
               "sheet before delivering, and re-cut without --hook or on "
               "another window if there are two texts in it."
               if hook else
               " No hook was asked for, so nothing of ours lands on it."))

    # ------------------------------------------------- de que relógio é este arquivo
    #
    # Se a fonte é uma janela recortada de um vídeo maior, o zero dela não é o
    # zero da fonte, e TODA conta que envolve a legenda tem de ser feita no
    # relógio da FONTE: o SRT está nele, a aprovação foi dada nele, e a janela
    # que o espectador vai ver está nele. Sem isso a legenda sai de outro
    # trecho -- aconteceu, nos dois clipes de uma medição, e nenhum portão viu.
    ficha, porque_ficha = origem_da_janela(source)
    fonte_zero = float((ficha or {}).get("source_start") or 0.0)
    if porque_ficha and caption_srt:
        raise RuntimeError(
            f"refusing to burn captions onto {os.path.basename(source)}: "
            f"{porque_ficha}. Without it there is no way to line the subtitle's "
            f"timestamps up with this file, and a caption from the wrong part "
            f"of the video looks finished and says things the person never said "
            f"at that moment. Cut it without --subtitles, or pull the window "
            f"again with `warden archive --window`, which writes the origin "
            f"beside the file.")
    if ficha:
        notes.append(
            f"this source is the {ficha['source_start']:.1f}-"
            f"{ficha['source_end']:.1f}s window of a longer file, so the caption "
            f"is lined up on the SOURCE clock: this cut burns "
            f"{fonte_zero + float(start):.1f}-"
            f"{fonte_zero + float(start) + length:.1f}s of it.")

    burn_reason = None
    # A faixa onde a NOSSA legenda vai cair, em fração da altura: do piso para
    # cima, duas linhas de entrelinha. Se o acervo já escreve ali, queimar por
    # cima é a legenda dupla -- e aqui o degradê do rodapé não salva, porque
    # cobrir 70% da altura cobriria o assunto do clipe.
    _piso = S.caption_piso(height)
    _linha = int(S.caption_size(height) * S.LEADING)
    nossa_faixa = ((height - _piso - 2 * _linha) / height,
                   (height - _piso) / height)
    acervo_na_nossa_faixa = [
        f for f in acervo_faixas
        if f["y1"] >= nossa_faixa[0] - 0.03 and f["y0"] <= nossa_faixa[1] + 0.03]
    if dividido and acervo_na_nossa_faixa:
        # No dividido o degradê VOLTA a ser uma saída, e a frase acima -- "aqui
        # o degradê não salva, porque cobrir 70% da altura cobriria o assunto"
        # -- deixa de valer. A legenda do acervo não está mais no meio do
        # quadro: ela está no pé da FAIXA DA TELA, e abaixo dela só há fundo
        # borrado. Cobrir dali para baixo custa os 25% de baixo da tela e nada
        # mais, e compra a nossa legenda de volta.
        #
        # Medido nesta live em 15/09: a faixa do acervo saiu a 63%-64% da
        # altura e a nossa mora a 65%-81%. Elas não se sobrepõem -- encostam --
        # e mesmo assim o corte saía SEM legenda nenhuma, o que é pior que o
        # problema que a recusa evitava.
        _tx, _ty, _tw, _th = faixas_do_layout["tela"]
        coberto = (_ty + 0.75 * _th) / height
        restantes = [f for f in acervo_na_nossa_faixa if f["y0"] < coberto]
        if not restantes:
            notes.append(
                f"the footage's own bottom text lands at "
                f"{acervo_na_nossa_faixa[0]['y0']:.0%}-"
                f"{acervo_na_nossa_faixa[0]['y1']:.0%} of this frame, which in "
                f"the split layout is the foot of the screen strip and not the "
                f"middle of the picture. The gradient footer covers it from "
                f"there down -- it costs the bottom quarter of the screen "
                f"strip and buys our caption back.")
        acervo_na_nossa_faixa = restantes
    style_facts["source_caption_clash"] = bool(acervo_na_nossa_faixa)

    cues = []
    fala_apos_o_corte = False
    # Onde a frase que o corte parte realmente fecha, em segundos do clipe.
    # É o número que faltava: quando alguém pede 20 segundos e a fala não cabe
    # em 20, a única saída que a ferramenta oferecia era "mova o --end", que é
    # mandar adivinhar. Com este número a escolha é entre dois valores, e a
    # pessoa pode ouvir a frase inteira em uma linha sem jargão nenhum.
    frase_fecha_em = None
    if caption_srt and os.path.exists(caption_srt) and acervo_na_nossa_faixa:
        f = acervo_na_nossa_faixa[0]
        burn_reason = (
            f"not burning captions: this footage already burns its own, at "
            f"{f['y0']:.0%}-{f['y1']:.0%} of the frame height, which is where "
            f"ours would go ({f['frames']} of {len(looked)} sampled frames, "
            f"{f['span']:.0%} of the kept width). Two captions in one frame is "
            f"the reject; the gradient footer cannot help this one, because "
            f"that band is the middle of the picture and covering it would "
            f"cover the subject. The speech is already on screen, so this clip "
            f"ships with the footage's own captions and no second set. Tell "
            f"the person that in ONE clause, in their own language -- the fact "
            f"is: this video already comes captioned, so no second caption was "
            f"added -- and say nothing about bands or frames.")
    elif caption_srt and os.path.exists(caption_srt):
        if R.get(rules, "sources.archive_has_captions") is True:
            # The campaign says this archive already burns its own captions.
            # Burning ours over them is the doubling the owner set this flag to
            # prevent, so the tool refuses it here rather than leaving it to be
            # caught by eye on the first clip.
            burn_reason = ("not burning captions: this campaign's archive already "
                           "carries its own (sources.archive_has_captions is true)")
        else:
            # A janela QUE VAI QUEIMAR, no tempo da fonte -- não o arquivo.
            # Perguntar pelo arquivo era a porta por onde `aromasas` passou.
            #
            # Com planos, não há UMA janela: há várias, e a aprovação tem de
            # cobrir todas. Perguntar só pela primeira e pela última aprovaria
            # de brinde tudo o que está entre elas, inclusive material que este
            # corte nem mostra.
            if planos:
                queima_de = fonte_zero + min(a for a, _b, _n in planos)
                queima_ate = fonte_zero + max(b for _a, b, _n in planos)
            else:
                queima_de = fonte_zero + float(start)
                queima_ate = queima_de + length
            approved, why = S.approval_state(
                caption_srt, start=queima_de, end=queima_ate)
            if not approved:
                # O portão do defeito 2.5. Sem aprovação o clipe sai SEM legenda,
                # em vez de sair com a palavra errada queimada: um clipe mudo se
                # conserta, um `jokovic jokovic` no vídeo publicado não.
                burn_reason = f"not burning captions: {why}"
            else:
                rows = _read_srt(caption_srt)
                if planos:
                    # A fala de cada plano, realocada para o relógio da EMENDA.
                    # O plano vem do minuto três da fonte e vai para o segundo
                    # sete do clipe; a cue tem de ir junto, ou a legenda fala de
                    # uma cena que já passou. É o mesmo defeito do arquivo de
                    # janela com relógio próprio, multiplicado por N.
                    window = []
                    saida = 0.0
                    # Quantos planos ABREM no meio de uma frase. Com planos
                    # espalhados pelo material é quase todo: a fala de cada um
                    # começa antes do plano e termina depois dele, então o que
                    # sobra são meias-frases emendadas. Medido em 15/09 num edit
                    # de 15 planos: a legenda saiu "portas, a que vai pra sala /
                    # e a que vai Você lembra," seguida de "Nossa dispensa é
                    # cheia. Então," -- mecanicamente correta, ilegível.
                    partidos = 0
                    # Planos COLADOS um no outro são um trecho só para a fala:
                    # a imagem não pula, então a frase continua. Cortar a cue em
                    # cada borda de plano nesse caso partiria frases que a
                    # emenda não partiu.
                    trechos = []
                    for a0, b0, _n in planos:
                        if trechos and abs(a0 - trechos[-1][1]) <= 0.05:
                            trechos[-1] = (trechos[-1][0], b0)
                        else:
                            trechos.append((a0, b0))
                    for a0, b0 in trechos:
                        a, b = fonte_zero + a0, fonte_zero + b0
                        primeira = True
                        for r in rows:
                            if r["end"] <= a or r["start"] >= b:
                                continue
                            de = max(r["start"], a)
                            ate = min(r["end"], b)
                            if ate - de < 0.12:
                                continue          # sobra de cue, não é fala
                            if primeira and r["start"] < a - 0.05:
                                partidos += 1
                            primeira = False
                            window.append({
                                "start": round(queima_de + saida + (de - a), 3),
                                "end": round(queima_de + saida + (ate - a), 3),
                                "text": r["text"],
                                "words": [
                                    {**w,
                                     "start": round(queima_de + saida
                                                    + (float(w["start"]) - a), 3),
                                     "end": round(queima_de + saida
                                                  + (float(w["end"]) - a), 3)}
                                    for w in (r.get("words") or [])
                                    if a <= float(w.get("start", -1)) < b],
                            })
                        saida += (b - a)
                    window.sort(key=lambda r: r["start"])
                    queima_ate = round(queima_de + saida, 3)
                    colagem = len(trechos) > 1 and partidos > len(trechos) / 2
                    style_facts["caption_collage"] = {
                        "runs": len(trechos),
                        "runs_opening_mid_sentence": partidos,
                        "shots": len(planos),
                        "is_collage": bool(colagem),
                    }
                    if colagem:
                        # Não é aviso: é recusa. Uma legenda de meias-frases é
                        # pior que legenda nenhuma, pela mesma razão que uma
                        # palavra errada queimada é -- ela vai para a tela e
                        # fica lá. O edit sai sem legenda e o motivo é dito.
                        burn_reason = (
                            f"not burning captions: {partidos} of "
                            f"{len(trechos)} spliced stretches open in the "
                            f"middle of a "
                            f"sentence, so the caption would be a collage of "
                            f"half-sentences -- measured, it reads like "
                            f"'portas, a que vai pra sala / e a que vai Você "
                            f"lembra,'. Either take the shots from one "
                            f"continuous moment so the speech holds together, "
                            f"or drop --subtitles and put a written line on it "
                            f"with --hook, which is what the approved "
                            f"reference does.")
                        window = []
                else:
                    window = [r for r in rows
                              if r["end"] > queima_de and r["start"] < queima_ate]
                clash = S.language_clash(
                    hook, " ".join(r["text"] for r in window),
                    declared=language or R.get(rules, "caption.language"))
                if clash:
                    # Defeito 2.4. Recusa e diz por quê, em vez de queimar inglês
                    # sobre um hook em português como saiu da última vez.
                    burn_reason = f"not burning captions: {clash}"
                else:
                    # A fala continua depois do fim do corte? Só quem tem o
                    # SRT inteiro sabe, e é isso que decide se a ÚLTIMA cue está
                    # pendurada ou só é a última. Ver `finais_pendurados`.
                    fim_janela = queima_ate
                    fala_apos_o_corte = any(r["end"] > fim_janela + 0.05
                                            for r in rows)
                    partida = [r for r in rows
                               if r["start"] < fim_janela < r["end"]]
                    if partida:
                        frase_fecha_em = round(partida[0]["end"] - queima_de, 2)
                    descartes = []
                    cues = S.reflow_cues(window, descartes=descartes)
                    # ---- encaixar o destaque na fala de verdade ----
                    #
                    # A legenda automática do YouTube carimba o tempo DEPOIS da
                    # fala: o reconhecedor só emite quando já tem contexto.
                    # Queimar esse tempo cru nasce atrasado, e o dono viu no
                    # clipe -- o homem fala a palavra e o amarelo chega depois.
                    #
                    # Medido contra a régua que ele mediu à mão no clipe
                    # entregue, a mediana era +0,543s. O detector de começo de
                    # fala reproduz essa régua: 8 de 8, com 0,01s de diferença
                    # num único valor.
                    # O detector ouve o ARQUIVO e devolve no relógio dele;
                    # os onsets são levados para o da fonte, que é o relógio em
                    # que as cues estão.
                    falas, porque_fala = S.fala_comeca(source, start, length)
                    falas = [f + fonte_zero for f in falas]
                    if falas:
                        cues, sincronia = S.encaixa_na_fala(cues, falas)
                    else:
                        sincronia = {"porque": porque_fala or "sem fala detectada"}
                        notes.append(
                            f"could not find the speech onsets in this stretch "
                            f"({porque_fala}), so the highlight is on the raw "
                            f"timing of the source subtitle, which usually "
                            f"runs late.")
                    if descartes:
                        # Era nota, e nota não protege: fala que existe no SRT
                        # e não vai para a tela não aparece no mosaico -- o que
                        # falta é invisível por definição. Quem olha conta as
                        # cues que estão lá, não as que deveriam estar.
                        raise RuntimeError(
                            f"{len(descartes)} subtitle line(s) could not be "
                            f"used and would silently not reach the screen: "
                            + "; ".join(descartes[:3])
                            + ". Speech that exists in the SRT and is missing "
                              "from the picture is invisible on the contact "
                              "sheet. Fix the srt, or cut without --subtitles.")
                    if not cues:
                        burn_reason = ("the subtitle file had no usable lines in "
                                       "this window, so nothing was burned")

    # ------------------------------------------------------- a legenda é ASS
    #
    # O hook continua PNG do PIL, porque ele é medido antes de ser desenhado e
    # é isso que garante que ele cabe. A LEGENDA sai do PIL e vira ASS queimado
    # pelo libass, por um motivo que o PNG não resolve: destaque palavra a
    # palavra. Um PNG por estado de palavra seriam cinquenta e cinco entradas de
    # ffmpeg num clipe de vinte segundos; `{\k}` faz o mesmo dentro de uma linha
    # de texto, e o libass já está no ffmpeg da imagem (`--enable-libass`).
    ass_path = None
    if cues:
        if not ffmpeg_tem_filtro("ass"):
            # Dependência ausente não degrada calado. Um clipe de podcast sem
            # legenda não é um clipe entregue, e "renderizou sem legenda" com
            # uma nota no meio de doze é como um defeito atravessa.
            raise RuntimeError(
                "este ffmpeg não foi compilado com libass, e a legenda deste "
                "renderizador é ASS com destaque palavra a palavra. O filtro "
                "`ass` não existe neste binário -- `ffmpeg -filters | grep ass` "
                "confirma. A imagem do agente traz um ffmpeg com "
                "`--enable-libass`; um ffmpeg de Homebrew normalmente não. "
                "Rode o corte na imagem, ou corte sem --subtitles.")
        perdas = []
        texto_ass = ass_from_cues(cues, width, height, safe=safe,
                                  offset=queima_de, length=float(length),
                                  perdas=perdas)
        if not texto_ass.strip():
            burn_reason = ("the ASS came out empty: no cue was left inside "
                           "the window once shifted to the clip's own clock")
            cues = []
        else:
            ass_path = os.path.join(art_dir, f"{stem}.ass")
            with open(ass_path, "w", encoding="utf-8") as fh:
                fh.write(texto_ass)
            escritas = texto_ass.count("Dialogue:")
            # Cues que caíram fora da janela do clipe. Elas são fala que
            # acontece depois do fim do corte, então descartá-las é certo --
            # mas descartá-las CALADO não é. Medido: num clipe de 22,2s o
            # reflow produzia cues até 30,66s, e três delas sumiam sem uma
            # linha em lugar nenhum.
            fora = len(cues) - escritas
            if ficha and cues:
                # O portão do desencontro de relógios, e ele mede a coisa certa.
                #
                # A primeira versão deste portão contava quantas cues caíam
                # fora do clipe, e isso era falso positivo: numa janela que
                # termina no meio da fala, duas cues sobrando é rotina. O sinal
                # de verdade não é QUANTAS sobram -- é a legenda estar em OUTRO
                # LUGAR DO VÍDEO. No defeito medido, o clipe cobria 181-201s da
                # fonte e as cues vinham de 2-22s: nenhuma sobreposição, e cada
                # outro portão dizia que estava tudo bem.
                cue_de = min(float(c["start"]) for c in cues)
                cue_ate = max(float(c["end"]) for c in cues)
                overlap = min(cue_ate, queima_ate) - max(cue_de, queima_de)
                if overlap < length * 0.5:
                    raise RuntimeError(
                        f"the caption for this cut sits at {cue_de:.1f}-"
                        f"{cue_ate:.1f}s of the source and the cut shows "
                        f"{queima_de:.1f}-{queima_ate:.1f}s: they overlap by "
                        f"{max(0.0, overlap):.1f}s of {length:.1f}s. The "
                        f"subtitle and this file are on different clocks. That "
                        f"is how two clips went out carrying the opening of the "
                        f"video as their caption, with every other gate green. "
                        f"Refusing rather than burning.")
            if fora > 0:
                notes.append(
                    f"{fora} subtitle cue(s) fall outside this cut's window and "
                    f"were not burned. That is speech happening after --end, so "
                    f"dropping them is right -- but if the cut was meant to "
                    f"include that speech, it is --end that is short.")
            longest = max(c["end"] - c["start"] for c in cues)
            most = max(len(c["lines"]) for c in cues)
            pendurados = S.finais_pendurados(
                cues, continua_depois=fala_apos_o_corte)
            style_facts["caption"] = {
                # Onde a última linha da legenda termina, e até onde ela pode
                # ir para a direita. Os dois saem das margens que o ASS
                # recebeu, que é o mesmo lugar onde o libass as aplica.
                "bottom_px": height - max(S.caption_piso(height),
                                          height - int(safe.get("y1", 0))),
                "right_px": width - max(10, width - int(safe.get("x1", width - 140))),
                "cues": escritas, "max_lines": most,
                "max_cue_s": round(longest, 2),
                "renderer": "ass",
                "karaoke": not perdas,
                "hanging_endings": pendurados,
                # A ÚLTIMA cue pendurada é outro defeito, e ele não é da
                # legenda: é o corte terminando no meio da frase. Medido no
                # render de conferência: o clipe fechava em "trabalho manual,
                # só que" e a fala seguia depois do fim. O reflow não tem o que
                # fazer -- não existe palavra seguinte DENTRO do clipe. Quem
                # conserta isso é o `--end`, e a mensagem tem de dizer isso, em
                # vez de mandar mexer na legenda.
                "ends_mid_sentence": bool(
                    cues and pendurados and cues[-1]["text"] in pendurados),
                # Onde a frase partida fecha. Sem isto, "mova o --end" é um
                # conselho sem destino.
                "sentence_closes_at_s": frase_fecha_em,
                # O corpo e a altura de letra que ele produz. A altura é o que
                # se vê e é o que encolheu 45% sem ninguém notar quando a
                # legenda virou ASS -- registrada aqui, ela vira aritmética.
                # A sincronia do destaque contra a fala medida no áudio. Fica
                # gravada para dar para acompanhar se piora, que foi o pedido.
                "sync": sincronia,
                "cues_fora_da_janela": fora,
                "size_px": S.caption_size(height),
                "ink_ratio": round(S.caption_size(height)
                                   * S.ASS_INK_POR_CORPO / height, 4),
            }
            notes.append(f"burned {escritas} caption cues as ASS: at most {most} "
                         f"lines and {longest:.1f}s each, each word lit as it is "
                         f"said (\\k, time split by syllable)")
            for perda in perdas:
                notes.append("WATCH OUT: " + perda)
            if pendurados:
                # A nota afirmava "porque o trecho não tinha fronteira nenhuma"
                # para toda cue acusada, e para a ÚLTIMA da janela isso é falso:
                # ali o que corta é o fim do clipe, não a falta de fronteira.
                # Duas causas, duas frases.
                porque = ("the reflow found no usable boundary in the stretch"
                          if len(pendurados) > 1 or not fala_apos_o_corte else
                          "the speech continues past the end of the cut")
                notes.append(
                    f"{len(pendurados)} cue(s) close on a word that needs a "
                    f"complement ({porque}): " + "; ".join(pendurados[:3]))
    if not cues and burn_reason:
        notes.append(burn_reason)

    # ------------------------------------------------- a geometria, parte dois
    #
    # AQUI, e não lá em cima, porque só aqui se sabe se vai haver legenda.
    #
    # O clipe que o dono recebeu em 15/09/2026 reservou 1240-1560 para uma
    # legenda que os portões acima derrubaram -- o SRT não estava aprovado --
    # e entregou um terço do quadro com fundo borrado e nada dentro. O layout
    # perguntava "onde a legenda cabe?" antes de alguém perguntar "vai haver
    # legenda?", e as duas perguntas estavam na ordem errada.
    queima_legenda = bool(cues and ass_path)
    if dividido:
        faixas_do_layout = layout_dividido(width, height, sw_util, sh,
                                           pip=pip, rosto=rosto,
                                           legenda=queima_legenda)
        seguida, rastreio = cadeia_dividida(
            width, height, faixas_do_layout,
            motion=bool(motion) and not planos, length=length,
            trilha=trilha, sw=sw_util, sh=sh,
            cheios=cheios, cheio_x=cheio_x)
        chain = pre + seguida
        if not queima_legenda:
            _pe = faixas_do_layout["pe_da_imagem"]
            notes.append(
                f"no caption is burned on this clip, so the caption band was "
                f"NOT reserved: the picture runs to y{_pe} instead of "
                f"y{faixas_do_layout['legenda_topo']} and the screen strip is "
                f"{faixas_do_layout['tela'][3]}px tall. A third of the frame "
                f"held empty for text that never arrives is a third of the "
                f"frame thrown away.")
    else:
        chain = (f"{pre}scale={width}:{height}:force_original_aspect_ratio=increase,"
                 f"crop={width}:{height}:(in_w-out_w)*{fx:.4f}:(in_h-out_h)*0.5,"
                 f"setsar=1,fps=30")

    # ------------------------------------------------------- movimento
    #
    # Defeito 2.8, e não é bug: é ausência de recurso. Um corte sem nenhuma
    # variação de escala lê como material bruto, e é a diferença mais barata
    # entre corte e edit. No PRIME o zoom leve é a regra e não a exceção -- todo
    # plano do `tese-01` traz um, entre 1,02 e 1,10.
    #
    # `shots` é a gramática dos EDITS portada: uma lista de planos com `in`/`out`
    # e um `efeito`. Sem ela, um zoom leve único cobre a janela inteira, que é o
    # mínimo para o clipe não ler como trecho bruto.
    # `shots` deixou de ser aceito-e-descartado. Até 15/09 este bloco escrevia
    # uma nota dizendo "IGNOREI os N planos deste clipe", e era a verdade: o
    # campo atravessava a assinatura inteira e nenhuma linha do render o lia.
    # A decupagem agora é feita mais abaixo, em `_decupagem()`, e o que sobra
    # aqui é o zoom da janela contínua -- que é o caso SEM planos.
    if motion and dividido:
        # No dividido o zoom já está DENTRO da faixa da webcam, e tem de estar:
        # aplicado no composto ele empurraria as duas faixas para fora do
        # lugar, cortando a tela nas beiradas -- que é o defeito que este modo
        # existe para não ter.
        if rastreio and not planos:
            notes.append(
                f"the person band FOLLOWS THE FACE frame by frame across "
                f"{rastreio['pontos']} sampled positions, zooming "
                f"{rastreio['zoom_min']:.1f}x to {rastreio['zoom_max']:.1f}x "
                f"(ceiling {rastreio['zoom_teto']:.1f}x, which is how far this "
                f"source's pixels stretch) as the layout changes -- 1.0x is the "
                f"source already being a full-frame webcam. The screen band is "
                f"held still on purpose: it is text, and text that drifts "
                f"cannot be read.")
        elif (faixas_do_layout or {}).get("corte_webcam") and not planos:
            notes.append("scale moves 1.00 to 1.06 inside the person band only. "
                         "The screen band is held still on purpose: it is text, "
                         "and text that drifts cannot be read.")
        else:
            notes.append("this clip has NO scale movement: there is no webcam "
                         "band to move, and moving the screen band would crop "
                         "the page it exists to keep whole. It will read as raw "
                         "footage, and that is the price of legibility here.")
    elif motion and not planos:
        zoom_from, zoom_to = 1.0, 1.06
        if isinstance(motion, dict):
            zoom_from = float(motion.get("de", zoom_from))
            zoom_to = float(motion.get("para", zoom_to))
        frames_total = max(1.0, length * 30)
        step = (zoom_to - zoom_from) / frames_total
        # fps antes do zoompan: com d=1 ele carimba cada quadro de entrada a
        # 1/30s, e sem acertar o fps antes o plano toca mais rápido que o áudio e
        # congela no fim. É a nota do PRIME, e ela custou um render para existir.
        chain += (f",zoompan=z='min({zoom_from}+{step:.8f}*on,{zoom_to})'"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
                  f":s={width}x{height}:fps=30,setsar=1")
        notes.append(f"scale moves {zoom_from:.2f} to {zoom_to:.2f} across the "
                     f"clip. A cut with no scale move reads as raw footage.")

    # ------------------------------------------- a decisão dita em voz alta
    #
    # UMA LINHA, SEMPRE, dizendo qual enquadramento saiu e por quê -- do mesmo
    # jeito que a detecção de legenda queimada já diz. Sem ela, "enquadrou
    # certo" e "ninguém olhou" ficam iguais para quem lê a saída, que foi
    # exatamente como os dois clipes de 15/09 saíram com a pessoa fora do
    # quadro e o relatório todo verde.
    if dividido:
        _wx, _wy, _ww, _wh = faixas_do_layout["webcam"]
        _tx, _ty, _tw, _th = faixas_do_layout["tela"]
        _corte_tela = faixas_do_layout.get("corte_tela")
        _de_onde = ("the WHOLE source fitted by width, nothing cropped"
                    if not _corte_tela else
                    f"the source MINUS the webcam corner ({_corte_tela[2]}x"
                    f"{_corte_tela[3]} at x{_corte_tela[0]} y{_corte_tela[1]}), "
                    f"fitted by width, so the person is not in frame twice")
        if rastreio:
            onde = (f"screen band {_tw}x{_th} at y{_ty}, {_de_onde}; person "
                    f"band {_ww}x{_wh} BELOW it at y{_wy}, TRACKING the face "
                    f"over {rastreio['pontos']} sampled positions "
                    f"({rastreio['zoom_min']:.1f}x-{rastreio['zoom_max']:.1f}x)")
        elif faixas_do_layout.get("corte_webcam"):
            _cx, _cy, _cw, _ch = faixas_do_layout["corte_webcam"]
            onde = (f"screen band {_tw}x{_th} at y{_ty}, {_de_onde}; person "
                    f"band {_ww}x{_wh} BELOW it at y{_wy}, cut from {_cw}x{_ch} "
                    f"of the source at x{_cx} y{_cy} (NO face track: not one "
                    f"sampled frame gave a face to follow)")
        else:
            onde = (f"screen band {_tw}x{_th} at y{_ty}, {_de_onde}, on a "
                    f"blurred fill -- no person band")
        notes.append(f"FRAMING: split screen ({enq['modo']}) -- {enq['porque']}. "
                     f"{onde}.")
        if cheios:
            notes.append(
                f"{len(cheios)} stretch(es) of this window are NOT a screen -- "
                + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in cheios)
                + ". There is nothing to split there, and splitting anyway puts "
                  "the same person in the frame twice, so those stretches are "
                  "framed the ordinary way: one vertical band on the face, "
                  "filling the picture.")
        _telas = enq.get("quadros_de_tela") or 0
        _lidos = enq.get("quadros") or 0
        if _lidos and _telas < _lidos * 0.7:
            # A janela MISTURA layouts, e ela é a regra numa live, não a
            # exceção: a fonte alterna entre webcam em tela cheia e tela
            # compartilhada com webcam de canto. A primeira versão deste modo
            # recortava sempre o mesmo retângulo e entregava um borrão de ombro
            # em 5 dos 8 quadros do mosaico -- medido na janela 82-106. Quem
            # resolve isso é o rastreio; a nota fica dizendo o que a janela é,
            # e só vira aviso quando não há rastreio para segurá-la.
            # A NOTA AFIRMAVA UMA CAUSA, E A CAUSA ESTAVA ERRADA.
            #
            # Ela dizia que os quadros de planura baixa "são uma webcam em
            # tela cheia". Numa live de navegador eram; numa live de gameplay
            # não são -- são o jogo, que é escuro e texturizado. Medido na
            # janela 481-502: 21 dos 39 quadros ficam abaixo do limiar e o
            # maior rosto de qualquer um deles é 0,0128, quatro vezes abaixo
            # de PIP_ROSTO_AREA_MAX. Nenhum é uma pessoa em close.
            #
            # Quem afirma agora é `cheios`, que mede rosto grande e não
            # planura. Esta nota voltou a ser o que devia: um relato do que
            # foi medido, sem concluir o que a medida não sustenta.
            notes.append(
                f"this window is not uniform: {_telas} of {_lidos} sampled "
                f"frames read as a flat screen and the rest do not. Low "
                f"flatness alone does NOT mean the camera took over -- dark "
                f"game capture measures the same -- so what decides is whether "
                f"a face big enough to be the subject shows up, and "
                + (f"it does, in {len(cheios)} stretch(es), listed above."
                   if cheios else
                   "it never does here: the person stays in the corner webcam "
                   "the whole way, and the person band is a fixed crop of it."))
    style_facts["framing"]["face_track"] = rastreio
    style_facts["framing"]["bands"] = None if not faixas_do_layout else {
        "webcam": list(faixas_do_layout["webcam"]),
        "screen": list(faixas_do_layout["tela"]),
        "caption_top": faixas_do_layout["legenda_topo"],
        "picture_bottom": faixas_do_layout.get("pe_da_imagem"),
        "caption_band_reserved": faixas_do_layout.get("legenda_reservada"),
        "screen_crop": list(faixas_do_layout.get("corte_tela") or []),
        "webcam_crop": list(faixas_do_layout.get("corte_webcam") or [])}

    # ------------------------------------------------------- texto do acervo
    #
    # Defeito 2.6, as duas metades. Detectar já foi feito acima; aqui é tratar,
    # e a decisão vai num aviso ao dono, nunca num silêncio.
    #
    #  - se o material já tem texto embaixo e nós vamos queimar legenda, sem
    #    tratamento saem DUAS legendas no mesmo quadro. O degradê do PRIME cobre
    #    a de baixo e a nossa fica sozinha.
    #  - `cover_footer=False` é a outra saída legítima: não cobre e não queima.
    queimamos = bool(cues and ass_path)
    # O terceiro estado, e o que ele provoca. `bottom` é o booleano duro,
    # calibrado para tarja fixa; `bottom_suspect` é o material que mostra sinal
    # de texto sem alcançá-lo -- ver os gatilhos em `warden_style`. Os dois
    # ligam o degradê, e essa escolha é assimétrica de propósito: cobrir a faixa
    # de baixo de um material limpo custa um degradê discreto num quinto do
    # quadro; NÃO cobrir um material com legenda custa o clipe, porque saem duas
    # legendas de origens diferentes no mesmo quadro e isso não se conserta
    # depois de publicado. Em 14/09 esse `False` desligou em cascata o degradê,
    # o REJECT do `check_sidecar` e todas as notas, e o clipe saiu.
    tem_texto = bool(source_text.get("bottom"))
    suspeito = bool(source_text.get("bottom_suspect"))
    # Como a nota chama o que foi visto. "Já carrega" e "pode carregar" são
    # coisas diferentes e a nota não pode dizer a primeira quando mediu a
    # segunda -- é o mesmo defeito de "limpo" e "nem olhei" serem a mesma frase.
    como = ("this footage already carries burned text along the bottom"
            if tem_texto else
            "this footage MAY already carry burned text along the bottom "
            "(SUSPECT, not the hard boolean)")
    if tem_texto or suspeito:
        cover = cover_footer
        if cover is None:
            # Cobre quando a nossa legenda vai disputar a mesma faixa. Se não
            # queimamos nada, o texto do acervo não está competindo com
            # ninguém, e cobri-lo seria estragar o quadro por nada. `queimamos`
            # é o que SAIU do render, não o que se pretendia queimar.
            cover = queimamos
        if cover:
            # A medida entra com folga, mas presa entre 18% e 24% da altura.
            #
            # A medida sozinha não serve para desenhar: material com duas faixas
            # de texto -- um disclaimer colado no rodapé e a legenda do acervo
            # acima dele -- tem um vão limpo entre as duas, e qualquer varredura
            # que pare na primeira queda cobre só a de baixo. Foi o que aconteceu
            # aqui: a varredura disse 8%, o texto ia até 14%, e a legenda do
            # acervo ficou legível embaixo da nossa.
            #
            # 20% cobre as duas faixas com folga e ainda deixa a nossa legenda,
            # que mora por volta de 25%, fora do degradê; 24% é o teto, porque
            # além disso se apaga imagem para resolver um problema de texto. A
            # medida continua na nota, como evidência de até onde o texto ia.
            reach = float(source_text.get("bottom_reach") or 0.0) + 0.06
            band = int(height * min(0.24, max(0.20, reach)))
            if dividido:
                # No dividido o pé da FONTE não é o pé do QUADRO: ele está no pé
                # da faixa da tela, e um degradê de 20% da altura do quadro
                # cobriria fundo borrado e deixaria a legenda do acervo
                # intacta, em cima. O degradê sobe até onde o texto da fonte
                # começa -- e o que ele pega abaixo disso é fundo, que não custa
                # nada. A nossa legenda é desenhada DEPOIS dele (ver a ordem das
                # camadas), então ela continua legível por cima.
                _tx, _ty, _tw, _th = faixas_do_layout["tela"]
                band = int(height - (_ty + (1.0 - min(0.9, reach)) * _th))
                band = max(1, min(height - 1, band))
            footer, fy = S.footer_png(os.path.join(art_dir, f"{stem}-rodape.png"),
                                      width, height, band=band)
            overlays.append((footer, fy, 0.0, float(length), 0.0))
            style_facts["footer_covered"] = True
            aviso_marca_dagua = (
                " If that bottom text is another clipper's watermark rather "
                "than the archive's own captions, covering it breaks the rules "
                "-- re-cut with cover_footer=False and drop --subtitles.")
            if queimamos:
                notes.append(
                    f"{como} ({source_text['evidence']}). Covered it with the "
                    "gradient footer so there is one caption in frame and not "
                    "two." + aviso_marca_dagua)
            else:
                # A nota afirmava a disputa "uma legenda em quadro e não duas"
                # mesmo quando legenda nenhuma nossa foi queimada. Chegar aqui
                # agora só é possível com `cover_footer=True` explícito, e aí o
                # degradê apagou a legenda do acervo em troca de nada -- o que a
                # nota tem de dizer, com o motivo de a nossa não ter saído.
                notes.append(
                    f"{como} ({source_text['evidence']}). Covered it with the "
                    "gradient footer because cover_footer=True was asked for "
                    "-- but NO caption of ours was burned in the end"
                    # Só a primeira frase do motivo: o texto inteiro do
                    # portão já saiu numa nota própria logo acima, e repeti-lo
                    # aqui enterra a frase que importa.
                    + (f" ({burn_reason.split('. ')[0]})" if burn_reason else "")
                    + ", so the gradient erased the archive's own text and put "
                    "nothing in its place. Re-cut with cover_footer=False to "
                    "get that text back, or fix what stopped our captions."
                    + aviso_marca_dagua)
        else:
            # Suspeito sem cobertura é tratado como o ramo duro, e o ramo
            # duro não derruba a legenda aqui: ele diz para não queimar por
            # cima, e o `check_sidecar` lá embaixo REPROVA o arquivo que saiu
            # assim mesmo. Não derruba porque chegar aqui exige um
            # `cover_footer=False` explícito -- o dono já disse que não quer o
            # degradê, e jogar a legenda dele fora em silêncio seria o mesmo
            # defeito pelo avesso. O que não pode é a saída ficar muda, e não
            # fica: a nota aqui, o REJECT no sidecar.
            notes.append(
                f"{como} ({source_text['evidence']}). Not covering it, so do "
                "not burn captions over it -- two captions in one frame is a "
                "reject.")

    # A decisão dita em voz alta, TODA VEZ QUE QUEIMA LEGENDA -- inclusive
    # quando a detecção não achou nada. Antes deste bloco a nota sobre o texto
    # do acervo só existia dentro do `if` acima, então um material limpo e um
    # material que ninguém olhou saíam idênticos para quem lê a saída: nada. E
    # "nada" era a saída do clipe de 14/09, onde a detecção tinha rodado, tinha
    # números, e eles não chegaram a lugar nenhum. Uma linha, com os números.
    if queimamos:
        if tem_texto and style_facts.get("footer_covered"):
            decidiu = ("covered the bottom with the gradient footer before "
                       "burning ours")
        elif suspeito and style_facts.get("footer_covered"):
            decidiu = ("covered the bottom with the gradient footer anyway -- a "
                       "gradient on clean footage is cheap, a second caption in "
                       "frame is not")
        elif tem_texto or suspeito:
            decidiu = ("did NOT cover it (cover_footer=False), so our caption "
                       "was burned over whatever is down there -- check the "
                       "contact sheet before delivering")
        else:
            decidiu = ("found nothing down there, so our caption was burned "
                       "straight onto the picture")
        notes.append(f"footage checked before burning captions "
                     f"({source_text['evidence']}): {decidiu}.")

    if hook:
        # O hook é medido contra a largura útil e quebrado em até duas linhas
        # antes de virar pixel. 854px num quadro de 1080: a coluna direita do
        # TikTok reserva 140px e a margem esquerda come 86px.
        usable = S.usable_width(width)
        size = max(20, int(width * S.HOOK_SIZE_RATIO))
        floor = max(16, int(width * S.HOOK_MIN_RATIO))
        # `*assim*` marca as palavras de acento. As marcas saem ANTES de medir:
        # medir o texto com os asteriscos dentro daria uma largura que não é a
        # do que vai para a tela, e a conta de "o hook cabe" é exata de propósito.
        hook, acento = S.split_acento(hook)
        lines, fitted, whole = S.fit_lines(hook, usable, size, floor)
        # O que a margem governa é o TOPO do bloco, não o centro dele, e essa
        # confusão é o defeito inteiro. `hook_y` sempre foi o centro que
        # `text_png` recebe: com `y0` 200 mais corpo 76 dando 276, e duas
        # linhas de entrelinha 101, metade do bloco subia -- topo em 175 e a
        # primeira linha de tinta medida em 184 no arquivo entregue (199 no
        # segundo corte, que abre sem acento). As abas e a lupa do TikTok
        # cobrem os primeiros ~200px, então o hook estava dentro da faixa de
        # risco desde sempre, e a conta que dizia "o hook cabe" só olhava a
        # LARGURA. Agora o topo do bloco é a margem e o centro é derivado dele.
        marge = S.margens(width, height)
        leading = int(fitted * S.LEADING)
        hook_top = max(marge["top"], int(safe.get("y0", 0)))
        # O LOWER-THIRD SAIU em 17/09/2026, junto com o layout que o pedia.
        #
        # Ele existia porque a faixa da webcam ficava EM CIMA e ocupava 616px:
        # um bloco de hook de 202px dentro dela caía em cima dos olhos, desse
        # jeito, e a saída era encostá-lo no pé da faixa, onde havia peito e
        # ombro. No layout novo a faixa da pessoa ficou EMBAIXO, encostada no
        # teto da legenda -- o mesmo cálculo agora põe o hook exatamente sobre
        # a cara, que é o defeito que ele fora criado para evitar.
        #
        # Sem ele o hook volta para o topo, que é o comportamento do caminho
        # normal e o que o corpus aprovado tem. Lá em cima ele cai sobre os
        # primeiros pixels da tela durante 3 segundos, do mesmo jeito que cai
        # sobre a imagem num corte normal.
        #
        # E QUANDO A FONTE TEM TEXTO PRÓPRIO NO TOPO, ele desce o suficiente
        # para não sentar em cima dele. Medido em 18/09/2026 nesta live: o
        # placar do jogo fica em y 30-120 da fonte e o `[Tab] Hand Signals` em
        # 150-185; dentro da faixa da tela (996x560 em y280) isso cai em
        # y 296-376 do quadro, e o hook ocupava 280-482. Cobria os dois.
        #
        # O quanto descer não é um número novo: `burned_text_bands` olha os
        # 16% de cima da fonte, então é essa mesma fração da faixa da tela que
        # o hook pula. Com 560px de tela dá 90px, e o bloco vai de 386 a 588 --
        # abaixo do texto do jogo e ainda bem acima da faixa da pessoa.
        #
        # O teto existe porque a conta tem de continuar cabendo: se a tela for
        # baixa o hook não pode ser empurrado para dentro da cara.
        if dividido and faixas_do_layout and source_text.get("top"):
            _tx, _ty, _tw, _th = faixas_do_layout["tela"]
            _folga = max(6, height // 120)
            _abaixo = _ty + int(_th * S.BANDA_TEXTO_FONTE) + _folga
            _teto = (faixas_do_layout["webcam"][1] - len(lines) * leading
                     - _folga)
            if hook_top < _abaixo <= _teto:
                hook_top = _abaixo
                notes.append(
                    f"the hook was pushed down to y{hook_top}: this source "
                    f"carries its own burned text in the top "
                    f"{S.BANDA_TEXTO_FONTE:.0%} of the frame, which lands at "
                    f"y{_ty}-{_ty + int(_th * S.BANDA_TEXTO_FONTE)} inside the "
                    f"screen strip, and the hook was sitting on it.")
        hook_y = hook_top + (len(lines) * leading) // 2
        png = os.path.join(art_dir, f"{stem}-hook.png")
        png, oy = S.text_png(lines, png, width, height, hook_y, fitted,
                             scrim=True, acento=acento)
        # Três segundos e um fade curto, não o clipe inteiro. Ver HOOK_SECONDS
        # em warden_style: em 14/09 a frase estava nos oito quadros do mosaico,
        # e dos 3s em diante ela só disputava o quadro com a legenda da fala.
        hook_s = min(float(length), S.HOOK_SECONDS)
        hook_fade = min(S.HOOK_FADE_S, hook_s / 2)
        overlays.append((png, oy, 0.0, hook_s, hook_fade))
        # A largura REAL do texto desenhado, medida com a fonte que o desenhou.
        # Não é estimativa: é o número contra o qual a quebra foi decidida, e é
        # o que permite a "o hook cabe inteiro" ser aritmética em vez de olhar.
        from PIL import Image as _Im, ImageDraw as _Dr
        _probe = _Dr.Draw(_Im.new("RGBA", (8, 8)))
        _f = S._font(fitted)
        drawn = int(max(_probe.textlength(l, font=_f) for l in lines)) if lines else 0
        style_facts["hook"] = {"width_px": drawn, "usable_px": usable,
                               # Onde a primeira linha começa e onde a última
                               # coluna de tinta termina. Vem de quem DESENHOU,
                               # não de uma estimativa: é o mesmo número que o
                               # `text_png` usou. Sem ele o portão de margem
                               # teria de adivinhar.
                               "top_px": hook_top,
                               "right_px": (width + drawn) // 2,
                               "lines": len(lines), "size_px": fitted,
                               "chars": len(str(hook)), "complete": bool(whole),
                               "seconds_on_screen": round(hook_s, 2),
                               "fade_s": round(hook_fade, 2),
                               "accent_words": sorted(
                                   " ".join(lines).split()[i] for i in acento
                                   if i < len(" ".join(lines).split())),
                               "accent_rgb": list(S.COR_ACENTO_HOOK[:3])}
        notes.append(f"hook drawn at {fitted}px in {len(lines)} line(s): "
                     f"{drawn}px against {usable}px of usable width")
        notes.append(f"the hook leaves at {hook_s:.1f}s with a "
                     f"{hook_fade:.1f}s fade, instead of standing on the picture "
                     f"for the whole {float(length):.1f}s")
        if fitted <= floor:
            notes.append(f"the hook only fits at the {floor}px floor. It is "
                         f"{len(hook)} characters -- shorter reads better in a feed.")

    audio_policy = R.get(rules, "video.audio")
    silent = audio_policy == "forbidden" or (sound == "platform" and audio_policy != "required")
    embed = bool(track) and not silent

    # Com planos, a entrada é posicionada no PRIMEIRO plano e cada `trim` corre
    # relativo a isso: `-ss` antes de `-i` é o seek rápido (o wiki do ffmpeg diz
    # "very fast" contra "relatively slow, frame-by-frame" para a forma de
    # saída), e um edit que decodifica desde o zero de uma fonte de 18 minutos
    # paga isso em cada plano.
    seek = float(planos[0][0]) if planos else float(start)
    args = ["ffmpeg", "-y", "-ss", f"{seek:.3f}", "-i", source]
    next_input = 1
    track_index = None
    if embed:
        # The track enters at its drop unless told otherwise, because an edit
        # that opens on an intro has spent its first second on nothing.
        at = track_start
        if at is None:
            at = (grid or {}).get("drop_s")
        if at is None:
            at = 0.0
            notes.append("no drop was found in this track -- it never gains "
                         "body -- so the music enters at 0.00s, which is its "
                         "intro. Pass --track-start to choose a better entry.")
        args += ["-ss", f"{float(at):.3f}", "-i", track]
        track_index = next_input
        next_input += 1

    # Cada PNG entra como um vídeo de um quadro em laço, porque um overlay com
    # fade de alfa sobre uma imagem parada apaga o único quadro que existe --
    # é a nota do PRIME e custou um render para ser descoberta.
    overlay_index = {}
    for png, _y, _a, _b, _f in overlays:
        args += ["-loop", "1", "-framerate", "30", "-i", png]
        overlay_index[png] = next_input
        next_input += 1

    args += ["-t", f"{length:.3f}"]

    # Um filter_complex quando há overlay ou trilha; o -vf simples continua
    # servindo o caso sem texto, que é o mais barato e o mais comum nos testes.
    # A ORDEM das camadas, e ela não é arbitrária. O degradê do rodapé cobre o
    # texto queimado do próprio material, então ele vai por baixo de tudo; a
    # nossa legenda vem sobre ele, senão o degradê apagaria a nossa junto com a
    # do acervo; e o hook vai por último, porque nada pode passar por cima dele
    # nos três segundos em que ele existe.
    camadas = [("overlay",) + o for o in overlays if o[0] == footer]
    if ass_path:
        camadas.append(("ass", ass_path))
    camadas += [("overlay",) + o for o in overlays if o[0] != footer]

    if planos:
        # Um `trim` por plano, cada um com o seu zoom, e `concat` no fim. O
        # enquadramento vertical vem DEPOIS da emenda, uma vez só: recortar
        # plano a plano daria a cada um um enquadramento diferente da mesma
        # cena, que é o oposto do que um edit quer.
        cortes = []
        rotulos = []
        for i, (a, b, _batidas) in enumerate(planos):
            dur = b - a
            filtro = (f"[0:v]trim=start={a - seek:.4f}:end={b - seek:.4f},"
                      f"setpts=PTS-STARTPTS,fps=30")
            if motion:
                # Zoom leve POR PLANO, alternando o sentido: todo plano
                # empurrando para dentro lê como um efeito, não como montagem.
                de, para = (1.0, 1.06) if i % 2 == 0 else (1.06, 1.0)
                quadros = max(1.0, dur * 30)
                passo = (para - de) / quadros
                filtro += (f",zoompan=z='clip({de}+{passo:.8f}*on,"
                           f"{min(de, para)},{max(de, para)})'"
                           f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1"
                           f":s={sw or width}x{sh or height}:fps=30,setsar=1")
            filtro += f"[p{i}]"
            cortes.append(filtro)
            rotulos.append(f"[p{i}]")
        cortes.append("".join(rotulos)
                      + f"concat=n={len(planos)}:v=1:a=0[emendado]")
        partes = cortes + [f"[emendado]{chain}[v0]"]
        if motion:
            notes.append(f"scale moves on each of the {len(planos)} shots, "
                         f"alternating in and out. A cut with no scale move "
                         f"reads as raw footage.")
    else:
        partes = [f"[0:v]{chain}[v0]"]
    last, n = "v0", 0
    for camada in camadas:
        n += 1
        alvo = f"v{n}"
        if camada[0] == "ass":
            # `fontsdir` apontando para os assets do repo: sem ele o libass não
            # acha a Anton e cai numa fallback genérica de peso errado, que é o
            # defeito 2.3 voltando pela porta da fonte.
            partes.append(f"[{last}]ass=f={_ff_valor(camada[1])}"
                          f":fontsdir={_ff_valor(S.ASSETS)}[{alvo}]")
        else:
            _, png, oy, a, b, fade = camada
            idx = overlay_index[png]
            # `shortest=0:repeatlast=0` impede que o último quadro do PNG fique
            # carimbado depois do fim da janela, que é como um texto de 2s vira
            # um texto que não sai mais da tela.
            filtros = "format=yuva420p,setpts=PTS-STARTPTS"
            if fade:
                # O fade corre no relógio do próprio clipe: a entrada é uma
                # imagem em laço a 30fps que começa em 0, igual ao vídeo. Um
                # `st` relativo à janela do overlay sairia adiantado.
                st = max(float(a), float(b) - float(fade))
                filtros += (f",fade=t=out:st={st:.3f}:d={float(fade):.3f}"
                            f":alpha=1")
            partes.append(f"[{idx}:v]{filtros}[o{n}]")
            partes.append(f"[{last}][o{n}]overlay=0:{int(oy)}"
                          f":shortest=0:repeatlast=0"
                          f":enable='between(t,{a:.3f},{b:.3f})'[{alvo}]")
        last = alvo
    video = ";".join(partes).replace(f"[{last}]", "[vid]")

    if embed:
        fade = max(0.5, min(3.0, length * 0.12))
        music = (f"[{track_index}:a]atrim=duration={length:.3f},asetpts=N/SR/TB,"
                 f"volume=-6dB,afade=t=out:st={max(0, length - fade):.3f}:d={fade:.3f}[m]")
        # "não tem áudio" e "não consegui perguntar" davam os dois `False`, e
        # `False` aqui joga fora a faixa de fala inteira: o clipe sai só com a
        # música, num modo cujo comentário logo abaixo diz que as palavras é que
        # carregam o clipe. Um mosaico de quadros não mostra fala faltando.
        sonda = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name", "-of", "csv=p=0", source],
            capture_output=True, text=True)
        if sonda.returncode != 0:
            raise RuntimeError(
                f"could not ask whether {os.path.basename(source)} has an audio "
                f"track (ffprobe exited {sonda.returncode}). Mixing a track over "
                f"it now would silently drop the speech, so this cut stops.")
        has_source_audio = bool(sonda.stdout.strip())
        if not has_source_audio:
            notes.append("this footage has no audio track, so the clip carries "
                         "only the music -- no speech was dropped, there was none")
        if has_source_audio and not planos:
            # Speech over music, not music over speech: the track carries the
            # cut, the words carry the clip.
            mix = (f"[0:a]atrim=duration={length:.3f},asetpts=N/SR/TB,volume=1.0[v];"
                   f"[v][m]amix=inputs=2:duration=first:weights=1 0.55[a]")
        else:
            mix = "[m]anull[a]"
        if has_source_audio and planos:
            # Mesma razão do bloco acima: a fala contínua sobre imagem emendada
            # fala da cena errada. Num edit a trilha é o áudio, e o que a fala
            # tinha a dizer está na legenda.
            notes.append("the source speech was left out: the picture is "
                         "spliced from several places and continuous speech "
                         "would be talking over the wrong scene. The track is "
                         "the audio, and the words are in the caption.")
        args += ["-filter_complex", f"{video};{music};{mix}",
                 "-map", "[vid]", "-map", "[a]"]
        notes.append(f"track mixed in from {float(at):.2f}s with a {fade:.1f}s fade out")
    elif overlays or ass_path or planos:
        # `ass_path` sem overlay nenhum é um caso real: clipe com legenda e sem
        # hook. Com o teste velho (`elif overlays`) ele caía no `-vf chain` e
        # saía sem legenda, sem erro e sem nota. `planos` entra pelo mesmo
        # motivo: a emenda vive no filter_complex, e cair no `-vf` a jogaria
        # fora em silêncio -- que é o defeito que este bloco existe para não
        # repetir.
        args += ["-filter_complex", video, "-map", "[vid]"]
        if not silent and not planos:
            args += ["-map", "0:a?"]
        elif planos and not silent:
            # A fala da fonte não acompanha uma imagem emendada: os planos vêm
            # de pontos diferentes do material e o áudio contínuo ficaria
            # falando sobre a cena errada a partir do primeiro corte. Um edit
            # sem trilha e com planos sai mudo, e diz isso.
            args += ["-an"]
            notes.append("spliced shots and no track, so this clip ships "
                         "silent: the source speech does not follow a picture "
                         "that jumps, and the platform is where the sound goes.")
    else:
        args += ["-vf", chain]

    args += ["-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF,
             "-maxrate", X264_MAXRATE, "-bufsize", X264_BUFSIZE,
             "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if silent:
        args += ["-an"]
        notes.append("ships silent: the sound is added on the platform"
                     + (", and the cut is on the bar so it will land"
                        if grid else ""))
    else:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args += [out]
    run(args, TIMEOUT_RENDER, "ffmpeg")
    # ffmpeg can exit 0 and write nothing worth having. What this returns is the
    # file as it is on disk, not the file we asked for.
    if not os.path.isfile(out) or os.path.getsize(out) < 1024:
        raise RuntimeError("ffmpeg finished but wrote no usable file")
    real = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", out], capture_output=True, text=True).stdout.strip()
    try:
        measured = round(float(real), 2)
    except ValueError:
        raise RuntimeError("ffmpeg wrote a file with no readable duration")
    if abs(measured - length) > 1.0:
        notes.append(f"asked for {length:.2f}s and the file is {measured:.2f}s")

    # O portão. Nenhum clipe sai daqui sem um mosaico do próprio render, porque
    # a verificação numérica aprovou um arquivo com dez defeitos visíveis e vai
    # aprovar o próximo: ela confere duração, resolução e regras da campanha, e
    # nenhum desses defeitos é um número. O arquivo pode existir sem o mosaico;
    # a ENTREGA não pode, e é quem chama que segura a entrega.
    # Camadas de texto SIMULTÂNEAS, não inputs de overlay. As cues não se
    # sobrepõem (o reflow garante), então o que divide um quadro é no máximo:
    # o degradê do rodapé, uma cue, e o hook. Contar os 14 inputs como 14
    # camadas foi o que fez o portão reprovar dois clipes corretos.
    #
    # E ele fica sendo o que é: um número no relatório, não o orçamento contra
    # o qual `style check` conta faixas de texto no render pronto. Quem faz essa
    # conta é `_linhas_demais`, e ela usa LINHAS (`hook.lines`,
    # `caption.max_lines`) e não camadas, porque uma cue de duas linhas lê como
    # duas faixas -- entre uma linha e outra passa imagem limpa. Cruzar com
    # `text_layers`, que diria 1, reprovaria todo corte de fala do projeto.
    # As bordas dos planos, no relógio do CLIPE, e o erro de cada uma contra a
    # grade de batida. É o que transforma "cortou na batida" em número: sem
    # isso, quem olha o mosaico vê trocas de cena e não tem como saber se elas
    # caem na música ou se são as do material de origem -- que foi exatamente o
    # que aconteceu no edit do Djokovic, onde as 12 trocas eram do vídeo e
    # nenhuma tinha sido escolhida.
    if planos:
        bordas, acc = [], 0.0
        for a, b, _n in planos[:-1]:
            acc += (b - a)
            bordas.append(round(acc, 4))
        erros, fase = warden_beat.erro_na_grade(bordas, (grid or {}).get("beat_s"))
        style_facts["shots"] = {
            "count": len(planos),
            "cuts_s": bordas,
            "beats": [n for _a, _b, n in planos],
            "per_20s": round(len(planos) * 20.0 / max(0.001, length), 1),
            "beat_s": (grid or {}).get("beat_s"),
            "grid_phase_s": fase,
            "cut_error_ms": [round(e * 1000) for e in erros],
            "worst_error_ms": round(max(erros) * 1000) if erros else 0,
        }
        if erros:
            pior = max(erros) * 1000
            notes.append(
                f"the {len(bordas)} scene change(s) land on the beat to within "
                f"{pior:.0f}ms at worst"
                + ("" if pior < 80 else
                   " -- over the 80ms the approved reference measures, so this "
                   "does not read as cut to the music"))

    style_facts["text_layers"] = (
        (1 if footer else 0)
        + (1 if style_facts.get("caption") else 0)
        + (1 if style_facts.get("hook") else 0))

    # ------------------------------------------------------ a tarja, medida
    #
    # ISTO É O PORTÃO DE TARJA PRETA, e ele estava fora do caminho.
    #
    # A detecção existe desde 15/09 -- `barras_pretas` abre os quadros e
    # `_barra_preta_reprova` reprova -- e só era alcançada por `cross_check`,
    # que só é chamado por `warden style check`. Esse comando não está na ordem
    # de trabalho da skill. O portão de entrega do `cut` é este bloco aqui, e
    # ele conferia apenas `check_sidecar`, que lê o que o render ANOTOU de si:
    # o sidecar sabe a largura do hook porque foi o PIL que o desenhou, e não
    # sabe nada sobre as bordas do arquivo, porque ninguém as tinha aberto.
    #
    # Então o clipe de 289px de tarja no pé do quadro -- o defeito que este
    # repositório documenta em três lugares -- passava por TODOS os portões que
    # o prompt manda rodar, e saía como aprovado. Testes verdes, e a tarja lá.
    #
    # O conserto é chamar o que já existe, não reescrevê-lo: os quadros saem do
    # `sample_frames` do próprio `warden_style`, a medida sai do
    # `barras_pretas` dele, e o veredito sai do `cross_check` dele. `medida`
    # carrega só as duas chaves de barra de propósito: sem
    # `text_rows_per_frame` o `_linhas_demais` devolve lista vazia e sem
    # `text_width_ratio` o cruzamento de largura não diz nada -- essas duas
    # perguntas são do `style check`, que mede o arquivo inteiro. Aqui a
    # pergunta é uma só, e é a que reprovava o clipe entregue.
    #
    # Custa uma passada de ffmpeg sobre o render pronto, ao lado da que o
    # contact sheet já faz. É o preço de olhar as bordas, e ele é menor do que
    # o de entregar a moldura de novo.
    barras, porque_barras = None, None
    try:
        quadros = S.sample_frames(out, 0, measured or length, count=10)
        barras, porque_barras = S.barras_pretas(quadros)
    except Exception as exc:
        # Nem um erro aqui vira silêncio: "não olhei as bordas" é a mensagem
        # que `_barra_preta_reprova` já sabe transformar em REJECT, e é o que
        # tem de acontecer -- uma tarja é invisível para todo o resto.
        porque_barras = (f"{type(exc).__name__}: {exc} ao abrir os quadros do "
                         f"render para olhar as bordas")
    style_facts["black_bars"] = barras
    if porque_barras:
        style_facts["black_bars_why"] = porque_barras

    style_path = os.path.splitext(out)[0] + "-estilo.json"
    try:
        with open(style_path, "w", encoding="utf-8") as fh:
            json.dump(style_facts, fh, ensure_ascii=False, indent=1)
    except OSError as exc:
        style_path = None
        notes.append(f"could not write {os.path.basename(style_path or '')} "
                     f"beside this clip ({type(exc).__name__}), so a later "
                     f"`warden style check` on it has only the approximate "
                     f"pixel metrics, which reject nothing.")

    sheet = None
    try:
        sheet = S.contact_sheet(out, os.path.splitext(out)[0] + "-contato.jpg",
                                label=os.path.basename(out))
    except Exception as exc:
        notes.append(f"could not build the contact sheet ({type(exc).__name__}: "
                     f"{exc}). Since 16/09 this does NOT block delivery -- send "
                     f"the clip -- but there will be no image of this render at "
                     f"all if it comes back wrong.")
    if sheet is None and not any("contact sheet" in n for n in notes):
        notes.append("could not build the contact sheet for this render. "
                     "Since 16/09 this does NOT block delivery -- send the clip "
                     "-- but there will be no image of it at all if it comes "
                     "back wrong.")
    # O render conta o que fez de si, e quem entrega confere antes do MEDIA:.
    # `cross_check` entra junto pelos PIXELS das bordas -- ver o bloco da tarja
    # acima. São as duas cegueiras: `check_sidecar` sabe o que desenhamos e não
    # vê o arquivo; `cross_check` vê o arquivo e não sabe o que desenhamos.
    breaches = S.check_sidecar(style_facts)
    breaches += S.cross_check(style_facts,
                              {"black_bars": barras,
                               "black_bars_why": porque_barras})
    # Desde 16/09/2026 a conferência visual NÃO impede a entrega -- ver o bloco
    # "O QUE BLOQUEIA UMA ENTREGA" no topo de warden_style.py. O que ela produz
    # agora é uma OBSERVAÇÃO, e o destino de uma observação é a pessoa: entra
    # nas notas, o agente a repassa em uma frase, e o clipe sai.
    #
    # O prefixo é diferente de propósito. `STYLE REJECT:` treinou o agente a
    # parar (foi o que ele fez no degrau 4 do demo, com os dois clipes prontos
    # em disco). `LOOK:` não manda fazer nada -- descreve o que a ferramenta
    # viu, que é tudo o que ela sabe fazer.
    for level, message in breaches:
        if level == S.OBRIGACAO:
            notes.append(f"STYLE REJECT: {message}")
        else:
            notes.append(f"LOOK: {message}")
    return {"out": out, "duration_s": measured, "asked_s": round(length, 2),
            "asked_for_captions": bool(caption_srt),
            "asked_for_hook": bool(hook),
            "notes": notes, "grid": grid, "sheet": sheet,
            "style": style_facts, "style_path": style_path,
            "style_breaches": [m for lv, m in breaches if lv == S.OBRIGACAO],
            "style_looks": [m for lv, m in breaches if lv == S.OBSERVACAO]}
