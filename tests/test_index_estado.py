"""O reparo de dono do estado do Agent Index, agora que o reporter é o da base.

POR QUE ESTE ARQUIVO EXISTE

A imagem passou a usar o reporter da imagem base (`/etc/s6-overlay/s6-rc.d/
agent-index`, que a base traz junto com o cliente que ela pina). É a decisão
certa: um reporter só, mantido por quem o escreveu. Mas o serviço que este
repositório tinha carregava, dentro do mesmo laço, um reparo que o da base NÃO
tem -- e o reparo não é enfeite.

MEDIDO 15/09/2026: `/var/lib/hermes/.agent-index.json` ficou com dono root
desde as 14:15, porque um `docker compose exec` que entrou como root rodou o
cliente e o cliente criou o estado como root. O cliente roda como hermes
(uid 10000): sai 2, "Permission denied". E 2 é justamente o código que o
reporter trata como "o estado está aí e eu não consigo ler -- fico parado em
vez de registrar por cima", que é o comportamento correto e que significa NUNCA
MAIS REPORTAR. O estrago é silencioso: o serviço continua no laço, esta
instalação simplesmente para de ser contada, e nada no chat diz isso.

Então o reparo ficou, como serviço próprio (`warden-index-estado`), e o
reporter ficou sendo o da base. Este teste é o que impede que ele suma de novo
numa próxima limpeza, e que impede a outra metade do estrago: um serviço nosso
chamado `agent-index` voltaria a SOBRESCREVER o da base, porque o
`COPY image/s6-overlay/ /etc/s6-overlay/` do Dockerfile roda depois dela.
"""

import os
import subprocess
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

S6 = os.path.join(RAIZ, "image", "s6-overlay", "s6-rc.d")
DIR_SERVICO = os.path.join(S6, "warden-index-estado")
RUN = os.path.join(DIR_SERVICO, "run")
CONTENTS = os.path.join(S6, "user", "contents.d", "warden-index-estado")


def _le(caminho):
    with open(caminho, encoding="utf-8") as arquivo:
        return arquivo.read()


class OREPAROESTAMONTADONOBOOT(unittest.TestCase):
    """O encaixe no s6: um `run` que o s6 nunca roda é um arquivo, não um boot."""

    def test_o_longrun_existe_executavel_e_esta_no_bundle(self):
        self.assertTrue(
            os.path.isfile(RUN),
            "falta image/s6-overlay/s6-rc.d/warden-index-estado/run -- sem ele "
            "o reparo que o reporter da base não tem não existe em lugar nenhum.")
        self.assertTrue(
            os.access(RUN, os.X_OK),
            "o `run` não está executável; o s6 não o roda e o serviço nunca sobe.")
        self.assertEqual(
            "longrun",
            _le(os.path.join(DIR_SERVICO, "type")).strip(),
            "tem de ser longrun: root pode quebrar o dono do estado em QUALQUER "
            "momento da vida da instalação -- foi assim que quebrou -- e um "
            "oneshot só repararia no boot, exigindo restart de quem já está numa "
            "sessão aberta.")
        self.assertTrue(
            os.path.isfile(
                os.path.join(DIR_SERVICO, "dependencies.d", "plow-init")),
            "sem `dependencies.d/plow-init` o serviço sobe antes de o plow-init "
            "montar o ambiente do container, como os irmãos.")
        self.assertTrue(
            os.path.isfile(CONTENTS),
            "o serviço não está em s6-rc.d/user/contents.d/, então o s6 nunca o "
            "roda.")

    def test_o_repo_nao_sobrescreve_o_reporter_da_base(self):
        """`COPY image/s6-overlay/` roda DEPOIS da base, então um diretório
        nosso com esse nome substitui o `run` dela e desfaz a PR inteira."""
        self.assertFalse(
            os.path.exists(os.path.join(S6, "agent-index")),
            "image/s6-overlay/s6-rc.d/agent-index/ voltou. O reporter é o da "
            "imagem base; um diretório nosso com o mesmo nome sobrescreve o "
            "dela no COPY do Dockerfile, e volta-se a manter aqui um reporter "
            "que outra pessoa mantém melhor.")
        self.assertFalse(
            os.path.exists(os.path.join(S6, "user", "contents.d", "agent-index")),
            "a entrada `user/contents.d/agent-index` voltou. A da base já liga "
            "o serviço dela; esta aqui só existiria para ligar um serviço nosso "
            "que não deve existir.")

    def test_a_cadencia_e_a_do_reporter(self):
        """Reparar mais rápido do que se reporta não compra nada, e reparar
        mais devagar deixa passe reportado contra estado quebrado."""
        self.assertIn(
            "/bin/sleep 300", _le(RUN),
            "o laço tem de dormir 300s, a mesma cadência do reporter da base "
            "(o `run` dela dorme 300 em cada saída do laço).")


class OREPARONAOSEGUELINKS(unittest.TestCase):
    """A metade perigosa: um chown de root que SEGUE symlink dentro do
    $HERMES_HOME -- que é o diretório que o próprio agente controla -- é
    escalação de privilégio escrita por nós, de cinco em cinco minutos."""

    def _roda_uma_passada(self, casa):
        """Roda o `devolve_o_estado` do serviço REAL, uma vez, contra `casa`.

        O harness troca duas linhas do script: o literal do HERMES_HOME (o
        serviço fixa /var/lib/hermes de propósito, e um teste não escreve lá) e
        o laço infinito, que vira uma chamada só. O corpo da função -- que é o
        que este teste julga -- vai inteiro, como está no arquivo.
        """
        texto = _le(RUN)
        texto = texto.replace(
            "HERMES_HOME=/var/lib/hermes", 'HERMES_HOME="%s"' % casa, 1)
        corpo, laco, _ = texto.partition("while :; do")
        self.assertTrue(laco, "o `run` não tem mais o laço que este teste corta")
        with tempfile.NamedTemporaryFile(
                "w", suffix=".sh", delete=False, encoding="utf-8") as fh:
            fh.write(corpo + "\ndevolve_o_estado\n")
            script = fh.name
        try:
            return subprocess.run(
                ["/bin/sh", script], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(script)

    def test_um_symlink_no_lugar_do_estado_e_recusado_e_o_alvo_nao_e_tocado(self):
        with tempfile.TemporaryDirectory() as tmp:
            casa = os.path.join(tmp, "hermes")
            os.mkdir(casa)
            alvo = os.path.join(tmp, "alvo-que-ninguem-pode-ganhar")
            with open(alvo, "w") as fh:
                fh.write("/etc/shadow faz este papel aqui\n")
            antes = os.stat(alvo)
            os.symlink(alvo, os.path.join(casa, ".agent-index.json"))

            saida = self._roda_uma_passada(casa)

            depois = os.stat(alvo)
            self.assertEqual(
                (antes.st_uid, antes.st_gid), (depois.st_uid, depois.st_gid),
                "o dono do ALVO do symlink mudou: o reparo seguiu o link. É "
                "exatamente a escalação que o `-h` e o teste de symlink existem "
                "para impedir.")
            self.assertIn(
                "symlink", saida.stderr,
                "o reparo recusou calado; quem lê o log precisa saber que "
                "havia um link onde deveria haver o estado.")

    @unittest.skipUnless(
        os.geteuid() == 0, "sem root não há como conferir um chown de verdade")
    def test_o_estado_de_root_volta_para_o_agente(self):
        with tempfile.TemporaryDirectory() as tmp:
            casa = os.path.join(tmp, "hermes")
            os.mkdir(casa)
            estado = os.path.join(casa, ".agent-index.json")
            with open(estado, "w") as fh:
                fh.write("{}\n")
            os.chown(estado, 0, 0)

            self._roda_uma_passada(casa)

            self.assertEqual(
                (10000, 10000),
                (os.stat(estado).st_uid, os.stat(estado).st_gid),
                "o estado continuou de root: o cliente roda como hermes "
                "(uid 10000), sai 2, e esta instalação para de ser contada.")


if __name__ == "__main__":
    unittest.main()
