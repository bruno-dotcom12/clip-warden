# -*- coding: utf-8 -*-
"""O que as SKILL.md têm de obedecer para chegarem inteiras ao modelo.

Estes testes não leem o conteúdo das skills como prosa: leem o TAMANHO, o
IDIOMA e a presença de três regras que o teste 7a provou faltarem. Nenhum deles
roda ffmpeg nem container.

## De onde vêm os tetos (medido no teste 7a, não estimado)

`logs-7a/container/config.yaml:61` -- `proactive_prune_tokens: 48000`. É esse o
limiar: passou disso, o runtime reescreve o histórico e as skills viram
`[SKILL_PRUNED: content lost in compression]`. Foi o que aconteceu às 14:52:50,
no instante em que o agente ia entregar.

`logs-7a/container/logs/agent.log:175` -- chamada #1, com UMA mensagem de 146
chars no histórico: `in=35006`. Esse é o PISO do prompt (sistema + 45 schemas de
ferramenta), antes de qualquer skill. Sobram 12.994 tokens até o limiar.

Deltas de `in=` em `agent.log:175-182`, que são o custo medido de cada skill:
warden-run 8.575 chars = 3.052 tokens; warden-clip 34.983 chars = 13.051;
warden-shared 11.494 chars = 4.124. Total 55.052 chars = 20.227 tokens
(2,72 chars por token). Com as três carregadas o prompt foi a 55.233 na chamada
#4 -- 7.233 ACIMA do limiar, antes de uma palavra de conversa.

E os 12.994 tokens de folga não são só das skills: uma única saída de
`lote render` custa 3.060 tokens (11.796 chars, delta da chamada #17,
`agent.log:230-232`), e ainda tem a conversa.

Então a meta escolhida, e o porquê de cada número:

  TETO_FRENTE = 23.000 chars para as TRÊS skills da porta de entrada somadas
  (warden-run + warden-clip + warden-shared). A 2,72 chars/token são ~8.456
  tokens: o prompt fica em 43.462 e restam ~4.538 tokens (~12.340 chars) para
  uma saída de `lote render` (3.060) mais a conversa, ainda abaixo de 48.000.

  ESTE NÚMERO ERA 22.000 e subiu em 16/09, com a conta escrita. Não subiu para
  caber prosa: subiu porque a auditoria de 16/09 (PRESTACAO-DE-CONTAS-REGRAS.md)
  REPROVOU o corte anterior por regra removida, e as regras que voltaram para o
  trio somam 585 chars -- os três passos do upload-post com o teto de 100 chars
  do título (degrau 6 do demo), o formato 9:16 (degrau 3), a regra do filtro
  incompleto, e os ponteiros que trocaram `docs/X.md` (fora da imagem) por
  `warden-<skill>/references/x.md` (dentro dela), que são mais longos. O
  procedimento que veio junto NÃO está aqui dentro: foi para `references/`, que
  o `skill_view` carrega sob demanda e não pesa em todo turno.

  O preço, dito de frente: a folga para conversa depois de UMA saída grande de
  ferramenta cai de ~486 para ~118 tokens quando se conta o embrulho medido
  abaixo. Ou seja, este teto está no limite e não há mais espaço para acrescentar
  regra à porta de entrada sem tirar outra. A outra metade do conserto continua
  sendo `proactive_prune_tokens`, que é de outra faixa.

  TETO_ARQUIVO = 12.000 chars por SKILL.md. Nenhum arquivo sozinho pode voltar
  a ser o estouro inteiro, como o warden-clip era (34.983 chars = 13.051
  tokens, sozinho maior que os 12.994 de folga).

## O que este teste NÃO mede, dito de frente

O `skill_view` cobra MAIS do que o arquivo tem. Comparando o agent.log com o
tamanho dos arquivos de então: warden-run 8.575 contra 7.610 bytes (+965),
warden-clip 34.983 contra 33.489 (+1.494), warden-shared 11.494 contra 10.237
(+1.257). São ~3.700 chars (~1.360 tokens) de embrulho que este teste não vê,
porque ele lê o arquivo. Com o trio em 22.000 o prompt fica em ~44.500 tokens
com o embrulho, e a folga que sobra até 48.000 cabe UMA saída grande de
ferramenta e pouca conversa.

Ou seja: encolher a skill é metade do conserto. A outra metade é subir
`proactive_prune_tokens`, que é de outra faixa e não é mexida aqui.
"""

import inspect
import os
import re
import sys
import textwrap
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(RAIZ, "warden-shared", "scripts"))
import warden  # noqa: E402  -- a fonte da verdade sobre o que a ferramenta imprime

# As três que o skill_view carregou na porta de entrada do teste 7a
# (agent.log:176, 180, 182). As outras quatro entram sob demanda.
FRENTE = ("warden-run", "warden-clip", "warden-shared")

# 23.100 desde 16/09, e a conta importa mais que o número. O orçamento real é o
# que separa o piso do prompt (35.006 tokens) da poda (48.000): ~12.994 tokens,
# ou ~35.000 chars. Os 23.000 eram a margem folgada escolhida em 15/09, não um
# limite físico. Estes 100 chars a mais valem ~37 tokens contra esse orçamento --
# 0,3% dele -- e pagam a proibição de falar no meio do turno, que custou a quarta
# gravação do dono.
#
# O que NÃO fazer: continuar subindo. A distância real até a poda é o que impede
# `warden-clip` de sumir do contexto no meio de um render, e foi exatamente isso
# que aconteceu em 7a (SKILL_PRUNED às 14:52:50). Da próxima vez, encolher.
TETO_FRENTE = 23_100
TETO_ARQUIVO = 12_000

CHARS_POR_TOKEN = 2.72   # 55.052 chars / 20.227 tokens, agent.log:175-182
PISO_PROMPT = 35_006     # agent.log:175
LIMIAR_PODA = 48_000     # logs-7a/container/config.yaml:61


def skills():
    """Todas as warden-*/SKILL.md do repo, por nome de pasta."""
    achadas = {}
    for nome in sorted(os.listdir(RAIZ)):
        caminho = os.path.join(RAIZ, nome, "SKILL.md")
        if nome.startswith("warden-") and os.path.isfile(caminho):
            achadas[nome] = caminho
    return achadas


def texto(caminho):
    with open(caminho, encoding="utf-8") as fh:
        return fh.read()


def corrido(caminho):
    """O mesmo texto com o espaco em branco colapsado.

    Uma regra quebrada em duas linhas pelo limite de coluna continua sendo a
    mesma regra; o teste procura a frase, nao a diagramacao.
    """
    return " ".join(texto(caminho).split())


class TamanhoDasSkills(unittest.TestCase):
    """Pedido 5 do dono: encolher as SKILL.md para diminuir a poda."""

    def test_o_trio_da_porta_de_entrada_cabe_na_folga_ate_a_poda(self):
        encontradas = skills()
        total = 0
        detalhe = []
        for nome in FRENTE:
            self.assertIn(nome, encontradas, f"{nome}/SKILL.md sumiu")
            n = len(texto(encontradas[nome]))
            total += n
            detalhe.append(f"{nome}={n}")
        self.assertLessEqual(
            total, TETO_FRENTE,
            "as skills da porta de entrada somam {} chars (~{:.0f} tokens); o "
            "teto é {} porque o piso do prompt é {} tokens e a poda entra em "
            "{}. Hoje: {}".format(
                total, total / CHARS_POR_TOKEN, TETO_FRENTE,
                PISO_PROMPT, LIMIAR_PODA, ", ".join(detalhe)))

    def test_o_prompt_com_as_tres_skills_fica_abaixo_do_limiar_de_poda(self):
        """A conta que o 7a fez de verdade: piso + skills contra 48.000."""
        total = sum(len(texto(skills()[n])) for n in FRENTE)
        prompt = PISO_PROMPT + total / CHARS_POR_TOKEN
        self.assertLess(
            prompt, LIMIAR_PODA,
            "com as três skills carregadas o prompt vai a {:.0f} tokens e a "
            "poda entra em {} -- foi exatamente isso às 14:52:50".format(
                prompt, LIMIAR_PODA))

    def test_nenhuma_skill_sozinha_passa_do_teto_por_arquivo(self):
        grandes = {n: len(texto(c)) for n, c in skills().items()
                   if len(texto(c)) > TETO_ARQUIVO}
        self.assertEqual(
            {}, grandes,
            f"nenhuma SKILL.md pode passar de {TETO_ARQUIVO} chars sozinha")


# ---------------------------------------------------------------------------
# Idioma
# ---------------------------------------------------------------------------
#
# msg 13 do state.db (14:50:27 UTC): a pessoa escreveu em inglês e o agente
# respondeu "Manda o link do vídeo que você quer cortar." -- 43 chars em
# português. As três mensagens imediatamente anteriores são as skills:
# id 8 warden-run (8.575 chars, com 'Em produção.'), id 10 warden-clip
# (34.983 chars, com 'esse vídeo' e 'Entreguei os 2'), id 12 warden-shared.
#
# A regra: as SKILL.md são escritas em inglês e não carregam frase nenhuma
# presa a um idioma -- nem como ordem ("send `Em produção.`"), nem como
# exemplo, nem como citação do dono. O que o agente diz é no idioma da pessoa;
# o histórico e as citações vão para docs/ ou para comentário no código.
#
# LIMITE CONHECIDO deste detector, dito de frente: ele pega acento e uma lista
# fechada de palavras. Uma frase em português SEM acento e sem nenhuma dessas
# palavras passa -- foi o caso do exemplo de hook em warden-style, achado à mão
# e corrigido à mão. O detector é uma trava contra regressão, não uma prova de
# que não sobrou nada; uma frase de exemplo ainda precisa de olho humano.

MARCAS_PT = (
    "ã", "õ", "ç", "á", "é", "í", "ó", "ú", "ê", "ô", "â", "à",
    "Ã", "Õ", "Ç", "Á", "É", "Í", "Ó", "Ú", "Ê", "Ô", "Â", "À",
)

PALAVRAS_PT = {
    "nao", "voce", "esta", "entao", "esse", "essa", "isso", "aqui",
    "sua", "seu", "meu", "minha", "quero", "rapido", "minimo",
    "qualidade", "velocidade", "acima", "tudo", "producao", "obrigacao",
    "unica", "entrega", "entreguei", "legenda", "corte", "cortes",
    "anexado", "reenviando", "segundos", "mudos", "plataforma", "campanha",
    "antes", "subir", "topo", "olha", "olhar", "veredito", "caixa",
    "entrada", "pra", "sala", "lembra", "portas", "possivel",
}

_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÿ]+")

# Um caminho `docs/NOME-DO-ARQUIVO.md` e um identificador de arquivo, nao uma
# frase que o agente diz. A isencao e estreita de proposito: so casa um caminho
# docs/ terminado em .md, entao nao tem como esconder uma frase prescrita.
_CAMINHO_DOCS = re.compile(r"docs/[A-Z0-9-]+\.md")

# A mesma isencao, pela mesma razao, para o caminho de um references/. Os
# arquivos movidos guardam o nome do assunto em portugues (`legenda-suspeita`,
# `fonte-bloqueada`), e um NOME DE ARQUIVO nao e uma frase que o agente diz. A
# isencao continua estreita: so casa `warden-<skill>/references/<nome>.md`.
_CAMINHO_DE_REFERENCIA = re.compile(r"warden-[a-z]+/references/[a-z0-9.-]+\.md")


def sem_caminhos(linha):
    """A linha sem os caminhos de arquivo, para o detector de idioma."""
    return _CAMINHO_DE_REFERENCIA.sub("", _CAMINHO_DOCS.sub("", linha))


# A segunda porta do mesmo defeito, e ela passa pelo detector acima: uma frase
# PRONTA em INGLÊS. `warden-run/SKILL.md:11` mandava enviar `Em produção.` e
# isso o detector pega; "the honest sentence is "it is in your inbox..."" ele
# não pega, e é a mesma coisa -- uma frase de um idioma só posta na boca do
# agente. A regra é a mesma para os dois: a skill descreve a FORMA, a pessoa
# recebe as palavras dela.
#
# O detector é estreito de proposito: so olha para uma aspa aberta logo depois
# de um verbo de dizer. Um exemplo de hook, um rotulo de botao do provedor e uma
# citacao de regra antiga nao casam, e nao deviam.

_VERBO_DE_DIZER = re.compile(
    r"\b(send|say|says|reply|answer|write|tell them|the (?:honest )?sentence"
    r" is|what you say is)\b[^.\n]{0,20}[\"`]([^\"`\n]{20,})[\"`]",
    re.IGNORECASE)

# Uma vaga -- `<...>` ou `<algo>` dentro da citacao -- e o oposto de uma frase
# pronta: e o molde que o modelo preenche na lingua da pessoa.
_TEM_VAGA = re.compile(r"<[^>]+>")


class FraseProntaNaBocaDoAgente(unittest.TestCase):

    def test_nenhuma_skill_poe_uma_frase_pronta_na_boca_do_agente(self):
        faltas = []
        for nome, caminho in sorted(skills().items()):
            # No texto CORRIDO: a frase prescrita costuma cair em cima da quebra
            # de linha, e uma varredura linha a linha passa por ela sem ver.
            for _verbo, citada in _VERBO_DE_DIZER.findall(corrido(caminho)):
                if _TEM_VAGA.search(citada):
                    continue
                faltas.append(f"{nome}/SKILL.md: {citada[:70]!r}")
        self.assertEqual(
            [], faltas,
            "a skill descreve a FORMA do que dizer; as palavras são do modelo, "
            "na língua da pessoa:\n" + "\n".join(faltas))


class IdiomaDasSkills(unittest.TestCase):

    def test_nenhuma_skill_prende_o_agente_a_um_idioma(self):
        faltas = []
        for nome, caminho in skills().items():
            for i, bruta in enumerate(texto(caminho).splitlines(), 1):
                linha = sem_caminhos(bruta)
                achado = [m for m in MARCAS_PT if m in linha]
                achado += [p for p in _PALAVRA.findall(linha.lower())
                           if p in PALAVRAS_PT]
                if achado:
                    faltas.append(f"{nome}/SKILL.md:{i}: {bruta.strip()[:90]}")
        self.assertEqual(
            [], faltas,
            "SKILL.md é escrita em inglês e não prescreve, nem exemplifica, "
            "nem cita frase num idioma só -- o agente fala o idioma da "
            "pessoa:\n" + "\n".join(faltas))


# ---------------------------------------------------------------------------
# A espera de um comando de fundo
# ---------------------------------------------------------------------------
#
# agent.log:252 (242,88s) e :279 (263,16s): os dois laços de espera rodaram o
# teto exato (24x10s e 26x10s) em vez de sair cedo. msg 63 do live.db
# (14:53:01): o `pgrep -af "warden lote render"` devolveu o PID 665, que é o
# PRÓPRIO bash do terminal. O render 1 acabou 14:54:33 e o laço só devolveu
# 14:57:20 -- 166s de espera contra nada.
#
# Nenhuma skill dizia como esperar (grep por sleep|pgrep|process_manage devolvia
# zero), então o agente inventou o laço. Agora tem de estar escrito.

class EsperaDeComandoDeFundo(unittest.TestCase):

    def test_a_skill_diz_como_esperar_em_vez_de_deixar_o_agente_inventar(self):
        t = texto(skills()["warden-clip"])
        self.assertIn(
            "process_manage", t,
            "warden-clip/SKILL.md tem de dizer que se espera com um "
            "`process_manage` no session id do terminal de fundo")

    def test_nenhuma_skill_ensina_pgrep_nem_laco_de_sleep(self):
        faltas = []
        for nome, caminho in skills().items():
            t = texto(caminho)
            for proibido in ("pgrep", "seq 1 24", "sleep 10"):
                if proibido in t:
                    faltas.append(f"{nome}/SKILL.md contém `{proibido}`")
        self.assertEqual(
            [], faltas,
            "um `pgrep -f` casa com o próprio bash do terminal (msg 63, PID "
            "665) e um laço de sleep roda o teto contra nada")


# ---------------------------------------------------------------------------
# Clipe pronto e não enviado
# ---------------------------------------------------------------------------
#
# 14:54:33: os dois clipes ficaram prontos e entregas.json gravou o 01 com
# "sent": false. Em vez de entregar, o agente re-renderizou às 14:57:42
# (--windows 300-330) e de novo às 15:02:34 (--windows 300-332), sem falar com
# ninguém desde 14:51:58. `warden delivered` sem argumento já responde essa
# pergunta e sai 1 enquanto faltar (warden-shared/scripts/warden.py:5355).

class ClipeProntoNaoEnviado(unittest.TestCase):

    def test_a_regra_de_entregar_o_que_ja_existe_esta_escrita(self):
        t = corrido(skills()["warden-clip"])
        self.assertIn(
            "warden delivered", t,
            "a skill tem de mandar rodar `warden delivered` antes de "
            "renderizar de novo")
        for pedaco in ('"sent": false', "Do not render it again",
                       "Never re-cut"):
            self.assertIn(
                pedaco, t,
                f"warden-clip/SKILL.md não carrega a regra do re-render: "
                f"falta {pedaco!r}")

    def test_entre_uma_tentativa_e_outra_a_pessoa_e_avisada(self):
        """O aviso continua obrigatório. O que mudou é QUANDO ele sai.

        16/09: este teste exigia, junto, "say what you are changing and why
        BEFORE you start it" -- que é falar e depois chamar ferramenta no mesmo
        turno, a redação exata que fez o adaptador engolir a entrega. A suíte
        estava PROTEGENDO o defeito: quem consertasse a skill quebrava o teste.
        """
        t = corrido(skills()["warden-clip"])
        self.assertIn(
            "never two renders in a row in silence", t,
            "entre 14:51:58 e a coleta foram 11 minutos e três renders sem "
            "uma palavra para a pessoa")
        self.assertNotIn(
            "BEFORE you start it", t,
            "voltou a mandar falar ANTES de começar, no mesmo turno")
        self.assertIn(
            "END a turn saying what you are changing", t,
            "o aviso tem de ENCERRAR um turno; o render começa no seguinte")


# ---------------------------------------------------------------------------
# A entrega não pode depender só da persona e das skills
# ---------------------------------------------------------------------------
#
# Pedido 3 do dono. A persona foi truncada (errors.log 14:37:58) e as skills
# foram podadas (14:52:50), e as duas cópias da regra de entrega sumiram
# juntas. A saída de ferramenta o modelo vê e ela não é podada -- então a skill
# manda obedecer ao bloco que `lote render` imprime, não à memória dela.

def constantes_do_bloco_final():
    """As duas linhas que `bloco_da_mensagem_final()` imprime, lidas do código.

    Nunca copiadas para cá: a rede dupla só existe se a skill citar a MESMA
    string que a ferramenta imprime. `warden.py` guarda as duas como constantes
    de módulo justamente para serem citadas assim.

    E não basta existirem: o teste confere que a função que imprime o bloco
    REFERENCIA as duas. Uma constante declarada e não impressa seria uma citação
    que aponta de novo para o nada.
    """
    fonte = textwrap.dedent(inspect.getsource(warden.bloco_da_mensagem_final))
    saida = []
    for nome in ("ROTULO_PRONTAS", "MANDA_AS_LINHAS"):
        valor = getattr(warden, nome, None)
        if not isinstance(valor, str) or not valor.strip():
            raise AssertionError(
                f"warden.py não tem mais a constante {nome} -- se o texto "
                f"impresso mudou de nome, warden-clip/SKILL.md e este teste "
                f"mudam junto")
        if nome not in fonte:
            raise AssertionError(
                f"{nome} existe mas bloco_da_mensagem_final() não a imprime")
        saida.append(valor)
    return saida



class AEntregaNaoDependeSoDoQuePodeSumir(unittest.TestCase):

    def test_a_skill_cita_o_MESMO_texto_que_o_codigo_imprime(self):
        t = corrido(skills()["warden-clip"])
        for esperado in constantes_do_bloco_final():
            self.assertIn(
                " ".join(esperado.split()), t,
                "warden-clip tem de citar, palavra por palavra, o que "
                "`lote render` imprime -- a saída de ferramenta não é podada, "
                "a skill é. Falta: {!r}".format(esperado))

    def test_a_skill_nao_inventa_um_rotulo_que_o_codigo_nao_imprime(self):
        """A metade que reprovou a rodada anterior.

        `MEDIA: PRONTAS` estava escrito na skill e não existe em lugar nenhum
        do warden.py: a skill mandava o modelo obedecer a um bloco que nunca
        aparece na tela.
        """
        fonte = texto(os.path.join(RAIZ, "warden-shared", "scripts",
                                   "warden.py"))
        t = texto(skills()["warden-clip"])
        for rotulo in re.findall(r"MEDIA: [A-Z]+", t):
            self.assertIn(
                rotulo, fonte,
                f"warden-clip cita o rótulo {rotulo!r} e o warden.py não "
                f"imprime isso em lugar nenhum")


# ---------------------------------------------------------------------------
# Um caminho docs/ citado é uma promessa
# ---------------------------------------------------------------------------
#
# A rodada anterior tirou a prosa das skills mandando o leitor para cinco
# arquivos de docs/ que ninguém escreveu. Cinco linhas de teste que teriam
# impedido aquilo.

def alvos_com_ponteiros():
    """Toda SKILL.md mais a persona -- os textos que citam docs/."""
    alvos = dict(skills())
    alvos["runtime/persona.md"] = os.path.join(RAIZ, "runtime", "persona.md")
    return alvos


class DocsCitadosExistem(unittest.TestCase):

    def test_todo_caminho_docs_citado_existe_no_repo(self):
        faltas = []
        for nome, caminho in sorted(alvos_com_ponteiros().items()):
            for i, linha in enumerate(texto(caminho).splitlines(), 1):
                for ref in _CAMINHO_DOCS.findall(linha):
                    if not os.path.isfile(os.path.join(RAIZ, ref)):
                        faltas.append(f"{nome}:{i} aponta para {ref}")
        self.assertEqual(
            [], faltas,
            "um caminho docs/ citado é uma promessa; estes não existem:\n"
            + "\n".join(sorted(set(faltas))))


# ---------------------------------------------------------------------------
# Os ponteiros da persona
# ---------------------------------------------------------------------------
#
# A persona é curta porque empurra regra para as skills. Um ponteiro que aponta
# para um arquivo onde a regra não está é pior do que não ter ponteiro: o
# modelo vai lá, não acha, e inventa.
#
# Para cada `warden-X` citado na persona, a âncora que TEM de estar no arquivo.

_PONTEIRO_PERSONA = re.compile(r"`(warden-[a-z]+)`")

ANCORAS = {
    # "`warden-check` for the rest" (a prova de cada tipo de afirmação)
    "warden-check": ("| The claim is about | What proves it |",),
    # "the three onboarding steps are in `warden-shared`" e
    # "what each network costs them ... is in `warden-shared`"
    "warden-shared": ("https://app.upload-post.com/api-keys",
                      "Business or Creator"),
    # "`warden-run` has the table" (o que tem default e nunca se pergunta)
    "warden-run": ("| how many | 2 |",),
    # "The ORDER OF WORK lives in `warden-clip`, at the top of that file"
    "warden-clip": ("## The order",),
    # "Read `warden-style` BEFORE writing a hook or burning a caption"
    "warden-style": ("## The hook", "## The caption"),
    # "`warden-campaign` turns a link or a pasted brief into rules"
    "warden-campaign": ("Never fill a field the brief did not state",),
    # "`warden-package` writes the caption the campaign demands"
    "warden-package": ("warden package",),
}


class PonteirosDaPersona(unittest.TestCase):

    def test_cada_ponteiro_da_persona_acha_a_regra_no_arquivo_apontado(self):
        persona = texto(os.path.join(RAIZ, "runtime", "persona.md"))
        apontadas = sorted(set(_PONTEIRO_PERSONA.findall(persona)))
        encontradas = skills()
        faltas = []
        for nome in apontadas:
            self.assertIn(
                nome, ANCORAS,
                f"a persona passou a apontar para `{nome}` e este teste não "
                f"sabe qual âncora cobrar -- acrescente-a em ANCORAS")
            self.assertIn(nome, encontradas, f"{nome}/SKILL.md não existe")
            t = texto(encontradas[nome])
            for ancora in ANCORAS[nome]:
                if ancora not in t:
                    faltas.append(f"{nome}/SKILL.md não contém {ancora!r}")
        self.assertEqual(
            [], faltas,
            "a persona manda ler a regra nestes arquivos e a regra não está "
            "lá:\n" + "\n".join(faltas))


# ---------------------------------------------------------------------------
# As regras que a rodada anterior deixou cair junto com a prosa
# ---------------------------------------------------------------------------
#
# Encolher é tirar prosa e história. Estas são REGRAS -- cada uma muda o que o
# agente faz -- e saíram junto. Ficam cobradas uma a uma, no arquivo que a
# persona já aponta.

REGRAS_QUE_NAO_PODEM_SUMIR = {
    "warden-shared": (
        "https://app.upload-post.com",           # passo 1 do onboarding
        "https://app.upload-post.com/api-keys",  # passo 2
        "warden post setkey",                    # passo 3
        "a paid plan",                           # TikTok
        "Business or Creator",                   # Instagram
        "2200",                                  # limite recusado, nao cortado
        "--draft",                               # TikTok ignora legenda/privacidade
        "where the key came from",               # a ORIGEM da chave
        "this machine",                          # o perfil por maquina
        "warden beat",
    ),
    "warden-check": (
        "| The claim is about | What proves it |",
    ),
    "warden-clip": (
        "/var/lib/hermes/warden/cookies.txt",
    ),
}


class RegrasQueSumiramComAProsa(unittest.TestCase):

    def test_as_regras_voltaram_para_o_arquivo_que_a_persona_aponta(self):
        faltas = []
        for nome, pedacos in sorted(REGRAS_QUE_NAO_PODEM_SUMIR.items()):
            t = corrido(skills()[nome])
            for pedaco in pedacos:
                if " ".join(pedaco.split()) not in t:
                    faltas.append(f"{nome}/SKILL.md perdeu {pedaco!r}")
        self.assertEqual(
            [], faltas,
            "encolher é tirar prosa, não regra:\n" + "\n".join(faltas))


# ---------------------------------------------------------------------------
# A receita de espera, com o que foi medido e o que NÃO foi
# ---------------------------------------------------------------------------
#
# msg 21 de sessao-mensagens.txt: a chamada já era `background=true` e o comando
# ainda terminava em `&`, então o processo que o terminal registrou foi o shell
# que já tinha saído -- o wait voltou em 0,01s. msg 24: `timeout_note` diz que o
# wait é clampado a 180s, e um render passa disso. Nada desta receita foi rodada
# num container ainda; enquanto não for, ela é escrita como não medida.

class ReceitaDeEspera(unittest.TestCase):

    def test_a_receita_diz_o_que_o_7a_mediu_e_o_que_ela_ainda_nao_tem(self):
        t = corrido(skills()["warden-clip"])
        for pedaco in ("process_manage",
                       "no `&` and no `nohup`",
                       "clamped to 180s",
                       "NOT MEASURED"):
            self.assertIn(
                pedaco, t,
                f"a receita de espera em warden-clip/SKILL.md não diz "
                f"{pedaco!r} -- e sem isso ela é uma certeza que ninguém mediu")


# ---------------------------------------------------------------------------
# LEI 1: o que nao entra na imagem nao e regra
# ---------------------------------------------------------------------------
#
# O Dockerfile copia `runtime/persona.md`, as sete pastas `warden-*/` INTEIRAS e
# `SPECS/`. Nao copia `docs/`. Entao um ponteiro de dentro da imagem mandando o
# AGENTE ler `docs/X.md` e uma regra REMOVIDA com aparencia de regra presente --
# foi o defeito que a auditoria de 16/09 pegou (PRESTACAO-DE-CONTAS-REGRAS.md,
# R1: cinco ponteiros para arquivos que nem existiam).
#
# O mecanismo legal e `warden-<skill>/references/<nome>.md`, e que ele existe
# esta medido: em 16/09 o `skill_view` devolveu "File 'references/publishing.md'
# not found in skill 'warden-run'" com um mapa `available_files` -- ele procura
# ali. Este bloco cobra as duas metades: nenhum ponteiro para fora da imagem, e
# todo ponteiro para dentro dela resolve.

# O caminho como as skills o escrevem: `<skill>/references/<nome>.md`. A pasta
# da skill diz para qual skill o `skill_view` e chamado; o resto e o arquivo.
_CAMINHO_REF = re.compile(r"(warden-[a-z]+)/(references/[a-z0-9.-]+\.md)")
assert _CAMINHO_DE_REFERENCIA.pattern == _CAMINHO_REF.pattern.replace("(", "").replace(")", ""), (
    "os dois padroes do mesmo caminho tem de casar a mesma coisa")

# A unica desculpa para um `docs/` sobreviver dentro da imagem: a frase dizer,
# nela mesma, que aquilo e leitura humana e nao um passo do agente.
_LEITURA_HUMANA = "for a human reader"


def dockerfile():
    return texto(os.path.join(RAIZ, "Dockerfile"))


def arquivos_de_referencia():
    """Todo warden-*/references/*.md do repo, por caminho relativo."""
    achados = {}
    for nome in sorted(skills()):
        pasta = os.path.join(RAIZ, nome, "references")
        if not os.path.isdir(pasta):
            continue
        for arq in sorted(os.listdir(pasta)):
            if arq.endswith(".md"):
                achados[f"{nome}/references/{arq}"] = os.path.join(pasta, arq)
    return achados


def textos_da_imagem():
    """Tudo que o agente le de dentro da imagem: SKILL.md + references/."""
    tudo = {f"{n}/SKILL.md": c for n, c in skills().items()}
    tudo.update(arquivos_de_referencia())
    return tudo


class PonteirosSoParaDentroDaImagem(unittest.TestCase):

    def test_o_dockerfile_copia_as_pastas_das_skills_e_nao_copia_docs(self):
        """A premissa de tudo neste bloco, lida do Dockerfile e nao suposta."""
        d = dockerfile()
        for nome in sorted(skills()):
            self.assertIn(
                f"{nome}/", d,
                f"o Dockerfile nao copia {nome}/ -- entao nada dentro dessa "
                f"pasta e regra, nem a SKILL.md nem references/")
        for linha in d.splitlines():
            if linha.strip().startswith("COPY") and " docs/" in linha:
                self.fail(
                    "o Dockerfile passou a copiar docs/ ({!r}) -- se isso e "
                    "intencional, este bloco inteiro muda junto".format(
                        linha.strip()))

    def test_nenhuma_skill_manda_o_agente_ler_um_arquivo_fora_da_imagem(self):
        faltas = []
        for nome, caminho in sorted(textos_da_imagem().items()):
            for i, linha in enumerate(texto(caminho).splitlines(), 1):
                if _CAMINHO_DOCS.search(linha) and _LEITURA_HUMANA not in linha:
                    faltas.append(f"{nome}:{i}: {linha.strip()[:80]}")
        self.assertEqual(
            [], faltas,
            "docs/ nao entra na imagem (Dockerfile): mandar o agente ler la e "
            "apagar a regra e deixar a aparencia dela. O conteudo vai para "
            "warden-<skill>/references/<nome>.md:\n" + "\n".join(faltas))

    def test_todo_references_citado_existe_dentro_da_pasta_da_skill(self):
        faltas = []
        for nome, caminho in sorted(textos_da_imagem().items()):
            for i, linha in enumerate(texto(caminho).splitlines(), 1):
                for pasta, arq in _CAMINHO_REF.findall(linha):
                    alvo = os.path.join(RAIZ, pasta, arq)
                    if not os.path.isfile(alvo):
                        faltas.append(f"{nome}:{i} aponta para {pasta}/{arq}")
        self.assertEqual(
            [], faltas,
            "o `skill_view` responde 'not found in skill' e o agente inventa o "
            "que faltou:\n" + "\n".join(sorted(set(faltas))))

    def test_nenhum_arquivo_de_referencia_fica_orfao(self):
        """Um references/ que ninguem aponta e prosa paga sem leitor.

        O `skill_view` so e chamado quando o agente decide chama-lo, e ele so
        decide se a SKILL.md mandou.
        """
        citados = set()
        for caminho in textos_da_imagem().values():
            for pasta, arq in _CAMINHO_REF.findall(texto(caminho)):
                citados.add(f"{pasta}/{arq}")
        orfaos = sorted(set(arquivos_de_referencia()) - citados)
        self.assertEqual(
            [], orfaos,
            "nenhuma SKILL.md aponta para estes, entao o agente nunca os le: "
            + ", ".join(orfaos))

    def test_os_arquivos_de_referencia_tambem_sao_escritos_em_ingles(self):
        """references/ ENTRA NA IMAGEM, entao a regra de idioma vale igual.

        A causa raiz do 7a: dos 18 exemplos de fala que sobreviveram, 18 em
        portugues e zero em ingles, mais um banho de portugues na saida das
        ferramentas. Mover prosa em portugues de docs/ para dentro da imagem
        seria o mesmo defeito entrando pela porta da correcao.
        """
        faltas = []
        for nome, caminho in sorted(arquivos_de_referencia().items()):
            for i, bruta in enumerate(texto(caminho).splitlines(), 1):
                linha = sem_caminhos(bruta)
                achado = [m for m in MARCAS_PT if m in linha]
                achado += [p for p in _PALAVRA.findall(linha.lower())
                           if p in PALAVRAS_PT]
                if achado:
                    faltas.append(f"{nome}:{i}: {bruta.strip()[:90]}")
        self.assertEqual(
            [], faltas,
            "um references/ em portugues ensina o modelo a responder em "
            "portugues, que e o defeito de 7a:\n" + "\n".join(faltas))

    def test_nenhum_references_poe_frase_pronta_na_boca_do_agente(self):
        faltas = []
        for nome, caminho in sorted(arquivos_de_referencia().items()):
            for _verbo, citada in _VERBO_DE_DIZER.findall(corrido(caminho)):
                if _TEM_VAGA.search(citada):
                    continue
                faltas.append(f"{nome}: {citada[:70]!r}")
        self.assertEqual(
            [], faltas,
            "references/ descreve a FORMA; as palavras sao do modelo, na "
            "lingua da pessoa:\n" + "\n".join(faltas))


# ---------------------------------------------------------------------------
# Os procedimentos que a auditoria mandou voltar PARA DENTRO DA IMAGEM
# ---------------------------------------------------------------------------
#
# Itens 1, 2, 6 e 7 dos doze. Cada um existia so em docs/, apontado de dentro
# da imagem -- ou seja, removido. Cada pedaco abaixo e uma REGRA: muda o que o
# agente faz. A busca e no texto da imagem inteira (SKILL.md + references/),
# porque o que importa e o agente poder chegar la, nao em qual arquivo esta.

PROCEDIMENTOS_NA_IMAGEM = {
    # 1. O edit na batida (E1-E8). O COMANDO nunca saiu da imagem
    # (warden_beat.py existe); sumiram o procedimento e a linha na tabela.
    "o edit na batida": (
        "--shots",                                    # E1
        "on the file's own clock",                    # E1
        "whole beats",                                # E1
        "Two beats is the short shot, four the long", # E2
        "framed ONCE, after the splice",              # E3
        "mid-sentence",                               # E4
        "sampled from the SHOTS, never from a continuous stretch",  # E5
        "the source's speech is discarded",           # E6
        "drop_s",                                     # E7b
        "warden beat",                                # E8
    ),
    # 2. O contact sheet: a lista de quem olha, e a linha que sumiu da arvore
    # inteira -- ler o .ass para decidir se ha duas legendas nao funciona.
    "o contact sheet": (
        "only ours is in it",
        "-frames:v 1",
        "31s of a 706s request",
        "is the owner's to reopen",     # C60b: nao reponha o portao em silencio
    ),
    # 7. A fonte bloqueada: regra de seguranca nascida do aviso do yt-dlp.
    "a fonte bloqueada": (
        "/var/lib/hermes/warden/cookies.txt",
        "throwaway account",
        "never theirs",
        "you never say it will, and you never say it will not",
    ),
    # A medicao que impede recolocar a recusa por legenda suspeita (C36).
    "a legenda suspeita": (
        "184 of 706",
        "--keep",
    ),
    # 3. A procedencia de cada afirmacao sobre publicar (degrau 10 do demo).
    "a procedencia da publicacao": (
        "openapi.json",
        "No upload from this project ever went through TikTok or Instagram",
    ),
}


def imagem_corrida():
    """Todo texto da imagem, junto e com espaco colapsado."""
    return " ".join(
        " ".join(texto(c).split()) for c in sorted(textos_da_imagem().values()))


class ProcedimentosDentroDaImagem(unittest.TestCase):

    def test_cada_procedimento_esta_em_algum_arquivo_que_o_agente_alcanca(self):
        tudo = imagem_corrida()
        faltas = []
        for assunto, pedacos in sorted(PROCEDIMENTOS_NA_IMAGEM.items()):
            for pedaco in pedacos:
                if " ".join(pedaco.split()) not in tudo:
                    faltas.append(f"{assunto}: falta {pedaco!r}")
        self.assertEqual(
            [], faltas,
            "regra que so existe em docs/ e regra removida (LEI 1):\n"
            + "\n".join(faltas))

    def test_a_lista_de_quem_olha_um_clipe_tem_os_nove_itens(self):
        """C63a. Tres das nove voltaram na tabela de warden-check; seis nao.

        A lista original e `git show 565feea:warden-clip/SKILL.md`, 548-556.
        """
        achadas = [c for c in arquivos_de_referencia().values()
                   if "- [ ]" in texto(c)]
        self.assertTrue(
            achadas,
            "nenhum references/ carrega a lista de quem olha um clipe")
        itens = sum(texto(c).count("- [ ]") for c in achadas)
        self.assertEqual(
            9, itens,
            f"a lista tem nove itens e a imagem carrega {itens}")


# ---------------------------------------------------------------------------
# Degrau 6 do demo: os tres passos, com os links escritos
# ---------------------------------------------------------------------------
#
# LEI 3, degrau 6: nao ha chave, o agente nao finge que publicou, entrega o
# arquivo com titulo e descricao escritos e da os tres passos para ligar, COM
# OS LINKS ESCRITOS. O passo 1 estava escrito de tres jeitos diferentes, e o
# agente le dois deles no mesmo turno (a skill e a saida do comando).

_URL_UPLOAD_POST = re.compile(r"https?://[a-z.]*upload-post\.com[^\s`'\")]*")


def urls_do_upload_post(texto_qualquer):
    return {u.rstrip("/.,") for u in _URL_UPLOAD_POST.findall(texto_qualquer)}


class DegrauSeisDoDemo(unittest.TestCase):

    def test_o_passo_1_tem_uma_grafia_so_na_imagem_e_na_saida_do_comando(self):
        """A grafia medida e a do onboarding: `https://app.upload-post.com`.

        `git show 565feea:runtime/persona.md:404` e `warden.py:1367` usam essa;
        so a skill trazia `www.upload-post.com`, que nunca foi medida.
        """
        fonte_warden = texto(os.path.join(RAIZ, "warden-shared", "scripts",
                                          "warden.py"))
        achadas = urls_do_upload_post(imagem_corrida())
        achadas |= urls_do_upload_post(fonte_warden)
        # A pagina das chaves e outro endereco, e e passo 2.
        base = {u for u in achadas if not u.endswith("/api-keys")}
        self.assertEqual(
            {"https://app.upload-post.com"}, base,
            "o degrau 6 escreve UM endereco no passo 1, e o agente le a skill "
            "E a saida do comando no mesmo turno")

    def test_os_tres_passos_dizem_que_o_titulo_cabe_em_100_caracteres(self):
        """O titulo do degrau 6 e o que a pessoa cola no degrau 10.

        `warden post youtube --title` RECUSA acima de 100 (warden_post.py),
        nao trunca -- entao o demo trava num passo que o agente ja tinha dado
        por resolvido.
        """
        t = corrido(skills()["warden-shared"])
        inicio = t.find("No key: ONE message")
        self.assertNotEqual(-1, inicio, "a secao dos tres passos sumiu")
        secao = t[inicio:inicio + 1200]
        self.assertIn(
            "100", secao,
            "a secao dos tres passos nao diz que o titulo que o agente escreve "
            "no chat e o mesmo que vai em --title, e cabe em 100 caracteres")


# ---------------------------------------------------------------------------
# O numero que o dono escolheu para o `inbox`
# ---------------------------------------------------------------------------
#
# warden.py fixa INBOX_ESPERA_S = 14.0 ("sobe para 14 segundos", decisao do
# dono de 16/09, medida em dois pedidos reais). As skills mandavam passar
# `--wait 12`, que SOBRESCREVE a decisao dele com o numero velho.

class OInboxEsperaOQueODonoEscolheu(unittest.TestCase):

    def test_nenhuma_skill_sobrescreve_o_default_com_o_numero_velho(self):
        faltas = []
        for nome, caminho in sorted(textos_da_imagem().items()):
            for achado in re.findall(r"--wait\s+([0-9.]+)", texto(caminho)):
                if float(achado) != warden.INBOX_ESPERA_S:
                    faltas.append(f"{nome}: --wait {achado}")
        self.assertEqual(
            [], faltas,
            "warden.py:INBOX_ESPERA_S = {} e um `--wait` escrito na skill "
            "sobrescreve isso:\n{}".format(
                warden.INBOX_ESPERA_S, "\n".join(faltas)))

    def test_a_razao_dos_segundos_esta_escrita_junto_do_comando(self):
        """Sem a razao o numero e magico e o proximo agente o aumenta."""
        t = corrido(skills()["warden-shared"]) + corrido(skills()["warden-clip"])
        self.assertIn(
            "screen share", t,
            "nenhuma skill diz POR QUE a espera e curta: isto e mostrado numa "
            "chamada com a tela compartilhada")


# ---------------------------------------------------------------------------
# Item 10: a regra do filtro incompleto
# ---------------------------------------------------------------------------
#
# `warden prefs ask --group search` lista so o que falta. Sem esta regra o
# agente filtra `warden discover` por uma preferencia que ninguem deu.

class FiltroIncompleto(unittest.TestCase):

    def test_warden_run_proibe_filtrar_por_resposta_que_nao_existe(self):
        t = corrido(skills()["warden-run"])
        self.assertIn(
            "you do not filter by it", t,
            "warden-run/SKILL.md nao tem a regra do filtro incompleto: uma "
            "resposta de busca que ainda falta nao vira criterio")
        self.assertIn(
            "say which and move on", t,
            "e uma pagina que nao abriu se diz, em uma linha, e segue")


# ---------------------------------------------------------------------------
# Degrau 3 do demo: o formato de saida
# ---------------------------------------------------------------------------
#
# `grep -rn '9:16'` em persona e nas sete SKILL.md devolvia UMA linha, e era a
# `description:` do front-matter. A persona proibe afirmar numero que nao veio
# do `warden`, entao o agente nao tinha como responder o formato quando
# perguntassem.

class FormatoDaSaida(unittest.TestCase):

    def test_o_corpo_da_skill_diz_que_o_clipe_sai_em_9_16(self):
        corpo = texto(skills()["warden-clip"]).split("---", 2)[-1]
        self.assertIn(
            "9:16", corpo,
            "nenhuma regra do corpo de warden-clip diz o formato da saida; a "
            "unica ocorrencia era a description do front-matter")


# ---------------------------------------------------------------------------
# O livro de entregas nao prova chegada
# ---------------------------------------------------------------------------
#
# 16/09, msg 63 da sessao 20260916_181828_6e588b67: a prosa e os dois MP4 de
# 16 MB foram carimbados `delivered` e o dono nao recebeu nada. A evidencia em
# que `warden delivered` se apoia e escrita ANTES do envio -- medido no gateway
# desta imagem:
#
#     $ docker exec -u 10000 warden-demo-agent-1 \
#           grep -n "Sending video attachment" \
#           /opt/hermes/gateway/platforms/base.py
#     3880:  logger.info("[%s] Sending video attachment (%s) to %s", ...)
#
# e 3881 e o `result = await self.send_video(...)`. O log e TENTATIVA, nunca
# CHEGADA: os dois anexos saíram no MESMO milissegundo (18:22:41,674), contra
# 5s de intervalo no 7a que chegou de verdade.
#
# Consertar a ferramenta e de outra faixa. Aqui se cobra o TEXTO: enquanto uma
# skill mandar o agente ler aquilo como "o arquivo chegou", ele escreve
# "entregue" para a pessoa mesmo com a ferramenta consertada.

class LivroDeEntregasNaoProvaChegada(unittest.TestCase):

    def test_a_tabela_do_shared_nao_diz_que_o_delivered_responde_se_saiu(self):
        t = corrido(skills()["warden-shared"])
        self.assertNotIn(
            "did THIS path go out?", t,
            "warden-shared/SKILL.md ainda apresenta `warden delivered <clip>` "
            "como quem responde se o arquivo saiu. Ele le a linha que o "
            "gateway escreve ANTES do envio (base.py:3880)")
        self.assertRegex(
            t, r"(?i)`warden delivered <clip>` \| [^|]*announce",
            "a linha de `warden delivered <clip>` em warden-shared/SKILL.md "
            "nao diz que o que ela devolve e o ANUNCIO do gateway")
        self.assertRegex(
            t, r"(?i)`warden delivered <clip>` \| [^|]*(never arrival|not "
               r"arrival|not that it arrived)",
            "a linha de `warden delivered <clip>` nomeia o anuncio mas nao "
            "diz que ele NAO e chegada -- que e a parte que evita o carimbo")

    def test_a_tabela_do_check_nao_oferece_o_delivered_como_prova_de_chegada(self):
        t = corrido(skills()["warden-check"])
        self.assertNotIn(
            "whether a file arrived | `warden delivered`", t,
            "warden-check/SKILL.md lista `warden delivered` na coluna 'what "
            "proves it' para a afirmacao 'whether a file arrived'. Nada neste "
            "install prova chegada hoje: `messages.platform_message_id` esta "
            "NULL nas 31 mensagens do assistente")
        self.assertNotRegex(
            t, r"(?i)\| whether a file arrived \|",
            "a afirmacao 'whether a file arrived' continua na tabela do "
            "warden-check sem nada que a prove")

    def test_a_regra_de_contagem_do_clip_nao_afirma_o_que_pousou(self):
        """warden-clip mandava reportar 'the count of attachments that left'.

        Em 18:22:41,674 o gateway anunciou dois e zero saiu. A contagem que o
        agente tem e a das linhas `MEDIA:` que ele PÔS na mensagem final --
        essa ele sabe. Quantas pousaram no telefone do dono, nao.
        """
        t = corrido(skills()["warden-clip"])
        self.assertIn(
            "Rendered is not delivered", t,
            "sumiu a regra `Rendered is not delivered` de warden-clip")
        self.assertNotIn(
            "the count of attachments that left", t,
            "warden-clip/SKILL.md manda reportar 'the count of attachments "
            "that left' -- o agente nao tem esse numero, tem o que o gateway "
            "anunciou")


if __name__ == "__main__":
    unittest.main()
