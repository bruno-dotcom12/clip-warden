"""O enquadramento, quando a fonte não é uma pessoa num quadro.

Todo o resto do enquadramento deste projeto responde a uma pergunta só: onde
está o rosto, para a faixa vertical cair nele. Numa live com TELA COMPARTILHADA
essa pergunta tem resposta e a resposta não serve -- o rosto está numa webcam de
canto, e a faixa vertical centrada nela leva junto meia página de navegador.

O dono reprovou dois clipes tirados exatamente desse material em 15/09/2026:
"o formato do clipe veio horrível, era um vídeo retangular e o negócio deu tanto
zoom que às vezes a cara da pessoa some". Medido no segundo clipe, nos oito
quadros do mosaico: em 4 deles a pessoa tinha sumido inteira e o que ficava era
uma página de busca cortada ao meio.

Estes casos prendem as três metades do conserto -- a classificação, a geometria
e a linha que diz qual modo saiu -- e a quarta, que é vídeo normal continuar
saindo pelo caminho de sempre.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden_media as M
import warden_rules as R


def _temp(caso, prefix="warden-enq-"):
    caminho = tempfile.mkdtemp(prefix=prefix)
    caso.addCleanup(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


def _ffmpeg_ou_pula():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise unittest.SkipTest("ffmpeg/ffprobe não está no PATH")


def _pil_ou_pula():
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("Pillow não está instalado")


def _cv2_ou_pula():
    pronto, porque = M.face_detection_status()
    if not pronto:
        raise unittest.SkipTest(f"sem detector de rosto nesta máquina: {porque}")


# As duas janelas que produziram os clipes reprovados. Elas vivem na imagem do
# agente, não no repositório: são 7 MB de live de terceiro e não entram em git.
# Na máquina de quem escreve o código estes casos PULAM, e isso está certo --
# o que não pode é eles não existirem, porque foi contra estes dois arquivos
# que os limiares foram calibrados.
LOTE = os.environ.get("WARDEN_FOOTAGE_TESTE",
                      "/var/lib/hermes/warden/footage/lote/7893834d2c")

# Cada segundo dos dois arquivos, classificado POR MIM, olhando o mosaico dos
# 24 quadros de cada um em 15/09/2026. True é tela compartilhada (navegador,
# busca do Google, matéria de jornal); False é câmera -- webcam cheia, ou
# webcam com o chat da live por cima, que continua sendo uma câmera com a
# pessoa grande no quadro.
#
# É a régua contra a qual a taxa de acerto é medida, e ela é feita de olhos e
# não de outra medição, porque medir a detecção contra si mesma não mede nada.
ROTULOS = {
    "janela-7893834d2c-82-106.mp4": [
        True, False, True, True, False, False, False, False, False, False,
        False, False, False, False, False, False, False, False, True, True,
        True, True, True, False],
    "janela-7893834d2c-1218-1242.mp4": [
        False, False, False, False, False, True, True, True, False, True,
        True, True, False, False, False, False, False, True, True, True,
        True, False, False, False],
}


def _quadros_por_segundo(caminho, destino, quantos=24):
    """Um quadro por segundo do arquivo, na ordem, como imagens do PIL."""
    from PIL import Image
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", caminho,
                    "-vf", "fps=1", "-frames:v", str(quantos),
                    os.path.join(destino, "f%02d.png")],
                   check=True, capture_output=True)
    saiu = []
    for nome in sorted(os.listdir(destino)):
        img = Image.open(os.path.join(destino, nome))
        img.load()
        saiu.append(img)
    return saiu


def _fonte(path, w=1920, h=1080, seconds=3, pip=None, cor_pip="red"):
    """Uma fonte 16:9 de mentira. Com `pip`, um retângulo sólido no canto.

    O retângulo faz o papel da webcam: é a única região do quadro cuja cor se
    reconhece depois de recortada, ampliada e composta, que é como um teste
    consegue afirmar "a webcam apareceu na faixa de CIMA" sem olhar com olhos.
    """
    if pip is None:
        args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                f"testsrc2=size={w}x{h}:rate=30", "-t", str(seconds)]
    else:
        px, py, pw, ph = pip
        args = ["ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=white:size={w}x{h}:rate=30",
                "-f", "lavfi", "-i", f"color=c={cor_pip}:size={pw}x{ph}:rate=30",
                "-filter_complex", f"[0][1]overlay={px}:{py}",
                "-t", str(seconds)]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             path]
    subprocess.run(args, check=True, capture_output=True)
    return path


def _regras(**over):
    base = R.blank()
    base.update({"id": "enq", "name": "Enquadramento", "schema": 1})
    base["video"].update({"duration_min_s": 1, "duration_max_s": 30,
                          "width": 1080, "height": 1920, "aspect": "9:16",
                          "audio": "forbidden"})
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


class PlanuraSeparaTelaDeCamera(unittest.TestCase):
    """Interface é feita de áreas chapadas; câmera não tem nenhuma.

    É a medida que decide tudo o mais, e ela é do PIL de propósito: assim roda
    -- e se confere -- na máquina de quem escreve o código, que não tem OpenCV.
    """

    def setUp(self):
        _pil_ou_pula()

    def test_uma_tela_chapada_mede_alto(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1920, 1080), (250, 250, 250))
        desenho = ImageDraw.Draw(img)
        for y in range(120, 900, 60):          # linhas de texto de uma página
            desenho.rectangle([200, y, 1400, y + 18], fill=(30, 30, 30))
        self.assertGreater(M.planura(img), M.PLANURA_TELA)

    def test_uma_camera_com_ruido_mede_baixo(self):
        import random
        from PIL import Image
        random.seed(7)
        img = Image.new("RGB", (640, 360))
        img.putdata([(random.randint(60, 200),) * 3 for _ in range(640 * 360)])
        self.assertLess(M.planura(img), M.PLANURA_TELA)

    def test_o_limiar_fica_no_vao_e_nao_na_borda(self):
        """0,32 não é um número escolhido: é o meio do vão medido.

        Câmera saiu entre 0,09 e 0,22 e tela entre 0,46 e 0,78 nos 48 quadros
        dos dois arquivos reais. Se alguém mexer no limiar sem mexer na
        medição, isto reprova."""
        self.assertGreater(M.PLANURA_TELA, 0.22)
        self.assertLess(M.PLANURA_TELA, 0.46)


class NoMaterialRealAClassificacaoAcerta(unittest.TestCase):
    """A taxa de acerto contra os rótulos que eu escrevi olhando o mosaico."""

    def setUp(self):
        _ffmpeg_ou_pula()
        _pil_ou_pula()
        self.dir = _temp(self)
        faltando = [n for n in ROTULOS if not os.path.isfile(os.path.join(LOTE, n))]
        if faltando:
            raise unittest.SkipTest(
                f"a filmagem de referência não está em {LOTE} ({faltando[0]} e "
                f"companhia). ELA NÃO ESTÁ NA IMAGEM PUBLICADA -- verificado em "
                f"18/09/2026, o diretório não existe lá, embora este texto "
                f"dissesse que sim. Então esta classe NÃO RODA EM LUGAR NENHUM: "
                f"pula no Mac por falta de OpenCV e pula na imagem por falta do "
                f"material, e uma suíte verde não diz nada sobre a detecção. "
                f"Quem cobre a janela real hoje é "
                f"`AJanelaRealDe1709VirouNUMEROS`, com os números medidos à mão. "
                f"Passe WARDEN_FOOTAGE_TESTE para apontar onde o material estiver.")

    def test_quarenta_e_oito_segundos_de_live_sao_classificados_um_a_um(self):
        erros = []
        for nome, rotulos in ROTULOS.items():
            pasta = os.path.join(self.dir, nome.replace(".", "_"))
            os.makedirs(pasta, exist_ok=True)
            quadros = _quadros_por_segundo(os.path.join(LOTE, nome), pasta)
            self.assertEqual(len(quadros), len(rotulos), nome)
            for i, (img, esperado) in enumerate(zip(quadros, rotulos)):
                medido = M.planura(img)
                if (medido >= M.PLANURA_TELA) != esperado:
                    erros.append(f"{nome} s{i}: planura {medido:.2f}, "
                                 f"e olhando é {'tela' if esperado else 'câmera'}")
        self.assertEqual(erros, [], "\n".join(erros))


class ADetecaoAchaAWebcamDeCanto(unittest.TestCase):
    """Onde a webcam está, no material real, com o detector de verdade."""

    def setUp(self):
        _ffmpeg_ou_pula()
        _pil_ou_pula()
        _cv2_ou_pula()
        for nome in ROTULOS:
            if not os.path.isfile(os.path.join(LOTE, nome)):
                raise unittest.SkipTest(
                    f"a filmagem de referência não está em {LOTE}, e NÃO está "
                    f"na imagem publicada também (verificado em 18/09/2026): "
                    f"esta classe não roda em lugar nenhum. Ver "
                    f"`AJanelaRealDe1709VirouNUMEROS`.")

    def test_as_duas_janelas_reprovadas_viram_dividido(self):
        for nome in ROTULOS:
            with self.subTest(nome):
                saiu = M.tela_compartilhada(os.path.join(LOTE, nome), 2, 20)
                self.assertEqual(saiu["modo"], "dividido", saiu["porque"])
                self.assertIsNotNone(saiu["pip"], saiu["porque"])
                x0, y0, x1, y1 = saiu["pip"]
                # Nos dois arquivos a webcam está no canto INFERIOR DIREITO --
                # medido, não suposto: x 75%-100% e y 67%-100% numa, x 81%-100%
                # e y 78%-100% na outra.
                self.assertGreater(x0, 0.6, f"{nome}: {saiu['pip']}")
                self.assertGreater(y0, 0.5, f"{nome}: {saiu['pip']}")
                self.assertAlmostEqual(x1, 1.0, places=2)
                self.assertAlmostEqual(y1, 1.0, places=2)
                self.assertLess((x1 - x0) * (y1 - y0), M.PIP_AREA_MAX)

    def test_na_janela_mista_a_trilha_vai_do_canto_ao_meio(self):
        """A prova numérica do conserto do caso misto: na janela que ALTERNA,
        a trilha tem de ter pontos no canto (a webcam do PiP, rosto pequeno) E
        pontos no meio (a fonte em tela cheia, rosto grande). Uma caixa fixa só
        teria os primeiros, e nos outros quadros entregava ombro."""
        saiu = M.tela_compartilhada(
            os.path.join(LOTE, "janela-7893834d2c-82-106.mp4"), 2, 20)
        trilha = saiu["trilha"]
        self.assertGreaterEqual(len(trilha), 10,
                                "uma amostra por segundo, ou a trilha tem vãos")
        canto = [p for p in trilha if p[1] > 0.7 and p[3] < 0.25]
        cheio = [p for p in trilha if p[3] > 0.30]
        self.assertTrue(canto, f"nenhum ponto na webcam de canto: {trilha}")
        self.assertTrue(cheio, f"nenhum ponto em tela cheia: {trilha}")

    def test_um_trecho_so_de_camera_continua_normal(self):
        """Do segundo 5 ao 17 da primeira janela não há tela nenhuma, e ali o
        enquadramento tem de seguir o rosto como sempre. É o portão da
        não-regressão, e ele roda no MESMO arquivo que dispara o dividido --
        um trecho, não outro vídeo."""
        saiu = M.tela_compartilhada(
            os.path.join(LOTE, "janela-7893834d2c-82-106.mp4"), 5, 12)
        self.assertEqual(saiu["modo"], "normal", saiu["porque"])
        self.assertEqual(saiu["quadros_de_tela"], 0, saiu["planuras"])
        # E os rostos voltam, para o caminho de sempre não pagar uma segunda
        # passada de ffmpeg pela mesma janela.
        self.assertTrue(saiu["faces"], "a detecção tem de devolver os rostos")


class ADecisaoEAritmetica(unittest.TestCase):
    """A regra do dono, exercitada sem ffmpeg, sem OpenCV e sem vídeo.

    Uma regra que só se testa com três dependências e um arquivo de 7 MB é uma
    regra que ninguém confere.
    """

    def test_video_normal_SEM_webcam_de_canto_segue_o_caminho_de_sempre(self):
        modo, porque = M.decide_enquadramento([0.1, 0.12, 0.09, 0.15], False)
        self.assertEqual(modo, "normal")
        # A saída do warden é INGLÊS de propósito (quem a lê é o modelo):
        # warden_media.py:4189-4190. A agulha segue o texto, o comportamento
        # é o mesmo.
        self.assertIn("this is ordinary video", porque)

    def test_planura_baixa_COM_webcam_de_canto_divide(self):
        """Gameplay é escuro e texturizado: mede planura baixa e não é vídeo
        comum. Foi exatamente assim que os dois clipes de 17/09/2026 saíram
        pelo caminho normal, com a tela fatiada nas duas bordas -- na fonte
        lê-se `YOU ARE DEAD` e no clipe entregue lê-se `AD`.

        Desde então quem decide é a webcam de canto, e a planura só é narrada.
        """
        modo, porque = M.decide_enquadramento([0.1, 0.12, 0.09, 0.15], True)
        self.assertEqual(modo, "dividido")
        self.assertIn("stable corner webcam", porque)
        self.assertIn("uncropped", porque)
        self.assertIn("0 of 4 sampled frames read as screen", porque,
                      "a planura continua sendo dita, só não decide mais")

    def test_tela_com_webcam_vira_dividido(self):
        modo, porque = M.decide_enquadramento([0.6, 0.7, 0.55, 0.12], True)
        self.assertEqual(modo, "dividido")
        self.assertIn("75%", porque)

    def test_a_planura_nao_muda_mais_o_veredito_quando_ha_webcam(self):
        """Havia uma faixa de dúvida entre 20% e 40% de planura, e ela existia
        porque a planura decidia. Ela não decide mais: com webcam de canto o
        modo é o mesmo em 10%, em 30% e em 60%, e o número entra só na
        justificativa. Um teste que prende as TRÊS de uma vez é o que impede
        a faixa de voltar sem que ninguém perceba."""
        for medidas in ([0.6] + [0.1] * 9,                       # 10%
                        [0.6, 0.6, 0.6] + [0.1] * 7,             # 30%
                        [0.6] * 6 + [0.1] * 4):                  # 60%
            modo, porque = M.decide_enquadramento(medidas, True)
            self.assertEqual(modo, "dividido", medidas)
            self.assertNotIn("DOUBTFUL", porque, medidas)

    def test_area_chapada_sem_webcam_nao_muda_nada(self):
        """Planura alta sozinha NÃO é tela compartilhada, e este caso custou um
        render para existir: o `testsrc2` do ffmpeg mede 0,8 de planura, e
        desenho animado e captura de jogo também são feitos de áreas chapadas.
        A webcam de canto é o que separa uma coisa da outra."""
        modo, porque = M.decide_enquadramento([0.6, 0.7, 0.8, 0.1], False)
        self.assertEqual(modo, "normal")
        self.assertIn("does not change", porque)
        self.assertIn("--crop", porque, "tem de dizer o que fazer")

    def test_duvida_sem_webcam_fica_no_caminho_de_sempre(self):
        medidas = [0.6, 0.6, 0.6] + [0.1] * 7
        self.assertEqual(M.decide_enquadramento(medidas, False)[0], "normal")

    def test_sem_quadro_nenhum_nao_inventa_um_veredito(self):
        modo, porque = M.decide_enquadramento([], True)
        self.assertEqual(modo, "normal")
        self.assertIn("nobody looked", porque)

    def test_sem_detector_a_detecao_nao_para_o_corte(self):
        """Na máquina sem OpenCV a pergunta não se responde -- e `quadros`
        volta None, não 0. A diferença importa: 0 é "olhei e não abriu um
        quadro", que faz o corte parar, e None é "nem cheguei a olhar"."""
        real = M.face_detection_status
        M.face_detection_status = lambda: (False, "OpenCV is not installed")
        self.addCleanup(lambda: setattr(M, "face_detection_status", real))
        saiu = M.tela_compartilhada("/nao/existe.mp4", 0, 5)
        self.assertEqual(saiu["modo"], "normal")
        self.assertIsNone(saiu["quadros"])
        self.assertIn("without a face detector", saiu["porque"])


class OLayoutNaoCobreALegenda(unittest.TestCase):
    """A geometria das faixas, que é subtração e não gosto."""

    def test_a_tela_acaba_acima_de_onde_a_legenda_comeca(self):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080)
        tx, ty, tw, th = faixa["tela"]
        self.assertLessEqual(ty + th, faixa["legenda_topo"],
                             "a legenda queimada cobriria a tela")

    def test_a_tela_entra_encaixada_por_largura(self):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080)
        tx, ty, tw, th = faixa["tela"]
        self.assertEqual(tw, 1080)
        self.assertEqual(th, 608)               # 1080 * 9/16, par
        self.assertEqual(tx, 0)

    def test_a_tela_fica_em_cima_e_a_pessoa_embaixo(self):
        """A ordem foi invertida em 17/09/2026, e por medição.

        Com o rosto em cima, a cara grande e a webcam que aparece DENTRO do
        jogo ficam a 297px uma da outra num quadro de 1920 -- 15%, e leem como
        erro de render. Com o rosto embaixo, 753px. E o jogo tem texto próprio
        no pé (`YOU ARE DEAD`, Health/Energy): com a tela embaixo esse texto
        encosta na nossa legenda.
        """
        pip = (0.75, 0.0, 1.0, 0.3333)
        faixa = M.layout_dividido(1080, 1920, 1920, 1080, pip=pip,
                                  rosto=(0.862, 0.138))
        tx, ty, tw, th = faixa["tela"]
        wx, wy, ww, wh = faixa["webcam"]
        self.assertGreaterEqual(wy, ty + th, "a pessoa fica ABAIXO da tela")
        fx, fy, fw, fh = faixa["faixa_rosto"]
        self.assertGreaterEqual(fh, int(1920 * M.ROSTO_FAIXA_MIN),
                                "a pessoa tem de caber grande")
        self.assertLessEqual(fy + fh, faixa["legenda_topo"],
                             "a legenda queimada cobriria a pessoa")

    def test_nada_nosso_entra_na_interface_do_app(self):
        """Ordem do dono de 17/09/2026, depois de olhar a formatação do
        Shorts: nem em cima nem embaixo."""
        import warden_style as S
        marge = S.margens(1080, 1920)
        pip = (0.75, 0.0, 1.0, 0.3333)
        faixa = M.layout_dividido(1080, 1920, 1920, 1080, pip=pip,
                                  rosto=(0.862, 0.138))
        tx, ty, tw, th = faixa["tela"]
        wx, wy, ww, wh = faixa["webcam"]
        self.assertGreaterEqual(ty, marge["top"], "a tela invade o topo do app")
        self.assertLessEqual(wy + wh, 1920 - marge["bottom"],
                             "a pessoa invade a base do app")

    def test_a_tela_encolhe_para_a_pessoa_caber_e_continua_INTEIRA(self):
        """A conta começa pelo rosto, e é a inversão que o dono escolheu
        olhando as maquetes: 12% menos de tela por 21% mais de rosto.

        O que NÃO pode acontecer é a tela deixar de caber inteira -- a caixa
        tem de manter a proporção da fonte, senão o `increase` + `crop` da
        cadeia come as bordas, que é o defeito 2.
        """
        pip = (0.75, 0.0, 1.0, 0.3333)
        faixa = M.layout_dividido(1080, 1920, 1920, 1080, pip=pip,
                                  rosto=(0.862, 0.138))
        tx, ty, tw, th = faixa["tela"]
        self.assertLess(tw, 1080, "a tela tinha de encolher pela pessoa")
        self.assertEqual(tx, (1080 - tw) // 2, "e ficar centrada")
        self.assertAlmostEqual(tw / th, 1920 / 1080, places=2,
                               msg="a caixa saiu fora da proporção da fonte, "
                                   "e aí o crop come as bordas")

    def test_o_recorte_da_webcam_fica_dentro_do_pip(self):
        pip = (0.75, 0.667, 1.0, 1.0)
        faixa = M.layout_dividido(1080, 1920, 1920, 1080, pip=pip,
                                  rosto=(0.87, 0.89))
        cx, cy, cw, ch = faixa["corte_webcam"]
        self.assertGreaterEqual(cx, int(pip[0] * 1920) - 1)
        self.assertGreaterEqual(cy, int(pip[1] * 1080) - 1)
        self.assertLessEqual(cx + cw, 1920)
        self.assertLessEqual(cy + ch, 1080)

    def test_sem_pip_nao_ha_faixa_de_webcam(self):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080)
        self.assertNotIn("corte_webcam", faixa)


class ACadeiaNaoPintaPretoNoPe(unittest.TestCase):
    """O que a cadeia de filtros diz, lida como texto.

    Um `pad` deixaria 472px de preto no pé do quadro, e preto no pé é
    exatamente o que `barras_pretas` reprova -- foi um defeito real deste
    projeto, 289px de tarja num clipe entregue.
    """

    def _cadeia(self, **kw):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.667, 1.0, 1.0), rosto=(0.87, 0.89))
        cadeia, _rastreio = M.cadeia_dividida(1080, 1920, faixa, length=20, **kw)
        return cadeia, faixa

    def test_o_fundo_e_o_proprio_quadro_borrado(self):
        cadeia, _ = self._cadeia()
        self.assertIn("boxblur", cadeia)
        self.assertNotIn("pad=", cadeia)
        self.assertNotIn("color=black", cadeia)

    def test_as_duas_faixas_entram_como_overlay_nas_alturas_do_layout(self):
        cadeia, faixa = self._cadeia()
        self.assertIn(f"overlay={faixa['webcam'][0]}:{faixa['webcam'][1]}", cadeia)
        self.assertIn(f"overlay={faixa['tela'][0]}:{faixa['tela'][1]}", cadeia)

    def test_sem_trilha_a_webcam_e_o_recorte_fixo_do_pip(self):
        """O ramo de trás, e ele continua existindo: é o caso em que a webcam
        foi achada pela planura e nenhum quadro deu rosto para seguir."""
        cadeia, faixa = self._cadeia()
        cx, cy, cw, ch = faixa["corte_webcam"]
        self.assertIn(f"crop={cw}:{ch}:{cx}:{cy}", cadeia)

    def test_o_zoom_vive_dentro_da_faixa_da_webcam(self):
        com, faixa = self._cadeia(motion=True)
        sem, _ = self._cadeia(motion=False)
        self.assertIn("zoompan", com)
        self.assertIn(f"s={faixa['webcam'][2]}x{faixa['webcam'][3]}", com)
        self.assertNotIn("zoompan", sem)

    def test_com_webcam_de_canto_o_RECORTE_FIXO_ganha_da_trilha(self):
        """A regra foi invertida em 17/09/2026, e por medição no render.

        Antes, ter trilha bastava para o rastreio mandar. Só que a webcam de
        canto é uma caixa PARADA na fonte: rastrear uma caixa que não anda só
        acrescenta tremor, porque o zoompan interpola posição e zoom entre
        amostras e as amostras em que o YuNet não achou rosto entram como
        quadro inteiro.

        Comparados quadro a quadro na janela 481-502, em 0,5s / 1,3s / 2,2s /
        4s / 8s / 12s: com rastreio a cara SOME em 1,3s -- sobra a barra de
        sub, ampliada -- e sai da borda em 2,2s. Com o recorte fixo os seis
        saem estáveis e enquadrados.
        """
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.667, 1.0, 1.0), rosto=(0.87, 0.89))
        trilha = [(0.5, 0.60, 0.61, 0.45), (2.5, 0.86, 0.89, 0.14)]
        cadeia, rastreio = M.cadeia_dividida(1080, 1920, faixa, length=20,
                                             trilha=trilha, sw=1920, sh=1080)
        cx, cy, cw, ch = faixa["corte_webcam"]
        self.assertIn(f"crop={cw}:{ch}:{cx}:{cy}", cadeia)
        self.assertIsNone(rastreio,
                          "o sidecar não pode dizer que rastreou se não rastreou")

    def test_SEM_caixa_de_webcam_a_trilha_volta_a_mandar(self):
        """O rastreio não foi apagado: ele é o caminho quando não há caixa
        fixa para recortar."""
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.667, 1.0, 1.0), rosto=(0.87, 0.89))
        faixa.pop("corte_webcam")
        trilha = [(0.5, 0.60, 0.61, 0.45), (2.5, 0.86, 0.89, 0.14)]
        cadeia, rastreio = M.cadeia_dividida(1080, 1920, faixa, length=20,
                                             trilha=trilha, sw=1920, sh=1080)
        self.assertIn("zoompan", cadeia)
        self.assertEqual(rastreio["pontos"], 2)


class AFaixaDeCimaSegueAPessoa(unittest.TestCase):
    """O conserto do caso MISTO, que é o que uma live é.

    A primeira versão recortava sempre o mesmo retângulo -- o da webcam de
    canto. Numa janela que alterna entre webcam em tela cheia e tela
    compartilhada, esse retângulo é o ombro da pessoa metade do tempo: medido
    em 15/09/2026 na janela 82-106, 5 dos 8 quadros do mosaico eram um close
    borrado de ombro, cabelo ou queixo. Avisar não bastava -- o clipe saía ruim
    do mesmo jeito.
    """

    def test_num_quadro_de_tela_vale_o_rosto_de_dentro_da_webcam(self):
        """A página de busca mostra FOTOS de rosto, e num quadro a foto é maior
        que a webcam. Quem decide é estar dentro do PiP, não o tamanho."""
        pip = (0.75, 0.667, 1.0, 1.0)
        quadro = [(0.9, 0.6, 0.20, 0.42),        # foto na página, e é a maior
                  (0.87, 0.89, 0.06, 0.14)]      # a webcam
        # a foto está fora do PiP em y, a webcam dentro
        trilha = M.trilha_do_rosto([(1.0, 0.55, quadro)], pip)
        self.assertEqual(len(trilha), 1)
        self.assertAlmostEqual(trilha[0][1], 0.87, places=2)
        self.assertAlmostEqual(trilha[0][3], 0.14, places=2)

    def test_num_quadro_de_camera_vale_o_maior_rosto(self):
        quadro = [(0.2, 0.3, 0.04, 0.09), (0.6, 0.5, 0.18, 0.40)]
        trilha = M.trilha_do_rosto([(1.0, 0.12, quadro)], (0.75, 0.667, 1.0, 1.0))
        self.assertAlmostEqual(trilha[0][1], 0.6, places=2)

    def test_uma_piscada_do_detector_segura_o_enquadramento(self):
        um = [(0.6, 0.5, 0.18, 0.40)]
        trilha = M.trilha_do_rosto(
            [(1.0, 0.12, um), (2.0, 0.12, []), (3.0, 0.12, um)], None)
        self.assertEqual([t for t, *_ in trilha], [1.0, 3.0],
                         "o quadro sem rosto não vira ponto: a rampa atravessa")

    def test_a_pessoa_fora_de_vista_ABRE_a_faixa_para_o_quadro_inteiro(self):
        """Duas faltas seguidas é a pessoa realmente fora de vista. Segurar ali
        entrega um close de cabelo ocupando um terço do clipe."""
        um = [(0.6, 0.5, 0.18, 0.40)]
        trilha = M.trilha_do_rosto(
            [(1.0, 0.12, um), (2.0, 0.12, []), (3.0, 0.12, []),
             (4.0, 0.12, um)], None)
        meio = [p for p in trilha if p[0] in (2.0, 3.0)]
        self.assertEqual(len(meio), 2)
        for _t, cx, cy, fh in meio:
            self.assertEqual((cx, cy, fh), (0.5, 0.5, 1.0),
                             "um rosto da altura do quadro dá zoom 1")

    def test_o_piso_de_tamanho_separa_o_adesivo_do_rosto(self):
        """O adesivo animado do Mario mede 0,010-0,014 de largura e o YuNet acha
        rosto nele com 0,79 de confiança; a webcam de canto mede 0,042-0,060."""
        self.assertGreater(M.ROSTO_MIN_LARGURA, 0.014)
        self.assertLess(M.ROSTO_MIN_LARGURA, 0.042)

    def test_o_zoom_cresce_quando_o_rosto_e_pequeno_e_fica_em_um_quando_e_grande(self):
        grande, _ = M.zoompan_do_rosto([(1.0, 0.5, 0.5, 0.45)], 1080, 616,
                                       1920, 1080, motion=False)
        pip, dados = M.zoompan_do_rosto([(1.0, 0.87, 0.89, 0.14)], 1080, 616,
                                        1920, 1080, motion=False)
        self.assertIn("zoompan", grande)
        self.assertEqual(
            M.zoompan_do_rosto([(1.0, 0.5, 0.5, 0.45)], 1080, 616, 1920, 1080,
                               motion=False)[1]["zoom_max"], 1.0,
            "um rosto de tela cheia já chega ao alvo: a faixa mostra o quadro")
        self.assertGreater(dados["zoom_max"], 2.0)
        self.assertLessEqual(dados["zoom_max"], M.ZOOM_ROSTO_MAX)

    def test_o_teto_do_zoom_sai_do_esticao_do_pixel_e_nao_de_um_numero(self):
        """"Saiu tudo com zoom" -- e o teto de 4,0 não era quem deixava.

        Medido nos dois cortes entregues em 15/09/2026, o zoom que saiu foi
        2,75 e 3,77: o teto quase não pegou. O que faltava era amarrar o zoom
        ao tamanho do rosto NA FONTE, e quem faz isso é a escala -- a faixa
        mostra `ch/z` pixels da fonte em `banda_h` de saída. Com a faixa de
        734px e a fonte de 1080, o teto cai sozinho para 2,6x.
        """
        _f, dados = M.zoompan_do_rosto([(1.0, 0.87, 0.89, 0.02)], 1080, 734,
                                       1920, 1080, motion=False)
        # Um rosto de 2% do quadro pediria 19x para chegar aos 38% da faixa.
        self.assertLess(dados["zoom_max"], 3.0, dados)
        self.assertEqual(dados["zoom_max"], dados["zoom_teto"], dados)
        self.assertAlmostEqual(dados["zoom_teto"],
                               round(M.ESCALA_FONTE_MAX * 1080 / 734, 2),
                               places=1)
        # E uma faixa mais alta aperta o teto ainda mais, porque estica mais.
        _f2, alta = M.zoompan_do_rosto([(1.0, 0.87, 0.89, 0.02)], 1080, 900,
                                       1920, 1080, motion=False)
        self.assertLess(alta["zoom_teto"], dados["zoom_teto"], (alta, dados))

    def test_o_pre_recorte_nao_corta_fora_a_webcam_do_canto(self):
        """Centrado no QUADRO, o pré-recorte tira 165px de cada lado de uma
        fonte 16:9 -- e a webcam desta live mora nos últimos 480px. Um terço
        dela ia embora antes de o rastreio começar, e a faixa de cima saía com
        uma tarja preta de página à esquerda do rosto. Ele é centrado no
        ALCANCE DO RASTREIO."""
        filtro, _ = M.zoompan_do_rosto(
            [(0.5, 0.89, 0.90, 0.15), (1.5, 0.89, 0.90, 0.15)],
            1080, 734, 1920, 1080, motion=False)
        w, _h, x, resto = filtro.split("crop=")[1].split(":", 3)
        # Encosta na borda direita a menos do pixel par que o crop arredonda.
        self.assertGreaterEqual(int(x) + int(w), 1918,
                                "o pré-recorte tinha de encostar na borda da "
                                "webcam, e ele corta 166px dela quando é "
                                "centrado no quadro")

    def test_sem_trilha_nao_ha_seguidor(self):
        self.assertEqual(M.zoompan_do_rosto([], 1080, 616, 1920, 1080),
                         (None, None))

    def test_a_entrada_e_pre_recortada_na_proporcao_da_faixa(self):
        """O zoompan recorta sempre na proporção da ENTRADA. Sem o pré-recorte
        a faixa de cima sai esticada."""
        filtro, _ = M.zoompan_do_rosto([(1.0, 0.5, 0.5, 0.20)], 1080, 616,
                                       1920, 1080, motion=False)
        self.assertTrue(filtro.startswith("crop="), filtro[:40])
        w, h = filtro.split("crop=")[1].split(":")[:2]
        self.assertAlmostEqual(int(w) / int(h), 1080 / 616, places=2)

    def test_a_trilha_anda_em_patamares_e_nao_em_varredura(self):
        """Uma rampa contínua entre uma amostra no canto e a seguinte no meio
        varre o quadro -- e no meio do caminho a faixa para numa parede de
        espuma acústica, sem rosto nenhum. Era o quadro de 8,8s do mosaico."""
        moldado = M._com_rampa([(0, 0.0), (30, 1.0)])
        valores = dict(moldado)
        self.assertEqual(valores[0], 0.0)
        self.assertEqual(valores[30], 1.0)
        # perto de cada amostra o valor ainda é o dela, não o do meio
        parados = [v for n, v in moldado if n <= 10]
        self.assertTrue(all(v == 0.0 for v in parados), moldado)

    def test_a_expressao_e_do_ffmpeg_e_fala_em_quadros(self):
        expr = M._expressao_por_quadro([(0, 0.2), (60, 0.8)])
        self.assertIn("if(lt(on,", expr)
        self.assertNotIn("\\", expr, "a expressão vai dentro de aspas simples")


class ODivididoSaiNoTamanhoCerto(unittest.TestCase):
    """Um render de verdade, e a webcam conferida na faixa de cima por COR.

    A fonte é branca com um retângulo vermelho sólido no canto onde a webcam
    de uma live fica. Se a faixa de cima sair vermelha, foi o canto que subiu;
    se sair branca, subiu a página -- que é o defeito inteiro.
    """

    def setUp(self):
        _ffmpeg_ou_pula()
        _pil_ou_pula()
        self.dir = _temp(self)

    def _render(self):
        from PIL import Image
        src = _fonte(os.path.join(self.dir, "src.mp4"),
                     pip=(1440, 720, 480, 360))
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.6667, 1.0, 1.0),
                                  rosto=(0.875, 0.833))
        cadeia, _rastreio = M.cadeia_dividida(1080, 1920, faixa, motion=False)
        png = os.path.join(self.dir, "quadro.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        "-vf", cadeia, "-frames:v", "1", png],
                       check=True, capture_output=True)
        img = Image.open(png)
        img.load()
        return img, faixa

    def test_o_quadro_sai_1080x1920(self):
        img, _ = self._render()
        self.assertEqual(img.size, (1080, 1920))

    def test_a_webcam_aparece_na_faixa_da_pessoa(self):
        """A faixa da pessoa mudou de lugar em 17/09/2026 -- foi do topo para
        o pé da imagem -- e este teste conta o mesmo que sempre contou: se ela
        sair vermelha, subiu o canto certo; se sair branca, subiu a página."""
        img, faixa = self._render()
        wx, wy, ww, wh = faixa["webcam"]
        r, g, b = img.convert("RGB").getpixel((wx + ww // 2, wy + wh // 2))[:3]
        self.assertGreater(r, 150, "a faixa da pessoa tinha de ser a webcam")
        self.assertLess(g, 90)
        self.assertLess(b, 90)

    def test_a_tela_LEVA_a_webcam_junto_porque_nada_dela_e_cortado(self):
        """Este teste é o INVERSO do que estava aqui, e a inversão é decisão
        do dono, tomada em 17/09/2026 olhando o material.

        Antes ele exigia ZERO pixel vermelho na faixa da tela: a webcam era
        recortada para fora para não pôr a mesma cara duas vezes. Só que nesta
        live a webcam é uma caixa POR CIMA do jogo, não um lado a lado, e a
        lasca que a exclui custa um quarto da cena -- com o HUD de munição
        dentro. Perguntado, o dono escolheu a cena inteira: a cara repetida já
        está repetida no layout do próprio streamer.

        Então o que se confere agora é o contrário: o retângulo vermelho TEM
        de estar na faixa da tela, no lugar dele, porque a ausência dele é
        prova de que a tela foi cortada.
        """
        img, faixa = self._render()
        tx, ty, tw, th = faixa["tela"]
        rgb = img.convert("RGB")
        pagina = rgb.getpixel((tx + tw // 4, ty + th // 4))[:3]
        self.assertGreater(min(pagina), 180, "a página tinha de estar inteira")
        # O PiP da fonte está em x 75%-100% e y 67%-100%. Dentro da caixa da
        # tela ele cai no mesmo lugar proporcional, porque a tela entra
        # encaixada por largura e sem recorte nenhum.
        dentro = rgb.getpixel((tx + int(tw * 0.87), ty + int(th * 0.83)))[:3]
        self.assertGreater(dentro[0], 140,
                           "a webcam sumiu da tela: ela foi cortada")
        self.assertLess(dentro[1], 100)
        tira = rgb.crop((tx, ty, tx + tw, ty + th))
        vermelhos = sum(1 for r, g, b in tira.getdata()
                        if r > 140 and g < 100 and b < 100)
        esperado = tw * th * 0.25 * 0.3333
        self.assertGreater(
            vermelhos, esperado * 0.8,
            f"só {vermelhos} pixels da webcam na faixa da tela, esperados "
            f"~{esperado:.0f}: falta pedaço da cena")

    def test_o_recorte_da_tela_e_a_menor_lasca_que_exclui_a_webcam(self):
        """Qual das quatro lascas sai é medida, não escolhida.

        Com o PiP em x 75%-100% e y 67%-100%, a lasca da direita custa 25% da
        largura e a de baixo custa 33% da altura. Sai a da direita: a página
        perde a coluna lateral e não a metade de baixo do texto.
        """
        corte = M._corte_sem_webcam(1920, 1080, (0.75, 0.6667, 1.0, 1.0))
        self.assertEqual(corte, (0, 0, 1440, 1080))
        # Um PiP baixo e largo -- uma barra de câmera no pé -- sai pelo outro
        # lado, pela mesma conta.
        baixo = M._corte_sem_webcam(1920, 1080, (0.0, 0.85, 1.0, 1.0))
        self.assertEqual(baixo, (0, 0, 1920, 918))

    def test_sem_webcam_achada_a_tela_continua_inteira(self):
        """Sem PiP não há o que excluir, e recortar por via das dúvidas seria
        jogar imagem fora de graça."""
        faixa = M.layout_dividido(1080, 1920, 1920, 1080)
        self.assertIsNone(M._corte_sem_webcam(1920, 1080, None))
        self.assertNotIn("corte_tela", faixa)

    def test_o_pe_do_quadro_nao_e_tarja_preta(self):
        """O portão que 289px de tarja num clipe entregue comprou."""
        import warden_style as S
        img, _ = self._render()
        barras, porque = S.barras_pretas([img])
        self.assertIsNone(porque)
        self.assertLess(barras["bottom"], S.BARRA_MIN, barras)
        self.assertLess(barras["top"], S.BARRA_MIN, barras)


class OHookNaoSentaNoTextoDaFonte(unittest.TestCase):
    """Aritmética do empurrão do hook, sem ffmpeg e sem vídeo.

    No layout de antes a faixa da webcam ficava em cima e o hook virava
    lower-third dentro dela, longe do texto da fonte. Com a tela em cima,
    encostada na margem do app, os dois passaram a disputar a mesma faixa:
    medido em 18/09/2026 nesta live, o placar do jogo cai em y296-342 do
    quadro e o `[Tab] Hand Signals` em y358-376, e o hook ocupava y280-482.
    """

    def _pecas(self):
        import warden_style as S
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.0, 1.0, 0.3333),
                                  rosto=(0.862, 0.138))
        corpo = int(1080 * S.HOOK_SIZE_RATIO)
        leading = int(corpo * S.LEADING)
        folga = max(6, 1920 // 120)
        return S, faixa, leading, folga

    def test_a_fracao_do_empurrao_e_a_MESMA_que_procura_o_texto(self):
        """Dois 0,16 soltos em arquivos diferentes saem de sincronia sem
        ninguém ver. O empurrão usa a constante que `burned_text_bands`
        usa para olhar."""
        import inspect
        import warden_style as S
        assinatura = inspect.signature(S.burned_text_bands)
        self.assertEqual(assinatura.parameters["band"].default,
                         S.BANDA_TEXTO_FONTE)

    def test_o_empurrao_poe_o_hook_abaixo_da_faixa_de_texto_da_fonte(self):
        S, faixa, leading, folga = self._pecas()
        tx, ty, tw, th = faixa["tela"]
        abaixo = ty + int(th * S.BANDA_TEXTO_FONTE) + folga
        self.assertGreater(abaixo, ty + int(th * S.BANDA_TEXTO_FONTE),
                           "tem de sobrar folga depois da faixa de texto")
        self.assertGreater(abaixo, S.margens(1080, 1920)["top"],
                           "e tem de ser um empurrão, não um recuo")

    def test_o_hook_empurrado_ainda_cabe_acima_da_faixa_da_pessoa(self):
        """O teto não é decoração: sem ele, uma tela baixa empurraria o hook
        para cima da cara, que é o defeito que o lower-third existia para
        evitar."""
        S, faixa, leading, folga = self._pecas()
        tx, ty, tw, th = faixa["tela"]
        abaixo = ty + int(th * S.BANDA_TEXTO_FONTE) + folga
        teto = faixa["webcam"][1] - 2 * leading - folga
        self.assertLessEqual(abaixo, teto,
                             f"hook empurrado para {abaixo} passa do teto {teto}")
        self.assertLessEqual(abaixo + 2 * leading, faixa["webcam"][1],
                             "o bloco do hook encostaria na faixa da pessoa")

    def test_sem_texto_no_topo_da_fonte_o_hook_nao_desce(self):
        """O empurrão é condicional. Uma fonte limpa não paga por ele -- e o
        corpus aprovado tem o hook no alto."""
        S, faixa, leading, folga = self._pecas()
        # O gate real vive no `cut` (`source_text.get("top")`); aqui se
        # confere que a margem continua sendo o ponto de partida.
        self.assertEqual(S.margens(1080, 1920)["top"], 280)


class AFaixaDaLegendaSoEReservadaSeHouverLegenda(unittest.TestCase):
    """Um terço de quadro guardado para um texto que não vem.

    O clipe que o dono recebeu em 15/09/2026 reservou 1240-1560 para a legenda
    queimada e não queimou nenhuma -- o SRT não estava aprovado. Aquele terço
    saiu com fundo borrado e nada dentro, e o layout não tinha como saber,
    porque perguntava "onde a legenda cabe?" antes de alguém perguntar "vai
    haver legenda?".
    """

    def test_com_legenda_a_imagem_para_no_teto_dela(self):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.6667, 1.0, 1.0), legenda=True)
        tx, ty, tw, th = faixa["tela"]
        self.assertLessEqual(ty + th, faixa["legenda_topo"])
        self.assertEqual(faixa["pe_da_imagem"], faixa["legenda_topo"])
        self.assertTrue(faixa["legenda_reservada"])

    def test_sem_legenda_a_imagem_desce_ate_a_interface_do_app(self):
        """A imagem cresce sobre o espaço da legenda que não vem -- mas PARA
        na interface do app, e não mais no pé do quadro.

        Até 17/09/2026 ela descia até 1920. A ordem nova do dono, depois de
        olhar a formatação do Shorts, é que nada nosso fique atrás dos botões
        do aplicativo, tenha ou não legenda. O borrão que ele reprovava não
        era o rodapé: era o fundo clipando em preto, e isso foi consertado em
        `cadeia_dividida`, não aqui.
        """
        import warden_style as S
        marge = S.margens(1080, 1920)
        com = M.layout_dividido(1080, 1920, 1920, 1080,
                                pip=(0.75, 0.6667, 1.0, 1.0), legenda=True)
        sem = M.layout_dividido(1080, 1920, 1920, 1080,
                                pip=(0.75, 0.6667, 1.0, 1.0), legenda=False)
        self.assertEqual(sem["pe_da_imagem"], 1920 - marge["bottom"])
        self.assertGreater(sem["pe_da_imagem"], com["pe_da_imagem"])
        self.assertGreater(sem["tela"][3], com["tela"][3],
                           "a tela tinha de ocupar o espaço da legenda")
        self.assertGreaterEqual(sem["webcam"][3], com["webcam"][3],
                                "e a faixa da pessoa não podia encolher")
        self.assertFalse(sem["legenda_reservada"])

    def test_a_pessoa_tem_o_piso_e_a_tela_fica_com_o_resto(self):
        """A conta começa pelo rosto -- é a inversão de 17/09/2026.

        O teto de 45% da faixa da webcam sumiu junto com o layout que o pedia:
        ele existia porque, sem legenda, sobravam 1094px EM CIMA para uma
        webcam de canto. Agora a faixa da pessoa não recebe a sobra, ela
        recebe o piso, e a sobra vai toda para a tela -- que é o que cresce
        quando a legenda não vem.
        """
        sem = M.layout_dividido(1080, 1920, 1920, 1080,
                                pip=(0.75, 0.6667, 1.0, 1.0), legenda=False)
        self.assertGreaterEqual(sem["faixa_rosto"][3],
                                int(1920 * M.ROSTO_FAIXA_MIN))
        self.assertEqual(sem["tela"][2], 1080, "a tela fica com a largura toda")
        self.assertAlmostEqual(sem["tela"][2] / sem["tela"][3], 1920 / 1080,
                               places=2, msg="e continua na proporção da fonte")

    def test_a_tela_mais_alta_que_o_encaixe_e_RECORTADA_e_nao_esticada(self):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.6667, 1.0, 1.0),
                                  rosto=(0.87, 0.89), legenda=False)
        cadeia, _r = M.cadeia_dividida(1080, 1920, faixa, motion=False, length=20)
        tw, th = faixa["tela"][2], faixa["tela"][3]
        self.assertIn(f"scale={tw}:{th}:force_original_aspect_ratio=increase",
                      cadeia)
        self.assertIn(f"crop={tw}:{th}", cadeia)


class OsTrechosDeCameraNaoSaoDivididos(unittest.TestCase):
    """Numa live a fonte alterna, e onde não há tela não há o que dividir.

    Numa passagem de webcam em tela cheia, o dividido põe a pessoa em cima E
    de novo embaixo -- a mesma pessoa duas vezes no mesmo quadro.
    """

    def test_rosto_GRANDE_vira_trecho_de_camera(self):
        """O sinal mudou em 17/09/2026: é o tamanho do rosto, não a planura.

        `rosto_grande` é "há alguém maior que uma caixinha de canto neste
        quadro" -- a definição direta de "a fonte está mostrando a pessoa".
        """
        trechos = M.trechos_de_camera([0.5, 1.5, 2.5, 3.5],
                                      [0.45, 0.05, 0.05, 0.45], 4.0,
                                      rosto_grande=[False, True, True, False])
        self.assertEqual(len(trechos), 1)
        de, ate = trechos[0]
        # Até a amostra de tela vizinha, não até o meio: na dúvida vale a
        # câmera, porque dividir um quadro de câmera é o defeito e enquadrar
        # um quadro de tela como câmera não é.
        self.assertAlmostEqual(de, 0.5, places=2)
        self.assertAlmostEqual(ate, 3.5, places=2)

    def test_so_de_tela_nao_ha_trecho_nenhum(self):
        self.assertEqual(
            M.trechos_de_camera([0.5, 1.5, 2.5], [0.45, 0.46, 0.45], 3.0,
                                rosto_grande=[False, False, False]), [])

    def test_PLANURA_BAIXA_SOZINHA_nao_e_mais_camera(self):
        """Este é o conserto do defeito 1, e ele é um teste de NÃO acontecer.

        Gameplay escuro mede planura baixa e não é câmera. Medido na janela
        481-502 da live de 17/09/2026: 21 dos 39 quadros ficam abaixo do
        limiar e nenhum deles é uma pessoa em close -- o maior rosto de
        qualquer quadro é 0,0128, quatro vezes abaixo de PIP_ROSTO_AREA_MAX.

        Com a regra antiga isso virava um trecho de câmera de 15,3s em 20,9s,
        73% do clipe, e nesse trecho a imagem saía recortada nas duas bordas.
        Foi ele que comeu o `YOU ARE DE` de `YOU ARE DEAD`.
        """
        self.assertEqual(
            M.trechos_de_camera([0.5, 1.5, 2.5, 3.5],
                                [0.05, 0.05, 0.05, 0.05], 4.0,
                                rosto_grande=[False, False, False, False]), [])

    def test_sem_medida_de_rosto_nao_se_inventa_trecho(self):
        """`None` é "ninguém mediu", e aí não se afirma nada. Sem trecho, o
        dividido vale o clipe inteiro -- que é o que ele faz bem. Com um
        trecho falso, a imagem é recortada nas bordas."""
        self.assertEqual(
            M.trechos_de_camera([0.5, 1.5, 2.5], [0.05, 0.05, 0.05], 3.0), [])

    def test_uma_piscada_do_detector_nao_troca_de_layout(self):
        """Um quadro sozinho com rosto grande é o YuNet achando uma cara onde
        ela mal existe. Trocar de layout por meio segundo por causa disso é um
        piscar de tela no clipe."""
        self.assertEqual(
            M.trechos_de_camera([0.5, 1.5, 2.5], [0.45, 0.45, 0.45], 3.0,
                                rosto_grande=[False, True, False]), [])

    def test_a_cadeia_ganha_uma_camada_com_enable_so_quando_ha_trecho(self):
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=(0.75, 0.6667, 1.0, 1.0),
                                  rosto=(0.87, 0.89), legenda=False)
        sem, _ = M.cadeia_dividida(1080, 1920, faixa, motion=False, length=20)
        self.assertNotIn("enable=", sem)
        com, _ = M.cadeia_dividida(1080, 1920, faixa, motion=False, length=20,
                                   cheios=[(2.0, 4.0)], cheio_x=0.6)
        self.assertIn("enable='between(t,2.000,4.000)'", com)
        # e ela cobre a JANELA DA IMAGEM inteira -- do topo da imagem ao pé
        # dela --, não de y=0 ao pé. A diferença é o defeito 1: desenhar de 0
        # a `pe_da_imagem` deixava a base do app com fundo e nada dentro, e a
        # moldura pulava quando a fonte alternava.
        alto = faixa["pe_da_imagem"] - faixa["topo_da_imagem"]
        self.assertIn(f"crop=1080:{alto}", com)
        self.assertIn(f"overlay=0:{faixa['topo_da_imagem']}:enable=", com)


class OQuandoDeCadaAmostraEMedidoENaoSuposto(unittest.TestCase):
    """Meio passo fora de fase é a faixa de cima seguindo o quadro anterior.

    `-ss X` com `fps=1/passo` entrega o primeiro quadro em X+passo, não em
    X+passo/2 como o rótulo dizia. Medido em 15/09/2026 na janela 343-369: as
    planuras da passada única saíram deslocadas de meio passo em relação às
    lidas uma a uma. Numa fonte que troca de layout a cada 1-3s isso é a faixa
    de cima recortando o canto de um quadro que já mudou.
    """

    def setUp(self):
        _ffmpeg_ou_pula()
        _pil_ou_pula()
        self.dir = _temp(self)

    def _fonte_que_vira(self, path, vira_em=2.0, total=4.0):
        """Branco até `vira_em`, preto depois. O relógio fica visível na cor."""
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c=white:size=640x360:rate=30",
             "-f", "lavfi", "-i", f"color=c=black:size=640x360:rate=30",
             "-filter_complex",
             f"[0]trim=duration={vira_em},setpts=PTS-STARTPTS[a];"
             f"[1]trim=duration={total - vira_em},setpts=PTS-STARTPTS[b];"
             f"[a][b]concat=n=2:v=1:a=0",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             path], check=True, capture_output=True)
        return path

    def test_o_tempo_devolvido_bate_com_o_que_esta_no_quadro(self):
        from PIL import Image
        src = self._fonte_que_vira(os.path.join(self.dir, "vira.mp4"))
        tmp, quadros = M._quadros_coloridos(src, 0.0, 4.0, 8)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertGreaterEqual(len(quadros), 6, quadros)
        for t, png in quadros:
            img = Image.open(png)
            img.load()
            claro = img.convert("L").getpixel((320, 180)) > 128
            # 1/30s de tolerância: o quadro é o que existe, não um instante.
            if abs(t - 2.0) < 0.05:
                continue
            self.assertEqual(
                claro, t < 2.0,
                f"o quadro carimbado em {t:.2f}s é "
                f"{'branco' if claro else 'preto'}, e a virada é aos 2,0s")


class ALinhaDoModoSaiSempre(unittest.TestCase):
    """Sem ela, "enquadrou certo" e "ninguém olhou" ficam iguais.

    É a mesma regra que a detecção de legenda queimada já segue neste arquivo,
    e ela existe porque os dois clipes de 15/09 saíram com a pessoa fora do
    quadro e um relatório inteiro sem uma linha sobre enquadramento.
    """

    def setUp(self):
        _ffmpeg_ou_pula()
        _pil_ou_pula()
        self.dir = _temp(self)
        self.src = _fonte(os.path.join(self.dir, "src.mp4"), seconds=4)

    def test_um_corte_normal_diz_que_foi_normal_e_por_que(self):
        saiu = M.cut(self.src, os.path.join(self.dir, "c.mp4"), _regras(),
                     start=0, end=3, sound="platform", crop="center")
        linhas = [n for n in saiu["notes"] if n.startswith("FRAMING:")]
        self.assertEqual(len(linhas), 1, saiu["notes"])
        self.assertIn("vertical band", linhas[0])

    def test_a_decisao_fica_gravada_ao_lado_do_clipe(self):
        saiu = M.cut(self.src, os.path.join(self.dir, "d.mp4"), _regras(),
                     start=0, end=3, sound="platform", crop="center")
        quadro = saiu["style"]["framing"]
        self.assertIn(quadro["mode"], ("normal", "dividido"))
        self.assertTrue(quadro["why"])


class OEncodeCarregaOPresetEOTeto(unittest.TestCase):
    """Velocidade que estoura o anexo não é velocidade.

    Medido em 15/09/2026 no container (amd64 emulado, sem codificador de
    hardware), cortando 20s de 1920x1080 para 1080x1920: `veryfast crf 20`
    custava 16,8s e 6,5 MB; `ultrafast crf 20` custa 6,8s e 16,8 MB, que é 2,6x
    o arquivo; `ultrafast crf 26` com teto de 6M custa 7,1s e 9,2 MB. Os
    arquivos vão como anexo pelo telefone do dono e a faixa que nunca falhou é
    de 7 a 14 MB -- então o teto anda junto com o preset, sempre.
    """

    def setUp(self):
        _ffmpeg_ou_pula()
        _pil_ou_pula()
        self.dir = _temp(self)
        self.src = _fonte(os.path.join(self.dir, "src.mp4"), seconds=4)

    def _args_do_encode(self):
        vistos = []
        real = M.run

        def espia(args, timeout, label):
            if label == "ffmpeg" and args[-1].endswith(".mp4"):
                vistos.append(list(args))
                raise RuntimeError("PARE AQUI")
            return real(args, timeout, label)

        M.run = espia
        self.addCleanup(lambda: setattr(M, "run", real))
        with self.assertRaises(RuntimeError):
            M.cut(self.src, os.path.join(self.dir, "e.mp4"), _regras(),
                  start=0, end=3, sound="platform", crop="center")
        self.assertTrue(vistos, "o encode nem chegou a ser montado")
        return vistos[0]

    def test_o_preset_rapido_e_o_padrao(self):
        args = self._args_do_encode()
        self.assertEqual(args[args.index("-preset") + 1], "ultrafast")
        self.assertEqual(args[args.index("-crf") + 1], "26")

    def test_o_teto_de_taxa_anda_junto_com_o_preset(self):
        args = self._args_do_encode()
        self.assertEqual(args[args.index("-maxrate") + 1], "6M")
        self.assertEqual(args[args.index("-bufsize") + 1], "12M")

    def test_os_quatro_saem_de_variavel_de_ambiente_e_o_padrao_e_o_rapido(self):
        self.assertEqual(M.X264_PRESET, os.environ.get("WARDEN_X264_PRESET",
                                                       "ultrafast"))
        self.assertEqual(M.X264_MAXRATE, os.environ.get("WARDEN_X264_MAXRATE",
                                                        "6M"))


if __name__ == "__main__":
    unittest.main()


class AJanelaRealDe1709VirouNUMEROS(unittest.TestCase):
    """As duas janelas que o dono reprovou, congeladas como aritmética.

    ISTO EXISTE PORQUE A SUÍTE NÃO COBRIA O QUE EU MEXI. Medido em 18/09/2026:
    `ADetecaoAchaAWebcamDeCanto` e `NoMaterialRealAClassificacaoAcerta` pulam no
    Mac (sem OpenCV) E pulam na imagem -- a filmagem `7893834d2c` não está lá,
    embora o texto do skip diga que "vive na imagem do agente". Os quatro testes
    que sustentavam a detecção não rodavam em lugar nenhum, e foi assim que o
    gatilho pôde ficar errado numa live de gameplay sem nada acusar.

    O conserto de verdade seria a filmagem estar em algum lugar. Enquanto não
    está, o que dá para congelar são os NÚMEROS que eu medi rodando a detecção
    de verdade, dentro da imagem, nas duas janelas reais
    (`kZAAnNJHaUc`, 481-502 e 2422-2444). Eles entram aqui como constantes e
    exercitam a mesma aritmética que decide o layout -- sem ffmpeg, sem OpenCV,
    sem vídeo, o que significa que rodam em TODA máquina, inclusive nesta.

    Se alguém mexer no gatilho e estes números deixarem de dar o layout que o
    dono aprovou olhando o clipe, o teste cai aqui e não no contact sheet.
    """

    # Medido dentro da imagem, `tela_compartilhada` nas duas janelas.
    JANELA_01 = {
        "quadros": 39, "de_tela": 18,          # planura >= 0.32
        "pip": (0.75, 0.0, 1.0, 0.3333),
        "rosto": (0.8622, 0.1378),
        "pip_em_quantos": 15,                  # 38% dos quadros amostrados
        "maior_rosto_area": 0.0128,            # o MAIOR de qualquer quadro
    }
    JANELA_02 = {
        "quadros": 40, "de_tela": 33,
        "pip": (0.75, 0.0, 1.0, 0.3333),
        "rosto": (0.8567, 0.1441),
        "pip_em_quantos": 11,                  # 28%
        "maior_rosto_area": 0.0114,
    }

    def _planuras(self, j):
        """Uma lista com a MESMA fração de quadros de tela que a janela real."""
        return [0.50] * j["de_tela"] + [0.10] * (j["quadros"] - j["de_tela"])

    def test_as_duas_janelas_viram_dividido(self):
        """Era o defeito: as duas saíam `normal` e com a tela fatiada."""
        for nome, j in (("01", self.JANELA_01), ("02", self.JANELA_02)):
            modo, porque = M.decide_enquadramento(self._planuras(j), True)
            self.assertEqual(modo, "dividido", f"janela {nome}: {porque}")

    def test_a_webcam_das_duas_passa_no_portao_de_estabilidade(self):
        """O portão antigo era `em_quantos*2 >= quadros_de_tela`, e a janela 02
        falhava nele (22 >= 33) apesar de a webcam estar em todo quadro."""
        for nome, j in (("01", self.JANELA_01), ("02", self.JANELA_02)):
            n, lidos = j["pip_em_quantos"], j["quadros"]
            self.assertGreaterEqual(n, M.PIP_QUADROS_MIN, f"janela {nome}")
            self.assertGreaterEqual(n, M.PIP_FRACAO_MIN * lidos, f"janela {nome}")
            # e o portão ANTIGO reprovava a 02: é o que este teste protege
            if nome == "02":
                self.assertLess(n * 2, j["de_tela"],
                                "se isto deixar de ser verdade, o teste perdeu "
                                "o caso que ele existe para prender")

    def test_uma_foto_numa_pagina_continua_recusada(self):
        """O outro lado do portão. O defeito do Yuri Gagarin: uma foto de rosto
        na página aparece em 1 ou 2 quadros de 40."""
        for aparece in (1, 2):
            self.assertLess(aparece, M.PIP_FRACAO_MIN * 40)
            self.assertLess(aparece, M.PIP_QUADROS_MIN)

    def test_nenhuma_das_duas_tem_trecho_de_camera(self):
        """O ramo que comia as bordas. Com o sinal de planura ele cobria 73% da
        janela 01; com o sinal de rosto grande ele some, e é a resposta certa:
        o maior rosto de qualquer quadro é 0,0128 contra o limiar de 0,05."""
        for nome, j in (("01", self.JANELA_01), ("02", self.JANELA_02)):
            self.assertLess(j["maior_rosto_area"], M.PIP_ROSTO_AREA_MAX,
                            f"janela {nome}")
            n = j["quadros"]
            tempos = [i * 0.5 for i in range(n)]
            trechos = M.trechos_de_camera(
                tempos, self._planuras(j), n * 0.5,
                rosto_grande=[False] * n)
            self.assertEqual(trechos, [], f"janela {nome}: {trechos}")

    def test_a_geometria_das_duas_e_a_que_o_dono_aprovou(self):
        """996x560 de tela e 512x384 de rosto -- os números do clipe que ele
        olhou e aprovou em 17/09/2026."""
        for nome, j in (("01", self.JANELA_01), ("02", self.JANELA_02)):
            faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                      pip=j["pip"], rosto=j["rosto"],
                                      legenda=True)
            self.assertEqual(faixa["tela"], (42, 280, 996, 560), f"janela {nome}")
            self.assertEqual(faixa["webcam"], (284, 856, 512, 384), f"janela {nome}")
            # e a tela na proporção da fonte: é o que garante o "nada cortado"
            self.assertAlmostEqual(996 / 560, 1920 / 1080, places=2)

    def test_o_recorte_do_rosto_e_a_caixa_INTEIRA_da_webcam(self):
        """É por isso que o rosto nunca sai da faixa: medido, ele anda 238px na
        horizontal e 118px na vertical DENTRO da webcam, e o recorte é a webcam
        toda. O rastreio recortava uma sub-região, e por isso perdia o cara."""
        j = self.JANELA_01
        faixa = M.layout_dividido(1080, 1920, 1920, 1080,
                                  pip=j["pip"], rosto=j["rosto"], legenda=True)
        cx, cy, cw, ch = faixa["corte_webcam"]
        px0, py0, px1, py1 = j["pip"]
        self.assertLessEqual(cx, int(px0 * 1920) + 1)
        self.assertLessEqual(cy, int(py0 * 1080) + 1)
        self.assertGreaterEqual(cx + cw, int(px1 * 1920) - 2)
        self.assertGreaterEqual(cy + ch, int(py1 * 1080) - 2)
        # os extremos MEDIDOS do rosto, em pixels da fonte
        for x0, y0, x1, y1 in ((1485, 28, 1867, 291),):
            self.assertLessEqual(cx, x0); self.assertLessEqual(cy, y0)
            self.assertGreaterEqual(cx + cw, x1)
            self.assertGreaterEqual(cy + ch, y1)
