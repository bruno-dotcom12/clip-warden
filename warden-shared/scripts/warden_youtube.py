#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Um clipe no YouTube (Shorts) sem que nenhuma senha chegue ao agente.

Este módulo é o irmão de `warden_tiktok.py` e segue as mesmas convenções: uma
única porta para a rede, exceção própria que limpa segredo na construção,
escrita de token atômica com modo 600, `status_conta()` que nunca levanta,
`progresso=None` em tudo que demora, timeout em toda requisição.

Uma convenção NÃO é seguida aqui, e de propósito: o `status_conta()` do TikTok
não toca a rede, e este toca. Ele descrevia o arquivo e chamava isso de
"conectado"; em 15/09/2026 disse "renovação automática armada" enquanto a
renovação devolvia `deleted_client`. Verificar é o ponto do comando.

A diferença que decide o desenho: aqui a autorização é OAuth 2.0 **Device
Flow** — o mesmo fluxo que uma TV usa. O agente pede um código curto, mostra
uma URL e o código, e a pessoa autoriza no celular, na conta Google dela. A
senha nunca passa por este processo, nunca fica num arquivo, nunca aparece num
log. É o contrário do que um "peça o login do usuário" faria.

Três coisas que este arquivo repete em voz alta porque mentir sobre elas seria
prometer o que a API não faz:

1. O VÍDEO SOBE TRANCADO COMO PRIVADO, E NINGUÉM DESTRANCA NO STUDIO. A
   documentação do `videos.insert` diz, palavra por palavra: "All videos
   uploaded via the videos.insert endpoint from unverified API projects created
   after 28 July 2020 will be restricted to private viewing mode." Este projeto
   do Google Cloud nunca passou pela auditoria da YouTube API Services, então
   `privacidade` tem padrão `private`, e mesmo pedindo `public` o YouTube pode
   devolver `private` — e o que esta função reporta é o que ELE devolveu, não o
   que nós pedimos.

   O que ACONTECE com esse vídeo está na Ajuda do YouTube
   (support.google.com/youtube/answer/7300965), literalmente: "For videos that
   have been locked as private due to upload via an unverified API service, you
   will not be able to appeal." E: "Unlike user-selected private videos, you
   will not be able to change the video's state until after you have
   successfully submitted the video for re-review." Ou seja: o dono NÃO muda a
   visibilidade no Studio, e não cabe recurso. A mesma página dá as duas saídas
   reais — "re-upload the video via a verified API service or via the YouTube
   app/site": ou o projeto passa pela auditoria (a YouTube API Services Audit,
   sem prazo publicado pelo Google), ou a pessoa sobe o MESMO arquivo pelo app
   ou pelo site do YouTube, à mão. Prometer "um toque no Studio" foi o que este
   arquivo dizia antes, e era falso.

   Cuidado com a confusão que custa caro: a verificação da TELA DE CONSENTIMENTO
   do OAuth (a que tira o aviso "app não verificado" e o teto de 100 usuários) é
   OUTRO processo, e passar por ela não destranca vídeo nenhum.
2. O CLIENTE OAUTH VEM DA IMAGEM, não do código-fonte. Ele é do tipo "TVs e
   dispositivos com entrada limitada", e a documentação do Google parte do
   princípio, literalmente, de que "it is assumed that the apps cannot keep
   secrets" nesse fluxo -- o par não dá acesso a canal nenhum sozinho, só serve
   para pedir um código que UMA PESSOA aprova no celular dela, na conta dela.
   Ainda assim ele NÃO fica no repositório: a proteção de segredos do GitHub
   recusa o push, e um par commitado é um push bloqueado para sempre. Entra no
   build pelo ENV, e quem constrói do código passa o seu por
   `WARDEN_YT_CLIENT_ID` / `WARDEN_YT_CLIENT_SECRET`. Impresso, nunca: é um dos
   três segredos registrados em `_SEGREDOS`.
3. SHORTS NÃO É UM ENDPOINT. Não existe "subir um Short": sobe-se um vídeo
   comum, e o YouTube o trata como Short quando ele é vertical e tem até 3
   minutos. O que esta casa entrega já é 1080x1920 e curto, então o vídeo cai
   em Shorts sozinho. Não há campo para pedir isso, e inventar um (ou colar
   `#Shorts` no título por conta própria) seria simpatia, não engenharia.

O que continua NÃO medido, e por isso não é afirmado em lugar nenhum daqui:
quanto tempo o YouTube leva para processar o vídeo depois do upload, se e
quando ele aparece na aba Shorts, quanto demora uma auditoria que nunca foi
pedida, e o comportamento do app depois dela. Este módulo devolve o `id` do
vídeo e o que o YouTube disse sobre a privacidade dele. O resto é da pessoa.

A CLI não mora aqui. Este arquivo é só função pura: `conecta()`, `token()`,
`status_conta()` e `publica()`.
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
# leem o global — trocar `warden_youtube.TOKEN_FILE` funciona.
TOKEN_FILE = os.environ.get("WARDEN_YT_TOKEN_FILE",
                            "/var/lib/hermes/warden/youtube.json")

# O cliente OAuth deste projeto, tipo "TVs e dispositivos com entrada limitada".
#
# POR QUE ISTO PODE ESTAR NUM REPOSITÓRIO PÚBLICO, e por que não é descuido:
# neste tipo de cliente o Google não trata o secret como confidencial — a
# própria documentação do fluxo diz que "it is assumed that the apps cannot
# keep secrets", que é justamente por que o fluxo existe. O par client_id +
# client_secret não abre canal nenhum: tudo que ele consegue fazer é PEDIR um
# código de 8 caracteres que uma pessoa precisa digitar e aprovar no celular
# dela. Sem esse toque humano, não há token, não há canal, não há upload. O que
# é secreto de verdade é o refresh_token que nasce DEPOIS da aprovação — e esse
# mora em TOKEN_FILE, com modo 600, e nunca é impresso.
#
# Quem usa outro projeto do Google Cloud sobrescreve pelo ambiente.
# Fora do código-fonte, dentro da IMAGEM. Medido em 15/09/2026: a proteção de
# segredos do GitHub recusa o push de um repositório que os carregue, e ela está
# certa em recusar -- "o Google diz que este tipo não é confidencial" é um
# argumento sobre RISCO, não sobre higiene, e um par commitado num repo público
# vira um push bloqueado toda vez, para sempre.
#
# Então eles entram no build, do segredo do CI para o ENV da imagem, e o
# repositório não os contém. Quem instala puxa a imagem publicada e não
# configura nada; quem constrói do código passa os próprios, ou conecta pelo
# TikTok e entrega o arquivo. `status_conta` diz qual dos dois é o caso em vez
# de falhar no meio de um envio.
CLIENT_ID = os.environ.get("WARDEN_YT_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("WARDEN_YT_CLIENT_SECRET", "")


def _cliente_configurado():
    """Este build tem com que falar com o Google? Sim ou não, sem levantar."""
    return bool(CLIENT_ID and CLIENT_SECRET)

URL_DEVICE = "https://oauth2.googleapis.com/device/code"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_UPLOAD = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
URL_CANAL = ("https://www.googleapis.com/youtube/v3/channels"
             "?part=snippet&mine=true")

# `urn:ietf:params:oauth:grant-type:device_code`, literal, é o que o Google
# documenta para o polling. Uma letra fora do lugar aqui vira `invalid_request`
# sem explicação, então ele é constante e não string solta no meio do código.
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"
GRANT_REFRESH = "refresh_token"

# Os dois escopos, separados por espaço, como o Google pede.
#
# `youtube.upload` é o mínimo para subir. `youtube.readonly` existe por um
# motivo só: perguntar de quem é o canal que acabou de ser autorizado, para a
# conexão poder dizer "conectado ao canal X" em vez de "conectado". Uma pessoa
# com três contas Google no celular escolhe a errada com facilidade, e descobrir
# isso só depois de publicar é caro. Sem o readonly não há como conferir.
#
# DIVERGÊNCIA NÃO RESOLVIDA, registrada aqui em vez de "consertada" no escuro:
#
#   - A doc do Device Flow
#     (developers.google.com/identity/protocols/oauth2/limited-input-device)
#     lista, entre os escopos permitidos NESSE fluxo, só `youtube` e
#     `youtube.readonly`. `youtube.upload` NÃO está na lista dela.
#   - A medição de 15/09/2026 (commit acb379e), numa conta Google real, diz o
#     contrário: o device flow pediu estes dois escopos, a pessoa aprovou, e o
#     Google concedeu OS DOIS — o `scope` que voltou no token trazia
#     `youtube.upload`.
#
# Duas fontes, uma delas é o comportamento observado. O escopo FICA como está:
# trocar por `youtube` (que é bem mais amplo e pede mais da pessoa na tela de
# consentimento) por causa de uma lista de documentação seria alargar permissão
# em cima de um papel, contra uma medição. Se um dia o Google recusar
# `invalid_scope` no `device/code`, a resposta está aqui e a troca é uma linha.
ESCOPOS = ("https://www.googleapis.com/auth/youtube.upload "
           "https://www.googleapis.com/auth/youtube.readonly")
ESCOPO_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"

# O access_token do Google dura 1 hora (`expires_in: 3599` na resposta). O
# refresh_token de um app **publicado em produção** não tem prazo: o prazo de 7
# dias que muita gente cita é dos apps em modo "Teste", e é por isso que este
# está publicado, mesmo sem verificação. Ele morre se a pessoa revogar o acesso
# em myaccount.google.com, e aí a recusa é `invalid_grant`.
VIDA_ACCESS = 3600

# Renova quando falta isto ou menos. Cinco minutos porque o que vem logo depois
# é um upload de arquivo: um token que vencia em trinta segundos passava no
# teste e morria no meio do PUT.
MARGEM_RENOVACAO = 300

TIMEOUT_API = 30          # device/code, token, channels: pedidos de alguns KB
TIMEOUT_UPLOAD = 600      # o PUT do arquivo, num link doméstico

# O intervalo de polling vem do Google (`interval`, normalmente 5s). Este é só
# o padrão de quando ele não vier.
INTERVALO_PADRAO = 5

# Quanto aumentar o intervalo a cada `slow_down`, e o teto dele. O Google diz
# para diminuir a frequência sem dizer quanto; cinco segundos por vez é
# conservador, e o teto existe para o intervalo não crescer até passar do prazo
# do próprio código.
PASSO_SLOW_DOWN = 5
TETO_INTERVALO = 60

# Limites de metadado do YouTube. São documentados e a API recusa com
# `invalidTitle` / `invalidDescription` — conferir aqui troca uma recusa em
# inglês, depois de subir o arquivo inteiro, por uma frase em português antes de
# gastar um byte de upload.
TETO_TITULO = 100
TETO_DESCRICAO = 5000

# O YouTube recusa `<` e `>` em título e descrição (é ele que monta HTML com
# isso). A recusa dele é `invalidTitle`, que não explica nada.
PROIBIDOS_NO_TEXTO = ("<", ">")

PRIVACIDADES = ("private", "unlisted", "public")

# O arquivo inteiro vai num PUT só. O upload resumável do Google permite mandar
# em pedaços e retomar de onde parou, e isso NÃO está implementado aqui: um
# clipe vertical de até 3 minutos tem dezenas de megabytes, e inventar retomada
# que ninguém nesta casa nunca viu funcionar seria código não medido no caminho
# de entrega. Acima deste teto o módulo recusa em voz alta em vez de tentar
# carregar o arquivo inteiro na memória.
TETO_ARQUIVO = 512 * 1024 * 1024

# O que este projeto entrega. O YouTube aceita muito mais formato do que isto.
TIPOS = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}

# Shorts, e por que não há nada a fazer a respeito no código.
#
# Não existe endpoint de Short nem campo "isShort": sobe-se um vídeo comum e o
# YouTube o classifica como Short quando ele é VERTICAL e tem ATÉ 3 MINUTOS. O
# que a esteira desta casa entrega é 1080x1920 com menos de 3 minutos, então a
# classificação acontece sozinha. Este texto vai junto do resultado porque a
# primeira pergunta depois de publicar é sempre "por que não apareceu em
# Shorts?", e a resposta quase sempre é duração ou proporção.
SOBRE_SHORTS = (
    "Não existe 'subir um Short': o YouTube classifica como Short o vídeo que é "
    "vertical e tem até 3 minutos. O que esta esteira entrega já é 1080x1920 e "
    "curto, então ele cai em Shorts sozinho — não há campo para pedir isso.")

# E por que este texto NÃO diz quantos toques. Ele já disse "é um toque, e
# ninguém precisa reenviar nada", e isso foi publicado uma vez: era falso. A
# Ajuda do YouTube (support.google.com/youtube/answer/7300965) diz que o vídeo
# trancado por upload de API não auditada não aceita recurso e não muda de
# estado até passar por re-review. A trava não é a visibilidade que a pessoa
# escolhe; é outra coisa, com o mesmo nome. Trocar "um toque" por "três toques"
# seria repetir o erro com outro número — então aqui não há número nenhum.
AVISO_PRIVADO = (
    "O vídeo subiu TRANCADO como privado e só você o vê. Isto não é escolha "
    "nossa nem uma caixinha que ficou desmarcada: o YouTube tranca assim todo "
    "vídeo enviado por um app que ainda não passou pela auditoria dele — a "
    "documentação diz 'All videos uploaded via the videos.insert endpoint from "
    "unverified API projects created after 28 July 2020 will be restricted to "
    "private viewing mode'. Essa trava NÃO se desfaz no YouTube Studio e não "
    "cabe recurso: a Ajuda do YouTube "
    "(support.google.com/youtube/answer/7300965) diz, sobre vídeos trancados "
    "por upload de API não verificada, 'you will not be able to appeal' e 'you "
    "will not be able to change the video's state'. Para o clipe ficar público "
    "agora, suba o MESMO arquivo pelo app ou pelo site do YouTube, pela sua "
    "conta — é o caminho que a própria Ajuda indica ('re-upload the video via "
    "a verified API service or via the YouTube app/site'). Na primeira vez o "
    "YouTube pode pedir para criar o canal antes de aceitar o envio: uma conta "
    "Google nova não vem com canal.")

# A versão curta do AVISO_PRIVADO, para o fim de uma linha de `status`. Ela
# repete a trava e a saída dela; o que não pode é repetir só "sobe privado", que
# soa como caixinha desmarcada.
# Começa com quebra de linha porque o que vem antes dele às vezes termina em
# ponto final (a receita de um erro do Google) e às vezes no meio de uma frase.
# Um "; " grudado num ponto final vira "no celular.; lembre que", que é o tipo
# de costura que faz a linha parecer gerada em vez de escrita.
_LEMBRETE_TRANCADO = (
    "\nLembre que o vídeo sobe TRANCADO como privado enquanto este app não "
    "passar pela auditoria do YouTube, e que essa "
    "trava não se desfaz no Studio — para publicar agora, suba "
    "o mesmo arquivo pelo app ou site do YouTube.")

COMO_AUTORIZAR = (
    "Abra {url} no celular (ou em qualquer navegador já logado na conta do "
    "canal) e digite o código: {codigo}\n"
    "A senha do Google NÃO passa por aqui: quem pergunta é o Google, na tela "
    "dele. Este agente só recebe a autorização depois que você aprova.\n"
    "O Google vai avisar que o app não é verificado — é este app mesmo. Toque "
    "em Avançado / Continuar para autorizar.\n"
    "Esse aviso da tela de consentimento é OUTRA coisa, e não é o motivo de o "
    "vídeo subir trancado como privado: quem tranca é a auditoria da YouTube "
    "API Services, que este projeto não fez. Passar pela verificação do "
    "consentimento não destrancaria vídeo nenhum.")

# Uma frase por motivo de recusa da API do YouTube, dizendo o que a PESSOA faz.
# O texto do Google vai junto, verbatim, sempre: o diagnóstico é dele.
RECEITAS = {
    "quotaExceeded": (
        "A cota diária do projeto no Google Cloud acabou. O padrão é 10.000 "
        "unidades por dia e cada upload custa 1.600, o que dá cerca de SEIS "
        "vídeos por dia — e consultas também consomem. A cota zera à meia-noite "
        "no horário do Pacífico. Não há o que consertar no clipe: é esperar, ou "
        "pedir aumento de cota no console do Google Cloud."),
    "uploadLimitExceeded": (
        "O CANAL bateu no limite de uploads do YouTube (é do canal, não do "
        "nosso projeto, e ele é mais apertado em canal novo ou sem número de "
        "telefone confirmado). Espere um dia e mande de novo."),
    "youtubeSignupRequired": (
        "A conta Google que você autorizou NÃO tem canal no YouTube. Abra "
        "youtube.com nessa conta, crie o canal, e refaça a conexão — o token "
        "atual é de uma conta que não pode receber vídeo."),
    "forbidden": (
        "O YouTube recusou a operação nesta conta. Confira se a conta "
        "autorizada é mesmo a do canal onde o clipe deve entrar; uma pessoa com "
        "várias contas Google no celular escolhe a errada com facilidade."),
    "insufficientPermissions": (
        "O token não tem o escopo de upload. Refaça a conexão: ela pede "
        "`youtube.upload` e `youtube.readonly`, e é preciso deixar as duas "
        "marcadas na tela do Google."),
    "authError": (
        "O YouTube disse que a credencial não vale. O agente renova sozinho e "
        "tenta mais uma vez; se este erro chegou até você, a renovação também "
        "falhou — refaça a conexão."),
    "invalidTitle": (
        "O YouTube recusou o título. Ele aceita até {teto_titulo} caracteres e "
        "NÃO aceita os sinais `<` e `>`."),
    "invalidDescription": (
        "O YouTube recusou a descrição. Ela aceita até {teto_descricao} "
        "caracteres e não aceita os sinais `<` e `>`."),
    "invalidTags": (
        "O YouTube recusou as tags. A soma dos caracteres de todas elas tem "
        "limite (cerca de 500), e uma tag sozinha não pode ser gigante."),
    "invalidCategoryId": (
        "O YouTube recusou a categoria. Este módulo NÃO manda categoria "
        "nenhuma, de propósito — se este erro apareceu, alguém acrescentou um "
        "`categoryId` ao corpo do envio."),
    "mediaBodyRequired": (
        "O YouTube não recebeu os bytes do vídeo. O arquivo pode ter sido "
        "trocado no meio do caminho; confira o caminho e mande de novo."),
    "failedPrecondition": (
        "O YouTube recusou o pedido nas condições atuais da conta. A palavra "
        "dele está acima; não há diagnóstico nosso para acrescentar."),
}

# O endpoint de OAuth usa OUTRO envelope: `error` é uma STRING no topo, com
# `error_description` ao lado, e não o `{"error": {"code": ..., "errors": [...]}}
# da API do YouTube. Por isso tem tabela própria e função própria de leitura.
RECEITAS_OAUTH = {
    "invalid_grant": (
        "O refresh_token não vale mais: o acesso foi revogado em "
        "myaccount.google.com/permissions, ou a senha da conta mudou. Num app "
        "publicado em produção ele não vence por tempo, então não é isso. Não "
        "há o que renovar: rode `warden youtube connect` de novo e regrave "
        "{arquivo}."),
    "deleted_client": (
        "O cliente do Google que autorizou esta conta foi APAGADO (ou trocado). "
        "O `refresh_token` em {arquivo} foi emitido por aquele cliente e não "
        "vale mais para nenhum outro — não há o que renovar, e tentar de novo "
        "vai dar exatamente isto. Rode `warden youtube connect` de novo, com a "
        "credencial nova, e a pessoa autoriza uma vez no celular."),
    "invalid_client": (
        "O Google não reconheceu este cliente OAuth: o par em "
        "`WARDEN_YT_CLIENT_ID` / `WARDEN_YT_CLIENT_SECRET` não é o que emitiu o "
        "token que está em {arquivo}. Ou o ambiente está com a credencial "
        "errada — confira contra o console do Google Cloud — ou o cliente foi "
        "trocado, e aí o token antigo morreu junto: rode `warden youtube "
        "connect` de novo com a credencial nova."),
    "unauthorized_client": (
        "Este cliente OAuth não pode usar este fluxo. O cliente precisa ser do "
        "tipo 'TVs e dispositivos com entrada limitada'; um cliente Web ou "
        "Desktop recusa o device flow com esta palavra."),
    "invalid_request": (
        "O Google não entendeu o pedido. Confira se o `client_id` está inteiro "
        "(ele termina em `.apps.googleusercontent.com`) e sem espaço sobrando."),
    "access_denied": (
        "A pessoa RECUSOU a autorização na tela do Google — ou fechou a tela do "
        "aviso de app não verificado sem tocar em Avançado / Continuar. Nada "
        "foi conectado. Rode a conexão de novo e, na tela do aviso, toque em "
        "Avançado e depois em 'Acessar (não seguro)'."),
    "expired_token": (
        "O código expirou antes de alguém digitá-lo (ele costuma durar 15 "
        "minutos). Nada foi conectado e nada foi perdido: rode a conexão de "
        "novo, com o celular já na mão, e digite o código novo."),
    "admin_policy_enforced": (
        "A conta é de um Google Workspace cujo administrador bloqueia este app. "
        "Use uma conta Google comum, ou peça ao administrador para liberar o "
        "cliente OAuth."),
}

# ROTAÇÃO DE CREDENCIAL MATA TODO refresh_token JÁ EMITIDO.
#
# Medido em 15/09/2026, 16:10 BRT, no container em execução. O dono rotacionou o
# cliente OAuth (o antigo tinha vazado dentro da imagem pública) e apagou o
# antigo. Com o `client_id` guardado em /var/lib/hermes/warden/youtube.json e o
# secret novo do ambiente:
#
#     POST https://oauth2.googleapis.com/token  (grant_type=refresh_token)
#     → HTTP 401
#       {"error": "deleted_client",
#        "error_description": "The OAuth client was deleted."}
#
# E o `warden youtube status` respondeu, no mesmo minuto:
#
#     connected -- conectado ao canal Pod Cortes, acesso vencido ou a menos de
#     5 min de vencer — e renova sozinho no próximo envio, sem você; renovação
#     automática armada
#
# Três afirmações, as três falsas, e nenhuma delas verificada: ele leu o disco e
# descreveu o disco. É o mesmo defeito que `warden delivered` já corrigiu nesta
# casa ("antes, este comando acreditava no modelo").
#
# O refresh_token é emitido POR um cliente e não vale para nenhum outro. Quem
# rotaciona credencial precisa reconectar, e o comando tem que dizer isso
# sozinho — por isso `status_conta` passou a EXERCITAR a credencial em vez de
# descrever o arquivo, e por isso estes nomes têm tabela própria.
CREDENCIAL_MORTA = ("deleted_client", "invalid_client", "invalid_grant",
                    "unauthorized_client")

# Segredos vistos neste processo, para nunca vazarem numa mensagem. São TRÊS:
# o access_token (1 hora), o refresh_token (que não vence e por isso vaza pior)
# e o client_secret. O `device_code` entra também enquanto o fluxo corre — ele
# é um segredo curto que resgata um token. O `user_code` NÃO entra: ele existe
# para ser mostrado.
_SEGREDOS = set()


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
    """A ÚNICA porta para a rede. -> (código HTTP, corpo em bytes, cabeçalhos).

    Três valores, e não dois como no irmão TikTok, por uma razão só: o upload
    resumável do Google devolve a sessão no cabeçalho `Location`, e o corpo
    dessa resposta é vazio. Jogar os cabeçalhos fora aqui deixaria o módulo sem
    para onde mandar o arquivo. As chaves vêm em minúsculas porque cabeçalho
    HTTP não diferencia maiúscula e depender disso é um defeito esperando o dia
    em que o Google escrever `location`.

    Um erro HTTP não é exceção aqui: o Google manda o JSON com o motivo dentro
    de um 4xx, e esse JSON é exatamente a informação que a pessoa precisa ler —
    inclusive `authorization_pending`, que chega como HTTP 428 e é o caminho
    NORMAL do device flow, não um erro.

    Toda a suíte troca esta função. Nada mais aqui abre socket.
    """
    pedido = urllib.request.Request(url, data=corpo, method=metodo,
                                    headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return (resposta.getcode(), resposta.read(),
                    {k.lower(): v for k, v in resposta.headers.items()})
    except urllib.error.HTTPError as exc:
        return (exc.code, exc.read(),
                {k.lower(): v for k, v in (exc.headers or {}).items()})
    # `codigo="sem_rede"` nos dois: quem chama precisa separar "o Google
    # recusou" de "ninguém atendeu", e essas duas coisas mandam fazer coisas
    # opostas. É o que deixa o `status` dizer "não consegui verificar" em vez de
    # afirmar que a conexão quebrou — ou, pior, que ela está boa.
    except urllib.error.URLError as exc:
        raise YouTubeIndisponivel(
            f"não deu para falar com {url}: {exc.reason}. Isto é rede desta "
            "máquina, não recusa do YouTube — confira a conexão e mande de novo.",
            codigo="sem_rede")
    except OSError as exc:
        raise YouTubeIndisponivel(
            f"não deu para falar com {url}: {type(exc).__name__}: {exc}",
            codigo="sem_rede")


# ──────────────────────────────────────────────────────────────── o segredo

def _limpa(texto):
    """Apaga de um texto qualquer segredo que este processo já leu.

    Cinto e suspensório. As mensagens daqui são montadas para não conter
    segredo, mas várias repassam o corpo de resposta de um servidor, e um dia
    algum servidor vai ecoar o que recebeu — o endpoint de token do Google, por
    exemplo, repete o `client_id` dentro do `error_description` de alguns erros.
    """
    texto = str(texto)
    for segredo in _SEGREDOS:
        if segredo and len(segredo) >= 8:
            texto = texto.replace(segredo, "<segredo oculto>")
    return texto


class YouTubeIndisponivel(RuntimeError):
    """Sem token, token expirado, escopo errado, ou o YouTube recusou.

    A mensagem passa por `_limpa` na construção, e não no ponto de uso. É a
    única garantia que não depende de quem escreveu a próxima linha de código
    lembrar da regra.

    `codigo` é o motivo que o Google deu, quando houve um: o `reason` da API do
    YouTube (`quotaExceeded`, `authError`, ...) ou o `error` do OAuth
    (`invalid_grant`, `access_denied`, ...). Existe para o código decidir sem
    reler a frase — `authError` é a única recusa que vale tentar de novo depois
    de renovar o token, e ler isso de dentro de uma mensagem em português seria
    frágil e feio.

    `http` é o status da resposta, quando houve resposta. O device flow precisa
    dele porque o Google devolve o MESMO `slow_down` em situações diferentes.
    """

    def __init__(self, mensagem, codigo=None, http=None):
        super().__init__(_limpa(mensagem))
        self.codigo = codigo
        self.http = http


def _guarda(*valores):
    """Registra segredos para `_limpa` nunca deixá-los sair numa mensagem."""
    for valor in valores:
        if isinstance(valor, str) and len(valor.strip()) >= 8:
            _SEGREDOS.add(valor.strip())


def _guarda_segredos(dados):
    """Registra os segredos que vieram do arquivo do token.

    O refresh_token é o pior deles: num app publicado em produção ele não vence,
    e sozinho renova o access_token para sempre. Vazá-lo é entregar o canal.
    """
    for chave in ("access_token", "refresh_token", "client_secret",
                  "device_code"):
        _guarda(dados.get(chave))


def _cliente():
    """(client_id, client_secret) do app, já registrados como segredo.

    Lê os globais do módulo (que nasceram do ambiente no import) em vez de ler
    o ambiente aqui, para o teste poder trocar `warden_youtube.CLIENT_ID` do
    mesmo jeito que troca `TOKEN_FILE`.
    """
    ident = (CLIENT_ID or "").strip()
    segredo = (CLIENT_SECRET or "").strip()
    if not ident or not segredo:
        raise YouTubeIndisponivel(
            "não há cliente OAuth configurado: `WARDEN_YT_CLIENT_ID` e "
            "`WARDEN_YT_CLIENT_SECRET` estão vazios e os valores embutidos no "
            "módulo foram removidos. Sem o par de cliente não há como nem "
            "PEDIR o código que a pessoa aprova no celular.")
    # O client_secret entra na lista de censura aqui, e não no import, porque o
    # teste limpa `_SEGREDOS` entre casos: se ele só fosse registrado uma vez,
    # o segundo teste rodaria com o segredo desprotegido e ninguém notaria.
    _guarda(segredo)
    return ident, segredo


def _modo_frouxo(caminho):
    """Aviso em uma linha se o arquivo do token for legível por outros, ou None.

    Não recusa. Um token com modo 0644 continua funcionando, e travar a
    publicação por causa disso trocaria um problema de segurança por um clipe
    não entregue; o que resolve é a pessoa saber, e o aviso sobe com o resultado.
    """
    try:
        modo = os.stat(caminho).st_mode
    except OSError:
        return None
    frouxo = stat.S_IMODE(modo) & 0o077
    if not frouxo:
        return None
    return (f"{caminho} está com modo {stat.S_IMODE(modo):04o}: qualquer conta "
            f"nesta máquina lê o seu token do YouTube. Corrija com "
            f"`chmod 600 {caminho}`.")


# ──────────────────────────────────────────────────────────── o arquivo

def _le_arquivo():
    """O JSON do token, já com os segredos registrados para nunca vazarem.

    Levanta YouTubeIndisponivel com instrução em cada jeito de o arquivo estar
    errado, porque "nunca conectou" e "arquivo corrompido" mandam a pessoa fazer
    coisas diferentes e um erro genérico faz ela tentar as duas.
    """
    caminho = TOKEN_FILE
    if not os.path.exists(caminho):
        raise YouTubeIndisponivel(
            f"não há conexão com o YouTube em {caminho}. Rode a conexão uma vez: "
            "ela mostra um código, você digita no celular, e pronto — a senha do "
            "Google não passa por aqui.")
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise YouTubeIndisponivel(
            f"{caminho} existe mas não deu para ler como JSON "
            f"({type(exc).__name__}). Apague o arquivo e rode a conexão de novo; "
            "são trinta segundos e um código digitado no celular.")
    if not isinstance(dados, dict):
        raise YouTubeIndisponivel(
            f"{caminho} não é um objeto JSON. Apague o arquivo e rode a conexão "
            "de novo.")
    _guarda_segredos(dados)
    bruto = dados.get("access_token")
    if not isinstance(bruto, str) or not bruto.strip():
        raise YouTubeIndisponivel(
            f"{caminho} não tem a chave `access_token`. Apague o arquivo e rode "
            "a conexão de novo.")
    return dados


def _instante(valor):
    """Um ponto no tempo em segundos unix, a partir de número OU de ISO-8601.

    `None` para qualquer outra coisa — a ausência de data é informação, e fingir
    uma seria o começo do chute.
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
    """O carimbo que vai para o arquivo. UTC, fuso explícito, sem microsegundo."""
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="seconds")


def _prazo(dados, absoluto, duracao):
    """Segundos que faltam para `absoluto` vencer, ou None quando não dá saber.

    `expires_in` é DURAÇÃO, não data: sozinha não diz nada, porque ninguém sabe
    quando o arquivo foi escrito. Então só se calcula com âncora — a data
    absoluta, ou um instante de emissão ao lado da duração. Sem âncora, None:
    não sabemos, e não sabemos é diferente de está bom. É por isso que toda
    escrita daqui grava a data absoluta.
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
    """True só quando o arquivo AFIRMA um escopo e o de upload não está nele.

    Arquivo sem a chave `scope` não é arquivo sem escopo: é arquivo que não
    contou. Chutar que está errado manda a pessoa refazer uma conexão que talvez
    esteja perfeita.
    """
    escopos = dados.get("scope")
    if not isinstance(escopos, str) or not escopos.strip():
        return False
    return ESCOPO_UPLOAD not in escopos.replace(",", " ").split()


def _pode_renovar(dados):
    """Tem refresh_token no arquivo E par de cliente configurado."""
    refresh = dados.get("refresh_token")
    if not isinstance(refresh, str) or not refresh.strip():
        return False
    return bool((CLIENT_ID or "").strip() and (CLIENT_SECRET or "").strip())


def _grava(dados):
    """Reescreve o arquivo do token inteiro, atômico e com modo 600.

    Atômico porque o agente pode morrer no meio — container reiniciado, máquina
    desligada — e um arquivo de token pela metade é uma pessoa sem canal até ela
    descobrir por quê. Escreve num temporário do MESMO diretório (os.replace só
    é atômico dentro de um sistema de arquivos), força para o disco e troca.

    O modo 600 nasce com o arquivo, via O_CREAT com 0o600: criar frouxo e
    apertar depois deixa uma janela em que qualquer conta da máquina lê o
    refresh_token, que num app em produção não vence nunca.
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


def _grava_ou_explica(dados, oque):
    """`_grava`, com a frase que explica o pior erro possível deste módulo."""
    try:
        _grava(dados)
    except OSError as exc:
        # O token existe do lado do Google e só está na memória deste processo.
        # Dizer isso é o que evita a pessoa passar uma hora achando que o
        # problema é outro — e, no caso da conexão, refazer o device flow por
        # nada quando o que falta é um `mkdir`.
        raise YouTubeIndisponivel(
            f"a {oque} deu certo no Google mas NÃO deu para gravar {TOKEN_FILE} "
            f"({type(exc).__name__}: {exc}). O token está só na memória deste "
            "processo e vai embora quando ele terminar. Conserte a permissão do "
            "diretório e rode de novo.")


# ────────────────────────────────────────────────────── ler o que o Google diz

def _cru(bruto):
    """O corpo da resposta como texto, SEM limpeza. Só para dar de comer ao JSON.

    A limpeza de segredo é para o que um humano lê, nunca para o que o programa
    interpreta. Misturar as duas coisas já custou um defeito no módulo irmão: o
    endpoint de token devolve o refresh_token, e quando ele volta IGUAL ao que
    foi enviado o segredo já está registrado — então o corpo chegava ao
    `json.loads` com a censura no lugar do token, e era isso que ia para o
    disco. Um arquivo com um refresh_token apagado é uma conta perdida em
    silêncio.
    """
    if isinstance(bruto, bytes):
        return bruto.decode("utf-8", "replace")
    return bruto if bruto is not None else ""


def _texto(bruto):
    """O mesmo corpo, limpo, para caber numa mensagem que alguém vai ler."""
    return _limpa(_cru(bruto))


def _json_oauth(codigo, bruto, oque):
    """-> (dados, nome_do_erro, descrição) do endpoint de OAuth, SEM levantar.

    Não levanta em erro de propósito: no device flow o "erro"
    `authorization_pending` chega como HTTP 428 e é o caminho normal — a pessoa
    ainda está pegando o celular. Quem decide o que é fatal é o laço de polling,
    que sabe a diferença. Só corpo ilegível vira exceção aqui.
    """
    cru, texto = _cru(bruto), _texto(bruto)
    try:
        dados = json.loads(cru) if cru.strip() else {}
    except ValueError:
        raise YouTubeIndisponivel(
            f"não deu para {oque}: o Google respondeu HTTP {codigo} com algo que "
            f"não é JSON: {texto[:400] or '(corpo vazio)'}", http=codigo)
    if not isinstance(dados, dict):
        raise YouTubeIndisponivel(
            f"não deu para {oque}: resposta HTTP {codigo} inesperada: "
            f"{texto[:400]}", http=codigo)
    erro = dados.get("error")
    if isinstance(erro, dict):
        # Nem sempre é string: o mesmo host serve a API do YouTube, cujo erro é
        # objeto. Aceitar os dois formatos é o que impede uma recusa de passar
        # por sucesso no dia em que o Google unificar os envelopes.
        nome = str(erro.get("status") or erro.get("message") or "").strip()
        descricao = str(erro.get("message") or "")
    else:
        nome = erro.strip() if isinstance(erro, str) else ""
        descricao = str(dados.get("error_description") or "")
    return dados, nome, descricao


def _erro_oauth(nome, descricao, codigo_http, oque):
    """A exceção de uma recusa do OAuth, com a palavra do Google primeiro."""
    linha = (f"o Google recusou {oque}. Ele disse, palavra por palavra: "
             f"`{nome}`: {descricao or '(sem descrição)'}")
    receita = RECEITAS_OAUTH.get(nome)
    if receita:
        linha += "\n" + receita.format(arquivo=TOKEN_FILE)
    else:
        linha += ("\nNão há receita conhecida para este código aqui. O que está "
                  "acima é a palavra do Google, não um diagnóstico nosso.")
    return YouTubeIndisponivel(linha, codigo=nome, http=codigo_http)


def _resposta_api(codigo, bruto, oque):
    """O JSON de uma resposta da API do YouTube, ou YouTubeIndisponivel.

    O envelope de erro aqui é `{"error": {"code": 403, "message": "...",
    "errors": [{"reason": "quotaExceeded", "message": "..."}]}}` — o `reason` do
    primeiro item é o que vale para decidir, e é o que vira `exc.codigo`.

    A regra do repo que este bloco existe para cumprir: NUNCA inventar causa. O
    que vai na mensagem é o `reason` e a `message` como o Google os escreveu. A
    frase de receita vem depois, separada, e para motivo desconhecido não há
    frase nenhuma — porque não sabemos.
    """
    cru, texto = _cru(bruto), _texto(bruto)
    try:
        dados = json.loads(cru) if cru.strip() else {}
    except ValueError:
        raise YouTubeIndisponivel(
            f"não deu para {oque}: o YouTube respondeu HTTP {codigo} com algo "
            f"que não é JSON: {texto[:400] or '(corpo vazio)'}", http=codigo)
    if not isinstance(dados, dict):
        raise YouTubeIndisponivel(
            f"não deu para {oque}: resposta HTTP {codigo} inesperada: "
            f"{texto[:400]}", http=codigo)
    erro = dados.get("error")
    if isinstance(erro, dict) or not 200 <= codigo < 300:
        erro = erro if isinstance(erro, dict) else {}
        itens = erro.get("errors")
        primeiro = itens[0] if isinstance(itens, list) and itens \
            and isinstance(itens[0], dict) else {}
        motivo = str(primeiro.get("reason") or erro.get("status") or "").strip()
        mensagem = str(primeiro.get("message") or erro.get("message")
                       or texto[:400] or "(sem mensagem)")
        linha = (f"o YouTube recusou {oque} (HTTP {codigo}). Ele disse, palavra "
                 f"por palavra: `{motivo or 'sem motivo nomeado'}`: {mensagem}")
        receita = RECEITAS.get(motivo)
        if receita:
            linha += "\n" + receita.format(teto_titulo=TETO_TITULO,
                                           teto_descricao=TETO_DESCRICAO)
        else:
            linha += ("\nNão há receita conhecida para este motivo aqui. O que "
                      "está acima é a palavra do Google, não um diagnóstico "
                      "nosso — procure o motivo na documentação de erros da "
                      "YouTube Data API.")
        raise YouTubeIndisponivel(linha, codigo=motivo or None, http=codigo)
    return dados


# ───────────────────────────────────────────────────────────── device flow

def _pede_codigo():
    """Passo 1: pede ao Google o código que a pessoa vai digitar. -> dict.

    `POST https://oauth2.googleapis.com/device/code`, form-urlencoded, com
    `client_id` e `scope`. Volta `device_code`, `user_code`, `verification_url`,
    `expires_in` e `interval`.
    """
    ident, _ = _cliente()
    corpo = urllib.parse.urlencode({"client_id": ident,
                                    "scope": ESCOPOS}).encode("utf-8")
    codigo, bruto, _cab = _http(
        "POST", URL_DEVICE, corpo=corpo,
        cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT_API)
    dados, nome, descricao = _json_oauth(codigo, bruto, "pedir o código")
    if nome:
        raise _erro_oauth(nome, descricao, codigo, "pedir o código de conexão")
    if not 200 <= codigo < 300:
        raise YouTubeIndisponivel(
            f"não deu para pedir o código: HTTP {codigo}, e a resposta não "
            f"trouxe `error`: {_texto(bruto)[:400] or '(corpo vazio)'}",
            http=codigo)
    device_code = dados.get("device_code")
    user_code = dados.get("user_code")
    # `verification_url` é como o Google chama; o RFC 8628 chama de
    # `verification_uri`. Aceitar os dois custa uma linha e evita uma conexão
    # que morre no dia em que ele resolver seguir o RFC ao pé da letra.
    url = dados.get("verification_url") or dados.get("verification_uri")
    if not device_code or not user_code or not url:
        raise YouTubeIndisponivel(
            "o Google respondeu o pedido de código sem erro e sem "
            f"device_code/user_code/verification_url. Resposta: {sorted(dados)!r}",
            http=codigo)
    _guarda(device_code)
    return dados


def _espera_autorizacao(device_code, *, espera, intervalo, prazo_codigo,
                        progresso):
    """Passo 2: pergunta ao Google, de tempos em tempos, se a pessoa aprovou.

    Devolve o payload do token. Levanta quando a pessoa recusou, quando o código
    expirou, ou quando estourou o teto de espera — nunca fica preso.

    Os quatro nomes de erro que o Google documenta, e o que cada um significa
    aqui:

      authorization_pending  a pessoa ainda não aprovou. É o caminho NORMAL;
                             chega como HTTP 428 e não é motivo de barulho.
      slow_down              estamos perguntando rápido demais. O Google não diz
                             quanto diminuir, então o intervalo cresce de 5 em 5
                             segundos, com teto.
      access_denied          a pessoa recusou (ou fechou o aviso de app não
                             verificado). Não adianta continuar perguntando.
      expired_token          o código morreu (~15 min). Recomeçar é a única
                             saída, e dizer isso é melhor que tentar sozinho e
                             confundir a pessoa com um código novo na tela.

    `espera` é TETO, e o prazo do próprio código também é: quem vencer primeiro
    manda. Perguntar depois que o código expirou é gastar requisição para ouvir
    `expired_token` — e travar o agente esperando um estado que talvez nunca
    venha é pior do que dizer onde parou.
    """
    ident, segredo = _cliente()
    corpo = urllib.parse.urlencode({
        "client_id": ident,
        "client_secret": segredo,
        "device_code": device_code,
        "grant_type": GRANT_DEVICE,
    }).encode("utf-8")
    inicio = _agora()
    teto = min(espera, prazo_codigo) if prazo_codigo else espera
    while True:
        restante = teto - (_agora() - inicio)
        if restante <= 0:
            break
        # Teto é teto: a última espera encolhe para caber, e nunca se dorme um
        # segundo além do que quem chamou autorizou.
        _dorme(min(intervalo, restante))
        codigo, bruto, _cab = _http(
            "POST", URL_TOKEN, corpo=corpo,
            cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT_API)
        dados, nome, descricao = _json_oauth(codigo, bruto, "conferir a conexão")
        if nome == "authorization_pending":
            continue
        if nome == "slow_down":
            intervalo = min(intervalo + PASSO_SLOW_DOWN, TETO_INTERVALO)
            _fala(progresso, "o Google pediu para perguntar mais devagar; "
                             f"passando a esperar {intervalo}s entre as "
                             "tentativas")
            continue
        if nome:
            raise _erro_oauth(nome, descricao, codigo, "a autorização")
        if not 200 <= codigo < 300:
            raise YouTubeIndisponivel(
                f"não deu para conferir a conexão: HTTP {codigo}, e a resposta "
                f"não trouxe `error`: {_texto(bruto)[:400] or '(corpo vazio)'}",
                http=codigo)
        acesso = dados.get("access_token")
        if not isinstance(acesso, str) or not acesso.strip():
            raise YouTubeIndisponivel(
                "o Google respondeu a autorização sem erro e sem "
                f"`access_token`. Resposta: {sorted(dados)!r}", http=codigo)
        _guarda_segredos(dados)
        return dados
    raise YouTubeIndisponivel(
        f"ninguém autorizou em {int(teto)}s. Nada foi conectado e nada foi "
        "perdido — o código simplesmente não chegou a ser digitado. Rode a "
        "conexão de novo com o celular já na mão; se precisar de mais tempo, "
        "peça uma espera maior.", codigo="espera_estourada")


def _canal(acesso):
    """(nome do canal, id, aviso) — quem acabou de autorizar. Nunca é fatal.

    Serve para a conexão poder dizer "conectado ao canal X". Uma pessoa com três
    contas Google no celular escolhe a errada com facilidade, e descobrir isso
    depois de publicar é caro.

    Não é fatal porque o token JÁ existe quando esta função roda: derrubar a
    conexão inteira porque uma consulta de cortesia falhou obrigaria a pessoa a
    digitar o código de novo por nada.
    """
    try:
        codigo, bruto, _cab = _http(
            "GET", URL_CANAL,
            cabecalhos={"Authorization": f"Bearer {acesso}"},
            timeout=TIMEOUT_API)
        dados = _resposta_api(codigo, bruto, "perguntar de quem é o canal")
    except YouTubeIndisponivel as exc:
        return None, None, (
            "a conexão funcionou, mas não deu para confirmar de qual canal ela "
            f"é: {exc}")
    itens = dados.get("items")
    if not isinstance(itens, list) or not itens:
        # Isto não é detalhe: uma conta Google sem canal aceita o OAuth inteiro
        # e recusa o upload lá na frente, com `youtubeSignupRequired`.
        return None, None, (
            "a conta Google que você autorizou NÃO tem canal no YouTube — a "
            "consulta de canais voltou vazia. Abra youtube.com nessa conta, crie "
            "o canal, e refaça a conexão; senão o primeiro envio vai falhar.")
    primeiro = itens[0] if isinstance(itens[0], dict) else {}
    snippet = primeiro.get("snippet") if isinstance(primeiro.get("snippet"), dict) \
        else {}
    return snippet.get("title"), primeiro.get("id"), None


# ────────────────────────────────────────────────────────── as quatro funções

def conecta(*, progresso=None, espera=300):
    """Conecta um canal do YouTube pelo device flow. -> dict.

    A pessoa não digita senha aqui. O agente pede um código ao Google, mostra a
    URL e o código pelo `progresso`, e espera. Quem pergunta a senha é o Google,
    na tela dele, no celular dela.

    `espera` é o TETO em segundos da espera pela autorização, não uma promessa —
    e o prazo do próprio código (cerca de 15 minutos) também é teto: quem vencer
    primeiro manda. Estourou, levanta com `codigo="espera_estourada"` dizendo
    que nada foi conectado e nada foi perdido.

    O dicionário devolvido:

      canal     o nome do canal autorizado, ou None se não deu para confirmar
      canal_id  o id dele, pelo mesmo motivo
      aviso     AVISO_PRIVADO — o vídeo vai subir privado, e por quê
      arquivo   onde o token foi gravado
      escopos   o que foi concedido, segundo o Google
      avisos    o que não impediu a conexão mas você precisa saber
    """
    if not _cliente_configurado():
        raise YouTubeIndisponivel(
                "este build do clip-warden não traz um cliente OAuth do Google, "
                "então não há com que falar com o YouTube. A imagem publicada "
                "traz; um build feito do código-fonte precisa dos seus, em "
                "WARDEN_YT_CLIENT_ID e WARDEN_YT_CLIENT_SECRET. Enquanto isso, "
                "o clipe é entregue como arquivo e a publicação é manual.")
    _fala(progresso, "pedindo um código ao Google (a senha não passa por aqui)")
    pedido = _pede_codigo()
    device_code = pedido["device_code"]
    user_code = pedido["user_code"]
    url = pedido.get("verification_url") or pedido.get("verification_uri")
    intervalo = pedido.get("interval")
    if not isinstance(intervalo, (int, float)) or isinstance(intervalo, bool) \
            or intervalo <= 0:
        intervalo = INTERVALO_PADRAO
    prazo = pedido.get("expires_in")
    if not isinstance(prazo, (int, float)) or isinstance(prazo, bool) \
            or prazo <= 0:
        prazo = 0

    _fala(progresso, COMO_AUTORIZAR.format(url=url, codigo=user_code))
    novo = _espera_autorizacao(device_code, espera=espera, intervalo=intervalo,
                               prazo_codigo=prazo, progresso=progresso)

    agora = _relogio()
    ident, _ = _cliente()
    dura = novo.get("expires_in")
    dados = {
        "access_token": novo["access_token"].strip(),
        "expires_at": _iso(agora + (
            dura if isinstance(dura, (int, float)) and not isinstance(dura, bool)
            else VIDA_ACCESS)),
        "token_type": str(novo.get("token_type") or "Bearer"),
        "scope": str(novo.get("scope") or ESCOPOS),
        # O client_id vai para o arquivo porque um token só serve com o cliente
        # que o emitiu: se alguém trocar o app, o `invalid_grant` que vai
        # aparecer fica explicável em vez de misterioso. O client_secret NÃO vai
        # — ele já está no módulo, e gravá-lo só criaria mais uma cópia de um
        # segredo no disco sem resolver nada.
        "client_id": ident,
        "conectado_em": _iso(agora),
    }
    refresh = novo.get("refresh_token")
    if isinstance(refresh, str) and refresh.strip():
        dados["refresh_token"] = refresh.strip()
    _guarda_segredos(dados)

    # GRAVA ANTES de perguntar o canal. A ordem importa: o token já existe do
    # lado do Google, e perder isso porque uma consulta de cortesia falhou
    # obrigaria a pessoa a digitar o código de novo por nada.
    _grava_ou_explica(dados, "conexão")

    avisos = []
    if "refresh_token" not in dados:
        # Acontece quando a conta já tinha autorizado este app antes: o Google
        # só manda o refresh_token na PRIMEIRA autorização. O token de uma hora
        # funciona, mas o agente para quando ele vencer.
        avisos.append(
            "o Google não mandou refresh_token nesta autorização — isso costuma "
            "acontecer quando esta conta já tinha autorizado o app antes. O "
            "acesso vale 1 hora e depois para. Para resolver de vez, revogue o "
            "app em myaccount.google.com/permissions e conecte outra vez.")
    nome, canal_id, aviso_canal = _canal(dados["access_token"])
    if aviso_canal:
        avisos.append(aviso_canal)
    if nome or canal_id:
        dados["canal"] = nome
        dados["canal_id"] = canal_id
        _grava_ou_explica(dados, "conexão")
        _fala(progresso, f"conectado ao canal: {nome}")
    elif nome == "" and canal_id is None:
        # A conta NÃO tem canal, e isso é gravado para o `status` poder dizer
        # sem tocar a rede.
        #
        # Medido em 15/09/2026 numa conta recém-criada do dono: ela autorizou o
        # app inteiro, os dois escopos foram concedidos, o refresh_token veio --
        # e `channels?mine=true` voltou com zero itens, porque uma conta Google
        # não ganha canal do YouTube junto. O primeiro envio falharia com
        # `youtubeSignupRequired`, três passos e vários minutos depois.
        #
        # Sem esta marca, `status_conta` dizia apenas "canal não registrado" --
        # a mesma frase para "a consulta não respondeu", que é cosmético, e para
        # "não há canal", que impede publicar. Uma frase para dois estados, um
        # deles bloqueante. A marca fica no arquivo para o status responder isso
        # sem depender de a rede estar de pé.
        dados["canal_ausente"] = True
        _grava_ou_explica(dados, "conexão")
    frouxo = _modo_frouxo(TOKEN_FILE)
    if frouxo:
        avisos.append(frouxo)
    _fala(progresso, AVISO_PRIVADO)
    return {"canal": nome,
            "canal_id": canal_id,
            "aviso": AVISO_PRIVADO,
            "arquivo": TOKEN_FILE,
            "escopos": dados["scope"],
            "avisos": avisos}


def token(*, progresso=None):
    """O access_token pronto para usar, renovando-o sozinho quando precisa.

    Devolve a string crua. Quem chama nunca a imprime, nunca a loga e nunca a
    põe numa mensagem: toda exceção deste módulo já passa por `_limpa`, e o
    teste `test_nenhum_dos_tres_segredos_aparece_em_mensagem` existe para isso
    não decair.

    Renova quando o access_token venceu ou vence nos próximos MARGEM_RENOVACAO
    segundos, E o arquivo tem refresh_token. Se não tiver, a recusa diz para
    reconectar. Se o arquivo não disser quando vence, não há renovação
    preventiva — sem âncora não se sabe, e `publica` tem a segunda linha de
    defesa: renovar depois de o YouTube recusar a credencial.
    """
    dados = _le_arquivo()
    if _escopo_faltando(dados):
        raise YouTubeIndisponivel(
            f"o token em {TOKEN_FILE} foi emitido para o escopo "
            f"`{dados.get('scope')}`, que não inclui `{ESCOPO_UPLOAD}`. Refaça a "
            "conexão e deixe as duas permissões marcadas na tela do Google.")
    _, restante = _validade(dados)
    if restante is not None and restante <= MARGEM_RENOVACAO:
        dados = _renova(dados, progresso)
    return dados["access_token"].strip()


def _renova(dados, progresso=None):
    """Troca o refresh_token por um access_token novo e REESCREVE o arquivo.

    O access_token do Google dura 1 hora. Sem renovação automática, um agente
    que corta às 3h da manhã morreria esperando alguém acordar para digitar um
    código — e é exatamente para isso que o refresh_token existe: ele não
    precisa da pessoa.

    O Google normalmente NÃO gira o refresh_token nesta chamada, e é justamente
    por isso que a rotação é tratada aqui: quando ela acontecer (o protocolo
    permite), quem tiver guardado o antigo funciona por semanas e quebra num dia
    em que ninguém mudou nada, com um `invalid_grant` que não explica nada. Por
    isso o arquivo é reescrito INTEIRO, com o refresh que voltou.
    """
    if not _pode_renovar(dados):
        raise YouTubeIndisponivel(
            f"o acesso ao YouTube venceu (ele dura 1 hora) e não dá para renovar "
            f"sozinho: falta `refresh_token` em {TOKEN_FILE}. Rode a conexão de "
            "novo — são trinta segundos e um código digitado no celular — e "
            "depois disso a renovação passa a acontecer sem você.",
            codigo="sem_refresh_token")
    ident, segredo = _cliente()
    _fala(progresso, "o acesso venceu ou está perto; renovando sozinho, sem "
                     "você")
    corpo = urllib.parse.urlencode({
        "client_id": ident,
        "client_secret": segredo,
        "grant_type": GRANT_REFRESH,
        "refresh_token": dados["refresh_token"].strip(),
    }).encode("utf-8")
    codigo, bruto, _cab = _http(
        "POST", URL_TOKEN, corpo=corpo,
        cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT_API)
    novo, nome, descricao = _json_oauth(codigo, bruto, "renovar o acesso")
    if nome:
        raise _erro_oauth(nome, descricao, codigo, "renovar o acesso")
    if not 200 <= codigo < 300:
        raise YouTubeIndisponivel(
            f"não deu para renovar o acesso: HTTP {codigo}, e a resposta não "
            f"trouxe `error`: {_texto(bruto)[:400] or '(corpo vazio)'}",
            http=codigo)
    acesso = novo.get("access_token")
    if not isinstance(acesso, str) or not acesso.strip():
        raise YouTubeIndisponivel(
            "o Google respondeu a renovação sem erro e sem `access_token`. "
            f"Resposta: {sorted(novo)!r}", http=codigo)

    agora = _relogio()
    atualizado = dict(dados)
    atualizado["access_token"] = acesso.strip()
    dura = novo.get("expires_in")
    atualizado["expires_at"] = _iso(agora + (
        dura if isinstance(dura, (int, float)) and not isinstance(dura, bool)
        else VIDA_ACCESS))
    # A ROTAÇÃO. Se voltou refresh_token, ele é o que vale, mesmo quando é igual
    # ao que foi enviado; guardar o antigo é o defeito clássico deste fluxo.
    girou = novo.get("refresh_token")
    if isinstance(girou, str) and girou.strip():
        atualizado["refresh_token"] = girou.strip()
    for herdado in ("scope", "token_type"):
        if isinstance(novo.get(herdado), str) and novo[herdado].strip():
            atualizado[herdado] = novo[herdado].strip()
    atualizado["renovado_em"] = _iso(agora)
    # As durações relativas saem do arquivo. Elas ficariam ancoradas numa
    # emissão que já passou, e é exatamente essa ambiguidade que faz um token
    # parecer válido depois de vencido.
    for relativo in ("expires_in", "obtained_at", "obtido_em"):
        atualizado.pop(relativo, None)

    _guarda_segredos(atualizado)
    _grava_ou_explica(atualizado, "renovação")
    _fala(progresso, f"acesso renovado; vence em {atualizado['expires_at']}")
    return atualizado


def _confere_credencial(dados):
    """Exercita a credencial AGORA. -> (estado, frase). NUNCA levanta.

    `estado` é um de:

      "ok"        o Google aceitou. A frase diz O QUE foi exercitado, porque
                  "aceitou" sem objeto é a mesma vagueza que se está corrigindo.
      "morta"     o Google recusou de um jeito que só reconectar resolve. A
                  frase é a dele, com a receita do que fazer.
      "recusada"  o Google recusou por outro motivo. Também é dele a palavra.
      "sem_rede"  ninguém atendeu. NADA foi verificado, e essa é a resposta.

    Quando há `refresh_token`, o que se exercita é a RENOVAÇÃO, e não uma
    consulta qualquer. Não é preciosismo: `status` afirmava "renovação
    automática armada", e a única chamada no mundo que responde se ela está
    armada é a própria renovação — foi ela que devolveu `deleted_client` na
    medição de 15/09. Uma consulta de canal com um access_token ainda válido
    teria dito "tudo bem" durante a hora inteira em que a conexão já estava
    morta.

    Isso custa um POST no endpoint de token (que NÃO consome cota da YouTube
    Data API — cota é dos endpoints de dados) e reescreve o arquivo do token com
    o acesso novo. Era essa a objeção antiga a tocar a rede aqui; ela vale menos
    que um `status` que mente.

    Sem `refresh_token` não há renovação para testar, e aí a pergunta possível é
    outra — "este access_token ainda serve?" —, respondida pela consulta de
    canal, que custa 1 unidade de cota.
    """
    pode = _pode_renovar(dados)
    try:
        if pode:
            novo = _renova(dados)
            _, falta = _validade(novo)
            resto = f", e o acesso novo vale {int(falta // 60)} min" \
                if falta is not None else ""
            return "ok", ("renovação automática testada agora e aceita pelo "
                          f"Google{resto}")
        acesso = str(dados.get("access_token") or "").strip()
        codigo, bruto, _cab = _http(
            "GET", URL_CANAL, cabecalhos={"Authorization": f"Bearer {acesso}"},
            timeout=TIMEOUT_API)
        _resposta_api(codigo, bruto, "conferir o acesso")
        return "ok", ("o Google aceitou este acesso agora (não há "
                      "`refresh_token` para testar, então ele para quando "
                      "vencer)")
    except YouTubeIndisponivel as exc:
        if exc.codigo == "sem_rede":
            return "sem_rede", str(exc)
        if exc.codigo in CREDENCIAL_MORTA:
            return "morta", str(exc)
        return "recusada", str(exc)
    except Exception as exc:                                  # noqa: BLE001
        # `status_conta` promete nunca levantar, e essa promessa só vale para o
        # que ninguém previu. Um defeito aqui não pode sumir com o relatório
        # inteiro do `warden status`.
        return "sem_rede", _limpa(f"não deu para verificar: "
                                  f"{type(exc).__name__}: {exc}")


def status_conta(*, verificar=True):
    """(ok, motivo) para o `warden status`. NUNCA levanta, nem com lixo no disco.

    TRÊS estados, com palavras diferentes, porque cada um manda fazer coisa
    diferente:

      NÃO CONECTADO         não há arquivo, ou o que há não serve (escopo
                            errado, conta sem canal). Não há o que verificar.
      CONECTADO E VERIFICADO a credencial foi exercitada AGORA e o Google
                            aceitou. Só aqui `ok` é True.
      PRESENTE, NÃO VERIFICADO o arquivo está lá e a verificação não confirmou
                            nada — ou porque o Google recusou (e a palavra dele
                            vem junto, com o que fazer), ou porque a rede não
                            respondeu. Esses dois não são a mesma frase.

    POR QUE ELA PASSOU A TOCAR A REDE, depois de uma vida inteira sem tocar: em
    15/09/2026 ela respondeu "connected -- conectado ao canal Pod Cortes (...)
    renovação automática armada" no mesmo minuto em que a renovação devolvia
    `deleted_client`. Ela nunca tinha perguntado nada a ninguém: leu o disco e
    descreveu o disco. "Conectado" é uma afirmação sobre o mundo, e um comando
    que a faz sem ter verificado está adivinhando — a mesma coisa que este
    projeto já tirou do `warden delivered`, e que a persona proíbe em voz alta.

    `verificar=False` desliga a rede e devolve o que o arquivo diz, SEM chamar
    isso de conectado: em modo offline a resposta honesta é "não verificado",
    não um palpite otimista.
    """
    if not _cliente_configurado():
        return (False, "this build carries no Google OAuth client, so there is "
                       "nothing to talk to YouTube with. The published image "
                       "has one; a build from source needs yours in "
                       "WARDEN_YT_CLIENT_ID and WARDEN_YT_CLIENT_SECRET.")
    try:
        dados = _le_arquivo()
    except YouTubeIndisponivel as exc:
        return False, str(exc)
    except Exception as exc:                                  # noqa: BLE001
        # Um `status` que explode some com TODO o relatório, inclusive as linhas
        # que não têm nada a ver com YouTube. Nenhum defeito aqui vale isso.
        return False, _limpa(f"não deu para ler {TOKEN_FILE}: "
                             f"{type(exc).__name__}: {exc}")
    try:
        if _escopo_faltando(dados):
            return False, (f"o token foi emitido para `{dados.get('scope')}`, "
                           f"sem `{ESCOPO_UPLOAD}`: refaça a conexão e deixe as "
                           "duas permissões marcadas.")
        pode = _pode_renovar(dados)
        expirou, falta = _validade(dados)
        if (expirou or (falta is not None and falta <= MARGEM_RENOVACAO)) \
                and not pode:
            return False, (
                "o acesso venceu ou está vencendo e não há como renovar sozinho: "
                f"falta `refresh_token` em {TOKEN_FILE}. Rode a conexão de novo; "
                "depois disso a renovação acontece sem você.")

        quem = dados.get("canal")
        if dados.get("canal_ausente"):
            # `ok=False`, e não um aviso no fim de uma frase que começa com
            # "conectado": esta conta autorizou tudo e NÃO PUBLICA. Chamar isso
            # de conectado é o tipo de meia-verdade que faz a pessoa descobrir
            # no meio de uma entrega.
            return (False,
                    "a conta autorizada não tem canal no YouTube, então nenhum "
                    "envio vai funcionar. Abra youtube.com nessa conta, crie o "
                    "canal, e rode a conexão de novo.")
        # Daqui para baixo, TUDO que o arquivo diz é descrição de arquivo. Ela
        # entra na frase depois do veredito, nunca como o veredito.
        #
        # O modo do arquivo é lido AGORA, antes de verificar, e não no fim: a
        # verificação renova, a renovação reescreve o arquivo com modo 600, e aí
        # um `status` lido depois juraria que nunca houve frouxidão. Houve — o
        # refresh_token ficou legível para a máquina inteira até este segundo, e
        # é isso que a pessoa precisa saber.
        frouxo = _modo_frouxo(TOKEN_FILE)
        rodape = _LEMBRETE_TRANCADO + (f"\nATENÇÃO: {frouxo}" if frouxo else "")
        de_quem = f" ao canal {quem}" if quem else \
            " (a consulta de canal não respondeu na conexão)"
        if falta is None:
            prazo = "sem data de validade no arquivo"
        elif falta <= MARGEM_RENOVACAO:
            prazo = (f"o acesso gravado venceu ou vence em menos de "
                     f"{MARGEM_RENOVACAO // 60} min")
        else:
            prazo = f"o acesso gravado vence em {int(falta // 60)} min"

        if not verificar:
            # O modo offline NÃO diz conectado. Ele diz o que sabe (o arquivo) e
            # diz o que não sabe (tudo o mais), e `ok=False` porque "conectado"
            # é afirmação sobre o mundo e ninguém perguntou nada ao mundo.
            return False, (
                f"arquivo de conexão presente{de_quem}, {prazo} — mas NÃO "
                "verificado: a verificação está desligada, então nada aqui foi "
                "confirmado com o Google." + rodape)

        estado, frase = _confere_credencial(dados)
        if estado == "morta":
            return False, (
                f"o arquivo de conexão está lá{de_quem}, mas ele NÃO vale mais "
                f"— verifiquei agora e o Google recusou. {frase}" + rodape)
        if estado == "recusada":
            return False, (
                f"o arquivo de conexão está lá{de_quem}, e a verificação de "
                f"agora não passou. {frase}" + rodape)
        if estado == "sem_rede":
            # Nem conectado nem quebrado: NÃO SEI. Dizer qualquer um dos dois
            # aqui seria inventar, e inventar o otimista é o defeito de 15/09.
            return False, (
                f"arquivo de conexão presente{de_quem}, {prazo} — mas não "
                f"consegui verificar, e por isso não afirmo que está conectado "
                f"nem que quebrou. {frase}" + rodape)

        recado = f"conectado e VERIFICADO agora{de_quem}: {frase}"
        if not pode:
            # Ainda não dói, mas vai doer em uma hora. Melhor agora do que às 3h.
            recado += ("; SEM renovação automática (falta `refresh_token`), "
                       "então ele vai parar em até 1 hora")
        return True, recado + rodape
    except Exception as exc:                                  # noqa: BLE001
        return False, _limpa(f"não deu para avaliar a conexão: "
                             f"{type(exc).__name__}: {exc}")


def publica(caminho, *, titulo, descricao="", tags=(), privacidade="private",
            progresso=None):
    """Sobe um clipe para o YouTube pelo upload resumável. -> dict.

    `privacidade` é `private` por padrão, e isso NÃO é conservadorismo nosso: a
    documentação do `videos.insert` diz que vídeo de projeto não verificado "will
    be restricted to private viewing mode". Pedir `public` é permitido aqui, mas
    quem decide é o YouTube — e o que este dicionário reporta em `privacidade` é
    o que ELE devolveu, ao lado de `privacidade_pedida`, que é o que nós
    pedimos. Prometer público quando a API entrega privado seria mentir sobre o
    resultado do trabalho.

    E quando ele devolve `private`, o vídeo fica TRANCADO assim: a Ajuda do
    YouTube (support.google.com/youtube/answer/7300965) diz que não cabe recurso
    e que o estado não muda até um re-review. Ninguém destranca no Studio. A
    saída imediata é subir o mesmo arquivo pelo app ou site do YouTube — é o que
    o `aviso` explica, em português, para quem receber este dicionário.

    O dicionário devolvido:

      video_id          o id do vídeo, que é o que serve para tudo depois
      url               o link do vídeo
      studio            o link do vídeo no YouTube Studio — serve para ver e
                        editar título, descrição e miniatura; NÃO serve para
                        destrancar a privacidade de um upload não auditado
      titulo            o título que subiu
      privacidade       o que o YouTube DISSE que ficou (nunca o que pedimos)
      privacidade_pedida o que nós pedimos
      bytes             o tamanho enviado
      segundos          do início do envio até a resposta do PUT
      aviso             AVISO_PRIVADO, quando ficou privado
      shorts            SOBRE_SHORTS — por que ele cai (ou não) em Shorts
      avisos            o que não impediu o envio mas você precisa saber

    Levanta YouTubeIndisponivel quando não há como enviar, ou quando o YouTube
    recusou. Na recusa, o `reason` e a `message` DELE vão na mensagem, verbatim.
    Nunca se inventa causa aqui.
    """
    if not _cliente_configurado():
        raise YouTubeIndisponivel(
                "este build do clip-warden não traz um cliente OAuth do Google, "
                "então não há com que falar com o YouTube. A imagem publicada "
                "traz; um build feito do código-fonte precisa dos seus, em "
                "WARDEN_YT_CLIENT_ID e WARDEN_YT_CLIENT_SECRET. Enquanto isso, "
                "o clipe é entregue como arquivo e a publicação é manual.")
    caminho = os.path.abspath(os.path.expanduser(caminho))
    if not os.path.isfile(caminho):
        raise YouTubeIndisponivel(f"não há arquivo em {caminho} para publicar.")
    tamanho = os.path.getsize(caminho)
    if tamanho <= 0:
        raise YouTubeIndisponivel(
            f"{caminho} tem 0 byte. O YouTube recusaria, e a recusa dele seria "
            "menos clara que esta.")
    if tamanho > TETO_ARQUIVO:
        raise YouTubeIndisponivel(
            f"{caminho} tem {tamanho} bytes, acima do teto de {TETO_ARQUIVO} "
            "que este módulo aceita. O arquivo vai num PUT só, carregado na "
            "memória; o envio em pedaços com retomada existe na API do Google e "
            "NÃO está implementado aqui, porque nunca foi medido nesta casa. Um "
            "Short de até 3 minutos não chega perto deste tamanho — se chegou, "
            "reencode o clipe.")
    tipo = TIPOS.get(os.path.splitext(caminho)[1].lower())
    if not tipo:
        raise YouTubeIndisponivel(
            f"{caminho} não é um formato que este módulo envia. Ele manda "
            f"{', '.join(sorted(TIPOS))} — e o que esta esteira entrega é .mp4.")

    titulo = _confere_texto(titulo, "o título", TETO_TITULO, obrigatorio=True)
    descricao = _confere_texto(descricao or "", "a descrição", TETO_DESCRICAO,
                               obrigatorio=False)
    if privacidade not in PRIVACIDADES:
        raise YouTubeIndisponivel(
            f"`{privacidade}` não é uma privacidade que o YouTube conheça. As "
            f"dele são: {', '.join(PRIVACIDADES)}.")
    etiquetas = [str(t).strip() for t in (tags or ()) if str(t).strip()]

    avisos = []
    frouxo = _modo_frouxo(TOKEN_FILE)
    if frouxo:
        avisos.append(frouxo)
        _fala(progresso, "AVISO: " + frouxo)
    if privacidade != "private":
        avisos.append(
            f"você pediu `{privacidade}`, mas enquanto este app não passar pela "
            "auditoria do YouTube ele restringe a privado todo vídeo enviado "
            "pela API — e tranca nesse estado, sem recurso e sem mudança pelo "
            "Studio. O que vier de volta é o que vale.")

    # A renovação acontece AQUI, antes de um byte sair da máquina. Quando ela
    # falha por credencial morta, o que a pessoa precisa ler é o que fazer e que
    # o arquivo NÃO foi enviado — não um `deleted_client` cru no meio de um
    # traceback de OAuth. Medido em 15/09/2026: foi exatamente essa a recusa
    # depois de o dono rotacionar o cliente do Google.
    try:
        tok = token(progresso=progresso)
    except YouTubeIndisponivel as exc:
        if exc.codigo in CREDENCIAL_MORTA:
            raise YouTubeIndisponivel(
                f"nada foi enviado: a conexão com o YouTube não vale mais, e o "
                f"envio parou antes de subir qualquer byte de {caminho}.\n"
                f"{exc}", codigo=exc.codigo, http=exc.http)
        raise

    # O corpo de metadados. NÃO tem `categoryId`: nós não sabemos a categoria do
    # clipe e chutar uma seria inventar informação sobre o canal de outra
    # pessoa. Também NÃO tem `selfDeclaredMadeForKids`: aquilo é uma declaração
    # legal sobre o conteúdo, e quem a faz é o dono do canal, no YouTube Studio
    # — um agente não declara nada em nome de ninguém.
    corpo = {"snippet": {"title": titulo, "description": descricao},
             "status": {"privacyStatus": privacidade}}
    if etiquetas:
        corpo["snippet"]["tags"] = etiquetas

    comeco = _agora()
    _fala(progresso, f"abrindo o envio: {tamanho} bytes, privacidade "
                     f"`{privacidade}`")
    try:
        sessao = _abre_sessao(tok, corpo, tamanho, tipo)
    except YouTubeIndisponivel as exc:
        # A segunda linha de defesa da renovação, e ela existe por um caso real:
        # um arquivo sem `expires_at` (escrito à mão, ou vindo de outra
        # ferramenta) não diz quando vence, então `token()` não renova
        # preventivamente. Aqui o YouTube acabou de dizer que a credencial não
        # vale, o que é a âncora que faltava. Uma tentativa só.
        if exc.codigo not in ("authError", "authorizationRequired",
                              "unauthorized") and exc.http != 401:
            raise
        if not _pode_renovar(_le_arquivo()):
            raise
        _fala(progresso, "o YouTube recusou a credencial; renovando e tentando "
                         "mais uma vez")
        tok = _renova(_le_arquivo(), progresso)["access_token"].strip()
        sessao = _abre_sessao(tok, corpo, tamanho, tipo)
    _fala(progresso, "sessão de upload aberta; enviando o arquivo")

    with open(caminho, "rb") as fh:
        conteudo = fh.read()
    # O PUT vai COM Authorization: a URL de sessão do Google vive até uma semana
    # e as bibliotecas oficiais dele mandam o cabeçalho em todas as requisições
    # do upload. É o mesmo host googleapis.com, sobre TLS.
    codigo, bruto, _cab = _http(
        "PUT", sessao, corpo=conteudo,
        cabecalhos={"Authorization": f"Bearer {tok}",
                    "Content-Type": tipo,
                    "Content-Length": str(tamanho)},
        timeout=TIMEOUT_UPLOAD)
    if codigo == 308:
        # 308 é "Resume Incomplete": o Google recebeu parte dos bytes e espera o
        # resto. Este módulo manda tudo de uma vez, então isto quer dizer que o
        # envio foi cortado no meio. Não há retomada implementada — dizer isso é
        # honesto; fingir que subiu não é.
        raise YouTubeIndisponivel(
            f"o upload voltou HTTP 308 (Resume Incomplete): o YouTube recebeu só "
            f"parte dos {tamanho} bytes e espera o resto. Este módulo envia o "
            "arquivo inteiro de uma vez e não implementa retomada, então o jeito "
            "é mandar de novo — nada foi publicado.", http=codigo)
    dados = _resposta_api(codigo, bruto, "receber o arquivo")
    video_id = dados.get("id")
    if not video_id:
        raise YouTubeIndisponivel(
            "o YouTube respondeu o upload sem erro e sem `id` do vídeo; sem ele "
            f"não há como saber o que subiu. Resposta: {sorted(dados)!r}",
            http=codigo)

    status = dados.get("status") if isinstance(dados.get("status"), dict) else {}
    # `ficou` é o `privacyStatus` que o YouTube DEVOLVEU. Nunca `privacidade`,
    # que é só o que pedimos. O fallback existe para a resposta que vem sem o
    # bloco `status` -- e mesmo aí a frase abaixo diz qual dos dois está falando,
    # porque "privacidade: public" sem dono é a mentira que este arquivo já
    # contou uma vez.
    devolvido = status.get("privacyStatus")
    ficou = devolvido or privacidade
    if ficou != privacidade:
        aviso = (f"você pediu `{privacidade}` e o YouTube gravou `{ficou}`. "
                 "Quem manda na privacidade é ele, não nós.")
        if ficou == "private":
            # O caso real: pediram público, veio privado. Dizer só "ele gravou
            # privado" deixa a pessoa procurando o botão que não existe.
            aviso += (" Ele tranca assim todo vídeo enviado por app que ainda "
                      "não passou pela auditoria dele, e essa trava não se "
                      "desfaz no Studio nem por recurso "
                      "(support.google.com/youtube/answer/7300965). Para o "
                      "clipe ficar público agora, suba o MESMO arquivo pelo "
                      "app ou pelo site do YouTube.")
        avisos.append(aviso)
    if devolvido:
        _fala(progresso, f"vídeo {video_id} no ar; o YouTube devolveu "
                         f"privacidade `{ficou}` (pedimos `{privacidade}`)")
    else:
        _fala(progresso, f"vídeo {video_id} no ar; o YouTube respondeu sem "
                         f"`privacyStatus`, então o que consta é o que pedimos: "
                         f"`{ficou}`")
    if ficou == "private":
        _fala(progresso, AVISO_PRIVADO)
    return {"video_id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "studio": f"https://studio.youtube.com/video/{video_id}/edit",
            "titulo": titulo,
            "privacidade": ficou,
            "privacidade_pedida": privacidade,
            "bytes": tamanho,
            "segundos": round(_agora() - comeco, 1),
            "aviso": AVISO_PRIVADO if ficou == "private" else "",
            "shorts": SOBRE_SHORTS,
            "avisos": avisos}


# ─────────────────────────────────────────────────────────────── por dentro

def _fala(progresso, mensagem):
    """Avisa quem está olhando, sem deixar um callback quebrado perder o envio.

    Se a função de progresso da CLI explodir DEPOIS do upload, o video_id vai
    junto e o arquivo já está no YouTube — o pior dos mundos. Então o erro do
    callback é de quem o escreveu, e não custa o resultado.
    """
    if progresso is None:
        return
    try:
        progresso(_limpa(mensagem))
    except Exception:                                         # noqa: BLE001
        pass


def _confere_texto(valor, oque, teto, *, obrigatorio):
    """Recusa título/descrição fora dos limites do YouTube, com a CONTAGEM.

    A contagem está na mensagem de propósito: "passou do limite" faz a pessoa
    contar caractere na mão; "tem 108, o limite é 100, corte 8" ela resolve em
    dez segundos. E a recusa acontece ANTES do upload — descobrir isso depois de
    subir 40 MB é o tipo de desperdício que a API não perdoa duas vezes no mesmo
    dia por causa da cota.

    O YouTube conta CARACTERES (unidades do BMP), não bytes; `len` do Python é a
    contagem certa aqui, e um emoji conta como um.
    """
    if not isinstance(valor, str):
        raise YouTubeIndisponivel(
            f"{oque} precisa ser texto, e veio {type(valor).__name__}.")
    texto = valor.strip()
    if obrigatorio and not texto:
        raise YouTubeIndisponivel(
            f"{oque} está vazio, e o YouTube exige um. Um clipe sem título é um "
            "clipe que ninguém acha.")
    if len(texto) > teto:
        raise YouTubeIndisponivel(
            f"{oque} tem {len(texto)} caracteres e o YouTube aceita no máximo "
            f"{teto}. Corte {len(texto) - teto} e mande de novo — a recusa é "
            "aqui, antes de subir o arquivo, para não gastar upload nem cota "
            "num envio que o YouTube ia rejeitar.")
    achados = [s for s in PROIBIDOS_NO_TEXTO if s in texto]
    if achados:
        raise YouTubeIndisponivel(
            f"{oque} tem {' e '.join(achados)}, e o YouTube recusa esses sinais "
            "em título e descrição (a recusa dele é `invalidTitle`, que não "
            "explica nada). Troque-os e mande de novo.")
    return texto


def _abre_sessao(tok, corpo, tamanho, tipo):
    """Passo 1 do upload resumável: o POST dos metadados. -> URL da sessão.

    `POST .../upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`
    com o JSON de metadados no corpo e o tamanho/tipo do arquivo nos cabeçalhos
    `X-Upload-Content-Length` e `X-Upload-Content-Type`. A resposta não traz o
    vídeo: traz um `Location`, e é para lá que o arquivo vai num PUT.

    Separado de `publica` porque acontece duas vezes no pior caso: a segunda
    depois de renovar o token.
    """
    codigo, bruto, cabecalhos = _http(
        "POST", URL_UPLOAD,
        corpo=json.dumps(corpo).encode("utf-8"),
        cabecalhos={"Authorization": f"Bearer {tok}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(tamanho),
                    "X-Upload-Content-Type": tipo},
        timeout=TIMEOUT_API)
    _resposta_api(codigo, bruto, "abrir o envio")
    sessao = (cabecalhos or {}).get("location")
    if not sessao:
        raise YouTubeIndisponivel(
            "o YouTube aceitou os metadados e não mandou o cabeçalho `Location` "
            "com a sessão de upload; sem ele não há para onde mandar o arquivo. "
            f"Cabeçalhos: {sorted((cabecalhos or {}))!r}", http=codigo)
    return sessao
