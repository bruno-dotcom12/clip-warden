"""Quando a fonte recusa, o que o agente diz -- e quanto ele pergunta para dizer.

Esta suíte existe por causa de duas conversas reais: em dois dias diferentes o
agente contou ao dono duas histórias contraditórias e ambas inventadas ("o
YouTube está bloqueando este vídeo específico", depois "o IP deste servidor foi
banido"), porque o que chegava até ele era o rabo em inglês do yt-dlp e ele
improvisava uma causa em cima. O conserto tem duas metades e as duas são
testadas aqui:

  1. a recusa é reconhecida num lugar só (`run`) e traduzida numa mensagem que
     diz o que é verdade e PROÍBE as três invenções;
  2. o link passou a custar UMA extração em vez de quatro, porque rajada é
     exatamente o que faz um endereço ser marcado -- `_facts` é essa metade, e
     um teste que só conta chamadas é o que impede alguém de reintroduzir a
     segunda extração sem perceber.

Nada aqui toca a rede: o subprocesso é um python que falha de propósito com o
texto real que o yt-dlp imprime, DNS é curto-circuitado, e a sonda do provider
de PO token aponta para uma porta de loopback comprovadamente fechada.
"""
import os
import socket
import subprocess
import sys
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

import warden_media


def _temp(caso, prefix="warden-bloqueio-"):
    """Um diretório temporário que se APAGA quando o teste acaba.

    Mesma regra do resto da suíte: nada consome em silêncio.
    """
    caminho = tempfile.mkdtemp(prefix=prefix)
    caso.addCleanup(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


def _troca(caso, nome, valor):
    """Troca um atributo do módulo e garante a devolução no fim do teste."""
    antigo = getattr(warden_media, nome)
    setattr(warden_media, nome, valor)
    caso.addCleanup(setattr, warden_media, nome, antigo)
    return antigo


def _porta_fechada():
    """Uma porta de loopback que ninguém está ouvindo, obtida sem chutar número.

    Abre, lê o número que o sistema deu, fecha. O que sobra é um endereço que
    recusa conexão na hora -- determinístico e sem rede.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


# O texto que o yt-dlp realmente imprime quando a sessão deslogada é recusada.
# Copiado da saída, não parafraseado: o valor do teste está em ser esta frase.
SAIDA_BOT = (
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication. "
    "See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#exe-. "
    "for how to manually pass cookies.")

SAIDA_COMUM = (
    "ERROR: [youtube] abc123: Video unavailable. This video has been removed "
    "by the uploader")

# As recusas que são sobre ESTE vídeo e sobre mais nenhum. Abrem com as mesmas
# quatro palavras do bot check ("Sign in to confirm...") e foi por isso que a
# primeira versão do conserto as confundiu.
SAIDA_IDADE = (
    "ERROR: [youtube] BaW_jenozKc: Sign in to confirm your age. "
    "This video may be inappropriate for some users.")

SAIDA_PRIVADO = (
    "ERROR: [youtube] BaW_jenozKc: Private video. Sign in if you've been "
    "granted access to this video")

SAIDA_MEMBROS = (
    "ERROR: [youtube] BaW_jenozKc: Join this channel to get access to "
    "members-only content like this video, and other exclusive perks.")

# O outro jeito de o endereço ser recusado: não uma frase, um código.
SAIDA_429 = (
    "ERROR: Unable to download API page: HTTP Error 429: Too Many Requests "
    "(caused by <HTTPError 429: Too Many Requests>)")


def _falha_com(texto):
    """Um comando REAL que falha imprimindo `texto` no stderr.

    Um subprocesso de verdade em vez de um mock de `subprocess.run`, porque o
    que está sendo testado é justamente o caminho de saída de processo: código
    de retorno, stderr, e a leitura que `run` faz dos dois.
    """
    return [sys.executable, "-c",
            "import sys; sys.stderr.write(%r); sys.exit(1)" % texto]


class MensagemDaRecusa(unittest.TestCase):
    """A recusa do bot check vira uma frase que o dono pode conferir.

    O modo de falha desta função nunca foi o download -- foi o agente inventar
    uma causa. Por isso os testes olham o CONTEÚDO da mensagem, não só o tipo
    da exceção.
    """

    def setUp(self):
        # O estado do provider não pode mudar o veredito dos testes: numa
        # máquina com o bgutil rodando, `_pot_alive` diria "reachable" e numa
        # sem ele diria o contrário. Fixado.
        _troca(self, "_pot_alive", lambda: False)

    def test_a_recusa_vira_a_mensagem_honesta_e_nao_o_rabo_do_ytdlp(self):
        """O inglês do yt-dlp é exatamente o material de que o agente improvisou
        as duas causas falsas. Ele não pode chegar ao modelo: `run` o substitui
        pela leitura honesta, e "Use --cookies-from-browser" -- a sugestão que
        virou "peça as credenciais ao dono" -- não sobrevive."""
        with self.assertRaises(warden_media.FonteBloqueada) as caso:
            warden_media.run(_falha_com(SAIDA_BOT), 30, "yt-dlp metadata lookup")
        msg = str(caso.exception)
        self.assertIn("refused this from this machine's outgoing address", msg)
        self.assertIn("yt-dlp metadata lookup", msg)          # qual chamada foi
        self.assertNotIn("--cookies-from-browser", msg)
        self.assertNotIn("Sign in to confirm", msg)
        self.assertNotIn("Video unavailable", msg)

    def test_a_mensagem_proibe_as_tres_invencoes(self):
        """As três frases que o agente disse ao dono e que eram mentira: que era
        aquele vídeo, que era autorização da fonte, e que passaria sozinho. A
        mensagem tem de negar as três por escrito -- é o único lugar onde a
        proibição existe, porque o modelo lê isto e nada mais."""
        msg = warden_media._porque_bloqueou("yt-dlp metadata lookup")
        self.assertIn("not this video", msg)
        self.assertIn("not the source's authorisation", msg)
        self.assertIn("not something that clears up on its own", msg)
        # E o que ELE pode fazer, para a mensagem não ser só uma negativa.
        self.assertIn("different outgoing address", msg)
        self.assertIn(warden_media.COOKIES_FILE, msg)

    def test_um_erro_comum_continua_entregando_o_rabo_do_erro(self):
        """A detecção não pode engolir erro normal. Um vídeo removido é um fato
        sobre o link e o dono precisa lê-lo tal como veio -- se o bot check
        passasse a capturar tudo, toda falha viraria "o endereço foi recusado",
        que é a mesma invenção com o sinal trocado."""
        with self.assertRaises(RuntimeError) as caso:
            warden_media.run(_falha_com(SAIDA_COMUM), 30, "yt-dlp metadata lookup")
        msg = str(caso.exception)
        self.assertIn("Video unavailable", msg)
        self.assertIn("yt-dlp metadata lookup failed", msg)
        self.assertNotIn("outgoing address", msg)

    def test_o_reconhecimento_e_estreito_e_so_pega_o_que_e_do_endereco(self):
        """`_BOT_CHECK` reconhece o que TODO link recebe desta máquina, e só.

        A versão anterior casava `sign in to confirm` solto -- e "Sign in to
        confirm your age" abre com as mesmas quatro palavras. Um vídeo com
        restrição de idade virava a mensagem de endereço, que afirma por escrito
        "not this video: every link gets the same refusal". Era falso exatamente
        no caso em que a causa ERA aquele vídeo. Por isso a lista é curta."""
        for texto in (SAIDA_BOT, SAIDA_429,
                      "ERROR: Unable to download: HTTP Error 429"):
            self.assertTrue(warden_media._BOT_CHECK.search(texto), texto)
        for texto in (SAIDA_IDADE, SAIDA_PRIVADO, SAIDA_MEMBROS, SAIDA_COMUM,
                      "ERROR: login_required: this video requires login",
                      "HTTP Error 404: Not Found", "Unable to download webpage"):
            self.assertIsNone(warden_media._BOT_CHECK.search(texto), texto)

    def test_o_que_e_sobre_este_video_esta_nomeado_a_parte(self):
        """`_ESTE_VIDEO` é a afirmação OPOSTA, e existe escrita para que o
        reconhecimento do endereço não possa reconquistá-la por descuido."""
        for texto in (SAIDA_IDADE, SAIDA_PRIVADO, SAIDA_MEMBROS, SAIDA_COMUM):
            self.assertTrue(warden_media._ESTE_VIDEO.search(texto), texto)
        for texto in (SAIDA_BOT, SAIDA_429):
            self.assertIsNone(warden_media._ESTE_VIDEO.search(texto), texto)

    def test_um_429_e_o_endereco_mesmo_sem_frase_nenhuma(self):
        """O endereço nem sempre é recusado com uma frase; às vezes é só o
        código. 429 é "você está pedindo demais, daqui" -- a mesma coisa que o
        bot check diz, e a mesma resposta honesta."""
        with self.assertRaises(warden_media.FonteBloqueada) as caso:
            warden_media.run(_falha_com(SAIDA_429), 30, "yt-dlp metadata lookup")
        self.assertIn("outgoing address", str(caso.exception))

    def test_a_bloqueada_ainda_e_um_RuntimeError(self):
        """Nove chamadas neste arquivo já capturavam RuntimeError. Se
        `FonteBloqueada` saísse dessa árvore, o conserto viraria traceback na
        cara do dono em todos os nove lugares."""
        self.assertTrue(issubclass(warden_media.FonteBloqueada, RuntimeError))


class OPortaoSoValeParaOYtdlp(unittest.TestCase):
    """`run` carrega três ferramentas, e a mensagem só fala de uma.

    O mesmo 429 sai do Google Drive pelo gdown e de um HTTP do ffmpeg. Responder
    a ele com um parágrafo sobre o YouTube ter recusado este endereço é o crime
    exato que este conserto existe para impedir -- uma causa afirmada com
    confiança e nunca observada -- cometido pelo código que deveria impedi-lo.
    Por isso a detecção é presa ao label.
    """

    def setUp(self):
        _troca(self, "_pot_alive", lambda: False)

    def test_um_429_do_gdown_nao_vira_recusa_do_youtube(self):
        """O acervo de uma campanha pode estar no Drive. Um 429 de lá é do
        Drive, e o dono precisa ler isso, não uma explicação sobre VPN e
        cookies do YouTube."""
        with self.assertRaises(RuntimeError) as caso:
            warden_media.run(_falha_com(SAIDA_429), 30, "gdown")
        exc = caso.exception
        self.assertNotIsInstance(exc, warden_media.FonteBloqueada)
        msg = str(exc)
        self.assertIn("Too Many Requests", msg)     # o rabo do erro, inteiro
        self.assertIn("gdown failed", msg)
        self.assertNotIn("outgoing address", msg)
        self.assertNotIn("cookies file", msg)

    def test_nem_um_429_do_ffmpeg(self):
        with self.assertRaises(RuntimeError) as caso:
            warden_media.run(_falha_com(SAIDA_429), 30,
                             "ffmpeg (window for transcription)")
        self.assertNotIsInstance(caso.exception, warden_media.FonteBloqueada)
        self.assertNotIn("outgoing address", str(caso.exception))

    def test_todo_label_de_ytdlp_do_arquivo_continua_coberto(self):
        """O portão é preso a um prefixo, então o prefixo é o contrato. Estes
        são os labels que o arquivo realmente usa: se alguém acrescentar uma
        chamada de yt-dlp com label fora do padrão, ela sai do portão em
        silêncio -- e o silêncio é como o bug original chegou ao dono."""
        for label in ("yt-dlp", "yt-dlp (audio)", "yt-dlp (window)",
                      "yt-dlp (windows)", "yt-dlp metadata lookup",
                      "yt-dlp playlist listing"):
            with self.assertRaises(warden_media.FonteBloqueada, msg=label):
                warden_media.run(_falha_com(SAIDA_BOT), 30, label)


class RecusaQueEDesteVideo(unittest.TestCase):
    """Idade, privado, só-para-membros, removido: aqui a causa É aquele link.

    Estes quatro são o achado que motivou o conserto. A mensagem de endereço
    diria, em letras garrafais, que não é este vídeo -- e seria mentira. O
    contrato é que eles voltam a ser erro comum, com o texto do yt-dlp inteiro,
    que é o que deixa o dono ver qual dos quatro é.
    """

    def setUp(self):
        _troca(self, "_pot_alive", lambda: False)

    def _erro_comum(self, saida):
        with self.assertRaises(RuntimeError) as caso:
            warden_media.run(_falha_com(saida), 30, "yt-dlp metadata lookup")
        exc = caso.exception
        self.assertNotIsInstance(exc, warden_media.FonteBloqueada)
        msg = str(exc)
        self.assertNotIn("outgoing address", msg)
        self.assertNotIn("not this video", msg)
        return msg

    def test_restricao_de_idade_nao_vira_mensagem_de_endereco(self):
        """O caso exato do achado: abre com "Sign in to confirm" e não tem nada
        a ver com o endereço desta máquina."""
        self.assertIn("confirm your age", self._erro_comum(SAIDA_IDADE))

    def test_video_privado_nao_vira_mensagem_de_endereco(self):
        self.assertIn("Private video", self._erro_comum(SAIDA_PRIVADO))

    def test_so_para_membros_nao_vira_mensagem_de_endereco(self):
        self.assertIn("members-only", self._erro_comum(SAIDA_MEMBROS))

    def test_a_decisao_e_por_LINHA_e_nao_pelo_texto_todo(self):
        """Duas linhas, dois vídeos, duas causas -- e a resposta é sobre o
        endereço.

        Esta é a correção de 15/09/2026, e o teste anterior travava o defeito.
        Ele juntava um 429 e um "Private video" e exigia que o vídeo ganhasse,
        raciocinando que afirmar de menos não inventa nada. Mas a saída de uma
        PLAYLIST é uma linha por item: o item 1 pode ser privado e o item 2
        bater no bot check, e não há relação entre os dois. Decidindo pelo texto
        inteiro, a mensagem honesta sumia exatamente ali -- e o que chegava ao
        modelo era a pilha de inglês que este arquivo existe para não entregar.

        A pergunta certa é se ALGUMA linha é bloqueio e não é sobre o vídeo.
        """
        playlist = SAIDA_PRIVADO + "\n" + SAIDA_429
        with self.assertRaises(warden_media.FonteBloqueada) as caso:
            warden_media.run(_falha_com(playlist), 30, "yt-dlp playlist listing")
        self.assertIn("outgoing address", str(caso.exception))

    def test_na_MESMA_linha_o_video_continua_ganhando(self):
        """E a guarda original continua de pé onde ela nasceu: um vídeo com
        restrição de idade abre com as mesmas quatro palavras do bot check, na
        mesma linha, e ali a causa É aquele vídeo."""
        uma_linha = "ERROR: [youtube] x: Sign in to confirm your age. Not a bot check."
        self.assertIn("confirm your age", self._erro_comum(uma_linha))


class ComoOYtdlpEChamado(unittest.TestCase):
    """As opções que toda chamada carrega -- ritmo, teto de retry, runtime de JS,
    provider de PO token -- porque cada uma delas foi medida depois de um
    bloqueio, não escolhida por gosto."""

    def setUp(self):
        # Sem isto o resultado depende de haver deno/node instalado na máquina
        # de quem roda a suíte.
        _troca(self, "have", lambda b: b in ("deno", "node"))

    def test_o_ritmo_e_o_teto_de_retries_vao_em_toda_chamada(self):
        """Dez retries (o padrão do yt-dlp) contra um endereço que está sendo
        recusado é exatamente a rajada que o wiki do yt-dlp aponta como o que
        marca um endereço. O teto e os sleeps são o conserto, então eles têm de
        estar presentes por padrão -- não atrás de uma flag que alguém esquece."""
        args = warden_media._ytdlp()
        self.assertIn("--sleep-requests", args)
        self.assertIn("--sleep-interval", args)
        self.assertIn("--max-sleep-interval", args)
        self.assertEqual(args[args.index("--retries") + 1], "3")
        self.assertEqual(args[args.index("--extractor-retries") + 1], "2")
        # Os valores do ritmo são os do módulo, não constantes duplicadas aqui.
        self.assertEqual(args[args.index("--sleep-requests") + 1],
                         warden_media.SLEEP_REQUESTS)
        self.assertEqual(args[args.index("--max-sleep-interval") + 1],
                         warden_media.SLEEP_MAX)

    def test_sem_ritmo_ainda_sobra_o_teto_de_retries(self):
        """`paced=False` existe para a chamada que não pode esperar (listar uma
        playlist antes de responder ao dono). Ela abre mão do sleep, e SÓ dele:
        o teto de retries é o que impede a pressa de virar rajada."""
        args = warden_media._ytdlp(paced=False)
        for opcao in ("--sleep-requests", "--sleep-interval", "--max-sleep-interval"):
            self.assertNotIn(opcao, args)
        self.assertIn("--retries", args)
        self.assertIn("--extractor-retries", args)

    def test_o_runtime_de_js_e_declarado_quando_existe(self):
        """`yt-dlp -v` imprimia `JS runtimes: none` com o node 26.5.1 instalado,
        porque o yt-dlp só habilita o deno sozinho -- e sem runtime ele derruba
        o cliente web em silêncio, o que é um handicap invisível em TODO link.
        Declarar o que existe é o conserto; a ordem é a preferência."""
        args = warden_media._ytdlp()
        self.assertEqual(args[args.index("--js-runtimes") + 1], "deno,node")

    def test_sem_nenhum_engine_a_opcao_nao_e_inventada(self):
        """Numa máquina sem engine nenhum, `--js-runtimes` vazio seria um
        argumento inválido e derrubaria toda chamada. Ausente é o certo."""
        _troca(self, "have", lambda b: False)
        self.assertIsNone(warden_media._js_runtimes())
        self.assertNotIn("--js-runtimes", warden_media._ytdlp())

    def test_a_chamada_aponta_para_o_provider_configurado(self):
        """O endereço vem da constante do módulo, então quem muda o env muda a
        chamada -- e é assim que o compose aponta o agente para o sidecar."""
        args = warden_media._ytdlp()
        valor = args[args.index("--extractor-args") + 1]
        self.assertEqual(
            valor,
            "youtubepot-bgutilhttp:base_url=" + warden_media.POT_BASE_URL)

    def test_o_padrao_sem_env_e_o_loopback(self):
        """O bgutil 2.0.0 existe porque escutar em 0.0.0.0 era execução remota de
        código (GHSA-qpv9-8xfj-xx9m), e este agente é publicado.

        A versão anterior deste teste afirmava que `POT_BASE_URL` É o loopback,
        e passava no Mac por acidente: lá a variável não existe. DENTRO do
        container ela vale `http://pot:4416`, que é o desenho -- o provider é um
        container irmão -- e o teste reprovava o próprio conserto. O que é
        invariante é o PADRÃO, não o valor, então é o padrão que se trava aqui.
        """
        import importlib
        antes = os.environ.pop("WARDEN_POT_URL", None)
        try:
            recarregado = importlib.reload(warden_media)
            self.assertTrue(
                recarregado.POT_BASE_URL.startswith("http://127.0.0.1"),
                recarregado.POT_BASE_URL)
        finally:
            if antes is not None:
                os.environ["WARDEN_POT_URL"] = antes
            importlib.reload(warden_media)

    def test_o_env_sobrepoe_o_padrao(self):
        """E a sobreposição é o mecanismo, não um acaso: sem ela o compose não
        teria como apontar o agente para o sidecar."""
        import importlib
        antes = os.environ.get("WARDEN_POT_URL")
        os.environ["WARDEN_POT_URL"] = "http://pot:4416"
        try:
            recarregado = importlib.reload(warden_media)
            self.assertEqual(recarregado.POT_BASE_URL, "http://pot:4416")
        finally:
            if antes is None:
                os.environ.pop("WARDEN_POT_URL", None)
            else:
                os.environ["WARDEN_POT_URL"] = antes
            importlib.reload(warden_media)


class ArquivoDeCookies(unittest.TestCase):
    """O cookies file é a única escotilha de saída, e ela nunca é do dono por
    padrão: quem instala o agente deixa o próprio arquivo ali se precisar."""

    def setUp(self):
        _troca(self, "have", lambda b: False)
        self.dir = _temp(self)

    def test_cookies_entra_quando_o_arquivo_existe(self):
        caminho = os.path.join(self.dir, "cookies.txt")
        with open(caminho, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
        _troca(self, "COOKIES_FILE", caminho)
        args = warden_media._ytdlp()
        self.assertEqual(args[args.index("--cookies") + 1], caminho)

    def test_sem_arquivo_a_opcao_nao_e_passada(self):
        """`--cookies` apontando para um arquivo que não existe faz o yt-dlp
        abortar antes de tentar o link -- a instalação sem cookies (que é a
        padrão, e a de todo mundo) pararia de funcionar por completo."""
        _troca(self, "COOKIES_FILE", os.path.join(self.dir, "nao-existe.txt"))
        self.assertNotIn("--cookies", warden_media._ytdlp())


class UmaExtracaoPorLink(unittest.TestCase):
    """Um link custava QUATRO extrações deslogadas disparadas em sequência --
    canal, título, legenda, download. As duas primeiras pedem campos da mesma
    resposta, e virar uma só é metade do conserto do bloqueio. Estes testes
    contam chamadas, porque é a única forma de a segunda extração não voltar
    despercebida."""

    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    LINHA = "Um título\tUCabc123\t@canal\thttps://youtube.com/channel/UCabc123\thttps://youtube.com/@canal"

    def setUp(self):
        # O cache é global do módulo: sem limpar, um teste responde pelo outro.
        warden_media._FACTS.clear()
        self.addCleanup(warden_media._FACTS.clear)
        # `safe_url` resolve o host de verdade; aqui o DNS não é o assunto e a
        # suíte não pode depender de rede.
        _troca(self, "_host_is_public", lambda host: True)
        _troca(self, "_pot_alive", lambda: False)
        self.chamadas = []

    def _responde(self, saida=None, erro=None):
        def fake_run(args, timeout, label):
            self.chamadas.append(args)
            if erro is not None:
                raise erro
            return saida
        _troca(self, "run", fake_run)

    def test_titulo_e_canal_do_mesmo_link_custam_uma_chamada_so(self):
        """`video_title` e `_channel_facts` são chamados juntos em todo link
        confiável. Duas extrações para dois campos da mesma resposta era metade
        da rajada; uma chamada é o contrato."""
        self._responde(saida=self.LINHA + "\n")
        titulo = warden_media.video_title(self.URL)
        canal = warden_media._channel_facts(self.URL)
        self.assertEqual(len(self.chamadas), 1)
        self.assertEqual(titulo, "Um título")
        self.assertIn("ucabc123", canal)
        self.assertIn("@canal", canal)

    def test_a_extracao_pede_um_item_so_e_nao_a_playlist_inteira(self):
        """Um link com `list=` puxaria a playlist toda numa consulta que só
        queria o título: uma rajada de centenas de requisições por um campo."""
        self._responde(saida=self.LINHA + "\n")
        warden_media.video_title(self.URL)
        args = self.chamadas[0]
        self.assertIn("--no-playlist", args)
        self.assertEqual(args[args.index("--playlist-items") + 1], "1")
        self.assertEqual(args[-1], self.URL)
        self.assertEqual(args[-2], "--")        # o link nunca vira opção

    def test_a_falha_nao_e_memorizada(self):
        """Uma recusa é um estado da REDE, não um fato sobre o link. Memorizada,
        ela sobreviveria ao motivo -- o dono troca de rede, o link volta a
        funcionar, e o agente continuaria repetindo o mesmo não pelo resto do
        comando."""
        self._responde(erro=RuntimeError("yt-dlp metadata lookup: refused"))
        self.assertEqual(warden_media._facts(self.URL), {})
        self.assertEqual(len(self.chamadas), 1)
        self.assertNotIn(self.URL, warden_media._FACTS)

        self._responde(saida=self.LINHA + "\n")
        self.assertEqual(warden_media._facts(self.URL)["title"], "Um título")
        self.assertEqual(len(self.chamadas), 2)

    def test_o_sucesso_e_memorizado(self):
        """A outra metade da mesma regra: o que a extração respondeu vale pelo
        resto do comando, senão o `--print` volta a ser disparado a cada campo
        que alguém pedir."""
        self._responde(saida=self.LINHA + "\n")
        warden_media._facts(self.URL)
        warden_media._facts(self.URL)
        warden_media.video_title(self.URL)
        self.assertEqual(len(self.chamadas), 1)

    def test_canal_sem_resposta_levanta_em_vez_de_devolver_lista_vazia(self):
        """Lista vazia leria como "não é de um canal confiável", que é um
        veredito inventado sobre a fonte a partir de uma falha de consulta -- e
        o dono ouviria que o canal DELE não é vouched for. A consulta é o que
        DECIDE a confiança, então quando ela não responde o tribunal não julga:
        levanta, e diz que não soube."""
        self._responde(erro=RuntimeError("yt-dlp metadata lookup failed:\n  boom"))
        with self.assertRaises(RuntimeError) as caso:
            warden_media._channel_facts(self.URL)
        msg = str(caso.exception)
        self.assertIn("unknown is not the same as no", msg)
        self.assertIn("do not rule the link out", msg)

    def test_o_canal_sem_resposta_nao_afirma_recusa_de_endereco(self):
        """O bug com o sinal trocado, e a razão desta suíte existir: `_facts`
        achatava QUALQUER falha em {} e `_channel_facts` anunciava que o
        endereço fora recusado -- para vídeo apagado, timeout, DNS fora do ar.
        Uma ferramenta que afirma uma causa que não observou é o defeito de que
        este arquivo inteiro trata. Se fosse endereço, `run` teria levantado
        FonteBloqueada e esta linha nem seria alcançada."""
        self._responde(erro=RuntimeError("yt-dlp metadata lookup failed:\n  boom"))
        with self.assertRaises(RuntimeError) as caso:
            warden_media._channel_facts(self.URL)
        msg = str(caso.exception)
        self.assertNotIn("outgoing address", msg)
        self.assertNotIn("VPN", msg)
        self.assertNotIn("cookies", msg)
        self.assertNotIsInstance(caso.exception, warden_media.FonteBloqueada)

    def test_bloqueio_de_endereco_atravessa_facts_em_vez_de_virar_dicionario_vazio(self):
        """`except Exception: return {}` engolia o bloqueio junto com o resto, e
        quem chamasse depois só via "sem resultado" -- que é onde a causa
        inventada nascia. A bloqueada sobe inteira, com a mensagem honesta."""
        bloqueio = warden_media.FonteBloqueada(
            warden_media._porque_bloqueou("yt-dlp metadata lookup"))
        self._responde(erro=bloqueio)
        with self.assertRaises(warden_media.FonteBloqueada) as caso:
            warden_media._facts(self.URL)
        self.assertIn("outgoing address", str(caso.exception))
        self.assertNotIn(self.URL, warden_media._FACTS)

    def test_o_canal_deixa_o_bloqueio_subir_mas_o_titulo_nao(self):
        """Os dois leitores de `_facts`, e eles divergem DE PROPÓSITO.

        A versão anterior deste teste travava `video_title` levantando também, e
        isso estava errado: `authorize` chama `video_title` na primeira linha,
        então a recusa escapava para `cmd_authorize`, que sai 1 -- e sair 1 é o
        sinal documentado de "NÃO está no acervo da campanha". Uma recusa de
        rede virava veredito sobre o acervo, e a persona então pede ao dono
        permissão para cortar fora dele. A mesma causa inventada, uma camada
        acima.

        Quem decide autorização é a lista local de ids; o título é como o agente
        NOMEIA o vídeo. Então um título ilegível custa um nome, não um veredito
        -- e a recusa é relatada onde é inequívoca, no download.

        `_channel_facts` é o oposto e continua levantando: ali a alternativa
        seria devolver lista vazia, que casaria com nenhuma entrada confiável e
        diria ao dono que o canal dele não é de confiança.
        """
        self._responde(erro=warden_media.FonteBloqueada(
            warden_media._porque_bloqueou("yt-dlp metadata lookup")))
        # O título: sem nome, sem exceção, sem veredito.
        self.assertIsNone(warden_media.video_title(self.URL))
        # O canal: sobe inteira, e chega como bloqueio de endereço.
        with self.assertRaises(warden_media.FonteBloqueada) as caso:
            warden_media._channel_facts(self.URL)
        self.assertIn("outgoing address", str(caso.exception))

    def test_trusted_check_nao_converte_bloqueio_em_nao_confiavel(self):
        """O portão que a auditoria pegou: `trusted_check` capturava tudo e
        devolvia `ok=False`, então `cmd_trusted` imprimia "NOT trusted" e saía 1.
        O agente dizia ao dono que o canal DELE não é de confiança -- e oferecia
        `warden trusted add` para um canal já na lista -- por causa de uma
        recusa de rede."""
        self._responde(erro=warden_media.FonteBloqueada(
            warden_media._porque_bloqueou("yt-dlp metadata lookup")))
        with self.assertRaises(warden_media.FonteBloqueada):
            warden_media.trusted_check(self.URL, ["@umcanalqualquer"])

    def test_uma_falha_comum_no_canal_continua_sendo_nao_sei(self):
        """E a outra metade continua valendo: falha COMUM não vira bloqueio, e
        também não vira "não confiável" -- vira "não consegui checar"."""
        self._responde(erro=RuntimeError("yt-dlp metadata lookup failed:\n  gone"))
        ok, porque = warden_media.trusted_check(self.URL, ["@umcanalqualquer"])
        self.assertFalse(ok)
        self.assertIn("could not read", porque)
        self.assertNotIn("outgoing address", porque)

    def test_uma_falha_comum_continua_sendo_dicionario_vazio(self):
        """A outra metade: só a bloqueada sobe. Um vídeo apagado continua sendo
        "sem fatos", que é o que os chamadores já sabem tratar."""
        self._responde(erro=RuntimeError("yt-dlp metadata lookup failed:\n  gone"))
        self.assertEqual(warden_media._facts(self.URL), {})
        self.assertIsNone(warden_media.video_title(self.URL))

    def test_campos_ausentes_viram_NA_e_nao_entram_como_canal(self):
        """O yt-dlp imprime `NA` para campo que não existe. `NA` entrando na
        lista casaria com uma entrada `NA` de confiança e, pior, viraria título
        do vídeo na pergunta que o agente faz ao dono."""
        self._responde(saida="NA\tUCabc123\tNA\tNA\tNA\n")
        self.assertIsNone(warden_media.video_title(self.URL))
        self.assertEqual(warden_media._channel_facts(self.URL), ["ucabc123"])


class SondaDoProvider(unittest.TestCase):
    """A sonda do PO token entra na mensagem de bloqueio. Se ela levantasse, a
    explicação do bloqueio morreria dentro de si mesma e o dono receberia um
    traceback em vez da leitura honesta."""

    def setUp(self):
        warden_media._FACTS.clear()
        self.addCleanup(warden_media._FACTS.clear)

    def test_sem_provider_ouvindo_a_resposta_e_False_e_nao_excecao(self):
        _troca(self, "POT_BASE_URL", "http://127.0.0.1:%d" % _porta_fechada())
        self.assertIs(warden_media._pot_alive(), False)

    def test_um_endereco_sem_sentido_tambem_e_so_um_nao(self):
        """Um WARDEN_POT_URL digitado errado não pode derrubar a explicação do
        bloqueio -- é justamente quando ela é mais necessária."""
        _troca(self, "POT_BASE_URL", "nao-e-uma-url")
        self.assertIs(warden_media._pot_alive(), False)

    def test_a_mensagem_de_bloqueio_sobrevive_ao_provider_fora_do_ar(self):
        _troca(self, "POT_BASE_URL", "http://127.0.0.1:%d" % _porta_fechada())
        msg = warden_media._porque_bloqueou("yt-dlp metadata lookup")
        self.assertIn("PO token provider: NOT reachable", msg)


if __name__ == "__main__":
    unittest.main()
