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

A CLI não mora aqui. Este arquivo é só função pura: `token()`, `status_conta()`
e `envia()`.
"""
import json
import os
import stat
import time
import urllib.error
import urllib.request

# Onde o token fica. O padrão é o diretório que a imagem cria e possui; fora
# dela, e nos testes, a variável manda. Lida no import, mas todas as funções
# leem o global — trocar `warden_tiktok.TOKEN_FILE` funciona.
TOKEN_FILE = os.environ.get("WARDEN_TIKTOK_TOKEN_FILE",
                            "/var/lib/hermes/warden/tiktok.json")

URL_INIT = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
URL_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

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
        "O access token não vale mais. Ele dura ~24h; refaça a autorização do "
        "app e regrave {arquivo} com o access_token novo."),
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

# Tokens vistos neste processo, para nunca vazarem numa mensagem. Sim, é estado
# de módulo; eles já estão na memória do processo de qualquer jeito, e o ganho é
# que toda exceção daqui passa por `_limpa` antes de existir.
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
    """

    def __init__(self, mensagem):
        super().__init__(_limpa(mensagem))


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
    _SEGREDOS.add(bruto.strip())
    return dados


def _validade(dados):
    """(expirou?, segundos que faltam) a partir do que o arquivo realmente diz.

    O TikTok devolve `expires_in`, que é uma duração e não uma data: sozinha ela
    não diz nada, porque ninguém sabe quando o arquivo foi escrito. Então só se
    calcula validade quando há uma âncora no tempo — `expires_at` absoluto, ou
    um instante de emissão ao lado do `expires_in`. Sem âncora, (False, None):
    não sabemos, e não sabemos é diferente de está bom.
    """
    fim = dados.get("expires_at")
    if not isinstance(fim, (int, float)):
        emitido = dados.get("obtained_at", dados.get("obtido_em"))
        dura = dados.get("expires_in")
        if isinstance(emitido, (int, float)) and isinstance(dura, (int, float)):
            fim = emitido + dura
        else:
            return False, None
    return fim <= _relogio(), fim - _relogio()


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


# ────────────────────────────────────────────────────────── as três funções

def token():
    """O access_token do arquivo, ou TikTokIndisponivel dizendo o que fazer.

    Devolve a string crua. Quem chama nunca a imprime, nunca a loga e nunca a
    põe numa mensagem: toda exceção deste módulo já passa por `_limpa`, e o
    teste `test_token_nunca_aparece_em_mensagem` existe para isso não decair.
    """
    dados = _le_arquivo()
    if _escopo_faltando(dados):
        raise TikTokIndisponivel(
            f"o token em {TOKEN_FILE} foi emitido para o escopo "
            f"`{dados.get('scope')}`, que não inclui `{ESCOPO}`. Refaça a "
            f"autorização marcando `{ESCOPO}` — e não `video.publish`, que é "
            "outro fluxo e é o que trava vídeo de cliente não auditado.")
    expirou, _ = _validade(dados)
    if expirou:
        raise TikTokIndisponivel(
            f"o token em {TOKEN_FILE} está vencido (o access token do TikTok "
            "dura ~24h). Refaça a autorização e regrave o arquivo.")
    return dados["access_token"].strip()


def status_conta():
    """(ok, motivo) para o `warden status`. NUNCA levanta, nem com lixo no disco.

    Não toca a rede, de propósito. `warden status` roda a qualquer hora, e uma
    chamada de cortesia consumiria uma das 6 requisições por minuto que o envio
    de verdade pode precisar. O que dá para responder offline — existe token,
    dá para ler, o escopo bate, a validade venceu, o modo do arquivo protege —
    é o que muda o que a pessoa faz em seguida.
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
        expirou, falta = _validade(dados)
        if expirou:
            return False, ("o token está vencido (dura ~24h): refaça a "
                           "autorização e regrave o arquivo.")
        recado = f"token presente em {TOKEN_FILE}"
        if falta is not None:
            recado += f", vence em {int(falta // 60)} min"
        else:
            recado += ", sem data de validade no arquivo (não dá para saber)"
        frouxo = _modo_frouxo(TOKEN_FILE)
        if frouxo:
            return True, recado + " — ATENÇÃO: " + frouxo
        return True, recado
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

    tok = token()
    _fala(progresso, AVISO_LEGENDA)

    comeco = _agora()
    _espera_a_vez(progresso)
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
    codigo, bruto = _http(
        "POST", URL_INIT,
        corpo=json.dumps(corpo).encode("utf-8"),
        cabecalhos={"Authorization": f"Bearer {tok}",
                    "Content-Type": "application/json; charset=UTF-8"},
        timeout=TIMEOUT_API)
    dados = _resposta(codigo, bruto, "abrir o envio")
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


def _texto(bruto):
    if isinstance(bruto, bytes):
        return _limpa(bruto.decode("utf-8", "replace"))
    return _limpa(bruto if bruto is not None else "")


def _resposta(codigo, bruto, oque):
    """O `data` da resposta, ou TikTokIndisponivel com a palavra do TikTok.

    A regra do repo que este bloco existe para cumprir: NUNCA inventar causa. O
    que vai na mensagem é `error.code` e `error.message` como o TikTok os
    escreveu, mais o `log_id`, que é o que o suporte deles pede. A frase de
    receita vem depois, separada, e para código desconhecido não há frase
    nenhuma — porque não sabemos.
    """
    texto = _texto(bruto)
    try:
        dados = json.loads(texto) if texto.strip() else {}
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
        raise TikTokIndisponivel(linha)
    if not codigo_erro and not 200 <= codigo < 300:
        raise TikTokIndisponivel(
            f"não deu para {oque}: HTTP {codigo}, e a resposta não trouxe "
            f"`error.code`: {texto[:400] or '(corpo vazio)'}")
    dados_uteis = dados.get("data")
    return dados_uteis if isinstance(dados_uteis, dict) else {}


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
