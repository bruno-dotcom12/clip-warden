"""As linhas `MEDIA:` na SAÍDA DA FERRAMENTA, que é a única coisa que não some.

Por que este arquivo existe, e o que ele mede.

No teste 7a (16/09/2026) o agente renderizou um clipe entregável às 14:54:33 e
nunca o entregou. As duas cópias da regra de entrega -- a persona e a skill --
tinham sido apagadas da memória do modelo antes disso:
`⚠️ Context file SOUL.md TRUNCATED: 30068 chars exceeds limit of 20000`
(logs-7a/container/logs/errors.log, 14:37:58) e
`[SKILL_PRUNED: content lost in compression]` para `warden-clip` (34.983 chars)
às 14:52:50, no MESMO instante em que o render começou
(logs-7a/ACHADOS-FASE-1.md, msgs 37/39/58).

A saída de ferramenta NÃO é podada. Então a regra de entrega tem de viajar
nela: o comando termina imprimindo as linhas `MEDIA:` prontas, uma por clipe,
com o caminho absoluto exato, e logo abaixo a instrução curta em inglês. Nada
para o modelo compor de memória -- compor caminho de memória é um defeito já
registrado neste projeto.

E o segundo pedido: enquanto houver clipe pronto e não enviado
(`entregas.json` com `"sent": false` -- medido em
logs-7a/container/warden/entregas.json, `corte-27cd49161b-01.mp4`, `sent:
false` nove minutos depois de pronto), um `lote render` novo NÃO roda. Ele
para e imprime o que já existe.
"""
import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden
# O dublê do lote inteiro -- rede, ffmpeg e Pillow -- mora em test_lote e é
# reusado aqui de propósito: duas montagens do mesmo lote falso são dois lotes
# que divergem no dia em que uma delas ganha um conserto.
from test_lote import _ComLoteFalso, _MediaFalso, _temp, URL, _nome  # noqa: E402


# A instrução, exata. Fica em inglês porque quem a lê é o modelo, na mesma
# língua do resto da saída do render -- e porque uma frase em português na
# saída de ferramenta vira prosa em português na conversa em inglês, que é
# outro defeito medido no mesmo teste 7a (msg 21, "Em produção.").
INSTRUCAO = "send these lines in your FINAL reply, nothing else pending"

# O rótulo que ABRE o bloco, lido do CÓDIGO e não copiado para cá.
#
# A auditoria da rodada 1 deu duas saídas e pediu uma decisão: ou o código passa
# a imprimir o literal `MEDIA: PRONTAS` que `warden-clip/SKILL.md:211` promete,
# ou a skill passa a citar o que o código realmente imprime. A decisão, e o
# motivo, está em `warden.ROTULO_PRONTAS`: `MEDIA: PRONTAS` é uma linha que
# COMEÇA com `MEDIA:`, e o bloco inteiro é copiado literalmente para a mensagem
# final -- o gateway leria `PRONTAS` como um caminho de arquivo. Além disso
# `PRONTAS` é palavra portuguesa dentro do único texto que o modelo repassa sem
# traduzir, que é o defeito do item 2 da mesma auditoria.
#
# Então existe UMA string, ela mora em `warden.py`, e quem a cita a lê de lá.
ROTULO = warden.ROTULO_PRONTAS


# Palavras que só existem em português. Não é um detector de idioma: é a lista
# curta do que NÃO pode aparecer num bloco que o modelo copia palavra por
# palavra para a mensagem da pessoa.
#
# Por que isso é um defeito e não um detalhe: em 7a a conversa era em inglês e
# o agente respondeu "Em produção." (live.db msg 21, 14:51:58), depois de ler
# um `prep` cujo cabeçalho era 100% português. O bloco final é o pior lugar
# possível para uma palavra em português, porque ele é o único texto da saída
# que o modelo é mandado copiar LITERALMENTE -- `Corte 1:` numa conversa em
# inglês é a ferramenta escolhendo a língua da pessoa por ela.
SO_EM_PORTUGUES = (
    # a mensagem e o que vai nela
    "corte", "cortes", "clipe", "clipes", "legenda", "legendas", "você",
    "vocês", "não", "mensagem", "arquivo", "caminho", "pessoa", "vídeo",
    "língua", "frase", "enviar", "envie", "pronto", "prontos", "segundos",
    "cabeçalho", "linha", "linhas",
    # a ficha do `prep`, medida na msg 28 do state.db de 7a
    # (nada de "decide" ou "material" aqui: são palavras das DUAS línguas, e
    # uma lista que acusa inglês é uma lista que ninguém mantém)
    "duração", "som", "padrão", "padrões", "quantidade", "preferência",
    "guardada", "campanha", "queimar", "transcrita", "ferramenta",
    "fonte", "nenhum", "nenhuma", "ninguém", "mudo", "disse",
    "vence", "escolhida", "pedida", "gancho", "idioma", "limites",
    "dentro", "recusar", "inteiro", "perguntar",
)


# Caminhos e NOMES DE ARQUIVO saem da conta. Os dois são dado, não prosa: um
# clipe gravado em `/home/joao/vídeos/` não é a ferramenta falando português, e
# `corte-27cd49161b-01.mp4` é o nome que este projeto dá aos seus arquivos --
# ele está gravado em `entregas.json`, em `links.json` e em toda linha `MEDIA:`
# já entregue, e renomeá-lo para caçar uma palavra seria trocar um defeito de
# idioma por um livro-razão que não bate.
_E_CAMINHO = re.compile(r"\S*/\S+|\S+\.[A-Za-z0-9]{2,4}\b")


def _palavras_pt(linha):
    """As palavras só-de-português numa linha de PROSA."""
    if linha.strip().startswith("MEDIA:"):
        return []
    sem_dados = _E_CAMINHO.sub(" ", linha)
    achadas = re.findall(r"[^\W\d_]+", sem_dados, flags=re.UNICODE)
    return [p for p in achadas if p.lower() in SO_EM_PORTUGUES]


def _bloco(err):
    """Só o bloco final: de `END YOUR TURN NOW` até a última linha."""
    return err.split("END YOUR TURN NOW", 1)[1]


def _ultimas_linhas(texto):
    return [l for l in texto.splitlines() if l.strip()]


class _ComEstado(unittest.TestCase):
    """Um WARDEN_DIR vazio por teste, porque o livro de entregas mora nele."""

    def setUp(self):
        self.dir = _temp(self, "warden-media-saida-")
        self.estado = os.path.join(self.dir, "estado")
        os.makedirs(self.estado, exist_ok=True)
        self._env = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.estado
        self.addCleanup(self._repoe)
        # A conversa de AGORA, como o container a tem. Sem ela a dívida antiga
        # não entra no bloco de propósito: um clipe devido numa conversa que
        # não existe mais, anexado nesta, é alguém recebendo um arquivo que não
        # pediu. Ver `caminhos_devidos_antes`.
        self._sessao = os.environ.get("WARDEN_SESSION_ID")
        os.environ["WARDEN_SESSION_ID"] = "sessao-do-teste"
        self.addCleanup(self._repoe_sessao)

    def _repoe_sessao(self):
        if self._sessao is None:
            os.environ.pop("WARDEN_SESSION_ID", None)
        else:
            os.environ["WARDEN_SESSION_ID"] = self._sessao

    def _repoe(self):
        if self._env is None:
            os.environ.pop("WARDEN_DIR", None)
        else:
            os.environ["WARDEN_DIR"] = self._env

    def _clipe(self, nome):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\x00" * 16)
        return caminho

    def _conta(self, liberados, failed=(), asked=None, **kw):
        asked = len(liberados) if asked is None else asked
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = warden.conta_do_lote(list(liberados), list(failed), asked, **kw)
        return code, out.getvalue(), err.getvalue()


# ─────────────────────────────────────────────── PEDIDO 3: a saída termina nelas

class ASaidaTerminaNasLinhasMEDIA(_ComEstado):
    """O último que o modelo lê é o que ele obedece.

    Medido em 7a: a ÚLTIMA linha da saída do render 1 era
    `# and say in that same message that this batch is NOT done: ...`
    (logs-7a/container/render-outs.txt:97), impressa DEPOIS do bloco de
    entrega. A leitura correta da última linha era "não entregue" -- e foi
    exatamente o que aconteceu: 21 segundos depois o agente chamou outro
    `lote render` (live.db msg 69 -> msg 70).
    """

    def test_a_ultima_linha_e_a_instrucao_curta(self):
        a = self._clipe("a.mp4")
        _code, _out, err = self._conta([("a.mp4", a)])
        self.assertEqual(_ultimas_linhas(err)[-1].strip(), INSTRUCAO)

    def test_uma_linha_MEDIA_por_clipe_logo_acima_da_instrucao(self):
        a, b = self._clipe("a.mp4"), self._clipe("b.mp4")
        _code, _out, err = self._conta([("a.mp4", a), ("b.mp4", b)])
        linhas = _ultimas_linhas(err)
        self.assertEqual(linhas[-1].strip(), INSTRUCAO)
        media = [l for l in linhas if l.strip().startswith("MEDIA:")]
        self.assertEqual([l.strip() for l in media],
                         [f"MEDIA:{a}", f"MEDIA:{b}"])

    def test_os_caminhos_sao_absolutos(self):
        """`cut --plan` aceita `out` relativo, e um caminho relativo colado
        numa mensagem final não é um arquivo para ninguém."""
        antes = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, antes)
        self._clipe("rel.mp4")
        _code, _out, err = self._conta([("rel.mp4", "rel.mp4")])
        media = [l.strip() for l in err.splitlines()
                 if l.strip().startswith("MEDIA:")]
        self.assertTrue(media)
        for linha in media:
            self.assertTrue(os.path.isabs(linha[len("MEDIA:"):]), linha)

    def test_o_aviso_de_lote_incompleto_vem_ANTES_do_bloco(self):
        """A ordem invertida. Entrega primeiro e proibição depois era ler
        'não entregue' na última linha (render-outs.txt:79-97)."""
        a = self._clipe("a.mp4")
        code, _out, err = self._conta([("a.mp4", a)],
                                      failed=[("b.mp4", "did not clear")],
                                      asked=2)
        self.assertEqual(code, 1)
        self.assertIn("NOT done", err)
        self.assertLess(err.index("NOT done"), err.index("MEDIA:"))
        self.assertLess(err.index("MEDIA:"), err.index(INSTRUCAO))

    def test_o_rotulo_do_bloco_e_uma_constante_que_o_codigo_imprime(self):
        """A metade da rede dupla que está na skill tem de apontar para cá.

        `warden-clip/SKILL.md:211` prometia um bloco `MEDIA: PRONTAS` que
        nenhuma linha deste código jamais imprimiu, e `test_skills.py:279`
        afirmava a mesma string sozinho -- as duas metades confirmando uma à
        outra e nenhuma olhando o programa. Agora a string é UMA, mora aqui, e
        este teste prova que ela é de fato o que sai.
        """
        a = self._clipe("a.mp4")
        _code, _out, err = self._conta([("a.mp4", a)])
        linhas = [l.strip() for l in _ultimas_linhas(err)]
        self.assertIn(ROTULO, linhas)
        # e ele ABRE o bloco: é a última coisa antes da primeira `MEDIA:`.
        self.assertLess(err.index(ROTULO), err.index(f"MEDIA:{a}"))
        pos = linhas.index(ROTULO)
        seguintes = [l for l in linhas[pos + 1:] if l]
        self.assertTrue(seguintes[0].startswith("<"), seguintes[:2])

    def test_o_rotulo_nao_pode_ser_uma_linha_MEDIA_falsa(self):
        """Por que a decisão NÃO foi imprimir `MEDIA: PRONTAS`.

        O bloco é copiado palavra por palavra para a mensagem final, e o
        gateway lê TODA linha `MEDIA:` dessa mensagem como um caminho de
        arquivo (`Delivering N non-image MEDIA`, `Sending video attachment`).
        Um rótulo que começa com `MEDIA:` vira uma tentativa de anexar um
        arquivo chamado `PRONTAS`, no meio da única mensagem que entrega.
        """
        self.assertFalse(ROTULO.startswith("MEDIA:"), ROTULO)
        self.assertEqual(_palavras_pt(ROTULO), [], ROTULO)

    def test_o_rotulo_sai_tambem_quando_so_ha_divida_antiga(self):
        velho = self._clipe("velho.mp4")
        warden.entregas_registra(velho)
        _code, _out, err = self._conta([], failed=[("x.mp4", "did not clear")],
                                       asked=1)
        self.assertIn(ROTULO, err)

    def test_nenhuma_linha_do_bloco_tem_palavra_so_de_portugues(self):
        """O bloco é copiado LITERALMENTE. Ele não pode escolher a língua.

        `print(f"Corte {i}: <caption>")` mandava o modelo abrir cada anexo com
        uma palavra portuguesa, em qualquer conversa. Era o pedido 3 servindo
        o defeito de idioma pela porta dos fundos.
        """
        a, b = self._clipe("a.mp4"), self._clipe("b.mp4")
        _code, _out, err = self._conta([("a.mp4", a), ("b.mp4", b)],
                                       failed=[("c.mp4", "ENDS mid-sentence")],
                                       asked=3)
        for linha in _bloco(err).splitlines():
            self.assertEqual(_palavras_pt(linha), [], linha)

    def test_o_bloco_em_disco_tambem_nao_fala_portugues(self):
        """A cópia em arquivo é a que o modelo lê quando stderr rolou demais;
        duas versões do bloco são duas ordens de trabalho."""
        a = self._clipe("a.mp4")
        arquivo = os.path.join(self.dir, "bloco.txt")
        err = io.StringIO()
        with redirect_stderr(err):
            warden.bloco_da_mensagem_final([("a.mp4", a)], arquivo=arquivo)
        texto = open(arquivo, encoding="utf-8").read()
        self.assertIn(ROTULO, texto)
        for linha in texto.splitlines():
            self.assertEqual(_palavras_pt(linha), [], linha)

    def test_o_bloco_diz_numa_linha_o_que_falhou_dentro_do_texto_a_enviar(self):
        """O que falhou tem de estar no texto que VAI para a pessoa, não só
        numa linha de contabilidade acima dele."""
        a = self._clipe("a.mp4")
        _code, _out, err = self._conta([("a.mp4", a)],
                                       failed=[("b.mp4", "ENDS mid-sentence")],
                                       asked=2)
        bloco = err.split("END YOUR TURN NOW", 1)[1]
        self.assertIn("b.mp4", bloco)


class OBlocoCarregaADividaANTIGA(_ComEstado):
    """Um lote que reprova tudo imprimia ZERO linhas MEDIA:.

    Foi o que aconteceu duas vezes em 7a (`# 0 of 1 cleared for delivery`,
    render_out3.log): o corte pronto de cinco minutos antes ficou órfão numa
    parte do histórico que a poda já tinha reescrito. A dívida está em disco
    (`entregas_pendentes()`); o bloco final tem de lê-la.
    """

    def test_um_clipe_devido_de_antes_entra_no_bloco(self):
        velho = self._clipe("velho.mp4")
        warden.entregas_registra(velho)
        novo = self._clipe("novo.mp4")
        _code, _out, err = self._conta([("novo.mp4", novo)])
        bloco = err.split("END YOUR TURN NOW", 1)[1]
        self.assertIn(f"MEDIA:{velho}", bloco)
        self.assertIn(f"MEDIA:{novo}", bloco)

    def test_um_lote_que_reprova_TUDO_ainda_imprime_o_clipe_devido(self):
        velho = self._clipe("velho.mp4")
        warden.entregas_registra(velho)
        code, _out, err = self._conta([], failed=[("novo.mp4", "did not clear")],
                                      asked=1)
        self.assertEqual(code, 1)
        self.assertIn(f"MEDIA:{velho}", err)
        self.assertEqual(_ultimas_linhas(err)[-1].strip(), INSTRUCAO)

    def test_o_clipe_ja_confirmado_nao_volta_ao_bloco(self):
        velho = self._clipe("velho.mp4")
        warden.entregas_registra(velho)
        warden.entregas_confirma(velho)
        novo = self._clipe("novo.mp4")
        _code, _out, err = self._conta([("novo.mp4", novo)])
        self.assertNotIn(f"MEDIA:{velho}", err)

    def test_o_mesmo_clipe_nao_sai_duas_vezes(self):
        """Este lote registra o que liberou; a dívida antiga é a MESMA linha."""
        a = self._clipe("a.mp4")
        warden.entregas_registra(a)
        _code, _out, err = self._conta([("a.mp4", a)])
        self.assertEqual(err.count(f"MEDIA:{a}"), 1, err)

    def test_o_stdout_nao_ganha_um_MEDIA_a_mais(self):
        """O bloco é stderr de propósito: o stdout do lote já carrega um
        `MEDIA:` por clipe, impresso pelo `deliver`, e repetir dobra a
        contagem de anexos."""
        a = self._clipe("a.mp4")
        _code, out, _err = self._conta([("a.mp4", a)])
        self.assertNotIn("MEDIA:", out)


# ───────────────────────────── PEDIDO 4: clipe pronto e não enviado tranca o render

class OLoteNaoRecortaComClipePRONTOENaoEnviado(_ComLoteFalso):
    """A trava, e a porta que a impede de ser um beco sem saída.

    Em 7a o render 2 (14:57:42) e o render 3 (15:02:34) partiram com o corte
    01 devendo desde 14:54:33 -- `entregas.json`, `"sent": false` -- e nada no
    caminho do render lia esse livro (`_lote_render` não chama
    `entregas_pendentes()` em lugar nenhum). Renderizar de novo com clipe
    pronto na mão é gastar CPU para adiar a entrega que já podia acontecer.
    """

    def setUp(self):
        super().setUp()
        self._sessao = os.environ.get("WARDEN_SESSION_ID")
        os.environ["WARDEN_SESSION_ID"] = "sessao-do-teste"
        self.addCleanup(self._repoe_sessao)

    def _repoe_sessao(self):
        if self._sessao is None:
            os.environ.pop("WARDEN_SESSION_ID", None)
        else:
            os.environ["WARDEN_SESSION_ID"] = self._sessao

    def _devendo(self, nome="corte-pronto.mp4"):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\x00" * 16)
        warden.entregas_registra(caminho)
        return caminho

    def _prep(self):
        self._roda(["lote", "prep", URL])

    def test_com_clipe_devendo_o_render_nao_baixa_nada(self):
        self._prep()
        self.media.chamadas.clear()
        pronto = self._devendo()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertNotEqual(code, 0, erro)
        self.assertEqual([c for c in self.media.chamadas
                          if c[0] == "archive_windows"], [], erro)
        self.assertIn(f"MEDIA:{pronto}", erro)

    def test_a_trava_diz_numa_linha_que_ha_clipe_pronto_nao_enviado(self):
        self._prep()
        pronto = self._devendo()
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertIn("NOT been sent", erro)
        self.assertEqual(_ultimas_linhas(erro)[-1].strip(), INSTRUCAO)
        self.assertIn(os.path.basename(pronto), erro)

    def test_a_trava_nomeia_a_flag_que_a_abre(self):
        self._prep()
        self._devendo()
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertIn("--even-if-owed", erro)

    def test_com_a_flag_o_render_roda(self):
        self._prep()
        self._devendo()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x",
             "--even-if-owed"])
        self.assertEqual(code, 0, erro)
        self.assertTrue([c for c in self.media.chamadas
                         if c[0] == "archive_windows"], erro)

    def test_com_a_flag_a_divida_antiga_ainda_sai_no_bloco(self):
        self._prep()
        pronto = self._devendo()
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x",
             "--even-if-owed"])
        bloco = erro.split("END YOUR TURN NOW", 1)[1]
        self.assertIn(f"MEDIA:{pronto}", bloco)

    def test_sem_divida_o_render_roda_como_sempre(self):
        self._prep()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)

    def test_um_clipe_devido_cujo_arquivo_sumiu_nao_tranca_nada(self):
        """O que se cobra é entrega de clipe que EXISTE."""
        self._prep()
        pronto = self._devendo()
        os.remove(pronto)
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)


# ───────── AUDITORIA 8: os OUTROS caminhos de render também leem o livro

class OsCaminhosDeCORTEAVULSOTambemLeemOLivro(_ComLoteFalso):
    """Por que `warden cut` AVISA e `warden lote render` PARA.

    A auditoria pediu uma decisão escrita: ou a leitura de
    `entregas_pendentes()` se estende ao `warden cut` avulso, ou o código diz
    por que ele fica de fora. A resposta é nenhuma das duas inteiras -- ele
    entra, mas como AVISO e não como portão -- e a razão é que os três
    comandos não pedem a mesma coisa:

      `lote render`  -- "faça um LOTE deste link". É o comando que 7a chamou
                        três vezes com o corte 01 devendo desde 14:54:33
                        (links.json: 14:52:50, 14:57:42, 15:02:34). Ele é o
                        que gasta download + N renders para adiar uma entrega
                        que já podia acontecer, e é o único que PARA.

      `cut --plan`   -- é o comando que `conta_do_lote` IMPRIME quando sobram
                        clipes (`warden cut --plan <arquivo>`). Travá-lo
                        deixaria os clipes que faltam sem nenhum comando que
                        os busque: a trava viraria um beco sem saída pela
                        porta que a própria ferramenta mandou usar.

      `cut` avulso   -- é um corte NOMEADO, com `--start`, `--end` e `--out`
                        escritos por alguém. Quem digita uma janela exata já
                        decidiu; recusar aqui seria a ferramenta discutindo
                        com uma ordem explícita.

    Nos dois casos de aviso as linhas `MEDIA:` devidas saem no fim, no bloco
    de `conta_do_lote` -- ninguém fica órfão, que era o defeito real.
    """

    def setUp(self):
        super().setUp()
        self._sessao = os.environ.get("WARDEN_SESSION_ID")
        os.environ["WARDEN_SESSION_ID"] = "sessao-do-teste"
        self.addCleanup(self._repoe_sessao)

    def _repoe_sessao(self):
        if self._sessao is None:
            os.environ.pop("WARDEN_SESSION_ID", None)
        else:
            os.environ["WARDEN_SESSION_ID"] = self._sessao

    def _devendo(self, nome="corte-pronto.mp4"):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"\x00" * 16)
        warden.entregas_registra(caminho)
        return caminho

    def _fonte(self):
        caminho = os.path.join(self.dir, "fonte.mp4")
        with open(caminho, "wb") as fh:
            fh.write(b"\x00" * 64)
        return caminho

    def test_o_cut_avulso_diz_que_ha_clipe_devendo(self):
        pronto = self._devendo()
        saida = os.path.join(self.estado, "novo.mp4")
        code, _out, erro = self._roda(
            ["cut", self._fonte(), "--start", "1", "--end", "11",
             "--out", saida])
        self.assertIn("owed", erro)
        self.assertIn(os.path.basename(pronto), erro)
        # AVISO, não portão: o corte nomeado sai.
        self.assertEqual(code, 0, erro)

    def test_o_cut_avulso_nao_e_travado_pela_divida(self):
        self._devendo()
        saida = os.path.join(self.estado, "novo.mp4")
        code, _out, erro = self._roda(
            ["cut", self._fonte(), "--start", "1", "--end", "11",
             "--out", saida])
        self.assertEqual(code, 0, erro)
        self.assertTrue(os.path.isfile(saida), erro)   # o corte nomeado saiu

    def test_o_cut_plan_avisa_e_nao_trava(self):
        """O comando que `conta_do_lote` manda rodar não pode ser travado por
        ela mesma: seriam os clipes que faltam sem comando que os busque."""
        pronto = self._devendo()
        plano = os.path.join(self.dir, "resto.json")
        with open(plano, "w", encoding="utf-8") as fh:
            json.dump({"source": self._fonte(),
                       "clips": [{"start": 1.0, "end": 11.0,
                                  "out": os.path.join(self.estado, "p1.mp4")}]}, fh)
        code, _out, erro = self._roda(["cut", "--plan", plano])
        self.assertIn("owed since before this batch", erro)
        self.assertIn(os.path.basename(pronto), erro)
        self.assertEqual(code, 0, erro)
        # e a linha devida sai no bloco final, que é o que impede a órfã
        self.assertIn(f"MEDIA:{pronto}", erro)


# ────────────────────────── CAUSA 6: duas ordens que se excluem na mesma saída

class _MediaReprovaOSegundo(_MediaFalso):
    """O clipe `-02` volta com uma quebra de estilo; o `-01` passa.

    É o lote de 7a: 1 de 2 liberado, o outro reprovado por terminar no meio da
    frase (render-outs.txt:77).
    """

    def cut(self, source, out, rules, start, end, **kw):
        resultado = super().cut(source, out, rules, start, end, **kw)
        # Pelo GANCHO, e não pelo sufixo do arquivo: um lote de uma janela só
        # escreve `-01` e o teste do "nada passou" não teria como existir.
        if kw.get("hook") == "dois":
            resultado["style_breaches"] = [
                'this clip ENDS mid-sentence, on "…the rule book of"']
        return resultado


class UmLoteComClipeLIBERADONaoMandaRecortar(_ComLoteFalso):
    """`END YOUR TURN NOW` e `re-cut it` na MESMA saída.

    Medido em 7a, render-outs.txt:79-95: para o clipe 02 reprovado saiu
    `re-cut it: a shorter hook, a different window, or no --subtitles.`
    (warden.py:2810) e para o clipe 01 liberado saiu `END YOUR TURN NOW ...
    (no tool call after it)` (warden.py:2963). Uma manda terminar o turno sem
    chamar ferramenta; a outra manda chamar a ferramenta de novo. O agente
    obedeceu a segunda 21 segundos depois (msg 69 -> msg 70) e nunca mais
    falou com a pessoa.
    """

    def setUp(self):
        super().setUp()
        self.media = _MediaReprovaOSegundo(self.dir)
        warden._media = lambda: self.media

    def test_com_um_clipe_liberado_a_saida_nao_manda_re_cortar(self):
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30,60-80",
             "--hooks", "um|dois"])
        self.assertEqual(code, 1, erro)          # 1 de 2: o lote não fechou
        self.assertIn("MEDIA:", erro)            # o que passou continua saindo
        self.assertNotIn("re-cut it:", erro)
        self.assertEqual(_ultimas_linhas(erro)[-1].strip(), INSTRUCAO)

    def test_nem_quando_NADA_passa_a_saida_manda_re_cortar_sozinha(self):
        """Renders 2 e 3 de 7a: `# 0 of 1 cleared` e o agente re-cortou
        mesmo assim, duas vezes, sem dizer uma palavra à pessoa."""
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "60-80", "--hooks", "dois"])
        self.assertEqual(code, 1, erro)
        self.assertNotIn("re-cut it:", erro)
        # e a ordem que sobra é falar com a pessoa
        self.assertIn("ask", erro.lower())


if __name__ == "__main__":
    unittest.main()


# ══════════════ AUDITORIA 7: o cabeçalho do prep é a primeira coisa que ele lê

class OCabecalhoDoPrepNaoFalaPortugues(_ComLoteFalso):
    """A primeira e a última coisa que o agente lê a cada render.

    Medido em 7a (logs-7a/container/state.db, msg 28): a ficha do `prep` saía
    100% em português numa conversa em INGLÊS --

        duração: 30s (pedida na mensagem)
        som: original (padrão; a campanha não decide isso)
        hook e legenda: en (padrão: a língua da fonte, lida do material)
        legenda: queimar em en, transcrita pela ferramenta (padrão)

    -- e o agente respondeu "Em produção." (msg 21, 14:51:58). Texto de
    ferramenta não é prosa para a pessoa: é instrução para o modelo, e ela
    chega antes de qualquer palavra que ele escreva. Um cabeçalho em português
    é um banho de português que antecede toda a conversa.

    Repare no que este teste NÃO faz: ele não proíbe o agente de falar
    português. A língua da conversa é da pessoa. O que ele proíbe é a
    FERRAMENTA escolher uma.
    """

    def test_a_ficha_do_prep_sai_em_ingles(self):
        _code, saida, erro = self._roda(["lote", "prep", URL])
        for linha in (saida + erro).splitlines():
            self.assertEqual(_palavras_pt(linha), [], linha)

    def test_a_ficha_com_numeros_pedidos_na_mensagem_sai_em_ingles(self):
        _code, saida, erro = self._roda(
            ["lote", "prep", URL, "--clips", "3", "--seconds", "45"])
        for linha in (saida + erro).splitlines():
            self.assertEqual(_palavras_pt(linha), [], linha)

    def test_o_cabecalho_do_render_sai_em_ingles(self):
        self._roda(["lote", "prep", URL])
        _code, saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        for linha in (saida + erro).splitlines():
            self.assertEqual(_palavras_pt(linha), [], linha)


# ════════════ CAUSAS 2, 3 e 4: nenhuma string IMPRESSA do caminho do render em PT

class NenhumaStringIMPRESSADORenderFalaPortugues(unittest.TestCase):
    """A varredura, e por que ela é estática e não por execução.

    Os testes acima cobrem o cabeçalho do `prep` e o bloco final porque esses
    dois caminhos têm dublê. As notas do render não têm: `não consegui montar
    o contact sheet`, `ATENÇÃO: <perda>`, `N cue(s) fecham numa palavra que
    pede complemento`, `AVISO este clipe tem legenda de fala queimada` e a
    instrução do `.env` só aparecem quando algo específico dá errado, e montar
    um dublê para cada uma seria testar o dublê.

    Então a varredura lê o CÓDIGO: toda string literal de quatro palavras ou
    mais que chega a um `print`, a um `die` ou a um `notes.append` nos três
    módulos do caminho do render. É o mesmo critério dos outros testes deste
    arquivo -- texto de ferramenta é instrução para o modelo -- aplicado ao
    lugar onde a instrução nasce, em vez de ao lugar onde ela sai.

    Medido em 7a (state.db msg 28 e msg 69): o cabeçalho do `prep` e o
    checklist do render eram as primeiras e as últimas linhas que o agente lia
    a cada render, e os dois saíam 100% em português numa conversa em inglês.
    Ele respondeu "Em produção." (msg 21, 14:51:58).

    O que esta varredura NÃO cobre, de propósito: `warden_post.py`,
    `warden_tiktok.py` e `warden_youtube.py`. Eles não estão no caminho do
    render, nenhum deles foi exercitado em 7a, e varrê-los nesta rodada seria
    trocar a correção medida por uma reescrita grande e não medida. Estão
    relatados como pendência, não como feito.
    """

    MODULOS = ("warden.py", "warden_media.py", "warden_style.py")

    def _literais_impressos(self, caminho):
        import ast
        with open(caminho, encoding="utf-8") as fh:
            arvore = ast.parse(fh.read())
        for no in ast.walk(arvore):
            fn = getattr(no, "func", None)
            impressa = isinstance(no, ast.Call) and (
                (isinstance(fn, ast.Name) and fn.id in ("print", "die"))
                or (isinstance(fn, ast.Attribute)
                    and fn.attr in ("append", "add")))
            # `return "normal", (f"...")` e `saiu["porque"] = f"..."` não são
            # chamada nenhuma, e foi exatamente por aí que a nota `FRAMING: ...
            # não dá para dizer se isto é tela compartilhada` escapou da
            # primeira versão desta varredura -- a nota que a causa 2 nomeia em
            # logs-7a/container/state.db msg 69. Frase é frase, quem a leva até
            # o `print` não muda a língua dela.
            if not impressa and not isinstance(no, (ast.Return, ast.Assign)):
                continue
            for arg in ast.walk(no):
                if (isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        # Quatro palavras separa PROSA de chave de dicionário:
                        # `"legenda_topo"` e `"chave_origem"` são nomes de
                        # campo, não frases, e renomeá-los mexeria em fichas
                        # já gravadas em disco.
                        and len(arg.value.split()) >= 4
                        and not self._e_dado_em_portugues(arg.value)):
                    yield no.lineno, arg.value

    # O português que DEVE ficar, e por quê. São duas coisas, e nenhuma das
    # duas é a ferramenta falando com o modelo:
    #
    #   1. as listas de palavras de `warden_style` -- `NAO_FECHA_CUE`,
    #      `PRONOMES_COMPLEMENTO_PT`, `ABRE_SINTAGMA`. São o LÉXICO que decide
    #      se uma cue fecha no meio da frase (`ends_mid_sentence`, o portão que
    #      reprovou o clipe 02 em 7a). Traduzi-las desligaria o portão;
    #
    #   2. a AMOSTRA medida em warden_media.py:5463 -- `'portas, a que vai pra
    #      sala / e a que vai Você lembra,'`. É a transcrição real que mostra
    #      como fica uma legenda montada de meias-frases. Uma amostra traduzida
    #      deixa de ser uma medição e vira uma ilustração.
    #
    # As duas são DADO. O que este teste persegue é INSTRUÇÃO: frase que a
    # ferramenta manda o modelo ler, ou repassar.
    _DADO_EM_PT = ("nao_fecha_cue", "pronomes_complemento_pt", "abre_sintagma")

    def _e_dado_em_portugues(self, texto):
        baixo = texto.lower()
        if any(nome in baixo for nome in self._DADO_EM_PT):
            return True
        # Um léxico é uma parede de palavras sem pontuação de frase.
        if "\n" in texto and not any(c in texto for c in ".!?:"):
            return True
        # A amostra medida vem entre aspas simples, citada verbatim.
        return "portas, a que vai pra sala" in texto

    def test_nenhuma_string_impressa_do_render_fala_portugues(self):
        raiz = os.path.join(os.path.dirname(HERE), "warden-shared", "scripts")
        erros = []
        for modulo in self.MODULOS:
            for linha, texto in self._literais_impressos(
                    os.path.join(raiz, modulo)):
                pt = _palavras_pt(texto)
                if pt:
                    erros.append(f"{modulo}:{linha} {sorted(set(pt))} "
                                 f"-- {texto[:80]!r}")
        self.assertEqual(erros, [], "\n".join(erros))
