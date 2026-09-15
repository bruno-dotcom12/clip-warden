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

Configuração — tudo por ambiente, nada no repositório
─────────────────────────────────────────────────────
  WARDEN_POST_API_KEY   a chave. SEM ELA O MÓDULO FICA DESLIGADO: toda função
                        pública levanta PostIndisponivel e NENHUMA abre socket.
  WARDEN_POST_PROVIDER  `upload-post` (padrão). Outro nome: erro claro de não
                        implementado.
  WARDEN_POST_PROFILE   o `user` da API. Sem ele: se a conta tiver um perfil
                        só, é esse; mais de um, erro listando os nomes.
  WARDEN_POST_BASE_URL  para o teste apontar para um servidor local. É o que
                        torna a suíte possível sem tocar a rede.

A chave não vem de arquivo do repositório e não vem de código. Um segredo
commitado é um segredo vazado, e este é o segredo que publica no canal do dono.

Só biblioteca padrão, como o resto do projeto. O multipart é montado à mão.
A CLI não mora aqui: este arquivo é função pura, `status()`,
`publish_youtube()` e `wait_for()`.
"""
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

BASE_PADRAO = "https://api.upload-post.com/api"
PROVEDOR_PADRAO = "upload-post"

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


def _http(metodo, url, *, corpo=None, cabecalhos=None, timeout=TIMEOUT_API):
    """A ÚNICA porta para fora do processo. -> (código HTTP, corpo em bytes).

    Um erro HTTP não vira exceção aqui. O corpo de um 4xx é justamente onde a
    API explica o que houve, e jogá-lo fora para levantar um `HTTPError` seco é
    como se perde a causa e se começa a inventar uma.
    """
    pedido = urllib.request.Request(url, data=corpo, method=metodo,
                                    headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
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


def _chave():
    """A chave do ambiente, já registrada como segredo. Levanta se não houver.

    Este é o interruptor: sem a variável, o módulo está DESLIGADO e nada aqui
    abre socket. A mensagem diz ONDE colocar a chave, porque "não configurado"
    sem endereço faz a pessoa procurar num arquivo do repositório — que é
    exatamente onde ela não pode estar.
    """
    valor = (os.environ.get(ENV_CHAVE) or "").strip()
    if not valor:
        raise PostIndisponivel(
            f"não há chave do intermediário de publicação: a variável de "
            f"ambiente {ENV_CHAVE} está vazia ou não existe, e sem ela este "
            f"módulo não faz nenhuma requisição. Pegue a chave no painel do "
            f"Upload-Post e exporte-a no ambiente do agente "
            f"(`export {ENV_CHAVE}=...`, ou a chave no `environment:` do "
            f"compose). Ela NÃO vai para arquivo nenhum deste repositório.")
    _SEGREDOS.add(valor)
    return valor


def esta_configurado():
    """True se há chave no ambiente. Não abre socket, não valida a chave.

    Serve para a CLI decidir se mostra o subcomando ou o aviso de desligado sem
    precisar capturar exceção para perguntar uma coisa simples.
    """
    return bool((os.environ.get(ENV_CHAVE) or "").strip())


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
        base = (os.environ.get(ENV_BASE) or BASE_PADRAO).rstrip("/")
        endereco = base + caminho
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


def _perfil(provedor, chave, perfis=None):
    """O `user` que vai no envio. -> (nome, lista de perfis, avisos).

    Sem WARDEN_POST_PROFILE e com mais de um perfil, ISTO RECUSA em vez de
    escolher o primeiro. Escolher sozinho aqui significa publicar no canal
    errado, e um vídeo no canal errado não se desfaz com um `unset`.
    """
    avisos = []
    escolhido = (os.environ.get(ENV_PERFIL) or "").strip()
    if escolhido and perfis is None:
        # Nomeado à mão e lista não pedida: vai assim, sem consultar /users.
        # Buscar a lista aqui custaria uma requisição a mais antes de CADA
        # envio só para produzir um aviso — e pior, faria um tropeço do /users
        # derrubar um envio que teria funcionado. Quem decide se o perfil
        # existe é a API, na hora do envio. A conferência continua existindo em
        # `status()`, onde a lista já está na mão e não custa nada.
        return escolhido, [], avisos
    if perfis is None:
        perfis = provedor.perfis(chave)["perfis"]
    nomes = [p["nome"] for p in perfis if p.get("nome")]
    if escolhido:
        if nomes and escolhido not in nomes:
            # Aviso e não recusa: a lista de perfis pode estar atrasada em
            # relação ao painel, e quem nomeou o perfil à mão sabe o que quis.
            # Quem decide se o nome existe é a API, na hora do envio.
            avisos.append(
                f"{ENV_PERFIL}={escolhido!r} não está entre os perfis que a API "
                f"listou ({', '.join(nomes)}). O envio vai assim mesmo e quem "
                "recusa, se for o caso, é o intermediário.")
        return escolhido, perfis, avisos
    if not nomes:
        raise PostIndisponivel(
            "a API não listou nenhum perfil nesta conta, então não há para "
            "onde publicar. Crie um perfil no painel do Upload-Post e conecte "
            f"nele a conta do YouTube, ou aponte {ENV_PERFIL} para um perfil "
            "existente.")
    if len(nomes) > 1:
        raise PostIndisponivel(
            f"esta conta tem {len(nomes)} perfis ({', '.join(nomes)}) e nenhum "
            f"foi escolhido. Exporte {ENV_PERFIL} com o nome do que vai "
            "receber o vídeo — escolher sozinho aqui seria publicar no canal "
            "errado, e isso não se desfaz.")
    return nomes[0], perfis, avisos


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
    chave = _chave()
    provedor = _provedor()
    conta = provedor.conta(chave)
    listagem = provedor.perfis(chave)
    perfis = listagem["perfis"]

    # O perfil pode não resolver (nenhum, ou mais de um sem escolha) e isso NÃO
    # derruba o status: status é justamente o comando que a pessoa roda para
    # descobrir que precisa escolher. A frase da recusa vira aviso.
    avisos = []
    escolhido = None
    try:
        escolhido, _, avisos = _perfil(provedor, chave, perfis=perfis)
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
        "email": conta["email"],
        "plano": conta["plano"] or listagem.get("plano"),
        "limite_perfis": listagem.get("limite"),
        "perfil": escolhido,
        "perfis": perfis,
        "reauth": reauth,
        "avisos": avisos,
    }


def publish_youtube(clip, titulo, descricao="", privacidade="public", shorts=None):
    """Manda um clipe para o YouTube PELO INTERMEDIÁRIO. -> dict com request_id.

    Este é o caminho que não cai na trava de `locked as private`: quem fala com
    a YouTube Data API é o app auditado do Upload-Post, não o projeto Google
    não auditado deste repositório.

    O envio é `POST /upload`, multipart/form-data, com `async_upload=true` — a
    documentação diz que acima de 59 segundos o envio vira assíncrono de
    qualquer jeito, então pedir síncrono só criaria dois caminhos de código,
    sendo que um deles morre em timeout no primeiro clipe grande. O que volta é
    um `request_id`, e o `request_id` é o que se leva para `wait_for`.

    O que se valida ANTES de abrir socket, e por quê:

      arquivo inexistente ou de 0 byte  — subir 0 byte gasta a janela de envio e
                                          volta um erro do YouTube menos claro
                                          que esta frase;
      título vazio                      — o YouTube exige título;
      título acima de 100 caracteres    — RECUSA, não trunca. Truncar publica no
                                          canal do dono um título que ninguém
                                          escreveu;
      privacidade fora do enum          — `public`/`unlisted`/`private`, da spec
                                          OpenAPI oficial.

    `shorts`: NÃO existe interruptor de Short nesta API, e não vai ser
    inventado um aqui. Quem decide que um vídeo é Short é o YouTube, pelo
    formato do arquivo (vertical) e pela duração. Com `shorts=True` o que este
    módulo faz — e diz que fez, nos avisos — é acrescentar `#Shorts` ao fim da
    descrição, que é uma DICA, não uma garantia, e que não foi medida aqui.
    `shorts=None` (padrão) e `shorts=False` não mexem em nada.

    A privacidade que sai daqui é a PEDIDA. Este dicionário não afirma em
    momento nenhum que o vídeo ficou público: quem compara pedido com resposta
    é `wait_for`, e quem prova é a janela anônima.

    Levanta PostIndisponivel sem chave, com arquivo ou título inválidos, e
    quando o intermediário recusa — nesses casos nenhum byte é enviado.
    """
    # A ordem importa: a chave primeiro, para que "módulo desligado" seja a
    # resposta mesmo quando o caminho do arquivo também está errado. São dois
    # problemas diferentes e o de configuração é o que bloqueia tudo.
    chave = _chave()
    provedor = _provedor()

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
    if len(titulo) > TETO_TITULO:
        raise PostIndisponivel(
            f"o título tem {len(titulo)} caracteres e o YouTube aceita no "
            f"máximo {TETO_TITULO}. Este módulo NÃO corta sozinho: um título "
            "truncado vai para o canal do dono com uma frase que ninguém "
            f"escreveu. Encurte em {len(titulo) - TETO_TITULO} caractere(s) e "
            "mande de novo. Nada foi enviado.")

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

    escolhido, _perfis, avisos_perfil = _perfil(provedor, chave)
    avisos.extend(avisos_perfil)

    # Lê o arquivo inteiro na memória. Os clipes deste projeto são de dezenas de
    # MB (o do TikTok medido em 14/09 tinha 8,9 MB) e o `urlopen` da stdlib quer
    # bytes de qualquer jeito; um envio em streaming só valeria a pena para
    # arquivo que não cabe na RAM, e esse arquivo não seria um clipe vertical.
    with open(caminho, "rb") as fh:
        conteudo = fh.read()

    # Lista de pares, não dicionário: `platform[]` é repetível, e é assim que
    # um dia este mesmo envio manda YouTube e TikTok juntos.
    campos = [
        ("user", escolhido),
        ("platform[]", "youtube"),
        ("title", titulo),
        ("privacyStatus", privacidade),
        ("async_upload", "true"),
    ]
    if descricao.strip():
        campos.append(("youtube_description", descricao))

    corpo, tipo = _multipart(campos, "video", caminho, conteudo)
    cabecalhos = dict(provedor.cabecalhos(chave))
    cabecalhos["Content-Type"] = tipo

    codigo, bruto = _http("POST", provedor.url("/upload"), corpo=corpo,
                          cabecalhos=cabecalhos, timeout=TIMEOUT_UPLOAD)
    dados = _json_ou_explica(codigo, bruto, "ao enviar o vídeo em /upload")

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
                        "perfil": escolhido}

    return {
        "request_id": pedido,
        # O `job_id` do worker durável, que veio junto na medição de 15/09. Não
        # serve para consultar status (quem serve é o request_id), mas é o que
        # identifica o envio num suporte do provedor.
        "job_id": _primeiro(dados, ("job_id", "jobId")),
        "provedor": provedor.nome,
        "perfil": escolhido,
        "plataformas": ["youtube"],
        "titulo": titulo,
        "privacidade_pedida": privacidade,
        "assincrono": True,
        "bytes": tamanho,
        "arquivo": caminho,
        "avisos": avisos,
        "proximo_passo": (
            f"o vídeo foi ACEITO para processamento com request_id={pedido}. "
            "Aceito não é publicado: chame `wait_for(request_id)` para ver o "
            "que a API devolve, e depois abra a URL numa janela anônima."),
    }


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
    youtube = plataformas.get("youtube") if isinstance(plataformas.get("youtube"), dict) else {}

    post_url = _primeiro(youtube, ("post_url", "postUrl", "url", "video_url",
                                   "link"))
    if not post_url:
        post_url = _primeiro(dados, ("post_url", "postUrl"))

    privacidade = _primeiro(youtube, ("privacyStatus", "privacy_status",
                                      "privacy"))
    if isinstance(privacidade, str):
        privacidade = privacidade.strip().lower()

    sucesso = youtube.get("success")
    if sucesso is None:
        sucesso = dados.get("success")
    if sucesso is not None:
        sucesso = bool(sucesso)
    if estado == "failed":
        # `failed` manda, mesmo que algum campo de `success` diga o contrário.
        # Uma resposta que se contradiz não vira sucesso aqui.
        sucesso = False

    avisos = []
    if estado == "failed":
        # `error_message` primeiro: é o nome MEDIDO em 15/09/2026 (vem `null`
        # quando dá certo). Os outros continuam na lista porque um envio é uma
        # amostra e este é o campo que a pessoa mais precisa ler.
        mensagem = _primeiro(youtube, ("error_message", "message", "error",
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
        "post_url": post_url,
        # O id do vídeo no YouTube (`lx9J_hD7nGA` na medição). É com ele que se
        # consulta `videos.list` — que foi, de fato, um dos dois lados da prova
        # de que o clipe saiu público.
        "platform_post_id": _primeiro(youtube, ("platform_post_id",
                                                "post_id", "id")),
        # O que o INTERMEDIÁRIO entendeu do arquivo: largura, altura, fps,
        # duração e codec. Vale expor porque é a leitura dele, não a nossa: um
        # clipe que sai daqui 1080x1920 e chega lá como outra coisa é um
        # problema que aparece aqui e em nenhum outro lugar.
        "prevalidacao": _primeiro(youtube, ("prevalidation_metadata",
                                            "prevalidation")),
        "privacidade": privacidade,
        "privacidade_pedida": privacidade_pedida,
        "esperou_s": decorrido,
        "avisos": avisos,
        "prova": PROVA_FINAL,
    }
