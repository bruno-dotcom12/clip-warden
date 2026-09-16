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


def carimbo_de_log(quando):
    """O carimbo de um instante no formato que `warden._carimbo_do_log` lê.

    `int`, e NUNCA `round`. Com `round`, uma fração >= 0,9995 escreve `,1000`:
    o campo passa a ter quatro dígitos, o leitor pega `linha[20:23]` == `"100"`
    e lê 0,1 s onde deviam ser 1,0 s -- enquanto o `strftime` acima truncou os
    segundos, então os dois campos discordam por um segundo inteiro. Isso fez
    `ZeroMilissegundoNaoEUmUpload::test_envios_desde_devolve_o_carimbo_de_cada_tentativa`
    falhar uma vez em duas rodadas, e um teste que falha pelo próprio andaime
    ensina a ignorar a suíte.

    Truncar é também o que o leitor faz: ele lê milissegundos inteiros. Os dois
    lados passam a arredondar para o mesmo lado, que é a única razão de este
    andaime existir. Ver `OCarimboDeTesteNaoEscreveMilSegundos`.

    UM formatador para os três lugares que carimbavam: o de
    `ZeroMilissegundoNaoEUmUpload`, o de `ACONFIRMACAONaoESobreUmaTENTATIVA` e
    o `_ComCronometro._linha`. Três cópias do mesmo defeito é o defeito três
    vezes, e foi assim que ele sobreviveu ao primeiro conserto.
    """
    return (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(quando))
            + ",%03d" % int((quando % 1) * 1000))


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
        self.assertIn("NOT sent: no message of yours cites that file", err)
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
        self.assertIn("no message of yours cites that file", err)
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
        self.assertIn("NOT sent: that MEDIA: line was written MID-TURN", err)
        self.assertIn("that attachment was thrown away", err)
        self.assertIn("FINAL message of a turn", err)
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
        self.assertRegex(err, r"message at \d\d:\d\d")

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
        self.assertIn("handed off", err)


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
        self.assertIn("handed off", err)
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

    def test_gateway_ilegivel_com_state_db_bom_nao_risca_e_diz(self):
        """Era "metade lida é melhor que nenhuma", e a metade riscava o clipe.

        Este teste dizia `assertEqual(code, 0)` e foi por isso que o buraco
        durou: a saída era honesta -- "não consigo ler o gateway.log daqui" --
        e o EFEITO dela era o de uma confirmação. O clipe saía do livro, o
        comando saía 0, o agente não reenviava, e a pessoa ficava sem o
        arquivo exatamente como em 16/09. A metade que faltava é justamente a
        que pegou aquele defeito: naquele dia as duas linhas `MEDIA:` estavam
        na mensagem final do turno e nenhum dos dois arquivos chegou, então a
        metade lida aqui não distingue um caso do outro.

        A metade continua sendo dita; o que mudou é que ela não fecha mais o
        livro. Quem fecha sem medida é a pessoa que recebeu (`--arrived`).
        """
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop")])
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        self.assertIn("NOT verified", err)
        self.assertIn("not readable from here", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)


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
        self.assertIn(warden.RUBRICA_DO_CLIPE.format(i=1), texto)
        self.assertIn("MEDIA:/clipes/a.mp4", texto)
        self.assertIn(warden.RUBRICA_DO_CLIPE.format(i=2), texto)
        self.assertIn("MEDIA:/clipes/b.mp4", texto)
        # e a ordem é a ordem dos clipes
        self.assertLess(texto.index("/clipes/a.mp4"), texto.index("/clipes/b.mp4"))

    def test_o_bloco_nao_manda_entregar_um_por_turno(self):
        texto = self._bloco([("a.mp4", "/clipes/a.mp4"),
                             ("b.mp4", "/clipes/b.mp4")])
        self.assertNotIn("one per turn", texto)
        self.assertNotIn("one clip per turn", texto)
        self.assertIn("ONE message carrying all 2", texto)

    def test_o_bloco_proibe_mandar_recado_ANTES_no_mesmo_turno(self):
        """O bloco proibia o tool call DEPOIS. O que perdeu a demonstração veio ANTES.

        Medido em 16/09/2026: às 18:20:21 o agente mandou "On it." pelo
        `plow_send_sequence`, no MEIO do turno. Isso marcou
        `turn["reply_delivered"] = True` (plow_chat:2603), e às 18:22:41 o
        portão anti-duplicata do adaptador (plow_chat:2062-2065) devolveu
        `SendResult(success=True)` SEM MANDAR NADA para a prosa e para os dois
        anexos de 16 MB. O livro de entregas carimbou `delivered`, e o dono não
        recebeu clipe nenhum.

        O bloco já dizia "não chame ferramenta DEPOIS destas linhas". A outra
        ponta -- não mande recado pelo chat ANTES, no mesmo turno -- não estava
        escrita em lugar nenhum: nem aqui, nem na persona, nem na skill.
        """
        texto = self._bloco([("a.mp4", "/clipes/a.mp4")])
        self.assertIn("EARLIER in this same turn", texto)
        self.assertIn("plow_send_sequence", texto)

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
        self.assertIn("Run this command again at the START of your NEXT turn",
                      texto)
        self.assertIn("cannot confirm a send that has not happened yet", texto)
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
        # A chave deixou de ter UM lugar só. Desde que `warden post setkey`
        # existe, ela pode estar no ambiente ou num arquivo do volume desta
        # máquina -- e "desligado" só é desligado quando nenhum dos dois tem
        # nada. Sem apontar WARDEN_POST_DIR para um temporário, este teste
        # leria o arquivo de quem está rodando a suíte: no Mac isso é
        # ~/.clip-warden/post-api-key, a chave do PRÓPRIO dono. Aí o teste que
        # prova a rota de fallback passaria a depender de o dono ter rodado
        # `setkey` ou não, e a suíte ainda encostaria num diretório que não é
        # dela -- o que já aconteceu uma vez e deu limpeza à mão.
        antes_dir = os.environ.get("WARDEN_POST_DIR")
        os.environ["WARDEN_POST_DIR"] = _temp(self, "warden-post-off-")
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
            if antes_dir is None:
                os.environ.pop("WARDEN_POST_DIR", None)
            else:
                os.environ["WARDEN_POST_DIR"] = antes_dir

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
                # Maiúsculas novas porque a frase mudou de tamanho, não de
                # sentido. A recusa antiga era uma oração só --
                # "WARDEN_POST_API_KEY is not set in this environment, so
                # nothing was sent" -- e ela só sabia de um lugar onde a chave
                # podia estar. Agora que `warden post setkey` grava a chave num
                # arquivo desta máquina, a recusa tem de dizer que os DOIS
                # estão vazios, e "Nothing was sent" virou começo de frase. O
                # que o teste continua exigindo é o mesmo: que a saída afirme
                # que nada foi TENTADO, e não apenas que faltou uma variável.
                self.assertIn("Nothing was sent and nothing was tried", texto)
                self.assertIn("not in WARDEN_POST_API_KEY", texto)
                self.assertIn("not in this machine's own file", texto)
                # e diz o que fazer em vez disso, que é a rota de fallback
                self.assertIn("final message", texto)
                self.assertIn("Do NOT claim anything was published", texto)
                # A segunda estrada, que na nuvem da Plow é a única que existe:
                # lá não há `.env` nem `compose.yml` para exportar variável
                # nenhuma, então mandar "configure o ambiente" seria mandar a
                # pessoa a um endereço que não existe. A saída tem de nomear o
                # comando que aceita a chave digitada na própria conversa.
                self.assertIn("warden post setkey", texto)

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

    def test_sem_contact_sheet_ENTREGA_do_mesmo_jeito_desde_16_09(self):
        """O mosaico deixou de ser portão, por decisão do dono em 16/09/2026.

        Ele recusava a entrega sem a imagem, e nasceu de um clipe com o hook
        cortado e legenda de seis linhas que passou por toda a verificação
        numérica. O que mudou foi o preço: as duas chamadas de visão de um
        pedido real custaram 31s de 706s, e o dono roda isto numa chamada de
        tela compartilhada. A frase dele: "entrega sem olhar, sua unica
        obrigacao = hook e legenda".

        Então sem mosaico o clipe SAI, e a saída diz em voz alta que não há
        nada visual guardado se ele voltar errado. O que continua recusando é
        hook e legenda -- os dois que ele nomeou.
        """
        code, saida, erro = self._entrega(self._regras(),
                                          self._resultado(com_mosaico=False))
        self.assertEqual(code, 0, erro)
        self.assertIn("MEDIA:", erro + saida)
        self.assertIn("no contact sheet was written", erro)
        self.assertIn("Not a blocker", erro)

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
        # `chave_origem` e `perfil_origem` são novas e o dublê as carrega
        # porque o comando as IMPRIME: sem elas aqui, a listagem sairia
        # dizendo "from the None", e um dublê que devolve menos que o módulo
        # real transforma uma saída quebrada num teste verde.
        #
        # A história que este dublê conta é coerente e é a mais comum na nuvem
        # da Plow: a chave foi digitada nesta máquina (`arquivo`), então a
        # conta é de quem está usando -- e por isso o perfil que já existia lá
        # dentro pôde ser ADOTADO em vez de um novo ter sido gerado.
        return {"provedor": "upload-post", "base": "https://x.invalid/",
                "chave": "configurada", "chave_origem": "arquivo",
                "email": "dono@example.com",
                "plano": "pro", "limite_perfis": 1, "perfil": "Clip-Warden",
                "perfil_origem": "adotado",
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
                # `file=None` não é enfeite: o ramo que lê a CONTA passou a ser
                # `action == "status" and not args.file`. Antes ele era só
                # `action == "status"` e vinha antes do ramo que consulta um
                # envio, então `warden post status <request_id>` caía aqui e
                # ignorava o id em silêncio -- o agente pedia o endereço do
                # vídeo que tinha prometido e recebia a listagem da conta. Com
                # a condição nova, um dublê sem `file` nem chega a rodar.
                code = warden.cmd_post(
                    type("A", (), {"action": "status", "file": None})())
            except SystemExit as saiu:
                code = saiu.code
        return code, saida.getvalue() + erro.getvalue()

    def test_conta_conectada_e_saudavel_sai_zero(self):
        code, texto = self._roda(_PostFalso({"youtube": _conta()}))
        self.assertEqual(code, 0, texto)
        self.assertIn("@poddclipes", texto)
        # A listagem passou a dizer DE ONDE veio a chave e DE ONDE veio o nome
        # do perfil, e isso é o que a pessoa lê quando o canal listado não é o
        # que ela esperava: uma chave do ambiente é a do dono do agente e pode
        # estar em várias máquinas, uma do arquivo é a dela. Nunca o valor da
        # chave -- só a procedência.
        self.assertIn("from the arquivo", texto)
        self.assertIn("(adotado)", texto)

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

    def test_o_comentario_da_constante_cita_a_fonte_que_a_escreve(self):
        """A frase foi lida na fonte em 16/09; o comentário tem de dizer ONDE.

        Ela deixou de ser "uma string que ninguém viu" -- base.py:3885-3886,
        lido dentro do container vivo, formata `[Plow_Chat] Failed to send
        media (.mp4): <erro>` palavra por palavra. O que o comentário agora
        precisa carregar é a outra metade, que é a que importa: a linha só é
        escrita quando `result.success` é falso, e o portão do Plow devolve
        `success=True` sem mandar nada -- então o silêncio desta rede continua
        não provando entrega nenhuma.
        """
        fonte = warden.__file__.replace(".pyc", ".py")
        with open(fonte, encoding="utf-8") as fh:
            texto = fh.read()
        antes = texto.split('_FALHOU = "Failed to send media"')[0]
        bloco = antes[-2400:]
        self.assertIn("base.py:3885-3886", bloco)
        self.assertIn("if not result.success", bloco)
        self.assertIn("success=True", bloco)

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
        # e agora diz de onde a frase vem, em vez de chamá-la de palpite
        self.assertIn("the gateway's send-failure wording", err)


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

    def test_hook_pedido_e_nenhum_desenhado_reprova(self):
        """O portão que nasceu sem rede, apontado pela auditoria de 16/09.

        `--hooks` passado e NENHUM hook na imagem não era pego por nada: o
        único portão do hook lia `hook.complete is False`, e quando nada foi
        desenhado a chave `hook` simplesmente não existe no sidecar. Um portão
        que lê campo AUSENTE não dispara -- o mesmo defeito, do outro lado da
        tela, que deixou sair o clipe mudo de 15/09.
        """
        r = self._resultado({"cues": 7, "max_lines": 2})
        r["asked_for_hook"] = True          # o --hooks foi passado
        r["style"].pop("hook", None)        # e nada foi desenhado
        code, saida, erro = self._entrega(r)
        self.assertEqual(code, 1, erro)
        self.assertEqual(saida, "", "saiu um MEDIA: para um clipe sem hook")
        self.assertIn("none was drawn", erro)

    def test_hook_pedido_e_desenhado_a_entrega_sai(self):
        """O par positivo: o portão novo não pode custar uma entrega boa."""
        r = self._resultado({"cues": 7, "max_lines": 2})
        r["asked_for_hook"] = True
        r["style"]["hook"] = {"complete": True, "lines": 2}
        code, saida, erro = self._entrega(r)
        self.assertEqual(code, 0, erro)
        self.assertIn("MEDIA:", saida)

    def test_sem_pedir_hook_a_ausencia_dele_nao_reprova(self):
        """Quem não pediu hook não fica sem clipe por não ter hook."""
        r = self._resultado({"cues": 7, "max_lines": 2})
        r["asked_for_hook"] = False
        r["style"].pop("hook", None)
        code, saida, erro = self._entrega(r)
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

    def test_o_padrao_cobre_o_link_da_mensagem_seguinte_sem_travar_uma_chamada(self):
        """A janela tem DOIS lados, e os dois vieram do uso real.

        Grande o bastante para o link que o app manda como segunda mensagem um
        instante depois -- sem isso a pergunta sai à toa e custa uma ida e
        volta, medido quatro vezes em quatro.

        E pequena o bastante para não travar uma chamada de compartilhamento de
        tela, que é onde isto vai ser avaliado. As palavras do dono em 16/09,
        depois de esperar sessenta: "não posso esperar tanto tempo assim".
        """
        self.assertGreaterEqual(warden.INBOX_ESPERA_S, 8.0)
        self.assertLessEqual(warden.INBOX_ESPERA_S, 20.0)
        self.assertEqual(warden._espera_do_inbox(None), warden.INBOX_ESPERA_S)

    def test_o_laco_confere_varias_vezes_por_segundo(self):
        """A espera não é tempo gasto: o que importa é quanto demora a DEVOLVER.

        Um link que chega em 300ms tem de custar 300ms, não meio segundo de
        arredondamento. É o que torna a janela curta suportável.
        """
        fonte = io.open(warden.__file__, encoding="utf-8").read()
        import re
        alvo = 'nothing with a link'
        m = re.search(r"time\.sleep\(([\d.]+)\)\s*\n\s*print\(f?[\"']" + alvo, fonte)
        self.assertIsNotNone(m, "não achei o laço de espera do inbox")
        self.assertLessEqual(float(m.group(1)), 0.2)

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


class ADividaDeEntregaEDaCONVERSA(unittest.TestCase):
    """Medido em 16/09/2026, no segundo teste real pela linha da Plow.

    O dono mandou `/new` e o pedido. A sessão nova abriu cobrando CINCO clipes
    da sessão anterior -- duas versões intermediárias de um ciclo de rerender
    entre eles -- e o agente respondeu, com essas palavras:

        "Achei — essa mesma tarefa já tinha sido feita ontem e os dois cortes
        ficaram prontos, só nunca confirmaram como entregues. Reenviando agora"

    Ele REENVIOU os clipes de ontem em vez de atender o pedido novo. E não é só
    desperdício: um clipe devido numa conversa que não existe mais não pode ser
    entregue na nova, porque o texto que o acompanhava também não existe mais.
    """

    def setUp(self):
        self.dir = _temp(self, "warden-divida-")
        os.environ["WARDEN_DIR"] = self.dir
        self.clip = os.path.join(self.dir, "corte.mp4")
        open(self.clip, "wb").write(b"x")
        self._sessao = os.environ.get("WARDEN_SESSION_ID")

    def tearDown(self):
        if self._sessao is None:
            os.environ.pop("WARDEN_SESSION_ID", None)
        else:
            os.environ["WARDEN_SESSION_ID"] = self._sessao

    def test_o_clipe_de_outra_conversa_NAO_e_cobrado(self):
        os.environ["WARDEN_SESSION_ID"] = "conversa-de-ontem"
        warden.entregas_registra(self.clip)
        os.environ["WARDEN_SESSION_ID"] = "conversa-de-hoje"
        pendentes = [r["clip"] for r in warden.entregas_pendentes()]
        self.assertNotIn(self.clip, pendentes,
                         "a conversa nova abriu cobrando dívida da anterior")

    def test_o_clipe_DESTA_conversa_continua_cobrado(self):
        os.environ["WARDEN_SESSION_ID"] = "conversa-de-hoje"
        warden.entregas_registra(self.clip)
        pendentes = [r["clip"] for r in warden.entregas_pendentes()]
        self.assertIn(self.clip, pendentes,
                      "o portão parou de cobrar o que é desta conversa")

    def test_livro_antigo_sem_conversa_anotada_continua_valendo(self):
        # Um livro escrito por uma versão anterior não tem o campo. Perder a
        # cobrança inteira seria pior que cobrar demais.
        os.environ["WARDEN_SESSION_ID"] = "conversa-de-hoje"
        warden.entregas_registra(self.clip)
        rows = warden.entregas_all()
        for r in rows:
            r.pop("session", None)
        warden._entregas_grava(rows)
        pendentes = [r["clip"] for r in warden.entregas_pendentes()]
        self.assertIn(self.clip, pendentes)


# ══════════════════════════════════════════════════════ o conselho impossível
#
# Um portão que reprova e manda fazer o que já está feito não é um portão: é um
# laço. Medido em 7a (16/09/2026), nos renders 2 e 3, texto idêntico nos dois:
#
#   That sentence closes 0.0s later, at 30.0s: cut 30.0s instead of 30.0s,
#   or move the start so it fits in the number that was asked for.
#
# (logs-7a/container/render-outs.txt:127 e :169). O agente obedeceu, re-cortou
# duas vezes, e as duas nasceram reprovadas. Custou 2 renders (~150s de CPU) e
# 2 laços de espera (~523s) em cima de uma instrução que não dizia nada.
#
# A origem é warden_style.py:2727-2732: `falta = float(fecha) - dur` montava a
# frase (warden_style.py:2733-2739) sem nenhuma guarda para `falta <= 0`. A
# guarda -- `if falta > 0.05` -- é o conserto, e está na 2732.

class OPortaoNuncaMandaCortarNOMESMONUMERO(unittest.TestCase):
    """A regra, em uma linha: nenhum conselho pode ter o número atual."""

    @staticmethod
    def _side(fecha, duracao=30.0):
        return {"duration_s": duracao,
                "caption": {"cues": 12, "karaoke": True, "max_lines": 2,
                            "hanging_endings": ["an analogy, which was "
                                                "something that you"],
                            "ends_mid_sentence": True,
                            "sentence_closes_at_s": fecha}}

    def _aponta(self, side):
        import warden_style as S
        # OBSERVAÇÃO, não reprovação: desde 16/09 a frase cortada no meio é
        # dita e o clipe sai. O conselho que este portão dá continua sendo
        # cobrado aqui -- ele é a razão de o portão existir.
        return [m for nivel, m in S.check_sidecar(side) if nivel == "WARN"]

    def test_a_frase_que_fecha_NO_FIM_do_corte_nao_vira_cut_X_instead_of_X(self):
        """`fecha == duração`: era `cut 30.0s instead of 30.0s`."""
        for msg in self._aponta(self._side(30.0)):
            self.assertNotIn("cut 30.0s instead of 30.0s", msg)
            self.assertNotIn("closes 0.0s later", msg)

    def test_nem_quando_o_fim_da_frase_fica_ANTES_do_fim_do_corte(self):
        """`fecha < duração` dava um `falta` negativo e um `--end` menor que o
        corte, que não é 'onde a frase fecha' -- é encurtar às cegas."""
        for msg in self._aponta(self._side(29.2)):
            self.assertNotIn("cut 29.2s instead of 30.0s", msg)

    def test_a_observacao_continua_existindo_e_diz_a_outra_saida(self):
        """Calar o portão não é a correção: o clipe ENDS mid-sentence de
        verdade. O que muda é o conselho."""
        msgs = self._aponta(self._side(30.0))
        self.assertTrue(any("ENDS mid-sentence" in m for m in msgs), msgs)
        texto = " ".join(msgs).lower()
        self.assertIn("move the start", texto)

    def test_quando_a_frase_fecha_DEPOIS_o_conselho_continua_sendo_o_numero(self):
        """O caso que sempre funcionou não pode ter sido perdido no conserto."""
        msgs = self._aponta(self._side(31.6))
        self.assertTrue(any("31.6s" in m for m in msgs), msgs)

    def test_nenhum_conselho_do_portao_repete_o_numero_atual(self):
        """A invariante, e não os três casos acima: para toda duração e todo
        ponto de fecho, a mensagem nunca oferece o número que já está lá."""
        import itertools
        for dur, fecha in itertools.product((15.0, 20.0, 30.0, 31.1),
                                            (14.0, 15.0, 20.0, 30.0, 31.1, 44.0)):
            for msg in self._aponta(self._side(fecha, dur)):
                self.assertNotIn(f"cut {dur:.1f}s instead of {dur:.1f}s", msg)
                self.assertNotIn("closes 0.0s later", msg)


class ATEXTONaoTRAZUMAFRASEPRONTAEMPORTUGUES(unittest.TestCase):
    """A nota separa o FATO da FRASE, e a frase é do modelo.

    Medido em 7a: a nota do render mandava o agente dizer, palavra por palavra,
    `"ficou {N} segundo(s) mais longo para não cortar a frase no meio"`
    (warden_media.py:4789-4791, :4818-4822; warden_style.py:2733-2739). Ela
    chegou ao modelo no meio de uma conversa EM INGLÊS -- state.db msgs 69 e
    75, e o mesmo texto em render-outs.txt:29, :64, :127 -- e a resposta do
    agente à pessoa foi em português ("Em produção.", msg 21).

    A ferramenta mede; quem escolhe as palavras, e a língua, é o modelo.
    """

    # O miolo da frase pronta, que é o que a ferramenta não pode mais soletrar.
    # Procurado como fragmento porque no código ele nasce de uma f-string
    # quebrada em várias linhas.
    AMOSTRA = "cortar a frase no meio"

    def _fonte(self, nome):
        caminho = os.path.join(os.path.dirname(HERE), "warden-shared",
                               "scripts", nome)
        with open(caminho, encoding="utf-8") as fh:
            return fh.read()

    def test_a_amostra_em_portugues_saiu_das_notas_do_render(self):
        for nome in ("warden_media.py", "warden_style.py"):
            fonte = self._fonte(nome)
            self.assertNotIn(self.AMOSTRA, fonte,
                             f"{nome} ainda soletra a frase que o modelo tem "
                             f"de repassar, numa língua só")

    def test_o_fato_medido_continua_na_nota(self):
        """Tirar a frase não pode ter tirado o número: o que a pessoa precisa
        ouvir é que o clipe ficou mais longo, e só a ferramenta sabe quanto."""
        fonte = self._fonte("warden_media.py")
        self.assertIn("because the cut landed in the middle", fonte)
        # "in their own" e não a frase inteira: no código ela nasce de uma
        # f-string quebrada em duas linhas.
        # `>=` e não `==`: contar exatamente duas era transformar "as duas
        # notas de duração mandam o modelo escolher as palavras" em "só elas
        # podem mandar". Em 16/09 uma TERCEIRA nota ganhou a mesma correção --
        # a de `not burning captions`, que trazia pronta a frase `"esse vídeo
        # já vem legendado, então não pus legenda em cima"` para o modelo
        # repassar palavra por palavra -- e este teste reprovou o conserto.
        self.assertGreaterEqual(fonte.count("their own language"), 2,
                                "as notas de duração -- a que estende e a que "
                                "encurta -- têm de mandar o modelo escolher as "
                                "palavras")
        self.assertNotIn("esse vídeo já vem legendado", fonte)


class OCHECKLISTFALAALINGUADORESTODASAIDA(unittest.TestCase):
    """As últimas linhas que o agente lê a cada render, e elas eram só em PT.

    Medido em 7a: a saída do render trazia os oito itens em português -- `o
    hook cabe inteiro no quadro...` -- no meio de um bloco em inglês e de uma
    conversa em inglês (state.db id 69; render-outs.txt, bloco do render 1). O
    cabeçalho do `prep` e este checklist são a primeira e a última coisa lidas
    a cada render: é o banho de português que antecede toda prosa que o modelo
    escreve, e a prosa saiu em português (msg 21, "Em produção.").

    O checklist NÃO é uma frase para a pessoa: é a conferência que o modelo faz
    olhando o mosaico. Então ele segue a língua do resto da saída da
    ferramenta, que é inglês.
    """

    def test_nenhum_item_traz_palavra_exclusiva_do_portugues(self):
        import warden_style as S
        # Só marcas inequívocas: " no " e " que " também são inglês, e um
        # marcador ambíguo reprova a tradução correta.
        marcas = ("ção", "não", "está", "quadro", "legenda", "moldura",
                  "máximo", "amarelo", " da ", " do ", "coberto")
        for item in S.CHECKLIST:
            for marca in marcas:
                self.assertNotIn(marca, item.lower(),
                                 f"item ainda em português: {item!r}")

    def test_o_checklist_continua_com_os_oito_itens(self):
        """Traduzir não pode ter virado encurtar: cada item corresponde a um
        defeito medido num clipe reprovado."""
        import warden_style as S
        self.assertEqual(len(S.CHECKLIST), 8)
        for item in S.CHECKLIST:
            self.assertTrue(item.strip())


# ══════════════════════════════════ AUDITORIA 8: o livro de entregas tem de FECHAR

class OLivroFechaSozinhoNoTurnoSEGUINTE(_ComAsDuasPontas):
    """Achado 11 do ACHADOS-FASE-1, e o pré-requisito da trava do pedido 4.

    Medido em 7a, DEPOIS da janela do diagnóstico: duas mensagens finais com
    `MEDIA:` (msg 91 às 15:14:47 e msg 97 às 15:17:10), com o gateway
    confirmando `Delivering 2 non-image MEDIA attachment(s)` nas duas, e
    `entregas.json` continuou com `"sent": false` nos dois clipes. `warden
    delivered` continuou respondendo `2 clip(s) rendered and cleared, and NOT
    confirmed as sent`, e por isso a msg 97 começa com "Reenviando os dois
    cortes, porque o sistema não confirmou que chegaram".

    A causa é estrutural e está em `cmd_delivered`: SEM argumento o comando
    só LISTA. Toda a verificação -- `mensagem_que_citou` + `envios_desde` --
    está atrás do `if args.clip:`, e nada obriga ninguém a passar um caminho.
    O agente rodou a forma sem argumento, leu "não confirmado", e reenviou.

    Por que isto vem ANTES de confiar na trava do pedido 4: a trava recusa um
    `lote render` novo enquanto houver clipe com `"sent": false`. Se o livro
    nunca fecha, a trava deixa de ser uma rede e vira um `lote render` que
    nunca mais roda nesta conversa.
    """

    def _sem_argumento(self):
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = warden.cmd_delivered(type("A", (), {"clip": None})())
        return code, err.getvalue() + out.getvalue()

    def test_sem_argumento_o_livro_fecha_quando_a_prova_existe(self):
        """A prova é a mesma que o comando COM argumento já aceita."""
        escreve_state_db(self.db, [
            ("assistant", f"here it is\nMEDIA:{os.path.abspath(self.clipe)}",
             "stop")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        code, saida = self._sem_argumento()
        self.assertEqual(code, 0, saida)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_sem_argumento_os_dois_clipes_do_mesmo_envio_fecham(self):
        """O caso exato da msg 91: duas linhas `MEDIA:` na mesma final."""
        dois = self._arquivo("corte-02.mp4")
        escreve_state_db(self.db, [
            ("assistant",
             f"here they are\nMEDIA:{os.path.abspath(self.clipe)}\n"
             f"MEDIA:{os.path.abspath(dois)}", "stop")])
        self._anexo_saiu(2)
        warden.entregas_registra(self.clipe)
        warden.entregas_registra(dois)
        code, saida = self._sem_argumento()
        self.assertEqual(code, 0, saida)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_sem_argumento_o_que_NAO_saiu_continua_devendo(self):
        """Fechar o livro não pode virar riscar tudo.

        O clipe citado no meio do turno é o defeito que este projeto perdeu
        clipe por não ver; ele tem de sobreviver a esta varredura.
        """
        dois = self._arquivo("corte-02.mp4")
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "stop"),
            ("assistant", f"MEDIA:{os.path.abspath(dois)}", "tool_calls")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        warden.entregas_registra(dois)
        code, saida = self._sem_argumento()
        self.assertEqual(code, 1, saida)
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(dois)])

    def test_sem_argumento_e_sem_prova_nenhuma_nada_e_riscado(self):
        """Nenhuma mensagem citou nada: o livro continua aberto, como deve."""
        escreve_state_db(self.db, [("assistant", "rendering", "stop")])
        warden.entregas_registra(self.clipe)
        code, saida = self._sem_argumento()
        self.assertEqual(code, 1, saida)
        self.assertEqual(len(warden.entregas_pendentes()), 1)


class ORecadoDeEntregaNaoFalaPortugues(_ComAsDuasPontas):
    """Item 7 da auditoria: `warden.py:6148-6156` é a mais barata e a mais grave.

    Ela é meia string em português e meia em inglês NA MESMA FRASE -- `NOT
    sent: MEDIA escrito no meio do turno (msg às HH:MM). Esse anexo foi
    descartado. Repita essa linha MEDIA: na mensagem FINAL. The message that
    carried ... ended with finish_reason=...` -- e é exatamente o que o agente
    lê quando a entrega JÁ falhou, isto é, no momento em que ele mais precisa
    de uma instrução que não o faça trocar de idioma.
    """

    # As mesmas palavras só-de-português usadas no bloco final. Ver
    # tests/test_media_na_saida.py: texto de ferramenta é instrução para o
    # modelo, e a língua dela é a do resto da saída do render.
    PT = ("escrito", "descartado", "mensagem", "anexo", "nenhuma", "citou",
          "esse", "essa", "arquivo", "turno", "repita", "rode", "envio",
          "aconteceu", "seguinte", "início", "linha", "não", "ele")

    def _pt_em(self, texto):
        import re as _re
        achadas = _re.findall(r"[^\W\d_]+",
                              _re.sub(r"/\S+|MEDIA:\S*", " ", texto),
                              flags=_re.UNICODE)
        return sorted({p for p in achadas if p.lower() in self.PT})

    def test_o_recado_de_nunca_citado_e_em_ingles(self):
        escreve_state_db(self.db, [("assistant", "rendering", "stop")])
        warden.entregas_registra(self.clipe)
        _code, err = self._confirma()
        self.assertIn("NOT sent", err)
        self.assertEqual(self._pt_em(err), [], err)

    def test_o_recado_de_meio_do_turno_e_em_ingles(self):
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(self.clipe)}", "tool_calls")])
        warden.entregas_registra(self.clipe)
        _code, err = self._confirma()
        self.assertIn("NOT sent", err)
        self.assertEqual(self._pt_em(err), [], err)

    def test_a_cobranca_final_e_em_ingles(self):
        """A última linha do `warden delivered` sem argumento dizia `Rode este
        comando no INÍCIO do turno seguinte. Ele não pode confirmar um envio
        que ainda não aconteceu.` no meio de um parágrafo em inglês."""
        warden.entregas_registra(self.clipe)
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            warden.cmd_delivered(type("A", (), {"clip": None})())
        texto = err.getvalue() + out.getvalue()
        self.assertEqual(self._pt_em(texto), [], texto)


class ZeroMilissegundoNaoEUmUpload(_ComAsDuasPontas):
    """16 MB não sobem em 0 ms, e o log já sabia disso -- ninguém olhava.

    Medido em 16/09/2026, na demonstração do dono. Os dois cortes de 16 MB
    saíram do render, a linha `MEDIA:` de cada um estava na mensagem FINAL do
    turno, e o gateway escreveu o par de linhas que `warden delivered` lê:

        18:22:41,674 [Plow_Chat] Delivering 2 non-image MEDIA attachment(s)
        18:22:41,674 [Plow_Chat] Sending video attachment (.mp4) to cht_knq...
        18:22:41,674 [Plow_Chat] Sending video attachment (.mp4) to cht_knq...

    O dono não recebeu nenhum dos dois (foto da conversa dele: depois de "On
    it." a bolha seguinte já é outra coisa). O comando respondeu `confirmed:
    corte-ec898de219-01.mp4 -- ... and 2 video attachment(s) left after it.`

    A comparação que faltava está no MESMO formato de log, no teste 7a, cujos
    clipes o dono recebeu (logs-7a/publicacao/logs/gateway.log:85-87):

        15:14:49,517 Delivering 2 non-image MEDIA attachment(s)
        15:14:49,518 Sending video attachment (.mp4)
        15:14:54,384 Sending video attachment (.mp4)      <- 4,866 s depois

    Fora do carimbo, as linhas são iguais caractere por caractere; `envios_desde`
    só contava ocorrências, então para ela os dois casos eram o mesmo. A única
    grandeza que separa um do outro já estava no arquivo e era jogada fora: a
    distância entre uma linha e a seguinte. Como o gateway escreve a linha
    ANTES do `await` do envio (base.py:3880-3881, lido do container vivo), essa
    distância É a duração do upload anterior. 4,866 s contra 0,000 s.
    """

    def _arquivo_grande(self, nome, megas=16):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\0" * (megas * 1000 * 1000))
        return caminho

    def _log_cronometrado(self, offsets, base=None):
        """Um gateway.log com os anexos nos deslocamentos (segundos) dados."""
        t0 = float(base if base is not None else time.time())
        eventos = [(t0, "[Plow_Chat] Delivering %d non-image MEDIA "
                        "attachment(s)" % len(offsets))]
        eventos += [(t0 + float(off),
                     "[Plow_Chat] Sending video attachment (.mp4) to cht_x")
                    for off in offsets]
        with open(self.log, "w", encoding="utf-8") as fh:
            for quando, texto in eventos:
                fh.write(f"{carimbo_de_log(quando)} INFO "
                         f"gateway.platforms.base: {texto}\n")
        return t0

    def _cita_na_final(self, *clipes):
        corpo = "\n".join(f"MEDIA:{os.path.abspath(c)}" for c in clipes)
        escreve_state_db(self.db, [("assistant", "Both clips are done.\n" + corpo,
                                    "stop")])
        for c in clipes:
            warden.entregas_registra(c)

    def test_envios_desde_devolve_o_carimbo_de_cada_tentativa(self):
        """Sem os carimbos na mão, nada acima pode medir intervalo nenhum."""
        t0 = self._log_cronometrado([0.001, 4.866])
        lida = warden.envios_desde(t0 - 1)
        self.assertEqual(lida["saiu"], 2, lida)
        carimbos = lida.get("carimbos")
        self.assertEqual(len(carimbos or []), 2, lida)
        self.assertAlmostEqual(carimbos[1] - carimbos[0], 4.865, delta=0.01)

    def test_os_dois_anexos_no_mesmo_milissegundo_nao_riscam_o_clipe(self):
        """O caso do dono: o comando dizia `confirmed` e o clipe estava perdido."""
        grande = self._arquivo_grande("corte-ec898de219-01.mp4")
        self._cita_na_final(grande)
        self._log_cronometrado([0.0, 0.0])
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("NOT confirming", err)
        self.assertNotIn("confirmed:", err)
        # e continua devendo: é o que faz o agente reenviar
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(grande)])

    def test_a_recusa_mostra_o_intervalo_medido_e_o_tamanho(self):
        """Uma recusa que não mostra a medida não dá para conferir nem contestar."""
        grande = self._arquivo_grande("corte-ec898de219-01.mp4")
        self._cita_na_final(grande)
        self._log_cronometrado([0.0, 0.0])
        _code, err = self._confirma(grande)
        self.assertIn("0.000s", err)
        self.assertIn("MB", err)
        self.assertIn("Send it again", err)

    def test_o_intervalo_do_7a_risca_o_clipe(self):
        """O controle: mesmo par de linhas, 4,866 s entre elas, e ele CHEGOU."""
        grande = self._arquivo_grande("corte-7a-01.mp4")
        self._cita_na_final(grande)
        self._log_cronometrado([0.001, 4.866])
        code, err = self._confirma(grande)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_um_clipe_pequeno_no_mesmo_milissegundo_nao_e_acusado(self):
        """O piso é uma medida de 16 MB; abaixo dele não há medida nenhuma.

        `warden delivered` prefere não dizer nada a dizer o que não mediu --
        nos dois sentidos. Acusar um arquivo de 1 byte de não ter subido em
        0,5 s seria inventar uma física que este projeto não mediu, e o custo
        disso é um livro que nunca fecha (achado 11 do 7a).
        """
        self._cita_na_final(self.clipe)
        self._log_cronometrado([0.0, 0.0])
        code, err = self._confirma(self.clipe)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])


class ACONFIRMACAONaoESobreUmaTENTATIVA(_ComAsDuasPontas):
    """`confirmed:` era dito sobre uma linha escrita ANTES do envio.

    `_SAIU = "Sending video attachment"` sai de base.py:3880, e o `await
    self.send_video(...)` é a linha 3881 -- a linha do log é a declaração de
    intenção do gateway, não o retorno do upload. `Delivering N non-image
    MEDIA` (base.py:3891) é escrita antes do laço da 3893. Nenhuma das duas
    sabe se um byte subiu.

    Em cima dessas duas o comando escrevia `confirmed: <clipe> -- ... and 2
    video attachment(s) LEFT after it.` -- e no dia da demonstração as duas
    linhas existiam e o dono não tinha nenhum dos dois arquivos.

    A única prova de chegada que existe hoje é o `uid` que a API do Plow
    devolve por mensagem (`_post_message` -> `SendResult(message_id=...)`,
    plow_chat:2482). O Hermes não o persiste: `messages.platform_message_id`
    está NULL nas 31 mensagens do assistente desta sessão. Enquanto não houver
    recibo, a saída deste comando não pode usar a palavra da chegada.
    """

    def _saida_boa(self):
        escreve_state_db(self.db, [
            ("assistant", f"here it is\nMEDIA:{os.path.abspath(self.clipe)}",
             "stop")])
        self._anexo_saiu()
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        return err

    def test_a_saida_boa_nao_usa_a_palavra_confirmed(self):
        err = self._saida_boa()
        self.assertNotIn("confirmed", err.lower())

    def test_a_saida_boa_diz_que_isto_nao_e_chegada(self):
        err = self._saida_boa()
        self.assertIn("NOT verified", err)
        self.assertIn("arriv", err)          # arrived / arrival

    def test_a_saida_boa_chama_a_linha_do_log_de_tentativa(self):
        """`left after it` afirma saída. A linha só diz "vou tentar"."""
        err = self._saida_boa()
        self.assertNotIn("left after it", err)
        self.assertIn("attempt", err)

    def test_com_um_anexo_so_a_saida_nao_finge_ter_cronometrado(self):
        """Um anexo só não tem intervalo nenhum: a duração do ÚLTIMO upload não
        é escrita em lugar nenhum do log. Dizer "nenhum deles instantâneo" aí
        seria a mesma mentira de novo, um degrau abaixo.

        E ESTE TESTE ESTAVA CONSAGRANDO UM BURACO. O `self.assertEqual(code, 0)`
        que `_saida_boa` faz dizia, sobre um log de UMA linha de anexo, que o
        clipe podia ser riscado -- e riscar é o que faz o agente não reenviar.
        Rodado em 16/09/2026 com o mesmo log e um arquivo de 16 MB: EXIT 0, e
        o clipe some do livro sem que nada tenha medido o upload.

        O que este teste mede continua valendo, porque `self.clipe` tem 1 byte:
        abaixo de `_BYTES_QUE_DEMORAM` o cronômetro nunca foi o guarda deste
        arquivo e segurar o livro ali seria o achado 11 do 7a de volta. A linha
        acrescentada abaixo é a borda -- o MESMO log com um arquivo grande não
        pode ser riscado --, para que o "code 0" daqui não volte a ser lido
        como "um anexo só basta".
        """
        err = self._saida_boa()                      # um anexo só, 1 byte
        self.assertNotIn("none of them instant", err)
        self.assertIn("no gap to time", err)
        grande = os.path.join(self.dir, "corte-16mb.mp4")
        with open(grande, "wb") as fh:
            fh.write(b"\0" * 16_000_000)
        os.remove(self.db)               # o state.db da primeira metade
        escreve_state_db(self.db, [
            ("assistant", f"here it is\nMEDIA:{os.path.abspath(grande)}",
             "stop")])
        self._anexo_saiu()
        warden.entregas_registra(grande)
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("NOT confirming", err)
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(grande)])

    def test_com_dois_anexos_espacados_a_saida_diz_o_que_mediu(self):
        dois = self._arquivo("corte-02.mp4")
        escreve_state_db(self.db, [
            ("assistant", f"here they are\nMEDIA:{os.path.abspath(self.clipe)}\n"
             f"MEDIA:{os.path.abspath(dois)}", "stop")])
        agora = time.time()
        with open(self.log, "w", encoding="utf-8") as fh:
            for off in (0.0, 5.0):
                quando = agora + off
                fh.write(carimbo_de_log(quando)
                         + " INFO gateway.platforms.base: [Plow_Chat] "
                           "Sending video attachment (.mp4) to cht_x\n")
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 0, err)
        self.assertIn("apart", err)

    def test_o_comando_registra_onde_a_prova_de_chegada_ESTARIA(self):
        """A prosa que impede a próxima pessoa de refazer a busca de 16/09.

        Quatro candidatas a prova de chegada foram lidas dentro do container
        vivo e nenhuma serve: `platform_message_id` está NULL nas 31 mensagens
        do assistente, `delivery_obligations` diz `delivered` para a prosa que
        o dono não recebeu, o `response_store.db` é cache do modelo, e nenhum
        uid de plataforma aparece nos logs. A única prova que existe é o GET da
        thread no Plow, que exige um bearer que este comando não tem.
        """
        doc = warden.cmd_delivered.__doc__
        self.assertIn("platform_message_id", doc)
        self.assertIn("/v1/chats/", doc)
        self.assertIn("handed off", doc)

    def test_o_livro_continua_fechando(self):
        """Trocar a palavra não pode reabrir o defeito do 7a.

        Se a saída honesta deixasse de riscar, `warden delivered` voltaria a
        responder `NOT confirmed as sent` para sempre e o agente reenviaria os
        mesmos clipes em todo turno -- msg 97 de 7a.
        """
        self._saida_boa()
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_sem_gateway_log_tambem_nao_diz_confirmed(self):
        """O outro ramo que dizia `confirmed:` -- log ilegível.

        O `assertEqual(code, 0)` que estava aqui media a PALAVRA e deixava
        passar o EFEITO: trocar "confirmed" por "NOT verified" sem parar de
        riscar deixa o agente exatamente onde ele estava -- sem reenviar. A
        asserção agora é sobre as duas coisas, porque foi a segunda que custou
        os dois clipes de 16/09.
        """
        escreve_state_db(self.db, [
            ("assistant", f"here it is\nMEDIA:{os.path.abspath(self.clipe)}",
             "stop")])
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        warden.entregas_registra(self.clipe)
        code, err = self._confirma()
        self.assertEqual(code, 1, err)
        # A palavra só pode aparecer negada: "NOT confirmed as sent", a linha
        # que lista quem ainda deve. Qualquer outra ocorrência é a afirmação
        # que este comando não tem como fazer.
        for antes in err.lower().split("confirmed")[:-1]:
            self.assertTrue(antes.endswith("not "), err)
        self.assertIn("NOT verified", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)


# ---------------------------------------------------------------------------
# A auditoria de 16/09/2026, depois do conserto do cronômetro: quatro buracos
# NA REDE que o cronômetro é. Cada um foi medido rodando o comando, não
# deduzido, e cada classe abaixo é o caso medido.
# ---------------------------------------------------------------------------


class _ComCronometro(_ComAsDuasPontas):
    """Base dos quatro: arquivo grande de verdade e log por EPISÓDIO.

    Um episódio é o que o gateway escreve por mensagem entregue: uma linha
    `Delivering N non-image MEDIA attachment(s)` e, depois dela, as N linhas
    `Sending video attachment`. O formato foi lido dos logs reais deste
    repositório -- logs-7a/publicacao/nuvem-logs/gateway.log:83-91 tem dois
    episódios seguidos, um de 2 anexos às 15:14:04 e um de 1 anexo às 15:17:07
    -- e não inventado aqui.
    """

    def _arquivo_grande(self, nome="corte-16mb.mp4", megas=16):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\0" * (megas * 1000 * 1000))
        return caminho

    def _linha(self, quando, texto):
        return (f"{carimbo_de_log(quando)} INFO gateway.platforms.base: "
                f"[Plow_Chat] {texto}\n")

    def _log_episodios(self, episodios, base=None):
        """`episodios` = [(offset_do_anuncio, anunciados, [offsets_dos_anexos])].

        `anunciados=None` escreve o episódio SEM a linha de anúncio, que é o
        caso dos testes antigos desta suíte.
        """
        t0 = float(base if base is not None else time.time())
        with open(self.log, "w", encoding="utf-8") as fh:
            for quando_anuncio, anunciados, anexos in episodios:
                if anunciados is not None:
                    fh.write(self._linha(
                        t0 + quando_anuncio,
                        "Delivering %d non-image MEDIA attachment(s)"
                        % anunciados))
                for off in anexos:
                    fh.write(self._linha(
                        t0 + off,
                        "Sending video attachment (.mp4) to cht_x"))
        return t0

    def _cita_na_final(self, *clipes):
        corpo = "\n".join(f"MEDIA:{os.path.abspath(c)}" for c in clipes)
        escreve_state_db(self.db, [("assistant", "Here they are.\n" + corpo,
                                    "stop")])
        for c in clipes:
            warden.entregas_registra(c)


class OCronometroSoOlhaOEpisodioDaquelaMensagem(_ComCronometro):
    """Qualquer anexo POSTERIOR desarmava o cronômetro inteiro.

    Medido em 16/09/2026, rodando o comando: `envios_desde` lia o log INTEIRO
    depois da mensagem, sem teto, e `_menor_intervalo` é um `max`. O episódio
    da mensagem sozinho dá intervalo 0,000 s e o comando acusa; basta UMA linha
    de anexo de um episódio POSTERIOR -- a resposta seguinte, três minutos
    depois -- para o `max` virar 180 s, ficar acima do piso, e o alarme calar
    sobre o clipe que se perdeu.

    A janela é o episódio: a linha de anúncio abre um, a próxima linha de
    anúncio fecha esse e abre outro, e nenhum episódio conta mais anexos do que
    anunciou. É a estrutura que os logs reais têm -- em
    logs-7a/publicacao/agent-1-logs/gateway.log cada `Sending video attachment`
    vem depois de um `Delivering N`, e o N bate com a contagem da corrida.
    """

    def test_o_anexo_do_episodio_seguinte_nao_entra_na_conta(self):
        t0 = self._log_episodios([(0.0, 2, [0.001, 0.001]),
                                  (180.0, 1, [180.001])])
        lida = warden.envios_desde(t0 - 1)
        self.assertEqual(lida["saiu"], 2, lida)
        self.assertEqual(len(lida["carimbos"]), 2, lida)
        self.assertLess(warden._menor_intervalo(lida["carimbos"]), 0.5, lida)

    def test_o_episodio_seguinte_nao_cala_o_alarme(self):
        """O caso inteiro: dois anexos engolidos, e a resposta de depois."""
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.0, 0.0]), (180.0, 1, [180.0])])
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("NOT confirming", err)
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(grande)])

    def test_mais_anexos_que_o_anunciado_nao_entram_no_episodio(self):
        """Sem anúncio próprio, um anexo de depois ainda é de depois.

        O teto de `anunciados` é a segunda borda da janela: o gateway escreve
        `Delivering N` e N linhas, então a linha N+1 é de outra entrega mesmo
        que o anúncio dela não tenha sido escrito.
        """
        t0 = self._log_episodios([(0.0, 2, [0.001, 0.001, 90.0])])
        lida = warden.envios_desde(t0 - 1)
        self.assertEqual(lida["saiu"], 2, lida)
        self.assertLess(warden._menor_intervalo(lida["carimbos"]), 0.5, lida)

    def test_o_episodio_do_7a_continua_riscando(self):
        """O controle: 4,866 s entre os dois anexos, e eles CHEGARAM."""
        grande = self._arquivo_grande("corte-7a-01.mp4")
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.001, 4.867]), (180.0, 1, [180.0])])
        code, err = self._confirma(grande)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])


class UmClipeSoPrecisaDeEvidenciaComoOsOutros(_ComCronometro):
    """Um anexo só não era protegido por nada, e "me dá um clipe" é o pedido.

    Medido em 16/09/2026: log com UMA linha de anexo em 0 ms, arquivo de 16 MB,
    `warden delivered <clipe>` -> EXIT 0 e clipe riscado. `_menor_intervalo`
    devolve None com menos de duas linhas, `upload_instantaneo` trata None como
    "nada a acusar", e o comando riscava.

    A escolha feita aqui é NÃO RISCAR quando o cronômetro não mediu e o arquivo
    é grande o bastante para a medida existir. A outra saída -- riscar com
    outra evidência -- foi procurada e não há: as quatro candidatas a prova de
    chegada estão mortas (ver o docstring de `cmd_delivered`), e a palavra do
    modelo já foi medida valendo zero. A única evidência que sobra é a da
    PESSOA, e ela agora tem um lugar: `warden delivered <clipe> --arrived`.

    O piso de tamanho é o MESMO que a acusação usa (`_BYTES_QUE_DEMORAM`):
    abaixo de 1 MB este projeto não mediu nada, então nem acusa nem se cala --
    simplesmente não é sobre isso.
    """

    def _args(self, clipe=None, arrived=False):
        return type("A", (), {"clip": clipe, "arrived": arrived})()

    def test_um_anexo_so_de_16_mb_nao_e_riscado(self):
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("NOT confirming", err)
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(grande)])

    def test_a_recusa_diz_por_que_nao_mediu_e_como_fechar_o_livro(self):
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        _code, err = self._confirma(grande)
        self.assertIn("no gap to time", err)
        self.assertIn("MB", err)
        self.assertIn("--arrived", err)

    def test_a_palavra_da_pessoa_fecha_o_livro(self):
        """A única evidência de CHEGADA que existe é a pessoa dizendo.

        Sem esta saída, "me dá um clipe" vira um livro que não fecha nunca e um
        agente reenviando o mesmo arquivo em todo turno -- o achado 11 do 7a,
        que custou caro. Com ela, quem fecha é quem recebeu.
        """
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        self.assertEqual(self._confirma(grande)[0], 1)
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = warden.cmd_delivered(self._args(grande, arrived=True))
        texto = err.getvalue() + out.getvalue()
        self.assertEqual(code, 0, texto)
        self.assertEqual(warden.entregas_pendentes(), [])
        self.assertIn("person", texto.lower())

    def test_a_palavra_da_pessoa_precisa_de_um_caminho(self):
        """`--arrived` sem arquivo esvaziaria o livro inteiro de uma vez."""
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = warden.cmd_delivered(self._args(None, arrived=True))
        self.assertEqual(code, 1, err.getvalue() + out.getvalue())
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_um_clipe_pequeno_com_um_anexo_so_continua_riscando(self):
        """Abaixo de 1 MB o cronômetro nunca foi o guarda deste clipe.

        Acusar aqui seria inventar uma física que este projeto não mediu, e o
        custo seria o livro que não fecha. A borda é a mesma dos dois lados.
        """
        self._cita_na_final(self.clipe)
        self._log_episodios([(0.0, 1, [0.001])])
        code, err = self._confirma(self.clipe)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])


class LogIlegivelNaoRisca(_ComCronometro):
    """"Não deu para ler" saía do livro pela mesma porta de "chegou".

    O ramo `if log is None` de `verifica_um_envio` dizia a verdade -- "não
    consigo ler o gateway.log daqui" -- e riscava assim mesmo. O efeito no
    agente é idêntico ao de uma confirmação: o clipe sai da lista, `warden
    delivered` sai 0, e o reenvio nunca acontece. Uma frase honesta com o
    efeito da mentira continua sendo o defeito que esta suíte vigia.
    """

    def test_sem_gateway_log_o_clipe_continua_devendo(self):
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("NOT verified", err)
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(grande)])

    def test_a_recusa_por_log_ilegivel_diz_como_fechar_o_livro(self):
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        _code, err = self._confirma(grande)
        self.assertIn("--arrived", err)

    def test_um_clipe_pequeno_sem_log_tambem_continua_devendo(self):
        """O tamanho não muda nada aqui: o que falta é a leitura inteira."""
        self._cita_na_final(self.clipe)
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        code, err = self._confirma(self.clipe)
        self.assertEqual(code, 1, err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)


class OPisoDoCronometroContraOSonoDoGateway(_ComCronometro):
    """O piso de 0,5 s pressupõe um gateway que não dorme entre anexos.

    `base.py:3894-3895` faz `await asyncio.sleep(human_delay)` antes de CADA
    anexo, e `_get_human_delay` (base.py:3729-3740) devolve 0,8-2,5 s quando
    `HERMES_HUMAN_DELAY_MODE=natural`. Com esse modo ligado, dois anexos
    ENGOLIDOS -- `SendResult(success=True)` sem envio -- ficariam espaçados
    pelo sono, acima do piso, e o cronômetro aprovaria a perda.

    O modo não está ligado nesta imagem (nenhum arquivo deste repositório
    escreve `HERMES_HUMAN_DELAY_MODE`), então isto é uma DEPENDÊNCIA não
    registrada, não um defeito ativo. Estes testes são o registro executável:
    se alguém ligar o modo, o cronômetro tem de saber que o intervalo que ele
    mede pode ser o sono, e não o upload.
    """

    def setUp(self):
        super().setUp()
        self._modo = os.environ.pop("HERMES_HUMAN_DELAY_MODE", None)
        self.addCleanup(self._repoe_modo)

    def _repoe_modo(self):
        os.environ.pop("HERMES_HUMAN_DELAY_MODE", None)
        if self._modo is not None:
            os.environ["HERMES_HUMAN_DELAY_MODE"] = self._modo

    def test_com_o_modo_ligado_um_intervalo_dentro_do_sono_nao_risca(self):
        os.environ["HERMES_HUMAN_DELAY_MODE"] = "natural"
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.001, 1.501])])   # 1,5 s: cabe no sono
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("HERMES_HUMAN_DELAY_MODE", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_com_o_modo_ligado_um_intervalo_acima_do_sono_risca(self):
        """2,5 s é o teto do sono; acima dele o que sobra é upload."""
        os.environ["HERMES_HUMAN_DELAY_MODE"] = "natural"
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.001, 4.867])])
        code, err = self._confirma(grande)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_sem_o_modo_o_piso_de_meio_segundo_continua_valendo(self):
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.001, 1.501])])
        code, err = self._confirma(grande)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_a_dependencia_esta_registrada_onde_o_piso_e_lido(self):
        """Um número que depende de um `sleep` de outra equipe tem de dizer.

        Sem esta linha, a próxima pessoa que mexer no piso -- ou que ligar o
        modo -- não tem como saber que os dois se tocam.
        """
        import inspect
        fonte = inspect.getsource(warden)
        piso = fonte[:fonte.index("_PISO_UPLOAD_S = 0.5")]
        registro = piso[piso.rindex("\n\n"):]
        self.assertIn("HERMES_HUMAN_DELAY_MODE", registro)
        self.assertIn("base.py:3894", registro)


# ---------------------------------------------------------------------------
# 16/09/2026, o custo do conserto anterior. O cronômetro passou a NÃO RISCAR o
# que não conseguiu medir, e isso estava certo -- mas a consequência não foi
# medida. Com UM anexo não existe intervalo entre dois carimbos, então não há
# medida possível NUNCA, e um clipe que nunca é riscado é um `lote render` que
# nunca mais roda: `para_por_clipe_devendo` é portão e sai 1. Nos logs reais
# deste repositório há 13 episódios de um anexo contra 9 de dois, então o caso
# sem medida é o caso COMUM -- "me dá um corte" --, não a exceção.
# ---------------------------------------------------------------------------


class UmClipeSoNaoPodeTravarOTrabalho(_ComCronometro):
    """As duas dívidas que eram uma só, e por que só uma delas trava.

    (i)  NUNCA ENTREGUE -- nenhuma linha `MEDIA:` saiu em mensagem final do
         turno. É o defeito de 15/09 (0 de 5 linhas do meio do turno
         chegaram), e ele CONTINUA travando: ali há o que fazer, e o que fazer
         é escrever a linha no lugar certo.

    (ii) ENTREGUE E NÃO VERIFICÁVEL -- a linha saiu na mensagem final e o log
         não deixa medir. Aqui não há nada que o agente possa fazer para
         produzir a medida, porque ela não existe: a duração do ÚLTIMO upload
         não é escrita em lugar nenhum do gateway.log. Esta é DITA, não
         travada: um reenvio e, depois dele, o livro fecha com uma frase que
         manda avisar a pessoa -- na língua dela -- que o arquivo foi mandado
         e que esta máquina não confirma chegada.

    O que estes testes vigiam nas duas pontas: (i) não pode afrouxar, e (ii)
    não pode travar. Um portão que não abre nunca não é honestidade, é o
    `--even-if-owed` que alguém vai digitar em duas horas -- e aí não sobra nem
    a honestidade nem o trabalho.
    """

    def _um_anexo_so(self, megas=16):
        """O caso comum: um clipe grande, um anexo, e nenhum intervalo."""
        grande = self._arquivo_grande(megas=megas)
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        return grande

    def test_a_primeira_passagem_ainda_e_portao_e_pede_UM_reenvio(self):
        grande = self._um_anexo_so()
        code, err = self._confirma(grande)
        self.assertEqual(code, 1, err)
        self.assertIn("NOT confirming", err)
        self.assertEqual([r["clip"] for r in warden.entregas_pendentes()],
                         [os.path.abspath(grande)])

    def test_o_livro_guarda_que_um_reenvio_ja_foi_pedido(self):
        """Sem isso a segunda passagem não sabe que não é a primeira."""
        grande = self._um_anexo_so()
        self.assertEqual(warden.entregas_reenvios_pedidos(grande), 0)
        self._confirma(grande)
        self.assertEqual(warden.entregas_reenvios_pedidos(grande), 1)

    def test_a_segunda_passagem_deixa_de_ser_portao(self):
        """O teste inteiro: um clipe só não pode travar a gravação do dono."""
        grande = self._um_anexo_so()
        self.assertEqual(self._confirma(grande)[0], 1)
        code, err = self._confirma(grande)
        self.assertEqual(code, 0, err)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_depois_dela_o_lote_render_volta_a_rodar(self):
        """O portão do `lote render` lê o MESMO livro. Ver `para_por_clipe_devendo`."""
        grande = self._um_anexo_so()
        self._confirma(grande)
        self.assertTrue(warden.entregas_pendentes())
        self._confirma(grande)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_a_segunda_passagem_manda_avisar_a_pessoa_na_lingua_dela(self):
        """Fechar sem medida só é honesto se a pessoa ficar sabendo."""
        grande = self._um_anexo_so()
        self._confirma(grande)
        _code, saida = self._confirma(grande)
        self.assertIn("their language", saida.lower())
        self.assertIn("cannot", saida.lower())
        self.assertIn("arriv", saida)

    def test_a_segunda_passagem_nunca_diz_confirmado(self):
        """A palavra da chegada continua sem ter com o que ser dita."""
        grande = self._um_anexo_so()
        self._confirma(grande)
        _code, saida = self._confirma(grande)
        # `can ` ERA um prefixo permitido, e uma auditoria de 16/09 mostrou o
        # buraco rodando a asserção contra "this machine can confirm it
        # arrived" -- que PASSAVA. "can confirm" é exatamente a afirmação que
        # esta máquina não pode fazer; só a negação dela é permitida.
        for antes in saida.lower().split("confirm")[:-1]:
            self.assertTrue(antes.endswith("not ") or antes.endswith("cannot "),
                            "a saída afirma que algo foi confirmado: %r" % saida)

    def test_o_recado_que_fecha_sem_medida_e_em_ingles(self):
        """Quem lê a saída é o modelo; a frase da pessoa é o modelo que escreve."""
        grande = self._um_anexo_so()
        self._confirma(grande)
        _code, saida = self._confirma(grande)
        self.assertEqual(ORecadoDeEntregaNaoFalaPortugues._pt_em(
            ORecadoDeEntregaNaoFalaPortugues, saida),
                         [], saida)

    def test_o_livro_deixa_escrito_que_fechou_sem_medida(self):
        """Riscado não pode virar indistinguível de medido."""
        grande = self._um_anexo_so()
        self._confirma(grande)
        self._confirma(grande)
        linha = [r for r in warden.entregas_all()
                 if r["clip"] == os.path.abspath(grande)][0]
        self.assertTrue(linha.get("sent"))
        self.assertTrue(linha.get("closed_without_measurement"))

    def test_o_log_ilegivel_tambem_para_de_travar_na_segunda(self):
        """A outra cegueira: sem gateway.log não há medida, e nunca haverá."""
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        warden.GATEWAY_LOG = os.path.join(self.dir, "nao-existe.log")
        self.assertEqual(self._confirma(grande)[0], 1)
        code, saida = self._confirma(grande)
        self.assertEqual(code, 0, saida)
        self.assertEqual(warden.entregas_pendentes(), [])

    def test_o_sono_do_gateway_tambem_para_de_travar_na_segunda(self):
        os.environ["HERMES_HUMAN_DELAY_MODE"] = "natural"
        self.addCleanup(os.environ.pop, "HERMES_HUMAN_DELAY_MODE", None)
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.001, 1.501])])
        self.assertEqual(self._confirma(grande)[0], 1)
        self.assertEqual(self._confirma(grande)[0], 0)
        self.assertEqual(warden.entregas_pendentes(), [])

    # ---- a outra ponta: (i) não pode afrouxar ----

    def test_nunca_citado_trava_para_sempre(self):
        """Ninguém escreveu a linha: não é falta de medida, é falta de envio."""
        grande = self._arquivo_grande()
        escreve_state_db(self.db, [("assistant", "rendering", "stop")])
        warden.entregas_registra(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        for _ in range(3):
            code, err = self._confirma(grande)
            self.assertEqual(code, 1, err)
            self.assertIn("NOT sent", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_meio_do_turno_trava_para_sempre(self):
        """O defeito de 15/09: 0 de 5 linhas do meio do turno chegaram."""
        grande = self._arquivo_grande()
        escreve_state_db(self.db, [
            ("assistant", f"MEDIA:{os.path.abspath(grande)}", "tool_calls")])
        warden.entregas_registra(grande)
        self._log_episodios([(0.0, 1, [0.001])])
        for _ in range(3):
            code, err = self._confirma(grande)
            self.assertEqual(code, 1, err)
            self.assertIn("NOT sent", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_o_gateway_anunciou_e_nao_mandou_trava_para_sempre(self):
        """`Delivering 1` e nenhuma linha de anexo é uma medida, não uma cegueira."""
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 1, [])])
        for _ in range(3):
            code, err = self._confirma(grande)
            self.assertEqual(code, 1, err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_o_envio_fantasma_MEDIDO_trava_para_sempre(self):
        """0 ms entre dois anexos de 16 MB é medida, e ela diz que não subiu.

        Esta é a única coisa que o log acusa sozinho, e afrouxá-la devolveria
        exatamente a perda de 16/09. O afrouxamento é só para a AUSÊNCIA de
        medida, nunca para uma medida que reprovou.
        """
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        self._log_episodios([(0.0, 2, [0.0, 0.0])])
        for _ in range(3):
            code, err = self._confirma(grande)
            self.assertEqual(code, 1, err)
            self.assertIn("NOT confirming", err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_a_falha_declarada_pelo_gateway_trava_para_sempre(self):
        grande = self._arquivo_grande()
        self._cita_na_final(grande)
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write(self._linha(time.time(),
                                 "Failed to send media (.mp4): boom"))
        for _ in range(3):
            code, err = self._confirma(grande)
            self.assertEqual(code, 1, err)
        self.assertEqual(len(warden.entregas_pendentes()), 1)

    def test_o_porque_de_nao_travar_esta_escrito_no_codigo(self):
        """Uma decisão desta ordem que não diz por quê é desfeita na primeira briga.

        A frase que tem de estar lá: honestidade que trava o trabalho vira
        `--even-if-owed` em duas horas, e aí não há nem honestidade nem
        trabalho.
        """
        import inspect
        fonte = inspect.getsource(warden._entregue_mas_sem_medida)
        self.assertIn("--even-if-owed", fonte)
        self.assertIn("13", fonte)           # os 13 episódios de um anexo só


class OCarimboDeTesteNaoEscreveMilSegundos(unittest.TestCase):
    """`,1000` não é um milissegundo válido, e o leitor lê os três dígitos.

    Medido nesta suíte: o formatador dos carimbos de mentira fazia
    `",%03d" % int(round((quando % 1) * 1000))`. Com `quando % 1 >= 0.9995` o
    arredondamento sobe para 1000, o campo passa a ter QUATRO dígitos, e
    `warden._carimbo_do_log` lê `linha[20:23]` == `"100"` -- 0,1 s onde deviam
    ser 1,0 s. O `strftime` acima, esse, trunca os segundos, então os dois
    campos discordavam por um segundo inteiro.

    Fez `ZeroMilissegundoNaoEUmUpload::test_envios_desde_devolve_o_carimbo_de_cada_tentativa`
    falhar uma vez em duas rodadas: um flake de ~1 em 2000 por carimbo, que
    numa suíte com dezenas de carimbos aparece sozinho de vez em quando. Um
    teste que falha por causa do seu próprio andaime ensina a ignorar a suíte.
    """

    def test_a_fracao_que_arredondava_para_mil_nao_escreve_quatro_digitos(self):
        for fracao in (0.9995, 0.99999, 0.9996, 0.99951):
            carimbo = carimbo_de_log(1_700_000_000 + fracao)
            self.assertEqual(len(carimbo), 23, carimbo)
            self.assertEqual(carimbo[20:23], "999", carimbo)

    def test_o_carimbo_volta_pelo_leitor_com_o_mesmo_instante(self):
        """A prova é a ida e a volta: quem escreve e quem lê têm de concordar."""
        for fracao in (0.0, 0.001, 0.5, 0.9, 0.9994, 0.9995, 0.99999):
            quando = 1_700_000_000 + fracao
            lido = warden._carimbo_do_log(
                carimbo_de_log(quando) + " INFO x: y")
            self.assertAlmostEqual(lido, quando, delta=0.002,
                                   msg=f"{fracao!r} -> {carimbo_de_log(quando)}")

class OCronometroNaoMenteNemTrancaOTrabalho(unittest.TestCase):
    """Os três buracos que uma auditoria independente abriu à mão em 16/09.

    Os três foram RODADOS contra o código antigo antes de existir conserto:
    uma engolida parcial era aprovada, um clipe pequeno em rede boa era acusado
    para sempre, e a saída afirmava "none of them instant" sobre um intervalo
    de 0,000 s que ela mesma tinha medido.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="warden-crono-")

    def _clipe(self, mb):
        caminho = os.path.join(self.dir, "c%s.mp4" % mb)
        with open(caminho, "wb") as fh:
            fh.write(b"\0" * int(mb * 1_000_000))
        return caminho

    @staticmethod
    def _log(carimbos):
        return {"carimbos": carimbos, "saiu": len(carimbos),
                "anunciados": len(carimbos)}

    def test_engolida_parcial_e_acusada(self):
        """`max` aprovava: um upload real mascarava os fantasmas ao lado dele.

        Três anexos, o primeiro levando 4,8 s e o resto 0,000 s. A pergunta que
        o número responde não é "algum demorou?", é "TODOS demoraram?".
        """
        dito = warden.upload_instantaneo(
            self._log([100.0, 104.8, 104.8]), self._clipe(16.5))
        self.assertTrue(dito, "engolida parcial passou batido")
        self.assertIn("0.000s", dito)

    def test_clipe_pequeno_em_rede_boa_nao_e_acusado(self):
        """1,5 MB a 0,300 s são 5 MB/s -- uma rede boa, não um fantasma.

        O piso de 0,5 s foi calibrado contra 16 MB. Aplicá-lo a um arquivo onze
        vezes menor é exigir que ele demore o mesmo, e o ramo da acusação não
        pede reenvio: o `lote render` ficava travado PARA SEMPRE.
        """
        self.assertIsNone(warden.upload_instantaneo(
            self._log([100.0, 100.3]), self._clipe(1.5)))

    def test_o_fantasma_de_16_09_continua_sendo_acusado(self):
        """O conserto não pode custar a acusação que motivou tudo isto."""
        dito = warden.upload_instantaneo(
            self._log([100.0, 100.0]), self._clipe(16.5))
        self.assertTrue(dito, "o fantasma de 16/09 deixou de ser acusado")

    def test_o_piso_de_tamanho_continua_valendo(self):
        """Abaixo de 1 MB não se mede nada: era o achado 11 do 7a."""
        self.assertIsNone(warden.upload_instantaneo(
            self._log([100.0, 100.0]), self._clipe(0.9)))

    def test_a_saida_nao_afirma_o_que_nao_mediu(self):
        """"none of them instant" saía sempre que houvesse dois carimbos.

        Inclusive quando o intervalo medido era zero. Agora a frase vem da
        medida: ou o número, ou a ausência dele.
        """
        import inspect
        fonte = inspect.getsource(warden)
        self.assertNotIn(' none of them instant"', fonte,
                         "a frase voltou a ser dita sem olhar o número")

