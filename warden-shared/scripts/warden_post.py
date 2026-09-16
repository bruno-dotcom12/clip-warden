#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publicar pelo INTERMEDIÁRIO, porque o caminho direto do YouTube é uma trava.

Por que este módulo existe
──────────────────────────
Vídeo enviado à YouTube Data API por um projeto Google que NÃO passou pela
auditoria de verificação fica "locked as private"
(support.google.com/youtube/answer/7300965): não é só "sobe privado e depois eu
abro" — o dono NÃO consegue torná-lo público, e não existe recurso. O
`warden youtube publish` deste projeto cai exatamente nessa trava. Ele sobe o
arquivo, devolve um `videoId` e um 200, e nada disso é publicação: é um arquivo
trancado numa conta.

O caminho que funciona sem esperar auditoria é mandar por um app que JÁ foi
auditado. É o que este módulo faz. O intermediário escolhido é o Upload-Post.

O que foi MEDIDO em 15/09/2026, contra a API real, e o que não foi
──────────────────────────────────────────────────────────────────
MEDIDO (conferido ao vivo, com a chave do dono):

  GET /uploadposts/me     com `Authorization: Apikey <chave>`  -> HTTP 200
    {"success":true,"message":"Token is valid","email":"...","plan":"Default"}

  GET /uploadposts/users                                        -> HTTP 200
    {"success":true,"limit":2,"plan":"default","profiles":[
       {"username":"Clip-Warden","social_accounts":{
          "tiktok":"",
          "youtube":{"display_name":"Pod Cortes","handle":"@poddclipes",
                     "reauth_required":false}}}]}

  A spec OpenAPI oficial (docs.upload-post.com/openapi.json) define o schema
  `YouTubePrivacyStatus` como {"enum":["public","unlisted","private"],
  "default":"public"} — é de lá, e não de chute, que sai PRIVACIDADES.

MEDIDO no primeiro envio REAL, 15/09/2026 às 15:32 BRT, produção, ponta a
ponta — o clipe saiu PÚBLICO no canal e a corrente fechou:

  POST /upload   user=Clip-Warden, platform[]=youtube, privacyStatus=public,
                 async_upload=true, arquivo de 14.249.812 bytes  -> HTTP 200
    {"success":true,"message":"Upload queued successfully in the durable
     worker.","request_id":"125924f68c5f42269bb081180df40d86",
     "job_id":"57a5caa0d9804e60ac5736a3077fb592","total_platforms":1}

  GET /uploadposts/status  passou por
    {"status":"processing","results":[{"platform":"youtube",
     "status":"processing","attempts":1,"success":false}]}
  e chegou em
    {"status":"completed","completed":1,"total":1,"results":[{
      "platform":"youtube","success":true,"platform_post_id":"lx9J_hD7nGA",
      "post_url":"https://www.youtube.com/watch?v=lx9J_hD7nGA",
      "error_message":null,"media_size_bytes":14249812,"is_async":true,
      "prevalidation_metadata":{"width":1080,"height":1920,"fps":30.0,
                                "duration":15.0,"video_codec":"h264"}}]}

Quatro coisas dessa medição mudaram este código, e valem mais que a
confirmação em si:

  1. O estado intermediário chama-se `processing`, NÃO `pending`. A primeira
     versão daqui só esperava enquanto fosse `pending` — ela teria saído do
     laço na PRIMEIRA consulta e chamado de concluído um envio que estava no
     meio. Daí ESTADOS_ANDAMENTO, com os dois nomes.
  2. `results` é uma LISTA de objetos com `platform` dentro, não um
     dicionário indexado por rede.
  3. A causa da falha chama-se `error_message` (vem `null` no sucesso).
  4. A resposta de status NÃO TRAZ CAMPO DE PRIVACIDADE. Nenhum. O que volta
     é `post_url`. Então este módulo NUNCA pode afirmar, a partir do status,
     que o vídeo ficou público — e não afirma: `privacidade` sai None e um
     aviso diz que a API não confirmou.

E a prova de que ficou público veio de FORA do intermediário, dos dois lados:
`videos.list` da API do YouTube, com o token do próprio dono, devolveu
`privacyStatus: public`, `uploadStatus: processed`, canal `Pod Cortes`; e a
página aberta DESLOGADA devolveu HTTP 200 com `"isPrivate":false` e
`"status":"OK"`, sem `LOGIN_REQUIRED`.

Ou seja: a regra continua valendo, e agora com medição em vez de escrúpulo.
O `200 OK` e o `post_url` do intermediário NÃO foram a prova — eles vieram
iguais aos de qualquer envio, e quem disse "público" foi a janela anônima.
É por isso que `wait_for` fecha toda saída mandando abrir o endereço
deslogado: esse passo não é zelo, é o único lugar onde a resposta existe.

A leitura das respostas continua TOLERANTE (procura a informação em mais de um
nome de campo) porque um envio é uma amostra, e um campo que veio hoje pode
vir com outro nome amanhã. Quando nenhum nome aparece, diz-se que não apareceu
em vez de preencher com o que foi pedido.

A regra de honestidade, que neste módulo é a regra inteira
──────────────────────────────────────────────────────────
1. Relata-se o que a API DEVOLVEU, nunca o que foi pedido. Pedir
   `privacyStatus=public` e ecoar "público" é a mesma mentira que o
   `youtube publish` conta hoje, com outro sotaque.
2. `200 OK` e um `post_url` NÃO são prova de que o vídeo está público. A prova
   é abrir o endereço numa janela anônima, deslogado. Toda saída de `wait_for`
   carrega essa frase — é o mesmo portão do contact sheet: teste verde não é
   clipe visto, e HTTP 200 não é vídeo publicado.
3. A chave nunca aparece. Nem em log, nem em erro, nem numa exceção de
   biblioteca — `_limpa` passa por cima de tudo que sai daqui.
4. Sem rede ou HTTP de erro: repassa-se o código e o corpo (truncado). Não se
   inventa causa.

Configuração — do ambiente, ou do volume desta máquina
──────────────────────────────────────────────────────
  WARDEN_POST_API_KEY   a chave, pelo ambiente. É assim que o DONO DO AGENTE
                        põe a dele, e aí várias máquinas usam a mesma conta.
  <volume>/post-api-key a chave da PRÓPRIA PESSOA, escrita por
                        `warden post setkey` com permissão 0600. Existe porque
                        na nuvem da Plow não há `.env` nem `compose.yml`: o
                        ambiente traz o que a Plow põe nele e nada mais, então
                        sem este arquivo a chave da pessoa não teria por onde
                        entrar. O ambiente ganha do arquivo.
  SEM NENHUMA DAS DUAS  O MÓDULO FICA DESLIGADO: toda função pública levanta
                        PostIndisponivel e NENHUMA abre socket.
  WARDEN_POST_PROVIDER  `upload-post` (padrão). Outro nome: erro claro de não
                        implementado.
  WARDEN_POST_PROFILE   o `user` da API, nomeado à mão. Ganha de tudo.
  <volume>/post-profile o nome do perfil DESTA MÁQUINA, decidido uma vez e
                        reusado sempre. Ver `_perfil` para a ordem inteira.
  WARDEN_POST_BASE_URL  para o teste apontar para um servidor local. É o que
                        torna a suíte possível sem tocar a rede.
  WARDEN_POST_DIR       onde os dois arquivos acima ficam. Padrão:
                        /var/lib/hermes/warden, a pasta 0700 do uid 10000 que
                        a imagem cria e que sobrevive a um reinício.

A chave não vem de arquivo do repositório e não vem de código. Um segredo
commitado é um segredo vazado, e este é o segredo que publica no canal do dono.
O arquivo do volume é outra coisa: ele está na máquina de UMA pessoa, com a
chave DELA, e não viaja com a imagem.

AS TRÊS REDES, E O QUE CADA UMA TEM ATRÁS
──────────────────────────────────────────
`publicar` manda para youtube, tiktok e instagram, pelo mesmo `POST /upload`,
com `platform[]` repetido. Só o YOUTUBE foi medido com envio real. Os campos
das outras duas vêm da spec OpenAPI oficial e nada mais, e todo envio que
inclui uma delas sai com um aviso dizendo exatamente isso.

Só biblioteca padrão, como o resto do projeto. O multipart é montado à mão.
A CLI não mora aqui: este arquivo é função pura — `guardar_chave()`,
`status()`, `conectar()`, `publicar()`, `publish_youtube()` e `wait_for()`.
"""
import ipaddress
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# ─────────────────────────────────────────────────────────────── configuração

# Lidos a CADA chamada, não no import. O teste troca a variável de ambiente
# entre um caso e outro, e um valor congelado no import faria a suíte passar
# por engano ao mesmo tempo que esconderia o caso real de quem exporta a chave
# depois de o agente já estar de pé.
ENV_CHAVE = "WARDEN_POST_API_KEY"
ENV_PROVEDOR = "WARDEN_POST_PROVIDER"
ENV_PERFIL = "WARDEN_POST_PROFILE"
ENV_BASE = "WARDEN_POST_BASE_URL"
ENV_DIR = "WARDEN_POST_DIR"

BASE_PADRAO = "https://api.upload-post.com/api"
PROVEDOR_PADRAO = "upload-post"

# ── ONDE O ESTADO DESTE MÓDULO MORA, e por que não é `warden.state_dir()`
#
# É a MESMA pasta: /var/lib/hermes/warden, a que a imagem cria como 0700 do uid
# 10000 e que sobrevive a um reinício na nuvem da Plow. A resolução está
# copiada aqui em vez de importada porque `warden.py` importa ESTE módulo, e
# uma importação de volta seria um ciclo que só aparece no dia em que alguém
# mudar a ordem dos imports. A cópia tem um teste próprio que falha se as duas
# discordarem (`tests/test_post.py`, EstadoNoDisco), que é o que torna a
# duplicação segura em vez de uma dívida.
DIR_PADRAO = "/var/lib/hermes/warden"

# Os dois arquivos. Separados de propósito: um é segredo e o outro não, e um
# arquivo só significaria que todo lugar que quer ler o nome do perfil abre o
# arquivo que tem a chave dentro.
NOME_ARQ_CHAVE = "post-api-key"
NOME_ARQ_PERFIL = "post-profile"

# O nome do perfil que este agente cria quando a máquina ainda não tem um.
#
# ERA "clip-warden", IGUAL PARA TODO MUNDO, e isso era um defeito com data
# marcada. A chave do intermediário pode chegar pelo ambiente que a Plow monta
# -- caso em que é a chave do DONO DO AGENTE e várias máquinas usam a mesma --
# e com um nome de perfil fixo todas elas cairiam no mesmo perfil, que é o
# mesmo canal do YouTube. A primeira pessoa conectaria o canal dela e a segunda
# publicaria lá dentro sem que nenhuma das duas tivesse pedido isso.
#
# Então o nome passa a ter um sufixo aleatório por máquina, guardado no volume
# na primeira vez e reusado sempre. `uuid4().hex[:8]` são 32 bits: para o
# punhado de máquinas que uma conta comporta (o plano medido em 16/09/2026
# permitia 2 perfis), a colisão é teoria.
PREFIXO_PERFIL = "clip-warden-"
DIGITOS_PERFIL = 8

# As redes que este módulo sabe enviar, e os nomes são os do enum
# `VideoPlatformEnum` da spec OpenAPI oficial (docs.upload-post.com/openapi.json,
# lida em 16/09/2026). A spec lista treze; aqui estão as três que este agente
# corta clipe para, e acrescentar uma quarta é acrescentar o mapa de campos
# dela abaixo, não só o nome nesta tupla.
PLATAFORMAS = ("youtube", "tiktok", "instagram")

# SÓ O YOUTUBE FOI MEDIDO COM ENVIO REAL (15 e 16/09/2026). TikTok e Instagram
# saem daqui com os nomes de campo da spec oficial e NENHUM envio real por
# trás. Esta constante não é decoração: ela vira aviso na saída de todo envio
# que inclui uma das duas, para que ninguém leia um 200 dessas redes como se
# tivesse o mesmo lastro do YouTube.
MEDIDAS_DE_VERDADE = ("youtube",)

TIMEOUT_API = 30           # /me, /users, /status: alguns KB de JSON
TIMEOUT_UPLOAD = 600       # o POST /upload leva o arquivo junto, em link doméstico

# Quanto do corpo de uma resposta ruim entra na mensagem. O suficiente para a
# causa aparecer, pouco o bastante para um HTML de erro de 200 KB não virar a
# saída inteira do comando.
TETO_CORPO = 800

# Teto do YouTube para o título, em caracteres. Este módulo RECUSA um título
# maior em vez de cortar: truncar em silêncio publica no canal do dono um
# título que ninguém escreveu, e quem descobre é a audiência.
TETO_TITULO = 100

# Do schema `YouTubePrivacyStatus` da spec OpenAPI oficial, lido em 15/09/2026.
# `public` é o default de lá e é o default daqui — o motivo de existir este
# módulo é justamente não terminar em privado.
PRIVACIDADES = ("public", "unlisted", "private")

# Teto da legenda no TikTok e no Instagram, em caracteres. Os dois números são
# 2.200 e vêm da página de limites da documentação oficial
# (docs.upload-post.com/resources/character-limits, lida em 16/09/2026). Mesma
# regra do título do YouTube: RECUSA, não corta.
TETO_LEGENDA = 2200

# O enum de privacidade do TikTok, do schema `TikTokPrivacyLevel` da mesma
# spec, e o mapa para as palavras que este projeto usa.
#
# `unlisted` NÃO ESTÁ AQUI, e a ausência é a decisão: o TikTok não tem "público
# para quem tem o link". Um pedido de `unlisted` para o TikTok é recusado com o
# motivo, em vez de virar SELF_ONLY (que publicaria para ninguém) ou
# PUBLIC_TO_EVERYONE (que publicaria para todos) — as duas traduções mentem, em
# direções opostas.
PRIVACIDADE_TIKTOK = {"public": "PUBLIC_TO_EVERYONE",
                      "private": "SELF_ONLY"}

# A espera do status. Backoff porque o teto de requisições do Upload-Post não é
# conhecido e um laço apertado é o jeito mais rápido de descobrir qual é, no
# meio do único envio que importava.
ESPERA_INICIAL = 2.0
ESPERA_FATOR = 1.5
ESPERA_TETO = 15.0

# Os estados que significam "ainda não acabou". `processing` está aqui porque o
# envio real de 15/09/2026 passou por ele, e NÃO por `pending`: a primeira
# versão desta espera só continuava enquanto o estado fosse `pending`, então ela
# teria saído do laço na primeira consulta e chamado de concluído um envio que
# mal tinha começado — devolvendo `post_url: None` e "a API não devolveu
# endereço" para um envio que ia dar certo. Os outros dois nomes não foram
# medidos; estão aqui porque errar para o lado de esperar é barato e errar para
# o lado de declarar pronto é exatamente o bug que acabou de acontecer.
ESTADOS_ANDAMENTO = ("pending", "processing", "queued", "in_progress")

# A frase que fecha toda saída de `wait_for`. Não é decoração: é a diferença
# entre o que a API disse e o que o mundo vê, e é o passo que o `youtube
# publish` deste projeto não tem — lá o 200 vinha e o vídeo ficava trancado.
PROVA_FINAL = (
    "Isto é o que a API DEVOLVEU, não o que o mundo vê. A confirmação final é "
    "abrir o endereço numa JANELA ANÔNIMA, deslogado: se abrir ali, está "
    "público de verdade; se pedir login ou der erro, não está.")

# Guardado por processo para `wait_for` poder comparar o que voltou com o que
# foi pedido sem que quem chama precise carregar o dado na mão. Em outro
# processo isso não existe, e aí a comparação simplesmente não é feita — o que
# é diferente de ser feita errado.
_PEDIDOS = {}

# A chave já vista neste processo, para nunca vazar numa mensagem. Estado de
# módulo, sim: ela já está na memória do processo de qualquer jeito, e o ganho
# é que toda exceção daqui passa por `_limpa` antes de existir.
_SEGREDOS = set()


class PostIndisponivel(RuntimeError):
    """Não há como publicar, ou o intermediário recusou.

    A mensagem passa por `_limpa` na CONSTRUÇÃO, não no ponto de uso: é a única
    garantia de sigilo que não depende de quem escrever a próxima linha lembrar
    da regra.

    `codigo` é o HTTP que voltou, quando houve um, para quem chama decidir sem
    reler a frase em português (401 manda conferir a chave, 5xx manda tentar de
    novo — são reações diferentes).
    """

    def __init__(self, mensagem, codigo=None):
        super().__init__(_limpa(mensagem))
        self.codigo = codigo


# ───────────────────────────────────────────────────────── costuras testáveis

def _agora():
    """Relógio monotônico, para prazos. Não anda para trás com ajuste de NTP."""
    return time.monotonic()


def _dorme(segundos):
    """O único sleep do módulo, para o teste não esperar 300 segundos de verdade."""
    time.sleep(segundos)


def _abre(pedido, timeout):
    """Abre o pedido pelo opener GUARDADO, nunca pelo `urlopen` cru.

    A diferença é um cabeçalho. Toda chamada daqui leva `Authorization: Apikey
    <chave>`, e o `urlopen` cru segue um 302 para outro host copiando os
    cabeçalhos do pedido anterior -- então um redirecionamento entregava a
    chave do dono a quem respondesse o segundo endereço. O opener do
    `warden_media` refaz a checagem de endereço a cada salto e larga o
    `Authorization` quando o host muda.

    Importado aqui dentro e não no topo: este módulo é carregado sozinho em
    boa parte da suíte, e o `warden_media` traz o PIL e o ffmpeg atrás.
    """
    import warden_media
    return warden_media.opener_guardado().open(pedido, timeout=timeout)


def _e_loopback(host):
    """O host é esta máquina, e só ela?

    `localhost` e `127.0.0.1` são o único endereço que pode ser `http` aqui, e
    a razão é que eles não são uma saída: quem conseguiu escrever
    `WARDEN_POST_BASE_URL` no ambiente deste processo também consegue ler
    `WARDEN_POST_API_KEY` dele. Mandar a chave para 127.0.0.1 não entrega nada
    a ninguém que já não a tivesse. É o que deixa a suíte subir um
    `http.server` de verdade e montar o multipart de verdade, que é a parte
    que um mock não prova.

    `10.0.0.5` é OUTRA coisa e continua recusado: ali a chave atravessa a
    fronteira da máquina.
    """
    if not host:
        return False
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_BASES_CONFERIDAS = {}


def _base_url():
    """O endereço do intermediário, CONFERIDO. `https` e host público, ou nada.

    `WARDEN_POST_BASE_URL` existia sem portão nenhum: qualquer valor no
    ambiente virava o endereço para onde `Authorization: Apikey <chave>` ia. Um
    `http://` mandava a chave do dono em texto claro; um `https://coletor.ruim`
    mandava a chave para quem escreveu a variável; um `http://169.254.169.254`
    apontava a mesma chave para o serviço de metadados da nuvem. Nenhuma das
    três precisava de um defeito no código para acontecer -- bastava a
    variável.

    Quem sabe fazer essa pergunta é o `safe_url` do `warden_media`, e é ele que
    responde: esquema aceitável, host que resolve, e nenhum endereço privado,
    de loopback, link-local ou de metadados atrás do nome. Aqui em cima dele
    fica a exigência de `https`, que o `safe_url` não faz porque a fonte de
    vídeo dele não carrega segredo nenhum e esta chamada carrega.

    O resultado é lembrado por valor: `safe_url` resolve o nome, e resolver o
    mesmo nome a cada requisição de uma sessão de upload é uma consulta de DNS
    por pedido sem responder nada de novo.
    """
    bruto = (os.environ.get(ENV_BASE) or BASE_PADRAO).rstrip("/")
    if bruto in _BASES_CONFERIDAS:
        return _BASES_CONFERIDAS[bruto]
    partes = urllib.parse.urlparse(bruto)
    esquema = (partes.scheme or "").lower()
    if _e_loopback(partes.hostname) and esquema in ("http", "https"):
        _BASES_CONFERIDAS[bruto] = bruto
        return bruto
    if esquema != "https":
        raise PostIndisponivel(
            f"{ENV_BASE}={bruto!r} não é `https`. A chave desta conta vai no "
            f"cabeçalho de toda chamada a esse endereço, e em `http` ela viaja "
            f"em texto claro por cada roteador do caminho. Corrija a variável "
            f"ou apague-a — sem ela o endereço é {BASE_PADRAO}.")
    try:
        import warden_media
        warden_media.safe_url(bruto)
    except PostIndisponivel:
        raise
    except Exception as exc:
        raise PostIndisponivel(
            f"{ENV_BASE}={bruto!r} não serve como endereço do intermediário: "
            f"{exc}. A chave desta conta iria para lá no cabeçalho de toda "
            f"chamada. Corrija a variável ou apague-a — sem ela o endereço é "
            f"{BASE_PADRAO}.")
    _BASES_CONFERIDAS[bruto] = bruto
    return bruto


def _http(metodo, url, *, corpo=None, cabecalhos=None, timeout=TIMEOUT_API):
    """A ÚNICA porta para fora do processo. -> (código HTTP, corpo em bytes).

    Um erro HTTP não vira exceção aqui. O corpo de um 4xx é justamente onde a
    API explica o que houve, e jogá-lo fora para levantar um `HTTPError` seco é
    como se perde a causa e se começa a inventar uma.
    """
    pedido = urllib.request.Request(url, data=corpo, method=metodo,
                                    headers=cabecalhos or {})
    try:
        with _abre(pedido, timeout) as resposta:
            return resposta.getcode(), resposta.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        # `exc.reason` pode trazer a URL, e a URL nunca carrega a chave (ela vai
        # no cabeçalho) — mas o `_limpa` do construtor cobre o dia em que
        # alguém mudar isso.
        raise PostIndisponivel(
            f"não deu para falar com {url}: {exc.reason}. Isto é a rede desta "
            "máquina, não recusa do intermediário — confira a conexão e mande "
            "de novo.")
    except OSError as exc:
        raise PostIndisponivel(
            f"não deu para falar com {url}: {type(exc).__name__}: {exc}")


# ────────────────────────────────────────────────────────── o estado no disco

def _dir_estado():
    """A pasta do agente. -> caminho, sem criar nada.

    Cópia fiel de `warden.state_dir()` menos o `makedirs`, pelo motivo do
    comentário em DIR_PADRAO. `WARDEN_POST_DIR` existe para a suíte apontar
    para um temporário sem mexer no resto do estado; `WARDEN_DIR` é a variável
    que o resto do projeto já respeita e continua valendo aqui.
    """
    explicito = os.environ.get(ENV_DIR) or os.environ.get("WARDEN_DIR")
    if explicito:
        return explicito
    if os.path.isdir("/var/lib/hermes"):
        return DIR_PADRAO
    return os.path.expanduser("~/.clip-warden")


def _caminho_chave():
    return os.path.join(_dir_estado(), NOME_ARQ_CHAVE)


def _caminho_perfil():
    return os.path.join(_dir_estado(), NOME_ARQ_PERFIL)


def _grava(caminho, texto, modo):
    """Escreve `texto` em `caminho` com a permissão pedida, atomicamente.

    Três cuidados, e cada um é um jeito de perder a chave:

    1. O arquivo NASCE com a permissão final. Criar 0644 e dar `chmod` depois
       deixa uma janela em que a chave do dono é legível por qualquer processo
       do container -- curta, e a única que existiria.
    2. Escreve num `.tmp` e faz `os.replace`. Um container morto no meio de um
       `write` deixaria um arquivo truncado, e uma chave pela metade falha com
       401 -- um erro que manda a pessoa conferir a chave certa que ela digitou.
    3. Se quem escreve é o ROOT e a pasta é de outro uid, o arquivo é entregue
       ao dono da pasta. Isso não é hipótese: `docker exec` entra como root
       neste projeto, e um arquivo root:root dentro do 0700 do uid 10000 é uma
       chave que o agente não consegue reler.
    """
    pasta = os.path.dirname(caminho)
    os.makedirs(pasta, exist_ok=True)
    tmp = caminho + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modo)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(texto)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:                                     # pragma: no cover
            pass
        raise
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            dono = os.stat(pasta)
            if dono.st_uid != 0:
                os.chown(tmp, dono.st_uid, dono.st_gid)
    except OSError:                                         # pragma: no cover
        # Sem permissão para doar o arquivo: o `replace` ainda vale mais que
        # nada, e quem lê depois recebe um erro de permissão com caminho e
        # dono, que é diagnosticável. Perder a gravação inteira aqui não é.
        pass
    os.replace(tmp, caminho)


def guardar_chave(bruto):
    """Guarda a chave da pessoa no volume. -> caminho do arquivo.

    Por que existir, já que `WARDEN_POST_API_KEY` no ambiente sempre funcionou:
    na nuvem da Plow não há `.env` e não há `compose.yml`. O ambiente traz o que
    a Plow põe nele e nada mais, então a chave da PRÓPRIA PESSOA -- a conta
    dela, o limite dela, a fatura dela -- não tem por onde entrar. Este arquivo
    é esse caminho, e é o único lugar do projeto onde um segredo é escrito.

    O que ela NÃO faz: imprimir, registrar, devolver ou repetir a chave. O
    retorno é o caminho, de propósito -- quem chama quer dizer "guardei", não
    mostrar o que guardou.
    """
    valor = (bruto or "").strip()
    if not valor:
        raise PostIndisponivel(
            "não veio chave nenhuma na entrada padrão. `warden post setkey` lê "
            "a chave do stdin justamente para ela não ficar no histórico do "
            "shell: use `echo <chave> | warden post setkey`, ou cole a chave e "
            "termine com Ctrl-D.")
    if "\n" in valor or "\r" in valor:
        # Uma chave com quebra no meio é colagem de duas coisas, e o erro que
        # ela produz depois é um 401 que manda conferir a chave certa.
        raise PostIndisponivel(
            "a entrada tem mais de uma linha, e uma chave de API é uma linha "
            "só. Nada foi guardado. Mande só a chave.")
    _SEGREDOS.add(valor)
    caminho = _caminho_chave()
    _grava(caminho, valor + "\n", 0o600)
    return caminho


def _chave_do_arquivo():
    """A chave guardada, ou None. Nunca levanta por arquivo ausente."""
    try:
        with open(_caminho_chave(), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        # Ausente, ilegível ou de outro dono: para quem chama é tudo "não há
        # chave no arquivo", e a diferença aparece no `status`, que olha a
        # permissão. Um traceback aqui derrubaria o comando inteiro por causa
        # de um arquivo opcional.
        return None


def id_guardado():
    """O nome do perfil desta máquina, se já foi decidido. -> str ou None."""
    try:
        with open(_caminho_perfil(), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _guarda_id(nome):
    """Grava o nome do perfil desta máquina. Uma vez, e para sempre."""
    _grava(_caminho_perfil(), str(nome).strip() + "\n", 0o644)
    return nome


def _novo_id():
    """`clip-warden-<8 hexa>`. O nome que é DESTA máquina e de nenhuma outra."""
    return PREFIXO_PERFIL + uuid.uuid4().hex[:DIGITOS_PERFIL]


# ───────────────────────────────────────────────────────────────── o segredo

def _limpa(texto):
    """Apaga de um texto qualquer chave que este processo já leu.

    Cinto e suspensório. As mensagens daqui são montadas para não conter a
    chave, mas duas delas repassam o corpo de resposta de um servidor, e um dia
    algum servidor vai ecoar o cabeçalho que recebeu. O caso real que isto
    cobre é o 401: é exatamente o erro em que um servidor gosta de devolver
    "invalid key: <a chave inteira>".
    """
    texto = str(texto)
    for segredo in _SEGREDOS:
        # Abaixo de 8 caracteres não se substitui: uma "chave" de 3 letras
        # apagaria pedaços de palavras da mensagem e o texto ficaria ilegível.
        if segredo and len(segredo) >= 8:
            texto = texto.replace(segredo, "<chave oculta>")
    return texto


def _chave_e_origem():
    """A chave e DE ONDE ela veio. -> (chave, "ambiente"|"arquivo").

    A ordem é ambiente primeiro, arquivo depois, e ela não é arbitrária: a
    variável é a alavanca de quem está de pé agora -- o dono do agente, a Plow,
    um teste -- e tem de ganhar de um arquivo que pode ter sido escrito semanas
    atrás.

    A ORIGEM IMPORTA, e é por isso que esta função devolve duas coisas:

      ambiente  a chave foi posta ali por quem monta a máquina. Pode ser a
                chave do DONO DO AGENTE, e nesse caso VÁRIAS máquinas usam a
                mesma conta -- então esta máquina não pode adotar um perfil que
                já exista na conta, porque ele é de outra pessoa.
      arquivo   a chave foi digitada nesta conversa, nesta máquina, por quem
                usa esta máquina. A conta é dela, e um perfil solitário lá
                dentro é dela também.

    Sem nenhuma das duas o módulo está DESLIGADO e nada aqui abre socket. A
    mensagem diz os DOIS caminhos, porque na nuvem da Plow não existe `.env`
    nem `compose.yml` e "exporte a variável" seria um endereço que não existe.
    """
    valor = (os.environ.get(ENV_CHAVE) or "").strip()
    origem = "ambiente"
    if not valor:
        valor = _chave_do_arquivo()
        origem = "arquivo"
    if not valor:
        raise PostIndisponivel(
            f"não há chave do intermediário de publicação, então este módulo "
            f"não fez nenhuma requisição. Há dois caminhos: a variável de "
            f"ambiente {ENV_CHAVE} (é assim que o dono do agente põe a chave "
            f"dele, e aí a conta é a dele), ou a chave da PRÓPRIA PESSOA, "
            f"guardada nesta máquina com `warden post setkey` -- que a lê da "
            f"entrada padrão e grava em {_caminho_chave()} com permissão 0600. "
            f"Pegue a chave em https://app.upload-post.com/api-keys. Ela NÃO "
            f"vai para arquivo nenhum deste repositório.")
    _SEGREDOS.add(valor)
    return valor, origem


def _chave():
    """Só a chave, para quem não precisa saber de onde ela veio."""
    return _chave_e_origem()[0]


def esta_configurado():
    """True se há chave, no ambiente ou no arquivo. Não abre socket.

    Serve para a CLI decidir se mostra o subcomando ou o aviso de desligado sem
    precisar capturar exceção para perguntar uma coisa simples.
    """
    if (os.environ.get(ENV_CHAVE) or "").strip():
        return True
    return bool(_chave_do_arquivo())


# ─────────────────────────────────────────────────────────────── multipart

def _parte_campo(nome, valor):
    """Um campo de texto do multipart, já em bytes."""
    return (f'Content-Disposition: form-data; name="{nome}"\r\n\r\n'
            f"{valor}\r\n").encode("utf-8")


def _multipart(campos, arquivo_campo, caminho, bytes_arquivo):
    """Monta um corpo multipart/form-data à mão. -> (corpo, content-type).

    À mão porque a regra do projeto é biblioteca padrão apenas, e a stdlib não
    traz um construtor de multipart. `campos` é uma lista de pares e não um
    dicionário de propósito: `platform[]` pode repetir, e repetir é como a API
    recebe mais de uma rede num envio só.
    """
    fronteira = "----warden" + uuid.uuid4().hex
    marca = ("--" + fronteira + "\r\n").encode("utf-8")
    pedacos = []
    for nome, valor in campos:
        pedacos.append(marca)
        pedacos.append(_parte_campo(nome, valor))
    nome_arquivo = os.path.basename(caminho)
    # `mimetypes` erra para alguns contêineres de vídeo e devolve None; o que
    # este projeto produz e mede é .mp4, então é esse o palpite de reserva.
    tipo = mimetypes.guess_type(nome_arquivo)[0] or "video/mp4"
    pedacos.append(marca)
    pedacos.append(
        (f'Content-Disposition: form-data; name="{arquivo_campo}"; '
         f'filename="{nome_arquivo}"\r\n'
         f"Content-Type: {tipo}\r\n\r\n").encode("utf-8"))
    pedacos.append(bytes_arquivo)
    pedacos.append(b"\r\n")
    pedacos.append(("--" + fronteira + "--\r\n").encode("utf-8"))
    return b"".join(pedacos), "multipart/form-data; boundary=" + fronteira


# ───────────────────────────────────────────────────────── leitura tolerante

def _texto(bruto):
    """Bytes de resposta como texto legível e truncado, já sem a chave."""
    if isinstance(bruto, bytes):
        try:
            texto = bruto.decode("utf-8", "replace")
        except Exception:                                   # pragma: no cover
            texto = repr(bruto)
    else:
        texto = str(bruto)
    texto = texto.strip()
    if len(texto) > TETO_CORPO:
        texto = texto[:TETO_CORPO] + f"… (+{len(texto) - TETO_CORPO} caracteres)"
    return _limpa(texto)


def _json_ou_explica(codigo, bruto, oque):
    """-> dict do corpo. Levanta com o código e o corpo verbatim quando não dá.

    A mensagem repassa o que o servidor disse e PARA. Nenhuma frase daqui
    adivinha por que o intermediário recusou: quem sabe a causa é ele.
    """
    if codigo >= 400:
        raise PostIndisponivel(
            f"{oque}: o intermediário respondeu HTTP {codigo}. O corpo da "
            f"resposta, verbatim: {_texto(bruto)}", codigo=codigo)
    try:
        dados = json.loads(bruto.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PostIndisponivel(
            f"{oque}: HTTP {codigo}, mas o corpo não é JSON "
            f"({type(exc).__name__}). O corpo, verbatim: {_texto(bruto)}",
            codigo=codigo)
    if not isinstance(dados, dict):
        raise PostIndisponivel(
            f"{oque}: HTTP {codigo}, mas o corpo é {type(dados).__name__} e "
            f"não um objeto JSON. Verbatim: {_texto(bruto)}", codigo=codigo)
    return dados


def _primeiro(dados, nomes):
    """O primeiro nome presente e não vazio em `dados`, ou None.

    Existe porque os corpos de `/upload` e `/status` NÃO foram medidos (só `/me`
    e `/users` foram, em 15/09/2026). Procurar em mais de um nome é o que
    permite ler a resposta sem fingir que o schema é conhecido; quando nenhum
    dos nomes aparece, quem chama devolve "a API não disse" — que é a verdade —
    em vez de repetir o que foi pedido.
    """
    if not isinstance(dados, dict):
        return None
    for nome in nomes:
        valor = dados.get(nome)
        if valor not in (None, "", [], {}):
            return valor
    return None


# ────────────────────────────────────────────────────────────── o provedor

class Provedor:
    """O contrato que um intermediário precisa cumprir.

    Existe como classe, e não como três funções soltas, porque já se sabe que
    haverá um segundo: o Upload-Post é escolha de hoje, não casamento. Quem
    acrescentar o próximo implementa estes quatro métodos e registra em
    PROVEDORES — nada fora daqui precisa saber o nome dele.
    """

    nome = "?"

    def url(self, caminho, consulta=None):                  # pragma: no cover
        raise NotImplementedError

    def cabecalhos(self, chave):                            # pragma: no cover
        raise NotImplementedError

    def perfis(self, chave):                                # pragma: no cover
        raise NotImplementedError

    def conta(self, chave):                                 # pragma: no cover
        raise NotImplementedError


class UploadPost(Provedor):
    """Upload-Post: o app auditado que publica no lugar do nosso, não auditado.

    O que este adaptador sabe de fato, porque foi medido em 15/09/2026, são as
    duas rotas de leitura (`/uploadposts/me` e `/uploadposts/users`) e o formato
    do cabeçalho de autorização — que é `Apikey <chave>`, e NÃO `Bearer`.
    """

    nome = "upload-post"

    def url(self, caminho, consulta=None):
        # `_base_url()`, não `os.environ` direto: o endereço para onde a chave
        # vai passa por um portão antes de virar requisição. Ver a função.
        endereco = _base_url() + caminho
        if consulta:
            endereco += "?" + urllib.parse.urlencode(consulta)
        return endereco

    def cabecalhos(self, chave):
        # `Apikey`, não `Bearer`. Foi assim que o 200 de 15/09 veio; com
        # `Bearer` a mesma chave não autentica.
        return {"Authorization": "Apikey " + chave,
                "Accept": "application/json"}

    def conta(self, chave):
        """GET /uploadposts/me -> dict com email e plano. Valida a chave."""
        codigo, bruto = _http("GET", self.url("/uploadposts/me"),
                              cabecalhos=self.cabecalhos(chave))
        dados = _json_ou_explica(codigo, bruto, "ao conferir a chave em /me")
        return {"email": dados.get("email"),
                "plano": dados.get("plan"),
                "mensagem": dados.get("message"),
                "valida": bool(dados.get("success"))}

    def perfis(self, chave):
        """GET /uploadposts/users -> lista de perfis já normalizada.

        Normalizar aqui é o que deixa o resto do módulo ignorar uma esquisitice
        medida: uma rede NÃO conectada vem como string vazia (`"tiktok": ""`),
        e uma conectada vem como objeto. Sem isto, todo lugar que lê contas
        precisaria lembrar de testar o tipo.
        """
        codigo, bruto = _http("GET", self.url("/uploadposts/users"),
                              cabecalhos=self.cabecalhos(chave))
        dados = _json_ou_explica(codigo, bruto, "ao listar os perfis em /users")
        perfis = []
        for bruto_perfil in (dados.get("profiles") or []):
            if not isinstance(bruto_perfil, dict):
                continue
            contas = {}
            for rede, valor in (bruto_perfil.get("social_accounts") or {}).items():
                if isinstance(valor, dict):
                    contas[rede] = {
                        "conectada": True,
                        "display_name": valor.get("display_name"),
                        "handle": valor.get("handle"),
                        "reauth_required": bool(valor.get("reauth_required")),
                    }
                else:
                    contas[rede] = {"conectada": False, "display_name": None,
                                    "handle": None, "reauth_required": False}
            perfis.append({"nome": bruto_perfil.get("username"), "contas": contas})
        return {"perfis": perfis, "limite": dados.get("limit"),
                "plano": dados.get("plan")}

    def cria_perfil(self, chave, nome):
        """POST /uploadposts/users {"username": nome} -> (criado?, mensagem).

        Medido em 16/09/2026: 409 `Username already in use` quando o nome já
        existe, e é por isso que "já existe" NÃO é erro aqui -- quem chama quer
        um perfil com esse nome, e um que já está lá serve.
        """
        codigo, bruto = _http(
            "POST", self.url("/uploadposts/users"),
            corpo=json.dumps({"username": nome}).encode("utf-8"),
            cabecalhos=dict(self.cabecalhos(chave),
                            **{"Content-Type": "application/json"}))
        if codigo == 409:
            return False, "esse perfil já existia"
        dados = _json_ou_explica(codigo, bruto, "ao criar o perfil")
        return bool(dados.get("success")), _texto(dados.get("message"))

    def link_de_conexao(self, chave, nome):
        """POST /uploadposts/users/generate-jwt {"username": nome} -> URL.

        É a única coisa que a pessoa precisa fazer fora do chat: abrir este
        endereço, escolher o canal na tela do Google e aprovar. Medido em
        16/09/2026 contra a API real; o campo do corpo é `username` (com
        `profile` a API responde 400 dizendo `profile_username is required`, e
        em GET com querystring responde 404).
        """
        codigo, bruto = _http(
            "POST", self.url("/uploadposts/users/generate-jwt"),
            corpo=json.dumps({"username": nome}).encode("utf-8"),
            cabecalhos=dict(self.cabecalhos(chave),
                            **{"Content-Type": "application/json"}))
        dados = _json_ou_explica(codigo, bruto, "ao gerar o link de conexão")
        url = _primeiro(dados, ("access_url", "url", "link"))
        if not url:
            raise PostIndisponivel(
                "o provedor respondeu sem endereço de conexão. O que ele "
                f"devolveu, verbatim: {_texto(bruto)[:300]}")
        return str(url)


PROVEDORES = {UploadPost.nome: UploadPost}


def _provedor():
    """O adaptador do provedor pedido. Levanta se não houver implementação.

    Um nome desconhecido não cai no padrão em silêncio: cair no padrão
    publicaria no canal do dono por um caminho que ninguém pediu.
    """
    nome = (os.environ.get(ENV_PROVEDOR) or PROVEDOR_PADRAO).strip().lower()
    classe = PROVEDORES.get(nome)
    if classe is None:
        raise PostIndisponivel(
            f"{ENV_PROVEDOR}={nome!r} não tem implementação neste módulo. "
            f"Implementado hoje: {', '.join(sorted(PROVEDORES))}. O desenho "
            "aceita outro provedor — falta escrever o adaptador dele, e "
            "escolher um nome não escreve.")
    return classe()


def _perfil(provedor, chave, perfis=None, origem_da_chave=None):
    """O `user` que vai no envio. -> (nome, lista de perfis, avisos, origem).

    A ordem de decisão, e cada degrau existe por um estrago diferente:

      1. WARDEN_POST_PROFILE  quem nomeou à mão decidiu, e ganha de tudo. É
         ("ambiente")         também a saída de emergência de uma instalação
                              antiga que quer continuar no canal de sempre.
      2. o id guardado        esta máquina já decidiu uma vez, no volume que
         ("guardado")         sobrevive a reinício. Reusar é a regra inteira:
                              um nome novo a cada boot é um canal novo a cada
                              boot. Este caminho não consulta /users.
      3. adotar o perfil      SÓ quando a chave veio do ARQUIVO e a conta tem
         ("adotado")          exatamente um perfil. Chave no arquivo quer dizer
                              que ela foi digitada nesta máquina, por quem usa
                              esta máquina: a conta é dela e o perfil solitário
                              lá dentro é o canal que ela já conectou. Perder
                              esse canal por causa de um nome novo seria o
                              agente esquecendo o que a pessoa já fez.
      4. gerar um id novo     todo o resto, e É O CASO NORMAL quando a chave vem
         ("gerado")           do ambiente. Aí a chave pode ser a do DONO DO
                              AGENTE, compartilhada por várias máquinas, e
                              adotar o perfil que estiver lá seria publicar no
                              canal de outra pessoa.

    O nome escolhido em 3 e 4 é GRAVADO na hora, antes de qualquer envio. Se o
    envio falhar, a máquina continua com o mesmo nome na próxima tentativa --
    que é o oposto de sortear um perfil por requisição.
    """
    avisos = []
    escolhido = (os.environ.get(ENV_PERFIL) or "").strip()
    origem = "ambiente"
    if not escolhido:
        escolhido = id_guardado()
        origem = "guardado"
    if escolhido and perfis is None:
        # Já decidido e lista não pedida: vai assim, sem consultar /users.
        # Buscar a lista aqui custaria uma requisição a mais antes de CADA
        # envio só para produzir um aviso — e pior, faria um tropeço do /users
        # derrubar um envio que teria funcionado. Quem decide se o perfil
        # existe é a API, na hora do envio. A conferência continua existindo em
        # `status()`, onde a lista já está na mão e não custa nada.
        return escolhido, [], avisos, origem
    if perfis is None:
        perfis = provedor.perfis(chave)["perfis"]
    nomes = [p["nome"] for p in perfis if p.get("nome")]
    if escolhido:
        if nomes and escolhido not in nomes:
            # Aviso e não recusa: a lista de perfis pode estar atrasada em
            # relação ao painel, e quem nomeou o perfil à mão sabe o que quis.
            # Quem decide se o nome existe é a API, na hora do envio.
            de_onde = (f"{ENV_PERFIL}={escolhido!r}" if origem == "ambiente"
                       else f"o perfil desta máquina, {escolhido!r},")
            avisos.append(
                f"{de_onde} não está entre os perfis que a API listou "
                f"({', '.join(nomes)}). Rode `warden post connect`, que cria o "
                "perfil e devolve o endereço de conectar o canal.")
        return escolhido, perfis, avisos, origem

    if origem_da_chave is None:
        origem_da_chave = _chave_e_origem()[1]
    if origem_da_chave == "arquivo" and len(nomes) == 1:
        escolhido = nomes[0]
        origem = "adotado"
        avisos.append(
            f"esta máquina passou a usar o perfil {escolhido!r}, que já existia "
            "nesta conta e é o único dela. A chave é a da própria pessoa, então "
            "o canal conectado nele é o dela — adotar é o que evita que ela "
            "tenha de conectar de novo um canal que já estava ligado.")
    else:
        escolhido = _novo_id()
        origem = "gerado"
        avisos.append(
            f"esta máquina ainda não tinha perfil e passou a ter o seu: "
            f"{escolhido!r}. O nome é único por máquina de propósito — a chave "
            f"pode ser a mesma em várias instalações, e um nome fixo faria "
            f"todas publicarem no mesmo canal. Ele fica guardado em "
            f"{_caminho_perfil()} e não muda mais.")
    _guarda_id(escolhido)
    return escolhido, perfis, avisos, origem


# ──────────────────────────────────────────────────────────── função pública

def status():
    """O que a conta do intermediário é, agora. -> dict.

    Faz duas leituras MEDIDAS em 15/09/2026: `/uploadposts/me`, que diz se a
    chave vale, e `/uploadposts/users`, que diz quais redes estão conectadas em
    cada perfil.

    O que importa nesta saída e passa despercebido: `reauth_required`. Uma
    conta conectada com `reauth_required: true` continua aparecendo na lista e
    NÃO publica — é o jeito mais silencioso de o agente falhar, porque tudo
    parece conectado até o envio. Por isso ele sai também numa lista própria,
    `reauth`, e não só enterrado dentro de cada conta.

    Levanta PostIndisponivel quando não há chave — e aí nenhuma requisição é
    feita.
    """
    chave, origem_da_chave = _chave_e_origem()
    provedor = _provedor()
    conta = provedor.conta(chave)
    listagem = provedor.perfis(chave)
    perfis = listagem["perfis"]

    # O perfil pode não resolver e isso NÃO derruba o status: status é
    # justamente o comando que a pessoa roda para descobrir o que falta. A
    # frase da recusa vira aviso.
    avisos = []
    escolhido = None
    origem_do_perfil = None
    try:
        escolhido, _, avisos, origem_do_perfil = _perfil(
            provedor, chave, perfis=perfis, origem_da_chave=origem_da_chave)
    except PostIndisponivel as exc:
        avisos.append(str(exc))

    reauth = []
    for perfil in perfis:
        for rede, conta_rede in sorted(perfil["contas"].items()):
            if conta_rede["conectada"] and conta_rede["reauth_required"]:
                reauth.append(f"{perfil['nome']}/{rede}")
    if reauth:
        avisos.append(
            "estas contas estão conectadas mas pedem REAUTENTICAÇÃO e não vão "
            f"publicar: {', '.join(reauth)}. Reconecte no painel do provedor "
            "antes de mandar um clipe — o envio falharia depois de subir o "
            "arquivo inteiro.")

    if not conta["valida"]:
        avisos.append(
            "a resposta de /me não trouxe `success: true`. A mensagem dela, "
            f"verbatim: {_texto(conta.get('mensagem'))}")

    return {
        "provedor": provedor.nome,
        "base": provedor.url(""),
        "chave": "configurada",       # jamais o valor. Só o fato.
        # DE ONDE ela veio, que é o que decide se esta máquina pode adotar um
        # perfil que já exista na conta. O valor nunca sai daqui; a procedência
        # sai, porque é ela que a pessoa precisa conferir quando o canal não é
        # o que ela esperava.
        "chave_origem": origem_da_chave,
        "perfil_origem": origem_do_perfil,
        "email": conta["email"],
        "plano": conta["plano"] or listagem.get("plano"),
        "limite_perfis": listagem.get("limite"),
        "perfil": escolhido,
        "perfis": perfis,
        "reauth": reauth,
        "avisos": avisos,
    }


def conectar(nome=None):
    """O link que conecta o canal da pessoa. -> dict.

    Um passo, e ele é o único que não cabe no chat: a senha do Google é
    digitada na tela do Google e em lugar nenhum mais.

    Antes disto, conectar era cinco passos num painel web, e o dono mediu o
    custo em 16/09/2026: "precisa ser no chat apenas que ele conecta ou no
    máximo um link de autenticação onde ele aprova ou não e posta". O perfil
    passa a ser criado aqui quando não existe, e o endereço sai pronto para
    colar.

    `ja_conectado` diz se aquele perfil JÁ tem o YouTube ligado. Quem chama usa
    isso para não mandar alguém aprovar de novo o que já está aprovado -- e
    para distinguir "conectado" de "conectado mas pedindo reautenticação", que
    é o jeito silencioso de isto falhar. `contas` traz as outras redes do mesmo
    perfil, porque o endereço de conexão é UM só para todas: a pessoa escolhe
    na tela do provedor qual vai ligar.

    O nome do perfil, quando não vem por argumento, é o MESMO que o envio vai
    usar -- `_perfil` decide, e decide uma vez. Enquanto eram duas resoluções
    diferentes, `connect` podia criar um perfil e o envio ir para outro.
    """
    chave, origem_da_chave = _chave_e_origem()
    provedor = _provedor()
    listagem = provedor.perfis(chave)
    perfis = listagem["perfis"]
    avisos = []
    origem_do_perfil = "pedido"
    alvo = (nome or "").strip()
    if not alvo:
        # A MESMA resolução do envio, e não uma paralela. Enquanto foram duas,
        # `connect` podia criar um perfil e o envio ir para outro.
        alvo, _p, avisos, origem_do_perfil = _perfil(
            provedor, chave, perfis=perfis, origem_da_chave=origem_da_chave)

    existente = next((p for p in perfis if p["nome"] == alvo), None)
    criado = False
    if existente is None:
        if listagem.get("limite") is not None and len(perfis) >= listagem["limite"]:
            # A causa, com o número, e o que fazer. Sem isto a pessoa recebe um
            # "não deu" e o agente tem de inventar um porquê -- que é a coisa
            # que este projeto inteiro proíbe.
            raise PostIndisponivel(
                f"NÃO DÁ PARA CRIAR OUTRO PERFIL: o plano desta conta do "
                f"Upload-Post permite {listagem['limite']} e já há "
                f"{len(perfis)} ({', '.join(p['nome'] for p in perfis)}). Isto "
                f"é o limite do plano e nada mais — a chave está valendo e a "
                f"rede respondeu. Saídas: apague um perfil que não usa no "
                f"painel do Upload-Post, suba o plano, ou aponte "
                f"{ENV_PERFIL} para um dos perfis que já existem.")
        criado, _msg = provedor.cria_perfil(chave, alvo)

    yt = (existente or {}).get("contas", {}).get("youtube") or {}
    return {
        "perfil": alvo,
        "perfil_origem": origem_do_perfil,
        "perfil_criado": criado,
        "ja_conectado": bool(yt.get("conectada")) and not yt.get("reauth_required"),
        "pede_reautenticacao": bool(yt.get("conectada")
                                    and yt.get("reauth_required")),
        "canal": yt.get("display_name"),
        "handle": yt.get("handle"),
        "contas": (existente or {}).get("contas", {}),
        "avisos": avisos,
        "url": provedor.link_de_conexao(chave, alvo),
    }


def _campos_youtube(titulo, descricao, privacidade, avisos):
    """Os campos do YouTube, com os nomes da spec oficial. -> lista de pares.

    `title`, `youtube_description` e `privacyStatus`: os três lidos em
    docs.upload-post.com/openapi.json e os três MEDIDOS num envio real em
    15 e 16/09/2026. `privacyStatus` é o único campo de privacidade desta API
    que este projeto já viu funcionar.
    """
    if len(titulo) > TETO_TITULO:
        raise PostIndisponivel(
            f"o título tem {len(titulo)} caracteres e o YouTube aceita no "
            f"máximo {TETO_TITULO}. Este módulo NÃO corta sozinho: um título "
            "truncado vai para o canal do dono com uma frase que ninguém "
            f"escreveu. Encurte em {len(titulo) - TETO_TITULO} caractere(s) e "
            "mande de novo. Nada foi enviado.")
    campos = [("privacyStatus", privacidade)]
    if descricao.strip():
        campos.append(("youtube_description", descricao))
    return campos


def _campos_tiktok(titulo, privacidade, rascunho, avisos):
    """Os campos do TikTok. -> lista de pares.

    Da spec oficial: `tiktok_title` é a legenda (cai em `title` quando não vem),
    `privacy_level` é um enum PRÓPRIO do TikTok -- nada a ver com o do YouTube
    -- e `post_mode` decide entre publicar e mandar para os rascunhos.

    NÃO EXISTE "unlisted" NO TIKTOK. O enum é PUBLIC_TO_EVERYONE,
    MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR e SELF_ONLY, e nenhum deles
    quer dizer "público para quem tem o link". Traduzir `unlisted` para
    SELF_ONLY publicaria para ninguém um vídeo que a pessoa pediu que fosse
    visível; traduzir para PUBLIC_TO_EVERYONE publicaria para todo mundo um que
    ela pediu que fosse discreto. Então isto RECUSA e diz por quê.
    """
    if len(titulo) > TETO_LEGENDA:
        raise PostIndisponivel(
            f"a legenda tem {len(titulo)} caracteres e o TikTok aceita no "
            f"máximo {TETO_LEGENDA}. Nada foi enviado.")
    nivel = PRIVACIDADE_TIKTOK.get(privacidade)
    if nivel is None:
        raise PostIndisponivel(
            f"o TikTok não tem {privacidade!r}. O enum dele, da spec oficial, é "
            "PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR e "
            "SELF_ONLY — nenhum deles quer dizer 'público para quem tem o "
            "link', e escolher um por você seria decidir quem vê o vídeo. "
            f"Mande {' ou '.join('--privacy ' + n for n in sorted(PRIVACIDADE_TIKTOK))}"
            ". Nada foi enviado.")
    campos = [("tiktok_title", titulo),
              ("privacy_level", nivel),
              ("post_mode", "MEDIA_UPLOAD" if rascunho else "DIRECT_POST")]
    if rascunho:
        avisos.append(
            "o TikTok vai receber isto como RASCUNHO (`post_mode=MEDIA_UPLOAD`), "
            "não como publicação: o vídeo aparece no app para a pessoa revisar "
            "e publicar. A documentação oficial avisa que, em rascunho, o "
            "TikTok IGNORA a legenda e a privacidade mandadas pela API — quem "
            "escreve a legenda é ela, no app.")
    return campos


def _campos_instagram(titulo, privacidade, stories, avisos):
    """Os campos do Instagram. -> lista de pares.

    `instagram_title` é a legenda, `media_type` escolhe entre REELS (o padrão)
    e STORIES, `share_to_feed` diz se o Reel também aparece no feed.

    O INSTAGRAM NÃO TEM PARÂMETRO DE PRIVACIDADE nesta API — nem na spec, nem
    na página da rede na documentação. Quem decide quem vê é o perfil, que é
    público ou privado inteiro. Então pedir `private` aqui não é uma opção que
    o módulo desliga: é um pedido que ele NÃO CONSEGUE cumprir, e mandar assim
    mesmo publicaria no feed da pessoa um vídeo que ela pediu que fosse privado.
    """
    if len(titulo) > TETO_LEGENDA:
        raise PostIndisponivel(
            f"a legenda tem {len(titulo)} caracteres e o Instagram aceita no "
            f"máximo {TETO_LEGENDA}. Nada foi enviado.")
    if privacidade != "public":
        raise PostIndisponivel(
            f"o Instagram não aceita privacidade por post: esta API não tem "
            f"campo nenhum para isso e quem decide quem vê é o perfil, que é "
            f"público ou privado inteiro. Foi pedido {privacidade!r}, e mandar "
            f"assim mesmo publicaria no feed dela um vídeo que ela pediu que "
            f"não fosse visto. Nada foi enviado.")
    campos = [("instagram_title", titulo),
              ("media_type", "STORIES" if stories else "REELS")]
    if not stories:
        campos.append(("share_to_feed", "true"))
    return campos


def publicar(clip, plataformas, titulo, descricao="", privacidade="public",
             shorts=None, rascunho=False, stories=False):
    """Manda um clipe para uma ou mais redes PELO INTERMEDIÁRIO. -> dict.

    Este é o caminho que não cai na trava de `locked as private`: quem fala com
    a YouTube Data API é o app auditado do Upload-Post, não o projeto Google
    não auditado deste repositório. E o mesmo vale para as outras duas — o app
    de TikTok e o app Meta do intermediário já passaram pelas auditorias das
    respectivas redes, que é o que dispensa quem instala isto de pedir a sua.

    O envio é UM `POST /upload`, multipart/form-data, com `platform[]` repetido
    uma vez por rede. É assim que a API recebe mais de uma: o mesmo arquivo sobe
    uma vez só e ela distribui.

    O QUE FOI MEDIDO E O QUE NÃO FOI, e a diferença importa para quem lê a
    saída: o YouTube foi medido com envios reais em 15 e 16/09/2026, ponta a
    ponta, com o vídeo saindo público no canal. TikTok e Instagram NÃO. Os
    nomes dos campos das duas vêm da spec OpenAPI oficial
    (docs.upload-post.com/openapi.json, lida em 16/09/2026) e da página de cada
    rede na documentação; nenhum byte foi enviado para elas por este código.
    Todo envio que inclui uma das duas sai com um aviso dizendo isso, porque um
    200 sem medição atrás não vale o mesmo que um 200 com.

    O que se valida ANTES de abrir socket, e por quê:

      arquivo inexistente ou de 0 byte  — subir 0 byte gasta a janela de envio e
                                          volta um erro menos claro que esta
                                          frase;
      rede desconhecida                 — cair numa rede que este módulo não
                                          sabe montar seria publicar sem os
                                          campos dela;
      título vazio                      — YouTube e Reddit exigem título, e um
                                          clipe sem legenda nas outras duas é
                                          meio produto;
      título acima de 100 (YouTube)     — RECUSA, não trunca;
      legenda acima de 2.200 (TikTok,   — o limite das duas, da página de
      Instagram)                          limites da documentação oficial;
      privacidade que a rede não tem    — ver `_campos_tiktok` e
                                          `_campos_instagram`.

    `shorts`: NÃO existe interruptor de Short nesta API, e não vai ser
    inventado um aqui. Quem decide que um vídeo é Short é o YouTube, pelo
    formato do arquivo (vertical) e pela duração. Com `shorts=True` o que este
    módulo faz — e diz que fez, nos avisos — é acrescentar `#Shorts` ao fim da
    descrição, que é uma DICA, não uma garantia, e que não foi medida aqui.

    A privacidade que sai daqui é a PEDIDA. Este dicionário não afirma em
    momento nenhum que o vídeo ficou público: quem compara pedido com resposta
    é `wait_for`, e quem prova é a janela anônima.

    Levanta PostIndisponivel sem chave, com arquivo, rede, título ou
    privacidade inválidos, e quando o intermediário recusa — nesses casos
    nenhum byte é enviado.
    """
    # A ordem importa: a chave primeiro, para que "módulo desligado" seja a
    # resposta mesmo quando o caminho do arquivo também está errado. São dois
    # problemas diferentes e o de configuração é o que bloqueia tudo.
    chave, origem_da_chave = _chave_e_origem()
    provedor = _provedor()

    if isinstance(plataformas, str):
        plataformas = [plataformas]
    redes = []
    for rede in plataformas or []:
        rede = str(rede).strip().lower()
        if rede not in PLATAFORMAS:
            raise PostIndisponivel(
                f"{rede!r} não é uma rede que este módulo saiba montar. "
                f"Implementadas: {', '.join(PLATAFORMAS)}. Cada uma tem os "
                "campos próprios dela, e mandar sem eles publicaria com a "
                "legenda e a privacidade erradas. Nada foi enviado.")
        if rede not in redes:
            redes.append(rede)
    if not redes:
        raise PostIndisponivel(
            f"não foi dita nenhuma rede. Escolha entre {', '.join(PLATAFORMAS)}. "
            "Nada foi enviado.")

    caminho = os.path.abspath(os.path.expanduser(str(clip)))
    if not os.path.isfile(caminho):
        raise PostIndisponivel(
            f"não há arquivo em {caminho} para publicar. Nada foi enviado.")
    tamanho = os.path.getsize(caminho)
    if tamanho <= 0:
        raise PostIndisponivel(
            f"{caminho} tem 0 byte. O intermediário recusaria depois de gastar "
            "a janela de envio, e a recusa dele seria menos clara que esta.")

    titulo = (titulo or "").strip()
    if not titulo:
        raise PostIndisponivel(
            "o vídeo precisa de um título: o YouTube exige um, e um vídeo sem "
            "título publicado no canal do dono é pior que um envio recusado.")

    privacidade = (privacidade or "").strip().lower()
    if privacidade not in PRIVACIDADES:
        raise PostIndisponivel(
            f"privacidade {privacidade!r} não existe. A spec OpenAPI do "
            f"provedor aceita {', '.join(PRIVACIDADES)}. Nada foi enviado.")

    descricao = descricao or ""
    avisos = []
    if shorts:
        # Uma dica, e dita como dica. O que classifica um Short é o vídeo:
        # vertical e curto. Se alguém um dia medir que a hashtag muda alguma
        # coisa, esta frase vira uma medição com data; até lá é o que é.
        if "#shorts" not in descricao.lower():
            descricao = (descricao.rstrip() + "\n\n#Shorts").strip()
        avisos.append(
            "`shorts=True` acrescentou `#Shorts` à descrição. Isso é uma DICA: "
            "quem decide que um vídeo é Short é o YouTube, pelo formato "
            "vertical e pela duração, e essa hashtag nunca foi medida aqui "
            "mudando classificação nenhuma.")

    # Lista de pares, e não dicionário: `platform[]` repete, e repetir é como a
    # API recebe mais de uma rede num envio só.
    campos = [("user", None)]                   # o perfil entra logo abaixo
    for rede in redes:
        campos.append(("platform[]", rede))
    campos.append(("title", titulo))
    # SÍNCRONO, e a diferença é de duas ordens de grandeza.
    #
    # Este campo era `"true"`, com o argumento de que "acima de 59 segundos
    # o envio vira assíncrono de qualquer jeito, então pedir síncrono só
    # criaria dois caminhos de código". O argumento estava certo sobre os
    # dois caminhos e errado sobre o custo. Medido em 16/09/2026, o MESMO
    # clipe de 16 MB, no mesmo link, na mesma conta:
    #
    #   async_upload=true    9min51s  -- fila do worker durável deles.
    #                                    Consultei três vezes no meio:
    #                                    `status: queued, attempts: 0`.
    #   async_upload=false      14,2s  -- HTTP 200 com `status: completed`
    #                                    e a URL do vídeo no corpo.
    #
    # Nove minutos de alguém olhando para uma conversa parada, contra
    # catorze segundos. O dono estava numa chamada de tela compartilhada
    # quando mediu o primeiro.
    #
    # E os dois caminhos continuam existindo, porque a documentação deles
    # diz que acima de 59 segundos o envio vira assíncrono sozinho: quando
    # a resposta vier sem `completed`, ela traz `request_id` e o código
    # abaixo segue pelo caminho de antes, sem mudar nada.
    campos.append(("async_upload", "false"))

    # Os campos de cada rede, montados pela função dela. As validações que
    # recusam moram lá dentro e acontecem ANTES do envio, de propósito: um
    # título longo demais para o YouTube não pode gastar um upload inteiro.
    if "youtube" in redes:
        campos.extend(_campos_youtube(titulo, descricao, privacidade, avisos))
    if "tiktok" in redes:
        campos.extend(_campos_tiktok(titulo, privacidade, rascunho, avisos))
    if "instagram" in redes:
        campos.extend(_campos_instagram(titulo, privacidade, stories, avisos))

    nao_medidas = [r for r in redes if r not in MEDIDAS_DE_VERDADE]
    if nao_medidas:
        avisos.append(
            f"NENHUM envio real deste projeto passou por "
            f"{', '.join(nao_medidas)}. Os nomes dos campos vêm da spec "
            f"OpenAPI oficial do provedor, lida em 16/09/2026, e só isso — o "
            f"que foi medido ponta a ponta, com o vídeo saindo no canal, foi o "
            f"YouTube. Reporte o que a API devolver e não afirme mais que isso.")

    escolhido, _perfis, avisos_perfil, _origem = _perfil(
        provedor, chave, origem_da_chave=origem_da_chave)
    avisos.extend(avisos_perfil)
    campos[0] = ("user", escolhido)

    # Lê o arquivo inteiro na memória. Os clipes deste projeto são de dezenas de
    # MB (o do TikTok medido em 14/09 tinha 8,9 MB) e o `urlopen` da stdlib quer
    # bytes de qualquer jeito; um envio em streaming só valeria a pena para
    # arquivo que não cabe na RAM, e esse arquivo não seria um clipe vertical.
    with open(caminho, "rb") as fh:
        conteudo = fh.read()

    corpo, tipo = _multipart(campos, "video", caminho, conteudo)
    cabecalhos = dict(provedor.cabecalhos(chave))
    cabecalhos["Content-Type"] = tipo

    codigo, bruto = _http("POST", provedor.url("/upload"), corpo=corpo,
                          cabecalhos=cabecalhos, timeout=TIMEOUT_UPLOAD)
    dados = _json_ou_explica(codigo, bruto, "ao enviar o vídeo em /upload")

    # A resposta síncrona traz o resultado INTEIRO: `status: completed` e a URL.
    # Quando ela vem assim, não há o que consultar depois -- e `wait_for` sobre
    # um envio já concluído seria uma volta à rede para reler o que está aqui.
    concluido_agora = str(dados.get("status") or "").lower() == "completed"

    pedido = _primeiro(dados, ("request_id", "requestId", "id"))
    if not pedido:
        raise PostIndisponivel(
            "o intermediário respondeu HTTP "
            f"{codigo} mas sem `request_id`, e sem ele não há como acompanhar "
            "nem confirmar nada. O corpo, verbatim: " + _texto(bruto),
            codigo=codigo)
    pedido = str(pedido)

    # Guardado para `wait_for` comparar o que voltar com o que foi pedido. Vale
    # só neste processo; noutro, a comparação não acontece — e não acontecer é
    # melhor que acontecer com um palpite.
    _PEDIDOS[pedido] = {"privacidade": privacidade, "titulo": titulo,
                        "perfil": escolhido, "plataformas": list(redes)}

    return {
        "request_id": pedido,
        # O `job_id` do worker durável, que veio junto na medição de 15/09. Não
        # serve para consultar status (quem serve é o request_id), mas é o que
        # identifica o envio num suporte do provedor.
        "job_id": _primeiro(dados, ("job_id", "jobId")),
        "provedor": provedor.nome,
        "perfil": escolhido,
        "plataformas": list(redes),
        "titulo": titulo,
        "privacidade_pedida": privacidade,
        "assincrono": not concluido_agora,
        # O corpo cru da resposta síncrona, para quem chama poder fechar sem
        # uma segunda requisição. `None` quando o envio caiu na fila.
        "concluido_no_envio": dados if concluido_agora else None,
        "bytes": tamanho,
        "arquivo": caminho,
        "avisos": avisos,
        "proximo_passo": (
            "o envio voltou CONCLUÍDO na própria resposta. Abra a URL numa "
            "janela anônima: é ela, e não o `success: true`, que diz se o "
            "vídeo está público." if concluido_agora else
            f"o vídeo foi ACEITO para processamento com request_id={pedido}. "
            "Aceito não é publicado: `warden post status " + str(pedido) +
            "` pergunta de novo, e depois abra a URL numa janela anônima."),
    }


def publish_youtube(clip, titulo, descricao="", privacidade="public", shorts=None):
    """`publicar` para o YouTube só. Continua existindo porque é o caminho
    MEDIDO — 15 e 16/09/2026, envios reais, vídeo público no canal — e porque
    quem chama de fora não deveria precisar montar uma lista para pedir a única
    rede que este projeto sabe que funciona.
    """
    return publicar(clip, ["youtube"], titulo, descricao=descricao,
                    privacidade=privacidade, shorts=shorts)


def _resultado_por_plataforma(dados):
    """Extrai o resultado por rede de um corpo de status. -> dict.

    Tolerante de propósito: o corpo do `/status` do Upload-Post NÃO foi medido
    (em 15/09/2026 só `/me` e `/users` foram). Então procura-se o bloco de
    resultados nos nomes plausíveis e aceita-se tanto o formato de dicionário
    por rede quanto o de lista com um campo `platform`. O que NÃO se faz é
    inventar valor quando nada é encontrado.
    """
    bloco = _primeiro(dados, ("results", "result", "response", "platforms",
                              "data"))
    if isinstance(bloco, dict) and isinstance(bloco.get("results"), (dict, list)):
        bloco = bloco["results"]
    saida = {}
    if isinstance(bloco, dict):
        for rede, valor in bloco.items():
            saida[str(rede)] = valor if isinstance(valor, dict) else {"raw": valor}
    elif isinstance(bloco, list):
        for item in bloco:
            if isinstance(item, dict):
                rede = item.get("platform") or item.get("name") or "?"
                saida[str(rede)] = item
    return saida


def wait_for(request_id, timeout_s=300, privacidade_pedida=None):
    """Acompanha um envio assíncrono até sair de `pending`. -> dict.

    Consulta `GET /uploadposts/status?request_id=<id>` com backoff (2s, depois
    ×1,5 até 15s). Sem backoff, um envio de vídeo vira algumas centenas de
    requisições contra um teto que ninguém aqui conhece — e descobrir qual é o
    teto no meio do único envio que importava é caro.

    `timeout_s` é um TETO, não uma promessa. Estourou, devolve `status` como
    estava e `concluido: False`, dizendo que AINDA ESTÁ PROCESSANDO. Não se
    transforma "não sei" em "falhou" nem em "deu certo".

    O dicionário devolvido:

      request_id        o mesmo que entrou
      status            o último status da API: processing/completed/failed
      concluido         True só quando saiu de ESTADOS_ANDAMENTO
      sucesso           True/False pelo que a API devolveu; None se ela não diz
      plataformas       o resultado por rede, com `success` e `post_url`
      post_url          o endereço, quando a API mandou um — e só quando mandou
      platform_post_id  o id do vídeo no YouTube, para consultar `videos.list`
      prevalidacao      o que o intermediário entendeu do arquivo (w/h/fps/codec)
      privacidade       a privacidade que a RESPOSTA trouxe — MEDIDO: ela não
                        traz nenhuma, então na prática isto é None e o aviso
                        correspondente explica por quê
      esperou_s         quanto tempo se esperou de fato
      avisos            divergências e o que a resposta não confirmou
      prova             PROVA_FINAL — a janela anônima

    As duas regras de honestidade desta função:

    1. `privacidade` é o que VOLTOU. O envio real de 15/09/2026 mostrou que a
       resposta de status NÃO TEM campo de privacidade — então este campo sai
       None e um aviso registra que pedimos X e a API não confirmou nada. Ecoar
       X aqui seria exatamente a mentira que o `youtube publish` conta hoje.
    2. `post_url` e HTTP 200 não provam publicação — e isso também foi medido:
       em 15/09 os dois vieram idênticos ao de qualquer envio, e quem disse
       "público" foi a página aberta deslogada (`"isPrivate":false`) e o
       `videos.list` do YouTube. `prova` diz isso em uma linha.
    """
    chave = _chave()
    provedor = _provedor()
    pedido = str(request_id).strip()
    if not pedido:
        raise PostIndisponivel(
            "wait_for precisa do `request_id` que veio do envio; sem ele não "
            "há o que consultar.")

    if privacidade_pedida is None:
        privacidade_pedida = (_PEDIDOS.get(pedido) or {}).get("privacidade")

    comeco = _agora()
    espera = ESPERA_INICIAL
    estado = None
    dados = {}
    while True:
        codigo, bruto = _http(
            "GET", provedor.url("/uploadposts/status", {"request_id": pedido}),
            cabecalhos=provedor.cabecalhos(chave))
        dados = _json_ou_explica(
            codigo, bruto, f"ao consultar o status de request_id={pedido}")
        estado = _primeiro(dados, ("status", "state"))
        if isinstance(estado, str):
            estado = estado.strip().lower()
        elif estado is None:
            interno = dados.get("data")
            if isinstance(interno, dict):
                estado = str(_primeiro(interno, ("status", "state")) or "").lower() or None
        if estado and estado not in ESTADOS_ANDAMENTO:
            break
        decorrido = _agora() - comeco
        if decorrido + espera > timeout_s:
            # Devolve-se o que se sabe, e o que se sabe é "ainda processando".
            # Travar o agente à espera de um estado que talvez demore não é
            # melhor que dizer onde parou.
            return {
                "request_id": pedido,
                "status": estado or "pending",
                "concluido": False,
                "prevalidacao": None,
                "platform_post_id": None,
                "sucesso": None,
                "plataformas": _resultado_por_plataforma(dados),
                "por_plataforma": {},
                "post_url": None,
                "privacidade": None,
                "esperou_s": round(decorrido, 1),
                "avisos": [
                    f"o teto de {timeout_s}s acabou e a API ainda responde "
                    f"{estado or 'pending'}: o envio AINDA ESTÁ PROCESSANDO. "
                    "Isto não é falha e não é sucesso — consulte de novo com "
                    f"`wait_for({pedido!r})` daqui a pouco."],
                "prova": PROVA_FINAL,
            }
        _dorme(espera)
        espera = min(espera * ESPERA_FATOR, ESPERA_TETO)

    decorrido = round(_agora() - comeco, 1)
    plataformas = _resultado_por_plataforma(dados)

    # QUAIS REDES LER, e a ordem delas.
    #
    # Era `plataformas.get("youtube")`, fixo, de quando só havia uma rede. Com
    # três, um `post_url` fixo no YouTube devolveria `None` para um envio que
    # só foi para o TikTok — "a API não devolveu endereço" para uma resposta
    # que devolveu.
    #
    # A ordem vem do que foi PEDIDO, guardado em `_PEDIDOS` no envio, para que
    # `post_url` continue sendo o da primeira rede pedida e não o que o
    # dicionário da resposta ordenar. Noutro processo `_PEDIDOS` não existe, e
    # aí vale a ordem da resposta — o que é diferente de escolher errado.
    pedidas = [r for r in ((_PEDIDOS.get(pedido) or {}).get("plataformas") or [])
               if r in plataformas]
    nomes_redes = pedidas or sorted(plataformas)

    def _bloco(rede):
        valor = plataformas.get(rede)
        return valor if isinstance(valor, dict) else {}

    por_rede = {}
    for rede in nomes_redes:
        bloco = _bloco(rede)
        sucesso_rede = bloco.get("success")
        if sucesso_rede is not None:
            sucesso_rede = bool(sucesso_rede)
        por_rede[rede] = {
            "sucesso": sucesso_rede,
            "url": _primeiro(bloco, ("post_url", "postUrl", "url", "video_url",
                                     "link")),
            "post_id": _primeiro(bloco, ("platform_post_id", "post_id", "id")),
            "erro": _primeiro(bloco, ("error_message", "message", "error",
                                      "detail")),
        }

    # A rede PRINCIPAL: a primeira pedida. Os três campos "no singular" abaixo
    # falam dela, e continuam idênticos ao que sempre foram num envio de uma
    # rede só — que é o único formato medido.
    principal = _bloco(nomes_redes[0]) if nomes_redes else {}

    post_url = _primeiro(principal, ("post_url", "postUrl", "url", "video_url",
                                     "link"))
    if not post_url:
        post_url = _primeiro(dados, ("post_url", "postUrl"))

    privacidade = _primeiro(principal, ("privacyStatus", "privacy_status",
                                        "privacy"))
    if isinstance(privacidade, str):
        privacidade = privacidade.strip().lower()

    sucesso = principal.get("success")
    if sucesso is None:
        sucesso = dados.get("success")
    if sucesso is not None:
        sucesso = bool(sucesso)
    # Com mais de uma rede, só é sucesso quando TODAS as que responderam deram
    # certo. Uma que falha no meio de três é um envio que falhou em parte, e
    # chamar isso de sucesso esconderia exatamente a rede que precisa ser
    # reenviada.
    reprovadas = [r for r, v in por_rede.items() if v["sucesso"] is False]
    if len(por_rede) > 1 and reprovadas:
        sucesso = False
    if estado == "failed":
        # `failed` manda, mesmo que algum campo de `success` diga o contrário.
        # Uma resposta que se contradiz não vira sucesso aqui.
        sucesso = False

    avisos = []
    for rede in nomes_redes:
        if por_rede[rede]["sucesso"] is False and estado != "failed":
            avisos.append(
                f"{rede}: a API devolveu falha para esta rede. O que ela disse, "
                f"verbatim: {_texto(por_rede[rede]['erro']) if por_rede[rede]['erro'] else '(nenhuma mensagem)'}")
    if estado == "failed":
        # `error_message` primeiro: é o nome MEDIDO em 15/09/2026 (vem `null`
        # quando dá certo). Os outros continuam na lista porque um envio é uma
        # amostra e este é o campo que a pessoa mais precisa ler.
        mensagem = _primeiro(principal, ("error_message", "message", "error",
                                         "detail"))
        if not mensagem:
            mensagem = _primeiro(dados, ("error_message", "message", "error",
                                         "detail"))
        avisos.append(
            "a API devolveu `failed`: o vídeo NÃO foi publicado. O que ela "
            f"disse, verbatim: {_texto(mensagem) if mensagem else '(nenhuma mensagem)'}")
    else:
        # A comparação que este módulo existe para fazer.
        if privacidade is None:
            avisos.append(
                "a resposta NÃO diz qual é a privacidade do vídeo"
                + (f" — foi pedido {privacidade_pedida!r} e a API não "
                   "confirmou nada a respeito." if privacidade_pedida
                   else ".")
                + " Então aqui não se afirma que ele está público: quem "
                  "confirma é a janela anônima.")
        elif privacidade_pedida and privacidade != privacidade_pedida:
            avisos.append(
                f"ATENÇÃO: foi pedido {privacidade_pedida!r} e a API devolveu "
                f"{privacidade!r}. Vale o que ela devolveu, não o que foi "
                "pedido — o vídeo está como ela disse, e é isso que a janela "
                "anônima vai mostrar.")
        if not post_url:
            avisos.append(
                "a API não devolveu endereço do vídeo. Sem URL não há como "
                "conferir deslogado, que é a única prova que vale — procure o "
                "vídeo no painel do provedor ou no YouTube Studio.")

    if sucesso is None:
        avisos.append(
            f"a resposta saiu de `pending` para {estado!r} mas não trouxe um "
            "campo de sucesso reconhecível. O corpo, verbatim: "
            + _texto(json.dumps(dados, ensure_ascii=False)))

    return {
        "request_id": pedido,
        "status": estado,
        "concluido": True,
        "sucesso": sucesso,
        "plataformas": plataformas,
        # O resultado LIMPO de cada rede: sucesso, endereço, id e erro, já com
        # os nomes de campo procurados em mais de uma grafia. É o que a CLI
        # imprime quando o envio foi para mais de uma.
        "por_plataforma": por_rede,
        "post_url": post_url,
        # O id do vídeo no YouTube (`lx9J_hD7nGA` na medição). É com ele que se
        # consulta `videos.list` — que foi, de fato, um dos dois lados da prova
        # de que o clipe saiu público.
        "platform_post_id": _primeiro(principal, ("platform_post_id",
                                                  "post_id", "id")),
        # O que o INTERMEDIÁRIO entendeu do arquivo: largura, altura, fps,
        # duração e codec. Vale expor porque é a leitura dele, não a nossa: um
        # clipe que sai daqui 1080x1920 e chega lá como outra coisa é um
        # problema que aparece aqui e em nenhum outro lugar.
        "prevalidacao": _primeiro(principal, ("prevalidation_metadata",
                                              "prevalidation")),
        "privacidade": privacidade,
        "privacidade_pedida": privacidade_pedida,
        "esperou_s": decorrido,
        "avisos": avisos,
        "prova": PROVA_FINAL,
    }
