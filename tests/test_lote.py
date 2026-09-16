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
        # Dois links na MESMA instalação, que é o que a auditoria de instalação
        # nova mediu: um publica legenda e o outro não. Sem isto o dublê só
        # sabia representar um link de cada vez, e um lote que queima a legenda
        # do link anterior nos clipes deste não tinha como aparecer.
        self.publicada_por_url = {}
        self.segmentos_por_url = {}
        # O que o whisper ouve DENTRO de um arquivo de janela, no relógio dele.
        self.segmentos_de_janela = None
        # A LÍNGUA da fonte. Um campo e não uma constante porque o padrão do
        # hook passou a segui-la em 15/09/2026: um dublê que só sabe falar
        # português não consegue mostrar o lote que voltava vazio quando a
        # legenda descia em inglês (2 de 6 rodadas do mesmo link, medido).
        self.lingua = "pt"
        self.lingua_por_url = {}
        # A língua foi MEDIDA, ou saiu de desempate por ordem de preferência?
        # Um dublê que sempre diz "medi" não consegue mostrar o defeito de
        # 15/09/2026: vídeo em inglês, legenda `pt-br` auto-traduzida
        # escolhida, e a ferramenta afirmando que português era a língua do
        # vídeo. `source_language` é a língua do VÍDEO quando ela é conhecida,
        # que é como se descobre que a legenda escolhida é tradução.
        self.lingua_medida = True
        self.lingua_do_video = None
        self.chamadas = []
        self.cortes = []
        self._url_do_arquivo = {}

    # -------------------------------------------------------------- prep
    def archive_trusted(self, url, out_dir, entries, mode="video"):
        self.chamadas.append(("archive_trusted", url, mode, list(entries)))
        os.makedirs(out_dir, exist_ok=True)
        caminho = os.path.join(out_dir, "fonte.pt.srt")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:10,000 --> 00:00:14,000\nfala\n\n")
        self._url_do_arquivo[caminho] = url
        return caminho

    def transcribe(self, path, **kw):
        self.chamadas.append(("transcribe", path, kw))
        if os.path.basename(path).startswith("janela-"):
            # O caminho de janela: outro arquivo, outro relógio e outro modelo.
            return {"source": "faster-whisper small (only this window)",
                    "language": self.lingua,
                    "language_measured": self.lingua_medida,
                    "segments": [dict(s) for s in (self.segmentos_de_janela or [])]}
        url = self._url_do_arquivo.get(path)
        publicada = self.publicada_por_url.get(url, self.publicada)
        lingua = self.lingua_por_url.get(url, self.lingua)
        fonte = (f"published subtitles ({lingua})" if publicada
                 else "whisper base")
        segmentos = self.segmentos_por_url.get(url, SEGMENTOS)
        return {"source": fonte, "language": lingua,
                "language_measured": self.lingua_medida,
                "source_language": self.lingua_do_video,
                "segments": [dict(s) for s in segmentos]}

    def to_srt(self, segments):
        # O SRT de verdade, do módulo de verdade: o que este dublê simula é a
        # REDE, e escrever um timestamp não é rede. Um formatador caseiro aqui
        # escrevia `00:00:300` para 300s e a legenda lida de volta não era a
        # mesma que foi escrita.
        import warden_media
        return warden_media.to_srt(segments)

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
        # Lê o ARQUIVO, e não uma lista fixa. Devolver sempre `SEGMENTOS`
        # respondia a mesma coisa para o SRT de qualquer link -- que é
        # exatamente o defeito sob teste -- e nenhuma asserção conseguia
        # distinguir a legenda de um link da do outro.
        import warden_media
        return warden_media._read_srt(caminho)

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


def _nome(url, i):
    """O nome que o lote dá ao clipe `i` deste link.

    Escrito como uma função e não como a string `corte-01.mp4` porque a string
    fixa É o defeito: dois links diferentes escreviam nos mesmos caminhos, e o
    segundo pedido apagava os clipes do primeiro. O nome carrega a marca da
    fonte -- a mesma conta que nomeia os arquivos de janela -- e um teste que
    soletrasse o nome à mão deixaria de valer no dia em que a marca entrasse.
    """
    return f"corte-{warden._marca_da_fonte(url)}-{i:02d}.mp4"


URL = "https://www.youtube.com/watch?v=abcdefghijk"
# O segundo link da MESMA instalação. Ele existe porque o defeito só aparece
# com dois: o lote de um link queimava a legenda que sobrou do outro.
URL_B = "https://www.youtube.com/watch?v=zyxwvutsrqp"

PALAVRAS_DE_A = [
    {"start": 10.0, "end": 14.0, "text": "esta fala pertence ao link A"},
    {"start": 20.0, "end": 24.0, "text": "e esta tambem é do link A"},
]


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
        # O idioma deixou de ser fixo em 15/09/2026: ele é o da fonte, e o
        # dublê publica legenda em `pt`, então é `pt` que tem de aparecer --
        # vindo da fonte, e não de uma constante. Ver `LinguaDoHook` abaixo.
        self.assertIn("hook e legenda: pt (padrão: a língua da fonte", bloco)
        self.assertIn("legenda: queimar em pt", bloco)
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
        ficha = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            dados = json.load(fh)
        self.assertTrue(dados["published"])

    def test_transcricao_nossa_nao_e_marcada_como_publicada(self):
        self.media.publicada = False
        self._roda(["lote", "prep", URL])
        ficha = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
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

    def test_cada_janela_leva_o_SEU_gancho(self):
        """O pareamento gancho-janela, e não a ordem em que os renders acabam.

        Este teste afirmava `[c["hook"] for c in cortes] == ["primeiro",
        "segundo"]`, e isso era uma asserção sobre a ordem de EXECUÇÃO: o
        dublê anota cada corte na hora em que ele acontece. Com
        `WARDEN_RENDER_PARALELO=2` -- que é o padrão do container desde
        16/09/2026 -- os dois ffmpeg correm juntos e qualquer um dos dois pode
        terminar primeiro, então o teste falhava em cerca de duas de cada cinco
        rodadas, na árvore limpa, sem nada de errado no produto.

        A ordem que IMPORTA continua garantida e não é esta: `lote render`
        percorre os futuros na ordem de submissão, então as linhas `MEDIA:` e
        o "primeiro corte / segundo corte" saem na ordem das janelas
        independentemente de quem acabou antes. O que este teste tem de pegar é
        o gancho do clipe 1 aparecendo no clipe 2 -- e isso se vê pareando cada
        gancho com a janela dele, que é o que ele faz agora.
        """
        self._prep()
        self._roda(["lote", "render", URL, "--windows", "10-30,60-80",
                    "--hooks", "primeiro|segundo"])
        # O par estável é o SUFIXO NUMÉRICO do arquivo de saída, que o lote
        # atribui pela ordem das janelas antes de submeter qualquer render. O
        # nome inteiro carrega um hash da fonte no meio (`corte-<hash>-01.mp4`)
        # e afirmar o nome cru amarraria este teste a esse hash.
        por_indice = {os.path.splitext(os.path.basename(c["out"]))[0][-2:]: c["hook"]
                      for c in self.media.cortes}
        self.assertEqual(por_indice, {"01": "primeiro", "02": "segundo"})

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
        srt = os.path.join(warden._dir_do_lote(URL), "lote.srt")
        ok, _porque = S.approval_state(srt, start=10.0, end=14.0)
        self.assertTrue(ok)

    def test_uma_linha_suspeita_avisa_e_a_legenda_queima_assim_mesmo(self):
        """O aviso, e o clipe COM legenda. Medido em 16/09/2026.

        Uma linha com número é o que uma transcrição mais erra, e até aqui ela
        calava o clipe inteiro até alguém repeti-la em `--keep`. No primeiro
        lote real com legenda publicada em português a proteção disparou em
        2 clipes de 2, duas vezes -- que é a reclamação do dono, "veio sem
        legenda também". Uma proteção que dispara em todo clipe desliga o
        produto em vez de protegê-lo.

        Então: a legenda queima, o aviso nomeia a linha e o `--keep` exato
        continua na saída, agora querendo dizer "já li esta, pare de avisar".
        """
        SEGMENTOS.append({"start": 300.0, "end": 304.0,
                          "text": "foram 90 mil reais naquele dia"})
        self.addCleanup(SEGMENTOS.pop)
        self._prep()
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "300-304", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        self.assertIn('--keep "foram 90 mil reais naquele dia"', erro)
        self.assertIn("CAPTIONS BURNED", erro)
        # e o clipe saiu COM legenda: é a única coisa que o dono espera
        self.assertTrue(self.media.cortes[0]["caption_srt"])

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

    def test_o_srt_da_fonte_inteira_nao_e_o_que_queima(self):
        """O texto do `prep` é do modelo BARATO sobre a fonte inteira.

        Ele serve para escolher o momento, e é o próprio `_lote_prep` que diz
        isso. O que queima é a transcrição da JANELA, feita de novo com o
        modelo bom -- nunca o SRT da varredura.
        """
        self.media.publicada = False
        self.media.segmentos_de_janela = [
            {"start": 2.0, "end": 6.0, "text": "a palavra desta janela"}]
        self._prep()
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        queimado = self.media.cortes[0]["caption_srt"]
        self.assertTrue(queimado, erro)
        self.assertNotEqual(
            queimado, os.path.join(warden._dir_do_lote(URL), "lote.srt"))
        with open(queimado, encoding="utf-8") as fh:
            self.assertIn("a palavra desta janela", fh.read())

    def test_dois_renders_por_vez_e_o_padrao_do_compose(self):
        """`WARDEN_RENDER_PARALELO=2`, e agora ele é medido.

        A versão anterior deste teste exigia 1 e citava "paralelo ganha 9%
        (64s contra 70s) e o pico bate em 3035 MiB de um teto de 3072". Nenhum
        dos dois números era de dois renders simultâneos -- o 3035 MiB era um
        render COM transcrição junto, que é outra coisa.

        Medido de verdade em 16/09/2026, o mesmo lote de dois clipes de 20s
        neste container:

          sequencial   105s    pico 1815 MiB
          paralelo=2    54,7s  pico 1584 MiB

        Metade do tempo, e o pico ABAIXO do sequencial. O teto do container
        subiu de 3g para 4g junto, por folga e não por necessidade.

        O que este teste protege é o valor chegar ao processo: ele vem do
        `compose.yml`, e um agente que renderiza em fila porque a variável se
        perdeu é o defeito voltando calado.
        """
        self.assertEqual(int(os.environ.get("WARDEN_RENDER_PARALELO") or 1), 2,
                         "WARDEN_RENDER_PARALELO não chegou ao processo; "
                         "confira o bloco `environment:` do compose.yml")

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
        combinado = os.path.join(warden._dir_do_lote(URL), "lote-contato.jpg")
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


class OLoteDeUmLinkNaoQueimaALegendaDeOutro(_ComLoteFalso):
    """O diretório do lote é por LINK, e a ficha diz de qual link ela é.

    Achado numa auditoria adversarial de instalação nova. O diretório do lote
    era FIXO -- `footage/lote`, o mesmo para todo link -- e a ficha
    `lote.legenda.json` só é reescrita quando o `prep` produziu SRT. Então:

      1. `lote prep <A>`: A publica legenda e a ficha fica com as palavras de A;
      2. `lote prep <B>`: B não publica legenda e não tem fala que o modelo
         barato ouça na fonte inteira, então nada é reescrito;
      3. `lote render <B>`: lê a ficha que sobrou de A e queima a legenda de A
         nos clipes de B.

    Ninguém percebe. O clipe sai bonito, com as palavras erradas -- que é a
    classe de defeito que este projeto existe para impedir.
    """

    def _dois_links(self):
        self.media.segmentos_por_url[URL] = PALAVRAS_DE_A
        self.media.publicada_por_url[URL_B] = False
        # B é um clipe musical sem fala que o modelo barato ouça na fonte
        # inteira: é o caso em que o `prep` não produz SRT nenhum e a ficha
        # anterior sobrevive.
        self.media.segmentos_por_url[URL_B] = []

    def test_os_clipes_de_B_nao_carregam_a_legenda_de_A(self):
        self._dois_links()
        self.media.segmentos_de_janela = []
        self._roda(["lote", "prep", URL])
        self._roda(["lote", "prep", URL_B])
        code, _saida, erro = self._roda(
            ["lote", "render", URL_B, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        queimado = self.media.cortes[0]["caption_srt"]
        if queimado and os.path.isfile(queimado):
            with open(queimado, encoding="utf-8") as fh:
                texto = fh.read()
            self.assertNotIn("link A", texto,
                             "os clipes de B saíram com a legenda de A")
        self.assertIsNone(queimado,
                          "B não tem palavra nenhuma, então nada pode queimar")

    def test_cada_link_tem_o_seu_diretorio_com_a_marca_do_arquivo_de_janela(self):
        """E a marca é a MESMA que `janela-<marca>-...` carrega.

        Duas contas diferentes para "que link é este" são duas respostas
        diferentes no dia em que uma delas mudar.
        """
        import hashlib
        self.assertNotEqual(warden._dir_do_lote(URL), warden._dir_do_lote(URL_B))
        marca = hashlib.sha256(URL.encode()).hexdigest()[:10]
        self.assertTrue(warden._dir_do_lote(URL).endswith(marca),
                        warden._dir_do_lote(URL))

    def test_o_prep_de_B_nao_apaga_a_ficha_de_A(self):
        self._dois_links()
        self._roda(["lote", "prep", URL])
        self._roda(["lote", "prep", URL_B])
        ficha_a = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
        with open(ficha_a, encoding="utf-8") as fh:
            dados = json.load(fh)
        self.assertTrue(dados["published"])
        self.assertEqual(dados["url"], URL)
        self.assertFalse(os.path.isfile(
            os.path.join(warden._dir_do_lote(URL_B), "lote.legenda.json")))

    def test_a_ficha_de_outro_link_e_recusada_nomeando_os_dois(self):
        """Cinto e suspensório: o hash sozinho não protege de mudarem o esquema.

        Se um dia o diretório voltar a ser um só, a ficha ainda diz de quem ela
        é -- e o render recusa em vez de queimar.
        """
        self._dois_links()
        self._roda(["lote", "prep", URL])
        destino = warden._dir_do_lote(URL_B)
        os.makedirs(destino, exist_ok=True)
        shutil.copy(os.path.join(warden._dir_do_lote(URL), "lote.legenda.json"),
                    os.path.join(destino, "lote.legenda.json"))
        code, _saida, erro = self._roda(
            ["lote", "render", URL_B, "--windows", "10-30", "--hooks", "x"])
        self.assertNotEqual(code, 0)
        self.assertIn(URL, erro)
        self.assertIn(URL_B, erro)
        self.assertEqual(self.media.cortes, [],
                         "nenhum clipe pode sair de uma ficha de outro link")


class FonteSemLegendaPublicadaNaoEntregaClipeMudo(_ComLoteFalso):
    """A legenda queimada é o produto anunciado, e o padrão é `captions: yes`.

    Segundo achado da mesma auditoria. Quando a fonte não publicava legenda, o
    `render` não assinava nada, `queimar` ficava vazio e TODOS os clipes saíam
    sem uma palavra na tela -- em silêncio. O portão de `deliver` não pega
    esse caso: ele reprova "legenda pedida e nenhuma queimada", mas
    `asked_for_captions` é `bool(caption_srt)`, então um clipe cortado sem SRT
    nunca pediu legenda e nada falta.
    """

    def _sem_legenda_publicada(self, texto_da_janela):
        self.media.publicada = False
        self.media.segmentos_de_janela = [
            {"start": 2.0, "end": 6.0, "text": texto_da_janela}]

    def test_a_transcricao_da_janela_e_queimada(self):
        self._sem_legenda_publicada("as palavras desta janela e nao de outra")
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        queimado = self.media.cortes[0]["caption_srt"]
        self.assertTrue(queimado, "o clipe saiu mudo numa fonte sem legenda")
        with open(queimado, encoding="utf-8") as fh:
            texto = fh.read()
        self.assertIn("as palavras desta janela e nao de outra", texto)
        # e no relógio da FONTE: o arquivo de janela começa em 8s (10 menos o
        # IN_POINT de 2s), então 2s dentro dele são 10s da fonte. Sem essa
        # soma a legenda sai de outro trecho, que é o defeito que o
        # `.origem.json` existe para impedir.
        self.assertIn("00:00:10,000", texto)

    def test_e_a_transcricao_e_a_da_janela_com_o_modelo_bom(self):
        self._sem_legenda_publicada("as palavras desta janela")
        self._roda(["lote", "prep", URL])
        self._roda(["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        janela = [c for c in self.media.chamadas
                  if c[0] == "transcribe"
                  and os.path.basename(c[1]).startswith("janela-")]
        self.assertTrue(janela, "a janela nunca foi transcrita")
        self.assertEqual(janela[0][2].get("proposito"), "janela")

    def test_linha_suspeita_da_transcricao_avisa_e_queima(self):
        """No caminho da transcrição a decisão pesa mais, não menos.

        "Em 1826" e "jokovic jokovic" foram para a tela porque uma linha foi
        marcada como provavelmente errada e assinada no mesmo fôlego. A
        resposta a isso era calar o clipe, e desde 16/09/2026 não é: se uma
        linha suspeita calasse o corte, TODA fonte sem legenda publicada
        entregaria clipe mudo -- e o dono espera exatamente uma coisa de cada
        clipe, que é a legenda.

        O aviso continua nomeando a linha e o `--keep` exato, e continua
        chegando à saída em vez de morrer num stderr no meio do render.
        """
        self._sem_legenda_publicada("foram 90 mil reais naquele dia")
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        self.assertTrue(self.media.cortes[0]["caption_srt"])
        self.assertIn('--keep "foram 90 mil reais naquele dia"', erro)
        self.assertIn("CAPTIONS BURNED", erro)
        self.assertNotIn("NO CAPTIONS", erro)

    def test_com_keep_a_linha_suspeita_da_janela_queima(self):
        self._sem_legenda_publicada("foram 90 mil reais naquele dia")
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x",
             "--keep", "foram 90 mil reais naquele dia"])
        self.assertEqual(code, 0, erro)
        self.assertTrue(self.media.cortes[0]["caption_srt"])
        self.assertNotIn("NO CAPTIONS", erro)

    def test_janela_sem_fala_nenhuma_sai_dita_na_conta_final(self):
        self._sem_legenda_publicada("")
        self.media.segmentos_de_janela = []
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        conta = erro.split("cleared for delivery", 1)[1]
        self.assertIn(f"NO CAPTIONS: {_nome(URL, 1)}", conta)
        self.assertIn("heard no speech", conta)

    def test_ficha_ausente_nao_e_erro(self):
        """Fonte sem legenda publicada é o caso NORMAL, não uma falha.

        Sem `prep` nenhum não há ficha, e isso não pode virar `die`: é
        exatamente por aí que se cai no caminho da transcrição da janela.
        """
        self._sem_legenda_publicada("a janela falou sozinha")
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        self.assertFalse(os.path.isfile(os.path.join(
            warden._dir_do_lote(URL), "lote.legenda.json")))
        self.assertTrue(self.media.cortes[0]["caption_srt"])

    def test_com_captions_desligado_nada_e_queimado_e_nada_e_cobrado(self):
        """`captions: no` é uma decisão guardada, não um clipe mudo por acidente."""
        self._roda(["prefs", "set", "--key", "captions", "--value", "no"])
        self._sem_legenda_publicada("as palavras desta janela")
        self._roda(["lote", "prep", URL])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30", "--hooks", "x"])
        self.assertEqual(code, 0, erro)
        self.assertIsNone(self.media.cortes[0]["caption_srt"])
        self.assertNotIn("NO CAPTIONS", erro)


class MaisDeTresClipesNaoSomemNoFundo(_ComLoteFalso):
    """Acima de três, os três primeiros saem agora e o resto é DITO em voz alta.

    Não é limite de capacidade, é limite de silêncio: um render mede ~64s, e o
    quarto clipe empurra a primeira entrega para além de quatro minutos com
    nada na tela.

    O que mudou, e é o conserto: até aqui o resto ia para um
    `subprocess.Popen(start_new_session=True)` e a saída dizia "their finishing
    is what wakes you for them". Nada acordava ninguém -- o despertar deste
    runtime é a ferramenta de fundo DO AGENTE, com aviso de conclusão, e um
    processo solto por este arquivo não passa por ela. Pedir 10 clipes
    entregava 3, e os outros 7 ficavam num log que ninguém lê.

    Então esta classe cobra o contrário do que cobrava: nenhum processo solto,
    e uma conta final que nomeia o que faltou E o comando exato que o traz.
    """

    _CINCO = "10-30,60-80,120-140,180-200,240-260"

    def test_cinco_pedidos_entregam_tres_e_dizem_quais_dois_faltam(self):
        self._roda(["lote", "prep", URL, "--n", "5"])
        code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", self._CINCO,
             "--hooks", "a|b|c|d|e"])
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), warden.LOTE_INLINE)
        bloco = erro.split("END YOUR TURN NOW", 1)[1]
        self.assertEqual(bloco.count("MEDIA:"), warden.LOTE_INLINE)
        # e a saída DIZ o que ficou para depois, com nome
        self.assertIn(_nome(URL, 4), erro)
        self.assertIn(_nome(URL, 5), erro)

    def test_a_conta_final_nao_promete_despertar_nenhum(self):
        """A promessa que o runtime não cumpre saiu, e saiu do texto todo."""
        self._roda(["lote", "prep", URL, "--n", "5"])
        _code, saida, erro = self._roda(
            ["lote", "render", URL, "--windows", self._CINCO,
             "--hooks", "a|b|c|d|e"])
        tudo = saida + erro
        self.assertNotIn("still rendering in the background", tudo)
        self.assertNotIn("what wakes you for them", tudo)
        self.assertIn("NOTHING WILL WAKE YOU", tudo)

    def test_a_conta_final_traz_o_comando_exato_que_busca_o_resto(self):
        """Nomear o que faltou sem dizer como buscá-lo é a mesma perda com
        outro texto: o modelo lê "faltam dois" e não tem o que rodar."""
        self._roda(["lote", "prep", URL, "--n", "5"])
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", self._CINCO,
             "--hooks", "a|b|c|d|e"])
        resto = os.path.join(warden._dir_do_lote(URL), "lote-resto.json")
        conta = erro.split("cleared for delivery", 1)[1]
        self.assertIn(f"warden cut --plan {resto}", conta)
        self.assertIn("next turn", conta)

    def test_nenhum_processo_solto_sobe(self):
        self._roda(["lote", "prep", URL, "--n", "5"])
        self._roda(["lote", "render", URL, "--windows", self._CINCO,
                    "--hooks", "a|b|c|d|e"])
        self.assertEqual(self.spawnados, [])

    def test_o_resto_vira_um_plano_em_disco_pronto_para_rodar(self):
        self._roda(["lote", "prep", URL, "--n", "5"])
        self._roda(["lote", "render", URL, "--windows", self._CINCO,
                    "--hooks", "a|b|c|d|e"])
        resto = os.path.join(warden._dir_do_lote(URL), "lote-resto.json")
        self.assertTrue(os.path.isfile(resto))
        with open(resto, encoding="utf-8") as fh:
            plano = json.load(fh)
        self.assertEqual([c["out"] for c in plano["clips"]],
                         [_nome(URL, 4), _nome(URL, 5)])

    def test_tres_ou_menos_nao_deixam_nada_para_tras(self):
        self._roda(["lote", "prep", URL, "--n", "3"])
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30,60-80,120-140",
             "--hooks", "a|b|c"])
        self.assertEqual(len(self.media.cortes), 3)
        self.assertEqual(self.spawnados, [])
        self.assertNotIn("NOTHING WILL WAKE YOU", erro)
        self.assertNotIn("did NOT render in this turn", erro)

    def test_se_o_plano_do_resto_nao_for_escrito_tudo_renderiza_aqui(self):
        """Entregar menos do que foi pedido porque um arquivo não foi escrito
        seria a falta de 14/09 com outra desculpa."""
        import builtins
        verdadeiro = builtins.open

        def recusa(caminho, *a, **kw):
            if str(caminho).endswith("lote-resto.json"):
                raise OSError("no room for you")
            return verdadeiro(caminho, *a, **kw)

        self._roda(["lote", "prep", URL, "--n", "5"])
        builtins.open = recusa
        try:
            code, _saida, erro = self._roda(
                ["lote", "render", URL, "--windows", self._CINCO,
                 "--hooks", "a|b|c|d|e"])
        finally:
            builtins.open = verdadeiro
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 5)
        self.assertIn("could not write the plan for the rest", erro)


class UmSegundoPedidoNaoApagaOsClipesDoPrimeiro(_ComLoteFalso):
    """O nome do arquivo de saída era global, e o lote seguinte o sobrescrevia.

    `corte-01.mp4` resolve sempre para o mesmo `clips_dir()`, que não é por
    pedido. Então o lote do link B escrevia por cima dos arquivos do link A --
    e a dívida de entrega de A, que guarda o CAMINHO, passava a apontar para o
    vídeo de B. Ninguém percebia: o caminho existia e o clipe era outro.
    """

    def test_dois_links_nao_dividem_um_caminho_de_saida(self):
        self._roda(["lote", "prep", URL])
        self._roda(["lote", "render", URL, "--windows", "10-30", "--hooks", "a"])
        de_a = [c["out"] for c in self.media.cortes]
        self._roda(["lote", "prep", URL_B])
        self._roda(["lote", "render", URL_B, "--windows", "10-30", "--hooks", "b"])
        de_b = [c["out"] for c in self.media.cortes[len(de_a):]]
        self.assertTrue(de_a and de_b)
        self.assertEqual(set(de_a) & set(de_b), set())

    def test_o_nome_carrega_a_marca_da_fonte_e_nao_outra_conta(self):
        """A mesma marca dos arquivos de janela, de propósito: duas contas para
        responder "que link é este" são duas respostas no dia em que uma mudar."""
        self._roda(["lote", "prep", URL])
        self._roda(["lote", "render", URL, "--windows", "10-30", "--hooks", "a"])
        self.assertEqual(os.path.basename(self.media.cortes[0]["out"]),
                         _nome(URL, 1))

    def test_o_mesmo_link_duas_vezes_nao_sobrescreve_o_arquivo_de_antes(self):
        self._roda(["lote", "prep", URL])
        self._roda(["lote", "render", URL, "--windows", "10-30", "--hooks", "a"])
        primeiro = self.media.cortes[0]["out"]
        with open(primeiro, "wb") as fh:          # o clipe que já está entregue
            fh.write(b"o primeiro")
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "300-320", "--hooks", "b"])
        segundo = self.media.cortes[-1]["out"]
        self.assertNotEqual(primeiro, segundo)
        self.assertIn("was NOT overwritten", erro)
        with open(primeiro, "rb") as fh:
            self.assertEqual(fh.read(), b"o primeiro")


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


class OIdiomaSegueAFonte(_ComLoteFalso):
    """A decisão do dono, 15/09/2026: "o idioma da legenda tem que ser o mesmo
    que a linguagem do vídeo disponibilizado".

    O padrão era `pt` FIXO, e o preço não era um clipe feio: era um lote vazio.
    `cut` RECUSA queimar quando o hook e a legenda estão em línguas diferentes
    -- regra certa, ela existe porque um hook em português sobre uma legenda em
    inglês foi entregue uma vez. Com o padrão fixo e uma fonte em inglês, o
    agente escrevia o gancho em português, a legenda descia em inglês, e a
    recusa pegava TODOS os clipes. Medido: o mesmo link trouxe legenda `en` em
    2 de 6 rodadas.
    """

    def test_o_padrao_deixou_de_ser_um_idioma_fixo(self):
        import warden_prefs as P
        self.assertEqual(P.DEFAULTS["hook"], P.HOOK_DA_FONTE)
        self.assertNotIn(P.DEFAULTS["hook"], ("pt", "en", "es"))
        # e continua sendo uma resposta possível da pergunta, senão
        # `prefs set --key hook --value source` seria recusado
        pergunta = next(q for q in P.QUESTIONS if q[0] == "hook")
        self.assertIn(P.HOOK_DA_FONTE, pergunta[2])

    def test_fonte_em_ingles_da_hook_e_legenda_em_ingles(self):
        self.media.lingua = "en"
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("LANG:en", saida)
        self.assertIn("hook e legenda: en", saida)
        self.assertIn("legenda: queimar em en", saida)
        self.assertIn("WRITE THE HOOKS IN EN", saida)
        # e o exemplo do próximo comando já vem na língua certa
        self.assertIn("<gancho em en>", saida)

    def test_fonte_em_portugues_da_hook_e_legenda_em_portugues(self):
        self.media.lingua = "pt"
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("LANG:pt", saida)
        self.assertIn("hook e legenda: pt", saida)
        self.assertIn("WRITE THE HOOKS IN PT", saida)

    def test_a_tag_regional_vira_a_raiz(self):
        """`en-US` contra `en` seria uma divergência inventada: o portão do
        `cut` compara os dois primeiros caracteres."""
        self.media.lingua = "pt-BR"
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("hook e legenda: pt (", saida)

    def test_a_preferencia_da_pessoa_vence_a_fonte(self):
        """"hook em português num vídeo em inglês" é escolha de quem pediu."""
        self.media.lingua = "en"
        self._roda(["prefs", "set", "--key", "hook", "--value", "pt"])
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("hook e legenda: pt (sua preferência guardada", saida)
        self.assertIn("vence a língua da fonte", saida)
        # o mesmo com a fonte em português e a pessoa pedindo inglês
        self._roda(["prefs", "set", "--key", "hook", "--value", "en"])
        self.media.lingua = "pt"
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("hook e legenda: en (sua preferência guardada", saida)

    def test_sem_lingua_lida_nada_e_chutado(self):
        """Chutar `pt` aqui é exatamente o que custava o lote."""
        tag, linha = warden.lingua_do_hook({}, None)
        self.assertIsNone(tag)
        self.assertIn("ainda não foi lida", linha)
        self.assertNotIn("pt", linha.split("--")[0])

    def test_none_continua_desligando_o_hook(self):
        tag, linha = warden.lingua_do_hook({"hook": "none"}, "en")
        self.assertEqual(tag, "none")
        self.assertIn("nenhum", linha)

    def test_a_lingua_desce_ate_o_cut(self):
        """Sem isto o `cut` não tem contra o que comparar o gancho."""
        self.media.lingua = "en"
        self._roda(["lote", "prep", URL])
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-14", "--hooks", "he bet it all"])
        self.assertEqual(self.media.cortes[0]["language"], "en")

    def test_o_render_repete_a_lingua_da_ficha_do_prep(self):
        """O `render` roda num processo novo: sem a ficha ele readivinharia."""
        self.media.lingua = "es"
        self._roda(["lote", "prep", URL])
        ficha = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["language"], "es")
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-14", "--hooks", "x"])
        self.assertIn("hook e legenda: es", erro)

    def test_a_transcricao_do_prep_ja_entrega_a_lingua(self):
        """Mesmo sem legenda PUBLICADA, o `prep` transcreve a fonte e a língua
        sai dali -- então o `render` não precisa readivinhar nada."""
        self.media.publicada = False
        self.media.lingua = "en"
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("LANG:en", saida)
        ficha = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["language"], "en")

    def test_sem_ficha_nenhuma_a_lingua_vem_da_transcricao_da_janela(self):
        """`render` sem `prep` antes: a língua só aparece depois de transcrever
        a janela, e o anúncio é REFEITO em voz alta em vez de deixar o gancho
        ser escrito no escuro.

        O anúncio de antes disse, corretamente, que a língua não tinha sido
        lida. Deixá-lo de pé depois de ela aparecer seria a ferramenta calando
        sobre a única coisa que o modelo precisa saber para escrever o gancho.
        """
        self.media.publicada = False
        self.media.lingua = "en"
        self.media.segmentos_de_janela = [
            {"start": 0.0, "end": 3.0, "text": "he bet everything on that game"}]
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-14", "--hooks", "x"])
        # o primeiro anúncio é honesto sobre não saber
        self.assertIn("ainda não foi lida", erro)
        # e o segundo diz o que mudou
        self.assertIn("the language came from transcribing the windows", erro)
        self.assertIn("it is EN", erro)
        self.assertIn("hook e legenda: en", erro)
        self.assertEqual(self.media.cortes[0]["language"], "en")


class ALinguaSoEAFIRMADAQUANDOFOIMEDIDA(_ComLoteFalso):
    """A instrução não muda; a justificativa sim.

    Medido em 15/09/2026, na imagem construída: o vídeo era em INGLÊS, a
    legenda escolhida foi a `pt-br` auto-traduzida, e o `prep` imprimiu
    "The caption burns in pt because that is the language of the video". A
    escolha errada foi consertada no `warden_media`; a FRASE continuava
    estruturalmente capaz de mentir, porque ela era afirmada
    incondicionalmente -- ela nunca perguntava se alguém tinha lido a língua.

    É a regra dura do projeto aplicada a uma frase em vez de a um número: o que
    não foi medido não é afirmado. Escrever o gancho na língua da legenda
    continua sendo a instrução nos dois casos, porque é ela que evita a recusa
    do `cut`.
    """

    def _prep(self):
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        return saida

    def test_medida_pode_dizer_que_e_a_lingua_do_video(self):
        self.media.lingua = "en"
        self.media.lingua_medida = True
        saida = self._prep()
        self.assertIn("that is the language of the video", saida)
        self.assertIn("lida do material", saida)
        self.assertIn("WRITE THE HOOKS IN EN", saida)

    def test_nao_medida_nao_afirma_nada_sobre_o_video(self):
        """O caso exato de 15/09: vídeo em inglês, legenda pt-br traduzida."""
        self.media.lingua = "pt-br"
        self.media.lingua_medida = False
        self.media.lingua_do_video = "en"
        saida = self._prep()
        # as duas afirmações que podiam ser falsas, ausentes
        self.assertNotIn("that is the language of the video", saida)
        self.assertNotIn("lida do material", saida)
        # e a ferramenta diz, em voz alta, o que NÃO apurou
        self.assertIn("NOT measured", saida)
        self.assertIn("NÃO foi medida", saida)
        self.assertIn("do NOT say this is the video's language", saida)
        # a INSTRUÇÃO continua a mesma, porque é ela que evita a recusa do cut
        self.assertIn("WRITE THE HOOKS IN PT", saida)
        self.assertIn("`cut` REFUSES", saida)
        # e, sabendo a língua do vídeo, nomeia a suspeita em vez de calar
        self.assertIn("SOURCE_LANG:en", saida)
        self.assertIn("auto-translation", saida)

    def test_a_frase_nao_pode_voltar_a_ser_incondicional(self):
        """O guarda: a mesma fonte, os dois valores de `language_measured`, e
        as saídas TÊM de divergir na justificativa e coincidir na instrução.

        Se alguém reescrever a linha afirmando sempre, este teste cai -- que é
        a única forma de a correção sobreviver a quem não leu o comentário.
        """
        self.media.lingua = "pt"
        self.media.lingua_medida = True
        com = self._prep()
        self.media.lingua_medida = False
        sem = self._prep()
        self.assertNotEqual(com, sem)
        self.assertIn("that is the language of the video", com)
        self.assertNotIn("that is the language of the video", sem)
        for saida in (com, sem):
            self.assertIn("WRITE THE HOOKS IN PT", saida)

    def test_a_ficha_guarda_se_foi_medida_e_o_render_repete_igual(self):
        """O `render` roda num processo novo. Uma ficha que só carrega a tag
        faz ele afirmar o que o `prep` teve o cuidado de não afirmar."""
        self.media.lingua = "pt-br"
        self.media.lingua_medida = False
        self.media.lingua_do_video = "en"
        self._prep()
        ficha = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            dados = json.load(fh)
        self.assertFalse(dados["language_measured"])
        self.assertEqual(dados["source_language"], "en")
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-14", "--hooks", "x"])
        self.assertIn("NÃO foi medida", erro)
        self.assertNotIn("lida do material", erro)
        # e o hook continua sendo pedido na língua da legenda
        self.assertEqual(self.media.cortes[0]["language"], "pt")

    def test_ficha_antiga_sem_o_campo_conta_como_nao_medida(self):
        """Não saber se foi medido não é a mesma coisa que ter medido."""
        self.media.lingua = "pt"
        self._prep()
        ficha = os.path.join(warden._dir_do_lote(URL), "lote.legenda.json")
        with open(ficha, encoding="utf-8") as fh:
            dados = json.load(fh)
        dados.pop("language_measured")
        with open(ficha, "w", encoding="utf-8") as fh:
            json.dump(dados, fh)
        _code, _saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-14", "--hooks", "x"])
        self.assertNotIn("lida do material", erro)
        self.assertIn("NÃO foi medida", erro)

    def test_lingua_do_hook_trata_none_como_nao_medida(self):
        _tag, com = warden.lingua_do_hook({}, "en", medida=True)
        _tag, sem = warden.lingua_do_hook({}, "en", medida=None)
        self.assertIn("lida do material", com)
        self.assertNotIn("lida do material", sem)
        self.assertIn("NÃO foi medida", sem)

    def test_a_preferencia_pinada_nao_fala_da_lingua_do_video(self):
        """Quando a pessoa pina um idioma, a fonte não entra na justificativa
        -- então não há nada a afirmar nem a desmentir sobre o vídeo."""
        for medida in (True, False):
            with self.subTest(medida=medida):
                _tag, linha = warden.lingua_do_hook({"hook": "pt"}, "en",
                                                    medida=medida)
                self.assertIn("sua preferência guardada", linha)
                self.assertNotIn("lida do material", linha)


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


SRT_BOM = ("1\n00:00:01,000 --> 00:00:04,000\nisso aqui é uma fala de verdade\n\n"
           "2\n00:00:04,500 --> 00:00:08,000\ne esta é a segunda\n\n")

# O conteúdo REAL de um `.aprovado`: é o que `warden captions review --approve`
# escreve, e é por ele parecer um arquivo legítimo -- existe, tem JSON dentro,
# tem o nome do SRT no caminho -- que o `cut` o aceitou sem piscar.
ASSINATURA = {"sha256": "0" * 64, "approved_at": "2026-09-15T00:00:00+00:00",
              "windows": [[181.0, 201.0]]}


class UmArquivoQueNaoELegendaNaoRenderizaUmClipeMUDO(_ComLoteFalso):
    """O defeito mais grave do primeiro teste real, 15/09/2026.

    A sequência do agente, reconstruída dos comandos no `state.db`:

      1. `cut ... --hook "..." --subtitles lote.srt ... --seconds`  REPROVADO
      2. igual                                                      REPROVADO
      3. trocou `--subtitles lote.srt` por `lote.srt.aprovado`      falhou
      4. TIROU o `--hook`                                           passou
      5. entregou dois clipes, dizendo que estavam prontos

    Os dois clipes saíram sem legenda e sem hook. O passo 3 é o que esta classe
    vigia: `lote.srt.aprovado` é o arquivo de ASSINATURA -- sha256 e janelas
    lidas -- e não tem uma cue dentro. O `cut` aceitou o caminho porque ele
    EXISTE, não achou legenda nenhuma e renderizou mudo, calado.

    O ponto não é o `.aprovado`. É que qualquer caminho existente servia.
    """

    def setUp(self):
        super().setUp()
        self.fonte = os.path.join(self.dir, "fonte.mp4")
        with open(self.fonte, "wb") as fh:
            fh.write(b"\x00" * 64)
        self.srt = os.path.join(self.dir, "lote.srt")
        with open(self.srt, "w", encoding="utf-8") as fh:
            fh.write(SRT_BOM)
        self.aprovado = warden.SUFIXO_ASSINATURA.join([self.srt, ""])
        with open(self.aprovado, "w", encoding="utf-8") as fh:
            json.dump(ASSINATURA, fh)

    def _corta(self, legenda):
        return self._roda(["cut", self.fonte, "--start", "0", "--end", "4",
                           "--any-length", "--crop", "center", "--out", "c.mp4",
                           "--subtitles", legenda])

    def test_o_arquivo_de_assinatura_e_recusado_e_a_recusa_nomeia_o_srt(self):
        code, saida, erro = self._corta(self.aprovado)
        self.assertEqual(code, 2, erro)
        # Nada foi renderizado: o portão é ANTES do render, que é o caro.
        self.assertEqual(self.media.cortes, [])
        self.assertEqual(saida, "")
        self.assertIn("APPROVAL SIGNATURE", erro)
        # E o nome do arquivo CERTO na tela, não só o do errado. Sem ele, quem
        # lê procura -- e quem procura tende a tirar o flag, que é o passo 4.
        self.assertIn(f"--subtitles {self.srt}", erro)

    def test_um_srt_vazio_tambem_e_recusado(self):
        vazio = os.path.join(self.dir, "vazio.srt")
        open(vazio, "w").close()
        code, saida, erro = self._corta(vazio)
        self.assertEqual(code, 2, erro)
        self.assertEqual(self.media.cortes, [])
        self.assertEqual(saida, "")
        self.assertIn("no readable cue", erro)

    def test_um_arquivo_que_nao_e_legenda_e_recusado(self):
        """Um JSON, um mp4, um texto solto: nenhum tem cue e todos passavam."""
        qualquer = os.path.join(self.dir, "transcript.json")
        with open(qualquer, "w", encoding="utf-8") as fh:
            json.dump({"segments": [{"start": 1, "text": "oi"}]}, fh)
        code, _saida, erro = self._corta(qualquer)
        self.assertEqual(code, 2, erro)
        self.assertEqual(self.media.cortes, [])
        self.assertIn("no readable cue", erro)

    def test_um_caminho_que_nao_existe_e_recusado(self):
        code, _saida, erro = self._corta(os.path.join(self.dir, "fantasma.srt"))
        self.assertEqual(code, 2, erro)
        self.assertEqual(self.media.cortes, [])
        self.assertIn("is not a file", erro)

    def test_o_srt_de_verdade_continua_passando(self):
        """O conserto não pode custar a legenda: o que TEM cue renderiza."""
        code, _saida, erro = self._corta(self.srt)
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 1)
        self.assertEqual(self.media.cortes[0]["caption_srt"], self.srt)

    def test_a_recusa_nao_ensina_a_tirar_o_flag(self):
        """A saída do passo 3 não pode empurrar para o passo 4."""
        _code, _saida, erro = self._corta(self.aprovado)
        self.assertIn("what this refusal exists to stop", erro)

    def test_o_plano_de_lote_recusa_antes_de_renderizar_N_clipes_mudos(self):
        """No lote o mesmo erro sai multiplicado pelo tamanho do lote."""
        plano = os.path.join(self.dir, "plano.json")
        with open(plano, "w", encoding="utf-8") as fh:
            json.dump({"source": self.fonte, "subtitles": self.aprovado,
                       "seconds": 4,
                       "clips": [{"out": "a.mp4", "start": 0, "end": 4},
                                 {"out": "b.mp4", "start": 5, "end": 9}]}, fh)
        code, saida, erro = self._roda(["cut", "--plan", plano])
        self.assertEqual(code, 2, erro)
        self.assertEqual(self.media.cortes, [])
        self.assertEqual(saida, "")
        self.assertIn("APPROVAL SIGNATURE", erro)

    def test_lote_render_com_o_aprovado_na_mao_tambem_morre(self):
        """`lote.srt` e `lote.srt.aprovado` ficam LADO A LADO no diretório do
        lote, com nomes que só diferem no sufixo. Foi ali que a troca nasceu."""
        code, saida, erro = self._roda(
            ["lote", "render", URL, "--windows", "10-30",
             "--hooks", "um gancho", "--subtitles", self.aprovado])
        self.assertEqual(code, 2, erro)
        self.assertEqual(saida, "")
        self.assertIn("APPROVAL SIGNATURE", erro)


class UmClipeQUEPASSOUNaoERenderizadoDeNovo(_ComLoteFalso):
    """Rerenderizar a mesma janela com os mesmos parâmetros não muda um quadro.

    Decisão do dono, 15/09/2026: o ciclo de re-render custou cerca de 2 minutos
    do primeiro teste real, e nenhum dos renders repetidos mudou um pixel.
    """

    def setUp(self):
        super().setUp()
        self.fonte = os.path.join(self.dir, "fonte.mp4")
        with open(self.fonte, "wb") as fh:
            fh.write(b"\x00" * 64)

    def _corta(self, out="c.mp4", **extra):
        argv = ["cut", self.fonte, "--start", "0", "--end", "4",
                "--any-length", "--crop", "center", "--out", out]
        for chave, valor in extra.items():
            argv += ["--" + chave.replace("_", "-"), str(valor)]
        return self._roda(argv)

    def test_o_segundo_corte_igual_nao_renderiza_e_entrega_o_mesmo_arquivo(self):
        code, primeira, erro = self._corta()
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 1)

        code, segunda, erro = self._corta(out="outro-nome.mp4")
        self.assertEqual(code, 0, erro)
        # NENHUM render novo: a lista de cortes do dublê não cresceu.
        self.assertEqual(len(self.media.cortes), 1)
        self.assertIn("already been rendered", erro)
        self.assertIn("CLEARED every gate", erro)
        # E o que sai é o clipe de antes, com a linha que o entrega.
        clipe = primeira.strip().splitlines()[0]
        self.assertIn(f"MEDIA:{clipe}", segunda)

    def test_o_nome_do_arquivo_nao_faz_dele_outro_clipe(self):
        """Foi com outro `--out` que o corte voltou a ser renderizado."""
        self._corta(out="a.mp4")
        _code, _saida, erro = self._corta(out="b.mp4")
        self.assertIn("already been rendered", erro)
        self.assertEqual(len(self.media.cortes), 1)

    def test_mudar_a_janela_renderiza_de_novo(self):
        """O portão não pode virar um bloqueio: outro corte é outro corte."""
        self._corta(out="a.mp4")
        code, _saida, erro = self._roda(
            ["cut", self.fonte, "--start", "10", "--end", "14",
             "--any-length", "--crop", "center", "--out", "b.mp4"])
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 2)
        self.assertNotIn("already been rendered", erro)

    def test_mudar_o_hook_renderiza_de_novo(self):
        self._corta(out="a.mp4")
        code, _saida, erro = self._corta(out="b.mp4", hook="outro gancho")
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 2)

    def test_um_corte_REPROVADO_pode_ser_tentado_de_novo(self):
        """Só o que PASSOU entra no livro. Consertar a campanha muda o veredito
        sem mudar um parâmetro do corte -- e aí ele tem de poder rodar."""
        warden.probe = lambda caminho: {
            "width": 1080, "height": 1920, "duration_s": 99.0, "fps": "30/1",
            "codec": "h264", "audio_codec": "aac", "subtitle_tracks": 0,
            "size_mb": 2.0}
        cid = self._campanha("curta", duration_max_s=5, width=1080, height=1920)
        code, _saida, _erro = self._roda(
            ["cut", self.fonte, "--start", "0", "--end", "4", "--any-length",
             "--crop", "center", "--out", "a.mp4", "--campaign", cid])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.media.cortes), 1)
        code, _saida, erro = self._roda(
            ["cut", self.fonte, "--start", "0", "--end", "4", "--any-length",
             "--crop", "center", "--out", "a.mp4", "--campaign", cid])
        self.assertEqual(len(self.media.cortes), 2, erro)
        self.assertNotIn("already been rendered", erro)

    def test_o_clipe_apagado_do_disco_renderiza_de_novo(self):
        code, saida, _erro = self._corta(out="a.mp4")
        self.assertEqual(code, 0)
        os.remove(saida.strip().splitlines()[0])
        code, _saida, erro = self._corta(out="a.mp4")
        self.assertEqual(code, 0, erro)
        self.assertEqual(len(self.media.cortes), 2)


class _MediaComTranscricaoReal(_MediaFalso):
    """A rede continua falsa; o DIGEST e os SINAIS são os de verdade.

    O que se mede aqui é o tamanho da saída do `prep`, e um dublê que devolve
    duas linhas de digest mede o dublê. As funções de `warden_media` que não
    tocam a rede -- `digest`, `analyze_signals` sem fonte, `text_signals` --
    entram inteiras.
    """

    def __init__(self, base, segmentos):
        super().__init__(base)
        self.segmentos_longos = segmentos

    def transcribe(self, path, **kw):
        self.chamadas.append(("transcribe", path, kw))
        return {"source": "published subtitles (pt)", "language": "pt",
                "language_measured": True, "source_language": "pt",
                "segments": [dict(s) for s in self.segmentos_longos]}

    def digest(self, segments, window=None):
        import warden_media
        self.chamadas.append(("digest", len(segments)))
        return warden_media.digest(segments, window=window)

    def analyze_signals(self, segments, source=None):
        import warden_media
        self.chamadas.append(("analyze_signals", source))
        # `source=None` de propósito: a envoltória de volume roda ffmpeg sobre o
        # arquivo, e aqui o arquivo é um SRT de mentira. Sem ela sobram os
        # sinais de TEXTO, que são determinísticos e são o que esta medição
        # precisa.
        return warden_media.analyze_signals(segments, source=None)


def _podcast(minutos=45, semente=7):
    """Um episódio realista: 45 minutos, fala de 2,5 a 5s, sinais espalhados."""
    import random
    random.seed(semente)
    falas = [
        "e aí você começa a entender como esse mercado funciona por dentro",
        "a gente passou seis meses tentando e não deu certo de jeito nenhum",
        "isso aí é o tipo de coisa que ninguém te conta quando você começa",
        "eu lembro que na época a gente nem tinha estrutura pra isso",
        "e o que aconteceu depois foi mais ou menos previsível",
        "a parte difícil não é montar, é manter aquilo de pé por dois anos",
        "eu falei pra ele que aquilo era o pior investimento possível",
        "cara, nossa, eu não acredito que ele fez isso na frente de todo mundo",
        "kkkkk isso é surreal demais, sério",
        "você acha mesmo que dá pra escalar isso sem quebrar tudo?",
        "é mentira, ele está completamente errado sobre esse ponto",
        "meu deus, foi o maior absurdo que eu já vi na minha vida",
        "a gente fechou o mês no positivo pela primeira vez desde o começo",
        "o time inteiro trabalhou de domingo a domingo naquele período",
        "e no fim do dia o que importa é quanto sobra no caixa",
    ]
    segmentos, t = [], 0.0
    while t < minutos * 60:
        dur = 2.5 + random.random() * 2.5
        segmentos.append({"start": round(t, 2), "end": round(t + dur, 2),
                          "text": random.choice(falas)})
        t += dur
    return segmentos


class OPrepCabeNUMALEITURA(_ComLoteFalso):
    """43 mil caracteres, e ~40s do modelo só lendo. Medido em 15/09/2026.

    Quase tudo era o digest linha a linha da fonte INTEIRA -- a transcrição de
    novo, com carimbo a cada 12 segundos. Para escolher uma janela o modelo não
    precisa da transcrição: precisa dos MOMENTOS e de contexto em volta de cada
    um. A transcrição inteira continua em disco e o caminho dela sai como
    `TRANSCRIPT:` na mesma saída, então nada se perde -- o que sumiu foi a
    repetição obrigatória.

    Meta do dono: abaixo de 15 mil caracteres.
    """

    def setUp(self):
        super().setUp()
        self.segmentos = _podcast()
        self.media = _MediaComTranscricaoReal(self.dir, self.segmentos)
        warden._media = lambda: self.media

    def test_a_saida_cabe_abaixo_de_quinze_mil_caracteres(self):
        code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertEqual(code, 0)
        self.assertLess(len(saida), warden.PREP_TETO_CHARS,
                        f"o prep voltou a {len(saida)} caracteres")

    def test_e_o_corte_e_grande_contra_o_que_saia_antes(self):
        """O "antes" é exatamente o que o código antigo imprimia: o digest da
        fonte inteira, linha a linha.

        Este episódio sintético dá 44.953 caracteres só de digest -- ou seja, é
        do tamanho do caso REAL que o dono mediu (43 mil). A asserção de baixo
        guarda isso: no dia em que a fonte deste teste encolher, ele deixa de
        provar qualquer coisa sobre o corte, e tem de falhar dizendo isso em vez
        de passar de graça.
        """
        import warden_media
        antes = len(warden_media.digest(self.segmentos))
        self.assertGreater(antes, 40000,
                           "a fonte deste teste deixou de ser realista: o caso "
                           "medido em 15/09 tinha 43 mil caracteres")
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertLess(len(saida), antes / 3)

    def test_os_sinais_continuam_na_saida(self):
        """Cortar não pode custar o que decide a janela."""
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("===== SIGNALS =====", saida)
        for tag in ("reaction", "conflict", "superlative"):
            self.assertIn(tag, saida)

    def test_os_trechos_com_sinal_trazem_contexto_em_volta(self):
        """Um sinal sem contexto é uma linha solta: o modelo não consegue dizer
        se aquilo fecha fora do episódio, que é a pergunta do clipe."""
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        digest = saida.split("===== DIGEST =====")[1].split("=====")[0]
        cabecas = [l for l in digest.splitlines() if l.startswith("--- ")]
        self.assertTrue(cabecas, digest[:400])
        # Cada bloco é uma FAIXA de tempo, não um instante.
        for linha in cabecas:
            faixa = linha.split()[1]
            de, ate = faixa.split("-")
            self.assertNotEqual(de, ate, linha)
        # E o corpo de cada bloco tem mais de uma linha de fala.
        corpo = [l for l in digest.splitlines()
                 if l.strip() and not l.startswith(("---", "#"))]
        self.assertGreater(len(corpo), len(cabecas))

    def test_a_saida_diz_que_nao_e_a_transcricao_inteira_e_onde_ela_esta(self):
        """Um recorte que se apresenta como o todo é pior que o todo: o modelo
        conclui que o resto do episódio não tem nada."""
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        self.assertIn("NOT the whole transcript", saida)
        self.assertIn("TRANSCRIPT:", saida)

    def test_uma_fonte_curta_continua_saindo_inteira(self):
        """O teto não pode cortar o que já cabia: um vídeo de dois minutos
        perde fala de graça se for recortado."""
        curto = [s for s in self.segmentos if s["end"] < 120]
        self.media.segmentos_longos = curto
        _code, saida, _erro = self._roda(["lote", "prep", URL])
        import warden_media
        inteiro = warden_media.digest(curto)
        self.assertIn(inteiro.splitlines()[0], saida)
        self.assertIn(inteiro.splitlines()[-1], saida)
        self.assertNotIn("NOT the whole transcript", saida)


if __name__ == "__main__":
    unittest.main()
