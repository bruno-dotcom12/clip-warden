# -*- coding: utf-8 -*-
"""O envio para a caixa de entrada do TikTok, sem tocar a rede uma vez.

A suíte inteira troca `warden_tiktok._http`, que é a única porta para fora do
processo. O que ela mede não é se o TikTok responde — isso foi medido à mão em
14/09/2026 e está registrado em `CRITERIO-TESTE-TIKTOK-INBOX.md` — e sim as
três coisas que decaem em silêncio quando ninguém trava nelas:

1. O corpo do init tem QUATRO campos. Não tem legenda, não tem título, não tem
   privacy_level. Um dia alguém vai achar que "faltou mandar a legenda" e
   acrescentar um campo; este arquivo é o que diz não.
2. Quando o TikTok recusa, quem diz a causa é o TikTok. As mensagens daqui
   repassam `error.code` e `error.message` verbatim e só DEPOIS dizem o que a
   pessoa faz. Há memória no repo sobre agente que inventou causa.
3. O token não aparece em lugar nenhum. Nem numa mensagem de erro, nem no PUT.

Cada teste foi conferido por mutação: quebrei de propósito a linha que ele
descreve e vi o teste ficar vermelho.
"""
import json
import os
import stat
import tempfile
import unittest

import warden_tiktok as T


PUBLISH_ID = "v_inbox_file~v2.7685480798933239816"      # o da medição de 14/09
UPLOAD_URL = "https://open-upload.tiktokapis.com/upload/?upload_id=abc123"
TOKEN = "act.exemploDeTokenQueNaoPodeVazarEmLugarNenhum123456"


# ───────────────────────────────────────────────────────── uma rede de mentira

class Rede:
    """Substitui `_http`. Guarda toda chamada e devolve o que lhe mandarem.

    `status` é a fila de `data.status` que o endpoint de status vai responder; o
    último valor se repete para sempre, que é o comportamento real de um envio
    que já chegou (em 14/09 ele respondeu SEND_TO_USER_INBOX 43 minutos depois).
    """

    def __init__(self, *, init=None, put=(201, b""), status=("SEND_TO_USER_INBOX",)):
        self.init = init if init is not None else (200, _ok({
            "publish_id": PUBLISH_ID, "upload_url": UPLOAD_URL}))
        self.put = put
        self.status = list(status)
        self.chamadas = []

    def __call__(self, metodo, url, *, corpo=None, cabecalhos=None, timeout=None):
        self.chamadas.append({"metodo": metodo, "url": url, "corpo": corpo,
                              "cabecalhos": dict(cabecalhos or {}),
                              "timeout": timeout})
        if url == T.URL_INIT:
            return self.init
        if url == T.URL_STATUS:
            valor = self.status[0] if len(self.status) == 1 else self.status.pop(0)
            if isinstance(valor, tuple):
                return valor
            return 200, _ok({"status": valor})
        return self.put

    # Açúcar para os testes lerem como frase.
    def pedidos(self, url):
        return [c for c in self.chamadas if c["url"] == url]

    def corpo_init(self):
        return json.loads(self.pedidos(T.URL_INIT)[0]["corpo"].decode("utf-8"))


def _ok(data):
    return json.dumps({"data": data,
                       "error": {"code": "ok", "message": "",
                                 "log_id": "2026091416504801"}}).encode("utf-8")


def _erro(code, message, http=400):
    return http, json.dumps({
        "data": {},
        "error": {"code": code, "message": message,
                  "log_id": "2026091416504899"}}).encode("utf-8")


class Relogio:
    """Um relógio que só anda quando alguém dorme.

    Sem isto o teste do teto de espera precisaria esperar de verdade. Com ele,
    `espera=20` e um intervalo de 6s dão três consultas e uma desistência, em
    zero segundo de parede.
    """

    def __init__(self):
        self.t = 1000.0
        self.dormiu = []

    def agora(self):
        return self.t

    def dorme(self, s):
        self.dormiu.append(s)
        self.t += s


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tiktok-")
        self.addCleanup(self._limpa_tmp)
        self.token_file = os.path.join(self.tmp, "tiktok.json")
        self.grava_token()
        self._restaura("TOKEN_FILE", self.token_file)
        self.relogio = Relogio()
        self._restaura("_agora", self.relogio.agora)
        self._restaura("_dorme", self.relogio.dorme)
        # Estado de módulo entre testes: a lista de inits contaria o teto de um
        # teste contra o seguinte, e o registro de segredos manteria vivo o
        # token de outro caso.
        T._INITS[:] = []
        T._SEGREDOS.clear()
        self.addCleanup(T._INITS.clear)
        self.addCleanup(T._SEGREDOS.clear)

    def _limpa_tmp(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _restaura(self, nome, valor):
        antigo = getattr(T, nome)
        setattr(T, nome, valor)
        self.addCleanup(setattr, T, nome, antigo)

    def grava_token(self, **campos):
        dados = {"access_token": TOKEN, "scope": "user.info.basic,video.upload",
                 "token_type": "Bearer"}
        dados.update(campos)
        with open(self.token_file, "w", encoding="utf-8") as fh:
            json.dump(dados, fh)
        os.chmod(self.token_file, 0o600)

    def clipe(self, nome="mbl-02-votaria-nele.mp4", tamanho=8933576):
        """Um arquivo do tamanho exato que foi medido subindo em 14/09."""
        caminho = os.path.join(self.tmp, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\0" * tamanho)
        return caminho

    def rede(self, **kw):
        rede = Rede(**kw)
        self._restaura("_http", rede)
        return rede


# ───────────────────────────────────────────────────────────── o caminho feliz

class CaminhoFeliz(Base):

    def test_init_put_status_termina_na_caixa_de_entrada(self):
        """Os três passos da medição, na ordem, terminando em SEND_TO_USER_INBOX."""
        rede = self.rede(status=("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"))
        caminho = self.clipe()
        falas = []
        saida = T.envia(caminho, progresso=falas.append)

        self.assertEqual([c["metodo"] for c in rede.chamadas],
                         ["POST", "PUT", "POST", "POST"])
        self.assertEqual(rede.chamadas[0]["url"], T.URL_INIT)
        self.assertEqual(rede.chamadas[1]["url"], UPLOAD_URL)
        self.assertEqual(rede.chamadas[2]["url"], T.URL_STATUS)

        self.assertEqual(saida["publish_id"], PUBLISH_ID)
        self.assertEqual(saida["status"], "SEND_TO_USER_INBOX")
        self.assertTrue(saida["concluido"])
        self.assertEqual(saida["bytes"], 8933576)
        self.assertEqual(saida["avisos"], [])

    def test_o_corpo_do_init_tem_exatamente_os_quatro_campos_medidos(self):
        """Nada de legenda, título ou privacy_level: o endpoint não os tem."""
        rede = self.rede()
        T.envia(self.clipe())
        corpo = rede.corpo_init()
        self.assertEqual(set(corpo), {"source_info"})
        self.assertEqual(corpo["source_info"], {
            "source": "FILE_UPLOAD",
            "video_size": 8933576,
            "chunk_size": 8933576,
            "total_chunk_count": 1})
        cru = rede.pedidos(T.URL_INIT)[0]["corpo"].decode("utf-8")
        for inventado in ("title", "caption", "description", "privacy_level",
                          "hashtag", "video_url"):
            self.assertNotIn(inventado, cru)

    def test_o_put_leva_content_range_e_o_tipo_do_arquivo(self):
        """`bytes 0-<n-1>/<n>` e video/mp4: o cabeçalho que subiu 201 na medição."""
        rede = self.rede()
        T.envia(self.clipe())
        cab = rede.pedidos(UPLOAD_URL)[0]["cabecalhos"]
        self.assertEqual(cab["Content-Range"], "bytes 0-8933575/8933576")
        self.assertEqual(cab["Content-Type"], "video/mp4")
        self.assertEqual(len(rede.pedidos(UPLOAD_URL)[0]["corpo"]), 8933576)

    def test_o_init_e_o_status_vao_com_bearer_e_o_put_nao(self):
        """A upload_url já vem assinada: segredo que não é enviado não vaza."""
        rede = self.rede()
        T.envia(self.clipe())
        self.assertEqual(rede.pedidos(T.URL_INIT)[0]["cabecalhos"]["Authorization"],
                         "Bearer " + TOKEN)
        self.assertEqual(rede.pedidos(T.URL_STATUS)[0]["cabecalhos"]["Authorization"],
                         "Bearer " + TOKEN)
        self.assertNotIn("Authorization", rede.pedidos(UPLOAD_URL)[0]["cabecalhos"])

    def test_toda_requisicao_tem_timeout(self):
        """Sem timeout, um envio pendurado pendura o agente inteiro."""
        rede = self.rede()
        T.envia(self.clipe())
        for chamada in rede.chamadas:
            self.assertIsInstance(chamada["timeout"], (int, float))
            self.assertGreater(chamada["timeout"], 0)

    def test_diz_em_voz_alta_que_a_legenda_nao_sobe_e_onde_o_rascunho_chega(self):
        """Custou 40 minutos ao dono em 14/09 procurar na aba Rascunhos."""
        self.rede()
        falas = []
        saida = T.envia(self.clipe(), progresso=falas.append)
        self.assertIn("legenda", saida["legenda"].lower())
        self.assertIn("não sobem", saida["legenda"].lower())
        self.assertIn("CAIXA DE ENTRADA", saida["onde"])
        self.assertIn("NÃO na aba Rascunhos", saida["onde"])
        # E não só no dicionário: quem só olha a saída na tela também ouve.
        self.assertTrue(any("legenda" in f.lower() for f in falas), falas)
        self.assertTrue(any("CAIXA DE ENTRADA" in f for f in falas), falas)

    def test_um_progresso_que_explode_nao_perde_o_publish_id(self):
        """O arquivo já está no TikTok; um print quebrado não pode custar o id."""
        self.rede()

        def ruim(_):
            raise RuntimeError("a CLI de alguém quebrou")

        saida = T.envia(self.clipe(), progresso=ruim)
        self.assertEqual(saida["publish_id"], PUBLISH_ID)


# ──────────────────────────────────────────────────────────── falta de token

class SemToken(Base):

    def test_arquivo_ausente_diz_o_caminho_e_o_escopo(self):
        os.remove(self.token_file)
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.token()
        texto = str(caso.exception)
        self.assertIn(self.token_file, texto)
        self.assertIn("video.upload", texto)
        self.assertNotIn(TOKEN, texto)

    def test_arquivo_sem_access_token_manda_gravar_a_resposta_inteira(self):
        with open(self.token_file, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": "x", "scope": "video.upload"}, fh)
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.token()
        self.assertIn("access_token", str(caso.exception))

    def test_json_corrompido_manda_regravar_e_nao_finge_que_e_falta_de_token(self):
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write("{isto nao e json")
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.token()
        texto = str(caso.exception)
        self.assertIn("JSON", texto)
        self.assertIn("Regrave", texto)

    def test_escopo_errado_manda_reautorizar_com_video_upload(self):
        """E avisa para NÃO pedir video.publish, que é a trava do Direct Post."""
        self.grava_token(scope="user.info.basic,video.publish")
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.token()
        texto = str(caso.exception)
        self.assertIn("video.upload", texto)
        self.assertIn("video.publish", texto)

    def test_escopo_ausente_nao_e_escopo_errado(self):
        """Arquivo que não conta o escopo não é arquivo com escopo ruim."""
        self.grava_token(scope=None)
        self.assertEqual(T.token(), TOKEN)

    def test_token_vencido_e_recusado_com_a_duracao_real(self):
        self.grava_token(expires_at=T._relogio() - 1)
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.token()
        self.assertIn("vencido", str(caso.exception))

    def test_expires_in_sozinho_nao_vence_nada(self):
        """`expires_in` é duração, não data: sem âncora não dá para concluir."""
        self.grava_token(expires_in=86400)
        self.assertEqual(T.token(), TOKEN)

    def test_envia_sem_token_nao_toca_a_rede(self):
        os.remove(self.token_file)
        rede = self.rede()
        with self.assertRaises(T.TikTokIndisponivel):
            T.envia(self.clipe())
        self.assertEqual(rede.chamadas, [])


# ───────────────────────────────────────────── o TikTok recusou, e ele diz por quê

class Recusas(Base):

    def _recusa(self, code, message="mensagem do TikTok"):
        self.rede(init=_erro(code, message))
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe())
        return str(caso.exception)

    def test_todo_codigo_conhecido_vira_uma_frase_acionavel(self):
        """O código e a mensagem DELE, verbatim, e depois o que a pessoa faz."""
        for code in T.RECEITAS:
            with self.subTest(code=code):
                texto = self._recusa(code, "mensagem crua do TikTok")
                self.assertIn(code, texto)
                self.assertIn("mensagem crua do TikTok", texto)
                self.assertIn(T.RECEITAS[code].format(arquivo=T.TOKEN_FILE), texto)

    def test_access_token_invalid_manda_refazer_a_autorizacao(self):
        texto = self._recusa("access_token_invalid", "The access token is invalid")
        self.assertIn("refaça a autorização", texto.lower())
        self.assertIn(self.token_file, texto)
        self.assertNotIn(TOKEN, texto)

    def test_scope_not_authorized_nomeia_o_escopo_certo_e_o_errado(self):
        texto = self._recusa("scope_not_authorized", "scope not authorized")
        self.assertIn("video.upload", texto)
        self.assertIn("video.publish", texto)

    def test_rate_limit_exceeded_cita_o_limite_medido(self):
        texto = self._recusa("rate_limit_exceeded", "too many requests")
        self.assertIn("6 por minuto", texto)

    def test_spam_risk_too_many_posts_manda_esvaziar_a_caixa(self):
        texto = self._recusa("spam_risk_too_many_posts", "spam risk")
        self.assertIn("5 rascunhos", texto)

    def test_url_ownership_unverified_explica_que_nao_e_o_nosso_modo(self):
        texto = self._recusa("url_ownership_unverified", "ownership unverified")
        self.assertIn("PULL_FROM_URL", texto)

    def test_codigo_desconhecido_repassa_e_admite_que_nao_sabe(self):
        """Inventar causa é o defeito que este repo tem memória de ter sofrido."""
        texto = self._recusa("codigo_que_ninguem_viu", "algo novo aconteceu")
        self.assertIn("codigo_que_ninguem_viu", texto)
        self.assertIn("algo novo aconteceu", texto)
        self.assertIn("Não há receita conhecida", texto)

    def test_o_log_id_do_tiktok_vai_junto(self):
        """É o número que o suporte deles pede; jogá-lo fora custa a conversa."""
        texto = self._recusa("access_token_invalid", "invalid")
        self.assertIn("2026091416504899", texto)

    def test_status_failed_repassa_o_fail_reason_dele(self):
        self.rede(status=(( 200, json.dumps({
            "data": {"status": "FAILED", "fail_reason": "file_format_check_failed"},
            "error": {"code": "ok", "message": ""}}).encode("utf-8")),))
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe())
        self.assertIn("file_format_check_failed", str(caso.exception))
        self.assertIn("FAILED", str(caso.exception))

    def test_put_que_nao_volta_2xx_diz_o_codigo_e_o_que_o_servidor_falou(self):
        self.rede(put=(403, b"SignatureDoesNotMatch"))
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe())
        texto = str(caso.exception)
        self.assertIn("403", texto)
        self.assertIn("SignatureDoesNotMatch", texto)

    def test_resposta_sem_publish_id_nao_finge_que_deu_certo(self):
        self.rede(init=(200, _ok({"upload_url": UPLOAD_URL})))
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe())
        self.assertIn("publish_id", str(caso.exception))

    def test_resposta_que_nao_e_json_nao_vira_traceback(self):
        self.rede(init=(502, b"<html>bad gateway</html>"))
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe())
        self.assertIn("502", str(caso.exception))


# ─────────────────────────────────────────────────────────── o arquivo em si

class ArquivoDeEntrada(Base):

    def test_arquivo_inexistente(self):
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(os.path.join(self.tmp, "nao-existe.mp4"))
        self.assertIn("nao-existe.mp4", str(caso.exception))

    def test_arquivo_vazio(self):
        vazio = self.clipe(tamanho=0)
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(vazio)
        self.assertIn("0 byte", str(caso.exception))

    def test_arquivo_maior_que_um_chunk_recusa_em_voz_alta(self):
        """Envio em várias partes nunca foi medido aqui; não se improvisa agora."""
        self.rede()
        grande = self.clipe(tamanho=T.TETO_CHUNK + 1)
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(grande)
        self.assertIn("chunk", str(caso.exception))

    def test_extensao_que_o_endpoint_nao_aceita(self):
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe(nome="clipe.gif", tamanho=10))
        self.assertIn(".mp4", str(caso.exception))


# ─────────────────────────────────────────────────────── o teto do polling

class Polling(Base):

    def test_estourar_o_teto_devolve_o_ultimo_status_em_vez_de_travar(self):
        rede = self.rede(status=("PROCESSING_UPLOAD",))
        saida = T.envia(self.clipe(), espera=20)
        self.assertEqual(saida["status"], "PROCESSING_UPLOAD")
        self.assertFalse(saida["concluido"])
        # 20s de teto a 6s por consulta: 6, 12, 18 e uma última encolhida para
        # caber nos 20. O teto é gasto inteiro e não é ultrapassado em 1s.
        self.assertEqual(len(rede.pedidos(T.URL_STATUS)), 4)
        self.assertEqual(self.relogio.t, 1020.0)
        self.assertTrue(any("PROCESSING_UPLOAD" in a for a in saida["avisos"]),
                        saida["avisos"])
        self.assertTrue(any("reconsulte" in a for a in saida["avisos"]),
                        saida["avisos"])

    def test_espera_zero_nao_consulta_e_admite_que_nao_consultou(self):
        rede = self.rede()
        saida = T.envia(self.clipe(), espera=0)
        self.assertEqual(rede.pedidos(T.URL_STATUS), [])
        self.assertIsNone(saida["status"])
        self.assertFalse(saida["concluido"])
        self.assertTrue(any("ninguém perguntou" in a for a in saida["avisos"]),
                        saida["avisos"])

    def test_para_assim_que_chega_na_caixa_de_entrada(self):
        """Nem uma consulta a mais: o limite de requisições é por token."""
        rede = self.rede(status=("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX",
                                 "SEND_TO_USER_INBOX"))
        T.envia(self.clipe(), espera=600)
        self.assertEqual(len(rede.pedidos(T.URL_STATUS)), 2)

    def test_o_tempo_de_upload_nao_come_o_prazo_de_perguntar(self):
        """Teto conta do fim do upload: um arquivo lento não some com o status."""
        rede = self.rede(status=("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"))
        relogio = self.relogio
        http = T._http

        def lento(metodo, url, **kw):
            if metodo == "PUT":
                relogio.t += 500        # meio milhar de segundos subindo
            return http(metodo, url, **kw)

        self._restaura("_http", lento)
        saida = T.envia(self.clipe(), espera=180)
        self.assertTrue(saida["concluido"])
        self.assertEqual(len(rede.pedidos(T.URL_STATUS)), 2)


# ───────────────────────────────────────────── seis por minuto, que é medido

class TetoDeInit(Base):

    def test_o_setimo_envio_no_mesmo_minuto_espera_a_janela_abrir(self):
        rede = self.rede()
        for _ in range(T.TETO_INIT):
            T.envia(self.clipe(), espera=0)
        self.assertEqual(len(rede.pedidos(T.URL_INIT)), T.TETO_INIT)
        antes = self.relogio.t
        T.envia(self.clipe(), espera=0)
        self.assertEqual(len(rede.pedidos(T.URL_INIT)), T.TETO_INIT + 1)
        self.assertGreaterEqual(self.relogio.t - antes, T.JANELA_INIT)

    def test_um_minuto_depois_a_janela_esta_limpa(self):
        rede = self.rede()
        for _ in range(T.TETO_INIT):
            T.envia(self.clipe(), espera=0)
        self.relogio.t += T.JANELA_INIT + 1
        antes = self.relogio.t
        T.envia(self.clipe(), espera=0)
        self.assertEqual(self.relogio.t, antes)      # não esperou nada
        self.assertEqual(len(rede.pedidos(T.URL_INIT)), T.TETO_INIT + 1)

    def test_relogio_parado_nao_vira_laco_infinito(self):
        """Se o tempo não andar, isto RECUSA — nunca gira para sempre."""
        self.rede()
        self._restaura("_dorme", lambda s: None)     # dormir sem o tempo andar
        for _ in range(T.TETO_INIT):
            T.envia(self.clipe(), espera=0)
        with self.assertRaises(T.TikTokIndisponivel) as caso:
            T.envia(self.clipe(), espera=0)
        self.assertIn("nada foi enviado", str(caso.exception))


# ──────────────────────────────────────────────────────────── o segredo, nunca

class OSegredo(Base):

    def test_o_token_nunca_aparece_em_mensagem_de_excecao(self):
        """Inclusive quando o próprio servidor ecoa o token de volta.

        Este é o teste que não confia em revisão de código: `TikTokIndisponivel`
        limpa a mensagem na CONSTRUÇÃO, então nenhuma linha futura precisa
        lembrar da regra.
        """
        casos = [
            lambda: self.rede(init=_erro("access_token_invalid",
                                         f"token {TOKEN} is invalid")),
            lambda: self.rede(init=(500, b"servidor caiu: " + TOKEN.encode())),
            lambda: self.rede(put=(403, b"denied for " + TOKEN.encode())),
            lambda: self.rede(status=((200, json.dumps({
                "data": {"status": "FAILED", "fail_reason": TOKEN},
                "error": {"code": "ok", "message": ""}}).encode()),)),
        ]
        for i, monta in enumerate(casos):
            with self.subTest(caso=i):
                monta()
                with self.assertRaises(T.TikTokIndisponivel) as caso:
                    T.envia(self.clipe())
                texto = str(caso.exception)
                self.assertNotIn(TOKEN, texto)
                self.assertIn("<token oculto>", texto)

    def test_a_excecao_limpa_a_mensagem_na_construcao(self):
        """A garantia que não depende de ninguém lembrar dela.

        Hoje o corpo de resposta já passa por `_limpa` antes de virar mensagem,
        então as duas camadas se cobrem e o teste de cima não distingue uma da
        outra. Este aqui é a de baixo, sozinha: qualquer linha futura que monte
        um texto com o token e levante TikTokIndisponivel continua limpa.
        """
        T._SEGREDOS.add(TOKEN)
        exc = T.TikTokIndisponivel(f"alguem escreveu {TOKEN} numa mensagem")
        self.assertNotIn(TOKEN, str(exc))
        self.assertIn("<token oculto>", str(exc))

    def test_o_progresso_limpa_a_mensagem_antes_de_falar(self):
        """A outra camada sozinha: nada vai para a tela sem passar por `_limpa`."""
        T._SEGREDOS.add(TOKEN)
        falas = []
        T._fala(falas.append, f"subindo com {TOKEN}")
        self.assertEqual(falas, ["subindo com <token oculto>"])

    def test_um_segredo_curto_demais_nao_vira_censura_de_tudo(self):
        """`_limpa` com uma string de 3 letras apagaria metade de cada mensagem."""
        T._SEGREDOS.add("abc")
        self.assertEqual(T._limpa("abcdef"), "abcdef")

    def test_o_token_nunca_chega_ao_progresso(self):
        self.rede(init=_erro("access_token_invalid", f"token {TOKEN} invalid"))
        falas = []
        with self.assertRaises(T.TikTokIndisponivel):
            T.envia(self.clipe(), progresso=falas.append)
        for fala in falas:
            self.assertNotIn(TOKEN, fala)

    def test_arquivo_legivel_por_outros_vira_aviso_e_nao_recusa(self):
        """Travar o envio por causa do modo trocaria um risco por um clipe perdido."""
        os.chmod(self.token_file, 0o644)
        self.rede()
        falas = []
        saida = T.envia(self.clipe(), progresso=falas.append)
        self.assertTrue(saida["concluido"])
        self.assertTrue(any("chmod 600" in a for a in saida["avisos"]),
                        saida["avisos"])
        self.assertTrue(any("chmod 600" in f for f in falas), falas)

    def test_modo_600_nao_gera_aviso(self):
        os.chmod(self.token_file, 0o600)
        self.rede()
        self.assertEqual(T.envia(self.clipe())["avisos"], [])


# ──────────────────────────────────────────── status_conta, que nunca levanta

class StatusConta(Base):

    def test_token_bom(self):
        ok, motivo = T.status_conta()
        self.assertTrue(ok)
        self.assertIn(self.token_file, motivo)
        self.assertNotIn(TOKEN, motivo)

    def test_sem_arquivo(self):
        os.remove(self.token_file)
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertIn("video.upload", motivo)

    def test_arquivo_corrompido_nao_levanta(self):
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write("\x00\x01 lixo binario que nao e json \xff")
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertTrue(motivo)

    def test_json_que_e_lista_nao_levanta(self):
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertTrue(motivo)

    def test_diretorio_no_lugar_do_arquivo_nao_levanta(self):
        os.remove(self.token_file)
        os.mkdir(self.token_file)
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertTrue(motivo)

    def test_escopo_errado_e_vencido_sao_motivos_diferentes(self):
        self.grava_token(scope="user.info.basic,video.publish")
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertIn("video.upload", motivo)

        self.grava_token(expires_at=T._relogio() - 1)
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertIn("vencido", motivo)

    def test_modo_frouxo_e_aviso_dentro_de_um_ok(self):
        os.chmod(self.token_file, 0o644)
        ok, motivo = T.status_conta()
        self.assertTrue(ok)
        self.assertIn("chmod 600", motivo)

    def test_nao_toca_a_rede(self):
        """`warden status` roda a toda hora; não pode gastar uma das 6 por minuto."""
        rede = self.rede()
        T.status_conta()
        self.assertEqual(rede.chamadas, [])

    def test_nunca_levanta_nem_com_o_impossivel(self):
        """Um `status` que explode some com o relatório inteiro, TikTok ou não."""
        def explode(*a, **kw):
            raise MemoryError("nada a ver com TikTok")

        self._restaura("_le_arquivo", explode)
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertIn("MemoryError", motivo)

        self._restaura("_validade", explode)
        self._restaura("_le_arquivo", lambda: {"access_token": TOKEN})
        ok, motivo = T.status_conta()
        self.assertFalse(ok)
        self.assertTrue(motivo)

    def test_permissao_do_arquivo_e_lida_sem_quebrar_quando_some_no_meio(self):
        os.remove(self.token_file)
        self.assertIsNone(T._modo_frouxo(self.token_file))


if __name__ == "__main__":
    unittest.main()
