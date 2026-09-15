"""Onde a cue quebra quando a fonte não traz pontuação nenhuma.

Toda legenda que este projeto recebe de graça -- música, live, podcast
transcrito, legenda automática do YouTube -- chega sem um ponto final sequer.
Medido em 15/09, num container limpo, sobre um link cuja legenda publicada é
letra de música: o clipe renderizou e o portão de entrega o reprovou, certo:

    STYLE REJECT: 2 caption cue(s) end on a word that needs what comes next
    (…de você); …de você)). That is the 'HATE THE' and 'CARA, EU VOTARIA NO'
    of 14/09

No mosaico o defeito se via a olho nu -- "Nunca" sozinho, "Eu" pendurado no
fim, "Nunca vou te fazer" sem objeto -- e no `.ass` a cue 1 fechava em
`(Desistir` e a cue 2 abria em `de você)`: um parêntese aberto pendurado numa
cue e o fechamento órfão na outra.

O que esta suíte cobre é o efeito prático disso: uma fonte sem pontuação
entregava ZERO clipes. Cada caso aqui é um quadro daquele mosaico.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden_style as S


# A letra medida, exatamente como a legenda publicada a entrega: seis linhas,
# nenhuma pontuação, cada linha um bloco do SRT.
LETRA = [
    "Nunca vou desistir de você",
    "Nunca vou te decepcionar",
    "Eu nunca vou fugir nem te abandonar",
    "Nunca vou te fazer chorar",
    "Nunca vou dizer adeus",
    "Nunca vou mentir nem te magoar",
]


def segmentos(linhas, comeca=0.0, por_linha=2.6):
    """Um SRT como o do YouTube: um bloco por linha, blocos colados.

    Colados de propósito -- `_merge_spans` junta o que está a menos de 0,4s e é
    assim que a frase volta a ser uma frase antes de ser cortada.
    """
    out, t = [], comeca
    for linha in linhas:
        # Cada linha dura na proporção do que se fala nela; uma linha de sete
        # sílabas não ocupa o mesmo tempo que uma de doze.
        peso = sum(S.silabas(w) for w in linha.split())
        dur = round(por_linha * peso / 9.0, 3)
        out.append({"start": round(t, 3), "end": round(t + dur, 3),
                    "text": linha})
        t += dur
    return out


class LetraSemPontuacao(unittest.TestCase):
    """A letra medida em 15/09, quebrada sem um único sinal de pontuação."""

    def setUp(self):
        self.cues = S.reflow_cues(segmentos(LETRA))
        self.textos = [c["text"] for c in self.cues]

    def test_nenhuma_cue_pendurada(self):
        """O portão não teria o que reprovar.

        `finais_pendurados` é exatamente o que o sidecar entrega ao
        `check_sidecar`, então zero aqui é zero REJECT lá.
        """
        self.assertEqual(
            S.finais_pendurados(self.cues, continua_depois=False), [],
            "cues: %r" % (self.textos,))

    def test_nenhuma_cue_fecha_em_palavra_que_pede_complemento(self):
        """O mesmo, dito palavra a palavra, para a falha nomear a culpada.

        A última cue é a única perdoada, e só porque a fala acaba ali -- é a
        mesma exceção que `finais_pendurados` faz. A outra exceção é o pronome
        depois de preposição ("…desistir de você"): o que ele pedia já está na
        cue, então ele não pendura nada.
        """
        for texto in self.textos[:-1]:
            palavras = texto.split()
            if S._fecha_apesar_da_lista(palavras, len(palavras) - 1):
                continue
            ultima = S._palavra(palavras[-1])
            self.assertNotIn(ultima, S.NAO_FECHA_CUE,
                             "%r fecha em %r; cues: %r"
                             % (texto, ultima, self.textos))

    def test_a_excecao_do_pronome_e_estreita(self):
        """"de você" fecha; "você" sozinho no fim de uma oração não fecha.

        A exceção existe para uma cue que sempre esteve certa, e não pode
        virar uma porta: exige a preposição imediatamente antes.
        """
        self.assertTrue(S._fecha_apesar_da_lista(["desistir", "de", "você"], 2))
        self.assertFalse(S._fecha_apesar_da_lista(["cara", "eu"], 1))
        self.assertFalse(S._fecha_apesar_da_lista(["e", "você"], 1))
        self.assertEqual(
            S.finais_pendurados([{"text": "olha o que você"},
                                 {"text": "fez com a gente"}],
                                continua_depois=False),
            ["olha o que você"])

    def test_os_quadros_do_mosaico(self):
        """As palavras em que o contact sheet de 15/09 mostrou a cue fechando.

        "Nunca" sozinho, "Eu" pendurado no fim, o auxiliar "vou" sem o
        particípio, e "fazer" sem objeto. Não basta contar: estes são o defeito
        COM nome, e é assim que uma regressão diz o que voltou a quebrar em vez
        de dizer "um a mais".

        "fazer" é o que a lista de palavras NÃO pega -- nenhuma lista razoável
        proíbe fechar em infinitivo -- e por isso ele é a prova do plano B:
        sem pontuação, a quebra tem de preferir fechar ANTES de "Nunca", que é
        onde o próximo sintagma começa.
        """
        for ruim in ("nunca", "eu", "vou", "fazer", "te", "nem"):
            fecham = [t for t in self.textos
                      if S._palavra(t.split()[-1]) == ruim]
            self.assertEqual(fecham, [],
                             "cue fechando em %r; cues: %r"
                             % (ruim, self.textos))

    def test_a_fala_inteira_continua_na_tela(self):
        """Nenhuma palavra se perde na quebra, que é a regra do reflow."""
        self.assertEqual(" ".join(self.textos), " ".join(LETRA))

    def test_limites_de_tela_e_de_tempo_valem_igual(self):
        """Consertar a fronteira não pode pagar com um bloco parado no rosto."""
        for cue in self.cues:
            self.assertLessEqual(len(cue["lines"]), S.MAX_LINES,
                                 "%r em %d linhas" % (cue["text"],
                                                      len(cue["lines"])))
            for linha in cue["lines"]:
                self.assertLessEqual(len(linha), S.MAX_CHARS_PER_LINE,
                                     "linha de %d caracteres: %r"
                                     % (len(linha), linha))
            self.assertLessEqual(round(cue["end"] - cue["start"], 2),
                                 S.MAX_CUE_S_TETO + 0.2,
                                 "%r fica %.2fs na tela"
                                 % (cue["text"], cue["end"] - cue["start"]))


class Delimitadores(unittest.TestCase):
    """`(Desistir de você)` partido no meio, que é o caso do `.ass` medido."""

    # A linha que o YouTube publica com o vocal de apoio entre parênteses.
    LINHA = ["nunca vou desistir (Desistir de você)"]

    def setUp(self):
        self.cues = S.reflow_cues(segmentos(self.LINHA, por_linha=2.3))
        self.textos = [c["text"] for c in self.cues]

    def test_o_grupo_nao_se_parte(self):
        """Ou cabe inteiro na cue, ou vai inteiro para a seguinte."""
        for texto in self.textos:
            if "(" in texto or ")" in texto:
                self.assertIn("(Desistir de você)", texto,
                              "grupo partido; cues: %r" % (self.textos,))

    def test_nenhuma_cue_fecha_com_delimitador_aberto(self):
        """Foi o que o `.ass` de 15/09 fazia: cue 1 fechava em `(Desistir`."""
        for texto in self.textos:
            self.assertEqual(texto.count("("), texto.count(")"),
                             "parêntese pendurado em %r" % (texto,))

    def test_o_portao_acusa_o_parentese_pendurado(self):
        """A cue de 15/09 que ninguém pegou: fechava em `(Desistir`.

        "desistir" não está em lista nenhuma, então a regra escrita passava por
        cima. Quem reprovou o clipe foi a cue SEGUINTE, por sorte -- ela
        fechava em "você)". Agora a acusação é direta.
        """
        ruins = S.finais_pendurados(
            [{"text": "nunca vou desistir (Desistir"},
             {"text": "de você)"}], continua_depois=False)
        self.assertEqual(ruins, ["nunca vou desistir (Desistir"])

    def test_aspas_tambem_sao_uma_unidade(self):
        """Mesma regra, mesmo motivo: metade de uma citação não diz nada."""
        linha = ['ele me olhou e disse "eu nunca mais volto aqui" e foi embora']
        cues = S.reflow_cues(segmentos(linha, por_linha=2.0))
        textos = [c["text"] for c in cues]
        for texto in textos:
            self.assertEqual(texto.count('"') % 2, 0,
                             "aspa pendurada em %r (cues: %r)" % (texto, textos))

    def test_grupo_maior_que_a_tela_nao_trava_o_reflow(self):
        """Um parêntese que não cabe em duas linhas ainda tem de terminar.

        A regra é "não parta"; quando partir é a única saída, ela cede e o
        reflow segue. O que não pode é rodar para sempre.
        """
        linha = ["olha (isto aqui é um comentário muito longo entre "
                 "parênteses que não cabe de jeito nenhum em duas linhas) pronto"]
        cues = S.reflow_cues(segmentos(linha, por_linha=12.0))
        self.assertTrue(cues)
        self.assertEqual(" ".join(c["text"] for c in cues), linha[0])


class PontuacaoContinuaMandando(unittest.TestCase):
    """Não-regressão: onde há pontuação, ela continua sendo a fronteira."""

    def test_a_virgula_e_o_ponto_cortam(self):
        linhas = ["Eu fiz o teste ontem, e deu certo.",
                  "Não era o que eu esperava, mas serve."]
        cues = S.reflow_cues(segmentos(linhas, por_linha=3.0))
        textos = [c["text"] for c in cues]
        self.assertTrue(any(t.endswith(",") or t.endswith(".") for t in textos),
                        "nenhuma cue fechou na pontuação: %r" % (textos,))
        self.assertEqual(S.finais_pendurados(cues, continua_depois=False), [],
                         "cues: %r" % (textos,))

    def test_o_fragmento_de_14_09_continua_proibido(self):
        """A lista não pode ter afrouxado no caminho."""
        for palavra in ("no", "the", "que", "de", "e", "para"):
            self.assertIn(palavra, S.NAO_FECHA_CUE)

    def test_o_ponto_final_perdoa_nos_dois_lugares(self):
        """A mesma regra escrita em dois arquivos são duas regras.

        `_fronteiras` perdoa ponto final desde sempre -- "…não sei o que."
        fecha uma ideia de verdade -- e `finais_pendurados` não perdoava.
        Medido em 15/09 sobre fala comum e pontuada: o reflow fechava em
        "ninguém aguenta mais.", fazendo o que lhe mandaram, e o relatório
        acusava a cue porque "mais" está na lista. Vírgula continua não
        perdoando: o complemento vem logo depois dela.
        """
        self.assertEqual(
            S.finais_pendurados([{"text": "ninguém aguenta mais."},
                                 {"text": "e acabou"}],
                                continua_depois=False), [])
        self.assertEqual(
            S.finais_pendurados([{"text": "ele me disse que,"},
                                 {"text": "no fundo, não queria"}],
                                continua_depois=False), ["ele me disse que,"])

    def test_um_pronome_solto_continua_pendurado(self):
        """"CARA, EU" sozinho é o defeito de 14/09 e segue sendo acusado."""
        ruins = S.finais_pendurados(
            [{"text": "cara, eu"}, {"text": "votaria no MBL"}],
            continua_depois=False)
        self.assertEqual(ruins, ["cara, eu"])


class PortaoDeEstilo(unittest.TestCase):
    """O verificador de estilo sobre o resultado do reflow: zero REJECT de cue."""

    def sidecar(self, cues):
        """O pedaço de `-estilo.json` que `warden_media` escreve sobre a legenda."""
        return {
            "duration_s": round(cues[-1]["end"], 2),
            "caption": {
                "cues": len(cues),
                "max_lines": max(len(c["lines"]) for c in cues),
                "max_cue_s": round(max(c["end"] - c["start"] for c in cues), 2),
                "renderer": "ass", "karaoke": True,
                "hanging_endings": S.finais_pendurados(cues,
                                                       continua_depois=False),
                "ends_mid_sentence": False,
                "sentence_closes_at_s": None,
            },
        }

    def test_a_letra_passa_no_portao(self):
        cues = S.reflow_cues(segmentos(LETRA))
        rejeitos = [m for nivel, m in S.check_sidecar(self.sidecar(cues))
                    if nivel == "REJECT"]
        self.assertEqual(rejeitos, [],
                         "cues: %r" % ([c["text"] for c in cues],))

    def test_o_portao_continua_reprovando_cue_ruim(self):
        """O conserto é a cue sair boa, nunca o portão aceitar cue ruim."""
        side = {"duration_s": 12.0,
                "caption": {"cues": 2, "max_lines": 2, "max_cue_s": 2.0,
                            "renderer": "ass", "karaoke": True,
                            "hanging_endings": ["O JOGO. DON'T HATE THE",
                                                "CARA, EU VOTARIA NO"],
                            "ends_mid_sentence": False}}
        rejeitos = [m for nivel, m in S.check_sidecar(side) if nivel == "REJECT"]
        self.assertTrue(any("end on a word that needs" in m for m in rejeitos),
                        rejeitos)


if __name__ == "__main__":
    unittest.main()


class AExcecaoDaPreposicaoNaoDeixaPassarCueRuim(unittest.TestCase):
    """Os três casos de 15/09 em que a exceção assinava legenda ruim.

    Uma revisão adversarial rodou o `reflow_cues` de verdade com e sem
    `_fecha_apesar_da_lista` e mostrou que a exceção PIORAVA a quebra em três
    formas. As três guardas que entraram saem daqui, e este teste é o que
    impede que alguém as tire por acharem-nas exageradas.
    """

    def cues(self, texto):
        palavras = texto.split()
        segs = [{"start": i * 0.4, "end": (i + 1) * 0.4, "word": w}
                for i, w in enumerate(palavras)]
        saida = S.reflow_cues([{"start": 0.0, "end": len(palavras) * 0.4,
                                "text": texto, "words": segs}])
        return [c["text"] if isinstance(c, dict) else c for c in saida]

    def test_pronome_sujeito_de_infinitivo_nao_fecha_a_cue(self):
        # "para você ENTENDER": o pronome é sujeito do infinitivo seguinte, e
        # fechar ali deixava "entender" órfão.
        primeira = self.cues("tudo de novo para voce entender de uma vez")[0]
        self.assertIn("entender", primeira,
                      "a cue fechou em 'voce' e deixou o infinitivo órfão")

    def test_par_coordenado_nao_e_partido_no_meio(self):
        # "entre ele | e ela" partia "entre ele e ela".
        for cue in self.cues("a briga toda comecou entre ele e ela naquela noite"):
            self.assertFalse(cue.strip().endswith("ele"),
                             "fechou em 'ele' e partiu o par coordenado")

    def test_preposicao_de_uma_lingua_nao_casa_com_pronome_de_outra(self):
        # `as`(pt) + `you`(en) fechava "…that as you", que é o 'HATE THE'.
        for cue in self.cues("I really think that as you can see the numbers"):
            self.assertFalse(cue.strip().endswith("you"),
                             "casou preposição portuguesa com pronome inglês")

    def test_o_caso_que_a_excecao_existe_para_permitir_continua_valendo(self):
        # Sem a exceção, letra de música não tem fronteira utilizável nenhuma.
        self.assertTrue(
            S._fecha_apesar_da_lista(["desistir", "de", "voce"], 2),
            "a exceção deixou de valer no caso que a justifica")


class MarcacaoDecorativaNaoVaiParaATela(unittest.TestCase):
    """Medido em 15/09: o símbolo musical da legenda automática era QUEIMADO.

    Visto no contact sheet de um clipe real, não deduzido do código.
    """

    NOTA = "♪"

    def test_o_simbolo_musical_sai(self):
        self.assertEqual(
            S._limpa_marcacao("Never gonna let you down %s %s" % (self.NOTA, self.NOTA)),
            "Never gonna let you down")

    def test_o_parentese_de_letra_de_verdade_FICA(self):
        # Sai o símbolo, não o parêntese: "(Give you up)" é letra, e parêntese
        # virou unidade indivisível em 15/09.
        self.assertEqual(
            S._limpa_marcacao("(Give you up) %s" % self.NOTA), "(Give you up)")

    def test_rotulo_de_som_entre_colchetes_sai(self):
        self.assertEqual(S._limpa_marcacao("fala normal [Music] continua"),
                         "fala normal continua")
        self.assertEqual(S._limpa_marcacao("fala [Aplausos] segue"), "fala segue")

    def test_cue_que_era_so_ruido_deixa_de_existir(self):
        self.assertEqual(S._limpa_marcacao("%s %s" % (self.NOTA, self.NOTA)), "")

    def test_a_limpeza_acontece_antes_de_montar_as_cues(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "ola %s mundo" % self.NOTA,
                 "words": [{"start": 0.0, "end": 0.4, "word": "ola"},
                           {"start": 0.4, "end": 0.6, "word": self.NOTA},
                           {"start": 0.6, "end": 1.0, "word": "mundo"}]}]
        for cue in S.reflow_cues(segs):
            texto = cue["text"] if isinstance(cue, dict) else cue
            self.assertNotIn(self.NOTA, texto)


class AListaCobreOInglesDoQueFoiVistoNaTela(unittest.TestCase):
    """As palavras que fecharam cue penduradas num clipe inglês APROVADO."""

    def test_as_palavras_do_mosaico_estao_na_lista(self):
        for palavra in ("each", "going", "can", "gonna", "what", "how"):
            self.assertIn(palavra, S.NAO_FECHA_CUE,
                          "%r fechou cue pendurada num clipe entregue" % palavra)

    def test_o_portugues_nao_regrediu(self):
        for palavra in ("de", "para", "vou", "nunca", "que"):
            self.assertIn(palavra, S.NAO_FECHA_CUE)


class PronomeObjetoDepoisDeVerboFechaBem(unittest.TestCase):
    """O falso positivo que derrubou um lote inteiro em 15/09.

    Depois de apertar a exceção, o portão passou a reprovar "…tell a lie and
    hurt you" -- que é uma frase COMPLETA. O pronome objeto tinha o que pedia
    atrás dele (o verbo), exatamente como em "…de você" tem a preposição. Os
    dois clipes do lote foram recusados por isso, e um portão que reprova clipe
    certo custa o mesmo que um que aprova clipe errado.
    """

    def test_pronome_depois_de_verbo_fecha(self):
        self.assertTrue(
            S._fecha_apesar_da_lista("tell a lie and hurt you".split(), 5))

    def test_mas_depois_de_palavra_que_pede_o_proximo_NAO_fecha(self):
        # `as` está na lista de quem pede o que vem depois; `hurt` não. É essa
        # a diferença, e é a única.
        self.assertFalse(
            S._fecha_apesar_da_lista("think that as you can".split(), 3))

    def test_e_as_tres_guardas_continuam_de_pe(self):
        self.assertTrue(S._fecha_apesar_da_lista("desistir de voce".split(), 2))
        self.assertFalse(S._fecha_apesar_da_lista("para voce entender".split(), 1))
        self.assertFalse(S._fecha_apesar_da_lista("entre ele e ela".split(), 1))
