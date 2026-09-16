# O ambiente em que este agente roda

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

Três coisas que parecem defeito e não são, e uma que era. Tudo aqui é medido:
cada afirmação traz o arquivo onde o número está. A evidência bruta do teste 7a
(16/09/2026) está em `logs-7a/`.

## 1. O aviso laranja do Docker Desktop é emulação amd64, e é esperado

Num Mac Apple Silicon o Docker Desktop mostra um aviso laranja de plataforma
neste container. Ele está certo, e não há o que corrigir:

- A imagem base da Plow publica **uma** arquitetura, `linux/amd64`. Por isso o
  `Dockerfile:17` traz `FROM --platform=linux/amd64` explícito — o comentário
  das linhas 12-16 explica: sem o flag acontece exatamente a mesma emulação,
  só que com um aviso que se lê como problema de alguém.
- Medido no 7a: **22 das 22 linhas** de `docker top` começam com
  `/run/rosetta/rosetta` — inclusive o `ffmpeg` (PID 53268) e o gateway do
  Hermes (PID 51300). Fonte: `logs-7a/snapshot-inicial/docker-top.txt`, com
  `Architecture: aarch64` em `docker-info.txt`.

**Não é regressão da etapa 3.** O commit inicial `c466d95` já usava a mesma base
amd64-only, o flag entrou em `2c4bae1` (12/09/2026), e
`logs-15-09/estado.txt` já registrava `PLATFORM linux/amd64` para o container do
agente em 15/09 — antes da etapa 3.

**E não é a causa do teste 7a.** No 7a os dois MP4 ficaram prontos **2min40**
depois de a pessoa mandar o link: o link é a msg 20 da sessão, epoch
1789570313,32 = 14:51:53 (`logs-7a/container/sessao-mensagens.txt`), e o clipe
01 está em `logs-7a/container/warden/entregas.json` com
`"at": "2026-09-16T14:54:33.174942+00:00"` — 159,9 s. O `-02.mp4` tem mtime
14:54 (`logs-7a/container/ls-warden.txt:31-36`), que é a precisão de minuto do
`ls`. (O `ACHADOS-FASE-1.md` escreveu 2min43; a conta dos dois carimbos dá
2min40.) A entrega não faltou por lentidão: os arquivos existiam. O aviso some
sozinho no dia em que a base publicar arm64; se incomodar na tela antes disso, o
lugar é a preferência "Use Rosetta" do próprio Docker Desktop, não o
`compose.yml`.

**O que 2min40 NÃO prova:** que o pipeline é rápido no caso geral. Esse lote
pegou o caminho barato — a legenda veio publicada pelo YouTube e o
faster-whisper não rodou uma única vez (`sessao-mensagens.txt:85`, `# the words
came from: published subtitles (en)`; zero ocorrências de `whisper` nos logs da
sessão). Um link sem legenda publicada volta a pagar transcrição, e esse teste
ainda não foi feito.

## 2. Memória: o teto de 4 GiB precisa de uma VM de 5120 MiB

O número está declarado em dois lugares e `tests/test_ambiente_7a.py` falha se
eles discordarem:

| onde | o quê | valor |
|---|---|---|
| `compose.yml` (`mem_limit`) | teto do container | **4096 MiB** (4g) |
| `compose.yml` + `docs/INSTALL.md` | mínimo da VM do Docker | **5120 MiB** (5 GiB) |
| `install.sh` | avisa abaixo de | **5120 MiB** |

A conta é 4096 de teto + 1024 MiB de folga para o resto da VM.

**O defeito que isso corrige:** no 7a o `mem_limit: 4g` foi aplicado ao
container (`inspect.json`, `HostConfig.Memory=4294967296`) numa VM de
**3.826 GiB = 3918 MiB** (`snapshot-inicial/docker-info.txt`). O Docker aceita
um teto maior que a VM e nunca o honra: `docker stats` imprimiu
`MEM USAGE / LIMIT 1.024GiB / 3.826GiB`
(`snapshot-inicial/docker-stats-1.txt`) — o LIMIT exibido é a VM. Efeito: o
container voltou a ser, na prática, ilimitado, que é o estado que o commit
`81c032f` existiu para acabar. Quem morre nesse arranjo é a VM, não o container.

**Por que levantar a VM e não baixar o teto.** O maior pico já medido neste
projeto é **1815 MiB**. Baixar o teto para caber na VM de hoje daria 2800m, ou
1,54× esse pico, contra os 2,26× de agora — e apertaria justamente o passo mais
pesado, que é a transcrição junto com o render. Ressalva honesta: os dois
lugares que citam 1815 MiB discordam sobre qual carga o produziu
(`RELATORIO-AUDITORIA-13-09.md:108` diz transcrição + corte juntos, em 13/09;
`tests/test_lote.py` diz lote sequencial de dois clipes, em 16/09). O teto não
depende de resolver isso.

**O 1,024 GiB do 7a não é pico.** É UMA amostra de `docker stats`, colhida às
15:03 UTC (`logs-7a/snapshot-inicial/_hora-da-coleta.txt`), num lote que nem
transcreveu. O que ainda falta medir é o pico de verdade: `docker stats
--no-stream` em laço de 1s gravando em arquivo, num link **sem** legenda
publicada — o caminho que paga transcrição. Isso é medição que falta, não
memória que falta.

### Isto está resolvido: a VM foi levantada, e o teto passou a ser honrado

Ainda em 16/09/2026, depois da coleta acima, o dono aumentou a memória do Docker
Desktop. Medido em seguida: `docker info` passa a mostrar **5.785 GiB**, e
`docker stats` imprime

```
MEM USAGE / LIMIT   545MiB / 4GiB
```

O que mudou não é o consumo (545 MiB é folgado dos dois lados): é a coluna
LIMIT. Ela deixou de ser a VM e passou a ser o teto que o `compose.yml` pede.
**O `mem_limit: 4g` só passou a existir de verdade aqui.**

Consequência para quem lê: **não se pede mais memória ao dono desta máquina.**
Os 3,826 GiB citados acima são a medição ANTIGA, guardada porque ela é a
evidência do defeito — não o estado de hoje. E o reinício da VM às 15:07-15:08
UTC que aparece nos logs do 7a (`gateway.previous_unclean_exit`, `prior_pid`
184) foi o dono aplicando essa memória: **não é defeito, e não é OOM**. O que
sobra desse reinício é só a consequência dele, e essa sim é defeito nosso: as
três instalações voltaram juntas, na mesma linha da Plow — é o §5 abaixo.

**Não houve OOM no 7a.** `oom=false`, `restartCount=0`, `ExitCode 0`,
`FinishedAt` zerado (`snapshot-inicial/estado-resumo.txt` e `inspect.json`
linhas 10-16); um único `gateway.start` às 14:37:35 em
`container/logs/gateway-exit-diag.log`; nenhum `SIGKILL`, `Killed` ou
`UNCLEANLY` nos logs. A falta de entrega não foi morte de processo.

Os 5120 MiB continuam sendo o requisito publicado, e continuam na conta
(4096 de teto + 1024 de folga). O que mudou é que a máquina do dono passou a
cumpri-lo: os números medidos nela a partir de agora valem para a configuração
entregue.

## 3. A poda de contexto pode levar as instruções junto

O container escreve no boot, em `image/s6-overlay/scripts/warden-context:60-63`,
`proactive_prune_tokens: 48000` e `proactive_prune_min_reclaim_tokens: 4096` —
confirmado aplicado em `logs-7a/container/config.yaml:61-62`. Não é
configuração da Plow: é nossa.

Às **14:52:50** do 7a, cinco segundos depois de o `lote render` começar, ela
disparou e reclamou 15.618 tokens (`container/logs/agent.log:220` e `:223`:
chamada #12 `in=60747`, chamada #13 `in=45129`). O que ela jogou fora não foi
saída de comando: foram os 34.983 chars do `warden-clip` e os 8.575 do
`warden-run` (`live.db` msgs 37 e 39), com a marca
`[SKILL_PRUNED: content lost in compression]`. Os dois clipes ficaram prontos
1min43 depois disso, e nunca foram entregues.

Consequência de projeto, e ela vale para quem escrever skill neste repo: **o que
o agente não pode esquecer não pode morar só numa skill**. Tem de sair na saída
da ferramenta que ele está rodando naquele instante — saída de ferramenta o
modelo vê, e o texto recém-produzido é o último a ser podado. O texto do
`docs/INSTALL.md`, em "Keeping a long conversation cheap", foi corrigido: ele
prometia que a poda "não tem como dar errado".

O ajuste do limiar e a proteção do `skill_view` são do arquivo
`image/s6-overlay/scripts/warden-context` e não foram feitos aqui.

## 4. O MCP `plow` que não conecta

Ruído conhecido, não é defeito deste repositório e não custou nada à entrega do
7a. O que o log chama de `405` é a resposta ao probe SSE (GET); o erro real é o
`503` do POST — relay sem dispositivo do outro lado, isto é, conta da Plow sem
um Mac com o Latch aberto. Medido: 24 pares POST→503 / GET→405 em
`container/logs/agent.log`, em 6 ciclos. Custo medido: 11,5 s **no boot** (o
gateway só começa 2 ms depois de a conexão MCP desistir) e 0 s na conversa — o
ciclo das 14:53 rodou inteiro dentro de uma chamada de `terminal` de 242,88 s.
Pinar `transport: sse` é a correção errada: deixaria só o GET, que é o que
falha 100% das vezes. O tratamento completo está em `docs/INSTALL.md`, em
"MCP server 'plow' failed initial connection".

**Não há alavanca neste repositório, e é isso que o torna pendência do dono.**
O `PLOW_MCP_URL` vem da resposta da Plow, não do ambiente — `plow-init.py:389`
pergunta, `:996-997` exporta, e `export()` (`:522`) grava em
`/run/s6/container_environment`, que o s6 aplica a todo serviço; uma variável
do `compose.yml` não vence isso, e `grep PLOW_MCP_URL compose.yml Dockerfile`
dá zero. As duas correções que parecem correção não são, e `docs/INSTALL.md`
já as registra: `enabled: false` no `config.yaml` não dura (o `plow-init`
reescreve essa chave a cada boot, `:557`) e, mesmo durando, não removeria o
parágrafo, que é condicionado ao `PLOW_MCP_URL` exportado.

O custo real não é o boot: é que, com um Mac **registrado e offline**, o prompt
do agente carrega um parágrafo dizendo que ele tem as mãos daquele Mac —
navegador, arquivos, cofre — enquanto zero ferramentas `plow_` existem
(`container/logs/agent.log:29`: `plow_chat: Mac skill manifest not fetched
(HTTPError); Latch section carries no skills yet`). É uma promessa que a
instalação não cumpre, dita ao modelo em todo turno, e a única alavanca é a
**conta da Plow**: tirar o Mac do registro, ou instalar o Latch e deixá-lo
aberto. Fica na lista de pendências do dono, ao lado do teste do YouTube por
outra rede e do token do TikTok.

## 5. Duas instalações na mesma linha da Plow — e o aviso que agora sai no boot

Este é o defeito que sobrou do reinício de 15:08, e é defeito nosso.

### O que aconteceu, medido

Às **15:08:1x UTC de 16/09/2026** três containers subiram juntos com a VM:
`warden-7a`, `clip-warden-agent-1` e `clip-warden-nuvem` (`docker ps` às 15:20,
em `logs-7a/ACHADOS-FASE-1.md`, "Achados da SEGUNDA coleta" §8). Dois deles
estavam parados **de propósito** — o dono tinha parado o `clip-warden-agent-1`
às 11:36 — e voltaram sozinhos, porque `restart:` os traz de volta quando a VM
volta.

Os três montavam o **mesmo** `./plow-credentials`, logo os três respondiam o
mesmo chat `cht_knqXxEnmlk01lLC_--xjPw`. Às 15:16:45 alguém escreveu "Publish
the last clip in YouTube". Quem atendeu **não foi o agente com quem a pessoa
estava falando**: foi o `clip-warden-agent-1`, o único com
`WARDEN_POST_API_KEY` e `WARDEN_POST_PROFILE=Clip-Warden`. Às 15:17:23 ele
publicou num canal real. Ninguém tinha pedido àquele agente.

O `warden-7a` fez a coisa certa e recusou (`warden post status` → "the
publishing intermediary is OFF: there is no key on this install", msg 94). A
colisão só foi descoberta às **15:20**, com `docker ps` de perícia.

### Por que não existe detecção, e o aviso não finge que existe

Saber, de dentro de um container, que **outra instalação está de pé agora**
exige um lock ou um handshake. Não há nenhum dos dois, e a resposta da Plow que
nomeia o chat desta instalação não diz quem mais está nele. Um aviso que
afirmasse "outra instalação está rodando" seria mentira em toda instalação
sozinha — e um aviso que mente é um aviso que se aprende a pular.

### O que o aviso faz

`image/s6-overlay/scripts/warden-linha`, um oneshot do s6 irmão do
`warden-context`, com `dependencies.d/plow-init` (para o
`PLOW_HOME_CHANNEL` já estar exportado) e `with-contenv` no `up`. Ele roda como
o agente, nunca como root. Sem rede, sem lock, sem arquivo escrito, sem socket
do Docker: lê o ambiente, pergunta se um caminho existe, fala e sai 0.

Ele diz três coisas verificáveis, e nomeia o chat em todas — sem o id, três
containers registram "linha compartilhada" e ninguém consegue casar as linhas:

1. **a credencial veio de um arquivo desta máquina** (`/var/lib/plow/
   credentials.host`, que o `compose.yml` monta de `./plow-credentials`), então
   qualquer outro container que monte o mesmo arquivo cai no mesmo chat. Na
   nuvem da Plow esse arquivo não existe — a credencial vem pelo ambiente — e
   ali o serviço **não fala nada**;
2. **se esta instalação publica**: com `WARDEN_POST_API_KEY` e um
   `WARDEN_POST_PROFILE` fixado à mão, o aviso sai como ATENÇÃO e **nomeia o
   perfil**, porque o perfil é o canal. Sem chave, ele diz o contrário, que é o
   estado de quem instala: uma colisão custa uma resposta repetida, não um
   vídeo no ar;
3. **que ele não sabe o resto**, com todas as letras.

A chave em si nunca aparece: só a existência dela. `tests/test_ambiente_7a.py`
cobra isso.

### Onde este aviso NÃO chega, e é a metade que falta

No log do Docker. Entre 15:08 e 15:16:54 do 7a **ninguém leu log nenhum**, e o
boot deste agente despeja, no mesmo intervalo, 24 pares POST→503/GET→405 e a
pilha inteira do s6 — uma linha nossa nasce enterrada ali. O aviso de boot é
barato e honesto, e é para a perícia depois; não conte com ele na hora.

A superfície que vale é a saída do `warden post`, que o agente lê no exato turno
em que vai publicar e repassa para a pessoa. Isso é
`warden-shared/scripts/warden.py`, e **não foi feito nesta faixa** — está
pedido. Vale a regra de projeto do §3: o que não pode ser esquecido sai na saída
da ferramenta que está rodando naquele instante.

### O que continua sem conserto possível daqui

Que um `docker compose up` esquecido volte junto com a VM. Isso é do host, não
da imagem. O que o aviso muda é que, quando acontecer, está escrito.

## Onde mora o "porquê"

As personas e as SKILL.md deste repo carregam **regra curta**, porque tudo o que
está nelas ocupa contexto em toda mensagem e é o primeiro a ser podado quando a
conversa cresce. A explicação, o histórico e o "medido em tal dia" saem de lá e
vêm para cá, ou para comentário no código junto da linha que os obedece. Se você
está prestes a escrever um parágrafo de justificativa dentro de uma persona ou
de uma skill: ele pertence a este arquivo.
