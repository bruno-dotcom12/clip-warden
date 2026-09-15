"""O que o agente PAGA por clipe, e as quatro contas que ele estava pagando à toa.

Cada classe aqui guarda uma medição de 15/09/2026, e nenhuma delas é sobre o
clipe ficar melhor -- é sobre ele chegar. Um agente que leva 12 minutos para o
primeiro corte lê como agente morto num chat, e o dono desiste antes do
resultado existir.

  transcrição   79s para 2min30s de áudio, com núcleos parados e busca em feixe
                de cinco hipóteses que ninguém usava
  legenda       220s de transcrição num vídeo EM INGLÊS que publicava legenda,
                porque a única língua que o agente sabia pedir era português
  Drive         671 MB em 87s para usar um arquivo
  nome          5 minutos e 9 chamadas de ferramenta por um acento em NFD

E duas que são sobre honestidade, não sobre tempo: o fiscal que pedia licença
para o dono do link, e a mensagem de bot-check que não dizia se havia cookies.

NADA AQUI TOCA A REDE. Todo caminho que chamaria yt-dlp, gdown ou o
faster-whisper é substituído por um dublê, e é isso que torna estes testes
capazes de rodar na máquina de quem só quer ver a suíte verde.
"""
import os
import shutil
import sys
import tempfile
import unicodedata
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden_media as M


def _temp(caso, prefix="warden-velocidade-"):
    """Um diretório temporário que se APAGA quando o teste acaba.

    A mesma função de `test_warden.py`, e pela mesma razão que está escrita lá:
    dezenove classes daquela suíte não limpavam nada e deixaram 60 GB em
    $TMPDIR. A regra do projeto vale para a própria suíte -- nada consome em
    silêncio.
    """
    caminho = tempfile.mkdtemp(prefix=prefix)
    caso.addCleanup(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


def _troca(caso, nome, valor):
    """Troca um atributo do módulo e desfaz no fim. Sem isto um teste responde
    pelo outro, porque o módulo é global."""
    antigo = getattr(M, nome)
    setattr(M, nome, valor)
    caso.addCleanup(setattr, M, nome, antigo)
    return antigo


def _env(caso, nome, valor):
    """Define (ou apaga, com None) uma variável de ambiente só durante o teste."""
    antigo = os.environ.get(nome)

    def _desfaz():
        if antigo is None:
            os.environ.pop(nome, None)
        else:
            os.environ[nome] = antigo
    caso.addCleanup(_desfaz)
    if valor is None:
        os.environ.pop(nome, None)
    else:
        os.environ[nome] = valor


# --------------------------------------------------------------- o modelo

class OModeloSegueOPROPOSITO(unittest.TestCase):
    """`base` para achar o momento, `small` para o que vira legenda queimada.

    A regra antiga escolhia por DURAÇÃO, e duração nunca foi a pergunta certa.
    As duas transcrições deste projeto querem coisas diferentes: a da fonte
    inteira responde "onde vale a pena olhar?" e ninguém lê esse texto; a da
    janela vira legenda na tela, e legenda errada é um clipe refeito.

    `base` é ~3x mais rápido que `small`. O erro de palavra dele fica coberto
    porque ele nunca chega à tela -- a legenda vem SEMPRE da segunda passada.
    """

    def setUp(self):
        _env(self, "WARDEN_WHISPER", None)

    def test_a_fonte_inteira_ganha_o_modelo_rapido(self):
        size, porque = M.pick_model(150.0, janela=False)
        self.assertEqual(size, "base")
        self.assertIn("choose a moment", porque)

    def test_a_janela_ja_escolhida_ganha_o_modelo_bom(self):
        size, porque = M.pick_model(150.0, janela=True)
        self.assertEqual(size, "small")

    def test_o_proposito_vence_a_duracao_nos_DOIS_sentidos(self):
        """É o ponto todo da mudança, e é o que um teste por duração não pegava.

        Uma fonte CURTA transcrita inteira ganhava `small` pela regra antiga, e
        agora ganha `base`. Uma janela de uma fonte LONGA ganhava `base`, e
        agora ganha `small` -- que é a que queima na tela.
        """
        self.assertEqual(M.pick_model(60.0, janela=False)[0], "base")
        self.assertEqual(M.pick_model(7200.0, janela=True)[0], "small")

    def test_quem_nao_diz_o_proposito_recebe_a_resposta_de_sempre(self):
        """O padrão não pode mudar debaixo de quem já chamava. `pick_model(s)`
        continua respondendo pela duração, exatamente como respondia."""
        self.assertEqual(M.pick_model(300)[0], "small")
        self.assertEqual(M.pick_model(3600)[0], "base")
        self.assertEqual(M.pick_model(None)[0], "small")

    def test_o_override_do_dono_continua_ganhando_de_tudo(self):
        """Quem definiu WARDEN_WHISPER tem uma razão, e o propósito não discute
        com ela."""
        _env(self, "WARDEN_WHISPER", "medium")
        self.assertEqual(M.pick_model(150.0, janela=False)[0], "medium")
        self.assertEqual(M.pick_model(150.0, janela=True)[0], "medium")


# ------------------------------------------------- o que chega ao whisper

class _WhisperDeMentira:
    """Um faster-whisper que não transcreve nada e anota como foi chamado."""

    ultima_construcao = None
    ultima_transcricao = None

    def __init__(self, size, **kwargs):
        _WhisperDeMentira.ultima_construcao = dict(kwargs, size=size)

    def transcribe(self, audio, **kwargs):
        _WhisperDeMentira.ultima_transcricao = dict(kwargs, audio=audio)

        class _Seg:
            start, end, text = 0.0, 1.0, "oi"
        return iter([_Seg()]), None


class OQueChegaAoWhisper(unittest.TestCase):
    """As duas opções que valem 79s medidos, e a terceira que NÃO foi ligada.

    `cpu_threads`: sem ela o ctranslate2 fica com o padrão dele e os outros
    núcleos da imagem ficam parados.

    `beam_size=1`: busca gulosa em vez das cinco hipóteses por passo que o
    faster-whisper usa por padrão.

    `batch_size`: NÃO. O benchmark oficial do faster-whisper mede 3608 MB de
    pico com pipeline em lote e o compose desta imagem declara `mem_limit: 3g`.
    3608 > 3072, e um OOM kill no meio da transcrição é a falha mais cara que
    existe aqui: silenciosa, e depois de já ter gasto o tempo todo. Este teste
    existe para que ligá-la fique vermelho em vez de ficar lento em produção.
    """

    def setUp(self):
        self.dir = _temp(self)
        self.audio = os.path.join(self.dir, "fonte.wav")
        with open(self.audio, "wb") as fh:
            fh.write(b"\0")
        _WhisperDeMentira.ultima_construcao = None
        _WhisperDeMentira.ultima_transcricao = None

        # O módulo `faster_whisper` pode nem estar instalado nesta máquina --
        # `transcribe` o importa lá dentro --, então ele é FABRICADO aqui.
        import types
        falso = types.ModuleType("faster_whisper")
        falso.WhisperModel = _WhisperDeMentira
        self.antigo = sys.modules.get("faster_whisper")
        sys.modules["faster_whisper"] = falso

        def _restaura():
            if self.antigo is None:
                sys.modules.pop("faster_whisper", None)
            else:
                sys.modules["faster_whisper"] = self.antigo
        self.addCleanup(_restaura)

        # Nada de ffprobe, nada de esperar modelo baixar, nada de rede.
        _troca(self, "duration_of", lambda p: 150.0)
        _troca(self, "model_wait_note", lambda s: None)
        _troca(self, "_subtitle_beside", lambda p, prefer=None: (None, None))
        _env(self, "WARDEN_WHISPER", None)

    def _transcreve(self, **kw):
        return M.transcribe(self.audio, progress=lambda linha: None, **kw)

    def test_as_threads_da_maquina_chegam_ao_modelo(self):
        self._transcreve()
        threads = _WhisperDeMentira.ultima_construcao["cpu_threads"]
        self.assertEqual(threads, os.cpu_count() or 4)
        self.assertGreaterEqual(threads, 1)

    def test_o_dono_pode_deixar_nucleo_livre_para_o_ffmpeg(self):
        """WARDEN_WHISPER_THREADS existe porque o ffmpeg costuma estar rodando
        ao lado, e tomar a máquina inteira para o whisper é uma escolha do dono,
        não do agente."""
        _env(self, "WARDEN_WHISPER_THREADS", "2")
        self._transcreve()
        self.assertEqual(_WhisperDeMentira.ultima_construcao["cpu_threads"], 2)

    def test_um_valor_sem_sentido_na_variavel_nao_derruba_a_corrida(self):
        """Uma transcrição de minutos não pode morrer por causa de um typo numa
        variável de ambiente."""
        for lixo in ("zero", "", "-4", "0"):
            _env(self, "WARDEN_WHISPER_THREADS", lixo)
            self._transcreve()
            self.assertEqual(
                _WhisperDeMentira.ultima_construcao["cpu_threads"],
                os.cpu_count() or 4, lixo)

    def test_a_busca_e_gulosa(self):
        self._transcreve()
        self.assertEqual(_WhisperDeMentira.ultima_transcricao["beam_size"], 1)

    def test_o_lote_NAO_e_ligado_porque_3608_MB_nao_cabe_em_3g(self):
        self._transcreve()
        self.assertNotIn("batch_size", _WhisperDeMentira.ultima_transcricao)

    def test_o_que_ja_funcionava_continua_chegando(self):
        """As opções novas não podem ter empurrado as antigas para fora: sem VAD
        e sem tempo por palavra não há corte com in/out preciso."""
        self._transcreve()
        self.assertTrue(_WhisperDeMentira.ultima_transcricao["vad_filter"])
        self.assertTrue(_WhisperDeMentira.ultima_transcricao["word_timestamps"])
        self.assertEqual(_WhisperDeMentira.ultima_construcao["compute_type"], "int8")

    def test_o_proposito_atravessa_ate_o_modelo_construido(self):
        """A escolha de modelo não serve de nada se ela não chegar do outro
        lado: o que importa é o `size` com que o WhisperModel é CONSTRUÍDO."""
        self._transcreve(proposito="fonte")
        self.assertEqual(_WhisperDeMentira.ultima_construcao["size"], "base")
        self._transcreve(proposito="janela")
        self.assertEqual(_WhisperDeMentira.ultima_construcao["size"], "small")

    def test_sem_proposito_a_resposta_e_a_de_sempre(self):
        """150s de fonte, sem propósito: a regra por duração dá `small`. Quem
        não passa nada não pode ver o comportamento mudar."""
        self._transcreve()
        self.assertEqual(_WhisperDeMentira.ultima_construcao["size"], "small")


# ---------------------------------------------------------------- o nome

class OCaminhoImpressoEOCaminhoQueExiste(unittest.TestCase):
    """5 minutos e 9 chamadas de ferramenta por um acento.

    Medido em 15/09/2026: um arquivo do Drive chamado "Refrão.mp4" desceu com o
    nome em NFD -- `a` + U+0303 (til combinante) em vez do único caractere `ã`.
    Os dois se desenham IGUAIS na tela. O agente leu o nome, digitou "Refrão"
    como qualquer teclado produz (NFC), e o arquivo não existia. Nove
    tentativas, nenhuma mensagem útil, porque para o sistema de arquivos eram
    dois nomes diferentes.

    O contrato que estes testes guardam é de uma frase: o caminho que a função
    DEVOLVE é um caminho que `os.path.isfile` encontra.
    """

    def setUp(self):
        self.dir = _temp(self)

    def _escreve(self, nome):
        caminho = os.path.join(self.dir, nome)
        with open(caminho, "wb") as fh:
            fh.write(b"video")
        return caminho

    def test_um_acento_combinante_vira_um_caminho_que_da_para_abrir(self):
        nfd = unicodedata.normalize("NFD", "Refrão") + ".mp4"
        # A precondição, e ela é o defeito inteiro: o nome que a pessoa digita
        # NÃO é o mesmo texto que está no disco.
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        cru = self._escreve(nfd)

        # E aqui está a razão de este defeito ter custado 9 chamadas em vez de
        # ser óbvio: ele NÃO REPRODUZ no Mac do dono. O APFS normaliza o nome
        # por conta própria, então abrir por NFC funciona e o bug some. O
        # container é Linux, e Linux compara bytes -- é lá que o arquivo não
        # existe. Esta asserção só vale onde o sistema de arquivos não ajuda.
        if not os.path.isfile(os.path.join(self.dir, "Refrão.mp4")):
            self.assertFalse(os.path.isfile(
                os.path.join(self.dir, unicodedata.normalize("NFC", nfd))))

        devolvido = M.nome_no_disco(cru)

        self.assertTrue(os.path.isfile(devolvido),
                        f"o caminho devolvido não existe: {devolvido!r}")
        self.assertEqual(os.path.basename(devolvido), "Refrao.mp4")
        # E o nome devolvido é digitável: ASCII puro, sem combinante nenhum.
        devolvido.encode("ascii")

    def test_o_conteudo_e_o_mesmo_arquivo_e_nao_uma_copia(self):
        """Renomear não pode deixar o original para trás: um acervo de 671 MB
        duplicado em disco é o conserto virando o defeito seguinte."""
        cru = self._escreve(unicodedata.normalize("NFD", "Refrão") + ".mp4")
        devolvido = M.nome_no_disco(cru)
        with open(devolvido, "rb") as fh:
            self.assertEqual(fh.read(), b"video")
        self.assertFalse(os.path.isfile(cru))
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_um_nome_que_ja_e_seguro_nao_e_tocado(self):
        cru = self._escreve("source-abc123def456.mp4")
        self.assertEqual(M.nome_no_disco(cru), cru)
        self.assertTrue(os.path.isfile(cru))

    def test_aspas_e_dois_pontos_saem_porque_isto_vira_filtro_do_ffmpeg(self):
        """Um nome de arquivo aqui acaba dentro de uma string de filtro do
        ffmpeg mais adiante. Aspas ali não são estética."""
        # Sem `/` no nome: barra não é um caractere de nome de arquivo, é um
        # separador, e o teste estaria inventando um diretório em vez de testar
        # o nome.
        cru = self._escreve('corte "um": o melhor.mp4')
        devolvido = M.nome_no_disco(cru)
        self.assertTrue(os.path.isfile(devolvido))
        base = os.path.basename(devolvido)
        for ruim in ('"', ":", "'", " "):
            self.assertNotIn(ruim, base)

    def test_um_nome_inteiro_nao_ASCII_nao_vira_nome_vazio(self):
        """A rede embaixo da rede: um título em japonês some inteiro na conta do
        ASCII, e um arquivo chamado `.mp4` é pior que um arquivo com hash."""
        cru = self._escreve("動画.mp4")
        devolvido = M.nome_no_disco(cru)
        self.assertTrue(os.path.isfile(devolvido))
        self.assertTrue(os.path.basename(devolvido).startswith("media-"))
        self.assertTrue(devolvido.endswith(".mp4"))

    def test_uma_colisao_nao_apaga_o_vídeo_que_ja_estava_la(self):
        """Perder um vídeo do acervo para arrumar um acento seria um conserto
        pior que o defeito."""
        primeiro = self._escreve("Refrao.mp4")
        with open(primeiro, "wb") as fh:
            fh.write(b"o primeiro")
        segundo = self._escreve(unicodedata.normalize("NFD", "Refrão") + ".mp4")
        devolvido = M.nome_no_disco(segundo)
        self.assertTrue(os.path.isfile(devolvido))
        self.assertNotEqual(os.path.abspath(devolvido),
                            os.path.abspath(primeiro))
        with open(primeiro, "rb") as fh:
            self.assertEqual(fh.read(), b"o primeiro")

    def test_um_arquivo_que_nao_existe_nao_vira_caminho_inventado(self):
        ausente = os.path.join(self.dir, "nao-esta-aqui.mp4")
        self.assertEqual(M.nome_no_disco(ausente), ausente)


# --------------------------------------------------------------- o fiscal

class QuemMandaOLinkJaAutorizou(unittest.TestCase):
    """Decisão do dono, 15/09/2026. O agente nunca pede licença.

    E a metade que NÃO mudou: a decisão foi sobre licença, não sobre segurança.
    `safe_url` continua de pé antes de qualquer sim.
    """

    def test_a_lista_vazia_nao_impede_nada(self):
        """A lista vazia é o estado de TODA instalação nova. Ela recusava o
        primeiro link que o dono mandasse e o mandava avalizar a si mesmo."""
        ok, porque, _ = M.fiscal("https://www.youtube.com/watch?v=AAAAAAAAAAA",
                                 trusted=[])
        self.assertTrue(ok, porque)
        self.assertIn("sent by the person", porque)

    def test_uma_fonte_fora_da_lista_tambem_passa(self):
        ok, _porque, _ = M.fiscal("https://exemplo.com/v.mp4",
                                  trusted=["youtube.com"])
        self.assertTrue(ok)

    def test_o_fiscal_nao_toca_a_rede(self):
        """O ganho que veio junto com a decisão. O caminho antigo chamava
        `_channel_facts` -> `_facts` -> yt-dlp para decidir licença, então o
        portão sofria o mesmo bloqueio de bot que o download: um link
        perfeitamente autorizado virava "não consegui checar a fonte" por causa
        de uma recusa de rede."""
        def _explode(*a, **k):
            raise AssertionError("o fiscal não pode tocar a rede")
        _troca(self, "run", _explode)
        _troca(self, "_facts", _explode)
        ok, _porque, _ = M.fiscal("https://www.youtube.com/watch?v=AAAAAAAAAAA",
                                  trusted=["@umcanalqualquer"])
        self.assertTrue(ok)

    def test_a_seguranca_continua_recusando(self):
        """Um link para dentro da máquina continua recusado, e isso nunca foi
        uma pergunta sobre direitos autorais."""
        for url in ("http://127.0.0.1/interno.mp4",
                    "http://169.254.169.254/latest/meta-data",
                    "http://10.0.0.1/v.mp4",
                    "file:///etc/passwd",
                    "ftp://exemplo.com/v.mp4"):
            with self.assertRaises(RuntimeError, msg=url) as erro:
                M.fiscal(url, trusted=[])
            self.assertIn("refusing", str(erro.exception))

    def test_com_campanha_o_acervo_continua_respondendo(self):
        """A outra porta não foi tocada: uma campanha sem acervo publicado
        continua não autorizando nada."""
        regras = {"schema": 1, "id": "f", "video": {"width": 1080, "height": 1920},
                  "sources": {"archive_urls": []}, "caption": {}, "posting": {}}
        ok, _porque, _ = M.fiscal("https://www.youtube.com/watch?v=AAAAAAAAAAA",
                                  rules=regras)
        self.assertFalse(ok)

    def test_um_IPv6_que_embrulha_um_endereco_interno_nao_passa(self):
        """NAT64 (`64:ff9b::/96`) e IPv4-mapeado (`::ffff:0:0/96`) carregam um
        IPv4 dentro de si, e desembrulhá-los foi o que fez `vimeo.com` voltar a
        ser aceito numa rede IPv6-only. O desembrulho não pode ter virado a
        porta de entrada que ele fechou."""
        import ipaddress
        for embrulhado, esperado in (("64:ff9b::7f00:1", "127.0.0.1"),
                                     ("::ffff:169.254.169.254", "169.254.169.254"),
                                     ("64:ff9b::a9fe:a9fe", "169.254.169.254")):
            dentro = M._desembrulha(ipaddress.ip_address(embrulhado))
            self.assertEqual(str(dentro), esperado)
            self.assertFalse(dentro.is_global, embrulhado)


# --------------------------------------------------------------- cookies

class OArquivoDeCookiesDoDono(unittest.TestCase):
    """A única escotilha de saída do bloqueio de bot, e ela nunca é do repositório.

    O arquivo fica na máquina de quem instalou, sob a conta dele. O CAMINHO
    aparece em mensagem de erro; o CONTEÚDO nunca, em lugar nenhum -- um
    cookies.txt do YouTube é a sessão inteira daquela conta, e este agente é
    publicado.
    """

    def setUp(self):
        self.dir = _temp(self)
        # Sem isto, um `deno` ou `node` instalado na máquina muda a linha de
        # comando e o teste passa a medir a máquina, não o código.
        _troca(self, "have", lambda b: False)

    def _cookies(self, texto="# Netscape HTTP Cookie File\n"):
        caminho = os.path.join(self.dir, "cookies.txt")
        with open(caminho, "w") as fh:
            fh.write(texto)
        return caminho

    def test_a_variavel_do_dono_aponta_o_arquivo(self):
        """WARDEN_YT_COOKIES é lida na importação do módulo, então o teste
        recarrega -- é o mesmo caminho que uma instalação de verdade faz."""
        import importlib
        caminho = self._cookies()
        _env(self, "WARDEN_YT_COOKIES", caminho)
        self.addCleanup(importlib.reload, M)
        recarregado = importlib.reload(M)
        self.assertEqual(recarregado.COOKIES_FILE, caminho)

    def test_o_arquivo_existente_entra_na_linha_de_comando(self):
        caminho = self._cookies()
        _troca(self, "COOKIES_FILE", caminho)
        args = M._ytdlp()
        self.assertIn("--cookies", args)
        self.assertEqual(args[args.index("--cookies") + 1], caminho)

    def test_sem_arquivo_a_opcao_NAO_e_passada(self):
        """`--cookies` apontando para um arquivo que não existe faz o yt-dlp
        abortar antes de tentar o link. A instalação sem cookies é a padrão e a
        de todo mundo: ela pararia de funcionar por completo."""
        _troca(self, "COOKIES_FILE", os.path.join(self.dir, "nao-existe.txt"))
        self.assertNotIn("--cookies", M._ytdlp())

    def test_um_arquivo_ilegivel_conta_como_ausente(self):
        """Um `--cookies` para arquivo sem permissão de leitura aborta o yt-dlp
        do mesmo jeito, e a mensagem que chega fala de permissão de arquivo no
        meio de uma frase sobre baixar vídeo."""
        caminho = self._cookies()
        os.chmod(caminho, 0o000)
        self.addCleanup(os.chmod, caminho, 0o600)
        if os.access(caminho, os.R_OK):
            self.skipTest("este processo lê qualquer arquivo (root?)")
        _troca(self, "COOKIES_FILE", caminho)
        self.assertNotIn("--cookies", M._ytdlp())

    def test_o_conteudo_do_arquivo_nunca_aparece_na_mensagem(self):
        """O teste que impede o pior acidente possível deste caminho: a sessão
        do dono vazando para um log, um chat ou um relatório de bug."""
        segredo = "SESSION_TOKEN_DO_DONO_12345"
        caminho = self._cookies(
            f".youtube.com\tTRUE\t/\tTRUE\t0\tSID\t{segredo}\n")
        _troca(self, "COOKIES_FILE", caminho)
        _troca(self, "_pot_alive", lambda: False)
        msg = M._porque_bloqueou("yt-dlp")
        self.assertNotIn(segredo, msg)
        self.assertIn(caminho, msg)     # o CAMINHO sim, para o dono achá-lo


class AFraseDoBloqueioDizSeHaviaCookies(unittest.TestCase):
    """A causa medida numa frase, antes dos cinco parágrafos.

    A explicação honesta tem cinco parágrafos, e cinco parágrafos é o que o
    modelo resume -- resumir foi exatamente como as causas inventadas nasceram,
    duas vezes, em dois dias. Então a primeira linha diz a coisa toda sozinha,
    inclusive se havia cookies: é a única variável que o dono controla, e a
    resposta muda com ela. Sem arquivo, o conserto é exportar um. Com arquivo
    que não resolveu, o conserto é outra rede.
    """

    def setUp(self):
        self.dir = _temp(self)
        _troca(self, "have", lambda b: False)
        _troca(self, "_pot_alive", lambda: False)

    def test_sem_cookies_a_frase_diz_que_nao_ha_nenhum(self):
        _troca(self, "COOKIES_FILE", os.path.join(self.dir, "nao-existe.txt"))
        msg = M._porque_bloqueou("yt-dlp")
        self.assertIn("In one sentence:", msg)
        self.assertIn("demanding a login", msg)
        self.assertIn("bot check", msg)
        self.assertIn("no cookies file is configured", msg)
        self.assertIn("WARDEN_YT_COOKIES", msg)

    def test_com_cookies_a_frase_diz_que_eles_nao_resolveram(self):
        caminho = os.path.join(self.dir, "cookies.txt")
        with open(caminho, "w") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
        _troca(self, "COOKIES_FILE", caminho)
        msg = M._porque_bloqueou("yt-dlp")
        self.assertIn("In one sentence:", msg)
        self.assertIn("did not clear it", msg)
        self.assertNotIn("no cookies file is configured", msg)

    def test_as_duas_frases_sao_DIFERENTES(self):
        """O teste que impede a frase de virar decoração: se as duas leituras
        dessem o mesmo texto, dizer "diz se havia cookies" seria falso e ninguém
        perceberia."""
        sem = os.path.join(self.dir, "nao-existe.txt")
        com = os.path.join(self.dir, "cookies.txt")
        with open(com, "w") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
        _troca(self, "COOKIES_FILE", sem)
        a = M._porque_bloqueou("yt-dlp")
        _troca(self, "COOKIES_FILE", com)
        b = M._porque_bloqueou("yt-dlp")
        self.assertNotEqual(a, b)

    def test_a_mensagem_continua_proibindo_as_tres_invencoes(self):
        """A frase nova não pode ter empurrado para fora o que ela veio
        reforçar: as três coisas que o agente disse ao dono e eram mentira."""
        _troca(self, "COOKIES_FILE", os.path.join(self.dir, "nao-existe.txt"))
        msg = M._porque_bloqueou("yt-dlp")
        self.assertIn("not this video", msg)
        self.assertIn("not the source's authorisation", msg)
        self.assertIn("not something that clears up on its own", msg)
        self.assertIn("different outgoing address", msg)


# --------------------------------------------------------------- legenda

class ALegendaNaLinguaDoVIDEO(unittest.TestCase):
    """220s de transcrição num vídeo que publicava legenda, por falta de `en`.

    Medido em 15/09/2026. A lista era `pt,pt-BR`, então toda fonte que não fosse
    brasileira caía na transcrição com a legenda publicada ao lado -- que é o
    defeito exato que o caminho barato existe para evitar.

    O que torna isto seguro é um conserto que já estava de pé: a legenda tem a
    própria corrida, então um 429 numa língua custa a legenda daquela corrida e
    não a execução inteira, que era o que custava em 14/09.
    """

    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def setUp(self):
        M._FACTS.clear()
        self.addCleanup(M._FACTS.clear)
        _env(self, "WARDEN_SUB_LANGS", None)
        # `safe_url` resolve host de verdade; aqui o DNS não é o assunto.
        _troca(self, "_host_is_public", lambda host: True)

    def _lingua(self, valor):
        """Responde à ÚNICA extração de metadados com esta língua."""
        campos = {"title": "Um título", "channel_id": "UCabc",
                  "uploader_id": "@canal", "channel_url": "u", "uploader_url": "u",
                  "language": valor}
        linha = "\t".join(campos[c] for c in M._FACTS_CAMPOS)
        self.chamadas = []

        def _run(args, timeout, label):
            self.chamadas.append(args)
            return linha + "\n"
        _troca(self, "run", _run)

    def test_um_video_em_ingles_pede_legenda_em_ingles(self):
        self._lingua("en")
        langs = M._sub_langs_para(self.URL)
        self.assertTrue(langs.startswith("en"), langs)

    def test_uma_lingua_que_ninguem_previu_entra_na_frente(self):
        """O ponto não é "pt e en": é a língua DO VÍDEO. Um vídeo em espanhol
        publica legenda em espanhol, e ela é a legenda certa. Na FRENTE porque
        o yt-dlp para no primeiro acerto."""
        self._lingua("es")
        langs = M._sub_langs_para(self.URL)
        self.assertTrue(langs.startswith("es"), langs)

    def test_NENHUM_CURINGA_chega_a_linha_de_comando(self):
        """Medido no container em 15/09/2026, 15:33 BRT, e é o teste que guarda
        a medição: `--sub-langs "pt.*"` expandiu para TRÊS variantes sozinho --
        escreveu `pt-BR` e `pt-en`, e levou `HTTP Error 429` na terceira. Um
        curinga é uma rajada escrita em dois caracteres."""
        for lingua in ("en", "es", "pt-BR", "ja"):
            M._FACTS.clear()
            self._lingua(lingua)
            langs = M._sub_langs_para(self.URL)
            self.assertNotIn("*", langs, lingua)
        self.assertNotIn("*", M.SUB_LANGS_PADRAO)

    def test_a_lista_para_em_tres_entradas(self):
        """O teto é de REQUISIÇÕES, não de gosto: a terceira variante já levou
        429 na medição."""
        for lingua in ("en", "es", "pt-BR", "ja", "de"):
            M._FACTS.clear()
            self._lingua(lingua)
            langs = M._sub_langs_para(self.URL)
            self.assertLessEqual(len(langs.split(",")), M.SUB_LANGS_MAX, langs)

    def test_a_raiz_nao_volta_como_variante_da_lingua_detectada(self):
        """`en` depois de `en-US` é uma requisição a mais pela mesma legenda, e
        requisição a mais é o que custa aqui."""
        self._lingua("en-US")
        langs = M._sub_langs_para(self.URL).split(",")
        self.assertEqual(langs[0], "en-US")
        self.assertNotIn("en", langs)

    def test_o_portugues_continua_na_lista_seja_qual_for_a_lingua(self):
        """Descobrir a original não pode APAGAR as nossas: o dono publica em
        português e a legenda dele continua sendo preferida."""
        self._lingua("ja")
        langs = M._sub_langs_para(self.URL)
        self.assertIn("pt-BR", langs)

    def test_o_padrao_novo_ja_inclui_ingles_sem_detectar_nada(self):
        self.assertIn("en", M.SUB_LANGS_PADRAO)
        self.assertIn("pt", M.SUB_LANGS_PADRAO)
        self.assertLessEqual(len(M.SUB_LANGS_PADRAO.split(",")), M.SUB_LANGS_MAX)

    def test_a_deteccao_custa_no_maximo_UMA_requisicao_e_ela_e_cacheada(self):
        """O comentário em cima de SUB_LANGS avisa que cada requisição a mais é
        uma chance a mais de 429, e este teste é o que impede a detecção de
        virar duas. `_facts` traz título, canal e língua da MESMA resposta."""
        self._lingua("en")
        M._sub_langs_para(self.URL)
        M._sub_langs_para(self.URL)
        M.video_title(self.URL)
        self.assertEqual(len(self.chamadas), 1)

    def test_a_variavel_do_dono_sobrescreve_TUDO(self):
        """Quem definiu WARDEN_SUB_LANGS decidiu. Uma detecção que passasse por
        cima dela seria o agente discutindo com o dono -- e ela nem consulta."""
        self._lingua("en")
        _env(self, "WARDEN_SUB_LANGS", "fr,de")
        self.assertEqual(M._sub_langs_para(self.URL), "fr,de")
        self.assertEqual(self.chamadas, [])

    def test_deteccao_que_falha_cai_no_padrao_sem_erro(self):
        """Uma legenda a menos é caro; uma execução derrubada por causa de uma
        consulta de idioma seria pior."""
        def _explode(args, timeout, label):
            raise RuntimeError("yt-dlp metadata lookup failed:\n  gone")
        _troca(self, "run", _explode)
        self.assertEqual(M._sub_langs_para(self.URL), M.SUB_LANGS)

    def test_endereco_bloqueado_nao_vaza_pela_consulta_de_idioma(self):
        """Se o endereço está recusado, quem diz isso é a corrida da legenda,
        com a mensagem honesta inteira -- não uma consulta de idioma vazando por
        baixo dela."""
        def _bloqueia(args, timeout, label):
            raise M.FonteBloqueada(M._porque_bloqueou("yt-dlp"))
        _troca(self, "run", _bloqueia)
        self.assertEqual(M._sub_langs_para(self.URL), M.SUB_LANGS)

    def test_uma_lingua_com_lixo_dentro_nao_entra_na_linha_de_comando(self):
        """O campo vem do lado de lá e acaba num argumento de subprocesso. O
        filtro é de FORMATO de tag de idioma, não de confiança -- e `pt.*`
        vindo de lá seria o 429 medido, entregue de graça."""
        for lixo in ("en; rm -rf /", "../../etc", "NA", "", "e" * 40, "pt.*"):
            self._lingua(lixo)
            M._FACTS.clear()
            self.assertEqual(M._sub_langs_para(self.URL), M.SUB_LANGS, lixo)


class UmQuatroCentoEVinteENoveNaoDerrubaOArchive(unittest.TestCase):
    """O 429 é o YouTube dizendo "devagar", não "esta máquina está bloqueada".

    Medido no container em 15/09/2026, 15:33 BRT: `--sub-langs "pt.*"` escreveu
    `t1.pt-BR.vtt` (3822 B) e `t1.pt-en.vtt` (4280 B) e SÓ ENTÃO levou
    `HTTP Error 429` na terceira variante, `pt-PT-en`.

    Duas coisas quebravam aí, e as duas eram caras:

      1. `_BOT_CHECK` casa 429 junto com "not a bot", então a corrida da legenda
         subia `FonteBloqueada` e derrubava o `archive` inteiro com um parágrafo
         dizendo ao dono que o endereço da casa dele está recusado. Falso.
      2. A checagem de "sobrou alguma legenda?" olhava o disco ANTES da corrida,
         então as duas legendas recém-escritas não contavam -- e o agente ia
         baixar o áudio para transcrever, com a legenda no disco.
    """

    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def setUp(self):
        self.dir = _temp(self)
        M._FACTS.clear()
        self.addCleanup(M._FACTS.clear)
        _troca(self, "_host_is_public", lambda host: True)
        _env(self, "WARDEN_SUB_LANGS", "pt-BR,pt,en")
        self.stem = "source-abc"
        self.template = os.path.join(self.dir, self.stem + ".%(ext)s")

    @staticmethod
    def _falha_com(texto):
        """Um comando REAL que falha imprimindo `texto` no stderr.

        Um subprocesso de verdade em vez de um mock, pelo mesmo motivo que
        `test_fonte_bloqueada.py` faz assim: o que está sendo testado é a
        leitura que `run` faz do código de retorno e do stderr, e o texto é o
        que o yt-dlp imprimiu de verdade na medição de 15/09.
        """
        return [sys.executable, "-c",
                "import sys; sys.stderr.write(%r); sys.exit(1)" % texto]

    SAIDA_429 = (
        "[info] Writing video subtitles to: /tmp/t1.pt-PT-en.vtt\n"
        "ERROR: Unable to download video subtitles for 'pt-PT-en': "
        "HTTP Error 429: Too Many Requests\n")
    SAIDA_BOT = (
        "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication.\n")

    def test_o_429_e_marcado_como_diferente_do_bot_check(self):
        """A marca existe porque a mensagem da exceção é `_porque_bloqueou`, que
        não carrega a saída original: sem ela, quem pega a exceção não tem como
        perguntar qual dos dois foi."""
        with self.assertRaises(M.FonteBloqueada) as erro:
            M.run(self._falha_com(self.SAIDA_429), 30, "yt-dlp (subtitles)")
        self.assertTrue(erro.exception.apenas_429)

    def test_o_bot_check_de_verdade_NAO_e_marcado_como_429(self):
        """A outra metade, e a que não pode afrouxar: um endereço realmente
        recusado continua subindo inteiro."""
        with self.assertRaises(M.FonteBloqueada) as erro:
            M.run(self._falha_com(self.SAIDA_BOT), 30, "yt-dlp (subtitles)")
        self.assertFalse(erro.exception.apenas_429)

    def test_a_legenda_ja_escrita_e_usada_apesar_do_429(self):
        """O ponto inteiro: a variante que JÁ baixou serve. Pagar 220s de
        transcrição com a legenda no disco é o defeito que o caminho barato
        existe para evitar."""
        def _run(args, timeout, label):
            for variante in ("pt-BR", "pt-en"):
                with open(os.path.join(self.dir,
                                       f"{self.stem}.{variante}.srt"), "w") as fh:
                    fh.write("1\n00:00:00,000 --> 00:00:01,000\noi\n\n")
            erro = M.FonteBloqueada("429")
            erro.apenas_429 = True
            raise erro
        _troca(self, "run", _run)
        caminho, porque = M._pull_subs(self.URL, self.dir, self.stem,
                                       self.template, ["--no-playlist"])
        self.assertIsNotNone(caminho, porque)
        self.assertTrue(os.path.isfile(caminho))
        self.assertIsNone(porque)

    def test_um_429_sem_nenhuma_legenda_vira_aviso_e_nao_bloqueio(self):
        """Sem legenda nenhuma, o 429 ainda não pode derrubar o `archive`: ele
        vira "não veio legenda" e a transcrição segue."""
        def _run(args, timeout, label):
            erro = M.FonteBloqueada("429")
            erro.apenas_429 = True
            raise erro
        _troca(self, "run", _run)
        caminho, porque = M._pull_subs(self.URL, self.dir, self.stem,
                                       self.template, ["--no-playlist"])
        self.assertIsNone(caminho)
        self.assertIn("429", porque)

    def test_a_requisicao_de_legenda_NAO_e_repetida_depois_do_429(self):
        """Repetir é o que transforma um "devagar" em bloqueio de verdade.

        Conta por RÓTULO, não o total. A consulta de metadados (que descobre a
        língua do vídeo) é outra requisição, com outro propósito, e somá-la aqui
        faria este teste falhar por uma razão que não é a dele -- foi o que
        aconteceu quando a regra da língua entrou. O que não pode repetir é a
        corrida de legenda.
        """
        self.chamadas = []

        def _run(args, timeout, label):
            self.chamadas.append(label)
            erro = M.FonteBloqueada("429")
            erro.apenas_429 = True
            raise erro
        _troca(self, "run", _run)
        M._pull_subs(self.URL, self.dir, self.stem, self.template,
                     ["--no-playlist"])
        legendas = [l for l in self.chamadas if "subtitle" in l]
        self.assertEqual(len(legendas), 1, self.chamadas)
        # E a consulta de metadados também não pode virar rajada: uma só.
        metadados = [l for l in self.chamadas if "metadata" in l]
        self.assertLessEqual(len(metadados), 1, self.chamadas)

    def test_um_bot_check_de_verdade_continua_subindo_inteiro(self):
        """O conserto do 429 não pode ter aberto a porta que `FonteBloqueada`
        fechou: um endereço recusado ainda derruba a corrida, com a mensagem
        honesta, em vez de virar "este vídeo não tem legenda"."""
        def _run(args, timeout, label):
            erro = M.FonteBloqueada(M._porque_bloqueou("yt-dlp (subtitles)"))
            erro.apenas_429 = False
            raise erro
        _troca(self, "run", _run)
        with self.assertRaises(M.FonteBloqueada):
            M._pull_subs(self.URL, self.dir, self.stem, self.template,
                         ["--no-playlist"])


class OBLOQUEIOMudaComOENDERECO(unittest.TestCase):
    """Medido em 15/09/2026: o mesmo container, por OUTRO endereço, baixou.

    E esta classe existe com este nome porque a primeira versão dela dizia
    outra coisa -- dizia que a recusa "liberou sozinha no mesmo endereço em
    algumas horas". Isso estava errado, e errado do jeito mais caro possível: o
    dono tinha ligado o hotspot 5G sem avisar, então o endereço de saída havia
    mudado de Mundivox residencial para a Telefônica (AS26599, 189.98.253.32).
    A variável que mudou não foi o tempo, foi o endereço -- e ninguém conferiu
    antes de escrever "passou sozinho" no código.

    É exatamente o defeito que este arquivo inteiro existe para impedir, dessa
    vez cometido POR dentro dele: uma causa afirmada com confiança sem medir a
    variável que mudou. Ficou registrado aqui em vez de ser apagado em silêncio.

    O que os testes de 15/09 realmente provaram, e é mais forte: por DOIS
    endereços diferentes o mesmo container baixou normalmente -- Telefônica
    (AS26599, 189.98.253.32) às 15:33, e Mundivox (AS17222, 67.159.227.250, o
    provedor de casa) às 19:00 --, enquanto de manhã o endereço residencial
    recusava. Ou seja: a saída de escape número 1 ("outro endereço de saída")
    está MEDIDA, não é mais só o que a documentação do yt-dlp promete.

    E aqui mora a segunda armadilha, que é a inversa da primeira: com o
    endereço de casa servindo download às 19:00, a tentação vira dizer "então
    passou sozinho". Não passou -- ou melhor, ninguém sabe. **O IP recusado de
    manhã não foi anotado**, e IP residencial troca por reconexão de modem ou
    lease do provedor. A marcação pode ter caído, ou o endereço pode ter mudado
    dentro do mesmo AS. As DUAS explicações cabem nos mesmos números, e é
    exatamente por isso que nenhuma pode ser afirmada.

    A LIÇÃO DE MÉTODO, e ela custou duas quase-invenções num dia só: **anotar o
    endereço de saída ANTES e DEPOIS de toda medição de rede, sempre.** As duas
    vezes em que este projeto quase gravou uma causa falsa hoje foram por falta
    de um `ipinfo.io` de dois segundos -- uma porque o hotspot estava ligado sem
    ninguém saber, outra porque o IP de manhã não foi registrado. A variável que
    muda é a que precisa de número, e num agente que baixa vídeo essa variável é
    o endereço.

    O que continua NÃO medido, e portanto não pode ser dito em nenhuma direção:
    se a recusa é permanente, e se ela passa sozinha.
    """

    def setUp(self):
        self.dir = _temp(self)
        _troca(self, "have", lambda b: False)
        _troca(self, "_pot_alive", lambda: False)
        _troca(self, "COOKIES_FILE", os.path.join(self.dir, "nao-existe.txt"))

    @staticmethod
    def _corrido():
        """A mensagem com as quebras de linha achatadas.

        A mensagem é quebrada para caber numa tela; o que estes testes querem é
        o CONTEÚDO. Sem achatar, uma asserção passa ou falha conforme onde a
        linha quebrou, que é o teste medindo a formatação em vez da frase.
        """
        return " ".join(M._porque_bloqueou("yt-dlp").split())

    def test_a_mensagem_diz_que_outro_endereco_FUNCIONOU_e_quais(self):
        """Os AS entram na frase de propósito: "outra rede" é vago, "AS26599, e
        baixou 830 KiB em 6s" é uma medição que o dono pode conferir. São dois
        porque foram dois, e o segundo é o provedor de casa."""
        msg = self._corrido()
        self.assertIn("15/09/2026", msg)
        self.assertIn("AS26599", msg)
        self.assertIn("AS17222", msg)
        self.assertIn("830 KiB in 6s", msg)
        self.assertIn("830 KiB in 7s", msg)

    def test_a_mensagem_NAO_diz_que_a_recusa_passa_sozinha(self):
        """A frase que saiu, e o teste que impede sua volta. Ela afirmava uma
        coisa não medida e atribuía a causa errada -- era o hotspot, não o
        tempo."""
        msg = self._corrido()
        for invencao in ("lift by itself", "clears up on its own in hours",
                         "hours rather than minutes", "wait a few hours",
                         "try again later"):
            self.assertNotIn(invencao, msg)

    def test_a_mensagem_NAO_diz_que_a_marcacao_CAIU(self):
        """A tentação INVERSA, criada pela medição das 19:00: com o endereço de
        casa baixando à noite, "então a marcação caiu" parece óbvio -- e é
        indistinguível de "o IP trocou", porque ninguém anotou o IP de manhã.
        Afirmar qualquer uma das duas seria repetir o erro do hotspot com o
        sinal trocado."""
        msg = self._corrido()
        for invencao in ("the flag was lifted at", "the block was lifted",
                         "no longer flagged", "the refusal is over",
                         "it has cleared"):
            self.assertNotIn(invencao, msg)
        # E o motivo tem de estar ESCRITO, não só o veredito: quem ler precisa
        # saber POR QUE não dá para escolher um lado.
        self.assertIn("nobody wrote down the IP", msg)
        self.assertIn("a residential IP changes on its own", msg)
        self.assertIn("BOTH explanations fit these numbers", msg)

    def test_e_tambem_NAO_diz_que_a_recusa_e_permanente(self):
        """As duas direções são invenção: nenhuma das duas foi medida. O que
        foi medido é que muda com o endereço."""
        msg = self._corrido()
        self.assertIn("What is NOT measured", msg)
        self.assertIn("do not say it is permanent", msg)
        self.assertIn("the refusal travels with the outgoing address", msg)

    def test_e_diz_junto_o_que_resolve_AGORA(self):
        """Nada disso pode substituir a resposta que entrega um clipe hoje."""
        msg = self._corrido()
        self.assertIn("TODAY", msg)
        self.assertIn("Drive", msg)


# ------------------------------------------------------------------ drive

class ALegendaSegueALinguaDoVIDEO(unittest.TestCase):
    """Decisão do dono, 15/09/2026: a legenda tem de estar na língua do vídeo.

    NÃO é "preferir português" -- essa era a regra antiga, e ela estava errada
    como padrão. Um vídeo em inglês quer legenda em inglês; em espanhol,
    espanhol. A legenda na língua da fonte é a que a fonte publicou; qualquer
    outra é tradução de máquina, normalmente por cima de transcrição de máquina,
    que é erro empilhado sobre erro.

    E o defeito que isto conserta é de corrida, medido em 15/09/2026: seis
    execuções de `lote prep` no MESMO link, cada uma num WARDEN_DIR limpo, deram
    `pt-br` em quatro e `en` em duas. Não era sorteio -- `sorted()` põe
    `.en.srt` antes de `.pt-BR.srt`, e o que variava era QUAIS variantes
    chegavam antes do 429. A escolha seguia a ordem de CHEGADA.

    Custava o clipe inteiro: `cut` recusa queimar hook e legenda em línguas
    diferentes (regra certa, não se toca), então a rodada "errada" entregava
    zero clipe. Duas em seis.
    """

    def setUp(self):
        self.dir = _temp(self)

    def _legendas(self, *tags):
        """Escreve as variantes e devolve os caminhos em ordem embaralhada."""
        caminhos = []
        for tag in tags:
            nome = f"source-abc123.{tag}.srt" if tag else "source-abc123.srt"
            caminho = os.path.join(self.dir, nome)
            with open(caminho, "w") as fh:
                fh.write("1\n00:00:00,000 --> 00:00:01,000\noi\n\n")
            caminhos.append(caminho)
        return caminhos

    def test_video_em_ingles_com_traducao_pt_escolhe_INGLES(self):
        """O caso exato da medição: o vídeo é em inglês e o YouTube oferece
        pt-BR traduzido automaticamente. A legenda em inglês é a original; a
        portuguesa é ASR traduzido, duas camadas de erro empilhadas."""
        caminhos = self._legendas("en", "pt-BR")
        escolhida = M._prefere_idioma(caminhos, prefer=["en"])[0]
        self.assertEqual(M._tag_do_nome(escolhida), "en")

    def test_video_em_portugues_escolhe_PORTUGUES(self):
        caminhos = self._legendas("en", "pt-BR")
        escolhida = M._prefere_idioma(caminhos, prefer=["pt"])[0]
        self.assertEqual(M._tag_do_nome(escolhida), "pt-br")

    def test_a_variante_regional_casa_pela_RAIZ(self):
        """`pt` aceita `pt-BR`, `en` aceita `en-US`. Sem isso um vídeo marcado
        como `en` ignoraria a legenda `en-US` que é dele mesmo."""
        self.assertEqual(
            M._tag_do_nome(M._prefere_idioma(
                self._legendas("pt-BR", "en-US"), prefer=["en"])[0]), "en-us")
        self.assertEqual(
            M._tag_do_nome(M._prefere_idioma(
                self._legendas("es-419", "pt-BR"), prefer=["es"])[0]), "es-419")

    def test_a_escolha_e_A_MESMA_com_a_ordem_de_chegada_EMBARALHADA(self):
        """O teste que trava a corrida. O 429 fazia as variantes chegarem em
        ordens diferentes a cada rodada, e a escolha seguia a chegada. Com a
        preferência decidindo DEPOIS de saber o que chegou, as 24 permutações
        têm de dar o mesmo arquivo."""
        import itertools
        caminhos = self._legendas("en", "pt-BR", "pt", "es")
        vistos = set()
        for ordem in itertools.permutations(caminhos):
            vistos.add(M._prefere_idioma(list(ordem), prefer=["en"])[0])
        self.assertEqual(len(vistos), 1, f"escolha instável: {vistos}")
        self.assertEqual(M._tag_do_nome(vistos.pop()), "en")

    def test_sem_a_lingua_do_video_a_escolha_ainda_e_ESTAVEL(self):
        """Mesmo no plano B -- língua da fonte desconhecida -- duas execuções
        iguais têm de dar o mesmo resultado. Instabilidade é o defeito; a ordem
        escolhida é secundária."""
        import itertools
        caminhos = self._legendas("en", "pt-BR")
        vistos = {M._prefere_idioma(list(o))[0]
                  for o in itertools.permutations(caminhos)}
        self.assertEqual(len(vistos), 1, f"escolha instável: {vistos}")

    def test_uma_lingua_que_ninguem_conhece_entra_se_for_a_unica(self):
        caminhos = self._legendas("fr")
        self.assertEqual(
            M._tag_do_nome(M._prefere_idioma(caminhos, prefer=["fr"])[0]), "fr")

    def test_a_tag_SAI_DO_NOME_do_arquivo(self):
        """O segundo bug da medição: o arquivo chamava-se `source-....en.srt`,
        com a tag no nome, e a saída dizia "no language tag" -- perdendo a única
        informação que deixaria o modelo perceber o idioma errado."""
        self.assertEqual(M._tag_do_nome("source-abc123.en.srt"), "en")
        self.assertEqual(M._tag_do_nome("source-abc123.pt-BR.srt"), "pt-br")
        self.assertEqual(M._tag_do_nome("/x/y/source-abc.es-419.vtt"), "es-419")
        # E o que NÃO é tag continua não sendo.
        self.assertIsNone(M._tag_do_nome("source-abc123.srt"))
        self.assertIsNone(M._tag_do_nome("source-abc123.orig.srt"))
        self.assertIsNone(M._tag_do_nome("janela-abc-122.0.mp4"))

    def test_a_legenda_COMO_fonte_nao_perde_a_tag(self):
        """`--text-first` devolve o `.srt` COMO fonte, então `transcribe` abre
        um arquivo que JÁ é a legenda. Era aí que "no language tag" nascia."""
        caminho = self._legendas("en")[0]
        achado, lang = M._subtitle_beside(caminho)
        self.assertEqual(achado, caminho)
        self.assertEqual(lang, "en")

    def test_a_lingua_do_video_e_anotada_e_relida_depois(self):
        """Quem escolhe legenda depois -- no `transcribe` -- não tem o link para
        perguntar de novo. Sem a anotação ele voltaria a decidir por ordem fixa
        em vez de pela língua da fonte."""
        self._legendas("en", "pt-BR")
        fonte = os.path.join(self.dir, "source-abc123.mp4")
        with open(fonte, "wb") as fh:
            fh.write(b"v")
        M._marca_lingua(self.dir, "source-abc123", "en")
        self.assertEqual(M._lingua_marcada(fonte), "en")
        _achado, lang = M._subtitle_beside(fonte)
        self.assertEqual(lang, "en", "ignorou a língua anotada do vídeo")

    def test_a_corrida_MEDIDA_nao_reproduz_mais_pelo_pull_subs(self):
        """A reprodução da medição, pelo caminho de verdade e não pela função
        de ordenar: seis rodadas do `_pull_subs` no mesmo link, variando QUAIS
        variantes conseguem baixar antes do 429 -- que foi o que variava na
        máquina. O vídeo é em inglês; a resposta tem de ser `en` nas seis, e
        `pt-br` (tradução automática) nunca pode ganhar."""
        _troca(self, "_host_is_public", lambda host: True)
        _env(self, "WARDEN_SUB_LANGS", None)
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        # O vídeo é em inglês, e é isso que a extração de metadados responde.
        campos = {"title": "T", "channel_id": "UC", "uploader_id": "@c",
                  "channel_url": "u", "uploader_url": "u", "language": "en"}
        linha = "\t".join(campos[c] for c in M._FACTS_CAMPOS)

        # As seis rodadas da medição: o 429 corta em pontos diferentes, então
        # cada rodada deixa em disco um conjunto diferente de variantes.
        rodadas = (["en"], ["pt-BR"], ["en", "pt-BR"], ["pt-BR", "en"],
                   ["pt-BR", "pt", "en"], ["en", "pt"])
        escolhas = []
        for chegaram in rodadas:
            M._FACTS.clear()
            pasta = _temp(self)
            stem = "source-abc123"

            def _run(args, timeout, label, _c=chegaram, _p=pasta):
                if "metadata" in label:
                    return linha + "\n"
                for tag in _c:
                    with open(os.path.join(_p, f"{stem}.{tag}.srt"), "w") as fh:
                        fh.write("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n")
                return ""
            _troca(self, "run", _run)
            caminho, _porque = M._pull_subs(
                url, pasta, stem, os.path.join(pasta, stem + ".%(ext)s"),
                ["--no-playlist"])
            escolhas.append(M._tag_do_nome(caminho))

        # Onde o inglês chegou, o inglês venceu -- sem exceção.
        for chegaram, escolhida in zip(rodadas, escolhas):
            if "en" in chegaram:
                self.assertEqual(escolhida, "en", f"chegaram={chegaram}")
            else:
                # Só a tradução chegou: ela serve, e a linha de aviso diz que é
                # tradução. Melhor uma legenda marcada do que transcrever.
                self.assertEqual(escolhida, "pt-br", f"chegaram={chegaram}")

    def test_O_CAMINHO_REAL_com_vtt_nao_convertido_escolhe_EN(self):
        """O teste que teria pego o defeito do Rick Astley, e ele olha o DISCO.

        Medido em 15/09/2026 na imagem construída: a pasta de um `lote prep`
        real tinha `source-....en.vtt`, `source-....pt-BR.vtt` e um `.webm` --
        o áudio, que não devia ter descido. `--convert-subs srt` converte só no
        FIM da execução do yt-dlp, então um 429 no meio deixa `.vtt` cru; e
        `_achadas()` só olhava `.srt`, via a pasta vazia, dizia "este vídeo não
        publica legenda" e ia transcrever o áudio com duas legendas ao lado.

        Depois `_subtitle_beside` achava os `.vtt` sem `.lingua` nenhum em disco
        e caía em `SUBTITLE_LANGS`, que começa em `pt-br`. Vídeo em inglês,
        legenda auto-traduzida, e a frase "that is the language of the video".

        Este teste roda o caminho de verdade, dubla só a rede, e depois LÊ A
        PASTA -- porque um teste que confia no retorno da função não teria visto
        nada disto.
        """
        _troca(self, "_host_is_public", lambda host: True)
        _env(self, "WARDEN_SUB_LANGS", None)
        M._FACTS.clear()
        self.addCleanup(M._FACTS.clear)
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        pasta = _temp(self)
        stem = "source-0424974c6853"
        campos = {"title": "Never Gonna Give You Up", "channel_id": "UC",
                  "uploader_id": "@rick", "channel_url": "u",
                  "uploader_url": "u", "language": "en"}
        linha = "\t".join(campos[c] for c in M._FACTS_CAMPOS)

        def _run(args, timeout, label):
            if "metadata" in label:
                return linha + "\n"
            # O yt-dlp escreve VTT e o 429 impede a conversão para SRT.
            for tag in ("en", "pt-BR"):
                with open(os.path.join(pasta, f"{stem}.{tag}.vtt"), "w") as fh:
                    fh.write("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n\n")
            erro = M.FonteBloqueada("429")
            erro.apenas_429 = True
            raise erro
        _troca(self, "run", _run)

        caminho, porque = M._pull_subs(
            url, pasta, stem, os.path.join(pasta, stem + ".%(ext)s"),
            ["--no-playlist"])

        # 1. A legenda foi ENCONTRADA, apesar de ser .vtt e do 429.
        self.assertIsNotNone(caminho, f"não achou a legenda: {porque}")
        # 2. E é a INGLESA, que é a língua do vídeo.
        self.assertEqual(M._tag_do_nome(caminho), "en")
        # 3. O sidecar existe EM DISCO. Olhar a pasta é o ponto do teste.
        em_disco = sorted(os.listdir(pasta))
        self.assertIn(stem + ".lingua", em_disco, em_disco)
        self.assertEqual(M._lingua_marcada(os.path.join(pasta, stem + ".webm")),
                         "en")

    def test_o_sidecar_e_escrito_MESMO_quando_a_legenda_nao_vem(self):
        """A língua do vídeo é um fato sobre o LINK, não sobre o download ter
        dado certo. Escrevê-la só no caminho de sucesso era o que deixava
        `_subtitle_beside` escolher no escuro justamente quando mais importava.
        """
        _troca(self, "_host_is_public", lambda host: True)
        _env(self, "WARDEN_SUB_LANGS", None)
        M._FACTS.clear()
        self.addCleanup(M._FACTS.clear)
        pasta = _temp(self)
        stem = "source-abc"
        campos = {"title": "T", "channel_id": "UC", "uploader_id": "@c",
                  "channel_url": "u", "uploader_url": "u", "language": "en"}
        linha = "\t".join(campos[c] for c in M._FACTS_CAMPOS)

        def _run(args, timeout, label):
            if "metadata" in label:
                return linha + "\n"
            return ""          # nenhuma legenda escrita
        _troca(self, "run", _run)

        caminho, porque = M._pull_subs(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", pasta, stem,
            os.path.join(pasta, stem + ".%(ext)s"), ["--no-playlist"])
        self.assertIsNone(caminho)
        self.assertIn("publishes no subtitle", porque)
        self.assertIn(stem + ".lingua", os.listdir(pasta))

    def test_sem_lingua_anotada_a_escolha_e_ANUNCIADA_como_desempate(self):
        """Cinto e suspensório: sidecar é arquivo, e arquivo some. Sem ele a
        escolha continua acontecendo -- parar deixaria o dono sem clipe --, mas
        ela NÃO passa por medição."""
        import io
        from contextlib import redirect_stderr
        self._legendas("en", "pt-BR")
        fonte = os.path.join(self.dir, "source-abc123.mp4")
        with open(fonte, "wb") as fh:
            fh.write(b"v")
        buf = io.StringIO()
        with redirect_stderr(buf):
            _achado, _lang = M._subtitle_beside(fonte)
        aviso = " ".join(buf.getvalue().split())
        self.assertIn("did not read what language", aviso)
        self.assertIn("NOT by measurement", aviso)
        self.assertIn("Do not tell anyone this is the video's language", aviso)

    def test_com_a_lingua_anotada_NAO_ha_aviso_de_desempate(self):
        """A outra metade: quando a língua foi lida, a frase pode ser afirmada
        e o aviso não pode poluir a saída."""
        import io
        from contextlib import redirect_stderr
        self._legendas("en", "pt-BR")
        fonte = os.path.join(self.dir, "source-abc123.mp4")
        with open(fonte, "wb") as fh:
            fh.write(b"v")
        M._marca_lingua(self.dir, "source-abc123", "en")
        buf = io.StringIO()
        with redirect_stderr(buf):
            _achado, lang = M._subtitle_beside(fonte)
        self.assertEqual(lang, "en")
        self.assertEqual(buf.getvalue().strip(), "")

    def test_o_lang_explicito_do_dono_ainda_ganha_da_anotacao(self):
        """A anotação é um padrão melhor, não uma ordem. `--lang` é o dono
        falando, e ele decide."""
        self._legendas("en", "pt-BR")
        fonte = os.path.join(self.dir, "source-abc123.mp4")
        with open(fonte, "wb") as fh:
            fh.write(b"v")
        M._marca_lingua(self.dir, "source-abc123", "en")
        _achado, lang = M._subtitle_beside(fonte, prefer=["pt-BR"])
        self.assertEqual(lang, "pt-br")


class ASaidaNomeiaALinguaEAProcedencia(unittest.TestCase):
    """O modelo precisa saber em que língua escrever o hook ANTES de renderizar.

    Sem esta linha ele só descobria a mistura ao bater no `cut`, depois de todo
    o download, com o lote perdido. A linha informa; quem decide é o modelo, e
    depois dele o `cut` -- cuja recusa continua certa e intocada.
    """

    def setUp(self):
        self.dir = _temp(self)

    def _diz(self, tags, lingua_video):
        import io
        from contextlib import redirect_stderr
        caminhos = []
        for t in tags:
            nome = f"source-abc.{t}.srt" if t else "source-abc.srt"
            caminhos.append(os.path.join(self.dir, nome))
        buf = io.StringIO()
        with redirect_stderr(buf):
            M._conta_a_legenda(caminhos, lingua_video)
        return " ".join(buf.getvalue().split())

    def test_quando_bate_com_o_video_diz_que_e_a_original(self):
        linha = self._diz(["en"], "en")
        self.assertIn("caption language: en", linha)
        self.assertIn("the video's own language", linha)

    def test_quando_NAO_bate_diz_que_e_TRADUCAO_e_por_que_isso_importa(self):
        linha = self._diz(["pt-BR"], "en")
        self.assertIn("TRANSLATION", linha)
        self.assertIn("two layers of error", linha)
        self.assertIn("'en'", linha)

    def test_a_linha_manda_escrever_o_hook_na_mesma_lingua(self):
        linha = self._diz(["en", "pt-BR"], "en")
        self.assertIn("Write the hook in en", linha)
        self.assertIn("refuses to burn a hook and a caption in different", linha)

    def test_a_linha_nomeia_as_outras_que_estao_em_disco(self):
        linha = self._diz(["en", "pt-BR"], "en")
        self.assertIn("Also on disk: pt-br", linha)

    def test_sem_tag_nenhuma_ela_diz_que_o_idioma_e_desconhecido(self):
        linha = self._diz([None], "en")
        self.assertIn("no language tag", linha)
        self.assertIn("unknown", linha)

    def test_ela_NAO_fala_mais_em_preferir_portugues(self):
        """A regra antiga saiu do código; a mensagem não pode continuar
        ensinando ela ao modelo."""
        for linha in (self._diz(["en"], "en"), self._diz(["pt-BR"], "en"),
                      self._diz(["es"], "es")):
            self.assertNotIn("default hook language is pt", linha)
            self.assertNotIn("not Portuguese", linha)


class ADrivePastaBaixaSoOQueVaiSerUsado(unittest.TestCase):
    """671 MB em 87s para usar um arquivo.

    Medido em 15/09/2026 num briefing de verdade: a pasta trazia cinco cortes
    oficiais, os cinco desciam, e um era usado. Os outros 537 MB desceram para
    serem pesados e descartados.

    A regra de escolha NÃO mudou -- o maior vídeo. A ordem mudou: lista, escolhe,
    baixa. E há uma limitação medida atrás disso, que estes testes tornam
    explícita: a listagem do Drive não publica o TAMANHO dos arquivos, então
    quando há vários vídeos e a sonda de tamanho não responde, a pasta inteira
    desce como descia antes -- em voz alta, e não em silêncio.
    """

    URL = "https://drive.google.com/drive/folders/ABC123"

    def setUp(self):
        self.dir = _temp(self)
        _troca(self, "_host_is_public", lambda host: True)
        _troca(self, "_tem_gdown", lambda: True)
        _troca(self, "_gdown", lambda: ["gdown"])
        _troca(self, "_drive_bytes", lambda ident: None)
        self.chamadas = []

    def _gdown_de_mentira(self, escreve):
        """Um gdown que não baixa nada e anota o que teria baixado."""
        def _run(args, timeout, label):
            self.chamadas.append(list(args))
            escreve(list(args))
            return ""
        _troca(self, "run", _run)

    def test_um_video_so_na_pasta_desce_sozinho_e_a_pasta_nao(self):
        _troca(self, "_drive_lista_pasta",
               lambda url, destino: [("corte.mp4", "id-1")])

        def _escreve(args):
            self.assertNotIn("--folder", args, "baixou a pasta inteira")
            destino = args[args.index("-O") + 1]
            with open(os.path.join(destino, "corte.mp4"), "wb") as fh:
                fh.write(b"v")
        self._gdown_de_mentira(_escreve)

        caminho = M._download_one(self.URL, self.dir)
        self.assertTrue(os.path.isfile(caminho))
        self.assertEqual(len(self.chamadas), 1)
        self.assertIn("id-1", " ".join(self.chamadas[0]))

    def test_com_os_tamanhos_disponiveis_so_o_MAIOR_desce(self):
        """A mesma regra de sempre, por 3 requisições em vez de 671 MB."""
        _troca(self, "_drive_lista_pasta", lambda url, destino: [
            ("pequeno.mp4", "id-p"), ("gigante.mp4", "id-g"),
            ("medio.mp4", "id-m")])
        _troca(self, "_drive_bytes",
               lambda ident: {"id-p": 10, "id-g": 900, "id-m": 100}[ident])

        def _escreve(args):
            self.assertNotIn("--folder", args)
            destino = args[args.index("-O") + 1]
            with open(os.path.join(destino, "gigante.mp4"), "wb") as fh:
                fh.write(b"v")
        self._gdown_de_mentira(_escreve)

        caminho = M._download_one(self.URL, self.dir)
        self.assertTrue(os.path.isfile(caminho))
        self.assertEqual(len(self.chamadas), 1)
        self.assertIn("id-g", " ".join(self.chamadas[0]))

    def test_sem_tamanho_a_pasta_inteira_desce_como_descia(self):
        """A limitação medida: o parser de pasta do gdown lê id, nome e tipo do
        HTML -- não há campo de bytes. Adivinhar qual é o maior sem ter medido
        nenhum seria escolher o corte errado da campanha em silêncio, então o
        caminho caro continua existindo e é ANUNCIADO."""
        _troca(self, "_drive_lista_pasta", lambda url, destino: [
            ("a.mp4", "id-a"), ("b.mp4", "id-b")])

        def _escreve(args):
            destino = args[args.index("-O") + 1]
            os.makedirs(destino, exist_ok=True)
            for nome, tamanho in (("a.mp4", 10), ("b.mp4", 500)):
                with open(os.path.join(destino, nome), "wb") as fh:
                    fh.write(b"x" * tamanho)
        self._gdown_de_mentira(_escreve)

        caminho = M._download_one(self.URL, self.dir)
        self.assertIn("--folder", self.chamadas[-1])
        self.assertEqual(os.path.basename(caminho), "b.mp4")   # o maior

    def test_a_pasta_sem_video_nenhum_diz_o_que_conferir(self):
        _troca(self, "_drive_lista_pasta",
               lambda url, destino: [("briefing.pdf", "id-1")])
        self._gdown_de_mentira(lambda args: None)
        with self.assertRaises(RuntimeError) as erro:
            M._download_one(self.URL, self.dir)
        self.assertIn("no video in it", str(erro.exception))
        self.assertEqual(self.chamadas, [], "baixou algo de uma pasta sem vídeo")

    def test_a_listagem_que_falha_cai_no_caminho_de_sempre(self):
        """Sem gdown importável, ou com o Drive mudando o HTML da pasta, a
        listagem devolve None -- e a pasta inteira continua sendo um caminho que
        funciona. Uma otimização que quebra o caso base não é otimização."""
        _troca(self, "_drive_lista_pasta", lambda url, destino: None)

        def _escreve(args):
            destino = args[args.index("-O") + 1]
            os.makedirs(destino, exist_ok=True)
            with open(os.path.join(destino, "corte.mp4"), "wb") as fh:
                fh.write(b"v")
        self._gdown_de_mentira(_escreve)

        caminho = M._download_one(self.URL, self.dir)
        self.assertTrue(os.path.isfile(caminho))
        self.assertIn("--folder", self.chamadas[-1])

    def test_o_nome_que_desce_da_pasta_e_normalizado_na_hora(self):
        """O Drive é justamente de onde veio o "Refrão" em NFD. O caminho
        devolvido tem de ser um caminho que existe."""
        nfd = unicodedata.normalize("NFD", "Refrão") + ".mp4"
        _troca(self, "_drive_lista_pasta", lambda url, destino: [(nfd, "id-1")])

        def _escreve(args):
            destino = args[args.index("-O") + 1]
            with open(os.path.join(destino, nfd), "wb") as fh:
                fh.write(b"v")
        self._gdown_de_mentira(_escreve)

        caminho = M._download_one(self.URL, self.dir)
        self.assertTrue(os.path.isfile(caminho), caminho)
        caminho.encode("ascii")     # digitável


if __name__ == "__main__":
    unittest.main()
