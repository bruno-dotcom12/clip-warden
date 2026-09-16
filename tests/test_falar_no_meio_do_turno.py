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

# O GATILHO É INVERTIDO, E ESSA É A LIÇÃO QUE CUSTOU TRÊS AUDITORIAS.
#
# A primeira versão deste arquivo listava cinco verbos de dizer e quatro de
# ação. Uma auditoria escreveu doze frases venenosas e ela pegou DUAS -- as duas
# de onde os regexes tinham sido copiados. Era a caçada de cópias com outro
# nome. Escapavam "let them know", "post a note", "drop a line", "say X then
# immediately Y", e a forma inteira em PORTUGUÊS, que é metade do que se escreve
# aqui.
#
# Então a pergunta mudou de "esta frase é uma das que eu conheço?" para "esta
# instrução de FALAR diz que a fala encerra o turno?". Toda fala sem esse
# marcador é acusada, e o ruído se resolve com EXCEÇÕES NOMEADAS lá embaixo --
# uma exceção nomeada é auditável; um regex que só casa o que já aconteceu não é.
DIZER = (
    # inglês
    r"say|says|saying|said|tells? (?!you\b)|telling (?!you\b)|speak|"
    r"announce|announces|"
    r"acknowledge|reply|replies|report back|let (?:them|him|her|the person|the owner) know|"
    r"(?:post|drop|leave) (?:a |one )?\w*\s?(?:note|line|message)|"
    r"give (?:them )?a heads[- ]up|message them|write to (?:them|the person)|"
    r"send (?:one |a |ONE |the )?(?:short |quick )?(?:line|message|note|word)|"
    # português
    r"diga|dizer|avise|avisar|fale|falar|responda|responder|conte|contar|"
    r"mande (?:uma )?(?:linha|mensagem|recado)|manda (?:uma )?(?:linha|mensagem)"
)

# O que, DEPOIS da fala, denuncia que o turno continua. Deliberadamente largo:
# qualquer trabalho que venha atrás de uma fala, no mesmo fôlego.
SEGUE = (
    # `before you SAY` é sempre a direção segura -- a fala é a subordinada, e o
    # que vem antes é a evidência ("o bloco do veredito antes de você dizer que
    # o clipe passou"). Só conta como "e siga" quando o que vem depois do
    # `before you` é TRABALHO.
    r"then|and then|e siga|e continue|e depois|"
    r"before you (?!say|says|tell|tells|speak|announce|reply|mention)|"
    r"before rendering|"
    r"antes de|keep going|keeps going|carry on|carrying on|proceed|proceeds|"
    r"continue|continues|continuing|immediately|right away|without (?:stopping|"
    r"pausing)|sem parar|while it (?:runs|is running)|in the SAME turn|"
    r"no mesmo turno|kick off|first, then|while (?:it|the \w+(?: \w+)?) (?:runs|is running)"
)

# As AÇÕES que, vindo depois da fala, fecham a acusação. Também largo.
ACAO = (
    r"run|runs|render|renders|rendering|cut|cuts|upload|uploads|publish|"
    r"publishes|post|posts|download|downloads|send|sends|queue|begin|batch|"
    r"job|command|clip|window|start|starts|starting|rode|rodar|renderiz|corta|cortar|publicar|postar"
)

# A DIREÇÃO É O TUDO. `DIZER ... then ... AÇÃO` é o defeito de 16/09. O inverso,
# `AÇÃO ... before you say`, é a regra mais antiga deste projeto -- verificar com
# o comando ANTES de afirmar qualquer coisa -- e uma versão anterior desta rede
# acusava as sete ocorrências dela. Uma rede que reprova a regra certa é
# desligada na mesma semana.
FORMAS = (
    (r"(?:%s)\b[^.!?]{0,140}?\b(?:%s)\b[^.!?]{0,60}?\b(?:%s)" % (DIZER, SEGUE, ACAO),
     "falar e seguir trabalhando no mesmo turno"),
    # "Before you run a single command, SEND one short line" -- a frase de
    # 16/09. Aqui o "before you" abre a oração SUBORDINADA e o imperativo é o
    # de DIZER: fala primeiro, trabalha depois. É o oposto de "read it back
    # before you say a word", onde o imperativo é o de agir e a fala vem
    # depois da verificação -- e essa é a regra mais antiga do projeto.
    (r"(?:^|[.!?]\s+)\s*before (?:you )?(?:%s)\b[^.!?]{0,90}?,\s*(?:%s)"
     % (ACAO, DIZER),
     "falar ANTES de trabalhar, no mesmo turno"),
    # "Tell the person what you are doing WHILE the render runs" -- aqui o
    # trabalho não vem depois da fala, corre JUNTO dela, e o turno é o mesmo.
    (r"(?:%s)\b[^.!?]{0,120}?\bwhile\b[^.!?]{0,60}?\b(?:%s)" % (DIZER, ACAO),
     "falar enquanto o trabalho corre, no mesmo turno"),
)

# EXCEÇÕES NOMEADAS. Cada uma é uma frase real deste repositório que a rede
# acusa e que está CERTA, com o motivo escrito. Auditável, ao contrário de um
# regex estreito: quem acrescentar uma aqui está declarando o que está fazendo.
EXCECOES = (
    # A fala que acompanha o clipe VAI na mensagem final, junto do MEDIA:.
    "beside the clip",
    "beside a clip",
    "ao lado do clipe",
    # O comando imprime o que a pessoa deve ouvir; quem fala é o modelo, depois.
    "in THEIR language",
    "in their own language",
    "na língua dela",
)

# `on the next` SOZINHO era chave-mestra: qualquer parágrafo com "on the next
# pass/render" a 260 chars desarmava a acusação, e este repositório usa a
# expressão o tempo todo. Agora só conta quando fala de TURNO.
SEGURO = re.compile(
    r"(?i)(final message|last message|END (?:a|that|your|this) turn|"
    r"ends that turn|ENDS THIS TURN|never between two tool calls|"
    r"on the next turn|encerra o turno|mensagem final)")

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
    """(rótulo, texto) de cada literal que os scripts do warden mandam ao modelo.

    A saída de ferramenta é o caso MAIS grave, e não o menos: a persona diz, com
    todas as letras, que o que a ferramenta imprime vence a memória dela --
    "tool output reaches you whole even when this page and the skills have been
    pruned". Uma ordem errada aqui sobrevive à poda que apaga a regra certa.

    TRÊS CEGUEIRAS QUE UMA AUDITORIA MEDIU EM 16/09, e que esta função teve de
    aprender a enxergar:

    1. CONSTANTE DE MÓDULO IMPRESSA POR NOME. `print(ROTULO_PRONTAS, ...)` não
       tem literal nenhum nos argumentos, e a versão anterior só olhava
       `ast.Constant`. As três linhas invisíveis eram justamente
       `ROTULO_PRONTAS`, `MANDA_AS_LINHAS` e `RUBRICA_DO_CLIPE` -- ou seja, O
       BLOCO DE ENTREGA, o texto mais load-bearing do sistema.
    2. `die()`, `fala()`, `diz()`. Escrevem em stderr pelo mesmo canal e o
       modelo os lê igual. Só `print` era varrido.
    3. FRASE PARTIDA EM DOIS `print()` SEGUIDOS -- o padrão dominante em
       `warden.py`. Cada um era um alvo isolado, então a forma proibida podia
       viver na emenda. Agora `print`s adjacentes são costurados antes de casar.
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

        # As constantes de módulo, para resolver `print(NOME)` e `NOME.format()`.
        constantes = {}
        for no in arvore.body:
            if isinstance(no, ast.Assign) and isinstance(no.value, ast.Constant) \
                    and isinstance(no.value.value, str):
                for alvo in no.targets:
                    if isinstance(alvo, ast.Name):
                        constantes[alvo.id] = no.value.value

        def texto_de(no):
            """Todo pedaço de string alcançável a partir deste nó."""
            pedacos = []
            for sub in ast.walk(no):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    pedacos.append(sub.value)
                elif isinstance(sub, ast.Name) and sub.id in constantes:
                    pedacos.append(constantes[sub.id])
            return pedacos

        # Uma lista por bloco, para costurar chamadas adjacentes.
        blocos = []
        for pai in ast.walk(arvore):
            corpo = getattr(pai, "body", None)
            if not isinstance(corpo, list):
                continue
            atual = []
            for no in corpo:
                fala = None
                if isinstance(no, ast.Expr) and isinstance(no.value, ast.Call):
                    chamada = no.value
                    alvo = chamada.func
                    quem = (alvo.id if isinstance(alvo, ast.Name)
                            else getattr(alvo, "attr", ""))
                    if quem in ("print", "die", "fala", "diz"):
                        fala = (no.lineno, texto_de(chamada))
                if fala and fala[1]:
                    atual.append(fala)
                else:
                    if atual:
                        blocos.append(atual)
                    atual = []
            if atual:
                blocos.append(atual)

        for bloco in blocos:
            linha = bloco[0][0]
            junto = " ".join(t for _l, ts in bloco for t in ts)
            saida.append((f"warden-shared/scripts/{nome}:{linha}", junto))
    return saida


def _acusacoes(alvos):
    faltas = []
    for rotulo, texto in alvos:
        corrido = " ".join(texto.split())
        for padrao, porque in FORMAS:
            for achado in re.finditer(padrao, corrido, re.IGNORECASE):
                ini = max(0, achado.start() - JANELA)
                vizinhanca = corrido[ini:achado.end() + JANELA]
                if SEGURO.search(vizinhanca):
                    continue
                if any(e.lower() in vizinhanca.lower() for e in EXCECOES):
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

    def test_a_rede_pega_doze_formas_diferentes(self):
        """A rede tem de pegar peixe que ela não conhece.

        A primeira versão deste arquivo tinha dois testes aqui, e uma auditoria
        os chamou pelo nome: tautologias. Um afirmava que o regex casava a
        frase de onde o regex tinha sido copiado. Medido: a rede pegava 2 destas
        12. Estas doze formas foram escritas POR OUTRA PESSOA, sem olhar os
        padrões, e é isso que as faz valerem alguma coisa.
        """
        veneno = (
            ("controle 16/09", "Before you run a single command, send ONE short "
             "line saying you are on it. Then work without stopping."),
            ("4a copia", "If they ask for another attempt, say what you are "
             "changing and why BEFORE you start it."),
            ("let them know", "Let the person know you are starting, and carry "
             "on with the remaining clips."),
            ("post a note", "Post a quick note in the chat acknowledging the "
             "request, then kick off the render."),
            ("then immediately", "Say you are on it, then immediately call "
             "`lote render` for the next window."),
            ("while it runs", "Tell the person what you are doing while the "
             "render job runs in the background."),
            ("before you upload", "Send one short line to the person before you "
             "upload the clip to TikTok."),
            ("heads-up", "Give them a heads-up before you render so they are "
             "not left staring at nothing."),
            ("em portugues", "Avise a pessoa em uma linha antes de rodar "
             "qualquer comando, e siga trabalhando sem parar."),
            ("do not stay silent", "Do not stay silent: drop a line in chat "
             "first, then continue the batch without pausing."),
            ("say so and proceed", "Say so in the chat and proceed with the "
             "second window right away."),
            ("send a message first", "Send a message to the person first, then "
             "run the three renders back to back."),
        )
        escaparam = [nome for nome, frase in veneno
                     if not _acusacoes([(nome, frase)])]
        self.assertEqual(
            [], escaparam,
            "a rede não pega %d de %d formas de mandar falar no meio do turno. "
            "Cada uma que escapa é a 5ª cópia entrando com a suíte verde: %s"
            % (len(escaparam), len(veneno), escaparam))

    def test_a_rede_nao_reprova_quem_esta_certo(self):
        """Uma rede barulhenta é desligada, e aí não pega mais nada.

        As cinco são formas legítimas que versões anteriores desta varredura
        acusaram -- inclusive a regra mais antiga do projeto, "verifique com o
        comando ANTES de afirmar", que tem sete ocorrências no repositório.
        """
        legitimas = (
            ("ordem entre comandos",
             "Run `warden delivered` before you render anything else."),
            ("ponteiro entre arquivos",
             "Section 5 tells you to read this reference before you cut one."),
            ("aviso que encerra o turno",
             "Start the job in the BACKGROUND with notify on, then END that "
             "turn with ONE short line saying you are on it."),
            ("evidencia antes da fala",
             "`stored and verified` with a path before you say one is stored."),
            ("clausula ao lado do clipe",
             "Repass the warning in ONE clause beside the clip, in their "
             "language."),
        )
        acusadas = [nome for nome, frase in legitimas
                    if _acusacoes([(nome, frase)])]
        self.assertEqual([], acusadas,
                         "a rede acusa quem está certo, e vai ser desligada: %s"
                         % acusadas)

    def test_a_rede_le_o_bloco_de_entrega(self):
        """As três linhas mais load-bearing do sistema eram invisíveis.

        `ROTULO_PRONTAS`, `MANDA_AS_LINHAS` e `RUBRICA_DO_CLIPE` são impressas
        por NOME, e a versão anterior só lia `ast.Constant` dentro do `print`.
        São exatamente o texto que `warden-clip/SKILL.md` manda obedecer ACIMA
        da memória do agente -- e a rede que existe para proteger a regra não
        conseguia lê-las.
        """
        todo = " ".join(t for _r, t in strings_impressas())
        for frase in ("END YOUR TURN NOW",
                      "send these lines in your FINAL reply",
                      "caption, in the person"):
            self.assertIn(frase, todo,
                          "a varredura não enxerga o bloco de entrega: %r" % frase)


if __name__ == "__main__":
    unittest.main()
