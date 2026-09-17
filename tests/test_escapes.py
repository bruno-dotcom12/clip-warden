# -*- coding: utf-8 -*-
"""Nenhum arquivo .py deste repositório carrega sequência de escape inválida.

POR QUE ESTE ARQUIVO EXISTE, e ele é o conserto de um defeito medido em
17/09/2026 dentro da imagem publicada: `warden_post.py` tinha uma docstring NÃO
raw citando o `_JWT_RE` do `redact.py`, com um `\\.` dentro. O Python 3.12+
chama isso de sequência de escape inválida e emite um `SyntaxWarning` na
primeira importação do módulo. Não quebrava nada -- e por isso ficou de pé --
mas saía CRU no meio do `warden status`:

    /opt/hermes/skills/warden-shared/scripts/warden_post.py:774: SyntaxWarning:
    invalid escape sequence '\\.'

Um aviso de Python no meio do diagnóstico faz quem lê desconfiar do resto da
saída, que é justamente a parte do produto cujo trabalho é ser confiável.

POR QUE A SUÍTE NÃO PEGAVA: a imagem roda Python 3.13, e o Mac de quem
desenvolve roda 3.9 (ou o 3.11 do anaconda). Antes do 3.12 isso é
`DeprecationWarning`, que é SILENCIOSA por padrão -- o defeito era invisível
exatamente na máquina onde a suíte roda. É o mesmo formato do libass: a suíte
daqui não prova o que só a imagem executa.

COMO ESTE TESTE ESCAPA DISSO: ele não confia na categoria do aviso, que muda
com a versão, e sim no TEXTO -- "invalid escape sequence" -- que é o mesmo nas
duas. Com `simplefilter("always")` a `DeprecationWarning` do 3.9/3.11 também
chega, então este teste tem dentes na máquina de quem desenvolve, que é a única
maneira de ele servir para alguma coisa.
"""
import os
import unittest
import warnings

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `vendor` é código de terceiros: um aviso lá não é nosso para consertar, e
# falharia a suíte por uma linha que este repositório não escreveu.
IGNORADAS = {".git", "vendor", ".pytest_cache", "__pycache__", "node_modules"}


def arquivos_python():
    for pasta, subpastas, arquivos in os.walk(RAIZ):
        subpastas[:] = [s for s in subpastas if s not in IGNORADAS]
        for nome in arquivos:
            if nome.endswith(".py"):
                yield os.path.join(pasta, nome)


class SemEscapeInvalido(unittest.TestCase):
    def test_nenhum_arquivo_emite_aviso_de_escape(self):
        achados = []
        for caminho in sorted(arquivos_python()):
            with open(caminho, "rb") as handle:
                fonte = handle.read().decode("utf-8", errors="replace")
            with warnings.catch_warnings(record=True) as capturados:
                warnings.simplefilter("always")
                try:
                    compile(fonte, caminho, "exec")
                except SyntaxError as erro:
                    achados.append("%s: não compila (%s)"
                                   % (os.path.relpath(caminho, RAIZ), erro))
                    continue
                for aviso in capturados:
                    if "invalid escape sequence" in str(aviso.message):
                        achados.append("%s: %s" % (os.path.relpath(caminho, RAIZ),
                                                   aviso.message))

        self.assertEqual(achados, [],
                         "sequência de escape inválida — o Python 3.13 da imagem "
                         "emite SyntaxWarning e ele sai no meio do `warden status`. "
                         "Se o `\\` é para ser literal (uma citação de regex numa "
                         "docstring, por exemplo), prefixe a string com `r`:\n  "
                         + "\n  ".join(achados))


if __name__ == "__main__":
    unittest.main()
