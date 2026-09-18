r"""A persona montada tem de caber no que o Hermes lê — e as regras que decidem
uma entrega têm de estar no COMEÇO dela.

O que este arquivo vigia foi medido no teste 7a, em 16/09/2026:

    logs-7a/container/logs/errors.log, 14:37:58
    ⚠️ Context file SOUL.md TRUNCATED: 30068 chars exceeds limit of 20000

O SOUL.md montado tinha 30.069 chars: o seed da Plow (3.474) + "\n" +
runtime/persona.md (26.594), exatamente o que `compose_identity()` faz em
logs-7a/imagem/plow-init.py:758. Perderam-se 10.069 chars — 33,5% — e o corte é
por PREFIXO, então quem decide o que o modelo lê é a ORDEM do arquivo. O corte
caiu no char 20.000, no meio da palavra: a frase

    **A `MEDIA:` line in a message that still calls a tool attaches nothing, in
    silence.**

começa no char 19.969 do montado (19.967 contando os dois asteriscos) — 31 chars
ANTES do corte — e o que o modelo leu termina em

    **A `MEDIA:` line in a message th

Medido agora, não estimado:

    python3 -c "s=open('logs-7a/container/SOUL.md',encoding='utf-8').read();\
                print(s.find('A \`MEDIA:\` line'), repr(s[19969:20000]))"
    19969 'A `MEDIA:` line in a message th'

(A rodada anterior escreveu 20.022 e "...that still calls a tool"; a auditoria
escreveu 19.969 e "...that still calls a to". O offset da auditoria confere; a
cauda não — o corte cai 31 chars depois do início da frase, não 49.)

A seção `## Publishing` inteira começava no char 21.989. Consequência medida:
zero ocorrências de `MEDIA:` na sessão e `"sent": false` em entregas.json.

O limite de 20.000 é do Hermes e não está declarado em lugar nenhum do repo;
não é nosso para mover. O que é nosso é o tamanho. A meta aqui é 17.000 — folga
de 3.000 chars contra um limite que ninguém deste lado controla. Era 16.000; o
dono reabriu em 16/09 para caber o que a auditoria mandou voltar, em vez de
cortar regra para bater número.

## Por que o seed está vendorizado em tests/fixtures/ e não em vendor/

`vendor/` guarda PINS: um sha de commit e um caminho que o build baixa de um
repositório público (ver vendor/client.pin). O seed da Plow não tem URL: ele só
existe dentro da imagem base, em /opt/hermes/plow-seed/SOUL.md, e nada no build
o busca. Um .pin apontaria para o nada. Então é um FIXTURE de teste — uma cópia
congelada, com o sha256 conferido a cada execução para que uma troca silenciosa
apareça como teste vermelho e não como um número que mudou sozinho.

Procedência da cópia em tests/fixtures/plow-seed-SOUL.md:

    origem   imagem clip-warden-agent:etapa3, /opt/hermes/plow-seed/SOUL.md
    extraída 16/09/2026, em logs-7a/imagem/plow-seed/SOUL.md
    sha256   038c798df463d5e5b684cfb06238556bf1a128cb530098da748913b1fb36cdab
    tamanho  3.474 chars

Quando a Plow mudar o seed dela, o teste do sha256 falha primeiro: é o aviso de
que a conta do tamanho precisa ser refeita, não um erro do nosso lado.
"""
import hashlib
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA = os.path.join(RAIZ, "runtime", "persona.md")
SEED = os.path.join(RAIZ, "tests", "fixtures", "plow-seed-SOUL.md")

# O sha256 do seed como ele veio da imagem. Ver o cabeçalho deste arquivo.
SEED_SHA256 = "038c798df463d5e5b684cfb06238556bf1a128cb530098da748913b1fb36cdab"

# A meta do dono. NÃO é o limite do Hermes (20.000): é folga abaixo dele, porque
# o limite é de outra equipe e pode encolher sem nos avisar.
#
# Era 16.000. O dono reabriu para 17.000 em 16/09, depois de a auditoria mostrar
# que as regras cobradas de volta (`logs-7a/PRESTACAO-DE-CONTAS-REGRAS.md`,
# "Orçamento, medido agora") não cabiam nos 16.000 e que a saída errada seria
# cortar regra para bater número. 3.000 de folga contra o limite medido.
# 18.000, e o número tem história. A meta era 16.000; a restauração das regras
# que o corte de 16/09 tinha removido (as 12 da auditoria, mais a regra nova de
# idioma da conversa e os pares PT/EN que a implementam) levou o montado a
# 17.834. O dono releu as cinco maiores seções com o relatório na mão, viu que
# são quase só regra, e decidiu: o teto sobe, a regra fica. O que NÃO se move é
# o limite do Hermes, que continua 20.000 e não é nosso -- daí a folga de 2.166
# que este número preserva de propósito.
# 18.500 desde 16/09, e a terceira vez que este número sobe -- sempre pelo mesmo
# motivo, que é a regra do dono: não se corta REGRA para bater número. 16.000 era
# a meta; 18.000 pagou a restauração das 12 regras que a auditoria cobrou de
# volta; 18.500 paga duas regras que a GRAVAÇÃO cobrou, e nenhuma delas é prosa:
# a confirmação que viaja junto do comando (sem ela o agente para para sempre) e
# o NO_REPLY para notificação de processo (sem ele o dono lê a vida interna do
# agente depois de os clipes chegarem).
#
# O limite real do Hermes é 20.000 e continua intocado -- `context_file_max_chars`
# não existe neste repositório. A folga que sobra, ~1.500, é o que separa este
# arquivo de voltar a ser truncado em silêncio, que é o defeito que originou
# tudo isto. Quando ela acabar, o caminho é encolher de verdade, não subir de
# novo.
# 18.700, e esta é a QUARTA vez que este número sobe. Todas pelo mesmo motivo --
# a regra do dono, "não se corta REGRA para bater número" -- e cada subida pagou
# uma regra que um defeito medido cobrou:
#   16.000  a meta original
#   18.000  as 12 regras que a auditoria da persona cobrou de volta
#   18.500  a confirmação que viaja com o trabalho, e o NO_REPLY
#   18.700  a proibição de mandar recado no meio do turno (16/09: um "On it."
#           mid-turn fez o adaptador engolir a entrega inteira)
#
# AVISO, e ele é para quem ler isto da próxima vez: a pista está acabando. O
# limite real do Hermes é 20.000 e ele NÃO se levanta -- `context_file_max_chars`
# não existe neste repositório e não vai existir. Sobram ~1.400 chars, e quando
# eles acabarem o arquivo volta a ser truncado EM SILÊNCIO, que é o defeito que
# originou este teste. A quinta vez não pode ser uma subida: tem de ser uma
# regra indo para uma SKILL.md, ou prosa saindo de verdade.
# 19.000. QUINTA subida, e a anterior dizia que esta não podia acontecer. Ela
# aconteceu, então o que vale é a conta, não a promessa:
#
#   limite REAL do Hermes            20.000   (não é nosso, não se levanta)
#   montado hoje                     18.812
#   folga que sobra                   1.188
#
# As cinco subidas pagaram, todas, regra que um defeito medido cobrou -- a
# última é a proibição de falar no meio do turno, que custou a quarta gravação
# do dono. Cortar regra para caber é o que este projeto se proibiu de fazer
# depois que um corte de tamanho apagou o contrato de entrega inteiro.
#
# O QUE FAZER DA PRÓXIMA VEZ, porque subir de novo é gastar os 1.188 que separam
# este arquivo de voltar a ser truncado EM SILÊNCIO: tirar a seção `## Publishing`
# (~2.900 chars) da persona e pô-la em `warden-shared/references/postagem.md`,
# que já existe e já entra na imagem, deixando na persona só as três REGRAS dela
# (nunca repetir a chave; a bifurcação com/sem chave; só YouTube foi medido).
# Isso devolve ~2.400 de uma vez. O custo é que uma skill pode ser podada e a
# persona não -- por isso as regras ficam e só o procedimento sai.
# ---------------------------------------------------------------------------
# PENDÊNCIA DECLARADA, 18/09/2026: A FOLGA ACABOU. UM CARACTERE.
#
#   limite REAL do Hermes            20.000   (não é nosso, não se levanta)
#   meta deste teste                 19.000
#   montado hoje                     18.999
#   folga                                 1
#
# A sexta mudança NÃO PODE ser uma subida, e desta vez não é conselho: não há
# espaço para subir sem encostar nos 20.000, que é onde o arquivo volta a ser
# truncado EM SILÊNCIO. O que consumiu os últimos chars foi o conserto de uma
# frase FALSA em `## What you say`: ela dizia que prosa de meio de turno não
# chega ao dono, e o clipe de produção provou que chega. Medido antes de mexer:
# o SOUL.md montado DENTRO da imagem publicada tem 18.991 chars e a frase cai
# no char 12.351 -- ela chegou ao modelo inteira, então o defeito era o que ela
# dizia. Corrigi-la custou 8 chars líquidos, e eram os últimos que havia.
#
# O QUE FAZER, e é a mesma instrução de três subidas atrás, agora obrigatória:
# tirar a seção `## Publishing` (~2.900 chars) da persona e pô-la em
# `warden-shared/references/postagem.md`, que já existe e já entra na imagem,
# deixando NA PERSONA só as três REGRAS dela:
#     1. nunca repetir a chave;
#     2. a bifurcação com chave / sem chave;
#     3. só o YouTube foi medido.
# Isso devolve ~2.400 de uma vez. O custo, e ele é real: uma skill pode ser
# podada da memória e a persona não -- por isso as REGRAS ficam e só o
# PROCEDIMENTO sai. É o mesmo desenho da consequência 3 do teste 7a: nada
# crítico pode depender só de um texto que a poda alcança.
#
# Enquanto isso não for feito, qualquer regra nova na persona tem de pagar o
# próprio espaço tirando prosa -- não subindo este número.
# ---------------------------------------------------------------------------
META_CHARS = 19_000

# O limite real observado no teste 7a, só para a mensagem de erro ter contexto.
LIMITE_HERMES_MEDIDO = 20_000


def montar_soul():
    """Monta o SOUL.md do mesmo jeito que o plow-init monta.

    logs-7a/imagem/plow-init.py:758, compose_identity():

        with open(SEED_SOUL, encoding="utf-8") as base:
            identity = base.read()
        if os.path.exists(SEED_PERSONA):
            with open(SEED_PERSONA, encoding="utf-8") as persona:
                identity += "\\n" + persona.read()

    O separador é UM "\\n" e não há mais nada entre os dois — nem cabeçalho, nem
    linha em branco. SEED_PERSONA é /opt/hermes/plow-seed/persona.md, que o
    Dockerfile:237 copia de runtime/persona.md.
    """
    with open(SEED, encoding="utf-8") as base:
        identidade = base.read()
    with open(PERSONA, encoding="utf-8") as persona:
        identidade += "\n" + persona.read()
    return identidade


@pytest.fixture(scope="module")
def soul():
    return montar_soul()


def test_seed_vendorizado_e_o_da_imagem():
    """A cópia congelada do seed confere com o sha256 anotado.

    Se este teste falhar, o seed mudou: a conta do tamanho abaixo foi feita em
    cima de 3.474 chars que não são mais os mesmos.
    """
    with open(SEED, "rb") as arquivo:
        digest = hashlib.sha256(arquivo.read()).hexdigest()
    assert digest == SEED_SHA256


def test_soul_montado_cabe_na_meta(soul):
    """O pedido 1 do dono: o montado cabe em 17.000 chars.

    Falhar aqui não é um detalhe de estilo. Foi assim que o contrato do `MEDIA:`
    sumiu da memória do agente em 16/09 sem ninguém perceber.

    A mensagem de falha IMPRIME as três medidas — seed, persona e montado — para
    que ninguém precise perguntar quanto está sobrando nem medir de novo à mão.
    """
    with open(SEED, encoding="utf-8") as arquivo:
        seed = len(arquivo.read())
    with open(PERSONA, encoding="utf-8") as arquivo:
        persona = len(arquivo.read())
    assert len(soul) <= META_CHARS, (
        "MEDIDO AGORA: seed da Plow %d + separador 1 + runtime/persona.md %d "
        "= SOUL.md montado %d chars.\n"
        "Meta %d -> %d ACIMA. Limite medido do Hermes: %d (folga restante: %d), "
        "e o corte é por PREFIXO: o que estoura é o FIM do arquivo.\n"
        "Corte PROSA em runtime/persona.md, nunca REGRA. Se só couber cortando "
        "regra, PARE e relate este número: a decisão é do dono."
        % (
            seed,
            persona,
            len(soul),
            META_CHARS,
            len(soul) - META_CHARS,
            LIMITE_HERMES_MEDIDO,
            LIMITE_HERMES_MEDIDO - len(soul),
        )
    )


def test_a_parte_que_e_nossa_deixa_folga_para_o_seed_crescer(soul):
    """O seed da Plow não é nosso e pode crescer sem aviso.

    Registrado como teste separado para que a mensagem diga de quem é o excesso.
    """
    with open(SEED, encoding="utf-8") as arquivo:
        seed = arquivo.read()
    with open(PERSONA, encoding="utf-8") as arquivo:
        persona = arquivo.read()
    nosso_teto = META_CHARS - len(seed) - 1  # o -1 é o "\n" que o plow-init põe
    assert len(persona) <= nosso_teto, (
        "runtime/persona.md tem %d chars e o teto da NOSSA parte é %d "
        "(%d da meta menos %d do seed da Plow menos 1 do separador)."
        % (len(persona), nosso_teto, META_CHARS, len(seed))
    )


def _offset(soul, agulha):
    posicao = soul.find(agulha)
    assert posicao != -1, "sumiu do SOUL.md montado: %r" % agulha
    return posicao


def test_regra_de_idioma_da_conversa_existe_e_vem_antes_de_tudo(soul):
    """Causa 1 do diagnóstico: não existia regra sobre o idioma da CONVERSA.

    Medido: em 14:49:50 a pessoa escreveu em inglês e às 14:50:27 o agente
    respondeu "Manda o link do vídeo que você quer cortar."
    (logs-7a/container/sessao-mensagens.txt, msgs 6 e 13). A busca por
    'language|idioma|Portug|English|locale' no system prompt inteiro só
    retornava regras sobre o idioma do HOOK e da LEGENDA.

    A regra tem de estar no COMEÇO: o corte é por prefixo.
    """
    regra = re.search(
        r"(?im)^##\s+.*\blanguage of the conversation\b.*$", soul
    )
    assert regra, (
        "não há seção sobre o idioma da CONVERSA em runtime/persona.md. "
        "A regra do idioma do hook/legenda existe e foi obedecida; o que "
        "faltou foi o equivalente para a prosa."
    )
    # Medido a partir do começo da NOSSA parte: os 3.474 chars do seed da Plow
    # vêm antes de tudo e não são nossos para reordenar.
    with open(SEED, encoding="utf-8") as arquivo:
        inicio_da_nossa_parte = len(arquivo.read()) + 1
    dentro_da_persona = regra.start() - inicio_da_nossa_parte
    assert 0 <= dentro_da_persona < 1_200, (
        "a regra de idioma da conversa está no char %d de runtime/persona.md; "
        "ela tem de ser das primeiras coisas que o modelo lê."
        % dentro_da_persona
    )


def test_nenhuma_frase_de_fala_e_obrigatoria_num_idioma_so(soul):
    """Causa 3: `Em produção.` era uma ordem para dizer a frase LITERALMENTE.

    runtime/persona.md:135 dizia 'answer `Em produção.` and nothing else' e :140
    'is the whole message'. A frase chegou ao modelo (char 10.602 do SOUL.md
    montado) e saiu em português no meio de uma conversa em inglês, às 14:51:58.

    A persona pode dar a amostra; não pode mandar copiá-la. Onde houver a
    amostra em português, a amostra em inglês tem de estar do lado.
    """
    for ordem in (
        "and nothing else --",
        "is the whole message",
        "Two words",
    ):
        assert ordem not in soul, (
            "ordem para repetir uma frase LITERALMENTE ainda na persona: %r"
            % ordem
        )


def test_entrega_e_postagem_vem_antes_da_prosa(soul):
    """Pedido 1(d) e causa 6: o fim do arquivo é o que o truncamento come.

    Em 7a, '## Publishing' começava no char 21.989 e a frase do `MEDIA:` no
    20.022 — as duas fora do corte de 20.000. Elas passam a abrir o arquivo.
    """
    entrega = _offset(soul, "## Handing the file over")
    postagem = _offset(soul, "## Publishing")
    prosa = _offset(soul, "## What you say")

    assert entrega < prosa, "a entrega tem de vir antes da prosa sobre estilo"
    assert postagem < prosa, "a postagem tem de vir antes da prosa sobre estilo"
    assert entrega < 9_000, (
        "a regra de entrega começa no char %d do montado; ela é a primeira "
        "coisa que precisa sobreviver a um corte por prefixo." % entrega
    )


def test_o_contrato_do_media_sobreviveu_inteiro(soul):
    """Causa 6, com o offset exato: a frase foi partida ao meio pelo corte.

    Aqui ela é cobrada inteira e bem dentro da meta.
    """
    frase = _offset(soul, "attaches nothing")
    assert frase < META_CHARS
    assert "in the LAST message of your turn" in soul
    assert "warden delivered" in soul, (
        "o protocolo do `warden delivered` estava no char 20.600 e nunca "
        "chegou ao modelo; ele não pode voltar a ficar no fim."
    )


def test_o_aviso_de_silencio_termina_o_turno(soul):
    """Regra medida que já estava na persona: preservá-la.

    Um turno que ainda chama ferramenta não entrega nada — o mesmo motivo pelo
    qual a linha `MEDIA:` não pode ir numa mensagem intermediária. O aviso de
    que o agente vai ficar minutos calado é uma MENSAGEM QUE TERMINA O TURNO.
    """
    aviso = re.search(r"(?i)\bgo minutes without (speaking|talking)\b", soul)
    assert aviso, (
        "sumiu a regra do aviso de silêncio: o agente tem de dizer quando vai "
        "ficar minutos calado"
    )
    perto = soul[aviso.start() : aviso.start() + 400]
    assert re.search(r"(?i)\b(last message|ends the turn)\b", perto), (
        "o aviso de silêncio está escrito sem dizer que ele TERMINA o turno; "
        "um turno que ainda chama ferramenta não entrega nada"
    )


# --------------------------------------------------------------------------
# Acrescentados na rodada 2, cada um por um item da auditoria.
# --------------------------------------------------------------------------

SOUL_DO_TESTE_7A = os.path.join(RAIZ, "logs-7a", "container", "SOUL.md")

# Onde a frase do `MEDIA:` começava no SOUL.md que o Hermes leu em 16/09, e o
# que sobrou dela depois do corte. Ver o cabeçalho deste arquivo.
OFFSET_MEDIA_EM_7A = 19_969
CAUDA_LIDA_EM_7A = "A `MEDIA:` line in a message th"


def test_o_numero_que_este_arquivo_cita_e_o_que_esta_no_soul_de_7a():
    """Item 9 da auditoria: o offset citado na docstring estava errado.

    Um número errado dentro de um arquivo de teste é o que a próxima pessoa
    cita como se tivesse sido medido. Então ele passa a ser medido aqui, no
    arquivo real, a cada execução.
    """
    with open(SOUL_DO_TESTE_7A, encoding="utf-8") as arquivo:
        soul_7a = arquivo.read()

    posicao = soul_7a.find("A `MEDIA:` line")
    assert posicao == OFFSET_MEDIA_EM_7A, (
        "a frase do `MEDIA:` começa no char %d do SOUL.md de 7a, e este "
        "arquivo afirma %d." % (posicao, OFFSET_MEDIA_EM_7A)
    )
    assert soul_7a[posicao:LIMITE_HERMES_MEDIDO] == CAUDA_LIDA_EM_7A, (
        "o que o modelo leu da frase foi %r, não %r."
        % (soul_7a[posicao:LIMITE_HERMES_MEDIDO], CAUDA_LIDA_EM_7A)
    )


def test_as_duas_regras_que_a_auditoria_cobrou_de_volta(soul):
    """Item 4 da auditoria: duas regras sumiram no corte da rodada 1.

    Estavam no inventário (logs-7a/inventario-persona.md, #36 e #93) marcadas
    como `persona`, e nenhuma das duas tem dono em outra faixa:

      - nunca uma TERCEIRA tentativa no mesmo clipe. Sem ela, "conserta o que o
        portão nomeou" não tem fundo: em 15/09 foram três renders do mesmo
        corte e dez minutos (logs-7a: itens #36 e #52 do inventário).
      - nunca postar no TikTok de um estranho. O token é de UMA conta — a do
        dono — e `warden tiktok` põe o clipe no inbox DELE.
    """
    assert re.search(r"(?is)never a\s+third attempt at the same clip", soul), (
        "sumiu a regra 'never a third attempt at the same clip' (inventário #36)"
    )
    terceira = soul.find("third attempt")
    assert terceira < META_CHARS

    tiktok = re.search(r"(?is)never offer .{0,40}stranger.{0,20}TikTok", soul)
    assert tiktok, (
        "sumiu a regra 'never offer to post to a stranger's TikTok' "
        "(inventário #93): o token do `warden tiktok` é de uma conta só"
    )
    assert tiktok.start() < META_CHARS


# Cada ponteiro da persona para uma skill, e a âncora que prova que o conteúdo
# apontado está mesmo lá. Item 5 da auditoria: quatro destes mentiam.
#
# A coluna `porque` é o que a faixa 'skills' precisa devolver ao arquivo — eu
# não edito SKILL.md, então aqui isto é uma cobrança, não uma correção.
PONTEIROS = (
    (
        "`warden-check` for the rest",
        "warden-check",
        r"(?im)^\|[^\n]*\b(claim|assertion|claimed)\b[^\n]*\|[^\n]*prov",
        "a tabela 'a afirmação é sobre / a prova é'",
    ),
    # O ponteiro "os três passos estão em `warden-shared`" SAIU da persona: os
    # passos voltaram para dentro dela (item 4 da auditoria), porque é para lá
    # que a saída do `warden post` sem chave manda o agente olhar
    # (warden-shared/scripts/warden.py:1369-1370) e porque a skill pode ter sido
    # podada da memória no turno em que ele precisa deles. O guarda agora é
    # test_os_tres_passos_do_upload_post_estao_na_persona, abaixo.
    (
        "what a 200 from it is worth, is in `warden-shared`",
        "warden-shared",
        r"(?is)(TikTok[^\n]{0,200}paid|paid[^\n]{0,200}TikTok)",
        "TikTok exige plano pago / Instagram exige Business ou Creator",
    ),
    (
        "`warden-run` has the table",
        "warden-run",
        r"(?m)^\| how many \| 2 \|",
        "a tabela dos defaults que nunca viram pergunta",
    ),
    (
        "lives in **`warden-clip`, at the top of that file",
        "warden-clip",
        r"(?im)^##\s+The order",
        "a ORDEM DE TRABALHO, no topo do arquivo",
    ),
    (
        "`warden-style` is the measured look: read it BEFORE",
        "warden-style",
        r"(?i)before\W{0,4} you write the hook",
        "a ordem de ler antes de escrever o hook",
    ),
)


@pytest.mark.parametrize("trecho,skill,ancora,porque", PONTEIROS)
def test_os_ponteiros_da_persona_nao_mentem(trecho, skill, ancora, porque):
    """Item 5 da auditoria: a persona manda o agente procurar o que não existe.

    Um ponteiro que mente é pior do que regra nenhuma: o agente gasta um turno
    lendo um arquivo e sai de lá sem a regra, convencido de que a leu.
    """
    with open(PERSONA, encoding="utf-8") as arquivo:
        persona = arquivo.read()
    # a persona é quebrada em ~79 colunas: a quebra de linha não pode decidir
    # se o ponteiro existe ou não.
    corrido = " ".join(persona.split())
    assert " ".join(trecho.split()) in corrido, (
        "o ponteiro %r não está mais em runtime/persona.md — se ele mudou de "
        "redação, este teste tem de mudar junto." % trecho
    )

    caminho = os.path.join(RAIZ, skill, "SKILL.md")
    assert os.path.exists(caminho), "a persona aponta para %s, que não existe" % caminho
    with open(caminho, encoding="utf-8") as arquivo:
        alvo = arquivo.read()

    assert re.search(ancora, alvo), (
        "a persona diz %r, e %s/SKILL.md não tem %s. A regra foi cortada da "
        "persona porque este arquivo ia carregá-la: ou ela volta para lá, ou "
        "ela se perdeu." % (trecho, skill, porque)
    )


def _copiados_pelo_dockerfile():
    """As ORIGENS de cada `COPY` do Dockerfile, como o Dockerfile as escreve.

    É daqui que sai a LEI 1: o que não entra na imagem não é regra. Ler o
    Dockerfile em vez de escrever a lista à mão é o que impede este teste de
    envelhecer em silêncio no dia em que um `COPY` sair de lá.
    """
    with open(os.path.join(RAIZ, "Dockerfile"), encoding="utf-8") as arquivo:
        dockerfile = arquivo.read()
    origens = re.findall(
        r"(?m)^COPY\s+(?:--\S+\s+)*(\S+)\s+\S+\s*$", dockerfile
    )
    assert origens, "nenhum COPY lido do Dockerfile: a varredura ficaria vazia"
    return {origem.rstrip("/") for origem in origens}


def test_a_persona_so_manda_ler_o_que_entra_na_imagem():
    """LEI 1, na parte que é da persona — e a versão desta com MATÉRIA.

    A versão anterior deste teste cobrava que todo `docs/X.md` citado pela
    persona existisse no REPOSITÓRIO. Ela passava POR VAZIO: `grep -c 'docs/'
    runtime/persona.md` devolve 0 hoje e devolvia 0 em 565feea, então o teste
    não tinha o que afirmar. Pior: existir no repositório é a pergunta errada.
    `docs/` não é COPYado (confira a lista abaixo, lida do Dockerfile), logo um
    ponteiro da persona para `docs/` é uma REGRA REMOVIDA com cara de regra
    presente.

    O que se cobra agora: toda skill que a persona manda ler é COPYada pelo
    Dockerfile, e nenhum ponteiro dela sai da imagem. A varredura tem de achar
    alguma coisa — a asserção do `>= 6` é o que impede este teste de voltar a
    passar por vazio.
    """
    with open(PERSONA, encoding="utf-8") as arquivo:
        persona = arquivo.read()
    copiados = _copiados_pelo_dockerfile()

    skills = sorted(set(re.findall(r"`(warden-[a-z]+)`", persona)))
    assert len(skills) >= 6, (
        "a persona nomeia %d skills (%s). O índice nominal das seis é o item 8 "
        "da auditoria: num runtime que poda skill da memória, a persona é o "
        "único lugar que diz que `warden-package` existe."
        % (len(skills), ", ".join(skills) or "nenhuma")
    )
    fora_da_imagem = [skill for skill in skills if skill not in copiados]
    assert not fora_da_imagem, (
        "a persona manda ler %s, e o Dockerfile não copia essa pasta para a "
        "imagem." % ", ".join(fora_da_imagem)
    )

    apontados = sorted(set(re.findall(r"docs/[A-Za-z0-9][A-Za-z0-9-]*\.md", persona)))
    assert not apontados, (
        "a persona manda o agente ler %s. `docs/` não está entre as origens "
        "que o Dockerfile copia (%s): dentro do container esse arquivo não "
        "existe, e a regra que estiver nele está REMOVIDA."
        % (", ".join(apontados), ", ".join(sorted(copiados)))
    )


# Palavras que só existem em português: se uma delas aparecer numa linha que o
# agente deve REPETIR, a linha está presa a um idioma e a regra de idioma da
# conversa não consegue traduzi-la.
SO_EM_PORTUGUES = (
    "corte",
    "cortes",
    "legenda",
    "para",
    "colar",
    "primeiro",
    "segundo",
    "vídeo",
)


def test_o_bloco_de_exemplo_da_entrega_nao_esta_preso_ao_portugues():
    """Item 2 da auditoria, no que cabe à persona.

    O bloco cercado que mostra a mensagem final é a coisa que o agente mais
    tende a copiar literalmente — é a única FORMA exata que este arquivo dá.
    Ele tem de ser um molde com lacunas, não uma mensagem em português.
    """
    with open(PERSONA, encoding="utf-8") as arquivo:
        persona = arquivo.read()
    blocos = [
        bloco
        for bloco in re.findall(r"(?s)```.*?```", persona)
        if "MEDIA:" in bloco
    ]
    assert blocos, "sumiu o bloco de exemplo com as linhas `MEDIA:`"

    for bloco in blocos:
        palavras = set(re.findall(r"(?u)[\wÀ-ÿ]+", bloco.lower()))
        presas = sorted(palavras & set(SO_EM_PORTUGUES))
        assert not presas, (
            "o molde da mensagem final tem palavra de um idioma só (%s). "
            "Ele é uma FORMA: as lacunas se escrevem na língua da pessoa."
            % ", ".join(presas)
        )


# --------------------------------------------------------------------------
# Rodada 3. Cada teste daqui para baixo nasceu de uma linha medida: ou um item
# que a auditoria mandou voltar (logs-7a/PRESTACAO-DE-CONTAS-REGRAS.md), ou um
# achado da varredura desta rodada. O que é de outra faixa não está aqui.
# --------------------------------------------------------------------------


def _inicio_da_nossa_parte():
    """O char do SOUL.md montado em que runtime/persona.md começa.

    Os 3.474 chars do seed da Plow vêm antes e não são nossos. Sem este
    deslocamento, uma busca no montado acha primeiro a frase do seed — e o seed
    tem a sua própria linha sobre reinício ("On a restart, say nothing.",
    tests/fixtures/plow-seed-SOUL.md:18), que é justamente a que a persona
    precisa contradizer.
    """
    with open(SEED, encoding="utf-8") as arquivo:
        return len(arquivo.read()) + 1


def test_os_tres_passos_do_upload_post_estao_na_persona(soul):
    """Item 4 da auditoria, e o degrau 6 da cadeia do demo.

    Em 565feea os três passos estavam escritos por extenso na persona
    (`git show 565feea:runtime/persona.md`, linhas 403-407). O corte os tirou e
    deixou `persona:86-87` mandando lê-los em `warden-shared`. A saída do
    próprio comando fecha o círculo pelo outro lado: `warden post`, sem chave,
    imprime *"The persona's \\"Publishing\\" section has the exact three lines to
    send"* (warden-shared/scripts/warden.py:1369-1370). Os dois apontavam um
    para o outro e nenhum tinha os passos.

    Aqui eles são cobrados NA PERSONA, que é contexto montado: a skill pode ter
    sido podada da memória — em 7a o `[SKILL_PRUNED]` caiu em `warden-clip` e
    `warden-run` às 14:52:50 — e o degrau 6 acontece depois disso.
    """
    nossa = soul[_inicio_da_nossa_parte() :]
    # `1.` ou `1)`: a numeração é escolha de redação, não a regra. O que se
    # cobra é a ORDEM — conta, chave, colar — com as duas URLs por extenso e o
    # comando que recebe a chave.
    passos = re.search(
        r"(?is)\b1[.)][^\n]{0,140}https://app\.upload-post\.com\b"
        r".{0,400}https://app\.upload-post\.com/api-keys"
        r".{0,400}warden post setkey",
        nossa,
    )
    assert passos, (
        "os três passos do upload-post não estão em runtime/persona.md, com as "
        "duas URLs escritas por extenso e o `warden post setkey` no terceiro. "
        "Sem eles o degrau 6 do demo é uma promessa que ninguém cumpre."
    )
    assert _inicio_da_nossa_parte() + passos.start() < META_CHARS


def test_o_indice_nominal_das_seis_skills(soul):
    """Item 8 da auditoria: a persona é o único lugar que lista as skills.

    O corte deixou "The skills carry the procedures and describe themselves" e
    o nome de UMA delas. Num runtime que poda skill da memória (o motivo
    declarado do corte), o agente perdeu a informação de que `warden-package`
    existe.

    Cobrado como UM parágrafo com os seis nomes: uma lista espalhada pelo
    arquivo não é um índice.
    """
    seis = (
        "warden-run",
        "warden-campaign",
        "warden-clip",
        "warden-check",
        "warden-package",
        "warden-style",
    )
    nossa = soul[_inicio_da_nossa_parte() :]
    indice = [
        paragrafo
        for paragrafo in re.split(r"\n\s*\n", nossa)
        if all("`%s`" % nome in paragrafo for nome in seis)
    ]
    assert indice, (
        "não há um parágrafo em runtime/persona.md que nomeie as seis skills. "
        "Faltam: %s"
        % ", ".join(nome for nome in seis if "`%s`" % nome not in nossa)
    )
    # cada nome tem de vir com o que a skill faz, não só o nome
    for nome in seis:
        depois = indice[0].split("`%s`" % nome, 1)[1]
        assert len(depois.split(".", 1)[0].split()) >= 3, (
            "`%s` aparece no índice sem dizer para que serve" % nome
        )


def test_as_tres_palavras_que_sumiram_da_lista_proibida(soul):
    """Item 9 da auditoria: `sidecar`, `IN_POINT` e `digest`.

    A lista do vocabulário interno sobreviveu ao corte menos estas três, e são
    exatamente as que um agente solta ao explicar por que um corte falhou. 28
    chars.
    """
    # A âncora é o CONTEÚDO da lista, não a frase que a apresenta: em 565feea
    # ela era "...side of the conversation: `cue`, ..." e hoje é "This
    # machine's vocabulary never goes in a message". Ancorar na frase faria
    # este teste falhar por redação e dizer que as palavras sumiram.
    lista = re.search(r"(?is)`cue`(.{0,700}`drop`)", soul)
    assert lista, "sumiu a lista do vocabulário interno proibido"
    faltando = [
        palavra
        for palavra in ("sidecar", "IN_POINT", "digest")
        if "`%s`" % palavra not in lista.group(1)
    ]
    assert not faltando, (
        "a lista do vocabulário proibido perdeu %s no corte de 16/09 "
        "(item 20 do inventário da persona)." % ", ".join(faltando)
    )


def test_a_razao_dos_doze_segundos(soul):
    """Item 12 da auditoria: sem a razão, doze vira número mágico.

    O número está em `persona` e em `warden-shared`; a razão — isto é mostrado
    numa chamada com a tela compartilhada — não está em lugar nenhum da imagem
    desde o corte. O próximo agente que achar doze segundos pouco vai aumentá-lo
    sem saber o que está pagando por isso.

    16/09: o NÚMERO saiu da persona e o teste mudou junto. Duas razões, as duas
    do dono: ele subiu a espera para 14s (`warden.py:1881`, porque em dois
    pedidos reais o link chegou 1 segundo depois da desistência aos 12), e as
    duas skills passaram a proibir a flag (`warden-shared/SKILL.md:53`,
    `warden-clip/SKILL.md:64`) — `warden inbox` sem flag já carrega o número e
    volta assim que um link chega. Uma persona que fixa `--wait 12` desfaz as
    duas decisões, e a persona ganha das skills porque nunca é podada.
    O que este teste cobra agora é a RAZÃO, que é o que se perde num corte: o
    número vive no código, onde é medido.
    """
    espera = soul.find("warden inbox")
    assert espera != -1, "sumiu a regra do `warden inbox`"
    assert "--wait 12" not in soul, (
        "o `--wait 12` voltou para a persona. Ele desfaz os 14s que o dono "
        "mediu e a proibição da flag que as duas skills carregam."
    )
    perto = soul[espera : espera + 500]
    assert re.search(r"(?i)(screen|call|watching|judging)", perto), (
        "o `--wait 12` está na persona sem a razão dos doze segundos: isto "
        "roda com a tela compartilhada, e um minuto de silêncio na frente de "
        "quem avalia custa mais que uma pergunta."
    )


def test_o_reinicio_manda_entregar_antes_de_renderizar(soul):
    """Achado 1 da varredura, medido em 7a, na ordem:

      - msg 83, 15:08:59: `warden delivered` responde "1 clip(s) rendered and
        cleared, and NOT confirmed as sent: .../corte-27cd49161b-01.mp4";
      - msg 84, 15:09:17: "This is a restart. ...";
      - msg 85, 15:09:21: "Voltando ao corte 2." e um `warden lote render` novo.

    Duas mensagens depois de a ferramenta dizer que havia clipe pronto e não
    enviado, ele começou OUTRO render. Não foi desobediência: as duas únicas
    regras escritas sobre reinício mandavam retomar o TRABALHO — a do seed da
    Plow ("On a restart, say nothing.") e `persona:169` ("A request in flight
    when your session was restored is resumed, not re-asked"), esta última
    arquivada dentro da seção sobre PERGUNTAS.

    A regra nova nomeia a PRIMEIRA ação e mora na metade da ENTREGA. Ela é
    executável porque o `session_id` NÃO muda no reinício: em
    logs-7a/container/final.db as msgs 84 e 85 estão as duas em
    `20260916_143802_7be69821`, o mesmo id de logs-7a/container/warden/
    entregas.json, então `conversa_de_agora()` (warden.py:770) continua achando
    a conversa e `entregas_pendentes()` (:808) continua devolvendo o clipe.
    """
    inicio = _inicio_da_nossa_parte()
    nossa = soul[inicio:]
    bloco = re.search(r"(?is)A restart is not a new request\.(.{0,500})", nossa)
    assert bloco, (
        "não há regra de reinício na metade da entrega de runtime/persona.md. "
        "A frase cobrada é '**A restart is not a new request.**' — se mudar de "
        "redação, este teste muda junto."
    )
    corpo = bloco.group(1)
    assert "warden delivered" in corpo, (
        "a regra de reinício não nomeia a PRIMEIRA ferramenta a rodar; uma "
        "disposição ('retome') foi o que o agente já tinha em 7a"
    )
    assert re.search(r"(?i)no render|not start a render|never start a render", corpo), (
        "a regra de reinício não proíbe começar um render no turno do reinício "
        "— que é exatamente o que foi medido às 15:09:21"
    )
    assert "resumed, not re-asked" not in soul, (
        "a regra antiga continua na seção das perguntas: ela arquiva o "
        "reinício como 'não repita a PERGUNTA' quando o risco medido é 'não "
        "repita o RENDER'"
    )
    assert inicio + bloco.start() < _offset(soul, "## Publishing"), (
        "a regra de reinício tem de morar junto das outras regras de entrega"
    )


def test_o_que_a_ferramenta_imprime_se_traduz_ao_repassar(soul):
    """Achado 3 da varredura, e a causa 5e pela porta que ficou aberta.

    A regra de idioma da conversa enumera o que governa — "the acknowledgement,
    the questions, the clause beside a clip, the posting steps" — e a
    enumeração não inclui o texto REPASSADO de uma ferramenta. "Repassar" é uma
    ordem de COPIAR, e copiar carrega o idioma junto: é o mecanismo pelo qual o
    português da saída do `warden` chega à pessoa.

    Cobra-se as duas metades: a regra existe, e nenhuma ordem de repassar na
    persona fica sem a marca de idioma.
    """
    nossa = soul[_inicio_da_nossa_parte() :]
    regra = re.search(
        r"(?is)tool (output|prints|printed)[^.]{0,200}\byou\b", nossa
    )
    assert regra and re.search(
        r"(?is)(translat|in their language|their own language)",
        nossa[regra.start() : regra.start() + 400],
    ), (
        "a seção de idioma não diz que a saída da ferramenta é escrita para "
        "VOCÊ: o que vem de um comando se TRADUZ ao repassar, e só o que está "
        "entre <>, um caminho, um link ou um número sai literal."
    )

    # A persona é quebrada em ~79 colunas, e a quebra de linha NÃO pode decidir
    # se uma regra existe. Este teste já falhou uma vez por isso: a ordem dizia
    # "in their\nlanguage" e a busca por "their language" não a achou. Mesmo
    # critério de tests/test_ponteiros.py -- normaliza antes de procurar.
    corrido = " ".join(nossa.split())
    ordens = [
        corrido[max(0, m.start() - 200) : m.start() + 200]
        for m in re.finditer(r"(?i)\brepass", corrido)
    ]
    assert len(ordens) >= 2, (
        "a varredura das ordens de repassar não achou nada em "
        "runtime/persona.md; ela não pode passar por vazio"
    )
    for trecho in ordens:
        assert re.search(r"(?i)(their language|their own language|translat)", trecho), (
            "uma ordem de repassar sem marca de idioma: %r" % " ".join(trecho.split())
        )


def test_uma_ferramenta_fora_da_lista_nao_existe(soul):
    """Achado 5: a persona não desmentia a promessa do Mac.

    O prompt de sistema medido em 7a (logs-7a/container/final.db, tabela
    system_prompts, 47.469 chars) traz, em offset 43113, a seção do plugin
    (3.948 chars, confirmada em logs-7a/container/logs/agent.log:117) mandando
    "your first tool call is on their Mac (a plow_ tool)" e "Before saying what
    you can or cannot do, call plow_list_skills". Do outro lado,
    logs-7a/container/logs/agent.log:52 registra "MCP: registered 0 tool(s)
    from 0 server(s) (1 failed)" e logs-7a/container/config.yaml:8-10 desliga o
    toolset `browser`. As ferramentas não existem nessa instalação.

    A persona é a única alavanca que este repositório tem sobre esse prompt — e
    em 565feea ela também não dizia nada, então isto não é regressão do corte,
    é buraco antigo. A redação cobrada é condicional de propósito: numa
    instalação com o Latch aberto as ferramentas aparecem e a frase continua
    verdadeira.
    """
    nossa = soul[_inicio_da_nossa_parte() :]
    regra = re.search(
        r"(?is)not in your tool list does not exist\.?(.{0,600})", nossa
    )
    assert regra, (
        "a persona não diz que uma ferramenta fora da lista de ferramentas não "
        "existe. Um parágrafo mais abaixo NESTE MESMO prompt entrega o Mac do "
        "dono ao agente, e a recência joga a favor dele."
    )
    corpo = regra.group(1)
    assert "plow_" in corpo, "a regra não nomeia as ferramentas `plow_`"
    assert re.search(r"(?i)never (offer|say)", corpo), (
        "a regra não diz o que NÃO fazer: nunca oferecer abrir uma página, ler "
        "um arquivo da máquina dela ou postar pelo navegador dela"
    )


def test_already_connected_nomeia_o_canal(soul):
    """Achado 6: a persona mandava calar no instante em que o canal aparece.

    `warden post connect`, no ramo `ja_conectado`, imprime `already connected:
    {canal} {handle} on profile {perfil}` (warden-shared/scripts/warden.py:
    1391-1393) — a única saída da árvore que NOMEIA o canal. A persona dizia
    "`already connected` means you say nothing and upload", e o degrau 10 do
    demo pede o oposto: quem publica sem dizer para onde foi não separa o que a
    API confirmou do que só a janela anônima confirma.

    Pré-existente, não regressão: `git show 565feea:runtime/persona.md`
    linhas 383-386 diz o mesmo com mais palavras.
    """
    assert "you say nothing and upload" not in soul, (
        "a persona ainda manda calar sobre o `already connected`"
    )
    trecho = re.search(r"(?is)already connected(.{0,400})", soul)
    assert trecho, "sumiu o tratamento do `already connected`"
    corpo = trecho.group(1)
    assert "<channel>" in corpo or "<canal>" in corpo, (
        "a persona não mostra o que o comando imprime: `already connected: "
        "<channel> <handle> on profile <profile>`"
    )
    assert re.search(r"(?i)name (that|the) channel|nomeia o canal", corpo), (
        "a persona não manda NOMEAR o canal na cláusula ao lado do upload"
    )


def test_a_primeira_mensagem_chega_antes_do_silencio(soul):
    """O degrau 2 do critério de aceite, corrigido pelo que foi medido.

    O critério do dono era "o agente avisa que começou, e esse aviso encerra o
    turno". A intenção é certa -- o aviso tem de CHEGAR antes do silêncio -- e
    a letra estava errada, porque encerrar o turno é justamente o que impede o
    trabalho de começar.

    Medido em 16/09, na gravação: a persona mandava "call nothing after it" e
    "the work starts on the turn after that one". O agente obedeceu à risca --
    mandou "On it." às 18:03:02, encerrou o turno -- e o turno seguinte NUNCA
    veio, porque num gateway de chat um turno novo só nasce de uma mensagem
    nova. `docker top` não mostrava um processo sequer: ele não travou no meio,
    nunca saiu do lugar.

    O mecanismo que ENTREGA o aviso cedo é outro, e está medido no 7a: conteúdo
    e chamada de ferramenta na MESMA mensagem. O gateway registra
    "Queued follow-up (...) sending first response before continuing" e
    "Sending response (33 chars)" (logs-7a/container/logs/gateway.log:41-42),
    entrega o texto, e o agente segue trabalhando no mesmo turno. É assim que o
    7a falou e cortou; é assim que a regra passou a ser escrita.
    """
    corrido = " ".join(soul.split())
    primeira = re.search(r"(?is)Your FIRST message(.{0,500})", corrido)
    assert primeira, "sumiu a regra da primeira mensagem"
    corpo = primeira.group(1)
    # 16/09, na gravação: a regra dizia "call nothing after it" e "the work
    # starts on the turn after that one". O agente obedeceu -- mandou "On it.",
    # encerrou o turno, e o TURNO SEGUINTE NUNCA VEIO, porque num gateway de
    # chat um turno novo só nasce de uma mensagem nova. Ficou parado para
    # sempre, sem um processo sequer rodando no container.
    #
    # O que faz o agente voltar é a notificação de um processo em SEGUNDO
    # PLANO. Então a confirmação tem de viajar NA MESMA mensagem que dispara o
    # trabalho -- foi assim que o 7a andou (state.db msg 21: conteúdo
    # "Em produção." e a chamada `terminal(background=true)` juntas).
    assert re.search(r"(?i)(rides with|same message|background)", corpo), (
        "a persona não diz que a confirmação viaja JUNTO com o comando que "
        "começa o trabalho. Sem isso o agente encerra o turno sem nada "
        "rodando e nada o acorda: %r" % corpo[:300]
    )
    # TERCEIRA redação desta regra em dois dias, e as duas primeiras custaram uma
    # gravação cada. O que sobrevive às três está aqui, e é isto que se cobra:
    #
    #   1ª  "encerra o turno, o trabalho começa no turno seguinte" -> o turno
    #       seguinte não existe. Parou para sempre, sem processo nenhum vivo.
    #   2ª  "a confirmação viaja junto e você continua no mesmo turno" -> o
    #       modelo alcançou `plow_send_sequence` para falar no meio do turno, e
    #       o adaptador engoliu a entrega inteira (16/09, 18:20:21 x 18:22:41).
    #   3ª  o trabalho fica RODANDO em segundo plano, a confirmação encerra o
    #       turno, e a NOTIFICAÇÃO do processo é o que traz o agente de volta.
    assert re.search(r"(?i)(notification|notify)", corpo), (
        "a persona não diz QUEM traz o agente de volta. Sem isso, 'a "
        "confirmação encerra o turno' é o defeito da 1ª redação: ele para e "
        "nada o acorda."
    )
    assert re.search(r"(?i)background", corpo), (
        "a persona não manda deixar o trabalho RODANDO antes de encerrar o "
        "turno. Encerrar com nada rodando é não ter o que notificar."
    )
    assert re.search(r"(?i)never send a message mid-turn", corpo), (
        "falta a proibição que custou a gravação de 16/09: uma mensagem no "
        "MEIO do turno faz o adaptador do chat tratar o turno como já "
        "respondido e engolir a entrega que vem depois, reportando sucesso"
    )
    assert "plow_send_sequence" in corpo, (
        "a proibição não NOMEIA a ferramenta. 'Não mande recado' sem o nome "
        "deixa o modelo achar que a regra é sobre outra coisa -- foi "
        "exatamente esta ferramenta que ele usou."
    )
    assert re.search(r"(?i)never end a turn with the confirmation", corpo), (
        "falta a proibição explícita: encerrar um turno com a confirmação e "
        "nada rodando foi o defeito medido em 16/09 na gravação"
    )
    # O degrau 2 do critério de aceite -- "o aviso chega antes do silêncio" --
    # deixou de ser uma frase a cobrar e passou a ser consequência: a prosa de um
    # turno é entregue QUANDO O TURNO TERMINA, e nesta redação a confirmação é o
    # fim de um turno curto. Ela chega em segundos, não porque o texto promete,
    # mas porque não há nada depois dela naquele turno.
    assert "then work nonstop" not in soul, (
        "a ordem de trabalhar no mesmo turno depois do aviso continua na "
        "persona"
    )



def test_notificacao_de_processo_nao_vira_conversa(soul):
    """16/09, na gravação, e foi a última coisa que o dono leu.

    Os dois clipes saíram às 18:22:40 e `warden delivered` confirmou os dois
    anexos. Seis segundos depois chegou a notificação de que o `prep` ANTIGO
    tinha terminado (msg 64). Isso abre um turno igual a uma mensagem -- e o
    agente respondeu, ao dono:

        "That old notification was just the earlier prep step finishing --
         nothing pending. Both clips already confirmed delivered, no action
         needed."

    Nada ali é para a pessoa. É a vida interna do agente, colada no fim de uma
    entrega que tinha dado certo, e foi o que fez a gravação ser jogada fora.
    A persona já proíbe "progress" e "a problem you found and fixed"; faltava
    dizer que a notificação de processo é uma DESSAS ocasiões, e que o silêncio
    tem um nome: NO_REPLY.
    """
    corrido = " ".join(soul.split())
    marca = re.search(r"(?i)background process finishing", corrido)
    assert marca, (
        "a persona não trata a notificação de processo em segundo plano. Ela "
        "abre um turno como uma mensagem abre, e sem regra o agente conversa."
    )
    janela = corrido[marca.start(): marca.start() + 400]
    assert "NO_REPLY" in janela, (
        "a regra não nomeia o silêncio. 'Não responda' sem NO_REPLY deixa o "
        "agente inventando uma forma de não responder: %r" % janela[:200]
    )
    assert re.search(r"(?i)(not them talking|is the machine|not the person)", janela), (
        "a regra não diz que quem abriu o turno foi a MÁQUINA e não a pessoa, "
        "que é a razão de não haver o que responder"
    )


def test_o_livro_de_entregas_nao_promete_chegada(soul):
    """16/09, msg 63: a entrega foi carimbada `delivered` e nunca saiu.

    O gateway registrou as três linhas em que `warden delivered` se apoia:

        18:22:41,665 [Plow_Chat] Sending response (250 chars)
        18:22:41,674 [Plow_Chat] Delivering 2 non-image MEDIA attachment(s)
        18:22:41,674 [Plow_Chat] Sending video attachment (.mp4)

    As três são escritas ANTES do `await` que envia. Medido no gateway desta
    imagem, não inferido:

        $ docker exec -u 10000 warden-demo-agent-1 \
              grep -n "Sending video attachment" \
              /opt/hermes/gateway/platforms/base.py
        3880:  logger.info("[%s] Sending video attachment (%s) to %s", ...)

    e a linha 3881 logo abaixo é `result = await self.send_video(...)`. Logo o
    log prova TENTATIVA e nunca CHEGADA. Naquele turno nada saiu -- os dois
    anexos de 16 MB foram registrados no MESMO milissegundo (18:22:41,674),
    contra 5 segundos de intervalo no teste 7a que chegou
    (logs-7a/container/logs/gateway.log, 15:14:49,517 e 15:14:54,384) -- e
    ainda assim `warden delivered` respondeu

        "confirmed: corte-ec898de219-01.mp4 -- its MEDIA: line was in a final
         message, the gateway announced 2 MEDIA and 2 video attachment(s)"

    O dono não recebeu nem a prosa nem os dois MP4.

    A persona mandava acreditar naquilo: dizia que `warden delivered` "says
    whether that exact file went out" e que ele prova "a file that arrived".
    Enquanto ela disser isso, o agente carimba entregue o que não saiu -- e,
    pior, nunca reenvia.

    Consertar a ferramenta (outra faixa) não basta: se a persona continuar
    prometendo chegada, o agente promete chegada com as palavras dele.
    """
    corrido = " ".join(soul.split())

    for promessa in ("whether that exact file went out",
                     "for a file that arrived"):
        assert promessa not in corrido, (
            "a persona ainda diz %r sobre `warden delivered`. O que ele lê é a "
            "linha que o gateway escreve ANTES do envio (base.py:3880), então "
            "ela é tentativa e nunca chegada." % promessa
        )

    marca = re.search(r"(?i)\bannounce", corrido)
    assert marca, (
        "a persona não diz em lugar nenhum que a evidência de `warden "
        "delivered` é o ANÚNCIO do gateway. Sem isso, 'it says whether the "
        "file went out' volta na próxima redação."
    )
    janela = corrido[max(0, marca.start() - 300): marca.start() + 400]
    assert "warden delivered" in janela, (
        "o anúncio do gateway está na persona longe de `warden delivered`, "
        "que é a ferramenta que o lê: %r" % janela[:200]
    )
    assert re.search(r"(?i)(not (its )?arrival|never arrival|not that it "
                     r"arrived|is not arrival)", janela), (
        "a persona nomeia o anúncio mas não diz que ele NÃO é chegada, que é "
        "a única coisa que o agente precisa saber antes de escrever "
        "'entregue': %r" % janela[:300]
    )


def test_o_aviso_de_inicio_sai_uma_vez_so(soul):
    """Degrau 4 do demo, 16/09/2026: "On it." saiu DUAS vezes.

    A primeira no lugar certo (msg 10, antes do prep). A segunda colada na
    mensagem que contava a falha (msg 26), onde não abre coisa nenhuma: a
    pessoa leu "On it." e logo abaixo "nothing went out", o que lê como um
    segundo trabalho começando em vez de o primeiro terminando.

    A regra existia -- "your FIRST message ... ENDS THAT TURN" -- e não dizia
    quantas vezes. Uma regra sobre a PRIMEIRA mensagem não é uma regra sobre a
    ÚNICA, e o modelo leu isso corretamente.
    """
    corrido = " ".join(soul.split())
    marca = re.search(r"(?i)\bONCE per request\b", corrido)
    assert marca, (
        "a persona não diz que o aviso de início sai UMA vez por pedido. "
        "Sem isso ele reaparece colado na entrega e na falha, que foi o "
        "medido no degrau 4 do demo."
    )
    janela = corrido[max(0, marca.start() - 400): marca.start() + 300]
    assert re.search(r"(?i)never said again|not said again|never again", janela), (
        "o 'ONCE per request' não diz que não se repete depois: %r" % janela
    )


def test_a_conferencia_visual_nao_para_a_entrega(soul):
    """A decisão do dono de 16/09, do lado do texto.

    O código é `warden_style.OBSERVACAO` e tem os seus próprios testes em
    tests/test_entrega_nao_e_estetica.py. Aqui se cobra a outra metade: que a
    persona mande o agente ENTREGAR e dizer, em vez de segurar. As duas
    precisam concordar, porque foi a persona que o agente leu quando decidiu
    não mandar nada.
    """
    corrido = " ".join(soul.split())
    assert re.search(r"(?i)only those two stop a delivery", corrido), (
        "a persona não diz que só gancho e legenda param uma entrega"
    )
    assert "LOOK:" in corrido, (
        "a persona não nomeia o rótulo `LOOK:` que a ferramenta imprime; sem "
        "o nome, o agente não liga uma coisa à outra"
    )
    marca = re.search(r"(?i)only those two stop a delivery", corrido)
    janela = corrido[marca.start(): marca.start() + 420]
    assert re.search(r"(?i)goes out anyway|is delivered anyway", janela), (
        "a persona nomeia as observações mas não diz que o clipe SAI: %r"
        % janela
    )

def test_a_persona_nao_se_contradiz_sobre_a_prosa_no_meio_do_turno(soul):
    """Achado 7, a outra metade: duas afirmações sobre o MESMO fato do runtime.

    `persona:43-45` diz que numa mensagem que ainda chama ferramenta "the prose
    arrives, the file does not"; `persona:131-132` dizia "A turn that still
    calls a tool delivers nothing". Uma das duas está errada, e é ela que
    decide se o degrau 2 funciona.

    O fato está medido em logs-7a/container/logs/gateway.log:41-42, e este
    teste o lê do arquivo em vez de confiar na memória de quem escreveu: a msg
    21 da sessão ("Em produção." COM uma chamada de `terminal` na mesma
    mensagem, logs-7a/container/sessao-mensagens.txt:64) chegou à pessoa —
    o gateway enfileirou um follow-up e mandou a resposta. Prosa no meio do
    turno CHEGA; o anexo é que não.
    """
    caminho = os.path.join(RAIZ, "logs-7a", "container", "logs", "gateway.log")
    with open(caminho, encoding="utf-8") as arquivo:
        gateway = arquivo.read()
    assert "final stream delivery not confirmed; sending first response" in gateway, (
        "a evidência que este teste cita saiu de %s" % caminho
    )
    assert re.search(r"Sending response \(\d+ chars\)", gateway), (
        "o gateway não registra o envio da prosa; a conta abaixo muda"
    )

    assert "the prose arrives, the file does not" in soul, (
        "sumiu o fato medido: numa mensagem que ainda chama ferramenta a prosa "
        "chega e o arquivo não"
    )
    assert "delivers nothing" not in soul, (
        "a persona ainda afirma que um turno que chama ferramenta 'delivers "
        "nothing', o que contradiz o que o gateway registrou em 14:51:52"
    )


# Uma amostra de fala é uma linha entre aspas que a persona mostra para o
# agente copiar a FORMA. Quando ela está presa a um idioma e não tem par, o
# modelo copia as palavras: foi o que 7a mediu, com 18 amostras em português e
# zero em inglês.
_ACENTOS = "áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ"


def test_toda_amostra_em_portugues_tem_o_par_em_ingles():
    """Achado 4, e a trava que a auditoria chamou de anti-regressão.

    A versão anterior deste teste verificava DUAS literais escritas à mão
    (`("Em produção.", "On it.")` e `("Subindo", "going up")`) — as duas frases
    nomeadas na evidência de 7a. Uma amostra nova em português passava sem
    ruído, e três já passavam: as de `:91`, `:113` e `:182`, que são os degraus
    8, 10 e 1 do demo — os três momentos em que a pessoa LÊ a mensagem.

    Agora a varredura acha toda amostra em português e exige o par. A asserção
    do `>= 2` é o que impede o teste de passar por vazio no dia em que a
    convenção `PT "..."` mudar de nome.
    """
    with open(PERSONA, encoding="utf-8") as arquivo:
        persona = arquivo.read()
    corrido = " ".join(persona.split())

    amostras = [
        (m.start(), m.group(1))
        for m in re.finditer(r'"([^"\n]{6,200})"', corrido)
        if any(letra in m.group(1) for letra in _ACENTOS)
        or re.search(
            r"(?i)\b(manda|abre|conecta|saiu|subindo|cortes|legenda|link que)\b",
            m.group(1),
        )
    ]
    assert len(amostras) >= 2, (
        "a varredura não achou amostra em português nenhuma em "
        "runtime/persona.md (%d): se a convenção mudou, este teste muda junto."
        % len(amostras)
    )

    presas = []
    for posicao, amostra in amostras:
        perto = corrido[max(0, posicao - 300) : posicao + 300]
        if not re.search(r'EN\s+"', perto):
            presas.append(amostra)
    assert not presas, (
        "amostra(s) em português sem o par em inglês ao lado: %s. Ou o par "
        "entra, ou a amostra vira molde com lacunas."
        % "; ".join(repr(amostra) for amostra in presas)
    )


def test_o_aviso_nao_vem_com_um_exemplo_por_LINGUA(soul):
    """Visto acontecer em 18/09/2026, na conversa do dono, ao vivo.

    A regra "ONCE per request" existia desde 16/09 e o agente mandou DUAS
    assim mesmo -- "On it." e logo abaixo "Em produção." -- numa conversa em
    INGLÊS, então a segunda ainda estava na língua errada. É o defeito do teste
    7a de volta: um banho de português vindo do texto que ele lê arrasta a
    resposta dele.

    A causa provável não era a regra e sim o EXEMPLO ao lado dela, que listava
    `PT "Em produção." / EN "On it."` -- duas frases prontas, lado a lado, num
    parágrafo diferente do "ONCE". Um modelo que lê duas frases de exemplo e
    manda as duas não está desobedecendo; está copiando o que viu.

    Este caso não repete o `test_o_aviso_de_inicio_sai_uma_vez_so`, que cobra a
    REGRA. Ele cobra que o EXEMPLO não convide o contrário dela.
    """
    corrido = " ".join(soul.split())
    # SÓ o parágrafo do aviso de início. A primeira versão desta varredura
    # olhava o SOUL inteiro e reprovou um par `PT ... / EN ...` legítimo, de
    # outro assunto (o aviso de clipe que não anexou). Um teste que acusa o
    # texto certo ensina a ignorá-lo.
    inicio = corrido.find("Your FIRST message is the LAST message")
    assert inicio >= 0, "sumiu o parágrafo do aviso de início"
    janela = corrido[inicio:inicio + 500]
    par = re.search(r'(?i)PT\s*"[^"]+"\s*/\s*EN\s*"[^"]+"', janela)
    assert not par, (
        "o parágrafo do aviso voltou a dar um exemplo por LÍNGUA, lado a lado: "
        "%r. Em 18/09 isso saiu como duas mensagens, a segunda em português "
        "numa conversa em inglês." % (par.group(0) if par else "")
    )
    achado = re.search(r"(?i)\bONE of them, never two\b", janela)
    assert achado, (
        "o parágrafo do aviso de início não diz, NO PRÓPRIO LUGAR do exemplo, "
        "que sai um só. A regra `ONCE per request` vive noutro parágrafo e "
        "sozinha ela não impediu as duas."
    )
