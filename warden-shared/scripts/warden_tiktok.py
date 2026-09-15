#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Um mp4 na caixa de entrada do TikTok, pelo caminho que foi MEDIDO em 14/09/2026.

Este módulo não faz o Direct Post. Ele faz o outro caminho, o de rascunho, e a
diferença entre os dois é a razão de o agente conseguir postar sem auditoria
nenhuma. Medido em 14/09/2026, com app em modo sandbox, NUNCA auditado, client
key `sbawmkiukayqqc1740`, escopo concedido `user.info.basic,video.upload` — sem
`video.publish`:

  16:50:48  POST /v2/post/publish/inbox/video/init/  -> HTTP 200,
            publish_id v_inbox_file~v2.7685480798933239816
  16:50:51  PUT do arquivo, 8.933.576 bytes em UM chunk -> HTTP 201
  16:50:57  status PROCESSING_UPLOAD, uploaded_bytes 8933576
  ~16:51:12 status SEND_TO_USER_INBOX
  17:34:17  status SEND_TO_USER_INBOX, sem erro

O dono abriu o app, publicou, entrou em OUTRA conta e viu o vídeo. Então a trava
de `SELF_ONLY` para cliente não auditado não alcança este fluxo. O registro
inteiro, com o critério escrito ANTES do teste, está em
`CRITERIO-TESTE-TIKTOK-INBOX.md`; o mapa das três redes está em
`ANALISE-POSTAGEM-REDES.md`.

Duas coisas que custaram tempo e que este módulo diz em voz alta, toda vez:

1. O rascunho chega na CAIXA DE ENTRADA (notificação) do app, não na aba
   Rascunhos do perfil. Em 14/09 o dono procurou na aba errada e por 40 minutos
   o teste pareceu ter falhado.
2. A LEGENDA NÃO SOBE. O corpo do init tem quatro campos e nenhum deles é texto:
   `source_info.source`, `video_size`, `chunk_size`, `total_chunk_count`. Quem
   escreve legenda e hashtag é a pessoa, no app, na hora de publicar. Não existe
   campo de título aqui, e inventar um é o jeito mais rápido de o envio quebrar
   com um erro que ninguém entende.

O que continua NÃO medido, e por isso não é afirmado em lugar nenhum daqui:
quanto tempo o rascunho demora a aparecer, se a entrega é confiável ao longo do
tempo (há relato público de parar e voltar sozinha — postiz-app #1338), e o teto
de 5 rascunhos pendentes em 24h, que nunca foi encostado.

O token se renova sozinho, e isso não é atrevimento: a documentação de OAuth do
TikTok diz, literalmente, que "it can be refreshed without user consent. The
developer's back-end server can schedule background jobs to keep tokens up to
date." O access_token dura 24 horas e o refresh_token dura 365 dias, então sem
renovação automática um agente que roda sozinho pararia todo dia esperando
alguém clicar. A armadilha, também literal, é que "the returned refresh_token
may be different than the one passed in the payload" — o refresh gira, e quem
guardar o antigo funciona por semanas e quebra no dia da rotação. Por isso toda
renovação reescreve o arquivo inteiro, de forma atômica, com o refresh que
voltou e uma data absoluta de validade.

A CLI não mora aqui. Este arquivo é só função pura: `token()`, `status_conta()`
e `envia()`.
"""
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Onde o token fica. O padrão é o diretório que a imagem cria e possui; fora
# dela, e nos testes, a variável manda. Lida no import, mas todas as funções
# leem o global — trocar `warden_tiktok.TOKEN_FILE` funciona.
TOKEN_FILE = os.environ.get("WARDEN_TIKTOK_TOKEN_FILE",
                            "/var/lib/hermes/warden/tiktok.json")

URL_INIT = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
URL_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
URL_OAUTH = "https://open.tiktokapis.com/v2/oauth/token/"

# A renovação do token, e por que ela é automática aqui.
#
# developers.tiktok.com/doc/oauth-user-access-token-management, citado palavra
# por palavra: o access_token "is valid for 24 hours after initial issuance", o
# refresh_token "is valid for 365 days after the initial issuance", e — o que
# decide o desenho deste módulo — "Although the fetched access_token expires
# within 24 hours, it can be refreshed without user consent. The developer's
# back-end server can schedule background jobs to keep tokens up to date."
#
# Sem isso, um agente que roda sozinho pedia a autorização de novo todo dia, e
# um clipe pronto às 3h da manhã morria esperando alguém acordar para clicar.
VIDA_ACCESS = 86400        # "valid for 24 hours"  (expires_in)
VIDA_REFRESH = 31536000    # "valid for 365 days"  (refresh_expires_in)

# Renova quando falta isto ou menos. Cinco minutos porque o envio que vem logo
# depois tem um PUT de arquivo no meio: um token que vencia em trinta segundos
# passava no teste e morria no upload.
MARGEM_RENOVACAO = 300

# Quando começar a avisar que o refresh de 365 dias está acabando. Um prazo
# anual é exatamente o tipo de coisa que vence em silêncio e derruba o agente
# num dia em que ninguém mudou nada.
AVISO_REFRESH_DIAS = 30

# O que o arquivo precisa ter para a renovação acontecer sem a pessoa.
CAMPOS_RENOVACAO = ("refresh_token", "client_key", "client_secret")

# O escopo que foi medido funcionando. NÃO é `video.publish`: aquele é o Direct
# Post, é ele que carrega a trava de `SELF_ONLY` para cliente não auditado, e
# pedi-lo a mais só aumenta a superfície da autorização sem servir para nada
# aqui.
ESCOPO = "video.upload"

TIMEOUT_API = 30          # init e status: pedidos de alguns KB
TIMEOUT_UPLOAD = 600      # o PUT do arquivo, num link doméstico

# De quanto em quanto tempo perguntar o status. Seis segundos porque foi o
# intervalo da medição (PUT 16:50:51, primeiro status 16:50:57) e porque o teto
# de requisições do endpoint de status não é conhecido: o limite medido, 6 por
# minuto por token, é do init. Devagar é barato; levar um `rate_limit_exceeded`
# no meio de um envio que já subiu o arquivo não é.
INTERVALO_STATUS = 6

# Limite MEDIDO: 6 requisições por minuto por access token no init.
TETO_INIT = 6
JANELA_INIT = 60.0

# O arquivo inteiro num chunk só foi o que se mediu (8.933.576 bytes -> 201). O
# maior chunk que o TikTok documenta é 64 MB, então acima disso este módulo
# recusa em vez de montar um envio em várias partes que ninguém aqui nunca viu
# funcionar. Recusar em voz alta é honesto; inventar o multipart não é.
TETO_CHUNK = 64 * 1024 * 1024

# O TikTok documenta MP4, WebM e MOV para este endpoint. O que foi medido é mp4.
TIPOS = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}

ONDE_CHEGA = (
    "O rascunho chega na CAIXA DE ENTRADA (a notificação) do app TikTok, NÃO na "
    "aba Rascunhos do perfil. Abra a notificação: o vídeo já está lá, com o "
    "fluxo normal de postagem.")

AVISO_LEGENDA = (
    "A legenda e as hashtags NÃO sobem com o vídeo: o corpo deste endpoint só "
    "tem source, video_size, chunk_size e total_chunk_count, e não existe campo "
    "de texto. Você cola a legenda no app, na hora de publicar.")

# Uma frase por código de erro, dizendo o que a PESSOA faz a respeito. O texto
# do TikTok vai junto, verbatim, sempre: o diagnóstico é dele, não nosso.
RECEITAS = {
    "access_token_invalid": (
        "O access token não vale mais — ele dura 24h. Com `refresh_token`, "
        "`client_key` e `client_secret` em {arquivo} o agente renova sozinho e "
        "este erro não chega até aqui; sem eles, refaça a autorização do app e "
        "regrave o arquivo."),
    "scope_not_authorized": (
        "A autorização que gerou este token não concedeu `video.upload`. Refaça "
        "a autorização marcando `video.upload` — e NÃO `video.publish`, que é o "
        "Direct Post e é ele que trava vídeo de cliente não auditado em "
        "privado."),
    "rate_limit_exceeded": (
        "Batemos no limite de requisições do TikTok (medido: 6 por minuto por "
        "access token no init). Espere um minuto e mande de novo; o arquivo não "
        "foi perdido."),
    "spam_risk_too_many_posts": (
        "O TikTok considera que esta conta mandou rascunhos demais em pouco "
        "tempo. Espere e mande mais tarde. Existe um teto documentado de 5 "
        "rascunhos pendentes em 24h que nunca foi medido aqui — publique ou "
        "descarte os que estão na caixa de entrada antes de tentar outra vez."),
    "url_ownership_unverified": (
        "Este erro é do modo PULL_FROM_URL, e este módulo não usa esse modo: ele "
        "manda os bytes do arquivo. Se ele apareceu, alguém está enviando um "
        "`video_url` de um domínio não verificado no portal do desenvolvedor."),
}

# O endpoint de OAuth não usa o mesmo envelope de erro dos outros: ele devolve
# `error` como STRING no topo, com `error_description` ao lado. Então tem tabela
# própria. A regra é a mesma: o código e a descrição dele vão verbatim, a frase
# de receita vem depois, e código desconhecido não ganha diagnóstico inventado.
RECEITAS_OAUTH = {
    "invalid_grant": (
        "O refresh_token não vale mais. Ele dura 365 dias a partir da emissão, "
        "e morre antes disso se a pessoa revogar o app nas configurações do "
        "TikTok. Não há o que renovar: refaça a autorização do zero, com o "
        "escopo `{escopo}`, e regrave {arquivo}."),
    "invalid_client": (
        "O `client_key` ou o `client_secret` gravados em {arquivo} não são os "
        "deste app. Confira os dois no portal do desenvolvedor do TikTok e "
        "regrave o arquivo — a autorização da pessoa não tem nada a ver com "
        "isso e não precisa ser refeita."),
    "invalid_request": (
        "O TikTok não entendeu o pedido de renovação. Confira se `client_key`, "
        "`client_secret` e `refresh_token` em {arquivo} estão completos e sem "
        "espaço sobrando."),
    "access_denied": (
        "O TikTok negou a renovação deste app. Confira no portal do "
        "desenvolvedor se o app continua ativo e se o escopo `{escopo}` "
        "continua concedido."),
}

# Tokens vistos neste processo, para nunca vazarem numa mensagem. São TRÊS
# segredos, não um: o access_token, o refresh_token (que vale 365 dias, e por
# isso vaza pior) e o client_secret. Sim, é estado de módulo; eles já estão na
# memória do processo de qualquer jeito, e o ganho é que toda exceção daqui
# passa por `_limpa` antes de existir.
_SEGREDOS = set()

# Carimbos dos inits deste processo, para respeitar o teto de 6 por minuto.
_INITS = []


# ───────────────────────────────────────────────────────── costuras testáveis

def _relogio():
    """Hora de parede, para validade de token. Separada para o teste mandar nela."""
    return time.time()


def _agora():
    """Relógio monotônico, para prazos. Nunca anda para trás com ajuste de NTP."""
    return time.monotonic()


def _dorme(segundos):
    """O único sleep do módulo, para o teste não esperar de verdade."""
    time.sleep(segundos)


def _http(metodo, url, *, corpo=None, cabecalhos=None, timeout=TIMEOUT_API):
    """A ÚNICA porta para a rede. -> (código HTTP, corpo em bytes).

    Um erro HTTP não é exceção aqui: o TikTok manda o JSON com `error.code` e
    `error.message` dentro de um 4xx, e esse JSON é exatamente a informação que
    a pessoa precisa ler. Jogar fora o corpo para levantar um `HTTPError` seco
    é como se perde a causa e se começa a inventar uma.

    Toda a suíte troca esta função. Nada mais aqui abre socket.
    """
    pedido = urllib.request.Request(url, data=corpo, method=metodo,
                                    headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return resposta.getcode(), resposta.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise TikTokIndisponivel(
            f"não deu para falar com {url}: {exc.reason}. Isto é rede desta "
            "máquina, não recusa do TikTok — confira a conexão e mande de novo.")
    except OSError as exc:
        raise TikTokIndisponivel(
            f"não deu para falar com {url}: {type(exc).__name__}: {exc}")


# ──────────────────────────────────────────────────────────────── o segredo

def _limpa(texto):
    """Apaga de um texto qualquer token que este processo já leu.

    Cinto e suspensório. As mensagens daqui são montadas para não conter o
    token, mas uma delas repassa o corpo de resposta de um servidor, e um dia
    algum servidor vai ecoar o que recebeu.
    """
    texto = str(texto)
    for segredo in _SEGREDOS:
        if segredo and len(segredo) >= 8:
            texto = texto.replace(segredo, "<token oculto>")
    return texto


class TikTokIndisponivel(RuntimeError):
    """Sem token, token expirado, escopo errado, ou o TikTok recusou.

    A mensagem passa por `_limpa` na construção, e não no ponto de uso. É a
    única garantia que não depende de quem escreveu a próxima linha de código
    lembrar da regra.

    `codigo` é o `error.code` que o TikTok mandou, quando houve um. Existe para
    o código decidir sem reler a frase: `access_token_invalid` é a única recusa
    que vale tentar de novo depois de renovar, e ler isso de dentro de uma
    mensagem em português seria frágil e feio.
    """

    def __init__(self, mensagem, codigo=None):
        super().__init__(_limpa(mensagem))
        self.codigo = codigo


def _modo_frouxo(caminho):
    """Aviso em uma linha se o arquivo do token for legível por outros, ou None.

    Não recusa. Um token com modo 0644 continua funcionando, e travar o envio
    por causa disso trocaria um problema de segurança por um clipe não entregue;
    o que resolve é a pessoa saber, e o aviso sobe junto com o resultado.
    """
    try:
        modo = os.stat(caminho).st_mode
    except OSError:
        return None
    frouxo = stat.S_IMODE(modo) & 0o077
    if not frouxo:
        return None
    return (f"{caminho} está com modo {stat.S_IMODE(modo):04o}: qualquer conta "
            f"nesta máquina lê o seu token. Corrija com `chmod 600 {caminho}`.")


def _le_arquivo():
    """O JSON do token, já com o segredo registrado para nunca vazar.

    Levanta TikTokIndisponivel com instrução em cada jeito de o arquivo estar
    errado, porque "sem token" e "token corrompido" mandam a pessoa fazer coisas
    diferentes e um erro genérico faz ela tentar as duas.
    """
    caminho = TOKEN_FILE
    if not os.path.exists(caminho):
        raise TikTokIndisponivel(
            f"não há token do TikTok em {caminho}. Autorize o app com o escopo "
            f"`{ESCOPO}` e grave a resposta ali, com `chmod 600`. Sem isso não "
            "há envio para a caixa de entrada.")
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise TikTokIndisponivel(
            f"{caminho} existe mas não deu para ler como JSON "
            f"({type(exc).__name__}). Regrave ali a resposta inteira da "
            f"autorização, que é um objeto JSON com `access_token`.")
    if not isinstance(dados, dict):
        raise TikTokIndisponivel(
            f"{caminho} não é um objeto JSON. O conteúdo esperado é a resposta "
            "da autorização, com a chave `access_token`.")
    bruto = dados.get("access_token")
    if not isinstance(bruto, str) or not bruto.strip():
        raise TikTokIndisponivel(
            f"{caminho} não tem a chave `access_token`. Grave ali a resposta "
            "inteira da autorização do TikTok, não só um pedaço dela.")
    _guarda_segredos(dados)
    return dados


def _guarda_segredos(dados):
    """Registra os TRÊS segredos do arquivo para `_limpa` nunca deixá-los sair.

    O refresh_token é o pior dos três: ele vale 365 dias e renova o access_token
    sem pedir nada a ninguém, então vazá-lo é entregar a conta por um ano.
    """
    for chave in ("access_token", "refresh_token", "client_secret"):
        valor = dados.get(chave)
        if isinstance(valor, str) and valor.strip():
            _SEGREDOS.add(valor.strip())


def _instante(valor):
    """Um ponto no tempo em segundos unix, a partir de número OU de ISO-8601.

    Número porque era o que o arquivo aceitava antes deste módulo escrever nele;
    ISO-8601 porque é o que ele escreve agora, e um carimbo que um humano lê é
    um carimbo que um humano confere. `None` para qualquer outra coisa — a
    ausência de data é informação, e fingir uma seria o começo do chute.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str) and valor.strip():
        texto = valor.strip()
        if texto.endswith(("Z", "z")):     # fromisoformat só aceita Z no 3.11+
            texto = texto[:-1] + "+00:00"
        try:
            quando = datetime.fromisoformat(texto)
        except ValueError:
            return None
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        return quando.timestamp()
    return None


def _iso(unix):
    """O carimbo que vai para o arquivo. UTC, com fuso explícito, sem microsegundo."""
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="seconds")


def _prazo(dados, absoluto, duracao):
    """Segundos que faltam para `absoluto` vencer, ou None quando não dá saber.

    `expires_in` e `refresh_expires_in` são DURAÇÕES, não datas: sozinhas não
    dizem nada, porque ninguém sabe quando o arquivo foi escrito. Então só se
    calcula com âncora — a data absoluta, ou um instante de emissão ao lado da
    duração. Sem âncora, None: não sabemos, e não sabemos é diferente de está
    bom. É por isso que toda renovação daqui grava a data absoluta.
    """
    fim = _instante(dados.get(absoluto))
    if fim is None:
        emitido = _instante(dados.get("obtained_at", dados.get("obtido_em")))
        dura = dados.get(duracao)
        if emitido is not None and isinstance(dura, (int, float)) \
                and not isinstance(dura, bool):
            fim = emitido + dura
        else:
            return None
    return fim - _relogio()


def _validade(dados):
    """(expirou?, segundos que faltam) do access_token. None quando não dá saber."""
    restante = _prazo(dados, "expires_at", "expires_in")
    if restante is None:
        return False, None
    return restante <= 0, restante


def _escopo_faltando(dados):
    """True só quando o arquivo AFIRMA um escopo e `video.upload` não está nele.

    Arquivo sem a chave `scope` não é arquivo sem escopo: é arquivo que não
    contou. Chutar que está errado manda a pessoa refazer uma autorização que
    talvez esteja perfeita.
    """
    escopos = dados.get("scope")
    if not isinstance(escopos, str) or not escopos.strip():
        return False
    partes = [p.strip() for p in escopos.replace(" ", ",").split(",")]
    return ESCOPO not in partes


# ────────────────────────────────────────────────── renovar sem pedir licença

def _falta_para_renovar(dados):
    """Os campos que faltam para a renovação automática, na ordem do arquivo.

    Lista, e não booleano, porque "não dá para renovar" manda a pessoa procurar
    o quê. Dizer exatamente qual chave falta é a diferença entre um `chmod` de
    dez segundos e uma tarde relendo documentação.
    """
    return [c for c in CAMPOS_RENOVACAO
            if not isinstance(dados.get(c), str) or not dados[c].strip()]


def _pode_renovar(dados):
    """Tem os três campos E o refresh ainda não venceu (ou não se sabe)."""
    if _falta_para_renovar(dados):
        return False
    sobra = _prazo(dados, "refresh_expires_at", "refresh_expires_in")
    return sobra is None or sobra > 0


def _grava(dados):
    """Reescreve o arquivo do token inteiro, atômico e com modo 600.

    Atômico porque o agente pode morrer no meio — container reiniciado, máquina
    desligada — e um arquivo de token pela metade é uma pessoa sem conta até ela
    descobrir por quê. Escreve num temporário do MESMO diretório (os.replace só
    é atômico dentro de um sistema de arquivos), força para o disco e troca.

    O modo 600 nasce com o arquivo, via O_CREAT com 0o600: criar frouxo e
    apertar depois deixa uma janela em que qualquer conta da máquina lê o
    refresh_token, que vale um ano.
    """
    destino = TOKEN_FILE
    pasta = os.path.dirname(destino) or "."
    tmp = os.path.join(pasta, f".{os.path.basename(destino)}.{os.getpid()}.novo")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, destino)
    except BaseException:
        # Qualquer saída que não seja o replace bem-sucedido não pode deixar um
        # arquivo pela metade no diretório do token, nem com nome de rascunho.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return destino


def _grava_ou_explica(dados):
    """`_grava`, com a frase que explica o pior erro possível deste módulo."""
    try:
        _grava(dados)
    except OSError as exc:
        # Este é o pior erro do módulo e merece a frase mais longa: o TikTok já
        # rotacionou o refresh_token, o antigo pode já não valer, e o novo está
        # só na memória deste processo. Dizer isso é o que evita a pessoa passar
        # uma hora achando que o problema é outro.
        raise TikTokIndisponivel(
            f"o token foi renovado no TikTok mas NÃO deu para gravar {TOKEN_FILE} "
            f"({type(exc).__name__}: {exc}). O refresh_token pode ter sido "
            "rotacionado do outro lado, então o que está no disco talvez já não "
            "sirva. Conserte a permissão do diretório e, se o próximo envio "
            "falhar com `invalid_grant`, refaça a autorização.")


def _resposta_oauth(codigo, bruto):
    """O corpo do /v2/oauth/token/, ou TikTokIndisponivel com a palavra do TikTok.

    Este endpoint NÃO usa o envelope `{"data": ..., "error": {"code": ...}}` dos
    outros: ele devolve `error` como string no topo, com `error_description` ao
    lado. Os dois formatos são aceitos aqui porque um dia isso muda e uma recusa
    lida como sucesso seria o pior dos resultados.
    """
    cru, texto = _cru(bruto), _texto(bruto)
    try:
        dados = json.loads(cru) if cru.strip() else {}
    except ValueError:
        raise TikTokIndisponivel(
            f"não deu para renovar o token: o TikTok respondeu HTTP {codigo} "
            f"com algo que não é JSON: {texto[:400] or '(corpo vazio)'}")
    if not isinstance(dados, dict):
        raise TikTokIndisponivel(
            f"não deu para renovar o token: resposta HTTP {codigo} inesperada: "
            f"{texto[:400]}")
    erro = dados.get("error")
    if isinstance(erro, dict):
        nome = (erro.get("code") or "").strip()
        descricao = erro.get("message") or ""
        log_id = erro.get("log_id") or erro.get("logid")
    else:
        nome = (erro or "").strip() if isinstance(erro, str) else ""
        descricao = dados.get("error_description") or ""
        log_id = dados.get("log_id") or dados.get("logid")
    if nome and nome != "ok":
        linha = (f"o TikTok recusou renovar o token. Ele disse, palavra por "
                 f"palavra: `{nome}`: {descricao or '(sem descrição)'}")
        if log_id:
            linha += f" (log_id {log_id})"
        receita = RECEITAS_OAUTH.get(nome)
        if receita:
            linha += "\n" + receita.format(arquivo=TOKEN_FILE, escopo=ESCOPO)
        else:
            linha += ("\nNão há receita conhecida para este código aqui. O que "
                      "está acima é a palavra do TikTok, não um diagnóstico "
                      "nosso.")
        raise TikTokIndisponivel(linha, codigo=nome)
    if not nome and not 200 <= codigo < 300:
        raise TikTokIndisponivel(
            f"não deu para renovar o token: HTTP {codigo}, e a resposta não "
            f"trouxe `error`: {texto[:400] or '(corpo vazio)'}")
    return dados


def _renova(dados, progresso=None):
    """Troca o refresh_token por um access_token novo e REESCREVE o arquivo.

    A documentação é explícita em que isto não precisa da pessoa: "it can be
    refreshed without user consent. The developer's back-end server can schedule
    background jobs to keep tokens up to date." É o que torna um agente que
    corta às 3h da manhã possível.

    A armadilha, também da documentação e também literal: "The returned
    refresh_token may be different than the one passed in the payload. You must
    use the newly-returned token if the value is different than the previous
    one." Quem ignora isso funciona por semanas e quebra no dia da rotação, com
    um `invalid_grant` que não explica nada. Por isso o arquivo é reescrito
    INTEIRO, com o refresh que voltou.
    """
    faltando = _falta_para_renovar(dados)
    if faltando:
        raise TikTokIndisponivel(
            f"o access token do TikTok venceu (ele dura 24h) e não dá para "
            f"renovar sozinho: falta "
            f"{', '.join('`' + c + '`' for c in faltando)} em {TOKEN_FILE}. "
            "A renovação não precisa da pessoa — a documentação do TikTok diz "
            "que o token 'can be refreshed without user consent' —, mas precisa "
            "desses campos. Grave-os (o client_key e o client_secret estão no "
            "portal do desenvolvedor) e isto passa a se resolver sozinho.")
    sobra = _prazo(dados, "refresh_expires_at", "refresh_expires_in")
    if sobra is not None and sobra <= 0:
        raise TikTokIndisponivel(
            f"o refresh_token em {TOKEN_FILE} também venceu — ele dura 365 dias "
            "a partir da emissão. Não há o que renovar sem a pessoa: refaça a "
            f"autorização do app com o escopo `{ESCOPO}` e regrave o arquivo.",
            codigo="refresh_expired")

    _fala(progresso, "o access token venceu ou está perto; renovando sozinho "
                     "(a documentação do TikTok permite, sem a pessoa)")
    corpo = urllib.parse.urlencode({
        "client_key": dados["client_key"].strip(),
        "client_secret": dados["client_secret"].strip(),
        "grant_type": "refresh_token",
        "refresh_token": dados["refresh_token"].strip(),
    }).encode("utf-8")
    codigo, bruto = _http(
        "POST", URL_OAUTH, corpo=corpo,
        cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT_API)
    novo = _resposta_oauth(codigo, bruto)

    acesso = novo.get("access_token")
    if not isinstance(acesso, str) or not acesso.strip():
        raise TikTokIndisponivel(
            "o TikTok respondeu a renovação sem erro e sem `access_token`. "
            f"Resposta: {sorted(novo)!r}")

    agora = _relogio()
    atualizado = dict(dados)
    atualizado["access_token"] = acesso.strip()
    dura = novo.get("expires_in")
    atualizado["expires_at"] = _iso(agora + (
        dura if isinstance(dura, (int, float)) and not isinstance(dura, bool)
        else VIDA_ACCESS))
    # A ROTAÇÃO. Se voltou refresh_token, ele é o que vale, mesmo quando é igual
    # ao que foi enviado; guardar o antigo é o defeito que a documentação avisa.
    girou = novo.get("refresh_token")
    if isinstance(girou, str) and girou.strip():
        atualizado["refresh_token"] = girou.strip()
    dura_refresh = novo.get("refresh_expires_in")
    if isinstance(dura_refresh, (int, float)) and not isinstance(dura_refresh, bool):
        atualizado["refresh_expires_at"] = _iso(agora + dura_refresh)
    for herdado in ("scope", "open_id", "token_type"):
        if isinstance(novo.get(herdado), str) and novo[herdado].strip():
            atualizado[herdado] = novo[herdado].strip()
    atualizado["renovado_em"] = _iso(agora)
    # As durações relativas saem do arquivo. Elas ficariam ancoradas numa
    # emissão que já passou, e é exatamente essa ambiguidade que faz um token
    # parecer válido depois de vencido.
    for relativo in ("expires_in", "refresh_expires_in", "obtained_at",
                     "obtido_em"):
        atualizado.pop(relativo, None)

    _guarda_segredos(atualizado)
    _grava_ou_explica(atualizado)
    _fala(progresso, f"token renovado; vence em {atualizado['expires_at']}")
    return atualizado


# ────────────────────────────────────────────────────────── as três funções

def token(*, progresso=None):
    """O access_token pronto para usar, renovando-o sozinho quando precisa.

    Devolve a string crua. Quem chama nunca a imprime, nunca a loga e nunca a
    põe numa mensagem: toda exceção deste módulo já passa por `_limpa`, e o
    teste `test_o_token_nunca_aparece_em_mensagem_de_excecao` existe para isso
    não decair.

    Renova quando o access_token venceu ou vence nos próximos
    MARGEM_RENOVACAO segundos, E o arquivo tem com que renovar. Se não tiver, a
    recusa nomeia exatamente qual campo falta. Se o arquivo não disser quando
    vence, não há renovação preventiva — sem âncora não se sabe, e `envia` tem
    a segunda linha de defesa: renovar depois de um `access_token_invalid`.
    """
    dados = _le_arquivo()
    if _escopo_faltando(dados):
        raise TikTokIndisponivel(
            f"o token em {TOKEN_FILE} foi emitido para o escopo "
            f"`{dados.get('scope')}`, que não inclui `{ESCOPO}`. Refaça a "
            f"autorização marcando `{ESCOPO}` — e não `video.publish`, que é "
            "outro fluxo e é o que trava vídeo de cliente não auditado.")
    _, restante = _validade(dados)
    if restante is not None and restante <= MARGEM_RENOVACAO:
        dados = _renova(dados, progresso)
    return dados["access_token"].strip()


def status_conta():
    """(ok, motivo) para o `warden status`. NUNCA levanta, nem com lixo no disco.

    Não toca a rede, de propósito — nem para renovar. `warden status` roda a
    qualquer hora, e uma renovação de cortesia gastaria um token de refresh e
    uma das 6 requisições por minuto que o envio de verdade pode precisar. Um
    token que vence em dez minutos e TEM com que se renovar não é um problema da
    pessoa, e esta função diz isso com todas as letras em vez de pintar de
    vermelho algo que se resolve sozinho no próximo envio.

    Os estados que ela distingue, porque cada um manda fazer coisa diferente:
    válido; vencendo mas com renovação automática armada; sem com que renovar, e
    aí dizendo qual campo falta; e refresh de 365 dias vencido, que é a única
    situação que exige a pessoa sentar e refazer o OAuth.
    """
    try:
        dados = _le_arquivo()
    except TikTokIndisponivel as exc:
        return False, str(exc)
    except Exception as exc:                                  # noqa: BLE001
        # Um `status` que explode some com TODO o relatório, inclusive as linhas
        # que não têm nada a ver com TikTok. Nenhum defeito aqui vale isso.
        return False, _limpa(f"não deu para ler {TOKEN_FILE}: "
                             f"{type(exc).__name__}: {exc}")
    try:
        if _escopo_faltando(dados):
            return False, (f"o token foi emitido para `{dados.get('scope')}`, "
                           f"sem `{ESCOPO}`: refaça a autorização.")
        faltando = _falta_para_renovar(dados)
        sobra_refresh = _prazo(dados, "refresh_expires_at", "refresh_expires_in")
        expirou, falta = _validade(dados)

        if sobra_refresh is not None and sobra_refresh <= 0:
            return False, ("o refresh_token venceu (ele dura 365 dias): a "
                           f"renovação automática acabou. Refaça a autorização "
                           f"do app com o escopo `{ESCOPO}` e regrave "
                           f"{TOKEN_FILE}.")
        if (expirou or (falta is not None and falta <= MARGEM_RENOVACAO)) \
                and faltando:
            return False, (
                "o access token venceu ou está vencendo e não há como renovar "
                f"sozinho: falta {', '.join('`' + c + '`' for c in faltando)} "
                f"em {TOKEN_FILE}. Com esses campos o agente renova sem a "
                "pessoa; sem eles, é uma autorização nova a cada 24h.")

        ok, recado = True, f"token presente em {TOKEN_FILE}"
        if falta is None:
            recado += ", sem data de validade no arquivo (não dá para saber)"
        elif falta <= MARGEM_RENOVACAO:
            recado += (f", vencido ou a menos de {MARGEM_RENOVACAO // 60} min de "
                       "vencer — e renova sozinho no próximo envio, sem você")
        else:
            recado += f", vence em {int(falta // 60)} min"
        if not faltando:
            recado += "; renovação automática armada"
        elif falta is None or falta > MARGEM_RENOVACAO:
            # Ainda não dói, mas vai doer em 24h. Melhor agora do que às 3h.
            recado += ("; SEM renovação automática (falta "
                       f"{', '.join('`' + c + '`' for c in faltando)}), então "
                       "ele vai vencer em até 24h e parar")
        if sobra_refresh is not None and \
                sobra_refresh < AVISO_REFRESH_DIAS * 86400:
            recado += (f"; o refresh_token vence em {int(sobra_refresh // 86400)} "
                       "dia(s) e aí é preciso refazer o OAuth")
        frouxo = _modo_frouxo(TOKEN_FILE)
        if frouxo:
            recado += " — ATENÇÃO: " + frouxo
        return ok, recado
    except Exception as exc:                                  # noqa: BLE001
        return False, _limpa(f"não deu para avaliar o token: "
                             f"{type(exc).__name__}: {exc}")


def envia(caminho, *, progresso=None, espera=180):
    """Manda um arquivo para a caixa de entrada do TikTok. -> dict.

    `progresso` é chamado com uma linha de texto a cada passo, se vier. `espera`
    é o TETO em segundos do acompanhamento do status, não uma promessa: estourou
    o teto, devolve o último status que o TikTok disse, com `concluido: False`.
    Travar o agente esperando um estado que talvez nunca venha é pior do que
    dizer onde parou — e em 14/09 o status ficou em `SEND_TO_USER_INBOX` por 43
    minutos sem nada mais acontecer.

    O dicionário devolvido:

      publish_id  o identificador do TikTok, que é o que serve para reconsultar
      status      o último `data.status` conhecido, ou None se nem deu tempo
      concluido   True só quando chegou em SEND_TO_USER_INBOX
      bytes       o tamanho que foi declarado e enviado
      segundos    do init até o último status
      onde        ONDE_CHEGA — a caixa de entrada, não a aba Rascunhos
      legenda     AVISO_LEGENDA — a legenda é colada por você, no app
      avisos      o que não impediu o envio mas você precisa saber

    Levanta TikTokIndisponivel quando não há como enviar, ou quando o TikTok
    recusou. Na recusa, o `error.code` e o `error.message` DELE vão na mensagem,
    verbatim. Nunca se inventa causa aqui.
    """
    caminho = os.path.abspath(os.path.expanduser(caminho))
    if not os.path.isfile(caminho):
        raise TikTokIndisponivel(f"não há arquivo em {caminho} para enviar.")
    tamanho = os.path.getsize(caminho)
    if tamanho <= 0:
        raise TikTokIndisponivel(
            f"{caminho} tem 0 byte. O TikTok recusaria, e a recusa dele seria "
            "menos clara que esta.")
    if tamanho > TETO_CHUNK:
        raise TikTokIndisponivel(
            f"{caminho} tem {tamanho} bytes, acima do maior chunk que o TikTok "
            f"aceita ({TETO_CHUNK} bytes). O que foi medido aqui é o arquivo "
            "inteiro num chunk só (8.933.576 bytes em 14/09/2026); o envio em "
            "várias partes nunca foi testado nesta casa e não vai ser "
            "improvisado agora. Reencode o clipe menor.")
    tipo = TIPOS.get(os.path.splitext(caminho)[1].lower())
    if not tipo:
        raise TikTokIndisponivel(
            f"{caminho} não é um formato que este endpoint aceita. O TikTok "
            f"documenta {', '.join(sorted(TIPOS))} — e o que foi medido é .mp4.")

    avisos = []
    frouxo = _modo_frouxo(TOKEN_FILE)
    if frouxo:
        avisos.append(frouxo)
        _fala(progresso, "AVISO: " + frouxo)

    tok = token(progresso=progresso)
    _fala(progresso, AVISO_LEGENDA)

    comeco = _agora()
    # Os ÚNICOS quatro campos que este endpoint aceita. Não existe título, não
    # existe legenda, não existe privacy_level — a ausência do privacy_level é
    # inclusive uma das razões de este fluxo escapar da trava de SELF_ONLY, já
    # que quem escolhe a privacidade é a pessoa dentro do app. `video_url` só
    # existe no outro modo, PULL_FROM_URL, que este módulo não usa.
    corpo = {"source_info": {"source": "FILE_UPLOAD",
                             "video_size": tamanho,
                             "chunk_size": tamanho,
                             "total_chunk_count": 1}}
    _fala(progresso, f"init: {tamanho} bytes em 1 chunk")
    try:
        dados = _init(tok, corpo, progresso)
    except TikTokIndisponivel as exc:
        # A segunda linha de defesa da renovação, e ela existe por um caso real:
        # um arquivo gravado direto da resposta do OAuth tem `expires_in`, que é
        # duração, e nenhuma âncora — então `token()` não sabe que venceu e não
        # renova sozinho. Aqui o TikTok acabou de dizer que venceu, o que é a
        # âncora que faltava. Uma tentativa só, e só para este código.
        if exc.codigo != "access_token_invalid" or not _pode_renovar(_le_arquivo()):
            raise
        _fala(progresso, "o TikTok recusou o token; renovando e tentando mais "
                         "uma vez")
        tok = _renova(_le_arquivo(), progresso)["access_token"].strip()
        dados = _init(tok, corpo, progresso)
    publish_id = dados.get("publish_id")
    upload_url = dados.get("upload_url")
    if not publish_id or not upload_url:
        raise TikTokIndisponivel(
            "o TikTok respondeu sem erro e sem publish_id/upload_url; sem eles "
            f"não há para onde mandar o arquivo. Resposta: {dados!r}")
    _fala(progresso, f"init ok: publish_id {publish_id}")

    with open(caminho, "rb") as fh:
        conteudo = fh.read()
    # O PUT vai SEM Authorization: a upload_url já vem assinada, o TikTok não
    # pede o token aqui, e um segredo que não é enviado é um segredo que não
    # vaza no log de um intermediário.
    codigo, resposta_put = _http(
        "PUT", upload_url, corpo=conteudo,
        cabecalhos={"Content-Range": f"bytes 0-{tamanho - 1}/{tamanho}",
                    "Content-Type": tipo},
        timeout=TIMEOUT_UPLOAD)
    # Medido: 201. Aceita-se a faixa 2xx inteira porque o que importa é o
    # servidor ter ficado com os bytes, e o status seguinte é que confirma.
    if not 200 <= codigo < 300:
        raise TikTokIndisponivel(
            f"o upload do arquivo voltou HTTP {codigo} (em 14/09/2026 a medição "
            f"deu 201). O servidor disse: "
            f"{_texto(resposta_put)[:400] or '(corpo vazio)'}")
    _fala(progresso, f"arquivo enviado, HTTP {codigo}")

    status, ultimo = _acompanha(tok, publish_id, espera, progresso)
    concluido = status == "SEND_TO_USER_INBOX"
    if not concluido and status is not None:
        avisos.append(
            f"o acompanhamento parou em `{status}` depois de {espera}s, que é o "
            "teto pedido, e não em SEND_TO_USER_INBOX. O envio não falhou por "
            "isso: reconsulte o publish_id mais tarde.")
    if status is None:
        avisos.append(
            "o status não chegou a ser consultado (espera=0), então o arquivo "
            "subiu e ninguém perguntou o que aconteceu com ele depois.")
    if concluido:
        _fala(progresso, "SEND_TO_USER_INBOX. " + ONDE_CHEGA)
    return {"publish_id": publish_id,
            "status": status,
            "concluido": concluido,
            "bytes": tamanho,
            "segundos": round(_agora() - comeco, 1),
            "onde": ONDE_CHEGA,
            "legenda": AVISO_LEGENDA,
            "avisos": avisos,
            "detalhe": ultimo}


# ─────────────────────────────────────────────────────────────── por dentro

def _fala(progresso, mensagem):
    """Avisa quem está olhando, sem deixar um callback quebrado perder o envio.

    Se a função de progresso da CLI explodir DEPOIS do upload, o publish_id vai
    junto e o arquivo já está no TikTok — o pior dos mundos. Então o erro do
    callback é de quem o escreveu, e não custa o resultado.
    """
    if progresso is None:
        return
    try:
        progresso(_limpa(mensagem))
    except Exception:                                         # noqa: BLE001
        pass


def _cru(bruto):
    """O corpo da resposta como texto, SEM limpeza. Só para dar de comer ao JSON.

    A limpeza de segredo é para o que um humano lê, nunca para o que o programa
    interpreta. Misturar as duas coisas já custou um defeito: o /v2/oauth/token/
    devolve o refresh_token, e quando ele volta IGUAL ao que foi enviado o
    segredo já está registrado — então o corpo chegava ao `json.loads` com
    `<token oculto>` no lugar do token, e era isso que ia para o disco. Um
    arquivo com um refresh_token apagado é uma conta perdida em silêncio.
    """
    if isinstance(bruto, bytes):
        return bruto.decode("utf-8", "replace")
    return bruto if bruto is not None else ""


def _texto(bruto):
    """O mesmo corpo, limpo, para caber numa mensagem que alguém vai ler."""
    return _limpa(_cru(bruto))


def _resposta(codigo, bruto, oque):
    """O `data` da resposta, ou TikTokIndisponivel com a palavra do TikTok.

    A regra do repo que este bloco existe para cumprir: NUNCA inventar causa. O
    que vai na mensagem é `error.code` e `error.message` como o TikTok os
    escreveu, mais o `log_id`, que é o que o suporte deles pede. A frase de
    receita vem depois, separada, e para código desconhecido não há frase
    nenhuma — porque não sabemos.
    """
    cru, texto = _cru(bruto), _texto(bruto)
    try:
        dados = json.loads(cru) if cru.strip() else {}
    except ValueError:
        raise TikTokIndisponivel(
            f"não deu para {oque}: o TikTok respondeu HTTP {codigo} com algo que "
            f"não é JSON: {texto[:400] or '(corpo vazio)'}")
    if not isinstance(dados, dict):
        raise TikTokIndisponivel(
            f"não deu para {oque}: resposta HTTP {codigo} inesperada: "
            f"{texto[:400]}")
    erro = dados.get("error") or {}
    codigo_erro = (erro.get("code") or "").strip()
    if codigo_erro and codigo_erro != "ok":
        mensagem = erro.get("message") or "(sem mensagem)"
        log_id = erro.get("log_id") or erro.get("logid")
        linha = (f"o TikTok recusou {oque}. Ele disse, palavra por palavra: "
                 f"`{codigo_erro}`: {mensagem}")
        if log_id:
            linha += f" (log_id {log_id})"
        receita = RECEITAS.get(codigo_erro)
        if receita:
            linha += "\n" + receita.format(arquivo=TOKEN_FILE)
        else:
            linha += ("\nNão há receita conhecida para este código aqui. O que "
                      "está acima é a palavra do TikTok, não um diagnóstico "
                      "nosso — procure o código na documentação da Content "
                      "Posting API.")
        raise TikTokIndisponivel(linha, codigo=codigo_erro)
    if not codigo_erro and not 200 <= codigo < 300:
        raise TikTokIndisponivel(
            f"não deu para {oque}: HTTP {codigo}, e a resposta não trouxe "
            f"`error.code`: {texto[:400] or '(corpo vazio)'}")
    dados_uteis = dados.get("data")
    return dados_uteis if isinstance(dados_uteis, dict) else {}


def _init(tok, corpo, progresso):
    """Um POST no init, já respeitando o teto de 6 por minuto. -> `data`.

    Separado de `envia` porque ele acontece duas vezes no pior caso: a segunda
    depois de renovar o token. E a segunda também gasta uma vaga do minuto — o
    limite é do TikTok, não do nosso laço, e fingir que a repetição é de graça
    seria o jeito de levar `rate_limit_exceeded` bem no fim do trabalho.
    """
    _espera_a_vez(progresso)
    codigo, bruto = _http(
        "POST", URL_INIT,
        corpo=json.dumps(corpo).encode("utf-8"),
        cabecalhos={"Authorization": f"Bearer {tok}",
                    "Content-Type": "application/json; charset=UTF-8"},
        timeout=TIMEOUT_API)
    return _resposta(codigo, bruto, "abrir o envio")


def _espera_a_vez(progresso):
    """Segura o init para não estourar o teto MEDIDO de 6 por minuto por token.

    Isto protege o agente do próprio laço — mandar cinco clipes seguidos — e de
    mais nada: outro processo usando o MESMO token não aparece nesta lista. Se o
    TikTok responder `rate_limit_exceeded` mesmo assim, a palavra dele é que
    vale, e a receita daquele código diz o que fazer.
    """
    for _ in range(2):
        agora = _agora()
        while _INITS and agora - _INITS[0] >= JANELA_INIT:
            _INITS.pop(0)
        if len(_INITS) < TETO_INIT:
            _INITS.append(agora)
            return
        falta = JANELA_INIT - (agora - _INITS[0])
        _fala(progresso, f"limite de {TETO_INIT} envios por minuto por token "
                         f"(medido): esperando {falta:.0f}s")
        _dorme(max(0.0, falta) + 0.5)
    raise TikTokIndisponivel(
        f"este processo já abriu {TETO_INIT} envios neste minuto, que é o "
        "limite medido do TikTok por access token. Espere um minuto e mande de "
        "novo — nada foi enviado.")


def _acompanha(tok, publish_id, espera, progresso):
    """(último status, último `data`) sem laço infinito e sem chute.

    `espera` é teto, e conta a partir DAQUI, não do início do envio: um arquivo
    que demorou dois minutos para subir não pode comer o prazo de perguntar o
    que aconteceu com ele. Estourou o teto, devolve o que se sabe — não existe
    estado "deve ter dado certo". Em 14/09 a transição de PROCESSING_UPLOAD para
    SEND_TO_USER_INBOX levou ~21s; o teto padrão de 180s é folga para o dia em
    que ela demorar mais, não uma medição.
    """
    ultimo, dados, inicio = None, {}, _agora()
    while True:
        # Teto é teto: a última espera encolhe para caber, e nunca se dorme um
        # segundo além do que quem chamou autorizou.
        restante = espera - (_agora() - inicio)
        if restante <= 0:
            break
        _dorme(min(INTERVALO_STATUS, restante))
        codigo, bruto = _http(
            "POST", URL_STATUS,
            corpo=json.dumps({"publish_id": publish_id}).encode("utf-8"),
            cabecalhos={"Authorization": f"Bearer {tok}",
                        "Content-Type": "application/json; charset=UTF-8"},
            timeout=TIMEOUT_API)
        dados = _resposta(codigo, bruto, "consultar o status do envio")
        ultimo = dados.get("status") or ultimo
        _fala(progresso, f"status: {ultimo}")
        if ultimo == "FAILED":
            # `fail_reason` é a palavra do TikTok sobre o próprio fracasso. É a
            # única coisa que se sabe, então é a única coisa que se diz.
            raise TikTokIndisponivel(
                f"o TikTok marcou o envio {publish_id} como FAILED. Motivo que "
                f"ELE deu: `{dados.get('fail_reason') or '(nenhum)'}`.")
        if ultimo in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return ultimo, dados
    return ultimo, dados
