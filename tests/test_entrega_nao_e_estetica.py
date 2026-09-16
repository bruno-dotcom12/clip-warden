"""Só gancho e legenda param uma entrega. O resto é observação.

Decisão do dono, 16/09/2026, no degrau 4 do vídeo de demonstração.

O que aconteceu: pediram dois clipes de 20s com legenda de
https://www.youtube.com/watch?v=aRVv5NLVRwE. Os dois renderizaram. Os dois
tinham `hook.png` e `.ass`. A fonte é tela dividida e deixa uma faixa escura em
duas bordas; `cross_check` leu a faixa como moldura e reprovou os dois. O
`lote render` saiu com código 1, `# 0 of 2 cleared for delivery`, e a pessoa
não recebeu nada -- com os dois arquivos prontos no disco, em
`logs-demo/clipes/corte-3b41505f01-01.mp4` e `-02.mp4`.

É a segunda vez que a mesma troca custa uma entrega. Em 15/09 o portão reprovou
e o agente APAGOU o que a pessoa pediu para calá-lo: entregou dois cortes sem
legenda e sem hook, dizendo que estavam prontos. Um portão que só diz "não"
ensina a tirar coisas ou a não entregar nada -- e nas duas vezes quem pediu
ficou sem o clipe.

A regra agora:

    obrigação  -- gancho e legenda. Sem uma delas não é o produto.
    observação -- toda conferência visual ou de estilo. O clipe SAI, e o agente
                  diz em uma frase o que a ferramenta notou.

E é exatamente por só essas duas pararem que apagá-las nunca é a saída: o
caminho de menor resistência deixou de existir.

Regra escrita no briefing de uma campanha continua bloqueando, por outro
caminho (`blocking`, em warden.py), que só existe quando há campanha ligada.
"""

import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "warden-shared", "scripts"))

import warden_style as S  # noqa: E402


def _fonte_dividida(caminho, segundos=4, faixa=200):
    """Uma fonte como a do demo: tela dividida e faixa escura em duas bordas.

    Duas fontes coladas uma sobre a outra, com `faixa` pixels de preto no pé e
    na esquerda -- que é o que `cross_check` lê como moldura.
    """
    largura, altura = 1080 - faixa, 1920 - faixa
    filtro = (
        f"testsrc2=size={largura}x{altura // 2}:rate=30[a];"
        f"testsrc=size={largura}x{altura // 2}:rate=30[b];"
        f"[a][b]vstack=inputs=2,pad=1080:1920:{faixa}:0:black"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"nullsrc=size=16x16:rate=30", "-filter_complex", filtro,
         "-t", str(segundos), "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", caminho],
        check=True,
    )
    return caminho


class FaixaEscuraNaoSeguraOClipe(unittest.TestCase):
    """O caso do demo, na unidade que o decidiu."""

    def test_faixa_preta_em_duas_bordas_e_observacao_e_nao_reprovacao(self):
        achados = S.cross_check(
            {"caption": {"cues": 3}, "hook": {"complete": True}},
            {"black_bars": {"top": 0.0, "bottom": 0.195,
                            "left": 0.107, "right": 0.0}},
        )
        barras = [(lv, m) for lv, m in achados if "black bar" in m]
        self.assertTrue(barras, achados)
        nivel, mensagem = barras[0]
        self.assertEqual(
            nivel, S.OBSERVACAO,
            "a faixa escura voltou a REPROVAR. Foi ela que segurou os dois "
            "clipes do degrau 4 do demo, ambos com gancho e legenda prontos.")
        self.assertIn("bottom 19.5%", mensagem)

    def test_nada_do_que_e_olhar_reprova(self):
        """A varredura inteira, não um portão de cada vez.

        Se alguém acrescentar um portão visual amanhã e o marcar como
        OBRIGACAO, este teste cai -- que é o ponto. A lista de quem pode
        barrar uma entrega é curta e fechada.
        """
        lado = {
            "hook": {"width_px": 2000, "usable_px": 854, "lines": 4,
                     "complete": True, "top_px": 10, "seconds_on_screen": 19.0},
            "caption": {"cues": 5, "max_lines": 6, "max_cue_s": 9.9,
                        "karaoke": False, "bottom_px": 1800},
            "duration_s": 20.0, "motion": False, "footer_covered": False,
            "source_text": {"bottom": True},
        }
        niveis = {lv for lv, _m in S.check_sidecar(lado)}
        self.assertNotIn(
            S.OBRIGACAO, niveis,
            "um portão VISUAL voltou a bloquear a entrega: %r"
            % [m for lv, m in S.check_sidecar(lado) if lv == S.OBRIGACAO])


class GanchoELegendaContinuamObrigatorios(unittest.TestCase):
    """O outro lado. Afrouxar o visual não pode afrouxar o produto."""

    def test_gancho_truncado_continua_bloqueando(self):
        achados = S.check_sidecar(
            {"hook": {"complete": False, "chars": 180, "size_px": 48},
             "caption": {"cues": 4, "max_lines": 2, "max_cue_s": 2.0,
                         "karaoke": True}})
        self.assertTrue(
            [m for lv, m in achados if lv == S.OBRIGACAO],
            "um hook que não cabe na tela deixou de bloquear: %r" % achados)

    def test_arquivo_ilegivel_continua_bloqueando(self):
        # `check_against`, e não `check_sidecar`: quem lê o ARQUIVO é ele.
        # Nenhum quadro aberto não é um clipe torto, é a ausência de clipe --
        # e por isso continua sendo a única coisa além de gancho e legenda que
        # para uma entrega.
        achados = S.check_against({"measured": False}, {})
        self.assertTrue(
            [m for lv, m in achados if lv == S.OBRIGACAO],
            "um arquivo do qual nenhum quadro abre não é um clipe: %r" % achados)

    def test_a_lista_de_quem_bloqueia_e_curta_e_nomeada(self):
        """Quem pode barrar uma entrega, por nome, para não crescer sozinho."""
        self.assertEqual(S.OBRIGACAO, "REJECT")
        self.assertEqual(S.OBSERVACAO, "WARN")
        self.assertNotEqual(S.OBRIGACAO, S.OBSERVACAO)


class ORenderDizOQueViuSemSegurarOClipe(unittest.TestCase):
    """Ponta a ponta, com um arquivo de verdade: o caso do demo."""

    @classmethod
    def setUpClass(cls):
        if subprocess.run(["which", "ffmpeg"],
                          capture_output=True).returncode != 0:
            raise unittest.SkipTest("sem ffmpeg")
        cls.dir = tempfile.mkdtemp(prefix="warden-entrega-")
        cls.fonte = _fonte_dividida(os.path.join(cls.dir, "dividida.mp4"))

    def test_o_clipe_sai_e_a_faixa_vira_LOOK_nas_notas(self):
        import warden_media as M
        r = M.cut(self.fonte, os.path.join(self.dir, "corte.mp4"), {},
                  start=0, end=3, sound="platform")
        self.assertTrue(os.path.exists(r["out"]),
                        "o arquivo não foi escrito")
        self.assertEqual(
            r.get("style_breaches") or [], [],
            "a conferência visual voltou a produzir bloqueio: %r"
            % r.get("style_breaches"))
        self.assertTrue(
            any(n.startswith("LOOK:") for n in r["notes"]),
            "nada foi observado numa fonte com faixa escura e tela dividida; "
            "entregar calado é o outro jeito de errar: %r" % r["notes"])


if __name__ == "__main__":
    unittest.main()
