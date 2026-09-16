# -*- coding: utf-8 -*-
"""A publicação pelo intermediário, sem tocar a rede de fora uma vez.

O servidor desta suíte é um `http.server` em 127.0.0.1, numa thread, e
`WARDEN_POST_BASE_URL` aponta para ele. Não é mock da função de rede: o
multipart é montado de verdade, sai por um socket de verdade e é REMONTADO do
outro lado. É o que permite afirmar que os campos chegam — um mock de `_http`
provaria que o dicionário estava certo antes de virar bytes, que é justamente a
parte que não quebra.

O que se mede aqui são as coisas que decaem em silêncio:

1. Sem `WARDEN_POST_API_KEY` o módulo está DESLIGADO — e desligado quer dizer
   que o servidor não recebe NADA. O teste conta as requisições que chegaram.
2. O que se relata é o que a API DEVOLVEU. Há um caso em que ela devolve
   privacidade diferente da pedida e um em que não devolve privacidade nenhuma;
   nos dois a saída tem que contar isso, e não ecoar o pedido.
3. `failed` não vira sucesso, nem quando o corpo se contradiz.
4. A chave não aparece. O caso do 401 devolve a chave inteira dentro do corpo
   do erro, que é exatamente o que um servidor real faz, e o teste confere que
   ela não sobreviveu à mensagem.
5. Título acima de 100 caracteres, arquivo inexistente e arquivo de 0 byte são
   recusados ANTES de qualquer requisição — de novo, contando o que chegou no
   servidor.

O que esta suíte NÃO prova, e nenhuma suíte provaria, está no relatório e no
cabeçalho de `warden_post.py`: o corpo real de `/upload` e o de `/status` não
foram medidos, e vídeo publicado só se confirma abrindo a URL deslogado.
"""
import email.parser
import email.policy
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden_post as P


def _temp(caso, prefix):
    """Um diretório temporário que se APAGA quando o teste acaba.

    Mesma função e mesma razão de `tests/test_warden.py`: em 14/09 a suíte
    tinha largado 4.939 diretórios e cerca de 60 GB em $TMPDIR. Nada consome em
    silêncio, nem a própria suíte.
    """
    caminho = tempfile.mkdtemp(prefix=prefix)
    caso.addCleanup(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


# A chave de mentira. Longa e reconhecível de propósito: é ela que os testes de
# sigilo procuram no texto de saída, e uma chave curta passaria despercebida
# tanto no `_limpa` quanto no `assertNotIn`.
CHAVE = "upk_ESTA_CHAVE_NAO_PODE_VAZAR_EM_LUGAR_NENHUM_1234567890"

# A resposta MEDIDA em 15/09/2026 contra a API real, copiada como veio.
ME = {"success": True, "message": "Token is valid",
      "email": "dono@example.com", "plan": "Default"}

USERS = {"success": True, "limit": 2, "plan": "default", "profiles": [
    {"username": "Clip-Warden", "social_accounts": {
        "tiktok": "",                       # não conectada vem como STRING VAZIA
        "youtube": {"display_name": "Pod Cortes", "handle": "@poddclipes",
                    "reauth_required": False}}}]}


# ──────────────────────────────────────────────────── um intermediário de mentira

class Servidor:
    """Um Upload-Post de mentira em 127.0.0.1, com fila de respostas por rota.

    `pedidos` guarda tudo que chegou — método, caminho, query, cabeçalhos e
    corpo — e é o que os testes de "não tocou a rede" consultam. Cada rota tem
    uma FILA: o último item se repete para sempre, que é como um status real se
    comporta depois de concluído.
    """

    def __init__(self, caso):
        self.pedidos = []
        self.filas = {
            "/api/uploadposts/me": [(200, ME)],
            "/api/uploadposts/users": [(200, USERS)],
            "/api/upload": [(200, {"success": True, "request_id": "req-42"})],
            "/api/uploadposts/status": [(200, {"success": True, "status": "completed"})],
        }
        servidor = self

        class Mao(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass                    # a suíte não é lugar de log de servidor

            def _atende(self, metodo):
                partes = urlparse(self.path)
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(tamanho) if tamanho else b""
                servidor.pedidos.append({
                    "metodo": metodo,
                    "caminho": partes.path,
                    "query": parse_qs(partes.query),
                    "cabecalhos": dict(self.headers),
                    "corpo": corpo,
                })
                codigo, dados = servidor._proxima(partes.path)
                bruto = (dados if isinstance(dados, bytes)
                         else json.dumps(dados).encode("utf-8"))
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(bruto)))
                self.end_headers()
                self.wfile.write(bruto)

            def do_GET(self):
                self._atende("GET")

            def do_POST(self):
                self._atende("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Mao)
        # `poll_interval` pequeno de propósito. O padrão é 0,5s e é o tempo que
        # `shutdown()` espera para voltar: com um servidor por teste isso
        # sozinho custava 14,5s dos 15s da suíte, medido em 15/09/2026.
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01},
            daemon=True)
        self.thread.start()
        caso.addCleanup(self._fecha)
        porta = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d/api" % porta

    def _fecha(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _proxima(self, caminho):
        fila = self.filas.get(caminho)
        if not fila:
            return 404, {"success": False, "message": "rota não roteada: " + caminho}
        return fila[0] if len(fila) == 1 else fila.pop(0)

    def responde(self, caminho, *respostas):
        """Troca a fila de uma rota. Cada resposta é (código, dict|bytes)."""
        self.filas[caminho] = list(respostas)

    def recebidos(self, caminho):
        return [p for p in self.pedidos if p["caminho"] == caminho]


def partes_multipart(pedido):
    """Remonta o corpo multipart que CHEGOU no servidor. -> lista de partes.

    Cada parte vira (nome, filename, bytes). Usa o parser de MIME da stdlib e
    não um `split` na fronteira: um split passaria num corpo mal formado que um
    servidor real recusaria.
    """
    tipo = pedido["cabecalhos"]["Content-Type"]
    bruto = (b"Content-Type: " + tipo.encode("utf-8")
             + b"\r\nMIME-Version: 1.0\r\n\r\n" + pedido["corpo"])
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(bruto)
    saida = []
    for parte in msg.iter_parts():
        saida.append((parte.get_param("name", header="content-disposition"),
                      parte.get_param("filename", header="content-disposition"),
                      parte.get_payload(decode=True)))
    return saida


class Base(unittest.TestCase):
    """Servidor de pé, ambiente isolado e nenhum sleep de verdade."""

    def setUp(self):
        self.servidor = Servidor(self)
        self.dir = _temp(self, prefix="warden-post-")
        self.clipe = os.path.join(self.dir, "clipe.mp4")
        # Bytes reconhecíveis, e binários de propósito: o teste do multipart
        # confere que chegaram INTACTOS, e texto puro esconderia um estrago de
        # codificação no caminho.
        self.conteudo = b"\x00\x01\x02mp4-de-mentira\xff\xfe"
        with open(self.clipe, "wb") as fh:
            fh.write(self.conteudo)
        self.ambiente(WARDEN_POST_API_KEY=CHAVE,
                      WARDEN_POST_BASE_URL=self.servidor.base,
                      WARDEN_POST_PROVIDER=None,
                      WARDEN_POST_PROFILE=None)
        # A memória de pedidos é estado de módulo e vazaria de um teste para o
        # outro; a comparação de privacidade depende dela e passaria por sorte.
        P._PEDIDOS.clear()
        self.addCleanup(P._PEDIDOS.clear)
        # Nenhuma espera de verdade: o backoff chega a 15s por volta e a suíte
        # inteira roda em segundos.
        dormiu = mock.patch.object(P, "_dorme", lambda s: self.dormidas.append(s))
        self.dormidas = []
        dormiu.start()
        self.addCleanup(dormiu.stop)

    def ambiente(self, **valores):
        """Ajusta variáveis de ambiente e as devolve ao que eram no fim."""
        for nome, valor in valores.items():
            antigo = os.environ.get(nome)
            self.addCleanup(self._restaura, nome, antigo)
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor

    @staticmethod
    def _restaura(nome, antigo):
        if antigo is None:
            os.environ.pop(nome, None)
        else:
            os.environ[nome] = antigo


# ─────────────────────────────────────────────── o interruptor: sem chave, nada

class SemChave(Base):
    """Sem `WARDEN_POST_API_KEY` o módulo está desligado, e desligado é literal."""

    def setUp(self):
        super().setUp()
        self.ambiente(WARDEN_POST_API_KEY=None)

    def test_toda_funcao_publica_recusa_e_diz_onde_por_a_chave(self):
        chamadas = [
            ("status", lambda: P.status()),
            ("publish_youtube", lambda: P.publish_youtube(self.clipe, "Título")),
            ("wait_for", lambda: P.wait_for("req-42")),
        ]
        for nome, chamada in chamadas:
            with self.subTest(funcao=nome):
                with self.assertRaises(P.PostIndisponivel) as erro:
                    chamada()
                texto = str(erro.exception)
                # Não basta recusar: a mensagem precisa dizer QUAL variável e
                # que ela não mora num arquivo do repositório — senão a pessoa
                # procura a chave onde ela nunca pode estar.
                self.assertIn("WARDEN_POST_API_KEY", texto)
                self.assertIn("repositório", texto)

    def test_nenhuma_requisicao_chega_no_servidor(self):
        for chamada in (lambda: P.status(),
                        lambda: P.publish_youtube(self.clipe, "Título"),
                        lambda: P.wait_for("req-42")):
            with self.assertRaises(P.PostIndisponivel):
                chamada()
        # O coração do teste. "Desligado" que ainda abre socket é um vazamento
        # de credencial esperando um dia ruim.
        self.assertEqual(self.servidor.pedidos, [])

    def test_esta_configurado_diz_nao_sem_abrir_socket(self):
        self.assertFalse(P.esta_configurado())
        self.assertEqual(self.servidor.pedidos, [])


# ──────────────────────────────────────────────────────────────────── status

class Status(Base):

    def test_le_perfis_contas_e_o_cabecalho_apikey(self):
        saida = P.status()
        self.assertEqual(saida["provedor"], "upload-post")
        self.assertEqual(saida["email"], "dono@example.com")
        self.assertEqual(saida["perfil"], "Clip-Warden")   # um só, resolve sozinho
        contas = saida["perfis"][0]["contas"]
        self.assertTrue(contas["youtube"]["conectada"])
        self.assertEqual(contas["youtube"]["handle"], "@poddclipes")
        # A esquisitice medida: rede não conectada vem como string vazia, não
        # como ausência e não como null.
        self.assertFalse(contas["tiktok"]["conectada"])
        # `Apikey`, não `Bearer`. Foi assim que o 200 de 15/09 veio.
        cabecalho = self.servidor.recebidos("/api/uploadposts/me")[0]["cabecalhos"]
        self.assertEqual(cabecalho["Authorization"], "Apikey " + CHAVE)

    def test_reauth_required_sobe_para_a_superficie(self):
        """Conta conectada que pede reautenticação NÃO publica, e parece normal."""
        users = json.loads(json.dumps(USERS))
        users["profiles"][0]["social_accounts"]["youtube"]["reauth_required"] = True
        self.servidor.responde("/api/uploadposts/users", (200, users))
        saida = P.status()
        self.assertEqual(saida["reauth"], ["Clip-Warden/youtube"])
        self.assertTrue(any("REAUTENTICAÇÃO" in a for a in saida["avisos"]))

    def test_dois_perfis_sem_escolha_viram_aviso_e_nao_escolha_sozinha(self):
        users = json.loads(json.dumps(USERS))
        users["profiles"].append({"username": "Outro-Canal", "social_accounts": {}})
        self.servidor.responde("/api/uploadposts/users", (200, users))
        saida = P.status()
        # `status` continua respondendo — é o comando que a pessoa roda para
        # descobrir que precisa escolher — mas não elege ninguém.
        self.assertIsNone(saida["perfil"])
        self.assertTrue(any("WARDEN_POST_PROFILE" in a for a in saida["avisos"]))

    def test_a_chave_nao_sai_na_saida_de_status(self):
        self.assertNotIn(CHAVE, json.dumps(P.status(), ensure_ascii=False))

    def test_provedor_desconhecido_nao_cai_no_padrao(self):
        self.ambiente(WARDEN_POST_PROVIDER="blotato")
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.status()
        self.assertIn("não tem implementação", str(erro.exception))
        self.assertEqual(self.servidor.pedidos, [])


# ───────────────────────────────────────────────────────────── o envio em si

class Publica(Base):

    def test_o_multipart_chega_com_os_campos_certos_e_os_bytes_intactos(self):
        saida = P.publish_youtube(self.clipe, "Clipe de teste",
                                  descricao="a descrição")
        self.assertEqual(saida["request_id"], "req-42")
        pedido = self.servidor.recebidos("/api/upload")[0]
        self.assertEqual(pedido["metodo"], "POST")
        partes = partes_multipart(pedido)
        campos = {nome: bruto for nome, arquivo, bruto in partes if arquivo is None}
        self.assertEqual(campos["user"], b"Clip-Warden")
        self.assertEqual(campos["platform[]"], b"youtube")
        self.assertEqual(campos["title"], "Clipe de teste".encode("utf-8"))
        self.assertEqual(campos["privacyStatus"], b"public")
        # `false`, e o número que decidiu isso está no comentário do campo em
        # `warden_post`: o MESMO clipe de 16 MB levou 9min51s com `true` --
        # parado na fila do worker durável deles, `attempts: 0` em três
        # consultas -- e 14,2s com `false`, voltando com a URL no corpo.
        # Medido em 16/09/2026, na conta real.
        self.assertEqual(campos["async_upload"], b"false")
        self.assertEqual(campos["youtube_description"], "a descrição".encode("utf-8"))
        # O arquivo: nome, e sobretudo os BYTES, iguais aos do disco.
        arquivos = [(a, b) for nome, a, b in partes if nome == "video"]
        self.assertEqual(arquivos, [("clipe.mp4", self.conteudo)])

    def test_a_saida_fala_em_privacidade_PEDIDA_e_nao_promete_publicado(self):
        saida = P.publish_youtube(self.clipe, "Clipe de teste")
        # O campo se chama `privacidade_pedida` de propósito: não existe aqui um
        # campo que afirme como o vídeo ficou, porque neste momento ninguém sabe.
        self.assertEqual(saida["privacidade_pedida"], "public")
        self.assertNotIn("privacidade", saida)
        self.assertIn("Aceito não é publicado", saida["proximo_passo"])

    def test_titulo_de_101_caracteres_e_recusado_antes_de_qualquer_requisicao(self):
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.publish_youtube(self.clipe, "a" * 101)
        texto = str(erro.exception)
        self.assertIn("101", texto)
        self.assertIn("100", texto)
        # Recusa, não corte. A frase precisa dizer isso: a próxima pessoa a
        # mexer aqui vai querer truncar, e é barato.
        self.assertIn("NÃO corta", texto)
        self.assertEqual(self.servidor.pedidos, [])

    def test_titulo_de_100_caracteres_passa(self):
        """O limite é 100, e o teste do limite trava o off-by-one dos dois lados."""
        P.publish_youtube(self.clipe, "a" * 100)
        self.assertEqual(len(self.servidor.recebidos("/api/upload")), 1)

    def test_arquivo_inexistente_e_recusado_antes_de_qualquer_requisicao(self):
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.publish_youtube(os.path.join(self.dir, "nao-existe.mp4"), "Título")
        self.assertIn("Nada foi enviado", str(erro.exception))
        self.assertEqual(self.servidor.pedidos, [])

    def test_arquivo_vazio_e_recusado_antes_de_qualquer_requisicao(self):
        vazio = os.path.join(self.dir, "vazio.mp4")
        open(vazio, "wb").close()
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.publish_youtube(vazio, "Título")
        self.assertIn("0 byte", str(erro.exception))
        self.assertEqual(self.servidor.pedidos, [])

    def test_titulo_vazio_e_privacidade_inventada_sao_recusados_sem_rede(self):
        with self.assertRaises(P.PostIndisponivel):
            P.publish_youtube(self.clipe, "   ")
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.publish_youtube(self.clipe, "Título", privacidade="secreto")
        # A lista de válidas vem da spec OpenAPI oficial, não de chute.
        self.assertIn("public, unlisted, private", str(erro.exception))
        self.assertEqual(self.servidor.pedidos, [])

    def test_perfil_do_ambiente_evita_a_consulta_de_perfis(self):
        self.ambiente(WARDEN_POST_PROFILE="Clip-Warden")
        P.publish_youtube(self.clipe, "Título")
        self.assertEqual(self.servidor.recebidos("/api/uploadposts/users"), [])

    def test_shorts_acrescenta_a_hashtag_e_AVISA_que_e_so_uma_dica(self):
        saida = P.publish_youtube(self.clipe, "Título", descricao="corpo",
                                  shorts=True)
        pedido = self.servidor.recebidos("/api/upload")[0]
        campos = {n: b for n, a, b in partes_multipart(pedido) if a is None}
        self.assertIn(b"#Shorts", campos["youtube_description"])
        self.assertTrue(any("DICA" in a for a in saida["avisos"]))

    def test_sem_request_id_nao_finge_que_deu_certo(self):
        self.servidor.responde("/api/upload", (200, {"success": True}))
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.publish_youtube(self.clipe, "Título")
        self.assertIn("sem `request_id`", str(erro.exception))


# ─────────────────────────────────────────────────────── o acompanhamento

class Espera(Base):

    def setUp(self):
        super().setUp()
        self.ambiente(WARDEN_POST_PROFILE="Clip-Warden")

    def _status(self, *respostas):
        self.servidor.responde("/api/uploadposts/status", *respostas)

    def test_sai_de_pending_para_completed_e_devolve_o_post_url(self):
        self._status(
            (200, {"success": True, "status": "pending"}),
            (200, {"success": True, "status": "pending"}),
            (200, {"success": True, "status": "completed", "results": {
                "youtube": {"success": True, "message": "Video uploaded",
                            "post_url": "https://youtu.be/abc123",
                            "privacyStatus": "public"}}}))
        P.publish_youtube(self.clipe, "Título")
        saida = P.wait_for("req-42", timeout_s=300)
        self.assertEqual(saida["status"], "completed")
        self.assertTrue(saida["concluido"])
        self.assertTrue(saida["sucesso"])
        self.assertEqual(saida["post_url"], "https://youtu.be/abc123")
        self.assertEqual(saida["plataformas"]["youtube"]["message"], "Video uploaded")
        # Três consultas, e o backoff aconteceu entre elas: não é laço apertado.
        self.assertEqual(len(self.servidor.recebidos("/api/uploadposts/status")), 3)
        self.assertEqual(self.dormidas, [P.ESPERA_INICIAL,
                                         P.ESPERA_INICIAL * P.ESPERA_FATOR])
        # O request_id vai na query, que é onde a API o espera.
        query = self.servidor.recebidos("/api/uploadposts/status")[0]["query"]
        self.assertEqual(query["request_id"], ["req-42"])

    def test_200_e_post_url_nao_sao_prova_e_a_saida_diz_isso(self):
        self._status((200, {"success": True, "status": "completed", "results": {
            "youtube": {"success": True, "post_url": "https://youtu.be/abc123",
                        "privacyStatus": "public"}}}))
        saida = P.wait_for("req-42")
        # A linha que este projeto inteiro existe para não esquecer.
        self.assertIn("JANELA ANÔNIMA", saida["prova"])
        self.assertIn("deslogado", saida["prova"])

    def test_failed_relata_a_falha_e_nao_inventa_sucesso(self):
        self._status(
            (200, {"success": True, "status": "pending"}),
            # O corpo se CONTRADIZ de propósito: `status: failed` com
            # `success: true` ao lado. `failed` manda.
            (200, {"success": True, "status": "failed", "results": {
                "youtube": {"success": True,
                            "message": "quota exceeded on the YouTube account"}}}))
        saida = P.wait_for("req-42")
        self.assertEqual(saida["status"], "failed")
        self.assertFalse(saida["sucesso"])
        self.assertIsNone(saida["post_url"])
        self.assertTrue(any("NÃO foi publicado" in a for a in saida["avisos"]))
        # A causa é da API, verbatim. Nenhuma frase daqui adivinha.
        self.assertTrue(any("quota exceeded" in a for a in saida["avisos"]))

    def test_privacidade_diferente_da_pedida_e_DENUNCIADA(self):
        """O caso que este módulo existe para pegar: pediu público, veio privado."""
        self._status((200, {"success": True, "status": "completed", "results": {
            "youtube": {"success": True, "post_url": "https://youtu.be/abc123",
                        "privacyStatus": "private"}}}))
        P.publish_youtube(self.clipe, "Título", privacidade="public")
        saida = P.wait_for("req-42")
        # O que vale é o que voltou.
        self.assertEqual(saida["privacidade"], "private")
        aviso = "\n".join(saida["avisos"])
        self.assertIn("ATENÇÃO", aviso)
        self.assertIn("'public'", aviso)
        self.assertIn("'private'", aviso)
        self.assertIn("não o que foi pedido", aviso)

    def test_sem_privacidade_na_resposta_nao_se_ecoa_o_pedido(self):
        self._status((200, {"success": True, "status": "completed", "results": {
            "youtube": {"success": True, "post_url": "https://youtu.be/abc123"}}}))
        P.publish_youtube(self.clipe, "Título", privacidade="public")
        saida = P.wait_for("req-42")
        # None, e não "public". Repetir o pedido aqui é a mentira que o
        # `youtube publish` conta hoje.
        self.assertIsNone(saida["privacidade"])
        self.assertTrue(any("não confirmou" in a for a in saida["avisos"]))

    def test_timeout_devolve_ainda_processando_e_nao_falha_nem_sucesso(self):
        self._status((200, {"success": True, "status": "pending"}))
        saida = P.wait_for("req-42", timeout_s=0)
        self.assertEqual(saida["status"], "pending")
        self.assertFalse(saida["concluido"])
        self.assertIsNone(saida["sucesso"])          # nem True, nem False
        self.assertTrue(any("AINDA ESTÁ PROCESSANDO" in a for a in saida["avisos"]))
        # Uma consulta só, e nenhuma espera: o teto já tinha acabado.
        self.assertEqual(len(self.servidor.recebidos("/api/uploadposts/status")), 1)
        self.assertEqual(self.dormidas, [])

    # ── o formato REAL, medido no primeiro envio de verdade (15/09/2026 15:32)

    def test_processing_nao_e_concluido(self):
        """O bug que a medição de 15/09 pegou, e o motivo de este teste existir.

        O estado intermediário do Upload-Post chama-se `processing`, não
        `pending`. A primeira versão de `wait_for` só continuava esperando
        enquanto fosse `pending`: ela saía do laço na PRIMEIRA consulta e
        chamava de concluído um envio que mal tinha começado — devolvendo
        `post_url: None` para um envio que ia dar certo.
        """
        self._status(
            (200, {"status": "processing", "results": [
                {"platform": "youtube", "status": "processing",
                 "attempts": 1, "success": False}]}),
            (200, {"status": "completed", "completed": 1, "total": 1, "results": [
                {"platform": "youtube", "success": True,
                 "post_url": "https://www.youtube.com/watch?v=lx9J_hD7nGA"}]}))
        saida = P.wait_for("req-42")
        self.assertEqual(saida["status"], "completed")
        self.assertTrue(saida["sucesso"])
        self.assertEqual(saida["post_url"],
                         "https://www.youtube.com/watch?v=lx9J_hD7nGA")
        # Duas consultas: ele ESPEROU o processing em vez de chamá-lo de pronto.
        self.assertEqual(len(self.servidor.recebidos("/api/uploadposts/status")), 2)

    def test_o_corpo_medido_em_15_09_e_lido_inteiro(self):
        """A resposta real, copiada como veio, com `results` em LISTA."""
        self._status((200, {
            "status": "completed", "completed": 1, "total": 1, "results": [{
                "platform": "youtube", "success": True,
                "platform_post_id": "lx9J_hD7nGA",
                "post_url": "https://www.youtube.com/watch?v=lx9J_hD7nGA",
                "error_message": None, "media_size_bytes": 14249812,
                "is_async": True,
                "prevalidation_metadata": {"width": 1080, "height": 1920,
                                           "fps": 30.0, "duration": 15.0,
                                           "video_codec": "h264"}}]}))
        P.publish_youtube(self.clipe, "Título", privacidade="public")
        saida = P.wait_for("req-42")
        self.assertTrue(saida["sucesso"])
        self.assertEqual(saida["platform_post_id"], "lx9J_hD7nGA")
        self.assertEqual(saida["prevalidacao"]["width"], 1080)
        self.assertEqual(saida["prevalidacao"]["video_codec"], "h264")
        # E o ponto que mais importa: a resposta REAL não traz privacidade
        # nenhuma. Então não se afirma que ficou público — pediu-se público e a
        # API não confirmou, e é isso que a saída diz.
        self.assertIsNone(saida["privacidade"])
        self.assertTrue(any("não confirmou" in a for a in saida["avisos"]))

    def test_o_upload_real_devolve_request_id_e_job_id(self):
        self.servidor.responde("/api/upload", (200, {
            "success": True,
            "message": "Upload queued successfully in the durable worker.",
            "request_id": "125924f68c5f42269bb081180df40d86",
            "job_id": "57a5caa0d9804e60ac5736a3077fb592",
            "total_platforms": 1}))
        saida = P.publish_youtube(self.clipe, "Título")
        self.assertEqual(saida["request_id"], "125924f68c5f42269bb081180df40d86")
        self.assertEqual(saida["job_id"], "57a5caa0d9804e60ac5736a3077fb592")

    def test_error_message_e_o_nome_do_campo_da_falha(self):
        """No formato real a causa chama-se `error_message`, não `message`."""
        self._status((200, {"status": "failed", "results": [
            {"platform": "youtube", "success": False,
             "error_message": "The YouTube account is not linked"}]}))
        saida = P.wait_for("req-42")
        self.assertFalse(saida["sucesso"])
        self.assertTrue(any("not linked" in a for a in saida["avisos"]))

    def test_sem_url_a_saida_diz_que_nao_da_para_conferir(self):
        self._status((200, {"success": True, "status": "completed", "results": {
            "youtube": {"success": True, "privacyStatus": "public"}}}))
        saida = P.wait_for("req-42")
        self.assertIsNone(saida["post_url"])
        self.assertTrue(any("não devolveu endereço" in a for a in saida["avisos"]))


# ──────────────────────────────────────────────────────────── o sigilo da chave

class Sigilo(Base):
    """A chave não sai daqui. Nem quando o servidor a devolve de volta."""

    def setUp(self):
        super().setUp()
        self.ambiente(WARDEN_POST_PROFILE="Clip-Warden")
        # O 401 que um servidor real manda: com a credencial recusada ECOADA
        # dentro do corpo. É o caso em que o vazamento acontece sem ninguém ter
        # escrito uma linha errada.
        self.corpo401 = {"success": False,
                         "message": "invalid api key: " + CHAVE + " (rejected)"}

    def test_401_repassa_codigo_e_corpo_mas_mascara_a_chave(self):
        for rota in ("/api/uploadposts/me", "/api/upload", "/api/uploadposts/status"):
            self.servidor.responde(rota, (401, self.corpo401))
        chamadas = [
            ("status", lambda: P.status()),
            ("publish_youtube", lambda: P.publish_youtube(self.clipe, "Título")),
            ("wait_for", lambda: P.wait_for("req-42")),
        ]
        for nome, chamada in chamadas:
            with self.subTest(funcao=nome):
                with self.assertRaises(P.PostIndisponivel) as erro:
                    chamada()
                texto = str(erro.exception)
                self.assertNotIn(CHAVE, texto)            # o teste inteiro é esta linha
                self.assertIn("<chave oculta>", texto)
                self.assertIn("401", texto)               # o código, verbatim
                self.assertIn("invalid api key", texto)   # a causa, dela e não nossa
                self.assertEqual(erro.exception.codigo, 401)

    def test_o_repr_da_excecao_tambem_esta_limpo(self):
        """`print(exc)` e `repr(exc)` são caminhos diferentes para a mesma saída."""
        self.servidor.responde("/api/uploadposts/me", (401, self.corpo401))
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.status()
        self.assertNotIn(CHAVE, repr(erro.exception))

    def test_a_chave_nao_aparece_na_saida_de_um_envio_que_deu_certo(self):
        saida = P.publish_youtube(self.clipe, "Título")
        self.assertNotIn(CHAVE, json.dumps(saida, ensure_ascii=False))
        saida = P.wait_for("req-42")
        self.assertNotIn(CHAVE, json.dumps(saida, ensure_ascii=False))

    def test_corpo_gigante_de_erro_e_truncado_e_ainda_assim_limpo(self):
        """Um HTML de erro de proxy não pode virar a saída inteira do comando."""
        lixo = ("<html>" + "x" * 5000 + CHAVE + "</html>").encode("utf-8")
        self.servidor.responde("/api/uploadposts/me", (500, lixo))
        with self.assertRaises(P.PostIndisponivel) as erro:
            P.status()
        texto = str(erro.exception)
        self.assertNotIn(CHAVE, texto)
        self.assertIn("caracteres)", texto)               # a marca do truncamento
        self.assertLess(len(texto), P.TETO_CORPO + 600)


if __name__ == "__main__":
    unittest.main()
