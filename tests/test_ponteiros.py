"""O que não entra na imagem não é regra.

Em 16/09/2026 a persona e as skills foram encolhidas, e a prosa foi mandada para
`docs/`. O corte foi correto; o endereço não. **O Dockerfile não copia `docs/`**
-- ele copia `runtime/persona.md`, as sete pastas `warden-*/` e `SPECS/`. Então
cinco ponteiros ficaram apontando para arquivos que o agente NUNCA veria:

    docs/EDIT-NA-BATINA.md   citado em warden-clip/SKILL.md
    docs/CONTACT-SHEET.md    citado em warden-check, warden-clip e warden-style
    docs/FONTE-BLOQUEADA.md  citado em warden-clip
    docs/LEGENDA-SUSPEITA.md citado em warden-clip
    docs/POSTAGEM.md         citado em warden-shared

Um deles carregava REGRA e não explicação: `warden-clip/SKILL.md` dizia
"**every rule it has** is in `docs/EDIT-NA-BATIDA.md`". O procedimento inteiro
do edit na batida tinha sumido da imagem, com aparência de estar presente --
que é pior que ter sumido declaradamente, porque o agente gasta um turno lendo
e sai de lá convencido de que leu.

Este arquivo é a rede para essa classe inteira. Ele afirma DUAS coisas de cada
caminho que um texto de dentro da imagem cita:

    1. o arquivo EXISTE no repositório;
    2. o caminho ENTRA NA IMAGEM.

E a lista do que entra na imagem é LIDA DO Dockerfile, nunca escrita à mão: se
alguém mudar um `COPY` amanhã, o teste acompanha sozinho em vez de mentir.

Onde a explicação humana deve morar: em `docs/`, como sempre -- mas referida
pelo NOME ("the install guide"), sem caminho de arquivo. Um caminho escrito num
texto que o agente lê é uma instrução para ele ir lá, e ele não pode.
"""

import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(RAIZ, "Dockerfile")
PERSONA = os.path.join(RAIZ, "runtime", "persona.md")

# Um caminho relativo com pasta e extensão de texto. Exige a barra: sem ela,
# `SKILL.md` sozinho é uma menção genérica ao formato, não um endereço.
CAMINHO = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:md|json|py|txt)")

# Três coisas casam com a expressão acima e NÃO são endereços deste
# repositório. Elas foram medidas nos textos de hoje, não imaginadas:
#
#   /var/lib/hermes/warden/cookies.txt   caminho de dentro do CONTAINER
#   docs.upload-post.com/openapi.json    URL
#   PRIME/edit.py                        outro projeto, que não vive aqui
#
# Um teste que reprova essas três vira ruído, e um teste que vira ruído alguém
# desliga -- e aí ele não pega mais o `docs/EDIT-NA-BATIDA.md` que ele existe
# para pegar. Então a regra é positiva: só é endereço deste repositório o que
# COMEÇA numa pasta que existe na raiz dele.
def raizes_do_repositorio():
    return {nome for nome in os.listdir(RAIZ)
            if os.path.isdir(os.path.join(RAIZ, nome))
            and not nome.startswith(".")}


def copiado_para_a_imagem():
    """Os prefixos de caminho que o Dockerfile leva para dentro da imagem.

    Lido do Dockerfile de propósito. A pergunta que este teste faz não é "está
    em docs/?" -- é "o agente consegue abrir isto?", e quem responde é o COPY.
    """
    prefixos = []
    with open(DOCKERFILE, encoding="utf-8") as arquivo:
        for linha in arquivo:
            if not linha.startswith("COPY"):
                continue
            # COPY [--flag=x ...] <origem>... <destino>
            campos = [c for c in linha.split()[1:] if not c.startswith("--")]
            if len(campos) < 2:
                continue
            destino = campos[-1]
            # Só interessa o que o agente lê: a persona semeada e as skills.
            if not (destino.startswith("/opt/hermes/skills")
                    or destino.startswith("/opt/hermes/plow-seed")):
                continue
            for origem in campos[:-1]:
                prefixos.append(origem.rstrip("/"))
    assert prefixos, (
        "nenhum COPY para /opt/hermes/ foi encontrado no Dockerfile -- ou o "
        "Dockerfile mudou de forma, ou este teste parou de saber ler. Um teste "
        "que não acha nada PASSA por vazio, e é por isso que esta asserção "
        "existe."
    )
    return prefixos


def entra_na_imagem(caminho, prefixos):
    normal = caminho.lstrip("./")
    return any(normal == p or normal.startswith(p + "/") for p in prefixos)


def textos_que_o_agente_le():
    """Persona, as sete SKILL.md, e tudo em warden-*/references/."""
    arquivos = [PERSONA]
    for nome in sorted(os.listdir(RAIZ)):
        pasta = os.path.join(RAIZ, nome)
        if not (nome.startswith("warden-") and os.path.isdir(pasta)):
            continue
        skill = os.path.join(pasta, "SKILL.md")
        if os.path.exists(skill):
            arquivos.append(skill)
        referencias = os.path.join(pasta, "references")
        if os.path.isdir(referencias):
            for ref in sorted(os.listdir(referencias)):
                if ref.endswith(".md"):
                    arquivos.append(os.path.join(referencias, ref))
    return arquivos


def citacoes():
    """(arquivo_que_cita, caminho_citado) para cada endereço DESTE repositório."""
    raizes = raizes_do_repositorio()
    achadas = []
    for arquivo in textos_que_o_agente_le():
        with open(arquivo, encoding="utf-8") as aberto:
            texto = aberto.read()
        for achado in CAMINHO.finditer(texto):
            bruto = achado.group(0)
            # `/var/lib/...` e `docs.upload-post.com/...` casam a partir do
            # segundo caractere; o que vem ANTES é o que os denuncia.
            anterior = texto[achado.start() - 1] if achado.start() else " "
            if anterior in "/.:-" or anterior.isalnum():
                continue
            if bruto.split("/", 1)[0] not in raizes:
                continue
            achadas.append((os.path.relpath(arquivo, RAIZ), bruto))
    return achadas


def test_o_dockerfile_ainda_e_legivel_por_este_teste():
    """Antes de qualquer outra coisa: o teste sabe do que está falando."""
    prefixos = copiado_para_a_imagem()
    assert "runtime/persona.md" in prefixos, prefixos
    for skill in ("warden-run", "warden-clip", "warden-shared", "warden-style",
                  "warden-check", "warden-package", "warden-campaign"):
        assert skill in prefixos, (
            "%s não é copiado para a imagem segundo o Dockerfile: %s"
            % (skill, prefixos)
        )
    assert "docs" not in prefixos, (
        "docs/ passou a ser copiado para a imagem. Se isso foi de propósito, "
        "este teste inteiro perde a razão de existir e a regra do projeto "
        "mudou -- decida isso explicitamente, não por acidente de COPY."
    )


def test_ha_o_que_conferir():
    """Um teste que varre o vazio passa sempre e não prova nada.

    A auditoria de 16/09 pegou exatamente isso: um teste de ponteiros `docs/`
    sobre um arquivo com zero ponteiros `docs/`, rotulado como se tivesse
    falhado antes. Aqui a matéria é conferida primeiro.
    """
    arquivos = textos_que_o_agente_le()
    assert len(arquivos) >= 8, arquivos
    assert citacoes(), (
        "nenhum caminho de arquivo foi encontrado na persona nem nas skills. "
        "Ou elas pararam de apontar para qualquer lugar, ou a expressão deste "
        "teste parou de casar."
    )


@pytest.mark.parametrize("quem,caminho", citacoes())
def test_todo_caminho_citado_existe(quem, caminho):
    assert os.path.exists(os.path.join(RAIZ, caminho)), (
        "%s manda o agente abrir `%s`, e esse arquivo NÃO EXISTE.\n"
        "Um ponteiro que mente é pior que regra nenhuma: o agente gasta um "
        "turno lendo e sai de lá sem a regra, convencido de que a leu."
        % (quem, caminho)
    )


@pytest.mark.parametrize("quem,caminho", citacoes())
def test_todo_caminho_citado_entra_na_imagem(quem, caminho):
    prefixos = copiado_para_a_imagem()
    assert entra_na_imagem(caminho, prefixos), (
        "%s cita `%s`, que EXISTE no repositório mas NÃO ENTRA NA IMAGEM.\n"
        "O Dockerfile copia %s -- e nada mais. Dentro do container esse "
        "arquivo não está lá, então para o agente essa regra não existe.\n"
        "Se é REGRA, mova para `warden-<skill>/references/` e aponte para lá.\n"
        "Se é explicação HUMANA, tire o caminho do texto e refira-a pelo nome "
        "(\"the install guide\"), porque um caminho escrito é uma ordem de ir lá."
        % (quem, caminho, ", ".join(prefixos))
    )
