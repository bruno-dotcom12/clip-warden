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

---

## FASE 3 — Entregar todos os arquivos, sem você cobrar

**A pergunta era: a confirmação é lida e ignorada, ou não é lida? Se ela não
existe de fato nesse caminho, não é regra, é frase.**

A resposta: **ela não existe, e a regra era frase.**

### O caminho realmente usado

A persona e o `warden-clip` mandavam entregar cada clipe chamando uma ferramenta
chamada `send_message`, ler o resultado, e reenviar se falhasse. Medido:

- Nas três conversas o agente chamou **cinco ferramentas** — `terminal`,
  `vision_analyze`, `search_files`, `read_file` e `patch`. **Zero chamadas de
  `send_message`.**
- `send_message` não existe neste runtime. Procurando no sistema inteiro, ele só
  aparece nos nossos próprios arquivos de instrução e em bibliotecas de
  terceiros sem relação.

Então a instrução mais enfática do documento — a que tem mais evidência anexada
— mandava chamar algo que não está lá. E "leia o resultado" não tinha resultado
para ler. Frase, não regra.

**O que entrega de verdade** é a linha `MEDIA:<caminho>` na **última mensagem do
turno**. O gateway lê essa mensagem, extrai cada caminho e manda cada um como
anexo. Duas linhas `MEDIA:` na mesma mensagem final entregam dois arquivos — foi
medido duas vezes, e nas duas foi o reenvio depois de você cobrar.

Uma linha `MEDIA:` em qualquer outro ponto do turno **não anexa nada, em
silêncio**, e o texto em volta chega normalmente. É isso que torna o defeito
caro: você lê "aqui está o primeiro corte" e nenhum arquivo aparece.

### O que mudou

**1. A entrega passou a ser descrita pelo caminho que existe.** Persona,
`warden-clip`, `warden-run`, `warden-style` e as linhas que o próprio `warden
cut` imprime: todas falavam de `send_message`. Nenhuma fala mais.

**2. Um clipe por turno, e o render seguinte é o que te acorda.** Como só a
mensagem final entrega, não dá para mandar o clipe 1 no meio do turno nem
segurá-lo até o clipe 2 ficar pronto. O caminho medido é outro: começar o render
do clipe 2 **em segundo plano** antes de encerrar o turno. Quando um comando de
fundo termina, o agente é acordado com o resultado — foi exatamente assim que a
transcrição de 15/09 o trouxe de volta quatro minutos depois.

```
1. renderiza o clipe 1, olha o mosaico
2. dispara o render do clipe 2 em segundo plano
3. encerra o turno com a linha MEDIA: do clipe 1   -> entregue
4. o render terminando te acorda; olha o mosaico do clipe 2
5. encerra esse turno com a linha MEDIA: do clipe 2 -> entregue
```

Três mensagens, dois arquivos, nenhum esperando o outro.

**3. A confirmação virou uma leitura.** Antes, `warden delivered <clipe>`
acreditava no agente: ele dizia "entreguei" e o clipe era riscado. Isso valia
zero, porque o caso que se quer pegar é justamente aquele em que o agente ACHA
que entregou e não entregou.

Agora o comando vai ao log do gateway — o único registro independente de que um
anexo saiu — e pergunta se algum saiu depois que aquele clipe foi liberado:

| O que o log diz | O que o comando faz |
|---|---|
| Nenhum anexo desde que o clipe foi liberado | **Recusa riscar**, e diz que a linha `MEDIA:` tem que estar na mensagem final |
| Uma falha de envio registrada | **Recusa riscar**, e manda reenviar com uma linha avisando |
| Um anexo saiu | Risca, e diz quantos saíram |
| O log não está legível | Risca, **e diz em voz alta que não verificou** |

Esse último caso é deliberado: "não consegui olhar" não é "está tudo certo", mas
travar a entrega para sempre porque o log mudou de lugar seria pior que o
defeito. O meio-termo é dizer.

### O teste que força a falha

Pedido: um teste que force uma falha de envio e prove que o reenvio acontece.
São seis, e eles falhariam sem o conserto:

1. **Envio que falhou não é riscado e o comando manda reenviar** — o log recebe
   `Failed to send media (.mp4): upstream refused`, o comando sai 1, diz "NOT
   confirming" e "Send it again", e o clipe **continua devendo**, que é o que
   impede o turno de terminar.
2. **Depois do reenvio bem-sucedido o clipe é riscado** — o mesmo clipe passa
   por três estados em sequência: sem anexo (sai 1), falha (sai 1), anexo
   registrado (sai 0 e a dívida zera).
3. **Sem anexo nenhum no log, recusa e diz onde a linha tem que estar** — é o
   defeito de 15/09 exatamente: `MEDIA:` no meio do turno.
4. **Um anexo anterior ao clipe não conta como entrega dele.**
5. **Log ilegível risca mas diz em voz alta que não verificou.**
6. **O número reportado é o de envios, não o de arquivos** — dois no disco, um
   confirmado, ainda deve um.

### Auditoria da FASE 3

**As quatro regras pedidas, e onde cada uma está:**

| Regra | Onde |
|---|---|
| Um envio por clipe, e o agente lê o retorno | Um TURNO por clipe; o retorno é o log, lido por `warden delivered` |
| Se um não confirmar, reenvia sozinho e avisa em uma linha | O comando recusa riscar e imprime a frase de reenvio; teste 1 e 2 |
| Nunca escreve "os N estão prontos" antes de os N chegarem | Guarda já existente, e ele me pegou |
| O número reportado é o de envios confirmados | Teste 6 |

**Um guarda de teste antigo me corrigiu, e eu mudei a frase em vez do guarda.**
Existe um teste que proíbe a saída do lote de afirmar entrega que não aconteceu.
Minha primeira redação dizia "Each one **is delivered** by ending a turn with..."
e ele reprovou. Está certo: a frase afirma no presente uma coisa que o comando
não fez. Ficou "reaches the person only when a turn ENDS with...".

**Dois testes antigos passaram a depender do log da máquina, e isso era um
defeito meu.** `ContagemDeEntregas` confirmava clipes e, com a mudança, passou a
consultar o log real do container — que não tem envio recente. Eles agora
escrevem o próprio log e apontam o comando para ele. Um teste que depende do que
a máquina fez hoje não é um teste.

**A suíte: 356 testes, todos passando.**

**O que ficou aberto:**

- O laço completo — render, entrega, despertar pelo render seguinte — só é
  exercido de ponta a ponta numa conversa real. É a FASE 8.
- O log do gateway diz que **um** anexo saiu, não **qual**. Com dois clipes
  entregues no mesmo segundo, a contagem prova que dois saíram, mas não casa
  arquivo com envio. Para o defeito que isso existe para pegar — nenhum anexo,
  ou uma falha — a contagem basta.

---

## FASE 4 — Ler o que está na tela

### O que a medição mostrou, e não é o que parecia

A hipótese era que o agente estivesse cego ao cartão de pré-visualização.
**Não é isso.** Conferido no conteúdo bruto que chega ao agente: o texto não tem
URL nenhuma, e o campo de metadados está vazio. O cartão que você vê é desenhado
pelo aplicativo de chat e **não viaja até o agente**.

O link vinha numa **mensagem separada, sete segundos depois**. E o agente
respondia em cinco.

| Conversa | Pedido | "Faltou o link" | O link chegou |
|---|---|---|---|
| 1 | 00:42:10 | 00:42:15 (+5s) | 00:42:17 (+7s) |
| 2 | 02:41:07 | 02:41:12 (+5s) | 02:41:14 (+7s) |
| música | 02:55:51 | 02:55:57 (+6s) | 02:55:58 (+7s) |

Três de três, o mesmo placar. **Ele perdia a corrida.** Responder rápido parecia
de graça e custava uma ida e volta, sempre.

### O que mudou

O log do gateway registra **toda mensagem que entra, com o texto e o horário, no
instante em que ela chega** — antes e independentemente do turno que ela vai
disparar. É a única fonte que responde "chegou mais alguma coisa enquanto eu
pensava?".

```
warden inbox --wait 20
```

Espera até 20 segundos por uma mensagem nova carregando uma URL, e imprime o
link assim que ele aparece. Sai 0 com o link, sai 1 depois do prazo — e aí sim a
pergunta é legítima.

A persona e o `warden-clip` passaram a mandar rodar isso, **em vez de
perguntar**, sempre que as palavras prometem um link que não está no texto:
"esse vídeo", "esse link", "abaixo", "essa música".

### Um defeito que o teste achou sozinho

O carimbo do log tem milissegundos (`00:42:17,602`) e eu estava descartando os
três dígitos. Duas mensagens no mesmo segundo — que é o caso comum, a pessoa
cola o link logo depois do texto — ficavam com o mesmo carimbo, e a segunda
sumia de qualquer comparação "chegou depois de". O teste do link truncado é que
expôs isso.

### A pergunta já respondida

O outro pedido da fase era varrer o fluxo atrás de pontos em que ele reprocessa
uma pergunta já respondida. Reconstituído do banco:

- 02:57:28 — o agente pergunta qual clipe editar
- 02:57:57 — você responde: "Faculdade nao vende"
- 02:58:06 — o agente **roda `warden cut --track`**
- 02:58:31 — o arquivo fica pronto no disco
- 03:04:59 — você pergunta "Esta fazendo?"
- 03:05:07 — ele responde: *"Não, ainda não comecei a cortar — estava esperando
  sua resposta sobre qual caminho seguir."*

A frase é falsa duas vezes: a pergunta já tinha sido respondida sete minutos
antes, e ele já tinha cortado. Duas regras foram para a persona:

- **Se você perguntou e a próxima mensagem da pessoa não é uma pergunta, ela é a
  resposta.** Aja. Nunca diga que está esperando algo que já está na conversa
  acima de você.
- **Nunca descreva o próprio estado de memória.** "Ainda não comecei" é uma
  afirmação sobre o mundo. Antes de dizer, olhe: o arquivo está no disco ou não,
  `warden delivered` sai 1 ou não. E depois de olhar, quase sempre, não diga —
  isso é a FASE 2.

### Auditoria da FASE 4

**A causa foi medida, não suposta, e ela contraria a hipótese original.** Valia
dizer isso: consertar "ler o cartão de pré-visualização" não teria consertado
nada, porque o cartão não existe do lado do agente.

**Seis testes novos**, todos falhariam sem o conserto: o link que chega durante a
espera é devolvido; a mensagem que disparou o turno **não** conta como nova (ou o
agente cortaria o vídeo da conversa anterior); sem link a pergunta volta a ser
legítima; uma mensagem sem link não encerra a espera; um link cortado pelo log é
**recusado** em vez de devolvido quebrado; e sem log o comando diz que não dá
para ver chegada em vez de fingir que nada chegou.

**A suíte: 362 testes, todos passando.**

**O que ficou aberto, e é uma limitação real:**

- O log corta a mensagem por volta de 80 caracteres. Quando a pessoa manda um
  texto longo **com o link no fim**, a URL pode vir truncada. O comando detecta
  isso e recusa, em vez de baixar outra coisa — mas nesse caso a pergunta volta
  a ser necessária.
- Se a pessoa demorar mais de 20 segundos para colar o link, a pergunta acontece
  do mesmo jeito. O prazo é ajustável e 20s cobre os 7s medidos com folga.

---

## FASE 5 — O edit de verdade

### A referência, medida antes de copiada

Baixei o short que você deu (`youtube.com/shorts/QmrLImO6fus`), 1152x1536, 60fps,
15,83s, e medi:

| | Valor |
|---|---|
| BPM da trilha | **129,2** (batida de 0,464s, barra de 1,858s) |
| Trocas de cena | **10 a 13** distintas (limiar 0,3 e 0,2) |
| **Cortes por 20 segundos** | **12,6 a 16,4** |
| Intervalo mediano entre cortes | 0,95 a 1,03s |
| Intervalo dominante, em batidas | **2 batidas** (meia barra), com 4 batidas frequentes |
| **Erro contra a batida** | **mediano 28 a 33ms**, 8 de 10 (e 11 de 13) dentro de 80ms |
| Erro contra a barra | 38ms |

**Um detalhe do método importa.** Na primeira medição a referência pareceu
desalinhada — erro mediano de 93ms — porque eu supus que a grade começava no
`first_beat_s` que a detecção devolve. Isso é assumir o que se quer medir.
Procurando a fase que minimiza o erro, a referência está a **33ms**. A grade que
governa é a **batida**, não a barra.

**E a referência não tem legenda de fala.** Olhei o mosaico dela: o que está na
tela é um **placar fixo** com os escudos dos times, uma marca d'água, e
transições borradas entre planos. Nenhuma palavra falada queimada. Isso não muda
o que você pediu — você pediu legenda — mas vale estar escrito.

### O que o edit do Djokovic era

| | A referência | O "edit" de 15/09 |
|---|---|---|
| Trocas por 20s | 12,6 a 16,4 | 11,5 |
| Erro contra a batida | mediano **33ms** | mediano **122ms** |
| Dentro de 80ms | 8 de 10 | **4 de 12** |
| De onde vieram os cortes | escolhidos | **do próprio vídeo** |
| Legenda | placar fixo | nenhuma |

4 de 12 dentro de 80ms numa grade de 650ms é o que o acaso dá (o esperado é 3).

### O que mudou no código

**`shots` deixou de ser aceito-e-descartado.** O bloco que existia escrevia, com
todas as letras, `"IGNOREI os N planos deste clipe"` — e era a verdade: o campo
atravessava a assinatura inteira e nenhuma linha do render o lia.

Agora:

- **Emenda de verdade.** Um `trim` por plano, `concat` no fim, e o enquadramento
  vertical aplicado **uma vez, depois** da emenda — recortar plano a plano daria
  a cada um um enquadramento diferente da mesma cena.
- **Cada plano dura um número inteiro de batidas.** O começo do plano nunca é
  mexido (quem escolheu o plano escolheu onde ele começa); o que se ajusta é
  quanto ele dura. Mínimo de duas batidas: meia batida de imagem não lê como
  plano, lê como falha.
- **Zoom leve por plano, alternando** para dentro e para fora. Todo plano
  empurrando no mesmo sentido lê como efeito, não como montagem.
- **A trilha entra no drop** e o áudio da fonte é descartado num edit emendado:
  fala contínua sobre imagem que pula está falando da cena errada.
- **A legenda acompanha os planos**: as falas de cada trecho são realocadas para
  o relógio da emenda. Planos colados são tratados como um trecho só, senão a
  frase seria cortada numa borda que a imagem não cortou.
- **O sidecar grava cada corte e o erro dele** contra a grade, com a fase
  procurada. "Cortou na batida" virou um número.

### Duas recusas novas, e as duas são o ponto

**A legenda que vira colagem.** Com planos espalhados pelo material, cada um
abre no meio de uma frase. Medido num render real de 15 planos, a legenda saiu:

> "portas, a que vai pra sala / e a que vai Você lembra," → "Nossa dispensa é
> cheia. Então," → "» para gravação. Vamos respirar. Mas enfim,"

Mecanicamente correta, ilegível. Agora, quando mais da metade dos trechos
emendados abre no meio de uma frase, o clipe sai **sem legenda** e diz por quê,
com as duas saídas: começar cada plano onde uma fala começa, ou usar `--hook` em
vez de `--subtitles`, que é o que a referência faz.

**A amostragem que olhava o lugar errado — e este era um defeito meu.** A
detecção de texto queimado na fonte amostra 8 quadros entre `start` e
`start+length`. Num edit isso olha um trecho **contínuo que o clipe não mostra**:
num edit de 7 planos entre 11s e 100s, os 8 quadros saíram de 11s a 29s. A
detecção disse "limpo" e o render saiu com a legenda do próprio vídeo aparecendo
atrás da nossa. **Eu só peguei isso porque extraí o quadro em resolução cheia** —
no mosaico reduzido as duas se confundem. Corrigido: os quadros agora vêm **dos
planos**, e com a correção a mesma renderização passou a recusar a legenda, que
é a resposta certa.

### O edit que bate o critério

`f5-final.mp4`, 7 planos de 4 batidas, na região do material que a sondagem
mostrou limpa, com cada plano começando onde uma fala começa:

| Critério | Pedido | Medido |
|---|---|---|
| Trocas na batida | erro < 80ms | **0ms**, nas 6 |
| Cenas em 20s | ≥ 5 | **7,7** |
| Legenda | sim | **14 cues**, até 2 linhas, com destaque palavra a palavra |
| Colagem | — | 0 de 7 trechos abrem no meio de frase |
| Contact sheet | olhado | olhado, item por item |

### Auditoria da FASE 5

**O mosaico foi olhado, item por item.** Oito cenas visivelmente diferentes —
corredor, corredor mais perto, corredor aberto, ilha da cozinha, bancada,
geladeira, forno. A legenda em duas linhas, amarela, com o destaque acompanhando
a fala. Rosto livre. Sem tarja preta. Sem borda de material cortada.

**Um item do checklist falha, e quem apontou foi a própria ferramenta:** uma cue
fecha em `"de crédito, era 10 vezes no"` — termina numa preposição. Está no
sidecar como `hanging_endings`. Não vou chamar o clipe de limpo: ele tem esse
defeito.

**O que ficou aberto, e é uma tensão real, não um item de lista:**

Um edit não consegue ter, ao mesmo tempo, **trocas de cena visíveis** e **legenda
de fala coerente**, quando a fonte é uma pessoa falando continuamente. Ou os
planos vêm do mesmo trecho — e aí a fala fecha, mas os cortes são invisíveis — ou
vêm de lugares diferentes — e aí a imagem muda, mas a fala vira colagem. O que
funciona é o meio-termo que produziu o `f5-final`: planos de dois a três
segundos, espalhados, **cada um começando onde uma fala começa**. Isso é
trabalho de escolha, e o agente ainda escolhe à mão.

A referência resolve isso de outro jeito: **não usa legenda de fala.** Usa um
gráfico fixo. Se você quiser o edit mais próximo dela, o caminho é `--hook` com
uma linha escrita, sem `--subtitles`.

**A suíte: 368 testes, todos passando.** Seis novos medem o encaixe na batida, e
um deles é a própria referência: os dez cortes medidos no seu short passam pelo
mesmo teste de 80ms que o nosso edit tem de passar.
