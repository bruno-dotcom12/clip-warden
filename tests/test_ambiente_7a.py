"""Faixa 'ambiente': o que o compose promete e o que os docs prometem têm de
ser o MESMO número, e o que o teste 7a mediu tem de estar escrito onde alguém
olha.

Por que este arquivo existe, medido no teste 7a de 16/09/2026:

  * `compose.yml` pedia `mem_limit: 4g` (4.096 MiB) numa VM de Docker de
    3.826 GiB = 3.918 MiB (logs-7a/snapshot-inicial/docker-info.txt). O Docker
    ACEITA esse teto e nunca o honra: `docker stats` imprimiu
    `1.024GiB / 3.826GiB` (logs-7a/snapshot-inicial/docker-stats-1.txt), isto é,
    exibiu como teto a RAM da VM. Na prática o container volta a ser ilimitado,
    que é exatamente o estado que o commit 81c032f existiu para acabar.
  * Nenhum arquivo dizia, em número, quanto de memória o dono precisa pôr no
    Docker Desktop para o teto do compose ser real. `install.sh` media contra
    5120 MiB, o `compose.yml` dizia só "é este número que se abaixa, ou a VM que
    se levanta" -- sem número nenhum.

O contrato agora é declarado em texto legível por máquina nos dois arquivos, e
este teste falha se eles discordarem.
"""

import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMPOSE = os.path.join(RAIZ, "compose.yml")
INSTALL_MD = os.path.join(RAIZ, "docs", "INSTALL.md")
INSTALL_SH = os.path.join(RAIZ, "install.sh")
README = os.path.join(RAIZ, "README.md")
AMBIENTE_MD = os.path.join(RAIZ, "docs", "AMBIENTE.md")

# A folga que o próprio projeto declara entre o teto do container e a VM:
# install.sh:81-82 e README.md:178-180 ("the remaining 1 GiB is the slack the
# rest of the VM needs").
FOLGA_MINIMA_MIB = 1024


def _le(caminho):
    with open(caminho, encoding="utf-8") as fh:
        return fh.read()


def _marcador(texto, chave):
    """Lê `warden-<chave>: <inteiro>` de qualquer lugar do arquivo.

    O marcador vive em comentário (`#` no YAML/sh, `<!-- -->` no Markdown), de
    modo que o número é o mesmo que o humano lê na linha de cima."""
    achados = re.findall(
        r"warden-" + re.escape(chave) + r"\s*:\s*(\d+)", texto)
    return [int(x) for x in achados]


def _mem_limit_mib(texto_compose):
    """`mem_limit: 4g` / `2800m` -> MiB."""
    m = re.search(r"^\s*mem_limit:\s*(\d+)\s*([gGmM])\s*$",
                  texto_compose, re.MULTILINE)
    if not m:
        return None
    valor, unidade = int(m.group(1)), m.group(2).lower()
    return valor * 1024 if unidade == "g" else valor


class ContratoDeMemoria(unittest.TestCase):
    """PEDIDO 6 do dono: compose.yml e docs/INSTALL.md têm de dizer a MESMA
    coisa sobre o mínimo de memória do Docker Desktop."""

    def test_compose_declara_o_minimo_da_vm_em_numero(self):
        vals = _marcador(_le(COMPOSE), "min-docker-vm-mib")
        self.assertEqual(
            len(vals), 1,
            "compose.yml precisa declarar, UMA vez, o mínimo de RAM da VM do "
            "Docker no marcador `warden-min-docker-vm-mib: <MiB>`. Sem ele, "
            "`mem_limit` é um teto sem máquina onde caiba -- foi o estado do "
            "teste 7a (4096 MiB de teto numa VM de 3918 MiB).")

    def test_docs_install_declara_o_mesmo_minimo_que_o_compose(self):
        do_compose = _marcador(_le(COMPOSE), "min-docker-vm-mib")
        do_docs = _marcador(_le(INSTALL_MD), "min-docker-vm-mib")
        self.assertEqual(
            len(do_docs), 1,
            "docs/INSTALL.md precisa declarar `warden-min-docker-vm-mib: "
            "<MiB>` junto do texto que pede memória ao dono.")
        self.assertEqual(
            do_compose, do_docs,
            "compose.yml e docs/INSTALL.md discordam sobre o mínimo de RAM da "
            "VM do Docker: %r contra %r MiB." % (do_compose, do_docs))

    def test_o_teto_do_container_e_o_mesmo_numero_nos_dois_arquivos(self):
        texto = _le(COMPOSE)
        real = _mem_limit_mib(texto)
        self.assertIsNotNone(
            real, "não achei `mem_limit: <n><g|m>` no compose.yml")
        declarado = _marcador(texto, "agent-mem-limit-mib")
        self.assertEqual(
            declarado, [real],
            "o marcador `warden-agent-mem-limit-mib` do compose.yml não bate "
            "com o `mem_limit:` de verdade (%r contra %s MiB)."
            % (declarado, real))
        nos_docs = _marcador(_le(INSTALL_MD), "agent-mem-limit-mib")
        self.assertEqual(
            nos_docs, [real],
            "docs/INSTALL.md anuncia um teto de container diferente do que o "
            "compose.yml aplica (%r contra %s MiB)." % (nos_docs, real))

    def test_o_teto_cabe_na_vm_minima_com_a_folga_que_o_projeto_declara(self):
        """O defeito do 7a em uma linha: teto > VM.

        4096 MiB de teto numa VM de 3918 MiB não é um limite apertado, é um
        limite que não existe (docker-stats-1.txt imprime a VM como LIMIT)."""
        teto = _mem_limit_mib(_le(COMPOSE))
        vm = _marcador(_le(COMPOSE), "min-docker-vm-mib")[0]
        self.assertLessEqual(
            teto + FOLGA_MINIMA_MIB, vm,
            "o teto do container (%s MiB) mais a folga de %s MiB que o projeto "
            "promete ao resto da VM não cabem no mínimo anunciado (%s MiB). "
            "Ou o teto desce, ou o mínimo sobe -- mas os dois números têm de "
            "fechar." % (teto, FOLGA_MINIMA_MIB, vm))

    def test_install_sh_mede_contra_o_mesmo_minimo(self):
        """O instalador é quem mede a VM de verdade; o número dele não pode ser
        um terceiro número. (install.sh é de outra faixa: aqui só se lê.)"""
        vm = _marcador(_le(COMPOSE), "min-docker-vm-mib")[0]
        sh = _le(INSTALL_SH)
        m = re.search(r'\[\s*"\$MEM_MIB"\s*-lt\s*(\d+)\s*\]', sh)
        self.assertIsNotNone(
            m, "não achei a comparação de MEM_MIB em install.sh")
        self.assertEqual(
            int(m.group(1)), vm,
            "install.sh avisa abaixo de %s MiB, mas o contrato declarado é %s "
            "MiB." % (m.group(1), vm))

    def test_readme_pede_a_mesma_memoria(self):
        vm = _marcador(_le(COMPOSE), "min-docker-vm-mib")[0]
        texto = " ".join(_le(README).split())
        self.assertIn(
            "%s MiB" % vm, texto,
            "README.md pede memória ao leitor sem citar o mesmo número em MiB "
            "(%s MiB), e 'GiB inteiro' já escondeu esta divergência uma vez."
            % vm)


class OQueO7aMediuEstaEscrito(unittest.TestCase):
    """As duas coisas que o teste 7a refutou e que ninguém documentava."""

    def test_docs_explicam_a_emulacao_amd64_como_esperada(self):
        """Causa 4 do diagnóstico: o aviso laranja é emulação amd64 via Rosetta,
        é anterior à etapa 3 e NÃO é a causa do 7a."""
        self.assertTrue(
            os.path.exists(AMBIENTE_MD),
            "falta docs/AMBIENTE.md: o aviso laranja do Docker Desktop, a "
            "emulação amd64 e os números de memória do 7a não têm casa.")
        texto = " ".join(_le(AMBIENTE_MD).split())
        # "2min40", não o "2min43" que a fase 1 escreveu: o link é a msg 20 do
        # live.db, epoch 1789570313,32, e o clipe 01 está em entregas.json com
        # `at: 2026-09-16T14:54:33.174942+00:00` -- 159,9 s entre os dois.
        for agulha in ("rosetta", "linux/amd64", "2min40"):
            self.assertIn(
                agulha, texto.lower(),
                "docs/AMBIENTE.md não cita %r; sem isso o aviso continua "
                "parecendo novidade e culpado." % agulha)

    def test_docs_registram_que_o_teto_de_4_gib_passou_a_ser_honrado(self):
        """A memória DEIXOU de ser pendência, e um doc que ainda pede memória
        ao dono é uma promessa que a instalação não cumpre -- ao contrário.

        Medido em 16/09/2026, depois de o dono levantar a VM: `docker info`
        passou a mostrar 5.785 GiB e `docker stats` passou a imprimir
        `545MiB / 4GiB`. O `4GiB` na coluna LIMIT é a prova: antes o Docker
        imprimia ali a VM inteira (`1.024GiB / 3.826GiB`), porque um teto maior
        que a VM nunca é alcançado. Enquanto docs/AMBIENTE.md disser que toda
        medição desta máquina é de uma máquina abaixo do requisito, ele está
        descrevendo uma máquina que não existe mais."""
        texto = " ".join(_le(AMBIENTE_MD).split())
        self.assertIn(
            "545MiB / 4GiB", texto,
            "docs/AMBIENTE.md não registra a medição que fechou o assunto da "
            "memória (`docker stats` imprimindo `545MiB / 4GiB`, isto é, o "
            "teto do compose aparecendo como LIMIT em vez da VM).")
        self.assertNotIn(
            "abaixo do requisito publicado", texto,
            "docs/AMBIENTE.md ainda trata a VM de 3918 MiB como o estado "
            "atual da máquina de teste. Ela foi levantada; o parágrafo virou "
            "um pedido de memória para quem já a deu.")

    def test_install_nao_promete_que_a_poda_nunca_erra(self):
        """Causa 2: docs/INSTALL.md dizia que a poda joga fora 'old, bulky
        command outputs' e que 'there is nothing to get wrong'. Às 14:52:50 do
        7a ela jogou fora os 34.983 chars do warden-clip e os 8.575 do
        warden-run (live.db msgs 37 e 39) -- as instruções de operação."""
        texto = " ".join(_le(INSTALL_MD).split())
        self.assertNotIn(
            "there is nothing to get wrong", texto,
            "docs/INSTALL.md ainda promete que a poda não tem como dar errado.")
        self.assertIn(
            "SKILL_PRUNED", texto,
            "docs/INSTALL.md não diz que a poda atinge o texto das skills "
            "carregadas por skill_view, nem qual é o sinal disso no histórico "
            "(`[SKILL_PRUNED: content lost in compression]`).")


class DocsNaoEhFonteDeRegra(unittest.TestCase):
    """LEI 1: o que não entra na imagem não é regra.

    O Dockerfile copia `runtime/persona.md` (:237), as sete `warden-*/`
    (:242-248) e `SPECS/` (:250). **`docs/` não é copiado** -- e essa é a
    diferença entre uma explicação e uma regra removida com aparência de regra
    presente, que foi o defeito que a auditoria pegou.

    Esta faixa é dona de `docs/`, então o que se cobra aqui é o lado de cá: um
    arquivo em `docs/` não pode se apresentar como o lugar onde a regra mora,
    nem anunciar que uma SKILL.md manda o agente vir lê-lo. Quem for procurar o
    ponteiro do lado de dentro da imagem procura em `tests/test_skills.py`; sem
    esta metade, um `docs/` reescrito amanhã volta a convidar o ponteiro."""

    # "aponta para cá", "manda ler isto", "a regra curta está lá" -- as cinco
    # aberturas que os docs desta rodada nasceram com. A frase é o sintoma: ela
    # só faz sentido se existir um ponteiro de dentro da imagem, que é
    # justamente o que a LEI proíbe.
    FRASES_DE_ALVO = (
        "aponta para cá",
        "aponta para ca",
        "aponta pra cá",
        "manda ler isto",
        "a regra curta está lá",
        "a regra curta mora lá",
    )

    BANNER = "Este arquivo NÃO entra na imagem"

    @staticmethod
    def _docs_md():
        pasta = os.path.join(RAIZ, "docs")
        return [os.path.join(pasta, n) for n in sorted(os.listdir(pasta))
                if n.endswith(".md")]

    def test_o_dockerfile_realmente_nao_copia_docs(self):
        """A premissa de tudo o mais neste bloco, medida e não suposta."""
        copias = [l for l in _le(os.path.join(RAIZ, "Dockerfile")).splitlines()
                  if re.match(r"\s*(COPY|ADD)\s", l)]
        self.assertGreater(
            len(copias), 5,
            "não achei as linhas COPY do Dockerfile -- o padrão parou de casar "
            "e este teste deixou de testar.")
        docs = [l for l in copias if re.search(r"(^|\s|/)docs/", l)]
        self.assertEqual(
            [], docs,
            "o Dockerfile passou a copiar docs/ (%r). Se isso foi de "
            "propósito, a LEI 1 mudou e estes testes precisam mudar junto; se "
            "não, um ponteiro de dentro da imagem voltou a parecer legal."
            % docs)

    def test_nenhum_doc_se_anuncia_como_alvo_de_um_ponteiro_da_imagem(self):
        achados = []
        arquivos = self._docs_md()
        self.assertGreater(len(arquivos), 3, "docs/ ficou sem .md nenhum")
        for caminho in arquivos:
            for num, linha in enumerate(_le(caminho).splitlines(), 1):
                baixa = linha.lower()
                for frase in self.FRASES_DE_ALVO:
                    if frase.lower() in baixa:
                        achados.append(
                            "%s:%d  %r" % (os.path.relpath(caminho, RAIZ),
                                           num, linha.strip()))
        self.assertEqual(
            [], achados,
            "arquivo(s) em docs/ se anunciam como o lugar para onde uma "
            "SKILL.md manda o agente ir. docs/ não entra na imagem "
            "(Dockerfile:237-250), então esse ponteiro é uma regra removida "
            "com cara de regra presente. A regra vai para "
            "`warden-<skill>/references/`; aqui fica a explicação humana:"
            "\n  " + "\n  ".join(achados))

    def test_todo_doc_declara_que_o_agente_nao_o_le(self):
        """Um leitor humano tem de saber, na primeira tela, que o que ele está
        lendo NÃO chega ao agente. Sem isso, a próxima pessoa que escrever uma
        regra aqui não terá como saber que a escreveu fora da imagem."""
        faltando = []
        for caminho in self._docs_md():
            # A frase é escrita em bloco de citação e em negrito, e quebra de
            # linha no meio: `> **Este arquivo NÃO entra\n> na imagem**`. O que
            # se procura é a frase, não o embrulho -- então os marcadores de
            # Markdown saem antes da comparação.
            cabeca = "".join(
                c for c in " ".join(_le(caminho).splitlines()[:12])
                if c not in ">*`")
            cabeca = " ".join(cabeca.split())
            if self.BANNER not in cabeca:
                faltando.append(os.path.relpath(caminho, RAIZ))
        self.assertEqual(
            [], faltando,
            "arquivo(s) em docs/ sem a declaração %r nas 12 primeiras linhas: "
            "%s" % (self.BANNER, ", ".join(faltando)))


class PonteirosEntreOsProprosDocs(unittest.TestCase):
    """Auditoria, item 3: um caminho `docs/X.md` citado é uma promessa.

    A checagem sobre as SKILL.md e a persona já existe em outra faixa
    (`tests/test_skills.py::CaminhosDocsCitados` e
    `tests/test_persona.py`), e não é repetida aqui. O que falta cobrir, e é
    desta faixa, é o docs/ apontando para ele mesmo: os cinco arquivos que
    nasceram nesta rodada citam uns aos outros e citam os .md da raiz, e um
    ponteiro podre entre eles envelhece igual."""

    PADRAO = re.compile(r"docs/([A-Z0-9][A-Z0-9-]*\.md)")

    def _fontes(self):
        for pasta in (os.path.join(RAIZ, "docs"), RAIZ):
            for nome in sorted(os.listdir(pasta)):
                if nome.endswith(".md"):
                    yield os.path.join(pasta, nome)

    def test_todo_docs_citado_por_um_doc_existe(self):
        faltando = []
        vistos = 0
        for caminho in self._fontes():
            for linha_num, linha in enumerate(
                    _le(caminho).splitlines(), 1):
                for alvo in self.PADRAO.findall(linha):
                    vistos += 1
                    if not os.path.exists(os.path.join(RAIZ, "docs", alvo)):
                        faltando.append(
                            "%s:%d aponta para docs/%s, que não existe"
                            % (os.path.relpath(caminho, RAIZ), linha_num, alvo))
        self.assertGreater(
            vistos, 0,
            "nenhum ponteiro `docs/X.md` encontrado entre os docs -- o padrão "
            "parou de casar e o teste virou um teste que não testa nada.")
        self.assertEqual(
            [], faltando,
            "ponteiro(s) para docs/ inexistente(s):\n  " +
            "\n  ".join(faltando))


class AvisoDeLinhaCompartilhada(unittest.TestCase):
    """O aviso de boot sobre a linha da Plow ser compartilhada.

    O QUE ACONTECEU, medido no 7a (logs-7a/ACHADOS-FASE-1.md, "Achados da
    SEGUNDA coleta" §8): às 15:08:1x UTC três containers subiram juntos --
    `warden-7a`, `clip-warden-agent-1` e `clip-warden-nuvem` -- todos montando
    o MESMO `./plow-credentials`, logo todos no MESMO chat
    `cht_knqXxEnmlk01lLC_--xjPw`. A mensagem "Publish the last clip in YouTube"
    às 15:16:45 foi atendida pelo `clip-warden-agent-1`, que tinha
    `WARDEN_POST_API_KEY` e `WARDEN_POST_PROFILE=Clip-Warden` -- e publicou no
    canal real às 15:17:23. Ninguém tinha pedido àquele agente. A colisão só foi
    descoberta às 15:20, por `docker ps` de perícia.

    O QUE ESTE AVISO NÃO É: uma detecção. Saber, no boot, que outra instalação
    está de pé agora exige lock ou handshake, e não há nenhum dos dois -- a
    própria resposta da Plow não diz quem mais está na linha. O aviso diz o que
    É verificável aqui dentro: que esta credencial veio de um arquivo do host,
    que qualquer outro container que monte o mesmo arquivo cai no mesmo chat, e
    que ESTA instalação tem (ou não) com que publicar.

    DECISÃO DO DONO: aviso, não bloqueio. Nada aqui recusa boot, toma lock,
    abre rede ou escreve arquivo."""

    DIR_SERVICO = os.path.join(
        RAIZ, "image", "s6-overlay", "s6-rc.d", "warden-linha")
    SCRIPT = os.path.join(RAIZ, "image", "s6-overlay", "scripts", "warden-linha")
    CONTENTS = os.path.join(
        RAIZ, "image", "s6-overlay", "s6-rc.d", "user", "contents.d",
        "warden-linha")

    CHAT = "cht_knqXxEnmlk01lLC_--xjPw"

    def _roda(self, ambiente, cred_host=True):
        """Roda o script como o s6 o roda: python, ambiente, nada mais."""
        import subprocess
        import sys
        import tempfile

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        env.update(ambiente)
        with tempfile.TemporaryDirectory() as tmp:
            falso = os.path.join(tmp, "credentials.host")
            if cred_host:
                with open(falso, "w") as fh:
                    fh.write("{}\n")
            env.setdefault("PLOW_CREDENTIAL_HOST", falso)
            saida = subprocess.run(
                [sys.executable, self.SCRIPT], env=env, cwd=tmp,
                capture_output=True, text=True, timeout=30)
        return saida.returncode, saida.stdout, saida.stderr

    # -- o encaixe no boot ------------------------------------------------

    def test_o_oneshot_existe_e_esta_ligado_no_boot(self):
        self.assertTrue(
            os.path.isfile(self.SCRIPT),
            "falta image/s6-overlay/scripts/warden-linha")
        self.assertEqual(
            "oneshot",
            _le(os.path.join(self.DIR_SERVICO, "type")).strip(),
            "warden-linha tem de ser oneshot, como o warden-context: ele fala "
            "uma vez no boot e sai.")
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.DIR_SERVICO, "dependencies.d", "plow-init")),
            "sem `dependencies.d/plow-init` o aviso roda ANTES de o plow-init "
            "exportar PLOW_HOME_CHANNEL, e sai sem saber o nome do chat.")
        self.assertTrue(
            os.path.isfile(self.CONTENTS),
            "o serviço não está em s6-rc.d/user/contents.d/, então o s6 nunca "
            "o roda -- um oneshot fora do bundle é um arquivo, não um boot.")

    def test_o_up_le_o_ambiente_do_plow_init_e_nao_roda_como_root(self):
        up = _le(os.path.join(self.DIR_SERVICO, "up"))
        self.assertIn(
            "with-contenv", up,
            "sem `with-contenv` o oneshot não enxerga "
            "/run/s6/container_environment, que é onde o plow-init pôs "
            "PLOW_HOME_CHANNEL (plow-init.py:986-998).")
        self.assertIn(
            "s6-setuidgid hermes", up,
            "este oneshot não tem por que rodar como root: um processo root "
            "dentro do $HERMES_HOME é como a contabilidade de uso desta "
            "instalação morreu em 15/09.")

    def test_o_dockerfile_copia_a_pasta_inteira_do_s6(self):
        """O equivalente da LEI 1 para o lado da imagem: o que o Dockerfile não
        copia não roda, e um serviço novo não vale nada se ficar de fora."""
        self.assertIn(
            "COPY image/s6-overlay/ /etc/s6-overlay/",
            _le(os.path.join(RAIZ, "Dockerfile")),
            "o Dockerfile deixou de copiar image/s6-overlay/ inteiro; um "
            "COPY arquivo-a-arquivo esquece o próximo serviço em silêncio.")

    # -- o que ele diz ----------------------------------------------------

    def test_avisa_nomeando_o_chat_quando_a_credencial_veio_do_host(self):
        rc, _, err = self._roda({"PLOW_HOME_CHANNEL": self.CHAT})
        self.assertEqual(0, rc)
        self.assertIn("warden-linha:", err)
        self.assertIn(
            self.CHAT, err,
            "o aviso não nomeia o chat. Sem o id, quem lê o log de três "
            "containers não tem como saber que é o MESMO chat -- que é "
            "exatamente o que ninguém viu entre 15:08 e 15:20 no 7a.")

    def test_o_aviso_forte_sai_quando_ha_chave_e_perfil_fixado(self):
        """O par que fez a publicação de 15:17:23 acontecer."""
        rc, _, err = self._roda({
            "PLOW_HOME_CHANNEL": self.CHAT,
            "WARDEN_POST_API_KEY": "sk-nao-e-uma-chave",
            "WARDEN_POST_PROFILE": "Clip-Warden",
        })
        self.assertEqual(0, rc)
        self.assertIn(
            "Clip-Warden", err,
            "com chave e perfil fixado, o aviso tem de NOMEAR o perfil: o "
            "perfil é o canal, e publicar no perfil de outro é o dano que não "
            "volta atrás.")
        self.assertIn(
            "WARDEN_POST_PROFILE", err,
            "o aviso tem de dizer de qual variável veio o perfil, senão quem "
            "lê não sabe onde desligar.")

    def test_a_chave_nunca_aparece_no_aviso(self):
        """Degrau 11 do critério de aceite: a chave não volta para lugar
        nenhum, e um log é um lugar."""
        segredo = "sk-um-segredo-que-nao-pode-vazar-1234567890"
        rc, out, err = self._roda({
            "PLOW_HOME_CHANNEL": self.CHAT,
            "WARDEN_POST_API_KEY": segredo,
            "WARDEN_POST_PROFILE": "Clip-Warden",
        })
        # O rc e o `warden-linha:` são o que impede este teste de passar por
        # vazio: um script ausente também não imprime segredo nenhum.
        self.assertEqual(0, rc)
        self.assertIn("warden-linha:", err)
        self.assertNotIn(segredo, out + err)
        self.assertNotIn(segredo[:12], out + err)

    def test_nao_afirma_que_outra_instalacao_esta_de_pe(self):
        """A frente de código mediu que detecção ao vivo é impossível sem lock
        ou handshake. Um aviso que afirmasse "outra instalação está rodando"
        seria mentira em toda instalação sozinha -- e um aviso que mente é um
        aviso que se aprende a ignorar."""
        _, _, err = self._roda({"PLOW_HOME_CHANNEL": self.CHAT})
        self.assertIn(
            "não dá para saber daqui", err,
            "o aviso precisa dizer, com todas as letras, que não sabe se "
            "outra instalação está de pé agora.")

    def test_cala_quando_nao_ha_credencial_de_host(self):
        """Na nuvem da Plow a credencial vem do ambiente e não há arquivo do
        host para dois containers montarem. Avisar ali é ruído."""
        rc, _, err = self._roda(
            {"PLOW_HOME_CHANNEL": self.CHAT}, cred_host=False)
        self.assertEqual(0, rc)
        self.assertEqual(
            "", err.strip(),
            "sem arquivo de credencial do host não há linha compartilhável "
            "por construção, e o aviso vira ruído de boot.")

    def test_o_aviso_esta_escrito_onde_a_pessoa_que_instala_le(self):
        """Uma linha de log que ninguém sabe procurar é uma linha de log que
        ninguém procura. O nome do serviço tem de estar nos dois textos que a
        pessoa lê antes de subir o container, senão o aviso depende de alguém
        adivinhar o grep."""
        faltando = [nome for nome, caminho in
                    (("README.md", README), ("docs/INSTALL.md", INSTALL_MD))
                    if "warden-linha" not in _le(caminho)]
        self.assertEqual(
            [], faltando,
            "%s não citam `warden-linha`: quem instala não fica sabendo que "
            "uma credencial só é uma linha só, nem onde o boot diz isso."
            % ", ".join(faltando))

    # -- e ele não derruba nada -------------------------------------------

    def test_nunca_falha_o_boot(self):
        """`A oneshot that fails is a boot that fails` -- a regra que o
        warden-context já escreveu, aplicada aqui."""
        casos = [
            ({}, True),
            ({}, False),
            ({"PLOW_HOME_CHANNEL": ""}, True),
            ({"PLOW_HOME_CHANNEL": self.CHAT,
              "PLOW_CREDENTIAL_HOST": "/proc/1/nao/existe/mesmo"}, True),
            ({"WARDEN_POST_API_KEY": "x", "WARDEN_POST_PROFILE": "y"}, True),
        ]
        for ambiente, cred in casos:
            rc, _, err = self._roda(ambiente, cred_host=cred)
            self.assertEqual(
                0, rc, "saiu %d com %r; stderr=%r" % (rc, ambiente, err))

    def test_nao_abre_rede_nem_escreve_arquivo(self):
        """'Nada frágil' é uma decisão do dono, e aqui ela é verificável: o
        script só lê o ambiente. Sem rede não há timeout de boot; sem escrita
        não há arquivo de marca para apodrecer; sem lock não há instalação
        travada por outra que morreu."""
        import ast

        arvore = ast.parse(_le(self.SCRIPT))
        proibidos = {"socket", "urllib", "http", "requests", "subprocess",
                     "fcntl", "shutil", "sqlite3"}
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                for alias in no.names:
                    self.assertNotIn(
                        alias.name.split(".")[0], proibidos,
                        "warden-linha importa %r" % alias.name)
            elif isinstance(no, ast.ImportFrom) and no.module:
                self.assertNotIn(
                    no.module.split(".")[0], proibidos,
                    "warden-linha importa de %r" % no.module)
            elif isinstance(no, ast.Call) and getattr(
                    no.func, "id", None) == "open":
                modos = [a.value for a in no.args[1:]
                         if isinstance(a, ast.Constant)]
                modos += [k.value.value for k in no.keywords
                          if k.arg == "mode" and isinstance(k.value,
                                                            ast.Constant)]
                for modo in modos:
                    self.assertNotIn(
                        "w", str(modo),
                        "warden-linha abre arquivo para escrita")
                    self.assertNotIn(
                        "a", str(modo),
                        "warden-linha abre arquivo para escrita")


if __name__ == "__main__":
    unittest.main()
