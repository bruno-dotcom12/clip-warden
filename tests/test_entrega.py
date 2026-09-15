"""N clipes pedidos = N clipes recebidos, e a única prova disso que existe.

O defeito que esta suíte vigia foi medido em 15/09/2026 e ele é de duas partes,
uma em cada ponta da entrega.

A PRIMEIRA: a linha `MEDIA:` escrita numa mensagem INTERMEDIÁRIA -- a que sai
com `finish_reason=tool_calls`, porque o modelo ainda vai chamar outra
ferramenta -- é DESCARTADA pelo gateway. Aconteceu em 5 de 5 pedidos de mais de
um clipe. Na mensagem FINAL (`finish_reason=stop`) o anexo chega sempre: 8 de 8,
inclusive com duas linhas `MEDIA:` juntas na mesma mensagem. O texto chegava, o
arquivo não, e ninguém acusava -- nem o agente soube.

A SEGUNDA: `warden delivered` não verificava clipe nenhum. Ele contava QUALQUER
"Sending video attachment" no gateway.log desde que o clipe tinha sido liberado,
então num lote de dois o anexo do clipe 1 confirmava o clipe 2 -- o comando
riscava como entregue exatamente o clipe que se perdeu.

O conserto é ler o state.db do Hermes e perguntar por ARQUIVO: em qual mensagem
sua este caminho apareceu, e essa mensagem foi a final do turno? O state.db é de
outra equipe e o schema dele é interno, então metade dos testes aqui é sobre o
que acontece quando não dá para ler -- e a resposta nunca pode ser uma exceção
nem um silêncio que pareça confirmação.
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

os.environ.setdefault("WARDEN_DIR", tempfile.mkdtemp(prefix="warden-entrega-"))

import warden


def _temp(caso, prefix):
    """Um diretório temporário que se APAGA quando o teste acaba.

    A mesma regra da suíte principal, e ela existe porque não existia: um dia
    rodando a suíte deixou 4.939 diretórios e cerca de 60 GB em $TMPDIR.
    """
    caminho = tempfile.mkdtemp(prefix=prefix)
    caso.addCleanup(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


# O schema REAL, confirmado: messages(id, session_id, role, content, timestamp,
# finish_reason, ...) e sessions(id, started_at, ...). Escrito aqui à mão em vez
# de copiado de um dump porque o que se está testando é a LEITURA: se o Hermes
# mudar o schema, é este arquivo que tem de mudar junto, e o teste que prova a
# degradação honesta (`colunas=` abaixo) é o que impede a mudança de virar um
# traceback no meio de uma entrega.
COLUNAS = ["id INTEGER PRIMARY KEY", "session_id TEXT", "role TEXT",
           "content TEXT", "timestamp REAL", "finish_reason TEXT"]


def escreve_state_db(caminho, mensagens, colunas=None):
    """Um state.db de mentira. `mensagens` é [(role, content, finish_reason)]."""
    colunas = colunas or COLUNAS
    con = sqlite3.connect(caminho)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS sessions "
                    "(id TEXT PRIMARY KEY, started_at REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS messages (%s)" % ", ".join(colunas))
        nomes = [c.split()[0] for c in colunas]
        con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)",
                    ("sess-1", time.time()))
        for i, (role, content, finish) in enumerate(mensagens, 1):
            valores = {"id": i, "session_id": "sess-1", "role": role,
                       "content": content, "timestamp": time.time(),
                       "finish_reason": finish}
            con.execute("INSERT INTO messages (%s) VALUES (%s)"
                        % (", ".join(nomes), ", ".join("?" for _ in nomes)),
                        [valores.get(n) for n in nomes])
        con.commit()
    finally:
        con.close()
    return caminho


class _ComAsDuasPontas(unittest.TestCase):
    """Base: um WARDEN_DIR próprio, um state.db e um gateway.log apontados."""

    def setUp(self):
        self.dir = _temp(self, "warden-entrega-")
        self.estado = os.path.join(self.dir, "estado")
        os.makedirs(self.estado, exist_ok=True)
        self._env = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.estado
        self.addCleanup(self._repoe)
        self.db = os.path.join(self.dir, "state.db")
        self.log = os.path.join(self.dir, "gateway.log")
        self._db_antigo, self._log_antigo = warden.STATE_DB, warden.GATEWAY_LOG
        warden.STATE_DB, warden.GATEWAY_LOG = self.db, self.log
        self.addCleanup(setattr, warden, "STATE_DB", self._db_antigo)
        self.addCleanup(setattr, warden, "GATEWAY_LOG", self._log_antigo)
        self.clipe = self._arquivo("corte-01.mp4")

    def _repoe(self):
        if self._env is None:
            os.environ.pop("WARDEN_DIR", None)
        else:
            os.environ["WARDEN_DIR"] = self._env

    def _arquivo(self, nome):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"x")
        return caminho

    def _log_com(self, linhas):
        agora = time.strftime("%Y-%m-%d %H:%M:%S") + ",000"
        with open(self.log, "w", encoding="utf-8") as fh:
            for texto in linhas:
                fh.write(f"{agora} INFO gateway.platforms.base: {texto}\n")

    def _anexo_saiu(self, quantos=1):
        self._log_com(["[Plow_Chat] Delivering %d non-image MEDIA attachment(s)"
                       % quantos]
                      + ["[Plow_Chat] Sending video attachment (.mp4) to cht_x"]
                      * quantos)

    def _confirma(self, clipe=None):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = warden.cmd_delivered(
                type("A", (), {"clip": clipe or self.clipe})())
        return code, err.getvalue() + out.getvalue()


class OCaminhoNuncaFoiEscrito(_ComAsDuasPontas):
    """(a) Nenhuma mensagem citou o arquivo.

    Não é um envio que falhou, é um envio que nunca foi tentado -- e era o caso
    que o `warden delivered` antigo confirmava com mais facilidade, porque
    bastava QUALQUER anexo ter saído desde que o clipe foi liberado.
    """

    def test_arquivo_nunca_citado_nao_e_riscado(self):
        escreve_state_db(self.db, [
            ("user", "faz dois cortes", None),
            ("assistant", "renderizando", "stop")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        self.assertIn("NOT sent: nenhuma mensagem sua citou esse arquivo", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_o_anexo_de_outro_clipe_nao_confirma_este(self):
        """O defeito exato de 15/09, na sua forma mais cara.

        Dois clipes liberados, UM citado na mensagem final, um anexo no log.
        A conta antiga -- "saiu >= 1 desde que este clipe foi liberado" --
        riscava os dois, e o segundo era um arquivo que a pessoa não tinha.
        """
        dois = self._arquivo("corte-02.mp4")
        escreve_state_db(self.db, [
            ("assistant", f"Corte 1\nMEDIA:{os.path.abspath(self.clipe)}",
             "stop")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        warden.entregas_registra(dois)
        code, err = self._confirma(self.clipe)
        self.assertEqual(code, 1, err)          # ainda deve o segundo
        code, err = self._confirma(dois)
        self.assertEqual(code, 1, err)
        self.assertIn("nenhuma mensagem sua citou esse arquivo", err)
        pendentes = [r["clip"] for r in warden.entregas_pendentes()]
        self.assertEqual(pendentes, [os.path.abspath(dois)])


class OCaminhoFoiEscritoNoMeioDoTurno(_ComAsDuasPontas):
    """(b) O caso medido: a linha existe, o arquivo existe, o anexo foi jogado fora.

    5 de 5 pedidos de mais de um clipe. E o pior detalhe é que o gateway pode
    ter mandado OUTRO anexo no mesmo intervalo, então a conta antiga dizia que
    estava tudo certo.
    """

    def test_media_com_tool_calls_depois_nao_conta_como_envio(self):
        escreve_state_db(self.db, [
            ("assistant", f"aqui o primeiro\nMEDIA:{os.path.abspath(self.clipe)}",
             "tool_calls")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        self.assertIn("NOT sent: MEDIA escrito no meio do turno", err)
        self.assertIn("Esse anexo foi descartado", err)
        self.assertIn("mensagem FINAL", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_a_recusa_diz_a_hora_da_mensagem(self):
        """Sem a hora, "escrito no meio do turno" não aponta para nada.

        Numa conversa de vinte mensagens, saber QUAL delas carregava a linha é
        a diferença entre reescrever uma linha e reler a conversa inteira.
        """
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "tool_calls")])
        warden.entregas_registra(self.clipe)
        _code, err = self._confirma()
        import re
        self.assertRegex(err, r"msg às \d\d:\d\d")

    def test_a_ultima_mensagem_e_a_que_vale(self):
        """Escreveu no meio, refez no fim: o que vale é a mais recente."""
        caminho = os.path.abspath(self.clipe)
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{caminho}", "tool_calls"),
            ("assistant", f"desculpa, agora vai\nMEDIA:{caminho}", "stop")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        self.assertIn("confirmed", err)


class OCaminhoFoiEscritoNaMensagemFinal(_ComAsDuasPontas):
    """(c) A linha estava no lugar certo -- e aí o gateway.log é cruzado."""

    def test_mensagem_final_mais_anexo_no_log_confirma(self):
        escreve_state_db(self.db, [
            ("assistant", f"Corte 1: o lance\nMEDIA:{os.path.abspath(self.clipe)}",
             "stop")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        self.assertIn("confirmed", err)
        self.assertIn("final message", err)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_falha_de_envio_logo_depois_nao_confirma(self):
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop")])
        self._log_com([
            "[Plow_Chat] Delivering 1 non-image MEDIA attachment(s)",
            "[Plow_Chat] Failed to send media (.mp4): upstream refused"])
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        self.assertIn("NOT confirming", err)
        self.assertIn("Send it again", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_mensagem_final_sem_anexo_nenhum_no_log_nao_confirma(self):
        """As duas pontas discordando: só ler as duas responde isto."""
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop")])
        self._log_com(["[Plow_Chat] nothing to do with media"])
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        self.assertIn("no video attachment", err)
        self.assertIn("LAST message", err)

    def test_duas_linhas_media_na_mesma_mensagem_final_confirmam_as_duas(self):
        """8 de 8, medido em 15/09, inclusive com duas linhas juntas.

        É a medição inteira do conserto: "um clipe por turno" era uma regra
        inventada por este repositório, não do gateway.
        """
        dois = self._arquivo("corte-02.mp4")
        escreve_state_db(self.db, [
            ("assistant",
             f"Corte 1: a\nMEDIA:{os.path.abspath(self.clipe)}\n\n"
             f"Corte 2: b\nMEDIA:{os.path.abspath(dois)}", "stop")])
        self._anexo_saiu(2)
        warden.entregas_registra(self.clipe)
        warden.entregas_registra(dois)
        self.assertEqual(self._confirma(self.clipe)[0], 1)   # ainda deve o 2
        code, err = self._confirma(dois)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])


class QuandoNaoDaParaLerORegistro(_ComAsDuasPontas):
    """O state.db é de outra equipe, e o schema dele é interno.

    Toda falha de leitura tem de virar uma frase distinta dizendo O QUE não deu
    para ler -- nunca uma exceção, nunca um silêncio que pareça confirmação.
    "Não consegui olhar" não é "está tudo certo"; travar a entrega para sempre
    também não é resposta; então o comando risca e diz, em voz alta, que não
    verificou.
    """

    def _nao_verificou(self, trecho):
        warden.entregas_registra(self.clipe)
        self._anexo_saiu()
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        self.assertIn("NOT verified", err)
        self.assertIn(trecho, err)
        # E nunca afirma que chegou.
        self.assertNotIn("confirmed:", err)
        return err

    def test_state_db_ausente(self):
        warden.STATE_DB = os.path.join(self.dir, "nao-existe.db")
        self._nao_verificou("is not at")

    def test_state_db_com_schema_diferente(self):
        """Uma coluna a menos é uma atualização do Hermes, não um bug daqui."""
        escreve_state_db(self.db, [("assistant", "oi", None)],
                         colunas=["id INTEGER PRIMARY KEY", "role TEXT",
                                  "content TEXT", "timestamp REAL"])
        err = self._nao_verificou("finish_reason")
        self.assertIn("schema is internal", err)

    def test_state_db_sem_a_tabela_messages(self):
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE outra_coisa (id INTEGER)")
        con.commit()
        con.close()
        self._nao_verificou("no `messages` table")

    def test_state_db_corrompido(self):
        with open(self.db, "wb") as fh:
            fh.write(b"isto nao e um banco sqlite, e nem parece um\n" * 40)
        self._nao_verificou("sqlite")

    def test_state_db_que_e_um_diretorio(self):
        """O OSError do caminho impossível, que não é um erro do sqlite."""
        alvo = os.path.join(self.dir, "state-dir.db")
        os.makedirs(alvo, exist_ok=True)
        warden.STATE_DB = alvo
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        self.assertIn("NOT verified", err)

    def test_nenhuma_falha_de_leitura_vira_excecao(self):
        """A rede de segurança, provada de fora.

        Nenhum destes estados pode subir um traceback: um `sqlite3.Error` no
        meio de uma entrega é um lote que para por causa do registro de outra
        equipe.
        """
        casos = [os.path.join(self.dir, "some.db"), self.dir]
        with open(os.path.join(self.dir, "lixo.db"), "wb") as fh:
            fh.write(b"\x00\x01\x02 nao sou um banco")
        casos.append(os.path.join(self.dir, "lixo.db"))
        for caminho in casos:
            with self.subTest(caminho=caminho):
                warden.STATE_DB = caminho
                resposta = warden.mensagem_que_citou(self.clipe)
                self.assertEqual(resposta["estado"], "ilegivel")
                self.assertTrue(resposta["porque"])

    def test_gateway_ilegivel_com_state_db_bom_risca_e_diz(self):
        """Metade lida é melhor que nenhuma, e ela é dita como metade."""
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop")])
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        self.assertIn("NOT verified beyond that", err)


class ALeituraESoLeitura(_ComAsDuasPontas):
    """`mode=ro`, e é uma exigência, não um detalhe.

    O banco é de um processo que está escrevendo nele agora. Abrir para
    escrita cria arquivo, roda migração e mexe no journal de quem escreve.
    """

    def test_ler_nao_cria_o_arquivo(self):
        warden.STATE_DB = os.path.join(self.dir, "ainda-nao-existe.db")
        warden.mensagem_que_citou(self.clipe)
        self.assertFalse(os.path.exists(warden.STATE_DB))

    def test_ler_nao_escreve_no_banco(self):
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop")])
        antes = os.path.getsize(self.db)
        warden.mensagem_que_citou(self.clipe)
        self.assertEqual(os.path.getsize(self.db), antes)
        self.assertFalse(os.path.exists(self.db + "-wal"))
        self.assertFalse(os.path.exists(self.db + "-journal"))


class OBlocoDaMensagemFinal(unittest.TestCase):
    """O texto que o modelo copia, e a ordem que ele substituiu.

    A saída antiga do lote mandava entregar "um clipe por turno, MEDIA: na
    última mensagem de cada". A medição de 15/09 diz que isso é fazer em N
    turnos o que cabe em um -- e que o único jeito de perder anexo é escrever a
    linha antes do fim do turno. O bloco abaixo é a ordem certa em forma de
    texto pronto para colar.
    """

    def _bloco(self, liberados, **kw):
        err = io.StringIO()
        with redirect_stderr(err):
            warden.bloco_da_mensagem_final(liberados, **kw)
        return err.getvalue()

    def test_o_bloco_traz_todos_os_caminhos_numa_mensagem_so(self):
        texto = self._bloco([("a.mp4", "/clipes/a.mp4"),
                             ("b.mp4", "/clipes/b.mp4")])
        self.assertIn("END YOUR TURN NOW", texto)
        self.assertIn("no tool call after it", texto)
        self.assertIn("Corte 1: <caption>", texto)
        self.assertIn("MEDIA:/clipes/a.mp4", texto)
        self.assertIn("Corte 2: <caption>", texto)
        self.assertIn("MEDIA:/clipes/b.mp4", texto)
        # e a ordem é a ordem dos clipes
        self.assertLess(texto.index("/clipes/a.mp4"), texto.index("/clipes/b.mp4"))

    def test_o_bloco_nao_manda_entregar_um_por_turno(self):
        texto = self._bloco([("a.mp4", "/clipes/a.mp4"),
                             ("b.mp4", "/clipes/b.mp4")])
        self.assertNotIn("one per turn", texto)
        self.assertNotIn("one clip per turn", texto)
        self.assertIn("ONE message carrying all 2", texto)

    def test_sem_clipe_nenhum_o_bloco_nao_sai(self):
        """Um "termine o turno agora" sem nada para anexar é uma ordem vazia."""
        self.assertEqual(self._bloco([]), "")

    def test_o_bloco_tambem_vai_para_disco_quando_pedido(self):
        destino = os.path.join(_temp(self, "warden-bloco-"), "bloco.txt")
        texto = self._bloco([("a.mp4", "/clipes/a.mp4")], arquivo=destino)
        self.assertIn(destino, texto)
        with open(destino, encoding="utf-8") as fh:
            gravado = fh.read()
        self.assertIn("MEDIA:/clipes/a.mp4", gravado)


class OLoteImprimeOBlocoNoFim(unittest.TestCase):
    """O `cut --plan` termina com o bloco, e não com um convite a repassar.

    Sem rede e sem ffmpeg: o render é trocado por um dublê, porque o que se
    está provando é o TEXTO do fim do lote, e não o vídeo.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-plano-")
        self.estado = os.path.join(self.dir, "estado")
        os.makedirs(self.estado, exist_ok=True)
        self._env = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.estado
        self.addCleanup(self._repoe)
        self._media_antigo = warden._media

        def pronto(source, out, regras, start, end, **kw):
            with open(out, "wb") as fh:
                fh.write(b"\x00" * 64)
            folha = os.path.splitext(out)[0] + "-contato.jpg"
            with open(folha, "wb") as fh:
                fh.write(b"\x00")
            return {"out": out, "notes": [], "sheet": folha,
                    "style": {"caption": None}, "style_breaches": [],
                    "asked_for_captions": False}

        falso = type("M", (), {"cut": staticmethod(pronto)})
        warden._media = lambda: falso
        self.addCleanup(setattr, warden, "_media", self._media_antigo)
        self._probe_antigo = warden.probe
        warden.probe = lambda caminho: {
            "width": 1080, "height": 1920, "duration_s": 3.0, "fps": "30/1",
            "codec": "h264", "audio_codec": None, "subtitle_tracks": 0,
            "size_mb": 1.0}
        self.addCleanup(setattr, warden, "probe", self._probe_antigo)

    def _repoe(self):
        if self._env is None:
            os.environ.pop("WARDEN_DIR", None)
        else:
            os.environ["WARDEN_DIR"] = self._env

    def _plano(self, quantos):
        corpo = {"source": os.path.join(self.dir, "src.mp4"),
                 "sound": "platform", "seconds": 3,
                 "clips": [{"out": f"p{i}.mp4", "start": i, "end": i + 3}
                           for i in range(quantos)]}
        caminho = os.path.join(self.dir, "plano.json")
        with open(caminho, "w") as fh:
            json.dump(corpo, fh)
        return caminho

    def _roda(self, quantos):
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.main(["cut", "--plan", self._plano(quantos)])
        return code, saida.getvalue(), erro.getvalue()

    def test_o_lote_termina_com_o_bloco_para_copiar(self):
        code, saida, erro = self._roda(2)
        self.assertEqual(code, 0, erro)
        self.assertIn("END YOUR TURN NOW", erro)
        depois = erro.split("END YOUR TURN NOW", 1)[1]
        self.assertEqual(depois.count("MEDIA:"), 2, depois)
        # o stdout continua com um MEDIA: por clipe, que é o que o `deliver`
        # imprime clipe a clipe; o bloco não o duplica.
        self.assertEqual(saida.count("MEDIA:"), 2)

    def test_o_lote_nao_convida_a_entregar_um_por_turno(self):
        _code, saida, erro = self._roda(2)
        tudo = saida + erro
        self.assertNotIn("one per turn", tudo)
        self.assertNotIn("one clip per turn", tudo)

    def test_o_lote_manda_rodar_delivered_no_turno_seguinte(self):
        """A condição impossível, desfeita.

        `warden delivered` saía 1 enquanto houvesse clipe devendo e a saída
        mandava "não termine o turno enquanto este comando sair 1" -- mas o
        anexo só sai QUANDO o turno termina. Os dois ficavam esperando um pelo
        outro, e foi assim que o pedido de 15/09 travou.
        """
        _code, _saida, erro = self._roda(2)
        self.assertIn("NEXT turn", erro)
        self.assertNotIn("Do not end your turn while this command exits 1", erro)

    def test_acima_de_tres_clipes_o_plano_avisa_que_lote_render_divide(self):
        _code, _saida, erro = self._roda(4)
        self.assertIn("lote render", erro)
        self.assertIn(str(warden.LOTE_INLINE), erro)


class OQueDeliveredDizQuandoFaltaAlguem(_ComAsDuasPontas):
    def test_a_frase_final_manda_rodar_no_inicio_do_turno_seguinte(self):
        warden.entregas_registra(self.clipe)
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = warden.cmd_delivered(type("A", (), {"clip": None})())
        texto = err.getvalue() + out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("Rode este comando no INÍCIO do turno seguinte", texto)
        self.assertIn("não pode confirmar um envio que ainda não aconteceu",
                      texto)
        self.assertNotIn("Do not end your turn while this command exits 1",
                         texto)

    def test_todas_as_linhas_na_mesma_mensagem_final(self):
        warden.entregas_registra(self.clipe)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            warden.cmd_delivered(type("A", (), {"clip": None})())
        self.assertIn("SAME last", err.getvalue())


class OComandoQueAPersonaMandaUsarPrecisaExistir(unittest.TestCase):
    """`warden post youtube` era um comando que a persona mandava usar e que
    não existia.

    O custo não era o erro, era a FORMA do erro. `invalid choice: 'post'` sai
    com código 2, e 2 é erro de uso do comando -- não é o estado "desligado"
    que a persona previu. A rota de fallback dela nunca era acionada, então o
    agente seguia prometendo uma publicação que não ia acontecer, que é o mesmo
    defeito do `warden youtube publish` por outra porta: um 200 da API não é um
    vídeo público.

    O módulo `warden_post` é de outro dono e tem a sua própria suíte. O que
    está coberto aqui é só a LIGAÇÃO: que o subcomando existe e que o caminho
    de "não configurado" é o que a persona espera.
    """

    def _roda(self, argv, com_chave=False):
        antes = os.environ.get("WARDEN_POST_API_KEY")
        if com_chave:
            os.environ["WARDEN_POST_API_KEY"] = "chave-de-teste"
        else:
            os.environ.pop("WARDEN_POST_API_KEY", None)
        try:
            saida, erro = io.StringIO(), io.StringIO()
            with redirect_stdout(saida), redirect_stderr(erro):
                try:
                    code = warden.main(argv)
                except SystemExit as saiu:
                    code = saiu.code
            return code, saida.getvalue() + erro.getvalue()
        finally:
            if antes is None:
                os.environ.pop("WARDEN_POST_API_KEY", None)
            else:
                os.environ["WARDEN_POST_API_KEY"] = antes

    def test_o_subcomando_existe(self):
        """O que quebrava era o argparse, antes de qualquer lógica rodar."""
        code, texto = self._roda(["post", "status"])
        self.assertNotIn("invalid choice", texto)
        self.assertNotEqual(code, 2, texto)

    def test_sem_chave_sai_1_e_diz_que_nada_foi_tentado(self):
        """1 e não 2 de propósito: quem não configurou o ambiente não usou
        errado o comando, e é o código que separa "desligado" de "erro meu"."""
        for argv in (["post", "status"],
                     ["post", "youtube", "/tmp/nao-existe.mp4", "--title", "t"]):
            with self.subTest(argv=argv):
                code, texto = self._roda(argv)
                self.assertEqual(code, 1, texto)
                self.assertIn("intermediary is OFF", texto)
                self.assertIn("nothing was sent and nothing was tried", texto)
                # e diz o que fazer em vez disso, que é a rota de fallback
                self.assertIn("final message", texto)
                self.assertIn("do NOT claim anything was published", texto)

    def test_sem_chave_nao_sobe_traceback(self):
        code, texto = self._roda(["post", "youtube", "/tmp/x.mp4", "--title", "t"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", texto)
        self.assertNotIn("Error:", texto)

    def test_youtube_publish_aponta_para_o_comando_que_publica(self):
        """Os dois comandos passaram a parecer a mesma coisa, e o que mente é
        o mais antigo: o upload dele fica trancado como privado sem recurso."""
        import warden_youtube as Y
        fonte = io.StringIO()
        antigo = Y.publica
        Y.publica = lambda *a, **kw: {"url": "https://youtu.be/abc",
                                      "avisos": []}
        self.addCleanup(setattr, Y, "publica", antigo)
        alvo = os.path.join(_temp(self, "warden-pub-"), "clipe.mp4")
        with open(alvo, "wb") as fh:
            fh.write(b"x")
        err = io.StringIO()
        with redirect_stdout(fonte), redirect_stderr(err):
            code = warden.main(["youtube", "publish", alvo, "--title", "t"])
        self.assertEqual(code, 0)
        self.assertIn("locked as PRIVATE", err.getvalue())
        self.assertIn("warden post youtube", err.getvalue())
        # e não promete nada sobre a chave, que não é assunto desta saída
        self.assertNotIn("WARDEN_POST_API_KEY", err.getvalue())


class UmClipeREPROVADONaoDeixaCaminhoNenhumNoStdout(unittest.TestCase):
    """O stdout de uma reprovação era UMA linha, e era o caminho do arquivo.

    `deliver` imprimia `result["out"]` ANTES dos portões. Numa reprovação o
    comando saía 1 e ainda assim deixava no stdout uma linha no mesmo formato
    da primeira linha de um sucesso -- um caminho de mp4, pronto para ser
    citado. E `runtime/persona.md` diz o contrário, com todas as letras: "if
    `warden cut` exited non-zero there is no line to send and no clip to
    describe". Havia: o clipe que o portão acabou de reprovar.

    O custo é a entrega errada mais cara que este projeto conhece -- o agente
    manda ao dono exatamente o arquivo que a campanha recusou.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-reprova-")
        self.estado = os.path.join(self.dir, "estado")
        os.makedirs(self.estado, exist_ok=True)
        self._env = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.estado
        self.addCleanup(self._repoe)
        self._probe_antigo = warden.probe
        self.addCleanup(setattr, warden, "probe", self._probe_antigo)

    def _repoe(self):
        if self._env is None:
            os.environ.pop("WARDEN_DIR", None)
        else:
            os.environ["WARDEN_DIR"] = self._env

    def _regras(self, **video):
        import warden_rules as R
        r = R.blank()
        r.update({"id": "t", "name": "t", "schema": 1})
        r["video"].update(dict({"duration_min_s": 1, "duration_max_s": 5,
                                "width": 1080, "height": 1920,
                                "audio": "forbidden"}, **video))
        r["caption"].update({"required_hashtags": [], "required_mentions": [],
                             "banned_terms": []})
        return r

    def _resultado(self, com_mosaico=True):
        clipe = os.path.join(self.dir, "corte.mp4")
        with open(clipe, "wb") as fh:
            fh.write(b"\x00" * 16)
        folha = os.path.join(self.dir, "corte-contato.jpg")
        if com_mosaico:
            with open(folha, "wb") as fh:
                fh.write(b"\x00")
        return {"out": clipe, "notes": [], "sheet": folha if com_mosaico else None,
                "style": {"caption": None}, "style_breaches": [],
                "asked_for_captions": False}

    def _entrega(self, regras, result):
        warden.probe = lambda caminho: {
            "width": 1080, "height": 1920, "duration_s": 3.0, "fps": "30/1",
            "codec": "h264", "audio_codec": None, "subtitle_tracks": 0,
            "size_mb": 1.0}
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.deliver(result, regras, None, [])
        return code, saida.getvalue(), erro.getvalue()

    def test_reprovado_pela_campanha_nao_imprime_nada_no_stdout(self):
        # 3s num clipe que tem de ter no máximo 2: REJECT.
        code, saida, erro = self._entrega(self._regras(duration_max_s=2),
                                          self._resultado())
        self.assertEqual(code, 1, erro)
        self.assertEqual(saida, "", saida)
        self.assertIn("does not clear the campaign", erro)

    def test_reprovado_sem_contact_sheet_tambem_nao_imprime_nada(self):
        code, saida, erro = self._entrega(self._regras(),
                                          self._resultado(com_mosaico=False))
        self.assertEqual(code, 1, erro)
        self.assertEqual(saida, "", saida)
        self.assertIn("nothing has looked at it", erro)

    def test_o_caminho_do_clipe_reprovado_nao_aparece_em_lugar_nenhum_do_stdout(self):
        result = self._resultado()
        _code, saida, _erro = self._entrega(self._regras(duration_max_s=2),
                                            result)
        self.assertNotIn(result["out"], saida)

    def test_aprovado_continua_imprimindo_caminho_sheet_e_media(self):
        """O conserto não pode custar a entrega: quem passa imprime tudo, e
        na mesma ordem de sempre -- caminho, mosaico, linha que anexa."""
        result = self._resultado()
        code, saida, erro = self._entrega(self._regras(), result)
        self.assertEqual(code, 0, erro)
        linhas = saida.strip().splitlines()
        self.assertEqual(linhas[0], result["out"])
        self.assertIn(f"SHEET:{result['sheet']}", saida)
        self.assertIn(f"MEDIA:{result['out']}", saida)
        self.assertLess(saida.index("SHEET:"), saida.index("MEDIA:"))


class _PostFalso:
    """O `warden_post` que a rede teria devolvido. Nenhum socket é aberto."""

    class PostIndisponivel(Exception):
        pass

    def __init__(self, contas, configurado=True):
        self._contas = contas
        self._configurado = configurado

    def esta_configurado(self):
        return self._configurado

    def status(self):
        reauth = [f"Clip-Warden/{rede}" for rede, c in sorted(self._contas.items())
                  if c["conectada"] and c["reauth_required"]]
        return {"provedor": "upload-post", "base": "https://x.invalid/",
                "chave": "configurada", "email": "dono@example.com",
                "plano": "pro", "limite_perfis": 1, "perfil": "Clip-Warden",
                "perfis": [{"nome": "Clip-Warden", "contas": self._contas}],
                "reauth": reauth, "avisos": []}


def _conta(conectada=True, reauth=False, handle="@poddclipes"):
    return {"conectada": conectada, "reauth_required": reauth,
            "display_name": "Podd Clipes" if conectada else None,
            "handle": handle if conectada else None}


class OCodigoDeSaidaDoPostSeparaTresEstados(unittest.TestCase):
    """`post status` saía 0 para uma conta que não vai publicar.

    A persona lê este comando de forma binária -- saiu != 0, a estrada está
    desligada; saiu 0, é "a estrada que publica" -- e a conta com
    `reauth_required` saía 0 com a frase "CONNECTED BUT NEEDS REAUTH -- it will
    not publish" enterrada na listagem. O agente prometia publicação, e o envio
    falhava depois de subir o arquivo inteiro.

    São TRÊS estados e eles precisam de três códigos: publica, não há chave, e
    a chave vale mas nenhuma conta publica.
    """

    def _roda(self, falso):
        antigo = warden._post
        warden._post = lambda: falso
        self.addCleanup(setattr, warden, "_post", antigo)
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            try:
                code = warden.cmd_post(type("A", (), {"action": "status"})())
            except SystemExit as saiu:
                code = saiu.code
        return code, saida.getvalue() + erro.getvalue()

    def test_conta_conectada_e_saudavel_sai_zero(self):
        code, texto = self._roda(_PostFalso({"youtube": _conta()}))
        self.assertEqual(code, 0, texto)
        self.assertIn("@poddclipes", texto)

    def test_sem_chave_sai_um(self):
        code, texto = self._roda(_PostFalso({}, configurado=False))
        self.assertEqual(code, 1, texto)
        self.assertIn("intermediary is OFF", texto)

    def test_chave_valida_com_reauth_pendente_nao_sai_zero(self):
        code, texto = self._roda(
            _PostFalso({"youtube": _conta(reauth=True)}))
        self.assertNotEqual(code, 0, texto)
        self.assertEqual(code, 3, texto)
        self.assertIn("NO account here will publish", texto)
        self.assertIn("Clip-Warden/youtube", texto)
        # e diz o que fazer, que é o que separa um código de saída de um erro
        self.assertIn("reconnect", texto.lower())

    def test_chave_valida_sem_conta_nenhuma_tambem_nao_sai_zero(self):
        code, texto = self._roda(
            _PostFalso({"youtube": _conta(conectada=False)}))
        self.assertEqual(code, 3, texto)
        self.assertIn("NO account here will publish", texto)

    def test_uma_conta_saudavel_ao_lado_de_uma_pedindo_reauth_ainda_publica(self):
        """Existe estrada: o TikTok pede reautenticação, o YouTube publica."""
        code, texto = self._roda(_PostFalso({
            "youtube": _conta(),
            "tiktok": _conta(reauth=True, handle="@outro")}))
        self.assertEqual(code, 0, texto)
        self.assertIn("NEEDS REAUTH", texto)

    def test_a_frase_de_desligado_nao_cronometra_o_dono(self):
        """"upload it by hand in under a minute": ninguém mediu isso, e esta
        frase existe justamente para ser a que não promete nada."""
        _code, texto = self._roda(_PostFalso({}, configurado=False))
        self.assertNotIn("under a minute", texto)
        self.assertIn("upload it by hand", texto)


class OInboxNaoInventaUmTruncamento(unittest.TestCase):
    """A heurística de link cortado tinha a forma de uma mensagem INTEIRA.

    Era `texto.endswith(link) and len(texto) >= 79`: qualquer mensagem
    comprida que termine num link -- "pega esse podcast e me faz os cortes
    https://..." -- era declarada truncada, e o comando mandava perguntar de
    novo. A persona dá UMA pergunta por turno; essa era a segunda, e ela era
    gasta com um link perfeito.

    Agora o corte só é afirmado quando o endereço PROVA que está pela metade.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-inbox-")
        self.log = os.path.join(self.dir, "gateway.log")
        self._antigo = warden.GATEWAY_LOG
        warden.GATEWAY_LOG = self.log
        self.addCleanup(setattr, warden, "GATEWAY_LOG", self._antigo)
        self._ms = 0

    def _escreve(self, *textos):
        with open(self.log, "a", encoding="utf-8") as fh:
            for texto in textos:
                self._ms = (self._ms + 1) % 1000
                carimbo = (time.strftime("%Y-%m-%d %H:%M:%S")
                           + f",{self._ms:03d}")
                fh.write(f"{carimbo} INFO gateway.run: inbound message: "
                         f"platform=plow_chat user=x chat=cht_a msg='{texto}' "
                         f"reply_to_id=None reply_to_text=''\n")

    def _roda(self, espera=2.0):
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            try:
                code = warden.cmd_inbox(type("A", (), {"wait": espera})())
            except SystemExit as saiu:
                code = saiu.code
        return code, saida.getvalue(), erro.getvalue()

    def _chega(self, texto, depois=0.4):
        import threading
        t = threading.Thread(
            target=lambda: (time.sleep(depois), self._escreve(texto)))
        t.start()
        self.addCleanup(t.join)
        return t

    def test_mensagem_longa_e_inteira_devolve_o_link_e_nao_pergunta(self):
        self._escreve("gatilho")
        inteira = ("Pega esse podcast e me faz os tres cortes mais virais dele "
                   "https://www.youtube.com/watch?v=9rwEGPyPasY")
        self.assertGreaterEqual(len(inteira), 79)
        self._chega(inteira)
        code, saida, erro = self._roda(espera=6.0)
        self.assertEqual(code, 0, erro)
        self.assertEqual(saida.strip(),
                         "https://www.youtube.com/watch?v=9rwEGPyPasY")
        self.assertNotIn("truncated", erro)

    def test_id_do_youtube_pela_metade_continua_sendo_recusado(self):
        self._escreve("gatilho")
        self._chega("Pega esse video e me faz os cortes mais virais "
                    "https://www.youtube.com/watch?v=9rwEGPyPa")
        code, _saida, erro = self._roda(espera=6.0)
        self.assertEqual(code, 1, erro)
        self.assertIn("truncated", erro)
        self.assertIn("11 characters", erro)

    def test_link_que_nao_e_do_youtube_nao_e_chutado_como_cortado(self):
        """Fora das formas conhecidas não dá para distinguir uma URL curta de
        uma URL cortada -- e aí o honesto é devolver o link."""
        self._escreve("gatilho")
        longo = ("Faz um corte legal desse episodio aqui pra mim por favor "
                 "https://exemplo.invalid/podcast/ep")
        self.assertGreaterEqual(len(longo), 79)
        self._chega(longo)
        code, saida, erro = self._roda(espera=6.0)
        self.assertEqual(code, 0, erro)
        self.assertEqual(saida.strip(), "https://exemplo.invalid/podcast/ep")

    def test_log_ilegivel_nao_e_a_mesma_coisa_que_link_nenhum(self):
        """Dois casos, dois códigos: 1 é "esperei e não veio", que é uma
        medida; 2 é "não olhei", que não é."""
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        code, _saida, erro = self._roda(espera=1.0)
        self.assertEqual(code, 2, erro)
        self.assertIn("nothing was measured", erro)
        self.assertNotIn("nothing with a link arrived", erro)

    def test_link_nenhum_depois_da_espera_continua_saindo_um(self):
        self._escreve("me faz uns cortes desse video")
        code, _saida, erro = self._roda(espera=1.0)
        self.assertEqual(code, 1, erro)
        self.assertIn("ask for the URL", erro)


class AVozContaMensagensSemDespejarConversaDeOutros(unittest.TestCase):
    """`warden voz` imprimia texto de conversas de OUTRAS sessões.

    O banco do runtime guarda todas as conversas daquela instalação, uma por
    `session_id`, e o volume sobrevive a `up`/`down`. Nenhuma consulta filtrava
    por sessão: `warden voz --since 0` despejava 56 caracteres de cada pedido e
    88 de cada mensagem do agente, de quem quer que tivesse usado a mesma
    instalação antes.

    Para CONTAR não é preciso mostrar o texto de ninguém -- e quando dá para
    dizer qual é a sessão corrente, o que se conta é só ela.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-voz-")
        self.db = os.path.join(self.dir, "state.db")
        self._antigo = warden.CONVERSAS_DB
        warden.CONVERSAS_DB = self.db
        self.addCleanup(setattr, warden, "CONVERSAS_DB", self._antigo)
        self._sessao = os.environ.pop("WARDEN_SESSION_ID", None)
        self.addCleanup(self._repoe)

    def _repoe(self):
        if self._sessao is None:
            os.environ.pop("WARDEN_SESSION_ID", None)
        else:
            os.environ["WARDEN_SESSION_ID"] = self._sessao

    def _banco(self, linhas, com_sessao=True):
        con = sqlite3.connect(self.db)
        if com_sessao:
            con.execute("create table messages (session_id text, "
                        "timestamp real, role text, content text)")
            con.executemany("insert into messages values (?,?,?,?)", linhas)
        else:
            con.execute("create table messages (timestamp real, role text, "
                        "content text)")
            con.executemany("insert into messages values (?,?,?)", linhas)
        con.commit()
        con.close()

    def _roda(self, since=None):
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            try:
                code = warden.cmd_voz(type("A", (), {"since": since})())
            except SystemExit as saiu:
                code = saiu.code
        return code, saida.getvalue(), erro.getvalue()

    # A conversa de outra pessoa, na mesma instalação, e a desta sessão.
    _OUTRA = [
        ("sess-antiga", 100.0, "user", "meu nome completo e meu CPF sao"),
        ("sess-antiga", 101.0, "assistant", "anotei o seu CPF, obrigado"),
        ("sess-antiga", 102.0, "assistant", "e o endereco da sua casa tambem"),
        ("sess-antiga", 103.0, "assistant", "mais uma mensagem"),
        ("sess-antiga", 104.0, "assistant", "e mais outra"),
        ("sess-antiga", 105.0, "assistant", "e mais outra ainda"),
    ]
    _MINHA = [
        ("sess-agora", 200.0, "user", "me faz 2 cortes desse video"),
        ("sess-agora", 201.0, "assistant", "Peguei."),
        ("sess-agora", 260.0, "assistant", "Primeiro corte.\nMEDIA:/x/a.mp4"),
        ("sess-agora", 300.0, "assistant", "Segundo corte.\nMEDIA:/x/b.mp4"),
    ]

    def test_a_conversa_de_outra_sessao_nao_e_contada_nem_impressa(self):
        self._banco(self._OUTRA + self._MINHA)
        code, saida, erro = self._roda(since=0)
        tudo = saida + erro
        self.assertNotIn("CPF", tudo)
        self.assertNotIn("endereco da sua casa", tudo)
        self.assertIn("1 request(s)", saida)
        self.assertEqual(code, 0, tudo)

    def test_a_saida_diz_qual_sessao_esta_contando(self):
        self._banco(self._OUTRA + self._MINHA)
        _code, _saida, erro = self._roda(since=0)
        self.assertIn("sess-agora", erro)

    def test_a_sessao_do_ambiente_vence_a_inferencia(self):
        os.environ["WARDEN_SESSION_ID"] = "sess-antiga"
        self._banco(self._OUTRA + self._MINHA)
        code, saida, _erro = self._roda(since=0)
        self.assertEqual(code, 1, saida)          # cinco mensagens, teto é 4
        self.assertIn("DEMAIS", saida)
        self.assertNotIn("me faz 2 cortes", saida)

    def test_sem_sessao_para_isolar_ele_conta_e_nao_mostra_texto(self):
        """Contar é a razão de o comando existir; mostrar o texto não é."""
        linhas = [(None, t, p, c) for _s, t, p, c in self._OUTRA + self._MINHA]
        self._banco(linhas)
        code, saida, erro = self._roda(since=0)
        self.assertIn("printing NO message text", erro)
        self.assertIn("(text not shown)", saida)
        self.assertNotIn("CPF", saida + erro)
        # e a contagem continua saindo, com veredito
        self.assertIn("request(s)", saida)
        self.assertEqual(code, 1, saida)

    def test_banco_sem_coluna_de_sessao_guarda_uma_conversa_so(self):
        """Uma tabela que não separa sessões não tem o que separar: aí o texto
        continua saindo, que é o que torna a contagem acionável."""
        self._banco([(200.0, "user", "me faz 2 cortes"),
                     (201.0, "assistant", "Peguei."),
                     (202.0, "assistant", "Video baixado."),
                     (203.0, "assistant", "Vou transcrever agora."),
                     (204.0, "assistant", "Achei um momento forte."),
                     (205.0, "assistant", "Primeiro corte.\nMEDIA:/x/a.mp4")],
                    com_sessao=False)
        code, saida, _erro = self._roda(since=0)
        self.assertEqual(code, 1, saida)
        self.assertIn("Video baixado.", saida)


class OQueNaoFoiMedidoNaoEAfirmado(_ComAsDuasPontas):
    """Duas frases que afirmavam mais do que o projeto sabe.

    `_FALHOU = "Failed to send media"` entrou num bloco que começa com "Medido
    em 15/09" e NUNCA foi observada em log nenhum -- a auditoria do próprio
    projeto registra isso duas vezes. Se a frase real do gateway for outra,
    esta rede de segurança nunca dispara, e o silêncio dela não prova nada. A
    saída dizia "the gateway logged N media send failure(s)", como fato.
    """

    def test_a_constante_esta_marcada_como_nao_confirmada_no_comentario(self):
        fonte = warden.__file__.replace(".pyc", ".py")
        with open(fonte, encoding="utf-8") as fh:
            texto = fh.read()
        antes = texto.split('_FALHOU = "Failed to send media"')[0]
        bloco = antes[-1400:]
        self.assertIn("NÃO foi confirmada", bloco)

    def test_a_recusa_nao_chama_o_padrao_de_falha_medida(self):
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop")])
        self._log_com([
            "[Plow_Chat] Delivering 1 non-image MEDIA attachment(s)",
            "[Plow_Chat] Failed to send media (.mp4): upstream refused"])
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        # continua NÃO confirmando e continua mandando reenviar
        self.assertIn("NOT confirming", err)
        self.assertIn("Send it again", err)
        # mas não afirma o que ninguém mediu
        self.assertNotIn("media send failure(s)", err)
        self.assertNotIn("The person does not have it", err)
        self.assertIn("never seen in a real gateway log", err)


class LegendaPEDIDAEZEROCUESNaTelaEUmClipeMUDO(unittest.TestCase):
    """O portão perguntava se um CAMINHO foi passado. A pergunta é outra.

    Medido em 15/09/2026: o agente passou `--subtitles lote.srt.aprovado` -- o
    arquivo de assinatura, sem cue nenhuma dentro -- e o caminho EXISTIA. Daí
    `asked_for_captions` saiu `True`, o render não queimou nada, e a checagem
    que deveria travar isso perguntava por um campo booleano em vez de por um
    número. Dois clipes foram entregues mudos, relatados como prontos.

    A pergunta certa é: QUANTAS cues foram para a tela. `warden_media` já
    registra o número em `style.caption.cues` -- a contagem de `Dialogue:` no
    ASS, ou seja, linhas que o libass de fato desenhou. Zero é zero.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-cues-")
        self.estado = os.path.join(self.dir, "estado")
        os.makedirs(self.estado, exist_ok=True)
        self._env = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.estado
        self.addCleanup(self._repoe)
        self._probe_antigo = warden.probe
        self.addCleanup(setattr, warden, "probe", self._probe_antigo)
        warden.probe = lambda caminho: {
            "width": 1080, "height": 1920, "duration_s": 3.0, "fps": "30/1",
            "codec": "h264", "audio_codec": None, "subtitle_tracks": 0,
            "size_mb": 1.0}

    def _repoe(self):
        if self._env is None:
            os.environ.pop("WARDEN_DIR", None)
        else:
            os.environ["WARDEN_DIR"] = self._env

    def _regras(self):
        import warden_rules as R
        r = R.blank()
        r.update({"id": "t", "name": "t", "schema": 1})
        r["video"].update({"duration_min_s": 1, "duration_max_s": 5,
                           "width": 1080, "height": 1920, "audio": "forbidden"})
        r["caption"].update({"required_hashtags": [], "required_mentions": [],
                             "banned_terms": []})
        return r

    def _resultado(self, caption, pediu=True, notes=()):
        clipe = os.path.join(self.dir, "corte.mp4")
        with open(clipe, "wb") as fh:
            fh.write(b"\x00" * 16)
        folha = os.path.join(self.dir, "corte-contato.jpg")
        with open(folha, "wb") as fh:
            fh.write(b"\x00")
        return {"out": clipe, "notes": list(notes), "sheet": folha,
                "style": {"caption": caption}, "style_breaches": [],
                "asked_for_captions": pediu}

    def _entrega(self, result):
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.deliver(result, self._regras(), None, [])
        return code, saida.getvalue(), erro.getvalue()

    def test_zero_cues_com_legenda_pedida_reprova(self):
        """O caso exato do `.aprovado`: o render devolveu a ficha da legenda com
        a contagem em zero, porque não havia linha nenhuma para queimar."""
        code, saida, erro = self._entrega(
            self._resultado({"cues": 0, "max_lines": 0}))
        self.assertEqual(code, 1, erro)
        self.assertEqual(saida, "")
        self.assertIn("0 cues were burned", erro)

    def test_nenhuma_ficha_de_legenda_com_legenda_pedida_tambem_reprova(self):
        code, saida, erro = self._entrega(self._resultado(None))
        self.assertEqual(code, 1, erro)
        self.assertEqual(saida, "")
        self.assertIn("0 cues were burned", erro)

    def test_com_cues_na_tela_a_entrega_sai(self):
        """O conserto não pode custar a entrega: quem queimou legenda passa."""
        code, saida, erro = self._entrega(
            self._resultado({"cues": 7, "max_lines": 2}))
        self.assertEqual(code, 0, erro)
        self.assertIn("MEDIA:", saida)

    def test_uma_cue_so_ja_nao_e_zero(self):
        code, _saida, erro = self._entrega(
            self._resultado({"cues": 1, "max_lines": 1}))
        self.assertEqual(code, 0, erro)

    def test_sem_legenda_pedida_zero_cues_nao_reprova_nada(self):
        """Um corte sem `--subtitles` é mudo de propósito."""
        code, saida, erro = self._entrega(
            self._resultado(None, pediu=False))
        self.assertEqual(code, 0, erro)
        self.assertIn("MEDIA:", saida)

    def test_a_fonte_que_ja_vem_legendada_continua_sendo_a_excecao(self):
        """Queimar a nossa por cima da do acervo é legenda dupla: não queimar é
        a decisão CERTA, e reprovar aqui obrigaria a não entregar nada."""
        result = self._resultado({"cues": 0})
        result["style"]["source_caption_clash"] = True
        code, saida, erro = self._entrega(result)
        self.assertEqual(code, 0, erro)
        self.assertIn("MEDIA:", saida)

    def test_a_recusa_carrega_o_porque_que_o_render_anotou(self):
        code, _saida, erro = self._entrega(self._resultado(
            None, notes=["not burning captions: lote.srt.aprovado has not been "
                         "approved"]))
        self.assertEqual(code, 1)
        self.assertIn("not burning captions", erro)

    def test_a_recusa_proibe_apagar_o_pedido_ate_o_portao_calar(self):
        """O passo 4 da sequência medida: ele tirou o `--hook` e o portão calou.

        Cada remoção era, isolada, uma reação razoável a uma recusa. Juntas,
        eram o pedido do dono sendo apagado até a ferramenta parar de reclamar.
        Um portão que só diz "não" ensina a tirar coisas.
        """
        _code, _saida, erro = self._entrega(self._resultado(None))
        self.assertIn("DO NOT drop what the person asked for", erro)
        self.assertIn("--hook", erro)
        self.assertIn("no caption and no hook", erro)

    def test_um_render_antigo_que_so_diz_que_queimou_nao_e_tratado_como_zero(self):
        """O terceiro estado. Um `style.caption` que é só `True` afirma que
        queimou e não diz quantas; tratá-lo como zero reprovaria entrega boa."""
        self.assertEqual(warden._cues_queimadas({"style": {"caption": True}}), -1)
        self.assertEqual(warden._cues_queimadas({"style": {"caption": None}}), 0)
        self.assertEqual(
            warden._cues_queimadas({"style": {"caption": {"cues": 0}}}), 0)
        self.assertEqual(
            warden._cues_queimadas({"style": {"caption": {"cues": 12}}}), 12)
        code, _saida, erro = self._entrega(self._resultado(True))
        self.assertEqual(code, 0, erro)


class OInboxEsperaOBastanteParaNaoPerguntarAToa(unittest.TestCase):
    """O link SEMPRE chega numa mensagem separada, um instante depois.

    O dono, 15/09/2026: "quando mando o link nas mensagens, mesmo que seja na
    mesma mensagem, ela vai em outra". Não é "às vezes demora": é o mecanismo.

    Com a janela de 20s isso custou uma ida e volta inteira -- o `inbox` esperou
    20s, nada veio, o agente perguntou "qual é o link?", e o link entrou 1
    SEGUNDO depois da pergunta. A pergunta não acelerou nada e queimou um turno.

    A conta é assimétrica e é por isso que a janela ficou grande: o comando
    devolve no INSTANTE em que o link chega, então esperar custa zero no caso
    comum, e não esperar custa um turno.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-espera-")
        self.log = os.path.join(self.dir, "gateway.log")
        self._antigo = warden.GATEWAY_LOG
        warden.GATEWAY_LOG = self.log
        self.addCleanup(setattr, warden, "GATEWAY_LOG", self._antigo)
        self._ambiente = os.environ.pop("WARDEN_INBOX_WAIT", None)
        self.addCleanup(self._repoe_ambiente)
        self._ms = 0

    def _repoe_ambiente(self):
        if self._ambiente is None:
            os.environ.pop("WARDEN_INBOX_WAIT", None)
        else:
            os.environ["WARDEN_INBOX_WAIT"] = self._ambiente

    def _escreve(self, *textos):
        with open(self.log, "a", encoding="utf-8") as fh:
            for texto in textos:
                self._ms = (self._ms + 1) % 1000
                carimbo = (time.strftime("%Y-%m-%d %H:%M:%S")
                           + f",{self._ms:03d}")
                fh.write(f"{carimbo} INFO gateway.run: inbound message: "
                         f"platform=plow_chat user=x chat=cht_a msg='{texto}' "
                         f"reply_to_id=None reply_to_text=''\n")

    def _chega(self, texto, depois=0.4):
        import threading
        t = threading.Thread(
            target=lambda: (time.sleep(depois), self._escreve(texto)))
        t.start()
        self.addCleanup(t.join)
        return t

    def test_o_padrao_e_grande_o_bastante_para_o_link_da_mensagem_seguinte(self):
        self.assertGreaterEqual(warden.INBOX_ESPERA_S, 45.0)
        self.assertEqual(warden._espera_do_inbox(None), warden.INBOX_ESPERA_S)

    def test_a_linha_de_comando_continua_mandando_no_numero(self):
        self.assertEqual(warden._espera_do_inbox(3.0), 3.0)

    def test_o_ambiente_decide_quando_ninguem_passou_o_flag(self):
        """Quem roda a suíte não quer 45s de relógio por chamada, e não deveria
        ter de editar o código para não tê-los."""
        os.environ["WARDEN_INBOX_WAIT"] = "2"
        self.assertEqual(warden._espera_do_inbox(None), 2.0)
        # e um valor que não é número não derruba nada: cai no padrão
        os.environ["WARDEN_INBOX_WAIT"] = "logo"
        self.assertEqual(warden._espera_do_inbox(None), warden.INBOX_ESPERA_S)

    def test_o_comando_devolve_no_instante_em_que_o_link_chega(self):
        """O ponto inteiro: a janela larga NÃO é tempo gasto.

        A espera pedida aqui é de 30s e o link entra em 0,4s. Se o comando
        sentasse na janela, este teste levaria 30 segundos.
        """
        self._escreve("me faz dois cortes desse video aqui")
        self._chega("https://www.youtube.com/watch?v=9rwEGPyPasY", depois=0.4)
        comeco = time.time()
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.cmd_inbox(type("A", (), {"wait": 30.0})())
        gasto = time.time() - comeco
        self.assertEqual(code, 0, erro.getvalue())
        self.assertEqual(saida.getvalue().strip(),
                         "https://www.youtube.com/watch?v=9rwEGPyPasY")
        self.assertLess(gasto, 5.0,
                        f"esperou {gasto:.1f}s por um link que chegou em 0,4s")

    def test_o_help_diz_o_numero_e_diz_por_que_ele_e_grande(self):
        """Um número grande sem explicação parece descuido, e o próximo a
        passar por aqui o "conserta" de volta para 20."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                warden.main(["inbox", "--help"])
            except SystemExit:
                pass
        texto = " ".join(buf.getvalue().split())
        self.assertIn(f"default {warden.INBOX_ESPERA_S:.0f}", texto)
        self.assertIn("WARDEN_INBOX_WAIT", texto)
        self.assertIn("message of its own", texto)
        self.assertIn("never sits out the window", texto)


if __name__ == "__main__":
    unittest.main()
