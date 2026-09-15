# Relatório das fases — 15/09/2026

Uma seção por fase. Cada uma diz o que mudou, o número que prova, e a auditoria
feita depois de fechar. A linha de base está em `MEDICAO-FASE-0.md`.

---

## FASE 1 — Velocidade

**Alvo: primeiro clipe em menos de 2 minutos, lote de 2 em menos de 4.**

### O que estava travando

Não era desobediência do agente. Medido na FASE 0:

```
$ warden archive --trusted <link> --text-first
warden archive: error: the following arguments are required: --campaign
```

O caminho barato **exigia uma campanha**. Quem manda um link solto — que foi o
caso das três conversas — não tinha caminho barato. O agente baixava o vídeo
inteiro porque era a única porta aberta, e pagava 3min46s de transcrição por um
texto que a legenda publicada entrega em 5 segundos.

### O que mudou

**1. O portão virou dois, e o caminho barato passou a existir sem campanha.**

```
warden archive --campaign <id> ...      o acervo da campanha avaliza
warden archive --trusted <url> ...      a lista de fontes do dono avaliza
```

O comando recusa rodar sem um dos dois. Isso é deliberado: abrir o caminho
rápido sem abrir uma porta lateral significa que o portão continua existindo, só
muda quem responde. A pergunta mora numa função só (`fiscal()` em
`warden_media.py`), e os dois caminhos de download passam por ela.

**2. As janelas do lote descem numa execução só.** `--windows 453.7-474.2,83.9-108.4`
em vez de dois comandos. Medido: **31s contra 38s**.

**3. A ordem de trabalho passou a existir em um lugar só.** Estava escrita por
extenso em cinco arquivos — persona, `warden-clip`, `warden-run`,
`warden-shared` e README — e três deles afirmavam ser o único lugar. Agora
`warden-clip` seção 2 é a versão, e a persona e o `warden-run` apontam para lá
em vez de repetir. A contradição sobre transcrever a fonte inteira ou só as
janelas foi apagada da persona, que era o lado errado.

### Render em paralelo: medido, e a resposta é não

O pedido era medir antes de afirmar que cabe. Medido:

| | Tempo | Pico de memória |
|---|---|---|
| Um render sozinho | 35s | +265 MiB |
| Dois em paralelo | **64s** | **3035 MiB de um teto de 3072** |

Dois em sequência custariam 70s. Em paralelo custam 64s. **O ganho é de 9%**, e
o preço é chegar a 37 MiB do teto de memória do container. O gargalo não é
espera, é CPU: x86_64 emulado sobre Apple Silicon, sem codificador de hardware.

**Não vale.** O paralelismo de render fica como está (o lote já renderiza dois
de cada vez, com teto), e o ganho real veio de outro lugar.

### Pesquisa na web, só em fonte oficial

Consultado: documentação do yt-dlp, do FFmpeg (ffmpeg.org e trac), do
faster-whisper/CTranslate2 e da Docker. O que rendeu número e o que não rendeu:

- **`--download-sections` aceita ser repetido** — está na documentação do
  yt-dlp. Foi o que virou `--windows`. Medido aqui: 31s contra 38s.
- **Por que a janela é lenta:** `--download-sections` "needs ffmpeg", e o ffmpeg
  remuxa a seção em tempo real, a ~1,8x. Uma janela de 28s custa ~17s de ffmpeg
  independentemente da banda. É por isso que duas janelas (31s) custam mais que
  o arquivo inteiro deste vídeo (13s).
- **`-ss` antes de `-i` é o rápido** — o wiki do FFmpeg diz "very fast" contra
  "relatively slow, frame-by-frame" — e o código **já usa** a forma rápida.
- **Não há codificador de hardware** num container Linux sem GPU. A tabela
  oficial do FFmpeg lista VideoToolbox como indisponível em Linux. A Docker
  documenta que a emulação QEMU "can be much slower", sem dar número.
- **faster-whisper int8**: 1m42s contra 6m58s do openai-whisper fp32, para 13
  minutos de áudio em 8 threads — número oficial do próprio README. Fica
  anotado, mas **não foi aplicado**, porque o caminho que importa deixou de
  passar por transcrição.
- Não encontrei em fonte oficial: número para o ganho de `-preset`, para
  `beam_size=1`, nem para o custo da emulação.

### A corrida que prova

Fluxo completo, do link aos dois clipes, com legenda aprovada e mosaico:

| Etapa | Aos |
|---|---|
| Portão da fonte | 1s |
| As palavras (legenda publicada) | 4s |
| As duas janelas, um comando | 35s |
| Aprovar as legendas das duas janelas | 36s |
| **Primeiro clipe pronto** | **69s** |
| **Segundo clipe pronto** | **104s** |

**Critério batido: 69s contra o alvo de 120s, e 1min44s contra o alvo de 4min.**

Contra a linha de base: o primeiro clipe levava de 9min42s a 18min21s, e o lote
de dois, de 10min56s a 20min39s — e só depois de cobrança.

### Auditoria da FASE 1

**O critério foi medido, não estimado.** Os 69s e os 104s saem de uma corrida
cronometrada de ponta a ponta, não da soma de etapas medidas em separado.

**Os dois mosaicos foram olhados, em resolução cheia.** No primeiro: o hook sai
da tela aos 3s, a legenda fica em duas linhas, o destaque amarelo acompanha a
fala, o rosto está livre. No segundo, o hook idem, e a única legenda visível é a
**do próprio vídeo de origem** — o tool detectou a faixa a 69-72% da altura,
**recusou queimar a nossa por cima** e escreveu o motivo. Eu havia lido isso
como defeito ao olhar o mosaico; a nota da ferramenta corrigiu a leitura. Isso é
o sistema funcionando.

**Um defeito reproduzido, e é da FASE 7.** O primeiro clipe saiu com a tarja
preta de ~290px embaixo, o mesmo defeito do "colonizadores". Não foi consertado
aqui porque é a FASE 7; o que mudou é que agora existe um render novo e recente
para testar o conserto contra.

**A suíte de testes: 336 testes, todos passando, 3 pulados.** Eram 325. Os onze
a mais são testes que existiam no arquivo e **nunca rodaram**: duas classes
estavam definidas duas vezes (`MissingDependencySpeaks` e
`NothingDegradesQuietly`), e em Python a segunda definição apaga a primeira. Com
elas se perdera o único teste da grade de batida. Foram renomeadas e voltaram a
existir.

E dois testes de `test_legenda_do_acervo.py` morriam num erro do ffmpeg em vez
de medir: o nome do arquivo de saída era igual ao da entrada, e o ffmpeg recusa
escrever sobre a própria entrada. Eram justamente os dois que guardam "a nota
sai mesmo quando o material está limpo". Corrigidos.

Nove testes novos guardam o portão novo: lista vazia não avaliza nada, fonte
fora da lista é recusada **antes de um byte de mídia descer**, a recusa diz como
o dono avaliza uma fonte, o mesmo fiscal atende os dois portões, o lote vira uma
execução só, e cada janela leva a própria ficha de origem.

**O que ficou aberto nesta fase:**

- A escolha entre janela e arquivo inteiro virou uma regra escrita ("abaixo de
  meia hora de fonte, puxe inteiro"), não uma decisão da ferramenta. O agente
  ainda pode errar essa.
- `faster-whisper` com `int8` não foi ligado. Só importa quando a fonte não
  publica legenda, que não é o caminho comum.
- A tagarelice, que é a outra metade da lentidão, é a FASE 2.

---

## FASE 2 — A voz

**Alvo: três mensagens numa tarefa de dois cortes. Acima de cinco, não fechou.**

### A causa da tagarelice estava escrita na persona

E ela também corrige um erro meu na FASE 0.

Eu havia medido, pelo log do gateway, que só 20 das 57 mensagens do agente
saíram da máquina, e que 37 "ficaram no caminho". Estava errado. O log do
gateway registra **uma linha de envio por turno**, e é por isso que parece
assim. Fui ao código do gateway: existe uma opção chamada
`interim_assistant_messages` que **vem ligada por padrão** e não está desligada
nesta instalação. Cada texto que o agente escreve entre duas chamadas de
ferramenta é entregue como mensagem, por um caminho separado.

O número que você contou no celular — 12 a 16 entre o link e o primeiro clipe —
é o certo. As 57 chegaram.

E aí está a causa. A persona dizia, com todas as letras:

> "Your working notes are not messages, and they are free — Text you write
> BETWEEN tool calls is not delivered to anyone, it stays on this side."

**O agente acreditou e narrou tudo.** Ele não estava desobedecendo a regra de
falar pouco: ele estava obedecendo uma regra que dizia que aquilo não era falar.
Cada uma das 37 "notas de trabalho" era uma notificação no seu celular.

O que **é** de graça é o raciocínio: `thinking_progress` vem **desligado**, e o
que o agente pensa antes de escrever nunca foi entregue. Também confirmado no
código.

### O que mudou

A seção da voz foi reescrita em cima do modelo certo:

- **Toda prosa que o agente escreve chega ao celular.** Dito assim, com o número
  das 57 e a citação da opção do gateway.
- **A única coisa que não viaja do meio do turno é o anexo.** O texto vai, o
  arquivo não, e nada avisa.
- **O orçamento caiu de quatro mensagens para três.** A quarta era "o que fazer
  agora", que passou a ir junto com o segundo clipe.
- **O aviso de trabalho longo é a mensagem 1 e mais nenhuma.** Era uma
  contradição dentro da própria persona: "nunca diga o que vai rodar" numa
  seção, "avise antes de começar" em outra, e em 15/09 saíram três mensagens
  seguidas dizendo "vídeo baixado".
- **Proibições novas:** narrar dúvida que você vai resolver olhando, explicar
  mudança de ideia, e dar lição não pedida.
- **Quatro exemplos novos** de ✗/✓, todos tirados do que foi realmente enviado
  em 15/09, incluindo os três "vídeo baixado" em sequência e o "antes de mandar,
  isso levantou dois problemas que preciso confirmar".
- **A lista de jargão ganhou `BPM`, `bar`, `drop` e `breach`**, que foram para a
  tela em 15/09 ("Faixa analisada: 92.3 BPM, barra de 2.6s, drop em 28.75s").
- **A lição não pedida virou regra própria**, com o caso do NCS: link de trilha
  que o dono manda, o agente usa. A mecânica de guardar a faixa é a FASE 6; a
  regra de não dar aula está aqui porque é voz.

### As contradições que sobravam

A varredura achou onze entre arquivos que o agente lê como ordem. As de voz
foram fechadas:

| Conflito | Como ficou |
|---|---|
| "Nunca narre o que vai rodar" × "avise antes de trabalho longo" | O aviso é a mensagem 1, e só ela |
| "Nada de notas de progresso" × "progresso visível por minuto" | A linha de 15s do `cut` é para o agente ler, não para repassar |
| "Não use medida da máquina" × "use as palavras exatas da ferramenta" | O `warden-check` passou a dizer que isso vale **quando a pessoa perguntou** se o clipe passa |
| "Suas notas são grátis" × a realidade do gateway | Apagada, e substituída pelo modelo medido |

### O instrumento: `warden voz`

"Ele fala demais" era impressão. Agora é um número que a ferramenta responde.

```
$ warden voz --since <quando>
15/09 00:42  16 message(s), 2 with a file  [DEMAIS]  https://www.youtube.com/...
       - Fonte confiável (youtube.com). Vou baixar, transcrever e escolher...
       - Vídeo baixado. Vou seguir com a transcrição e escolha dos momentos.
       - Vídeo baixado (18.7min, 1920x1080). Vou transcrever agora...
       ...
17 request(s); the worst one cost 16 message(s). Target 3, ceiling 4.
```

Ele lê o banco de conversas do runtime, conta por PEDIDO da pessoa (o despertar
de um render que terminou não conta como pedido novo), **nomeia as mensagens que
sobraram**, e **sai com código 1** quando alguma tarefa passa de quatro. Rodado
sobre as três conversas de 15/09, reproduz exatamente os números da FASE 0: 16,
11 e 5.

### Auditoria da FASE 2

**O critério não pôde ser fechado por mim, e digo por quê.** O critério é rodar
o fluxo de novo numa conversa e contar as mensagens. Isso exige escrever no seu
chat como se fosse você, e eu não vou fazer isso — você está dormindo e a
conversa é sua. O que eu pude fazer, e fiz, foi transformar a contagem em algo
que a ferramenta responde sozinha, com código de saída, para que a corrida da
FASE 8 e a sua próxima conversa sejam medidas em vez de sentidas.

**Então o estado honesto da FASE 2 é: a causa foi encontrada e removida, o
instrumento existe, e o número final sai na primeira conversa real.** Não vou
escrever que fechou antes de o número existir.

**A suíte: 350 testes, todos passando.** Cinco novos guardam a contagem: três
mensagens para dois cortes passa, dezesseis reprova e sai 1, as mensagens que
sobraram são nomeadas uma a uma, o despertar de um processo de fundo não abre
uma tarefa nova, e um banco ausente diz "não dá para medir daqui" em vez de
devolver zero como se fosse medida.

**A tabela de comandos foi completada.** A persona afirma que `warden-shared`
descreve todos os comandos, e faltavam lá `warden tracks`, `warden captions
review` e `warden style` — três que outras skills mandam usar pelo nome.

**O que ficou aberto:**

- A contagem real, numa conversa de verdade. É a FASE 8 e a sua próxima
  conversa.
- A entrega continua apontando para uma ferramenta que não existe. É a FASE 3, a
  seguir, e é o que faz a mensagem 2 e a 3 chegarem sem arquivo.
