# -*- coding: utf-8 -*-
"""A fonte longa é MEDIDA antes de ser lida, e só os picos vão ao transcritor.

POR QUE ESTE ARQUIVO EXISTE, e ele é o conserto de uma falha medida em
17/09/2026 na nuvem da Plow: o dono mandou um VOD de live para `lote prep`, o
passo da transcrição recebeu a fonte inteira e o transcritor ESTOUROU A
MEMÓRIA. A resposta que ele recebeu foi "rodando de novo, o transcritor
estourou memória na primeira tentativa". Não foi demora -- foi o passo não
escalar com a duração, e o resultado é clipe nenhum.

A conta, com os números que `warden_media` já mediu: o `base` faz 120s de áudio
em 15,4s, ou ~7,8x o tempo real. Três horas de VOD são ~23 minutos só de
transcrição, e é na mesma faixa que a memória acaba. Seis janelas de três
minutos são 18 minutos de áudio, ~2,3 minutos de transcrição, com a memória
constante porque cada janela é recortada e lida sozinha.

Tudo aqui é aritmética sobre uma lista de (segundo, LUFS). Nenhum destes testes
abre vídeo, chama ffmpeg ou carrega whisper -- é a mesma regra do
`ADecisaoEAritmetica`: uma regra que só se exercita com três dependências e um
arquivo de 400 MB é uma regra que ninguém confere.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden_media as M


def envelope(trechos, duracao, passo=0.1):
    """Um envelope sintético: [(t, LUFS)] a cada `passo`.

    `trechos` é [(de, ate, lufs)]. Fora deles o silêncio de fundo é -40, que é
    ruído de sala e não o -70 que a função descarta como "sem leitura".
    """
    env = []
    t = 0.0
    while t < duracao:
        valor = -40.0
        for de, ate, lufs in trechos:
            if de <= t < ate:
                valor = lufs
                break
        env.append((round(t, 2), valor))
        t += passo
    return env


class OsPicosViramJanelas(unittest.TestCase):

    def test_sem_envelope_nao_inventa_janela(self):
        """Vazio é "não deu para medir", e quem chama segue pelo caminho
        inteiro. NUNCA é "não há nada bom nesta fonte"."""
        self.assertEqual(M.picos_para_janelas([]), [])
        self.assertEqual(M.picos_para_janelas(None), [])

    def test_fonte_curta_nao_e_recortada(self):
        """Se a fonte cabe numa janela, não há o que escolher."""
        env = envelope([], duracao=100)
        self.assertEqual(M.picos_para_janelas(env, janela_s=180, duracao=100), [])

    def test_o_trecho_alto_e_escolhido(self):
        """Uma hora de fonte com um pico aos 30 min: a janela cai nele."""
        env = envelope([(1800, 1980, -10.0)], duracao=3600)
        janelas = M.picos_para_janelas(env, quantos=1, janela_s=180,
                                       duracao=3600)
        self.assertEqual(len(janelas), 1)
        ini, fim = janelas[0]
        self.assertLessEqual(ini, 1800, "a janela tem de começar no pico ou antes")
        self.assertGreaterEqual(fim, 1980 - 1, "e cobrir o pico")

    def test_energia_sustentada_ganha_de_estouro_de_microfone(self):
        """A MÉDIA e não o máximo, e este teste prende essa escolha.

        Um estouro de um décimo de segundo tem o maior pico da fonte inteira e
        não é momento nenhum. Três minutos de sala alta têm a maior média. Se
        alguém trocar a média por máximo, este caso reprova.
        """
        env = envelope([(100, 100.2, 0.0),          # estouro, 0,2s
                        (2000, 2180, -12.0)],       # sala alta, 3 min
                       duracao=3600)
        janelas = M.picos_para_janelas(env, quantos=1, janela_s=180,
                                       duracao=3600)
        ini, fim = janelas[0]
        self.assertGreater(ini, 1000,
                           "escolheu o estouro em vez da energia sustentada")

    def test_as_janelas_nao_se_sobrepoem(self):
        env = envelope([(600, 900, -8.0), (610, 910, -8.5), (2400, 2700, -9.0)],
                       duracao=3600)
        janelas = M.picos_para_janelas(env, quantos=6, janela_s=180,
                                       duracao=3600)
        for i in range(len(janelas) - 1):
            self.assertLessEqual(janelas[i][1], janelas[i + 1][0],
                                 "duas janelas se cruzam: o mesmo áudio seria "
                                 "transcrito duas vezes")

    def test_saem_em_ordem_de_tempo(self):
        """Ordenadas pelo relógio da fonte, não pela altura -- quem lê o texto
        depois precisa dele em ordem para entender a conversa."""
        env = envelope([(3000, 3180, -5.0), (600, 780, -6.0), (1800, 1980, -7.0)],
                       duracao=3600)
        janelas = M.picos_para_janelas(env, quantos=3, janela_s=180,
                                       duracao=3600)
        self.assertEqual(janelas, sorted(janelas))

    def test_o_teto_de_candidatos_e_respeitado(self):
        env = envelope([(t, t + 180, -10.0) for t in range(0, 3600, 400)],
                       duracao=3600)
        self.assertLessEqual(len(M.picos_para_janelas(env, quantos=4,
                                                      janela_s=180,
                                                      duracao=3600)), 4)

    def test_o_orcamento_de_transcricao_cai_de_verdade(self):
        """O ponto inteiro da mudança, em números.

        Seis janelas de três minutos são 18 min de áudio. Contra as 3 horas da
        fonte, é menos de 11% -- e é a diferença entre ~23 minutos de
        transcrição (que estourou a memória) e ~2,3 minutos.
        """
        env = envelope([(t, t + 120, -10.0) for t in range(0, 10800, 900)],
                       duracao=10800)
        janelas = M.picos_para_janelas(env, quantos=6, janela_s=180,
                                       duracao=10800)
        coberto = sum(fim - ini for ini, fim in janelas)
        self.assertLessEqual(coberto, 0.15 * 10800,
                             "a varredura tem de ler uma fração pequena da fonte")
        self.assertGreater(coberto, 0, "e tem de ler alguma coisa")

    def test_silencio_absoluto_nao_vira_janela(self):
        """-70 ou abaixo é "sem leitura", não é som baixo."""
        env = [(t / 10.0, -80.0) for t in range(0, 36000)]
        self.assertEqual(M.picos_para_janelas(env, duracao=3600), [])

    def test_o_limiar_deixa_passar_fonte_de_vinte_minutos(self):
        """O limiar é sobre a espera, não sobre a quebra: 20 min de fonte são
        ~2,5 min de transcrição, que é aceitável e não precisa de varredura."""
        self.assertGreaterEqual(M.VARREDURA_LIMIAR_S, 900)
        self.assertLessEqual(M.VARREDURA_LIMIAR_S, 1800)


class AJanelaVoltaEmTempoDaFonte(unittest.TestCase):
    """O defeito que este desenho NÃO pode ter, preso por teste.

    Se a transcrição de uma janela devolvesse tempo relativo à janela, os
    clipes sairiam do lugar errado e PARECERIAM certos -- a pior classe de
    defeito que este projeto conhece. `transcribe` soma o offset (`s.start +
    offset`), e é disso que `transcreve_por_picos` depende para simplesmente
    concatenar. Este teste existe para quebrar no dia em que alguém tirar
    aquele `+ offset`.
    """

    def test_transcribe_soma_o_offset_da_janela(self):
        import inspect
        fonte = inspect.getsource(M.transcribe)
        self.assertIn("+ offset", fonte,
                      "transcribe parou de somar o offset da janela: os tempos "
                      "de `transcreve_por_picos` passam a ser da janela e não "
                      "da fonte, e todo clipe sai do lugar errado parecendo "
                      "certo")


if __name__ == "__main__":
    unittest.main()
