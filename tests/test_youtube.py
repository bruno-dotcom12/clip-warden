# -*- coding: utf-8 -*-
"""A conexão e a publicação no YouTube, sem tocar a rede uma vez.

A suíte inteira troca `warden_youtube._http`, que é a única porta para fora do
processo. O que ela mede não é se o Google responde — isso depende de uma
pessoa com um celular na mão — e sim as coisas que decaem em silêncio quando
ninguém trava nelas:

1. O DEVICE FLOW NÃO PODE VIRAR LAÇO INFINITO. `authorization_pending` é o
   caminho normal e continua; `slow_down` aumenta o intervalo; `access_denied` e
   `expired_token` param na hora; e, acima de tudo, o teto de espera existe e é
   respeitado. Um agente preso perguntando para sempre é um agente morto.
2. O VÍDEO SOBE TRANCADO COMO PRIVADO, e quem diz como ele ficou é o YouTube: o
   que a função reporta é a resposta DELE, nunca o que pedimos. E o texto que
   sai junto não pode voltar a prometer que o dono destranca isso no Studio —
   ele não destranca, e a classe `NaoPrometePublicar` existe só para travar
   essa frase.
3. OS TRÊS SEGREDOS NUNCA SAEM: access_token, refresh_token e client_secret.
   Nem numa exceção, nem numa linha de progresso, nem quando o próprio Google
   ecoa o segredo de volta dentro de um `error_description`.
4. O limite de 100 caracteres do título é conferido AQUI, antes de subir o
   arquivo — e a recusa traz a contagem, senão a pessoa conta na mão.

Cada teste foi conferido por mutação: quebrei de propósito a linha que ele
descreve e vi o teste ficar vermelho.
"""
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from unittest import mock

import warden_youtube as Y


class Impossivel(Exception):
    """Um tipo que o módulo não conhece.

    `status_conta` promete NUNCA levantar, e essa promessa só vale para o que
    ninguém previu. Testar com um `ZeroDivisionError` deixaria passar um
    `except ZeroDivisionError` estreitado, que é justamente o defeito.
    """


# Os três segredos. São valores longos de propósito: `_limpa` só censura o que
# tem 8 caracteres ou mais, e um segredo curto demais viraria censura de tudo.
ACESSO = "ya29.a0AfB_exemploDeAccessTokenQueNaoPodeVazarEmLugarNenhum123"
ACESSO_NOVO = "ya29.a0AfB_oAccessTokenNovinhoQueVeioDaRenovacao445566"
REFRESH = "1//04oRefreshTokenQueNumAppEmProducaoNaoVenceNunca0011"
REFRESH_NOVO = "1//04oRefreshQueGIROU-esteEhOqueValeDaquiPraFrente7788"
SEGREDO_APP = "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VazarJamais2233"
IDENT_APP = "975264052643-exemplo.apps.googleusercontent.com"

# O que a pessoa VÊ e digita. Este NÃO é segredo — ele existe para ser mostrado,
# e censurá-lo deixaria a conexão impossível de completar.
USER_CODE = "GQVQ-JKEC"
DEVICE_CODE = "AH-1Ng2oDeviceCodeQueResgataOTokenEPorIssoTambemEhSegredo"
URL_VERIFICACAO = "https://www.google.com/device"

SESSAO = ("https://www.googleapis.com/upload/youtube/v3/videos"
          "?uploadType=resumable&upload_id=AEnB2Uo-exemplo")
VIDEO_ID = "dQw4w9WgXcQ"
CANAL = "Bruno Arantes"
CANAL_ID = "UC_exemplo_de_id_de_canal"


# ───────────────────────────────────────────────────────── uma rede de mentira

class Rede:
    """Substitui `_http`. Guarda toda chamada e devolve o que lhe mandarem.

    Devolve TRÊS valores — código, corpo e cabeçalhos — porque é o que o módulo
    espera: a sessão do upload resumável chega no cabeçalho `Location`, com
    corpo vazio.

    As filas (`polling`, `upload`, ...) repetem o último valor para sempre, que
    é o comportamento de um servidor que já decidiu.
    """

    def __init__(self, *, device=None, polling=None, refresh=None, upload=None,
                 put=None, canal=None):
        self.device = device if device is not None else resposta_device()
        self.polling = polling if polling is not None else [resposta_token()]
        self.refresh = refresh if refresh is not None else [
            resposta_token(refresh_token=REFRESH_NOVO, access_token=ACESSO_NOVO)]
        self.upload = upload if upload is not None else (200, b"",
                                                         {"location": SESSAO})
        self.put = put if put is not None else resposta_video()
        self.canal = canal if canal is not None else resposta_canal()
        self.chamadas = []

    def __call__(self, metodo, url, *, corpo=None, cabecalhos=None, timeout=None):
        self.chamadas.append({"metodo": metodo, "url": url, "corpo": corpo,
                              "cabecalhos": dict(cabecalhos or {}),
                              "timeout": timeout})
        if url == Y.URL_DEVICE:
            return _fila(self.device)
        if url == Y.URL_TOKEN:
            campos = urllib.parse.parse_qs((corpo or b"").decode("utf-8"))
            if campos.get("grant_type") == ["refresh_token"]:
                return _fila(self.refresh)
            return _fila(self.polling)
        if url == Y.URL_CANAL:
            return _fila(self.canal)
        if url == Y.URL_UPLOAD:
            return _fila(self.upload)
        return _fila(self.put)

    # Açúcar para os testes lerem como frase.
    def pedidos(self, url):
        return [c for c in self.chamadas if c["url"] == url]

    def form(self, url, indice=0):
        bruto = self.pedidos(url)[indice]["corpo"].decode("utf-8")
        return {k: v[0] for k, v in urllib.parse.parse_qs(bruto).items()}

    def corpo_json(self, url, indice=0):
        return json.loads(self.pedidos(url)[indice]["corpo"].decode("utf-8"))


def _fila(valor):
    if isinstance(valor, list):
        return valor.pop(0) if len(valor) > 1 else valor[0]
    return valor


def resposta_device(**campos):
    corpo = {"device_code": DEVICE_CODE,
             "user_code": USER_CODE,
             "verification_url": URL_VERIFICACAO,
             "expires_in": 1800,
             "interval": 5}
    corpo.update(campos)
    return 200, json.dumps(corpo).encode("utf-8"), {}


def resposta_token(**campos):
    corpo = {"access_token": ACESSO,
             "expires_in": 3599,
             "refresh_token": REFRESH,
             "scope": Y.ESCOPOS,
             "token_type": "Bearer"}
    corpo.update(campos)
    return 200, json.dumps(corpo).encode("utf-8"), {}


def erro_oauth(nome, descricao="", http=400):
    """O envelope do OAuth: `error` é STRING no topo, com `error_description`.

    Não é capricho de teste, é o que o endpoint devolve — e
    `authorization_pending` chega assim, dentro de um HTTP 428, sendo o caminho
    NORMAL do fluxo.
    """
    return http, json.dumps({"error": nome,
                             "error_description": descricao}).encode("utf-8"), {}


def erro_api(motivo, mensagem="", http=403):
    """O OUTRO envelope: o da API do YouTube, com `error.errors[0].reason`."""
    return http, json.dumps({"error": {
        "code": http, "message": mensagem or motivo,
        "errors": [{"reason": motivo, "message": mensagem or motivo,
                    "domain": "youtube.video"}]}}).encode("utf-8"), {}


def resposta_video(**status):
    corpo = {"kind": "youtube#video", "id": VIDEO_ID,
             "snippet": {"title": "um clipe"},
             "status": dict({"privacyStatus": "private",
                             "uploadStatus": "uploaded"}, **status)}
    return 200, json.dumps(corpo).encode("utf-8"), {}


def resposta_canal(itens=None):
    corpo = {"kind": "youtube#channelListResponse",
             "items": [{"id": CANAL_ID, "snippet": {"title": CANAL}}]
             if itens is None else itens}
    return 200, json.dumps(corpo).encode("utf-8"), {}


class Relogio:
    """Um relógio que só anda quando alguém dorme.

    Sem isto o teste do teto de espera precisaria esperar cinco minutos de
    verdade. Com ele, `espera=20` e intervalo de 5s dão quatro tentativas e uma
    desistência, em zero segundo de parede. Move o monotônico E a hora de
    parede, porque `expires_at` é hora de parede.
    """

    def __init__(self):
        self.t = 1000.0
        self.parede = 1789000000.0     # uma hora de parede qualquer, fixa
        self.dormiu = []

    def agora(self):
        return self.t

    def relogio(self):
        return self.parede

    def dorme(self, s):
        self.dormiu.append(s)
        self.t += s
        self.parede += s


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="youtube-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.token_file = os.path.join(self.tmp, "youtube.json")
        self._restaura("TOKEN_FILE", self.token_file)
        self._restaura("CLIENT_ID", IDENT_APP)
        self._restaura("CLIENT_SECRET", SEGREDO_APP)
        self.relogio = Relogio()
        self._restaura("_agora", self.relogio.agora)
        self._restaura("_relogio", self.relogio.relogio)
        self._restaura("_dorme", self.relogio.dorme)
        # Estado de módulo entre testes: o registro de segredos manteria vivo o
        # token de outro caso e um vazamento passaria despercebido.
        Y._SEGREDOS.clear()
        self.addCleanup(Y._SEGREDOS.clear)

    def _restaura(self, nome, valor):
        antigo = getattr(Y, nome)
        setattr(Y, nome, valor)
        self.addCleanup(setattr, Y, nome, antigo)

    def grava_token(self, **campos):
        """Um arquivo de conexão completo, como `conecta` o deixa."""
        dados = {"access_token": ACESSO,
                 "refresh_token": REFRESH,
                 "expires_at": Y._iso(self.relogio.parede + 3600),
                 "scope": Y.ESCOPOS,
                 "token_type": "Bearer",
                 "client_id": IDENT_APP,
                 "canal": CANAL,
                 "canal_id": CANAL_ID,
                 "conectado_em": Y._iso(self.relogio.parede)}
        for chave, valor in campos.items():
            if valor is None:
                dados.pop(chave, None)
            else:
                dados[chave] = valor
        with open(self.token_file, "w", encoding="utf-8") as fh:
            json.dump(dados, fh)
        os.chmod(self.token_file, 0o600)
        return dados

    def le_token_do_disco(self):
        with open(self.token_file, encoding="utf-8") as fh:
            return json.load(fh)

    def modo_do_token(self):
        import stat as _stat
        return _stat.S_IMODE(os.stat(self.token_file).st_mode)

    def rascunhos_no_diretorio(self):
        """Sobras de escrita atômica. Deve ser sempre vazio, dê no que der."""
        return [n for n in os.listdir(self.tmp)
                if n.startswith(".") and "youtube.json" in n]

    def clipe(self, nome="mbl-02-votaria-nele.mp4", tamanho=8933576):
        caminho = os.path.join(self.tmp, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\0" * tamanho)
        return caminho

    def rede(self, **kw):
        rede = Rede(**kw)
        self._restaura("_http", rede)
        return rede


# ──────────────────────────────────────────────────────────── o device flow

class DeviceFlow(Base):

    def test_fluxo_feliz_pede_codigo_mostra_ao_usuario_e_grava_o_token(self):
        """Pede o código, mostra URL e código, espera, grava e diz de quem é o canal."""
        rede = self.rede()
        falas = []
        saida = Y.conecta(progresso=falas.append)

        # 1. o pedido do código: form-urlencoded, com client_id e os dois escopos
        pedido = rede.form(Y.URL_DEVICE)
        self.assertEqual(pedido["client_id"], IDENT_APP)
        self.assertEqual(pedido["scope"], Y.ESCOPOS)
        self.assertIn("youtube.upload", pedido["scope"])
        self.assertIn("youtube.readonly", pedido["scope"])
        self.assertEqual(rede.pedidos(Y.URL_DEVICE)[0]["metodo"], "POST")

        # 2. a pessoa precisa ver a URL e o código, senão não há o que digitar
        texto = "\n".join(falas)
        self.assertIn(USER_CODE, texto)
        self.assertIn(URL_VERIFICACAO, texto)

        # 3. o polling manda o grant_type literal do device flow
        poll = rede.form(Y.URL_TOKEN)
        self.assertEqual(poll["grant_type"],
                         "urn:ietf:params:oauth:grant-type:device_code")
        self.assertEqual(poll["device_code"], DEVICE_CODE)
        self.assertEqual(poll["client_id"], IDENT_APP)
        self.assertEqual(poll["client_secret"], SEGREDO_APP)

        # 4. o arquivo ficou com o que serve, e com data ABSOLUTA
        disco = self.le_token_do_disco()
        self.assertEqual(disco["access_token"], ACESSO)
        self.assertEqual(disco["refresh_token"], REFRESH)
        self.assertEqual(disco["expires_at"],
                         Y._iso(self.relogio.parede + 3599))
        self.assertNotIn("expires_in", disco)
        self.assertEqual(disco["client_id"], IDENT_APP)
        # o client_secret NÃO vai para o disco: ele já mora no módulo, e mais
        # uma cópia de um segredo não resolve nada
        self.assertNotIn("client_secret", json.dumps(disco))

        # 5. o resultado diz de quem é o canal e avisa da trava do privado
        self.assertEqual(saida["canal"], CANAL)
        self.assertEqual(saida["canal_id"], CANAL_ID)
        self.assertIn("TRANCADO", saida["aviso"])
        self.assertEqual(saida["avisos"], [])

    def test_a_senha_do_google_nunca_e_pedida_por_aqui(self):
        """A instrução precisa dizer isso: é o ponto inteiro do device flow."""
        self.rede()
        falas = []
        Y.conecta(progresso=falas.append)
        texto = "\n".join(falas).lower()
        self.assertIn("senha", texto)
        self.assertIn("não passa por aqui", texto)

    def test_authorization_pending_continua_e_depois_conclui(self):
        """O caminho NORMAL: a pessoa ainda está pegando o celular."""
        rede = self.rede(polling=[erro_oauth("authorization_pending",
                                             "Precondition Failed", http=428),
                                  erro_oauth("authorization_pending",
                                             "Precondition Failed", http=428),
                                  resposta_token()])
        saida = Y.conecta()
        self.assertEqual(len(rede.pedidos(Y.URL_TOKEN)), 3)
        self.assertEqual(saida["canal"], CANAL)
        self.assertEqual(self.le_token_do_disco()["access_token"], ACESSO)

    def test_authorization_pending_nao_vira_barulho_no_progresso(self):
        """Esperar a pessoa não é erro, e não merece uma linha de aviso por vez."""
        self.rede(polling=[erro_oauth("authorization_pending", http=428)] * 3
                  + [resposta_token()])
        falas = []
        Y.conecta(progresso=falas.append)
        self.assertEqual([f for f in falas if "pending" in f], [])

    def test_slow_down_aumenta_o_intervalo(self):
        """O Google mandou perguntar mais devagar; então se pergunta mais devagar."""
        rede = self.rede(polling=[erro_oauth("slow_down", http=403),
                                  erro_oauth("slow_down", http=403),
                                  resposta_token()])
        falas = []
        Y.conecta(progresso=falas.append)
        # 5s (o `interval` do Google), depois 10, depois 15: cresce de verdade
        self.assertEqual(self.relogio.dormiu, [5, 10, 15])
        self.assertEqual(len(rede.pedidos(Y.URL_TOKEN)), 3)
        self.assertIn("devagar", "\n".join(falas))

    def test_o_intervalo_nao_cresce_sem_teto(self):
        """Sem teto, o intervalo passaria do prazo do próprio código."""
        self.rede(device=resposta_device(expires_in=100000),
                  polling=[erro_oauth("slow_down", http=403)] * 40
                  + [resposta_token()])
        Y.conecta(espera=100000)
        self.assertLessEqual(max(self.relogio.dormiu), Y.TETO_INTERVALO)

    def test_access_denied_vira_mensagem_clara_e_para_na_hora(self):
        """A pessoa recusou. Continuar perguntando seria teimosia."""
        rede = self.rede(polling=[erro_oauth("access_denied",
                                             "The user denied the request",
                                             http=403),
                                  resposta_token()])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta()
        msg = str(caixa.exception)
        self.assertEqual(caixa.exception.codigo, "access_denied")
        self.assertIn("access_denied", msg)                  # a palavra do Google
        self.assertIn("The user denied the request", msg)    # verbatim
        self.assertIn("RECUSOU", msg)                        # e o que houve
        self.assertIn("Avançado", msg)                       # e o que fazer
        self.assertEqual(len(rede.pedidos(Y.URL_TOKEN)), 1)
        self.assertFalse(os.path.exists(self.token_file))

    def test_expired_token_manda_recomecar(self):
        """O código morreu. A saída é um código novo, e dizer isso poupa a tarde."""
        self.rede(polling=[erro_oauth("expired_token", "Token has expired",
                                      http=400)])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta()
        msg = str(caixa.exception)
        self.assertEqual(caixa.exception.codigo, "expired_token")
        self.assertIn("expired_token", msg)
        self.assertIn("expirou", msg)
        self.assertIn("de novo", msg)
        self.assertIn("Nada foi conectado", msg)

    def test_o_teto_de_espera_existe_e_nao_vira_laco_infinito(self):
        """Ninguém digitou. O agente desiste e diz que nada foi perdido."""
        rede = self.rede(polling=[erro_oauth("authorization_pending", http=428)])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta(espera=20)
        msg = str(caixa.exception)
        self.assertEqual(caixa.exception.codigo, "espera_estourada")
        self.assertIn("20s", msg)
        self.assertIn("Nada foi conectado", msg)
        # 20s de teto e 5s de intervalo: quatro tentativas, nem uma a mais
        self.assertEqual(len(rede.pedidos(Y.URL_TOKEN)), 4)
        self.assertEqual(sum(self.relogio.dormiu), 20)

    def test_a_ultima_espera_encolhe_para_caber_no_teto(self):
        """Teto é teto: não se dorme um segundo além do que foi autorizado."""
        self.rede(polling=[erro_oauth("authorization_pending", http=428)])
        with self.assertRaises(Y.YouTubeIndisponivel):
            Y.conecta(espera=12)
        self.assertEqual(self.relogio.dormiu, [5, 5, 2])

    def test_o_prazo_do_proprio_codigo_tambem_e_teto(self):
        """Perguntar depois que o código expirou é gastar requisição por nada."""
        rede = self.rede(device=resposta_device(expires_in=12),
                         polling=[erro_oauth("authorization_pending", http=428)])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta(espera=3600)
        self.assertEqual(len(rede.pedidos(Y.URL_TOKEN)), 3)
        self.assertIn("12s", str(caixa.exception))

    def test_erro_ao_pedir_o_codigo_repassa_a_palavra_do_google(self):
        rede = self.rede(device=erro_oauth(
            "invalid_client", "The OAuth client was not found.", http=401))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta()
        msg = str(caixa.exception)
        self.assertIn("invalid_client", msg)
        self.assertIn("The OAuth client was not found.", msg)
        self.assertIn("WARDEN_YT_CLIENT_ID", msg)
        self.assertEqual(rede.pedidos(Y.URL_TOKEN), [])

    def test_resposta_sem_device_code_nao_finge_que_deu_certo(self):
        self.rede(device=(200, json.dumps({"user_code": USER_CODE}).encode(), {}))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta()
        self.assertIn("sem erro e sem", str(caixa.exception))

    def test_verification_uri_do_rfc_tambem_serve(self):
        """O Google manda `verification_url`; o RFC 8628 diz `verification_uri`."""
        self.rede(device=(200, json.dumps({
            "device_code": DEVICE_CODE, "user_code": USER_CODE,
            "verification_uri": URL_VERIFICACAO, "expires_in": 1800,
            "interval": 5}).encode(), {}))
        falas = []
        Y.conecta(progresso=falas.append)
        self.assertIn(URL_VERIFICACAO, "\n".join(falas))

    def test_toda_requisicao_tem_timeout(self):
        rede = self.rede()
        Y.conecta()
        self.assertTrue(rede.chamadas)
        for chamada in rede.chamadas:
            self.assertIsNotNone(chamada["timeout"], chamada["url"])
            self.assertGreater(chamada["timeout"], 0)

    def test_conta_sem_canal_no_youtube_vira_aviso_sem_perder_o_token(self):
        """Conta Google sem canal aceita o OAuth e recusa o upload lá na frente."""
        self.rede(canal=resposta_canal(itens=[]))
        saida = Y.conecta()
        self.assertIsNone(saida["canal"])
        self.assertTrue(any("NÃO tem canal" in a for a in saida["avisos"]))
        self.assertEqual(self.le_token_do_disco()["access_token"], ACESSO)

    def test_consulta_de_canal_que_falha_nao_derruba_a_conexao(self):
        """O token já existe; perder isso por uma cortesia seria digitar de novo por nada."""
        self.rede(canal=erro_api("forbidden", "Forbidden"))
        saida = Y.conecta()
        self.assertIsNone(saida["canal"])
        self.assertTrue(any("não deu para confirmar" in a for a in saida["avisos"]))
        self.assertEqual(self.le_token_do_disco()["refresh_token"], REFRESH)

    def test_sem_refresh_token_a_conexao_avisa_que_vai_parar_em_uma_hora(self):
        """O Google só manda refresh na PRIMEIRA autorização daquela conta."""
        self.rede(polling=[resposta_token(refresh_token=None)])
        saida = Y.conecta()
        self.assertTrue(any("refresh_token" in a for a in saida["avisos"]))
        self.assertTrue(any("myaccount.google.com" in a for a in saida["avisos"]))

    def test_um_progresso_que_explode_nao_perde_a_conexao(self):
        def explode(_):
            raise RuntimeError("a CLI quebrou")
        self.rede()
        saida = Y.conecta(progresso=explode)
        self.assertEqual(saida["canal"], CANAL)
        self.assertEqual(self.le_token_do_disco()["access_token"], ACESSO)

    def test_o_arquivo_nasce_600_e_sem_rascunho_no_diretorio(self):
        self.rede()
        Y.conecta()
        self.assertEqual(self.modo_do_token(), 0o600)
        self.assertEqual(self.rascunhos_no_diretorio(), [])

    def test_o_temporario_ja_nasce_600_antes_de_qualquer_chmod(self):
        """Criar frouxo e apertar depois deixa uma janela em que qualquer conta
        da máquina lê o refresh_token — que num app em produção não vence nunca.
        Conferir só o modo final não vê essa janela: o chmod a fecha tarde."""
        import stat as _stat
        chmod_real = os.chmod
        nascimentos = []

        def espia(caminho, modo, *a, **kw):
            nascimentos.append(_stat.S_IMODE(os.stat(caminho).st_mode))
            return chmod_real(caminho, modo, *a, **kw)

        self.rede()
        with mock.patch("os.chmod", espia):
            Y.conecta()
        self.assertTrue(nascimentos)
        for modo in nascimentos:
            self.assertEqual(modo, 0o600, "o temporário nasceu frouxo")

    def test_sem_cliente_configurado_nao_toca_a_rede(self):
        rede = self.rede()
        self._restaura("CLIENT_SECRET", "")
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.conecta()
        self.assertIn("WARDEN_YT_CLIENT_SECRET", str(caixa.exception))
        self.assertEqual(rede.chamadas, [])


# ─────────────────────────────────────────────────────────────── a renovação

class Renovacao(Base):

    def test_renova_quando_o_acesso_venceu(self):
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        rede = self.rede()
        self.assertEqual(Y.token(), ACESSO_NOVO)
        form = rede.form(Y.URL_TOKEN)
        self.assertEqual(form["grant_type"], "refresh_token")
        self.assertEqual(form["refresh_token"], REFRESH)

    def test_renova_quando_esta_perto_de_vencer(self):
        """Um token que vence em 30s morre no meio de um upload de 40 MB."""
        self.grava_token(expires_at=Y._iso(self.relogio.parede + 30))
        self.rede()
        self.assertEqual(Y.token(), ACESSO_NOVO)

    def test_nao_renova_quando_o_acesso_ainda_e_bom(self):
        self.grava_token()
        rede = self.rede()
        self.assertEqual(Y.token(), ACESSO)
        self.assertEqual(rede.chamadas, [])

    def test_sem_ancora_de_validade_nao_renova_preventivamente(self):
        """Sem data não se sabe, e não saber é diferente de estar vencido."""
        self.grava_token(expires_at=None)
        rede = self.rede()
        self.assertEqual(Y.token(), ACESSO)
        self.assertEqual(rede.chamadas, [])

    def test_a_rotacao_do_refresh_token_e_gravada(self):
        """Quem guarda o refresh antigo funciona por semanas e quebra num dia."""
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede()
        Y.token()
        disco = self.le_token_do_disco()
        self.assertEqual(disco["refresh_token"], REFRESH_NOVO)
        self.assertEqual(disco["access_token"], ACESSO_NOVO)

    def test_renovacao_sem_refresh_novo_preserva_o_antigo(self):
        """O Google normalmente NÃO gira o refresh; apagá-lo seria perder a conta."""
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede(refresh=[resposta_token(access_token=ACESSO_NOVO,
                                          refresh_token=None)])
        Y.token()
        self.assertEqual(self.le_token_do_disco()["refresh_token"], REFRESH)

    def test_grava_data_absoluta_e_joga_fora_a_duracao(self):
        """`expires_in` sozinho fica ancorado numa emissão que já passou."""
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede(refresh=[resposta_token(access_token=ACESSO_NOVO,
                                          expires_in=3599)])
        Y.token()
        disco = self.le_token_do_disco()
        self.assertEqual(disco["expires_at"], Y._iso(self.relogio.parede + 3599))
        self.assertNotIn("expires_in", disco)
        self.assertIn("renovado_em", disco)

    def test_o_arquivo_renovado_continua_600_e_sem_rascunho(self):
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        os.chmod(self.token_file, 0o600)
        self.rede()
        Y.token()
        self.assertEqual(self.modo_do_token(), 0o600)
        self.assertEqual(self.rascunhos_no_diretorio(), [])

    def test_sem_refresh_token_diz_para_reconectar(self):
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10),
                         refresh_token=None)
        rede = self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.token()
        self.assertEqual(caixa.exception.codigo, "sem_refresh_token")
        self.assertIn("refresh_token", str(caixa.exception))
        self.assertIn("conexão de novo", str(caixa.exception))
        self.assertEqual(rede.chamadas, [])

    def test_invalid_grant_manda_refazer_a_conexao(self):
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede(refresh=[erro_oauth("invalid_grant", "Token has been expired "
                                      "or revoked.", http=400)])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.token()
        msg = str(caixa.exception)
        self.assertEqual(caixa.exception.codigo, "invalid_grant")
        self.assertIn("Token has been expired or revoked.", msg)
        self.assertIn("myaccount.google.com", msg)

    def test_escopo_errado_no_arquivo_manda_reconectar(self):
        self.grava_token(scope="https://www.googleapis.com/auth/youtube.readonly")
        rede = self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.token()
        self.assertIn("youtube.upload", str(caixa.exception))
        self.assertEqual(rede.chamadas, [])

    def test_escopo_ausente_nao_e_escopo_errado(self):
        """Arquivo que não conta o escopo não é arquivo com escopo errado."""
        self.grava_token(scope=None)
        self.rede()
        self.assertEqual(Y.token(), ACESSO)

    def test_credencial_recusada_no_upload_renova_e_tenta_mais_uma_vez(self):
        """A segunda linha de defesa: o YouTube é a âncora que o arquivo não tinha."""
        self.grava_token(expires_at=None)
        rede = self.rede(upload=[erro_api("authError", "Invalid Credentials",
                                          http=401),
                                 (200, b"", {"location": SESSAO})])
        saida = Y.publica(self.clipe(), titulo="um clipe")
        self.assertEqual(saida["video_id"], VIDEO_ID)
        self.assertEqual(len(rede.pedidos(Y.URL_UPLOAD)), 2)
        # a segunda tentativa vai com o token NOVO, senão renovar não serviu
        self.assertEqual(rede.pedidos(Y.URL_UPLOAD)[1]["cabecalhos"]
                         ["Authorization"], f"Bearer {ACESSO_NOVO}")

    def test_credencial_recusada_sem_refresh_nao_tenta_de_novo(self):
        self.grava_token(expires_at=None, refresh_token=None)
        rede = self.rede(upload=erro_api("authError", "Invalid Credentials",
                                         http=401))
        with self.assertRaises(Y.YouTubeIndisponivel):
            Y.publica(self.clipe(), titulo="um clipe")
        self.assertEqual(len(rede.pedidos(Y.URL_UPLOAD)), 1)


# ─────────────────────────────────────────────────────── título e descrição

class Metadados(Base):

    def setUp(self):
        super().setUp()
        self.grava_token()

    def test_titulo_com_101_caracteres_e_recusado_com_a_contagem(self):
        """Antes de subir o arquivo, e dizendo quantos cortar."""
        rede = self.rede()
        titulo = "a" * 101
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo=titulo)
        msg = str(caixa.exception)
        self.assertIn("101", msg)
        self.assertIn("100", msg)
        self.assertIn("Corte 1", msg)
        # e nada foi enviado: a recusa é ANTES do upload, por causa da cota
        self.assertEqual(rede.chamadas, [])

    def test_titulo_com_exatamente_100_caracteres_passa(self):
        """O limite é 100, não 99. Um off-by-one aqui recusaria trabalho bom."""
        self.rede()
        saida = Y.publica(self.clipe(), titulo="a" * 100)
        self.assertEqual(saida["titulo"], "a" * 100)

    def test_descricao_com_5001_caracteres_e_recusada_com_a_contagem(self):
        rede = self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok", descricao="b" * 5001)
        msg = str(caixa.exception)
        self.assertIn("5001", msg)
        self.assertIn("5000", msg)
        self.assertEqual(rede.chamadas, [])

    def test_titulo_vazio_e_recusado(self):
        self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="   ")
        self.assertIn("vazio", str(caixa.exception))

    def test_sinal_que_o_youtube_recusa_e_barrado_aqui(self):
        self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="Lula <3 Bolsonaro")
        self.assertIn("invalidTitle", str(caixa.exception))

    def test_privacidade_desconhecida_e_recusada(self):
        self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok", privacidade="privado")
        msg = str(caixa.exception)
        self.assertIn("private", msg)
        self.assertIn("unlisted", msg)
        self.assertIn("public", msg)

    def test_o_corpo_leva_titulo_descricao_tags_e_privacidade(self):
        rede = self.rede()
        Y.publica(self.clipe(), titulo="Votaria nele", descricao="corte do MBL",
                  tags=("mbl", " política ", ""))
        corpo = rede.corpo_json(Y.URL_UPLOAD)
        self.assertEqual(corpo["snippet"]["title"], "Votaria nele")
        self.assertEqual(corpo["snippet"]["description"], "corte do MBL")
        self.assertEqual(corpo["snippet"]["tags"], ["mbl", "política"])
        self.assertEqual(corpo["status"]["privacyStatus"], "private")

    def test_o_corpo_nao_inventa_categoria_nem_declaracao_de_publico_infantil(self):
        """Categoria nós não sabemos; a declaração de conteúdo infantil é legal
        e quem a faz é o dono do canal, não um agente."""
        rede = self.rede()
        Y.publica(self.clipe(), titulo="ok")
        corpo = rede.corpo_json(Y.URL_UPLOAD)
        self.assertNotIn("categoryId", corpo["snippet"])
        self.assertNotIn("selfDeclaredMadeForKids", corpo["status"])
        self.assertEqual(set(corpo), {"snippet", "status"})

    def test_sem_tags_o_campo_nao_vai(self):
        rede = self.rede()
        Y.publica(self.clipe(), titulo="ok")
        self.assertNotIn("tags", rede.corpo_json(Y.URL_UPLOAD)["snippet"])


# ──────────────────────────────────────────────────────── o upload resumável

class Upload(Base):

    def setUp(self):
        super().setUp()
        self.grava_token()

    def test_post_abre_a_sessao_e_o_put_vai_no_location(self):
        """Os dois passos do upload resumável, na ordem, com os cabeçalhos certos."""
        rede = self.rede()
        caminho = self.clipe(tamanho=1234)
        saida = Y.publica(caminho, titulo="Votaria nele")

        self.assertEqual([(c["metodo"], c["url"]) for c in rede.chamadas],
                         [("POST", Y.URL_UPLOAD), ("PUT", SESSAO)])
        abre = rede.pedidos(Y.URL_UPLOAD)[0]
        self.assertIn("uploadType=resumable", abre["url"])
        self.assertIn("part=snippet,status", abre["url"])
        self.assertEqual(abre["cabecalhos"]["X-Upload-Content-Length"], "1234")
        self.assertEqual(abre["cabecalhos"]["X-Upload-Content-Type"], "video/mp4")
        self.assertEqual(abre["cabecalhos"]["Authorization"], f"Bearer {ACESSO}")

        # o arquivo vai INTEIRO para a URL da sessão, e não para o endpoint
        envio = rede.pedidos(SESSAO)[0]
        self.assertEqual(envio["corpo"], b"\0" * 1234)
        self.assertEqual(envio["cabecalhos"]["Content-Type"], "video/mp4")
        self.assertEqual(envio["timeout"], Y.TIMEOUT_UPLOAD)

        self.assertEqual(saida["video_id"], VIDEO_ID)
        self.assertEqual(saida["url"], f"https://youtu.be/{VIDEO_ID}")
        self.assertIn(VIDEO_ID, saida["studio"])
        self.assertEqual(saida["bytes"], 1234)

    def test_sem_location_nao_finge_que_subiu(self):
        rede = self.rede(upload=(200, b"", {}))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        self.assertIn("Location", str(caixa.exception))
        self.assertEqual(rede.pedidos(SESSAO), [])

    def test_o_location_e_lido_sem_depender_de_maiuscula(self):
        """Cabeçalho HTTP não diferencia maiúscula; depender disso é defeito."""
        rede = self.rede(upload=(200, b"", {"location": SESSAO}))
        Y.publica(self.clipe(), titulo="ok")
        self.assertEqual(len(rede.pedidos(SESSAO)), 1)

    def test_o_video_sobe_trancado_e_o_aviso_manda_reenviar_pelo_app(self):
        """A saída real, não a que este arquivo prometia.

        Ele dizia "abra no Studio e mude a visibilidade — é um toque". É falso:
        a Ajuda do YouTube (support.google.com/youtube/answer/7300965) diz, de
        um vídeo trancado por upload de API não verificada, "you will not be
        able to appeal" e "you will not be able to change the video's state".
        O que existe é reenviar pelo app/site.
        """
        self.rede()
        falas = []
        saida = Y.publica(self.clipe(), titulo="ok", progresso=falas.append)
        self.assertEqual(saida["privacidade"], "private")
        self.assertIn("TRANCADO", saida["aviso"])
        self.assertIn("app ou pelo site do YouTube", saida["aviso"])
        self.assertIn("TRANCADO", "\n".join(falas))

    def test_quem_manda_na_privacidade_e_o_youtube_e_nao_o_pedido(self):
        """Pedimos público, ele gravou privado. Reportar o pedido seria mentir."""
        self.rede()
        saida = Y.publica(self.clipe(), titulo="ok", privacidade="public")
        self.assertEqual(saida["privacidade_pedida"], "public")
        self.assertEqual(saida["privacidade"], "private")
        self.assertTrue(any("gravou `private`" in a for a in saida["avisos"]))

    def test_pedido_public_negado_diz_por_que_e_o_que_fazer(self):
        """Não basta dizer "gravou privado": sozinho isso manda a pessoa
        procurar no Studio um botão que não existe."""
        self.rede()
        saida = Y.publica(self.clipe(), titulo="ok", privacidade="public")
        texto = "\n".join(saida["avisos"])
        self.assertIn("auditoria", texto)
        self.assertIn("app ou pelo site do YouTube", texto)
        self.assertIn("answer/7300965", texto)

    def test_quando_o_youtube_devolve_public_e_isso_que_o_relatorio_diz(self):
        """O outro lado da mesma trava: se um dia a auditoria passar e ele
        devolver `public`, o relatório precisa refletir ISSO — sem aviso de
        trancado e sem divergência inventada."""
        self.rede(put=resposta_video(privacyStatus="public"))
        saida = Y.publica(self.clipe(), titulo="ok", privacidade="public")
        self.assertEqual(saida["privacidade"], "public")
        self.assertEqual(saida["privacidade_pedida"], "public")
        self.assertEqual(saida["aviso"], "")
        self.assertFalse([a for a in saida["avisos"] if "gravou" in a])

    def test_o_relatorio_le_o_privacy_status_devolvido_e_nao_o_pedido(self):
        """A mutação que este teste pega: trocar `status["privacyStatus"]` por
        `privacidade` no retorno. Pedimos `private`, ele devolveu `unlisted` —
        se a saída disser `private`, alguém reportou o pedido."""
        self.rede(put=resposta_video(privacyStatus="unlisted"))
        saida = Y.publica(self.clipe(), titulo="ok", privacidade="private")
        self.assertEqual(saida["privacidade"], "unlisted")
        self.assertEqual(saida["privacidade_pedida"], "private")
        self.assertEqual(saida["aviso"], "")

    def test_a_fala_do_progresso_diz_de_quem_e_a_privacidade(self):
        """"privacidade `private`" não tem dono. Quem lê precisa saber se
        aquilo é a resposta do YouTube ou o nosso pedido."""
        self.rede()
        falas = []
        Y.publica(self.clipe(), titulo="ok", privacidade="public",
                  progresso=falas.append)
        linha = [f for f in falas if VIDEO_ID in f and "privacidade" in f]
        self.assertTrue(linha, "o progresso não disse como o vídeo ficou")
        self.assertIn("o YouTube devolveu", linha[0])

    def test_o_resultado_explica_o_que_faz_um_short(self):
        self.rede()
        saida = Y.publica(self.clipe(), titulo="ok")
        self.assertIn("vertical", saida["shorts"])
        self.assertIn("3 minutos", saida["shorts"])

    def test_cliente_apagado_no_envio_diz_o_que_fazer_e_que_nada_subiu(self):
        """A recusa de 15/09 no caminho do envio, não no do status.

        Se a renovação morre na hora de publicar, o que a pessoa precisa ler é
        (a) que o arquivo NÃO foi enviado e (b) que o conserto é reconectar —
        não um `deleted_client` cru saindo de um traceback de OAuth.
        """
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        rede = self.rede(refresh=[erro_oauth(
            "deleted_client", "The OAuth client was deleted.", 401)])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        msg = str(caixa.exception)
        self.assertIn("nada foi enviado", msg)
        self.assertIn("warden youtube connect", msg)
        self.assertIn("The OAuth client was deleted.", msg)
        self.assertEqual(caixa.exception.codigo, "deleted_client")
        # e o arquivo não chegou a sair da máquina
        self.assertEqual(rede.pedidos(Y.URL_UPLOAD), [])
        self.assertEqual(rede.pedidos(SESSAO), [])

    def test_o_segredo_nao_vaza_quando_a_renovacao_morre_no_envio(self):
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede(refresh=[erro_oauth(
            "invalid_client", f"client {SEGREDO_APP} is unknown", 401)])
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        for segredo in (ACESSO, REFRESH, SEGREDO_APP):
            self.assertNotIn(segredo, str(caixa.exception))

    def test_http_308_nao_vira_sucesso(self):
        """308 é 'recebi parte'. Não há retomada aqui, e fingir que subiu é pior."""
        self.rede(put=(308, b"", {"range": "bytes=0-100"}))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        self.assertIn("308", str(caixa.exception))
        self.assertIn("nada foi publicado", str(caixa.exception))

    def test_resposta_sem_id_nao_finge_que_deu_certo(self):
        self.rede(put=(200, json.dumps({"kind": "youtube#video"}).encode(), {}))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        self.assertIn("sem `id`", str(caixa.exception))

    def test_quota_exceeded_explica_os_seis_videos_por_dia(self):
        self.rede(upload=erro_api("quotaExceeded",
                                  "The request cannot be completed because you "
                                  "have exceeded your quota."))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        msg = str(caixa.exception)
        self.assertIn("quotaExceeded", msg)
        self.assertIn("exceeded your quota", msg)      # verbatim
        self.assertIn("1.600", msg)
        self.assertIn("SEIS", msg)

    def test_youtube_signup_required_manda_criar_o_canal(self):
        self.rede(upload=erro_api("youtubeSignupRequired", "Unauthorized"))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        self.assertIn("não tem canal", str(caixa.exception).lower())

    def test_motivo_desconhecido_repassa_e_admite_que_nao_sabe(self):
        self.rede(upload=erro_api("chuchuBeleza", "algo novo"))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        msg = str(caixa.exception)
        self.assertIn("chuchuBeleza", msg)
        self.assertIn("algo novo", msg)
        self.assertIn("Não há receita conhecida", msg)
        self.assertIn("não um diagnóstico nosso", msg)

    def test_resposta_que_nao_e_json_nao_vira_traceback(self):
        self.rede(upload=(502, b"<html>Bad Gateway</html>", {}))
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(self.clipe(), titulo="ok")
        self.assertIn("não é JSON", str(caixa.exception))
        self.assertIn("Bad Gateway", str(caixa.exception))

    def test_arquivo_inexistente(self):
        rede = self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(os.path.join(self.tmp, "nao-existe.mp4"), titulo="ok")
        self.assertIn("não há arquivo", str(caixa.exception))
        self.assertEqual(rede.chamadas, [])

    def test_arquivo_vazio(self):
        caminho = self.clipe(tamanho=0)
        self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(caminho, titulo="ok")
        self.assertIn("0 byte", str(caixa.exception))

    def test_extensao_que_este_modulo_nao_envia(self):
        caminho = os.path.join(self.tmp, "clipe.mkv")
        with open(caminho, "wb") as fh:
            fh.write(b"\0" * 10)
        self.rede()
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(caminho, titulo="ok")
        self.assertIn(".mp4", str(caixa.exception))

    def test_arquivo_acima_do_teto_recusa_em_voz_alta(self):
        """Não há retomada implementada, e carregar 1 GB na memória não é plano."""
        self.rede()
        caminho = self.clipe(tamanho=10)
        antigo = Y.TETO_ARQUIVO
        Y.TETO_ARQUIVO = 5
        self.addCleanup(setattr, Y, "TETO_ARQUIVO", antigo)
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            Y.publica(caminho, titulo="ok")
        self.assertIn("retomada", str(caixa.exception))

    def test_um_progresso_que_explode_nao_perde_o_video_id(self):
        def explode(_):
            raise RuntimeError("a CLI quebrou")
        self.rede()
        saida = Y.publica(self.clipe(), titulo="ok", progresso=explode)
        self.assertEqual(saida["video_id"], VIDEO_ID)


# ─────────────────────────────────────────────────────────────── o segredo

class OSegredo(Base):
    """Os TRÊS segredos: access_token, refresh_token e client_secret.

    Vazar qualquer um dos três é perder o canal, então os três passam pelo mesmo
    critério: nunca numa exceção, nunca numa linha de progresso.
    """

    SEGREDOS = (ACESSO, ACESSO_NOVO, REFRESH, REFRESH_NOVO, SEGREDO_APP,
                DEVICE_CODE)

    def confere(self, texto, onde):
        for segredo in self.SEGREDOS:
            self.assertNotIn(segredo, texto, f"segredo vazou em {onde}")

    def test_nenhum_dos_tres_segredos_aparece_em_mensagem_de_excecao(self):
        """Varredura: toda recusa que este módulo sabe levantar, conferida."""
        casos = []

        # 1. o Google ECOA o client_secret de volta na descrição do erro
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede(refresh=[erro_oauth(
            "invalid_client",
            f"Unauthorized client_secret={SEGREDO_APP}", http=401)])
        casos.append(self.pega(Y.token))

        # 2. o YouTube ecoa o access_token dentro da mensagem de erro
        self.grava_token()
        self.rede(upload=erro_api("authError", f"Invalid Credentials {ACESSO}",
                                  http=403))
        casos.append(self.pega(Y.publica, self.clipe(), titulo="ok"))

        # 3. corpo que não é JSON, repassado verbatim, com o refresh dentro
        self.grava_token()
        self.rede(upload=(500, f"erro no servidor: {REFRESH}".encode(), {}))
        casos.append(self.pega(Y.publica, self.clipe(), titulo="ok"))

        # 4. o polling do device flow ecoando o device_code
        self.rede(polling=[erro_oauth("access_denied",
                                      f"denied for {DEVICE_CODE}", http=403)])
        casos.append(self.pega(Y.conecta))

        self.assertEqual(len(casos), 4)
        for texto in casos:
            self.confere(texto, "exceção")
            self.assertIn("<segredo oculto>", texto)

    def pega(self, funcao, *args, **kw):
        with self.assertRaises(Y.YouTubeIndisponivel) as caixa:
            funcao(*args, **kw)
        return str(caixa.exception)

    def test_nenhum_dos_tres_segredos_chega_ao_progresso(self):
        """O caminho feliz inteiro, com cada linha de progresso conferida."""
        self.rede()
        falas = []
        Y.conecta(progresso=falas.append)
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        Y.publica(self.clipe(), titulo="ok", progresso=falas.append)
        self.assertTrue(falas)
        self.confere("\n".join(falas), "progresso")

    def test_o_user_code_NAO_e_censurado(self):
        """Ele existe para ser mostrado; censurá-lo torna a conexão impossível."""
        self.rede()
        falas = []
        Y.conecta(progresso=falas.append)
        self.assertIn(USER_CODE, "\n".join(falas))

    def test_a_excecao_limpa_a_mensagem_na_construcao(self):
        """A garantia não pode depender de quem escrever a próxima linha lembrar."""
        Y._guarda(ACESSO)
        erro = Y.YouTubeIndisponivel(f"vazou {ACESSO} aqui")
        self.assertNotIn(ACESSO, str(erro))
        self.assertIn("<segredo oculto>", str(erro))

    def test_um_segredo_curto_demais_nao_vira_censura_de_tudo(self):
        Y._guarda("abc")
        self.assertEqual(Y._limpa("abcdef"), "abcdef")

    def test_o_corpo_lido_pelo_programa_nao_passa_pela_censura(self):
        """O refresh que volta IGUAL já está registrado; censurar aqui gravaria
        `<segredo oculto>` no disco, que é perder a conta em silêncio."""
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede(refresh=[resposta_token(access_token=ACESSO_NOVO,
                                          refresh_token=REFRESH)])
        Y.token()
        self.assertEqual(self.le_token_do_disco()["refresh_token"], REFRESH)

    def test_arquivo_legivel_por_outros_vira_aviso_e_nao_recusa(self):
        self.grava_token()
        os.chmod(self.token_file, 0o644)
        self.rede()
        saida = Y.publica(self.clipe(), titulo="ok")
        self.assertEqual(saida["video_id"], VIDEO_ID)
        self.assertTrue(any("chmod 600" in a for a in saida["avisos"]))


# ─────────────────────────────────────────────────────────────── o status

class StatusConta(Base):
    """`status` afirma sobre o MUNDO, então ele pergunta ao mundo.

    Até 15/09/2026 esta função lia o disco e descrevia o disco, e chamava isso
    de "connected". No dia em que o dono rotacionou o cliente OAuth, ela disse
    "conectado ao canal Pod Cortes (...) renovação automática armada" no mesmo
    minuto em que a renovação devolvia `deleted_client`. Três afirmações, as
    três falsas, nenhuma verificada.

    A suíte continua sem tocar a rede: `_http` é trocado, como no resto do
    arquivo. O que mudou é que agora ELA PRECISA ser trocada — um `status` que
    não chama ninguém é um `status` que não sabe de nada, e é isso que os testes
    abaixo travam.
    """

    def rede_morta(self, erro="deleted_client",
                   descricao="The OAuth client was deleted.", http=401):
        """A rede do dia 15/09: o refresh volta recusado."""
        return self.rede(refresh=[erro_oauth(erro, descricao, http)])

    def rede_muda(self):
        """Ninguém atende. `_http` levanta com `codigo='sem_rede'`, que é como
        o módulo separa "o Google recusou" de "não deu para perguntar"."""
        def muda(*_a, **_kw):
            raise Y.YouTubeIndisponivel(
                "não deu para falar com o Google: [Errno 8] nodename nor "
                "servname provided, or not known", codigo="sem_rede")
        self._restaura("_http", muda)
        return muda

    # ─────────────────────────────────────────── o defeito medido em 15/09

    def test_cliente_apagado_nao_e_connected(self):
        """A medição, virada teste.

        POST oauth2.googleapis.com/token → 401 {"error": "deleted_client"}.
        O que NÃO pode sair daqui: "connected", "renovação automática armada",
        "renova sozinho". O que precisa sair: reconectar.
        """
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede_morta()
        ok, motivo = Y.status_conta()
        self.assertFalse(ok, "um token que não renova não está conectado")
        self.assertNotIn("renovação automática armada", motivo)
        self.assertNotIn("renova sozinho", motivo)
        self.assertIn("deleted_client", motivo)
        self.assertIn("warden youtube connect", motivo)

    def test_cliente_apagado_repete_a_palavra_do_google(self):
        """O diagnóstico é dele, verbatim, antes da nossa receita."""
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10))
        self.rede_morta()
        _ok, motivo = Y.status_conta()
        self.assertIn("The OAuth client was deleted.", motivo)

    def test_invalid_grant_e_invalid_client_tambem_reprovam(self):
        """Os outros dois jeitos de a credencial morrer. Nenhum é 'connected'."""
        for erro in ("invalid_grant", "invalid_client", "unauthorized_client"):
            with self.subTest(erro=erro):
                self.grava_token()
                self.rede_morta(erro, "seja lá o que for", 400)
                ok, motivo = Y.status_conta()
                self.assertFalse(ok, motivo)
                self.assertIn(erro, motivo)
                self.assertNotIn("renovação automática armada", motivo)

    def test_renovacao_ok_e_conectado_e_verificado(self):
        """O verde de verdade: a renovação foi TENTADA e o Google aceitou."""
        self.grava_token()
        rede = self.rede()
        ok, motivo = Y.status_conta()
        self.assertTrue(ok, motivo)
        self.assertIn("VERIFICADO", motivo)
        self.assertIn(CANAL, motivo)
        self.assertIn("testada agora", motivo)
        # e a prova de que não foi conversa: o POST de refresh aconteceu
        refresh = [c for c in rede.pedidos(Y.URL_TOKEN)
                   if b"refresh_token" in (c["corpo"] or b"")]
        self.assertTrue(refresh, "disse 'verificado' sem verificar nada")

    def test_sem_rede_nao_afirma_nem_uma_coisa_nem_outra(self):
        """Nem conectado nem quebrado: NÃO SEI, e é isso que ele diz."""
        self.grava_token()
        self.rede_muda()
        ok, motivo = Y.status_conta()
        self.assertFalse(ok, "não verificado não é conectado")
        self.assertIn("não consegui verificar", motivo)
        self.assertNotIn("VERIFICADO agora", motivo)
        self.assertNotIn("renovação automática armada", motivo)
        # e não pode acusar a conexão de quebrada, que é o erro oposto
        self.assertNotIn("não vale mais", motivo)

    def test_verificar_false_desliga_a_rede_e_nao_diz_conectado(self):
        """O modo offline: descreve o arquivo, e chama isso de NÃO verificado."""
        self.grava_token()
        rede = self.rede()
        ok, motivo = Y.status_conta(verificar=False)
        self.assertEqual(rede.chamadas, [], "verificar=False tocou a rede")
        self.assertFalse(ok, "sem verificar, 'conectado' é palpite")
        self.assertIn("NÃO verificado", motivo)
        self.assertIn(CANAL, motivo)

    def test_afirmar_renovacao_armada_exige_ter_renovado(self):
        """A trava da regressão, e ela não olha um caso: olha TODOS.

        Se a frase voltar a prometer renovação — "armada", "renova sozinho" —
        sem que um POST de refresh tenha saído, este teste fica vermelho. É o
        defeito de 15/09 escrito como invariante, e não como exemplo.
        """
        cenarios = {
            "vencido, refresh ok": (dict(expires_at=Y._iso(
                self.relogio.parede - 10)), self.rede),
            "válido, refresh ok": ({}, self.rede),
            "vencido, cliente apagado": (dict(expires_at=Y._iso(
                self.relogio.parede - 10)), self.rede_morta),
            "válido, cliente apagado": ({}, self.rede_morta),
            "sem data de validade": (dict(expires_at="ontem à tarde"),
                                     self.rede),
        }
        promessas = ("armada", "renova sozinho", "renovação automática")
        for nome, (campos, fabrica) in cenarios.items():
            with self.subTest(cenario=nome):
                self.grava_token(**campos)
                rede = fabrica()
                _ok, motivo = Y.status_conta()
                renovou = [c for c in rede.pedidos(Y.URL_TOKEN)
                           if b"grant_type=refresh_token" in (c["corpo"] or b"")]
                if any(p in motivo for p in promessas) and "SEM renovação" \
                        not in motivo:
                    self.assertTrue(
                        renovou,
                        f"[{nome}] a frase promete renovação e nenhum POST de "
                        f"refresh saiu: {motivo}")

    def test_offline_tambem_nao_promete_renovacao(self):
        """O mesmo invariante no caminho que NUNCA chama ninguém."""
        self.grava_token()
        self.rede()
        _ok, motivo = Y.status_conta(verificar=False)
        self.assertNotIn("renovação automática armada", motivo)
        self.assertNotIn("renova sozinho", motivo)

    # ──────────────────────────────────────────────── o que já era travado

    def test_conectado_diz_o_canal_e_a_trava_do_privado(self):
        self.grava_token()
        self.rede()
        ok, motivo = Y.status_conta()
        self.assertTrue(ok)
        self.assertIn(CANAL, motivo)
        # O status lembra a trava, e lembra a saída dela: só "PRIVADO" fazia a
        # pessoa achar que era uma caixinha desmarcada.
        self.assertIn("TRANCADO", motivo)
        self.assertIn("app ou site do YouTube", motivo)

    def test_sem_arquivo_diz_o_que_fazer(self):
        ok, motivo = Y.status_conta()
        self.assertFalse(ok)
        self.assertIn(self.token_file, motivo)
        self.assertIn("código", motivo)

    def test_sem_arquivo_nao_pergunta_nada_a_ninguem(self):
        """Não há o que verificar quando não há credencial. Poupa a rede e,
        principalmente, não inventa um estado para descrever."""
        rede = self.rede()
        ok, _motivo = Y.status_conta()
        self.assertFalse(ok)
        self.assertEqual(rede.chamadas, [])

    def test_sem_refresh_token_e_vencido_e_vermelho(self):
        self.grava_token(expires_at=Y._iso(self.relogio.parede - 10),
                         refresh_token=None)
        rede = self.rede()
        ok, motivo = Y.status_conta()
        self.assertFalse(ok)
        self.assertIn("refresh_token", motivo)
        self.assertEqual(rede.chamadas, [], "isto se decide sem perguntar")

    def test_sem_refresh_token_mas_valido_confere_o_acesso_e_avisa(self):
        """Ainda não dói, mas vai doer em uma hora. Melhor saber agora.

        Sem `refresh_token` não há renovação para testar, então o que dá para
        perguntar é se este access_token ainda serve — e é isso que ele pergunta,
        em vez de afirmar a partir do `expires_at` gravado.
        """
        self.grava_token(refresh_token=None)
        rede = self.rede()
        ok, motivo = Y.status_conta()
        self.assertTrue(ok, motivo)
        self.assertIn("SEM renovação automática", motivo)
        self.assertEqual(len(rede.pedidos(Y.URL_CANAL)), 1)

    def test_sem_refresh_token_com_acesso_recusado_reprova(self):
        self.grava_token(refresh_token=None)
        self.rede(canal=erro_api("authError", "Invalid Credentials", 401))
        ok, motivo = Y.status_conta()
        self.assertFalse(ok, motivo)
        self.assertIn("authError", motivo)

    def test_escopo_errado_e_motivo_diferente_de_vencido(self):
        self.grava_token(scope="https://www.googleapis.com/auth/youtube.readonly")
        rede = self.rede()
        ok, motivo = Y.status_conta()
        self.assertFalse(ok)
        self.assertIn("youtube.upload", motivo)
        self.assertEqual(rede.chamadas, [], "isto se decide sem perguntar")

    def test_modo_frouxo_e_aviso_dentro_de_um_ok(self):
        self.grava_token()
        os.chmod(self.token_file, 0o644)
        self.rede()
        ok, motivo = Y.status_conta()
        self.assertTrue(ok, motivo)
        self.assertIn("chmod 600", motivo)

    def test_nunca_levanta_nem_com_o_impossivel(self):
        """Um status que explode some com TODO o relatório, não só com o YouTube."""
        casos = []
        self.rede()

        with open(self.token_file, "w") as fh:
            fh.write("isto não é json {{{")
        casos.append(Y.status_conta())

        with open(self.token_file, "w") as fh:
            json.dump(["uma lista"], fh)
        casos.append(Y.status_conta())

        with open(self.token_file, "w") as fh:
            json.dump({"access_token": 12345}, fh)
        casos.append(Y.status_conta())

        os.unlink(self.token_file)
        os.mkdir(self.token_file)
        casos.append(Y.status_conta())
        os.rmdir(self.token_file)

        self.grava_token(expires_at="ontem à tarde")
        casos.append(Y.status_conta())

        # e os dois casos patológicos: explodir ao LER e explodir ao AVALIAR.
        # A exceção é de um tipo que este módulo não conhece de propósito —
        # `except Exception` tem de valer para o que ninguém previu, e um
        # `except` estreitado passaria batido se o teste usasse um tipo comum.
        self.grava_token()
        self._restaura("_modo_frouxo", self.impossivel)
        casos.append(Y.status_conta())
        self._restaura("_le_arquivo", self.impossivel)
        casos.append(Y.status_conta())

        self.assertEqual(len(casos), 7)
        for ok, motivo in casos[:4] + casos[5:]:
            self.assertFalse(ok, motivo)
            self.assertTrue(motivo.strip())
        # Data ilegível não é motivo para vermelho — e agora menos ainda: sem
        # saber quando vence, a renovação é TENTADA, e foi ela que respondeu.
        self.assertTrue(casos[4][0], casos[4][1])
        self.assertIn("VERIFICADO", casos[4][1])

    def test_verificacao_que_explode_nao_derruba_o_status(self):
        """O verificador tem `except Exception` por isto, e não por elegância."""
        self.grava_token()
        self._restaura("_renova", self.impossivel)
        ok, motivo = Y.status_conta()
        self.assertFalse(ok, motivo)
        self.assertTrue(motivo.strip())

    @staticmethod
    def impossivel(*_args, **_kw):
        raise Impossivel("ninguém previu isto")

    def test_o_segredo_nao_vaza_nem_no_status(self):
        self.grava_token()
        self.rede()
        _ok, motivo = Y.status_conta()
        for segredo in (ACESSO, REFRESH, SEGREDO_APP):
            self.assertNotIn(segredo, motivo)

    def test_o_segredo_nao_vaza_quando_o_google_recusa(self):
        """O endpoint de token ecoa o `client_id` dentro do error_description,
        e um dia vai ecoar outra coisa. A censura vale também no caminho ruim."""
        self.grava_token()
        self.rede_morta("invalid_client", f"client {SEGREDO_APP} is unknown", 401)
        _ok, motivo = Y.status_conta()
        for segredo in (ACESSO, REFRESH, SEGREDO_APP):
            self.assertNotIn(segredo, motivo)


class ContaSemCanal(Base):
    """Uma conta Google que autoriza tudo e não tem canal no YouTube.

    Medido em 15/09/2026 numa conta recém-criada: os dois escopos concedidos, o
    refresh_token gravado, e `channels?mine=true` com zero itens -- porque uma
    conta Google não ganha canal junto. O envio falharia lá na frente com
    `youtubeSignupRequired`, vários minutos e três passos depois.

    O que este teste trava é a HONESTIDADE do status: "conectado (canal não
    registrado)" era a mesma frase para dois estados, e um deles impede
    publicar. Chamar de conectado uma conta que não publica é a meia-verdade
    que faz a pessoa descobrir no meio de uma entrega.
    """

    def test_sem_canal_o_status_reprova_e_diz_o_que_fazer(self):
        self.grava_token(canal_ausente=True)
        ok, porque = Y.status_conta()
        self.assertFalse(ok, "uma conta que não publica não está 'conectada'")
        self.assertIn("não tem canal", porque)
        self.assertIn("youtube.com", porque)

    def test_consulta_que_nao_respondeu_nao_e_a_mesma_coisa(self):
        """O outro estado: a consulta falhou, mas o canal existe. Isso é
        cosmético e NÃO pode reprovar."""
        self.grava_token()
        self.rede()
        ok, porque = Y.status_conta()
        self.assertTrue(ok, "consulta falha não é conta sem canal")
        self.assertNotIn("não tem canal", porque)


# ───────────────────────────────────────── a promessa que não pode voltar

class NaoPrometePublicar(unittest.TestCase):
    """O vídeo trancado como privado NÃO é destrancado pelo dono.

    Este arquivo já prometeu o contrário, com estas palavras: "abra o vídeo no
    YouTube Studio e mude a visibilidade para Público — é um toque, e ninguém
    precisa reenviar nada." Foi publicado assim. É falso.

    A Ajuda do YouTube (support.google.com/youtube/answer/7300965) diz, sobre
    vídeo trancado por upload de API não verificada: "you will not be able to
    appeal", "Unlike user-selected private videos, you will not be able to
    change the video's state until after you have successfully submitted the
    video for re-review", e dá a saída — "re-upload the video via a verified
    API service or via the YouTube app/site".

    Os testes desta classe não olham comportamento: olham o TEXTO. Uma promessa
    volta por edição de texto, não por regressão de lógica, e o teste que só
    exercita `publica()` não a pega. Nada aqui toca a rede.
    """

    # Cada uma destas frases mediu um número de toques que ninguém mediu. A
    # primeira já foi publicada; as outras são o mesmo erro com outro número.
    CONTAGENS_DE_TOQUE = ("um toque", "1 toque", "dois toques", "três toques",
                          "3 toques", "one tap", "a tap", "three taps")

    def fonte(self):
        with open(Y.__file__, encoding="utf-8") as fh:
            return fh.read()

    def test_o_aviso_nao_conta_toques(self):
        baixo = Y.AVISO_PRIVADO.lower()
        for frase in self.CONTAGENS_DE_TOQUE:
            self.assertNotIn(frase, baixo,
                             f"o aviso voltou a prometer '{frase}' — o dono não "
                             "destranca este vídeo em toque nenhum")

    def test_o_aviso_nao_manda_mudar_a_visibilidade_no_studio(self):
        baixo = Y.AVISO_PRIVADO.lower()
        for frase in ("mude a visibilidade", "mudar a visibilidade",
                      "altere a visibilidade", "abra o vídeo no youtube studio"):
            self.assertNotIn(frase, baixo,
                             f"o aviso voltou a mandar '{frase}' — a Ajuda do "
                             "YouTube diz que esse estado não muda")

    def test_o_aviso_nao_diz_que_ninguem_precisa_reenviar(self):
        """Era a metade mais cara da mentira: reenviar é EXATAMENTE a saída."""
        self.assertNotIn("ninguém precisa reenviar", Y.AVISO_PRIVADO.lower())

    def test_o_aviso_manda_reenviar_pelo_app_ou_site(self):
        baixo = Y.AVISO_PRIVADO.lower()
        self.assertIn("mesmo arquivo", baixo)
        self.assertIn("app ou pelo site do youtube", baixo)

    def test_o_aviso_diz_que_nao_cabe_recurso_e_cita_a_fonte(self):
        baixo = Y.AVISO_PRIVADO.lower()
        self.assertIn("não cabe recurso", baixo)
        self.assertIn("support.google.com/youtube/answer/7300965", baixo)

    def test_o_aviso_avisa_que_pode_faltar_canal(self):
        """Medido em 15/09/2026: a conta Google nova do dono tinha ZERO canais.

        Mandar reenviar pelo app sem dizer isso é mandar a pessoa para uma tela
        que ela não esperava, no fim de uma entrega.
        """
        self.assertIn("criar o canal", Y.AVISO_PRIVADO.lower())

    # Palavras que, ao lado de "Studio", descrevem tornar o vídeo público.
    PROMESSA = ("públic", "publicar", "publique", "visibilidade")
    # E o que precisa estar NA MESMA FRASE para a menção ser honesta.
    NEGACAO = ("não", "nunca", "nem ", "falso", "not be able")

    def test_nenhuma_linha_junta_studio_com_tornar_publico(self):
        """Varre o ARQUIVO INTEIRO: 'Studio' só aparece negado ou como link.

        O link do Studio continua no resultado, e serve — para ver e editar
        título, descrição, miniatura. O que não pode voltar é uma linha que
        junte Studio com tornar o vídeo público sem dizer, ali mesmo, que isso
        não funciona.

        A unidade é a LINHA, e isso é de propósito. Duas alternativas foram
        medidas e recusadas:

          - janela de N caracteres em volta da palavra: o arquivo é cheio de
            "não" sobre outros assuntos, e a janela absolve a promessa com a
            negação do vizinho. Um comentário dizendo "basta abrir o YouTube
            Studio e deixar o clipe público" passou batido com 220 caracteres.
          - frase (corte em `.`): código quase não tem ponto final, então
            dezenas de linhas de `return {...}` viram uma frase só, do tamanho
            de meia função, e aí volta o problema da janela — com falso
            POSITIVO junto.

        O preço da linha é que a negação precisa caber ao lado de "Studio" na
        mesma linha. Isso aperta quem escreve, e aperta na direção certa.
        """
        culpadas = []
        for numero, linha in enumerate(self.fonte().splitlines(), 1):
            baixo = linha.lower()
            if "studio" not in baixo:
                continue
            if not any(p in baixo for p in self.PROMESSA):
                continue
            if not any(n in baixo for n in self.NEGACAO):
                culpadas.append(f"{numero}: {linha.strip()}")
        self.assertEqual(culpadas, [],
                         "alguma linha junta 'Studio' com tornar o vídeo "
                         "público sem negar que isso funciona; o dono NÃO "
                         "destranca este vídeo no Studio")

    def test_o_modulo_nao_conta_toques_em_nenhum_texto_entregue(self):
        """Os textos que a pessoa LÊ, todos, sem contagem de toque.

        `COMO_AUTORIZAR` pode dizer "toque em Avançado" — ali o toque existe e
        é na tela do Google. O que não pode é um NÚMERO de toques prometendo
        publicação.
        """
        textos = [Y.AVISO_PRIVADO, Y.COMO_AUTORIZAR, Y.SOBRE_SHORTS]
        textos += [v for v in Y.RECEITAS.values() if isinstance(v, str)]
        for texto in textos:
            baixo = texto.lower()
            for frase in self.CONTAGENS_DE_TOQUE:
                self.assertNotIn(frase, baixo,
                                 f"'{frase}' voltou a um texto entregue")

    def test_o_escopo_do_device_flow_tem_a_divergencia_registrada(self):
        """A doc do Device Flow não lista `youtube.upload`; a medição diz que
        ele foi concedido. As duas fontes ficam no arquivo, ao lado do escopo,
        para ninguém "consertar" isso no escuro e alargar a permissão."""
        fonte = self.fonte()
        self.assertIn("limited-input-device", fonte)
        self.assertIn("acb379e", fonte)
        self.assertIn("youtube.upload", Y.ESCOPOS)


# O guarda de execução direta fica no FIM, e não no meio, que era onde estava:
# tudo declarado depois dele não rodava em `python3 tests/test_youtube.py`.
# `unittest discover` importa o módulo inteiro e nunca sentiu falta, então o
# buraco era silencioso -- e as classes de baixo são justamente as que travam
# uma promessa que já foi publicada errada uma vez.
if __name__ == "__main__":
    unittest.main()
