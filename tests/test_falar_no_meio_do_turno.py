"""Nenhum texto da imagem pode mandar falar no MEIO de um turno.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
Em 16/09/2026 uma entrega inteira -- prosa e dois MP4 de 16 MB -- foi engolida
pelo portão anti-duplicata do adaptador do Plow
(`/opt/hermes/plugins/plow_chat/__init__.py:2062-2065`), que devolve
`SendResult(success=True)` SEM ENVIAR quando o turno já foi marcado como
respondido. Quem marcou foi o próprio agente, mandando "On it." no meio do turno
com `plow_send_sequence`. Ele fechou o portão contra a própria entrega, 141
segundos antes dela.

O gatilho era uma frase num texto que ele lê. E a frase foi consertada QUATRO
VEZES, cada vez num lugar só, cada vez com o defeito sobrevivendo noutro:

    1ª  runtime/persona.md          -- consertada, e a skill continuou
    2ª  warden-shared/scripts/warden.py (saída do `lote render`) -- e a persona
        continuou dizendo o contrário 16 linhas abaixo da proibição
    3ª  warden-run/SKILL.md         -- a porta de entrada, achada por auditoria
    4ª  warden-clip/SKILL.md        -- e havia um TESTE exigindo a frase errada

Caçar cópia a cópia não termina: na quarta vez a própria suíte protegia o
defeito. Este arquivo troca a caçada por uma varredura. Ele lê TUDO que entra na
imagem -- a persona, as sete SKILL.md, os `references/`, e cada string que
`warden-shared/scripts/*.py` imprime -- e reprova a FORMA da instrução, não uma
frase específica.

A regra que ele guarda, numa linha: **prosa chega quando o TURNO termina.**
Mandar dizer algo "antes de" rodar um comando, ou dizer algo "e seguir", é
mandar falar entre duas chamadas de ferramenta -- e isso não chega, arma a
armadilha, e custa a entrega inteira.
"""

import ast
import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(RAIZ, "warden-shared", "scripts")

# A REGRA É SOBRE PROSA, NÃO SOBRE ORDEM ENTRE COMANDOS.
#
# A primeira versão desta varredura acusava "run `warden delivered` before you
# render anything" e "Read that before you cut one" -- as duas legítimas: rodar
# um comando antes de outro no mesmo turno é o trabalho normal. O que mata a
# entrega é PROSA entre duas chamadas de ferramenta.
#
# Então o gatilho é um VERBO DE DIZER perto de uma forma de "e siga". Sem o
# verbo de dizer não há acusação -- uma varredura que reprova quem está certo é
# desligada na semana seguinte, e aí não pega mais o peixe que importa.
# `tell(s) you` é o DOCUMENTO falando com o agente ("section 5 tells you to read
# this"), não o agente falando com a pessoa. Sem esta exclusão a varredura
# acusava um ponteiro entre arquivos.
DIZER = (r"(?:say|says|saying|tells? (?!you\b)|telling (?!you\b)|"
         r"send (?:one |a |ONE )?(?:short )?(?:line|message)|"
         r"write to (?:them|the person))")

FORMAS = (
    # warden-run/SKILL.md, a 3ª cópia: "send ONE short line ... BEFORE you run
    # a single command ... Then work without stopping."
    (DIZER + r"[^.]{0,120}?\bbefore you (?:run|start|render|cut)\b",
     "mandar falar ANTES de rodar"),
    (r"(?i)\bbefore you (?:run|start|render|cut)\b[^.]{0,120}?" + DIZER,
     "mandar falar ANTES de rodar"),
    # warden-package/SKILL.md: "say so before rendering a clip".
    (DIZER + r"[^.]{0,60}?\bbefore rendering\b", "mandar falar antes de renderizar"),
    # warden.py (lote prep), a 2ª: "Say those five lines ... and keep going."
    (DIZER + r"[^.]{0,160}?\bkeep going\b", "mandar falar e seguir"),
    (r"(?i)\bwork without stopping\b", "mandar falar e seguir"),
    # warden.py (lote prep): "then, in the SAME turn, pick N window(s) and run"
    (DIZER + r"[^.]{0,160}?in the SAME turn", "mandar falar e agir no mesmo turno"),
    # warden_media.py: "a long silence in a chat -- say so before you wait."
    (DIZER + r"[^.]{0,40}?\bbefore you wait\b", "mandar avisar antes de esperar"),
)

# O que TORNA a frase segura. Uma instrução de falar só é legítima quando diz,
# na mesma vizinhança, que a fala encerra o turno.
SEGURO = re.compile(
    r"(?i)(final message|last message|END (?:a|that|your) turn|ends that turn|"
    r"ENDS THAT TURN|never between two tool calls|on the next)")

JANELA = 260


def textos_da_imagem():
    """(rótulo, texto) de tudo que o Dockerfile copia e o agente lê."""
    saida = [("runtime/persona.md",
              open(os.path.join(RAIZ, "runtime", "persona.md"),
                   encoding="utf-8").read())]
    for nome in sorted(os.listdir(RAIZ)):
        pasta = os.path.join(RAIZ, nome)
        if not (nome.startswith("warden-") and os.path.isdir(pasta)):
            continue
        skill = os.path.join(pasta, "SKILL.md")
        if os.path.exists(skill):
            saida.append((f"{nome}/SKILL.md",
                          open(skill, encoding="utf-8").read()))
        refs = os.path.join(pasta, "references")
        if os.path.isdir(refs):
            for ref in sorted(os.listdir(refs)):
                if ref.endswith(".md"):
                    saida.append((f"{nome}/references/{ref}",
                                  open(os.path.join(refs, ref),
                                       encoding="utf-8").read()))
    return saida


def strings_impressas():
    """(rótulo, texto) de cada literal que os scripts do warden imprimem.

    A saída de ferramenta é o caso MAIS grave, e não o menos: a persona diz, com
    todas as letras, que o que a ferramenta imprime vence a memória dela --
    "tool output reaches you whole even when this page and the skills have been
    pruned". Uma ordem errada aqui sobrevive à poda que apaga a regra certa.
    """
    saida = []
    for nome in sorted(os.listdir(SCRIPTS)):
        if not nome.endswith(".py"):
            continue
        caminho = os.path.join(SCRIPTS, nome)
        try:
            arvore = ast.parse(open(caminho, encoding="utf-8").read())
        except SyntaxError:  # pragma: no cover
            continue
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.Call)
                    and isinstance(no.func, ast.Name)
                    and no.func.id == "print"):
                continue
            pedacos = []
            for arg in no.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        pedacos.append(sub.value)
            if pedacos:
                saida.append((f"warden-shared/scripts/{nome}:{no.lineno}",
                              " ".join(pedacos)))
    return saida


def _acusacoes(alvos):
    faltas = []
    for rotulo, texto in alvos:
        corrido = " ".join(texto.split())
        for padrao, porque in FORMAS:
            for achado in re.finditer(padrao, corrido, re.IGNORECASE):
                ini = max(0, achado.start() - JANELA)
                if SEGURO.search(corrido[ini:achado.end() + JANELA]):
                    continue
                faltas.append(
                    "%s: %s -- %r"
                    % (rotulo, porque,
                       corrido[max(0, achado.start() - 90):achado.end() + 90]))
    return faltas


class NadaMandaFalarNoMeioDoTurno(unittest.TestCase):

    def test_ha_o_que_varrer(self):
        """Um teste que varre o vazio passa sempre. Este repositório já teve três."""
        self.assertGreaterEqual(len(textos_da_imagem()), 8, textos_da_imagem())
        impressas = strings_impressas()
        self.assertGreaterEqual(len(impressas), 200, len(impressas))

    def test_nenhum_texto_da_imagem_manda_falar_e_seguir(self):
        faltas = _acusacoes(textos_da_imagem())
        self.assertEqual(
            [], faltas,
            "texto da imagem mandando falar no MEIO do turno -- foi assim que a "
            "entrega de 16/09 morreu, e esta é a quinta vez que a mesma regra "
            "aparece consertada num lugar só:\n  " + "\n  ".join(faltas))

    def test_nenhuma_saida_de_ferramenta_manda_falar_e_seguir(self):
        faltas = _acusacoes(strings_impressas())
        self.assertEqual(
            [], faltas,
            "saída de ferramenta mandando falar no MEIO do turno. Isto é PIOR "
            "que o mesmo erro num texto: a persona manda obedecer ao que a "
            "ferramenta imprime ACIMA da própria memória, então esta ordem "
            "sobrevive à poda que apaga a regra certa:\n  " + "\n  ".join(faltas))

    def test_a_varredura_pega_a_frase_que_custou_a_gravacao(self):
        """A rede tem de pegar o peixe. A frase exata de warden-run, de 16/09."""
        veneno = ("Before you run a single command, send ONE short line saying "
                  "you are on it. Then work without stopping.")
        self.assertTrue(
            _acusacoes([("teste", veneno)]),
            "a varredura NÃO pega a frase que custou a quarta gravação")

    def test_a_varredura_nao_reprova_a_redacao_certa(self):
        """E não pode pegar quem está certo, ou alguém a desliga."""
        bom = ("Start the job in the BACKGROUND with notify on, then END that "
               "turn with ONE short line saying you are on it.")
        self.assertEqual([], _acusacoes([("teste", bom)]))


if __name__ == "__main__":
    unittest.main()
