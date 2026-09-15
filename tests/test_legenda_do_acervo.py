# -*- coding: utf-8 -*-
"""Duas legendas de origens diferentes no mesmo quadro, e nada reprovou.

O defeito real, medido: um vlog do YouTube trazia legenda queimada própria --
fina, centralizada, e INTERMITENTE, porque some entre as falas. A detecção de
texto do acervo rodou no arquivo certo, no material de origem, antes de o render
existir. Ela devolveu `bottom: false`, com 1 de 8 quadros votando e média 1,7x a
densidade de borda do miolo, contra um limiar de 1,9x por quadro e persistência
em 60% dos quadros. Os dois números foram calibrados para TARJA FIXA -- um
disclaimer permanente -- e a legenda de vlog não é isso.

`false` ali desligava em cascata o degradê do rodapé, o REJECT do
`check_sidecar` e todas as notas de aviso. O clipe saiu com a nossa legenda por
cima da do canal e a saída do `cut` não disse uma palavra sobre o material,
porque a nota também morava dentro do mesmo `if`.

Cada teste daqui guarda uma das metades desse conserto.
"""
import os
import subprocess
import unittest

from test_warden import (_ffmpeg_or_skip, _make_source, _pillow_or_skip,
                         _subtitles_filter_or_skip, _temp, rules)

import warden_media as M
import warden_style as S


# ───────────────────────────────────────────────────────────── quadros de prova

def _quadro(w=960, h=540, *, disclaimer=False, vlog=False, fino=0, denso=False):
    """Um quadro do PIL com (ou sem) cada forma de texto de acervo que existe.

    `disclaimer` é a tarja fixa para que o booleano duro foi calibrado: duas
    linhas grandes coladas no rodapé. `vlog` é o caso que passou: uma linha só,
    centralizada, com contorno. `fino` é a mesma linha em cinza claro, sem
    contorno -- ela nunca alcança 1,9x em quadro nenhum e só aparece na média.
    `denso` é uma grade que vai até o teto da varredura, que é o terceiro sinal.
    """
    from PIL import Image, ImageDraw
    im = Image.new("L", (w, h), 128)
    d = ImageDraw.Draw(im)
    # O mesmo miolo esparso de `_frame_with` na suíte principal: sem textura a
    # densidade de base é zero e toda razão vira infinito.
    for i in range(0, w, 90):
        d.line([(i, int(h * 0.25)), (i + 30, int(h * 0.75))], fill=190, width=4)
    if disclaimer:
        font = S._font(max(14, h // 22))
        for k in range(2):
            d.text((20, h - int(h * 0.11) + k * int(h * 0.05)),
                   "AVISO LEGAL NAO DISPONIVEL EM TODAS AS JURISDICOES",
                   font=font, fill=255)
    if vlog:
        font = S._font(max(12, h // 28))
        texto = "entao eu peguei a camera e sai"
        largura = d.textlength(texto, font=font)
        d.text(((w - largura) / 2, h - int(h * 0.09)), texto, font=font,
               fill=255, stroke_width=2, stroke_fill=0)
    if fino:
        font = S._font(max(10, h // 34))
        texto = "uma linha fina de legenda do acervo"
        largura = d.textlength(texto, font=font)
        d.text(((w - largura) / 2, h - int(h * 0.08)), texto, font=font, fill=fino)
    if denso:
        for y in range(h - int(h * 0.22), h, 6):
            d.line([(0, y), (w, y)], fill=235, width=2)
    return im


class TresEstados(unittest.TestCase):
    """O booleano duro continua duro; o que entrou é o estado do meio."""

    def setUp(self):
        _pillow_or_skip()
        if not S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")

    def test_o_caso_medido_um_quadro_em_oito_vira_suspeito(self):
        """1 de 8 quadros acima de 1,9x. É exatamente o que a legenda de vlog
        produz -- ela some entre as falas -- e era o que saía como 'limpo'."""
        frames = [_quadro() for _ in range(7)] + [_quadro(vlog=True)]
        got = S.burned_text_bands(frames)
        self.assertFalse(got["bottom"], got["evidence"])
        self.assertTrue(got["bottom_suspect"], got["evidence"])
        self.assertIn("1 frame(s) over 1.9x", got["bottom_why"])

    def test_material_limpo_de_verdade_nao_vira_suspeito(self):
        """O teste que impede o conserto de virar paranoia.

        Zero votos e média 1,0x. Se isto virar suspeito, todo clipe ganha um
        degradê que apaga um quinto do quadro para resolver um problema que não
        existe -- trocar um defeito por outro, que é a regra da casa.
        """
        got = S.burned_text_bands([_quadro() for _ in range(8)])
        self.assertFalse(got["bottom"], got["evidence"])
        self.assertFalse(got["bottom_suspect"], got["evidence"])
        self.assertEqual(got["bottom_why"], [])

    def test_a_media_pega_o_que_nenhum_quadro_sozinho_alcanca(self):
        """Legenda fina e constante: nunca 1,9x num quadro, 1,5x na média.

        O piso é 1,35x, e o número que o justifica está do outro lado: limpo
        mede 1,0x no quadro sintético e 0,99x num `testsrc2`, que é o padrão de
        teste mais carregado que existe.
        """
        got = S.burned_text_bands([_quadro(fino=175) for _ in range(8)])
        self.assertFalse(got["bottom"], got["evidence"])
        self.assertTrue(got["bottom_suspect"], got["evidence"])
        self.assertTrue(any("floor" in p for p in got["bottom_why"]),
                        got["bottom_why"])

    def test_o_booleano_duro_nao_afrouxou(self):
        """O conserto ACRESCENTA um estado, não abaixa o limiar do outro."""
        fixa = S.burned_text_bands([_quadro(disclaimer=True) for _ in range(6)])
        self.assertTrue(fixa["bottom"], fixa["evidence"])
        # E quem é certo não é suspeito também: são estados, não camadas.
        self.assertFalse(fixa["bottom_suspect"], fixa["evidence"])

    def test_o_alcance_da_faixa_aparece_sempre_na_evidencia(self):
        """`bottom_reach` só entrava na evidência quando `bottom` era True.

        Ele era escondido justamente no caso em que era a única coisa a dizer.
        Quem lê a saída não tinha como saber que o instrumento tinha um segundo
        número, e que ele discordava do primeiro.
        """
        for rotulo, frames in (("limpo", [_quadro() for _ in range(6)]),
                               ("tarja", [_quadro(disclaimer=True) for _ in range(6)])):
            got = S.burned_text_bands(frames)
            self.assertIn("the bottom band reaches", got["evidence"], rotulo)

    def test_a_varredura_de_alcance_mede_alguma_coisa(self):
        """`bottom_reach` valia 0,22 -- o teto -- para TUDO, e ninguém viu.

        A varredura chamava a detecção de bordas dentro de cada fatia, e o
        filtro carimba uma borda forte na primeira e na última linha do que
        recebe: numa fatia de 2% da altura essas duas linhas são um quinto dos
        pixels. Medido em 14/09 com o código como estava: 0,22 nos quadros
        limpos, 0,22 no `testsrc2`, 0,22 no disclaimer, os três iguais. Um
        número que não muda não é uma medição, e era ele que dimensionava o
        degradê.
        """
        limpo = S.burned_text_bands([_quadro() for _ in range(6)])
        tarja = S.burned_text_bands([_quadro(disclaimer=True) for _ in range(6)])
        self.assertLess(limpo["bottom_reach"], S.BAND_CAP, limpo["evidence"])
        self.assertGreater(tarja["bottom_reach"], limpo["bottom_reach"],
                           tarja["evidence"])
        self.assertLessEqual(tarja["bottom_reach"], S.BAND_CAP, tarja["evidence"])

    def test_faixa_densa_ate_o_teto_e_sinal(self):
        """O terceiro gatilho, que só faz sentido depois do conserto acima."""
        got = S.burned_text_bands([_quadro(denso=True) for _ in range(8)])
        self.assertEqual(got["bottom_reach"], S.BAND_CAP, got["evidence"])

    def test_sem_quadro_nenhum_nao_ha_suspeita_nem_certeza(self):
        """Lista vazia é 'não olhei', e não pode sair com cara de resposta."""
        got = S.burned_text_bands([])
        self.assertFalse(got["bottom"])
        self.assertFalse(got["bottom_suspect"])
        self.assertFalse(got["top_suspect"])


class TestsrcNaoEhLegenda(unittest.TestCase):
    """O falso positivo mais caro seria o padrão de teste mais carregado."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        self.dir = _temp(self, prefix="warden-acervo-limpo-")

    def test_testsrc2_nao_e_acusado_de_carregar_legenda(self):
        src = _make_source(os.path.join(self.dir, "src.mp4"), seconds=5)
        got = S.burned_text_bands(S.sample_frames(src, 0, 4, count=8))
        self.assertFalse(got["bottom"], got["evidence"])
        self.assertFalse(got["bottom_suspect"], got["evidence"])


class PortaoDoSidecar(unittest.TestCase):
    """O REJECT existia e era real -- e `bottom: false` o desligava em cascata."""

    def _lado(self, **over):
        lado = {"caption": {"cues": 6, "max_lines": 2, "max_cue_s": 2.0,
                            "karaoke": True},
                "hook": None, "footer_covered": False, "motion": True,
                "source_text": {"bottom": False, "bottom_suspect": False,
                                "bottom_why": []}}
        lado.update(over)
        return lado

    def _rejeicoes(self, lado):
        return [m for lv, m in S.check_sidecar(lado) if lv == "REJECT"]

    def test_suspeito_e_sem_cobrir_reprova(self):
        """A conjunção começava pelo booleano duro, então uma suspeita não
        reprovava nada. O portão não pode ser mais estreito que a detecção que
        o alimenta."""
        lado = self._lado(source_text={"bottom": False, "bottom_suspect": True,
                                       "bottom_why": ["1 frame(s) over 1.9x"]})
        self.assertTrue(any("signs of its own burned text" in m
                            for m in self._rejeicoes(lado)),
                        self._rejeicoes(lado))

    def test_suspeito_e_coberto_passa(self):
        """Cobrir é a saída, e quem cobriu não pode ser reprovado por ter
        coberto."""
        lado = self._lado(footer_covered=True,
                          source_text={"bottom": False, "bottom_suspect": True,
                                       "bottom_why": ["1 frame(s) over 1.9x"]})
        self.assertFalse(any("burned text" in m for m in self._rejeicoes(lado)),
                         self._rejeicoes(lado))

    def test_material_limpo_com_legenda_nossa_passa(self):
        """O outro lado do mesmo portão: contar camadas sem perguntar se havia
        texto do acervo reprovava clipe cujo material é limpo."""
        self.assertFalse(any("burned text" in m
                             for m in self._rejeicoes(self._lado())),
                         self._rejeicoes(self._lado()))

    def test_sem_legenda_nossa_a_suspeita_nao_reprova(self):
        """Texto do acervo sozinho no quadro não é legenda dupla: é o material
        como ele é. O que reprova é a NOSSA por cima."""
        lado = self._lado(caption=None,
                          source_text={"bottom": False, "bottom_suspect": True,
                                       "bottom_why": ["1 frame(s) over 1.9x"]})
        self.assertFalse(any("burned text" in m for m in self._rejeicoes(lado)),
                         self._rejeicoes(lado))


class SegundaOpiniaoNoRenderPronto(unittest.TestCase):
    """`text_rows` mede o arquivo PRONTO, e não dependia de detecção nenhuma.

    Ela estava em `REPORTED_ONLY` e não votava. O cruzamento com o que o sidecar
    diz que desenhamos transforma ela num portão -- com a folga que o próprio
    corpus exige, e não com a folga que seria bonita.
    """

    def _lado(self, *, hook_linhas=0, ficou=3.0, cue_linhas=2, dur=20.0):
        return {"hook": ({"lines": hook_linhas, "seconds_on_screen": ficou}
                         if hook_linhas else None),
                "caption": {"cues": 6, "max_lines": cue_linhas},
                "duration_s": dur}

    def _cruz(self, lado, por_quadro):
        return S._linhas_demais(lado, {"text_rows_per_frame": por_quadro})

    def test_faixa_a_mais_do_que_desenhamos_reprova(self):
        """Nossa cue de 2 linhas mais uma legenda de acervo de 2: 4 faixas onde
        cabem 2. Medido num render sintético, 4 em todos os dez quadros."""
        achados = self._cruz(self._lado(), [4] * 10)
        self.assertEqual([lv for lv, _m in achados], ["REJECT"], achados)

    def test_legenda_nossa_de_duas_linhas_nao_e_legenda_dupla(self):
        """Uma cue de DUAS linhas lê como DUAS faixas: entre uma linha e outra
        passa imagem limpa, e a varredura só junta o que é contíguo. Cruzar com
        `text_layers` (que diria 1) reprovaria todo corte de fala do projeto."""
        achados = self._cruz(self._lado(), [2] * 10)
        self.assertEqual([lv for lv, _m in achados], ["ok"], achados)

    def test_o_hook_que_sai_aos_tres_segundos_nao_compra_orcamento(self):
        """O portão erra dos dois lados se tratar o hook como permanente.

        Num corte de 20s ele fica 3s: oito dos dez quadros amostrados não têm
        hook nenhum. Somá-lo ao orçamento de todos compra duas linhas de crédito
        para a metade do clipe em que ele não existe -- medido, com o hook no
        orçamento o render com legenda dupla dá 4 faixas contra um limite de 5 e
        PASSA. Sem ele, dá 4 contra 4 e reprova.
        """
        # O mesmo arquivo defeituoso do primeiro teste, agora com hook.
        achados = self._cruz(self._lado(hook_linhas=1, ficou=3.0, dur=20.0),
                             [3, 3] + [4] * 8)
        self.assertEqual([lv for lv, _m in achados], ["REJECT"], achados)
        # E o corte curto, onde o hook É o quadro mediano, mantém o crédito.
        achados = self._cruz(self._lado(hook_linhas=1, ficou=3.0, dur=5.0),
                             [3] * 10)
        self.assertEqual([lv for lv, _m in achados], ["ok"], achados)

    def test_uma_faixa_de_folga_nao_reprova_porque_o_corpus_tem_uma(self):
        """A folga é +2 e não +1, e quem decide isso é o corpus aprovado.

        Os vinte scenepacks carregam UMA frase-âncora de no máximo duas linhas e
        mediram `text_rows` de 0,9 a 2,1 de MÉDIA. Média 2,1 com duas linhas
        desenhadas quer dizer quadros com três e quatro faixas em clipes que o
        dono aprovou: a varredura inventa uma faixa sozinha em material real.
        Com +1 este portão reprovaria o corpus inteiro, e um portão que reprova
        clipe bom é desligado na semana seguinte e aí não protege nada.
        """
        achados = self._cruz(self._lado(), [3] * 10)
        self.assertEqual([lv for lv, _m in achados], ["ok"], achados)

    def test_um_quadro_solto_nao_reprova_o_clipe(self):
        """Máximo é um letreiro no fundo; mediana é o clipe. A pergunta é 'na
        maioria dos quadros sobra texto que não é nosso?'."""
        achados = self._cruz(self._lado(), [2, 2, 2, 5, 2, 2, 2, 2, 2, 2])
        self.assertEqual([lv for lv, _m in achados], ["ok"], achados)

    def test_sem_sidecar_o_cruzamento_nao_opina(self):
        """Sem saber o que desenhamos, faixa de texto é só faixa de texto. Um
        palpite aqui é a régua medindo a si mesma."""
        self.assertEqual(self._cruz(None, [4] * 10), [])
        self.assertEqual(S._linhas_demais(self._lado(), {}), [])

    def test_o_maximo_por_quadro_e_medido_e_sai_no_relatorio(self):
        """A média sozinha não responde a 'existe ALGUM quadro com faixa a
        mais', e era só ela que existia."""
        self.assertIn("text_rows_max", S.METRICS)
        self.assertIn("text_rows_max", S.REPORTED_ONLY)


# ──────────────────────────────────────────────────────── o corte, no arquivo

def _fonte_de_vlog(dir_, nome, *, com_legenda):
    """Um mp4 1920x1080 cuja legenda de acervo aparece por 0,4s, ou nunca.

    É a forma do defeito e não uma aproximação dele: `cut` amostra oito quadros
    da janela, e uma legenda que fica 0,4s de 4s cai em um deles. Um quadro em
    oito é exatamente o que o corte de 14/09 mediu.
    """
    from PIL import Image, ImageDraw
    def png(caminho, legenda):
        w, h = 1920, 1080
        im = Image.new("L", (w, h), 128)
        d = ImageDraw.Draw(im)
        for i in range(0, w, 90):
            d.line([(i, int(h * 0.25)), (i + 30, int(h * 0.75))], fill=190, width=4)
        if legenda:
            font = S._font(h // 16)
            texto = "entao eu peguei a camera e sai"
            largura = d.textlength(texto, font=font)
            d.text(((w - largura) / 2, h - int(h * 0.14)), texto, font=font,
                   fill=255, stroke_width=6, stroke_fill=0)
        Image.merge("RGB", (im, im, im)).save(caminho)
        return caminho
    limpo = png(os.path.join(dir_, f"{nome}-limpo.png"), False)
    saida = os.path.join(dir_, f"{nome}.mp4")
    args = ["ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", "30", "-i", limpo]
    if com_legenda:
        args += ["-loop", "1", "-framerate", "30", "-i",
                 png(os.path.join(dir_, f"{nome}-cap.png"), True),
                 "-filter_complex", "[0][1]overlay=0:0:enable='between(t,1.0,1.4)'"]
    args += ["-t", "5", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", saida]
    subprocess.run(args, check=True, capture_output=True)
    return saida


class OLadoSeguroECobrir(unittest.TestCase):
    """Cobrir material limpo custa um degradê. Não cobrir custa o clipe."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        if not S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")
        self.dir = _temp(self, prefix="warden-acervo-corte-")
        self.r = rules(video={"duration_min_s": 1, "duration_max_s": 5,
                              "width": 1080, "height": 1920, "audio": "forbidden"})

    def _corte(self, fonte, nome, **kw):
        return M.cut(fonte, os.path.join(self.dir, nome), self.r,
                     start=0, end=4, sound="platform", crop="center", **kw)

    def test_material_suspeito_ganha_o_degrade(self):
        """A detecção diz `bottom: false` e `bottom_suspect: true`, e antes
        deste conserto o `false` sozinho decidia: nada era coberto."""
        fonte = _fonte_de_vlog(self.dir, "vlog", com_legenda=True)
        visto = S.burned_text_bands(S.sample_frames(fonte, 0, 4, count=8))
        self.assertFalse(visto["bottom"], visto["evidence"])
        self.assertTrue(visto["bottom_suspect"], visto["evidence"])
        r = self._corte(fonte, "coberto.mp4", cover_footer=True)
        self.assertTrue(r["style"]["footer_covered"], r["notes"])
        self.assertTrue(r["style"]["source_text"]["bottom_suspect"])
        self.assertTrue(any("MAY already carry burned text" in n
                            for n in r["notes"]), r["notes"])

    def test_material_limpo_continua_sem_degrade(self):
        """O mesmo teste pelo avesso, e o que impede o conserto de virar um
        degradê carimbado em todo clipe."""
        fonte = _fonte_de_vlog(self.dir, "limpo", com_legenda=False)
        visto = S.burned_text_bands(S.sample_frames(fonte, 0, 4, count=8))
        self.assertFalse(visto["bottom_suspect"], visto["evidence"])
        r = self._corte(fonte, "sem-degrade.mp4", cover_footer=True)
        self.assertFalse(r["style"]["footer_covered"], r["notes"])


class DizEmVozAlta(unittest.TestCase):
    """"Limpo" e "nem olhei" eram a mesma saída: nenhuma.

    A nota sobre o material morava dentro do `if source_text.get("bottom")`, e
    com `false` o `cut` não dizia nada. Quem leu a saída do corte de 14/09 não
    tinha como saber que a detecção tinha rodado, muito menos com que números.
    """

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        # Queimar legenda pede um ffmpeg com libass. A imagem tem; um ffmpeg de
        # Homebrew normalmente não, e nestes dois testes a legenda queimada é o
        # gatilho da nota, então aqui não há como medir sem ela.
        _subtitles_filter_or_skip()
        if not S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")
        self.dir = _temp(self, prefix="warden-acervo-nota-")
        self.srt = os.path.join(self.dir, "c.srt")
        with open(self.srt, "w", encoding="utf-8") as fh:
            fh.write(M.to_srt([{"start": 0.0, "end": 2.0,
                                "text": "uma fala que vai para a tela"}]))
        S.write_approval(self.srt)
        self.r = rules(video={"duration_min_s": 1, "duration_max_s": 5,
                              "width": 1080, "height": 1920, "audio": "forbidden"})

    def _corte(self, fonte, nome, **kw):
        return M.cut(fonte, os.path.join(self.dir, nome), self.r, start=0, end=4,
                     sound="platform", crop="center", caption_srt=self.srt, **kw)

    def test_a_nota_sai_mesmo_quando_o_material_esta_limpo(self):
        fonte = _fonte_de_vlog(self.dir, "limpo", com_legenda=False)
        r = self._corte(fonte, "corte-limpo.mp4")
        nota = [n for n in r["notes"] if "footage checked before burning" in n]
        self.assertEqual(len(nota), 1, r["notes"])
        self.assertIn("found nothing down there", nota[0])
        # E com os números, não só com a conclusão: sem eles a nota é uma
        # opinião, e a opinião é justamente o que falhou.
        self.assertIn("frames", nota[0])

    def test_suspeito_e_queimando_legenda_cobre_o_rodape_e_diz(self):
        fonte = _fonte_de_vlog(self.dir, "vlog", com_legenda=True)
        r = self._corte(fonte, "corte-vlog.mp4")
        self.assertTrue(r["style"]["footer_covered"], r["notes"])
        nota = [n for n in r["notes"] if "footage checked before burning" in n]
        self.assertEqual(len(nota), 1, r["notes"])
        self.assertIn("covered the bottom", nota[0])
        # E o portão do sidecar não reprova o que foi coberto.
        self.assertEqual([m for m in r["style_breaches"] if "burned text" in m
                          or "signs of its own" in m], [], r["style_breaches"])


if __name__ == "__main__":
    unittest.main()
