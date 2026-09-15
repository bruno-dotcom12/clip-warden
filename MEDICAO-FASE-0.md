# Medição do estado atual — 15/09/2026

A linha de base. Toda fase seguinte se compara com estes números.

Nada aqui é impressão. Cada número tem a origem escrita ao lado: o banco de
conversas do agente (`/var/lib/hermes/state.db`), o log do gateway
(`/var/lib/hermes/logs/gateway.log`), os arquivos renderizados dentro do
container, ou uma corrida cronometrada feita hoje.

As três conversas medidas são as que o dono testou em 15/09 entre 00:39 e 03:11.

---

## 1. Tempo

### Do link ao primeiro arquivo na mão da pessoa

| Conversa | Fonte | Link chegou | 1º clipe chegou | Tempo | 2º clipe chegou | Tempo |
|---|---|---|---|---|---|---|
| 1 | vídeo de 18,7 min | 00:42:17 | 01:00:38 | **18min21s** | 01:02:56 *(só após cobrança)* | 20min39s |
| 2 | vídeo de 16,4 min | 02:41:14 | 02:50:56 | **9min42s** | 02:52:10 *(só após cobrança)* | 10min56s |
| 3 | edit com trilha | 02:54:36 | 03:11:40 | **17min04s** | — | — |

Os horários de chegada são do log do gateway, nas linhas
`[Plow_Chat] Sending video attachment (.mp4)`. Não são a hora em que o arquivo
ficou pronto no disco: são a hora em que ele saiu da máquina.

### Etapa por etapa, na conversa 1

Tirado dos comandos que o agente de fato rodou, com o horário de cada um.

| Etapa | Duração | Observação |
|---|---|---|
| Conferir a fonte e gravar a campanha | 29s | três comandos, dois deles repetidos |
| **Baixar o vídeo inteiro** | 28s | 382 MB |
| **Transcrever o vídeo inteiro** | **3min46s** | 18,7 min de áudio para usar 40s |
| Resumo e sinais | ~20s | |
| Escolher a janela e aprovar a legenda | 64s | |
| Renderizar o corte 1 | ~3min | e **renderizou de novo** aos 00:52:23 |
| Renderizar o corte 2 | ~2min | e **renderizou de novo** aos 00:55:38 |
| Investigar um problema que não existia | **3min01s** | 00:57:37 → 01:00:38 |

Dois renders foram jogados fora por mudança de ideia depois de o vídeo já estar
aberto, e três minutos inteiros foram gastos perseguindo uma legenda dupla que
não estava lá.

### O atalho que existia e não foi usado

Medido hoje, no mesmo vídeo da conversa 1:

| O que se baixa | Tempo | Tamanho |
|---|---|---|
| A legenda que o YouTube já publica | **5s** | 89 KB |
| O vídeo inteiro | 13s | 382 MB |

A legenda publicada desse vídeo está completa: **1007 falas cobrindo até
18:29**, de um vídeo de 18,7 minutos. Estava disponível o tempo todo.

**Custo do caminho errado: 3min46s de transcrição para conseguir um texto que
vinha pronto em 5 segundos.**

### Por que o agente não pegou o atalho

Não foi desobediência. O comando não oferece o atalho nesse caso:

```
$ warden archive --trusted <link> --text-first
warden archive: error: the following arguments are required: --campaign
```

`warden archive --text-first` **exige uma campanha**. Quando a pessoa manda um
link solto — que é exatamente o que aconteceu nas três conversas — o caminho
rápido não existe. O agente baixou o vídeo inteiro porque era a única porta
aberta.

---

## 2. Mensagens

Contadas no banco de conversas, nas três conversas juntas.

| | Quantidade |
|---|---|
| Mensagens escritas pelo agente | **57** |
| Que de fato saíram da máquina | 20 |
| Que ficaram no caminho (escritas entre chamadas de ferramenta) | 37 |

Das 20 que saíram:

| O que a mensagem fazia | Quantas |
|---|---|
| Trazia um arquivo | 5 |
| Fazia uma pergunta que só a pessoa podia responder | 6 |
| Narrava decisão interna, sem mudar nada para a pessoa | 5 |
| **Queimava uma ida e volta à toa** | **4** |

Do link ao primeiro clipe, conversa 1: **16 mensagens**. Conversa 2: **11**.

### As idas e voltas desperdiçadas

Nove, em três conversas. Cada uma é uma mensagem que a pessoa teve que digitar
por causa do agente:

1. "Faltou o link" — conversa 1
2. "Faltou o link" — conversa 2
3. "Não veio nenhum arquivo nem link" — sobre a música
4. Lição de direitos autorais sobre o link do NCS, que a pessoa teve que rebater
5. "E o outro video?"
6. "Aqui chegou so 1 video do ultimo link"
7. "Falta o outro video, voce so me mandou um"
8. "Esta fazendo?"
9. Escolher qual clipe editar levou três perguntas em cadeia, uma por mensagem

---

## 3. Por que só chegou um clipe — a causa, encontrada

Esta é a descoberta mais importante da medição.

**A persona e o `warden-clip/SKILL.md` mandam o agente entregar cada clipe
chamando uma ferramenta chamada `send_message`. Essa ferramenta não existe
neste sistema.**

Verificado de três formas:

- Nas três conversas, o agente usou **cinco ferramentas**: `terminal` (74
  vezes), `vision_analyze` (23), `search_files` (10), `read_file` (2) e `patch`
  (2). **Nenhuma chamada de `send_message`. Zero.**
- Procurando `send_message` em todo o sistema, ele só aparece nos nossos
  próprios arquivos de instrução e dentro de bibliotecas de terceiros sem
  relação (Slack, Discord). Não é uma ferramenta oferecida ao agente.
- O que de fato entrega um arquivo é escrever `MEDIA:<caminho>` na mensagem, e o
  gateway extrai o anexo daí.

E o detalhe que decide tudo: **o gateway só extrai anexo da última mensagem do
turno.** As outras mensagens chegam como texto, mas qualquer `MEDIA:` dentro
delas não anexa nada — e nada em lugar nenhum avisa que não anexou.

Foi exatamente isso que aconteceu:

| Conversa | `MEDIA:` escrito no meio do turno | `MEDIA:` na última mensagem |
|---|---|---|
| 1 | 00:53:29 — **não chegou** | 01:00:38 — chegou |
| 2 | 02:48:12 — **não chegou** | 02:50:56 — chegou |

Dois arquivos escritos, dois perdidos em silêncio, e a pessoa teve que cobrar
nas duas vezes. Nas duas vezes o agente reenviou os dois juntos, numa mensagem
só, e aí os dois chegaram — o que prova que **várias linhas `MEDIA:` na mesma
mensagem funcionam**.

### A confirmação que a regra exige não é possível

A persona manda: "chame a ferramenta, leia o resultado, e reenvie se falhar".
Como a ferramenta não existe, não há resultado nenhum para ler. A regra da
confirmação, escrita com a maior autoridade do documento, não tem onde
acontecer.

### A contabilidade de entrega nunca foi usada

O arquivo `entregas.json` registra oito clipes liberados. **Os oito estão
marcados `sent: false`**, inclusive os cinco que comprovadamente chegaram.
`warden delivered` nunca foi rodado, nem uma vez.

O comando funciona e sai com código 1 enquanto houver clipe devendo. Mas nada no
código chama por ele: `warden cut` renderiza e libera um clipe novo sem nunca
perguntar se o anterior foi entregue. A cobrança existe só como frase impressa
em stderr e como regra escrita nos `.md`. O comentário do próprio código admite:
*"Não impede o modelo de mentir — nada impede"*.

---

## 4. Por que o agente disse "faltou o link"

Nas três vezes, o link **não estava** na mensagem. Verificado no conteúdo bruto
que chega ao agente: o texto não tem URL, e o campo de metadados está vazio
(`metadata: None`). O card de pré-visualização que aparece na tela da pessoa é
desenhado pelo aplicativo de chat; ele não viaja até o agente.

O link veio numa **mensagem separada, sete segundos depois**. E o agente
respondeu em cinco.

| Conversa | Pedido | Resposta do agente | Link chegou |
|---|---|---|---|
| 1 | 00:42:10 | 00:42:15 (+5s) | 00:42:17 (+7s) |
| 2 | 02:41:07 | 02:41:12 (+5s) | 02:41:14 (+7s) |
| música | 02:55:51 | 02:55:57 (+6s) | 02:55:58 (+7s) |

Três de três, o mesmo padrão. **O agente não é cego ao link: ele responde
rápido demais e perde a corrida.** O conserto não é enxergar o card — é não
responder "faltou" quando o texto aponta para algo que ainda está chegando.

---

## 5. "Estava esperando sua resposta", sobre coisa já respondida

Reconstituído do banco:

- 02:57:28 — o agente pergunta qual clipe editar.
- 02:57:57 — a pessoa responde: "Faculdade nao vende".
- 02:58:06 — o agente **roda `warden cut --track`**.
- 02:58:31 — o arquivo `clip-vender-melhor-edit.mp4` fica pronto no disco.
- 02:58:31 a 03:02:11 — **três minutos e quarenta de silêncio total.**
- 03:04:59 — a pessoa pergunta: "Esta fazendo?"
- 03:05:07 — o agente responde: *"Não, ainda não comecei a cortar — estava
  esperando sua resposta sobre qual caminho seguir."*

**A frase é falsa.** O agente tinha cortado seis minutos antes, tinha o arquivo
pronto no disco, o examinou, reprovou e não contou. A pergunta já tinha sido
respondida às 02:57:57.

---

## 6. Os defeitos de imagem, medidos nos arquivos

### O edit do Djokovic

`clip-djokovic-edit.mp4`, 1080x1920, 20,8s.

**Trocas de cena detectadas: 12.** Intervalo médio 1,585s. Os intervalos são
2,93 · 3,03 · 0,60 · 2,03 · 1,93 · 0,17 · 0,17 · 0,67 · 2,97 · 1,73 · 1,20 —
irregulares de 0,17s a 3,03s.

A trilha é Disfigure – Blank: **92,3 BPM**, batida a cada 0,650s, barra de
2,600s.

Cruzando as duas coisas:

| | |
|---|---|
| Trocas dentro de 80ms de uma batida | **4 de 12** |
| Erro mediano contra a batida | **122ms** |
| Erro mediano contra a meia barra | **268ms** |

Para cortes jogados ao acaso contra uma grade de 650ms, o esperado seria ~3 de
12 dentro de 80ms. Deu 4. **Está no nível do acaso: as trocas não foram feitas
na batida. São as trocas do vídeo original.**

O que o arquivo de acompanhamento do render diz, e confirma:

- `"caption": null` — **o edit saiu sem legenda nenhuma**
- `"hook": null` — sem hook
- `"source_caption_clash": true` — a ferramenta **detectou** que o material já
  traz texto queimado
- `"footer_covered": false` — e **não cobriu**

No quadro dos 19,5s aparece o aviso amarelo da Polymarket do próprio vídeo,
cortado nos dois lados. No quadro dos 11,7s, uma cartela em preto e branco com
texto fatiado na margem esquerda.

**Causa, no código:** `warden cut --plan` aceita um campo `shots` com a lista de
planos, passa ele pela assinatura inteira de `cut()` — e então
(`warden_media.py:1970`) escreve uma nota dizendo *"IGNOREI os N planos deste
clipe"* e renderiza uma janela contínua só: um `-ss`, um `-t`, um zoom linear
sobre tudo. Não existe emenda de trechos em lugar nenhum. A trilha altera
apenas o **comprimento** do corte (arredondado para barra inteira) e o segundo
em que a música entra. Nada alinha imagem a batida.

### A tarja preta do clipe "colonizadores"

Medida linha a linha, em resolução cheia, em dois quadros diferentes:

| | |
|---|---|
| Tarja no topo | **0 px** |
| Tarja embaixo | **288 a 290 px** — 15% do quadro, em todos os quadros |

A tarja é só embaixo, não em cima e embaixo. Mas ela é real e está em todo
quadro.

**Causa encontrada.** É o "degradê" que cobre legenda do acervo. O arquivo
`rodape.png` tem 1080x384 e a curva de opacidade dele é:

- do topo até 26% da faixa: sobe de transparente até quase opaco
- **de 26% até o fim: opacidade 252 de 255, constante**

Ou seja: 74% da faixa é preto sólido. O código chama de degradê
(`warden_style.py:530`, `FOOTER_ALPHA = 252`), o comentário promete *"cobre o
texto de baixo sem virar tarja"*, e o que sai é uma tarja com a borda de cima
suavizada.

E ela foi aplicada sobre material limpo: a medição do próprio render diz
`0/8 quadros` acima do limiar, média 1,5x contra um piso de 1,35x. Entrou pela
regra do "suspeito", que manda cobrir por precaução.

### A legenda amarela do "colonizadores"

**A legenda amarela é a nossa, não a do vídeo de origem.**

O arquivo de legenda do clipe define a cor primária como `&H0000E5FF`, que é
amarelo puro, e o texto dele bate palavra por palavra com o que está na tela,
inclusive o `Caralho, muito foda. Em\N1826,`. A contagem de camadas de texto no
render é 2 (a nossa legenda e a tarja), não duas legendas.

Isso muda o defeito, não o elimina: **a nossa legenda é amarela, na mesma
posição e no mesmo estilo em que os vídeos de origem costumam queimar a
própria.** Ela é indistinguível de uma legenda alheia — foi o que aconteceu com
quem olhou.

O "Em 1826" é erro de transcrição, e ele foi **queimado depois de o próprio
agente marcá-lo como suspeito**: às 02:48:48 ele escreveu *"Legenda um pouco
confusa ('Em 1826' — provavelmente erro do Whisper...). Vou aprovar assim
mesmo"*, e aprovou.

### Como a verificação errou nas duas direções

Na conversa 1 o agente viu legenda dupla no mosaico, foi conferir no arquivo de
legenda, não achou a frase e declarou falso positivo. O arquivo de legenda só
contém a nossa legenda — ele nunca acharia a do vídeo de origem lá. A prova não
respondia à pergunta. Custou 3min01s.

Na conversa 3 ele inventou um problema lendo mal o mosaico, e depois descobriu
sozinho que não existia.

---

## 7. O que a varredura das instruções encontrou

Onze contradições entre arquivos que o agente lê como ordem. As que custam caro:

| Conflito | De um lado | Do outro |
|---|---|---|
| Transcrever a fonte inteira, ou só as janelas escolhidas | `persona.md:284` | `warden-clip/SKILL.md:72` |
| Nunca narrar o que vai fazer × avisar antes de trabalho longo | `persona.md:50` | `persona.md:283` |
| Não usar medida da máquina × usar as palavras exatas da ferramenta | `persona.md:50` | `warden-check/SKILL.md:31` |
| Nada de notas de progresso × progresso visível por minuto | `warden-run/SKILL.md:109` | `BRIEFING:201` |
| Checklist do mosaico com 5 itens × com 8 itens | `BRIEFING:213` | `warden-clip/SKILL.md:306` |
| Duas definições de "edit": na batida × com movimento de escala | `warden_prefs.py:51` | `warden-style/SKILL.md:162` |
| Escreva a lista de planos × o renderizador descarta a lista de planos | `warden-style/SKILL.md:170` | `warden_media.py:1971` |

E três lugares afirmam ser "o único lugar onde esta regra está escrita" quando
a regra está escrita em quatro:

- A ordem de trabalho diz estar só em `warden-clip`. Está também na persona, em
  `warden-run`, em `warden-shared` e no README.
- A regra de entrega diz estar só na persona. Está também em `warden-clip`, em
  `warden-run` e impressa pelo próprio `warden.py`.
- A persona diz que `warden-shared` descreve todos os comandos. Faltam lá
  `warden tracks`, `warden captions review` e `warden style`.

Além disso, quatro testes em `tests/test_warden.py` são código morto: duas
classes são definidas duas vezes (linhas 2296/2442 e 2361/2507) e a primeira
definição nunca roda. Um teste da grade de batida se perdeu assim.

---

## 8. A linha de base, em uma tabela

| Medida | Hoje | Alvo |
|---|---|---|
| Primeiro clipe na mão da pessoa | 9min42s a 18min21s | menos de 2min |
| Lote de 2 completo | 10min56s a 20min39s, e só após cobrança | menos de 4min, sem cobrança |
| Mensagens do agente por tarefa de 2 cortes | 11 a 16 | 3 |
| Idas e voltas desperdiçadas | 9 em 3 conversas | 0 |
| Clipes que chegaram sozinhos | 0 de 2, três vezes seguidas | 2 de 2 |
| Texto obtido | transcrição de 3min46s | legenda publicada em 5s |
| Trocas de cena na batida, num edit | 4 de 12, no nível do acaso | erro < 80ms |
| Edit com legenda | não | sim |
