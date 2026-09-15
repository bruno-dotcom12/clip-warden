"""`warden lote`: do link aos clipes em duas saídas, sem perguntar nada.

A medição que justifica este comando, de 15/09/2026: um pedido de 2 clipes
levou 26min22s até o primeiro -- e único -- clipe. 10min34s foram perguntas que
não mudaram um quadro, 8min50s foram 35 chamadas do modelo, e só 4min52s foram
ferramenta. O trabalho custou cinco minutos e a conversa sobre o trabalho custou
vinte.

Nenhuma dessas 35 chamadas era desnecessária sozinha. O formato é que era:
`archive`, `transcribe`, `digest`, `signals`, `captions review`, `cut`,
`delivered` são sete comandos, e entre vários deles não há decisão nenhuma a
tomar -- `digest` e `signals` leem o MESMO arquivo e respondem sobre o MESMO
texto. Então `lote` é duas saídas: `prep` junta tudo o que o modelo precisa para
escolher as janelas, `render` faz tudo o que vem depois de escolhê-las.

A rede é simulada aqui inteira, de propósito. O que estes testes provam é a
ORQUESTRAÇÃO -- o que é chamado, em que ordem, com que padrões e o que sai na
tela -- e não o download nem o ffmpeg, que têm os seus próprios testes.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

os.environ.setdefault("WARDEN_DIR", tempfile.mkdtemp(prefix="warden-lote-base-"))

import warden
import warden_rules as R


def _temp(caso, prefix):
    """Um diretório temporário que se APAGA quando o teste acaba.

    A mesma regra da suíte principal: um dia rodando a suíte já deixou 4.939
    diretórios e cerca de 60 GB em $TMPDIR.
    """
    caminho = tempfile.mkdtemp(prefix=prefix)
    caso.addCleanup(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


def _pillow_ou_pula():
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("Pillow não está instalado")


SEGMENTOS = [
    {"start": 10.0, "end": 14.0, "text": "eu apostei tudo naquele jogo"},
    {"start": 60.0, "end": 64.0, "text": "e voce sabe o que aconteceu depois?"},
    {"start": 120.0, "end": 124.0, "text": "nunca mais fiz isso na minha vida"},
    {"start": 180.0, "end": 184.0, "text": "e a lição ficou para sempre"},
]


class _MediaFalso:
    """O `warden_media` que a rede teria devolvido, sem rede.

    Cada método anota o que foi pedido, porque metade do que se prova aqui é
    QUE a orquestração chamou a função certa com os argumentos certos -- é o
    que separa "lote é orquestração" de "lote reimplementou o download".
    """

    def __init__(self, base, publicada=True):
        self.base = base
        self.publicada = publicada
        self.chamadas = []
        self.cortes = []

    # -------------------------------------------------------------- prep
    def archive_trusted(self, url, out_dir, entries, mode="video"):
        self.chamadas.append(("archive_trusted", url, mode, list(entries)))
        os.makedirs(out_dir, exist_ok=True)
        caminho = os.path.join(out_dir, "fonte.pt.srt")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:10,000 --> 00:00:14,000\nfala\n\n")
        return caminho

    def transcribe(self, path, **kw):
        self.chamadas.append(("transcribe", path, kw))
        fonte = ("published subtitles (pt)" if self.publicada
                 else "whisper base")
        return {"source": fonte, "language": "pt", "segments": list(SEGMENTOS)}

    def to_srt(self, segments):
        linhas = []
        for i, s in enumerate(segments, 1):
            linhas.append(f"{i}\n00:00:{int(s['start']):02d},000 --> "
                          f"00:00:{int(s['end']):02d},000\n{s['text']}\n")
        return "\n".join(linhas)

    def digest(self, segments, window=None):
        self.chamadas.append(("digest", len(segments)))
        return "0:10 eu apostei tudo\n1:00 e voce sabe"

    def analyze_signals(self, segments, source=None):
        self.chamadas.append(("analyze_signals", source))
        return ([{"start": 60.0, "signals": ["question"],
                  "text": "e voce sabe o que aconteceu depois?"}], None)

    # ------------------------------------------------------------ render
    def archive_windows(self, rules, out_dir, url, janelas, folga=2.0,
                        trusted=None):
        self.chamadas.append(("archive_windows", url, list(janelas),
                              list(trusted or [])))
        os.makedirs(out_dir, exist_ok=True)
        saida = []
        for i, (de, _ate) in enumerate(janelas, 1):
            caminho = os.path.join(out_dir, f"janela-{i:02d}.mp4")
            with open(caminho, "wb") as fh:
                fh.write(b"\x00" * 32)
            saida.append((caminho, 2.0))
        return saida

    def _read_srt(self, caminho):
        linhas = []
        for s in SEGMENTOS:
            linhas.append({"start": s["start"], "end": s["end"],
                           "text": s["text"]})
        return linhas

    def cut(self, source, out, rules, start, end, **kw):
        self.cortes.append({"source": source, "out": out, "start": start,
                            "end": end, **kw})
        with open(out, "wb") as fh:
            fh.write(b"\x00" * 64)
        folha = os.path.splitext(out)[0] + "-contato.jpg"
        self._mosaico(folha)
        return {"out": out, "notes": [], "sheet": folha,
                "style": {"caption": bool(kw.get("caption_srt"))},
                "style_breaches": [],
                "asked_for_captions": bool(kw.get("caption_srt"))}

    @staticmethod
    def _mosaico(caminho):
        from PIL import Image
        Image.new("RGB", (60, 40), (30, 30, 30)).save(caminho)


class _ComLoteFalso(unittest.TestCase):
    def setUp(self):
        _pillow_ou_pula()
        self.dir = _temp(self, "warden-lote-")
        self.estado = os.path.join(self.dir, "estado")
        os.makedirs(self.estado, exist_ok=True)
        self._env = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.estado
        self.addCleanup(self._repoe)
        self.media = _MediaFalso(self.dir)
        self._media_antigo = warden._media
        warden._media = lambda: self.media
        self.addCleanup(setattr, warden, "_media", self._media_antigo)
        self._probe_antigo = warden.probe
        warden.probe = lambda caminho: {
            "width": 1080, "height": 1920, "duration_s": 20.0, "fps": "30/1",
            "codec": "h264", "audio_codec": "aac", "subtitle_tracks": 0,
            "size_mb": 2.0}
        self.addCleanup(setattr, warden, "probe", self._probe_antigo)
        # Nada aqui pode subir um processo de verdade.
        self._popen_antigo = subprocess.Popen
        self.spawnados = []

        def popen_falso(cmd, **kw):
            self.spawnados.append(list(cmd))
            return type("P", (), {"pid": 4242, "poll": lambda self: None})()

        subprocess.Popen = popen_falso
        self.addCleanup(setattr, subprocess, "Popen", self._popen_antigo)

    def _repoe(self):
        if self._env is None:
            os.environ.pop("WARDEN_DIR", None)
        else:
            os.environ["WARDEN_DIR"] = self._env

    def _campanha(self, cid, **video):
        r = R.blank()
        r.update({"id": cid, "name": cid, "schema": 1})
        r["video"].update(video)
        os.makedirs(os.path.dirname(warden.campaign_path(cid)), exist_ok=True)
        with open(warden.campaign_path(cid), "w") as fh:
            json.dump(r, fh)
        return cid

    def _roda(self, argv):
        saida, erro = io.StringIO(), io.StringIO()
        code = None
        with redirect_stdout(saida), redirect_stderr(erro):
            try:
                code = warden.main(argv)
            except SystemExit as saiu:
                code = saiu.code
        return code, saida.getvalue(), erro.getvalue()


URL = "https://www.youtube.com/watch?v=abcdefghijk"


class LotePrepEUmaLeituraSo(_ComLoteFalso):
    """Fase A: tudo o que o modelo precisa para escolher as janelas, de uma vez."""

    def test_prep_traz_texto_digest_e_sinais_na_mesma_saida(self):
        code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertEqual(code, 0)
        self.assertIn("SOURCE_TEXT:", saida)
        self.assertIn("TRANSCRIPT:", saida)
        self.assertIn("===== DIGEST =====", saida)
        self.assertIn("===== SIGNALS =====", saida)
        # e os dois na ORDEM em que se lê: primeiro o resumo, depois as provas
        self.assertLess(saida.index("DIGEST"), saida.index("SIGNALS"))
        # que é o ponto do comando: um `digest` e um `signals` eram duas
        # chamadas do modelo sobre o mesmo arquivo, sem decisão entre elas.
        nomes = [c[0] for c in self.media.chamadas]
        self.assertIn("digest", nomes)
        self.assertIn("analyze_signals", nomes)

    def test_prep_puxa_so_o_texto_e_nunca_o_video(self):
        """O caminho barato: legenda publicada se houver, áudio se não houver."""
        self._roda(["lote", "prep", URL])
        pull = next(c for c in self.media.chamadas if c[0] == "archive_trusted")
        self.assertEqual(pull[2], "text")

    def test_prep_transcreve_a_fonte_inteira_dizendo_para_que(self):
        self._roda(["lote", "prep", URL])
        chamada = next(c for c in self.media.chamadas if c[0] == "transcribe")
        self.assertEqual(chamada[2].get("proposito"), "fonte")
        self.assertIsNone(chamada[2].get("window"))

    def test_prep_nao_pede_licenca_e_avaliza_o_link_de_quem_mandou(self):
        """Quem manda o link está afirmando que pode usar o material.

        Decisão do dono, 15/09/2026. O que prova aqui é que o host do link
        entrou na lista que desce para o download, mesmo com a lista do dono
        vazia -- e que existe rastro do que foi autorizado e por quê.
        """
        code, saida, erro = self._roda(["lote", "prep", URL])
        self.assertEqual(code, 0, erro)
        pull = next(c for c in self.media.chamadas if c[0] == "archive_trusted")
        self.assertIn("www.youtube.com", pull[3])
        with open(warden.links_path(), encoding="utf-8") as fh:
            rastro = json.load(fh)
        self.assertEqual(rastro[-1]["url"], URL)
        self.assertIn("sent by the person", rastro[-1]["why"])
        # e nada na tela pergunta sobre direitos
        tudo = (saida + erro).lower()
        for proibida in ("permission", "licen", "rights", "authoris", "authoriz"):
            self.assertNotIn(proibida, tudo, f"a saída fala em {proibida!r}")

    def test_prep_anuncia_os_padroes_em_vez_de_perguntar(self):
        code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertEqual(code, 0)
        bloco = saida.split("SEM PERGUNTAR", 1)[1]
        self.assertIn("quantidade: 2 clipe(s)", bloco)
        self.assertIn("duração: 20s", bloco)
        self.assertIn("som: original", bloco)
        self.assertIn("hook: pt", bloco)
        self.assertIn("legenda: queimar em português", bloco)
        # e diz explicitamente para NÃO pedir confirmação
        self.assertIn("Do not ask them to confirm", saida)

    def test_o_numero_da_mensagem_vence_o_padrao(self):
        _code, saida, _erro = self._roda(
            ["lote", "prep", URL, "--n", "3", "--seconds", "45"])
        self.assertIn("quantidade: 3 clipe(s) (pedida na mensagem)", saida)
        self.assertIn("duração: 45s (pedida na mensagem)", saida)

    def test_a_campanha_vence_o_padrao_de_duracao_e_de_som(self):
        """O padrão é gosto; o limite da campanha é o pagamento."""
        cid = self._campanha("prep-apertada", duration_min_s=25,
                             duration_max_s=40, audio="forbidden")
        _code, saida, _erro = self._roda(["lote", "prep", URL, "--campaign", cid])
        self.assertIn("duração: 25s", saida)
        self.assertIn("limites da campanha", saida)
        self.assertIn("som: mudo", saida)
        self.assertIn("video.audio", saida)

    def test_prep_escreve_o_srt_e_a_ficha_de_onde_ele_veio(self):
        """A ficha é o que deixa o `render` assinar sozinho, e só por isso.

        Assinar automaticamente o que uma transcrição inventou seria assinar
        palavra não lida -- foi assim que `jokovic jokovic` foi para a tela.
        Assinar o que o detentor dos direitos publicou é outra coisa.
        """
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("SRT:", saida)
        ficha = os.path.join(warden._dir_do_lote(), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            dados = json.load(fh)
        self.assertTrue(dados["published"])

    def test_transcricao_nossa_nao_e_marcada_como_publicada(self):
        self.media.publicada = False
        self._roda(["lote", "prep", URL])
        ficha = os.path.join(warden._dir_do_lote(), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            self.assertFalse(json.load(fh)["published"])

    def test_prep_diz_qual_e_o_proximo_comando(self):
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("warden lote render", saida)
        self.assertIn("--windows", saida)
        self.assertIn("--hooks", saida)


class LoteRenderFazTudoODepois(_ComLoteFalso):
    """Fase B: as janelas, a legenda, os renders e as linhas MEDIA: no fim."""

    def _prep(self, *extra):
        self._roda(["lote", "prep", URL, *extra])

    def test_render_baixa_so_as_janelas_escolhidas(self):
        self._prep()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30,60-80",
             "--hooks", "um|dois"])
        self.assertEqual(code, 0, erro)
        pull = next(c for c in self.media.chamadas if c[0] == "archive_windows")
        self.assertEqual(pull[2], [(10.0, 30.0), (60.0, 80.0)])

    def test_render_corta_no_relogio_do_arquivo_de_janela_e_nao_da_fonte(self):
        """O arquivo da janela tem RELÓGIO PRÓPRIO: 179s da fonte viram 0s ali.

        Foi o pior defeito que este projeto entregou -- dois cortes do minuto
        três saíram com a legenda da abertura -- e o `IN_POINT` é a correção.
        O lote tem de cortar a partir dele, nunca do segundo da fonte.
        """
        self._prep()
        self._roda(["lote", "render", URL, "--windows", "180-200",
                    "--hooks", "x"])
        corte = self.media.cortes[0]
        self.assertEqual(corte["start"], 2.0)          # o IN_POINT do dublê
        self.assertEqual(corte["end"], 22.0)           # + os 20s da janela

    def test_os_hooks_vao_um_por_janela_na_ordem(self):
        self._prep()
        self._roda(["lote", "render", URL, "--windows", "10-30,60-80",
                    "--hooks", "primeiro|segundo"])
        self.assertEqual([c["hook"] for c in self.media.cortes],
                         ["primeiro", "segundo"])

    def test_a_legenda_publicada_e_assinada_sozinha(self):
        """A queima é determinística: a ferramenta transcreve e queima.

        O modelo não precisa repetir o texto da letra na conversa para que a
        legenda aconteça. Numa campanha musical isso é o serviço contratado --
        a campanha vem do detentor dos direitos e EXIGE legenda em português.
        """
        self._prep()
        self._roda(["lote", "render", URL, "--windows", "10-14", "--hooks", "x"])
        self.assertTrue(self.media.cortes[0]["caption_srt"],
                        "a janela veio da legenda publicada e não foi queimada")
        import warden_style as S
        srt = os.path.join(warden._dir_do_lote(), "lote.srt")
        ok, _porque = S.approval_state(srt, start=10.0, end=14.0)
        self.assertTrue(ok)

    def test_uma_linha_suspeita_nao_e_assinada_e_o_comando_diz_o_keep_exato(self):
        """O portão que foi contornado em 15/09, e a saída que o destrava.

        Uma linha com número é o que o Whisper mais erra, e uma legenda
        publicada pode trazer uma. Ela não é assinada sozinha -- mas a recusa
        NOMEIA o `--keep` exato, com a linha entre aspas, pronto para colar.
        Sem isso, "há uma linha suspeita" custa mais um turno e o clipe sai
        mudo numa campanha que paga pela legenda.
        """
        SEGMENTOS.append({"start": 300.0, "end": 304.0,
                          "text": "foram 90 mil reais naquele dia"})
        self.addCleanup(SEGMENTOS.pop)
        self._prep()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "300-304", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        self.assertIn('--keep "foram 90 mil reais naquele dia"', erro)
        self.assertIn("re-run this SAME command", erro)
        # e o clipe saiu SEM legenda, nunca com uma palavra que ninguém leu
        self.assertIsNone(self.media.cortes[0]["caption_srt"])

    def test_com_keep_a_linha_suspeita_e_assinada(self):
        SEGMENTOS.append({"start": 300.0, "end": 304.0,
                          "text": "foram 90 mil reais naquele dia"})
        self.addCleanup(SEGMENTOS.pop)
        self._prep()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "300-304", "--hooks", "x",
             "--keep", "foram 90 mil reais naquele dia"])
        self.assertEqual(code, 0, erro)
        self.assertTrue(self.media.cortes[0]["caption_srt"])

    def test_transcricao_nossa_nao_e_assinada_sozinha(self):
        self.media.publicada = False
        self._prep()
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-14", "--hooks", "x"])
        self.assertIn("warden captions review", erro)
        self.assertIsNone(self.media.cortes[0]["caption_srt"])

    def test_um_render_por_vez(self):
        """`WARDEN_RENDER_PARALELO` continua 1, e não é gosto: é memória.

        Paralelo ganha 9% (64s contra 70s) e o pico bate em 3035 MiB de um
        teto de 3072. Velocidade que o OOM killer interrompe não é velocidade.
        """
        self.assertEqual(int(os.environ.get("WARDEN_RENDER_PARALELO") or 1), 1)

    def test_as_linhas_media_saem_todas_no_bloco_do_fim(self):
        self._prep()
        code, saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30,60-80",
             "--hooks", "um|dois"])
        self.assertEqual(code, 0, erro)
        self.assertIn("END YOUR TURN NOW", erro)
        bloco = erro.split("END YOUR TURN NOW", 1)[1]
        self.assertEqual(bloco.count("MEDIA:"), 2, bloco)
        self.assertIn("Corte 1: <caption>", bloco)
        self.assertIn("Corte 2: <caption>", bloco)
        self.assertIn("no tool call after it", erro)
        # e o stdout continua com um MEDIA: por clipe, do `deliver`
        self.assertEqual(saida.count("MEDIA:"), 2)

    def test_um_contact_sheet_para_o_lote_inteiro(self):
        """Uma conferência visual por lote: cada `vision_analyze` custa ~14s.

        Os mosaicos por clipe continuam todos lá -- o `deliver` não entrega
        sem o seu -- e este é o de cima deles, para que olhar dois clipes não
        custe dois olhares.
        """
        self._prep()
        _code, saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30,60-80",
             "--hooks", "um|dois"])
        folhas = [l for l in (saida + erro).splitlines() if l.startswith("SHEET:")]
        combinado = os.path.join(warden._dir_do_lote(), "lote-contato.jpg")
        self.assertTrue(os.path.isfile(combinado), erro)
        self.assertTrue(any(combinado in l for l in folhas),
                        "o mosaico do lote não foi impresso")
        self.assertIn("one image for the whole batch", erro)
        # ele é a soma dos dois, não um deles
        from PIL import Image
        with Image.open(combinado) as im:
            self.assertEqual(im.size, (60, 80))

    def test_a_campanha_vence_o_padrao_no_render(self):
        cid = self._campanha("render-apertada", duration_min_s=25,
                             duration_max_s=40, audio="forbidden")
        self._prep("--campaign", cid)
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x",
             "--campaign", cid])
        self.assertIn("duração: 25s", erro)
        self.assertIn("som: mudo", erro)
        self.assertEqual(self.media.cortes[0]["sound"], "platform")
        self.assertEqual(self.media.cortes[0]["asked_s"], 25)

    def test_render_sem_campanha_roda_igual(self):
        self._prep()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        self.assertEqual(self.media.cortes[0]["sound"], "embedded")


class MaisDeTresClipesVaoParaOFundo(_ComLoteFalso):
    """Acima de três, os três primeiros saem agora e o resto acorda o agente.

    Não é limite de capacidade, é limite de silêncio: um render mede ~64s, e o
    quarto clipe empurra a primeira entrega para além de quatro minutos com
    nada na tela.
    """

    def test_cinco_pedidos_entregam_tres_e_continuam_dois(self):
        self._roda(["lote", "prep", URL, "--n", "5"])
        code, saida, erro = self._roda(
            ["lote", "render", URL,
             "--windows", "10-30,60-80,120-140,180-200,240-260",
             "--hooks", "a|b|c|d|e"])
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), warden.LOTE_INLINE)
        bloco = erro.split("END YOUR TURN NOW", 1)[1]
        self.assertEqual(bloco.count("MEDIA:"), warden.LOTE_INLINE)
        # e a saída DIZ o que ficou para depois, com nome
        self.assertIn("still rendering in the background", erro)
        self.assertIn("corte-04.mp4", erro)
        self.assertIn("corte-05.mp4", erro)

    def test_o_resto_vira_um_plano_em_disco_e_um_processo_solto(self):
        self._roda(["lote", "prep", URL, "--n", "5"])
        self._roda(["lote", "render", URL,
                    "--windows", "10-30,60-80,120-140,180-200,240-260",
                    "--hooks", "a|b|c|d|e"])
        resto = os.path.join(warden._dir_do_lote(), "lote-resto.json")
        self.assertTrue(os.path.isfile(resto))
        with open(resto, encoding="utf-8") as fh:
            plano = json.load(fh)
        self.assertEqual([c["out"] for c in plano["clips"]],
                         ["corte-04.mp4", "corte-05.mp4"])
        self.assertEqual(len(self.spawnados), 1)
        self.assertIn("--plan", self.spawnados[0])
        self.assertIn(resto, self.spawnados[0])

    def test_tres_ou_menos_nao_vao_para_o_fundo(self):
        self._roda(["lote", "prep", URL, "--n", "3"])
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30,60-80,120-140",
             "--hooks", "a|b|c"])
        self.assertEqual(len(self.media.cortes), 3)
        self.assertEqual(self.spawnados, [])
        self.assertNotIn("still rendering in the background", erro)

    def test_se_o_processo_de_fundo_nao_sobe_tudo_renderiza_aqui(self):
        """Entregar menos do que foi pedido porque um Popen falhou seria a
        falta de 14/09 com outra desculpa."""
        def explode(cmd, **kw):
            raise OSError("no fork for you")
        subprocess.Popen = explode
        self._roda(["lote", "prep", URL, "--n", "5"])
        code, _saida, erro = self._roda(
            ["lote", "render", URL,
             "--windows", "10-30,60-80,120-140,180-200,240-260",
             "--hooks", "a|b|c|d|e"])
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 5)
        self.assertIn("could not start the background render", erro)


class UmLinkSoltoNaoPrecisaDeCampanha(_ComLoteFalso):
    """A decisão do dono de 15/09/2026, na porta de cada comando.

    Quem manda o link está afirmando que pode usar o material. O agente nunca
    pede licença, nunca pergunta sobre direitos e nunca trava um link por não
    estar numa lista. O que NÃO muda: as regras de uma campanha continuam
    valendo em toda checagem onde há campanha.
    """

    def test_cut_sem_campanha_renderiza_e_entrega(self):
        fonte = os.path.join(self.dir, "src.mp4")
        with open(fonte, "wb") as fh:
            fh.write(b"\x00" * 32)
        code, saida, erro = self._roda(
            ["cut", fonte, "--start", "0", "--end", "20", "--seconds", "20",
             "--out", "solto.mp4"])
        self.assertEqual(code, 0, erro)
        self.assertIn("MEDIA:", saida)

    def test_regras_em_branco_nao_reprovam_e_nao_aprovam(self):
        """Campo nulo nunca foi "pode": ele é "ninguém checou", e sai dito."""
        vazio = warden.regras_de(None)
        achados = warden.check(vazio, warden.probe("x"), None, [])
        self.assertEqual(warden.verdict(achados), warden.OK)
        texto = warden.render(achados, vazio, warden.probe("x"))
        self.assertIn("MEASURED, NOT APPROVED", texto)
        self.assertNotIn("Nothing blocks this clip", texto)

    def test_regras_em_branco_nao_carregam_o_texto_de_exemplo_do_template(self):
        """"Campaign as the brief names it" é um exemplo, e exemplo na tela lê
        como fato."""
        vazio = warden.regras_de(None)
        self.assertIsNone(vazio["id"])
        self.assertIsNone(vazio["name"])
        self.assertIn("(no campaign)",
                      warden.render([], vazio, warden.probe("x")))

    def test_check_sem_campanha_mede_e_nao_diz_que_passa(self):
        """A corrente que não fechava: a persona manda citar número do
        `warden`, a tabela de provas manda provar duração com `warden check`, e
        a skill PROÍBE inventar campanha para passar de um flag obrigatório. Com
        `--campaign` obrigatória não havia saída escrita nenhuma."""
        alvo = os.path.join(self.dir, "medir.mp4")
        with open(alvo, "wb") as fh:
            fh.write(b"\x00" * 32)
        code, saida, _erro = self._roda(["check", alvo])
        self.assertEqual(code, 0)
        self.assertIn("MEASURED, NOT APPROVED", saida)
        self.assertNotIn("Nothing blocks this clip", saida)
        # e o que é medível sem campanha continua medido, para poder ser citado
        self.assertIn("20.0s", saida)
        self.assertIn("1080x1920", saida)
        self.assertIn("30 fps", saida)
        self.assertIn("subtitle track", saida)

    def test_check_json_sem_campanha_diz_que_nao_houve_julgamento(self):
        alvo = os.path.join(self.dir, "medir.mp4")
        with open(alvo, "wb") as fh:
            fh.write(b"\x00" * 32)
        _code, saida, _erro = self._roda(["check", alvo, "--json"])
        dados = json.loads(saida)
        self.assertFalse(dados["judged"])
        self.assertIsNone(dados["campaign"])
        self.assertEqual(dados["media"]["duration_s"], 20.0)

    def test_check_com_campanha_continua_julgando(self):
        cid = self._campanha("check-real", duration_max_s=5)
        alvo = os.path.join(self.dir, "longo.mp4")
        with open(alvo, "wb") as fh:
            fh.write(b"\x00" * 32)
        code, saida, _erro = self._roda(["check", alvo, "--campaign", cid])
        self.assertEqual(code, 1)
        self.assertIn("over the 5s maximum", saida)
        self.assertNotIn("MEASURED, NOT APPROVED", saida)

    def test_package_sem_campanha_nao_inventa_hashtag(self):
        """Um campo adivinhado lê como regra e reprova quem obedeceu."""
        code, saida, erro = self._roda(["package", "--hook", "ele apostou tudo"])
        self.assertEqual(code, 0, erro)
        self.assertEqual(saida.strip(), "ele apostou tudo")
        self.assertNotIn("#", saida)
        self.assertNotIn("@", saida)
        self.assertIn("no campaign was given", erro)
        self.assertIn("no hashtag", erro)

    def test_package_com_campanha_continua_exigindo(self):
        cid = "package-real"
        r = R.blank()
        r.update({"id": cid, "name": cid, "schema": 1})
        r["caption"].update({"required_hashtags": ["#AD"],
                             "required_mentions": ["@marca"]})
        with open(warden.campaign_path(cid), "w") as fh:
            json.dump(r, fh)
        _code, saida, _erro = self._roda(
            ["package", "--campaign", cid, "--hook", "ele apostou tudo"])
        self.assertIn("#AD", saida)
        self.assertIn("@marca", saida)

    def test_authorize_sem_campanha_diz_sim_e_deixa_rastro(self):
        code, saida, _erro = self._roda(["authorize", URL])
        self.assertEqual(code, 0)
        self.assertIn("came from the person", saida)
        with open(warden.links_path(), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)[-1]["url"], URL)

    def test_link_e_sinonimo_de_trusted_no_archive(self):
        """`--link` é o nome que descreve o que isto é hoje; `--trusted` fica.

        Um flag que some é um comando que quebra na mão de quem já sabia
        usá-lo, e as skills e as conversas antigas escrevem `--trusted`.
        """
        for flag in ("--link", "--trusted"):
            with self.subTest(flag=flag):
                self.media.chamadas.clear()
                code, _saida, erro = self._roda(
                    ["archive", flag, URL, "--text-first"])
                self.assertEqual(code, 0, erro)
                pull = next(c for c in self.media.chamadas
                            if c[0] == "archive_trusted")
                self.assertEqual(pull[1], URL)
                self.assertEqual(pull[2], "text")


class PrefsNaoTemMaisOQuePerguntarAntesDeUmClipe(_ComLoteFalso):
    """Zero perguntas desnecessárias, medido: 10min34s de 26min22s."""

    def test_prefs_ask_group_edit_nao_lista_perguntas(self):
        _code, saida, _erro = self._roda(["prefs", "ask", "--group", "edit"])
        self.assertIn("nothing to ask before a clip: every edit preference "
                      "has a default", saida)
        for chave in ("sound", "target_s", "batch", "approval", "captions"):
            self.assertNotIn(f"{chave}:", saida)

    def test_o_grupo_search_continua_perguntando(self):
        """Lá a resposta muda QUAIS campanhas aparecem, e adivinhar o nicho de
        alguém é entregar a lista errada."""
        _code, saida, _erro = self._roda(["prefs", "ask", "--group", "search"])
        self.assertIn("region:", saida)
        self.assertIn("niches:", saida)

    def test_prefs_set_nao_devolve_uma_lista_de_perguntas(self):
        _code, saida, _erro = self._roda(
            ["prefs", "set", "--key", "hook", "--value", "pt"])
        self.assertEqual(saida.strip(), "hook = pt")
        self.assertNotIn("still to ask", saida)


if __name__ == "__main__":
    unittest.main()
