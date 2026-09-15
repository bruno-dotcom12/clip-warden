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


if __name__ == "__main__":
    unittest.main()
