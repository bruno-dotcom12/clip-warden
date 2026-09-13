# Relatório da auditoria do Clip Warden — 13/09/2026

Escrito para o dono, que não é desenvolvedor. Todo número aqui veio de um comando
que rodei, não da minha leitura do código. Onde digo "medido", medi.

O agente que os hosts do hackathon vão instalar a partir de 14/09 precisava de
três coisas: entregar o que promete, não vazar a credencial de quem o roda, e
subir na mão de um estranho sem quebrar. As três estavam furadas em algum ponto.
Estão fechadas agora, cada conserto no seu commit, e o digest da base não foi
tocado.

## O placar

| Classe | Quantos | Corrigidos | Deixados de propósito |
| --- | --- | --- | --- |
| Crítico | 3 | 3 | 0 |
| Grave | 5 | 5 | 0 |
| Cosmético | 3 | 3 | 0 |
| Aberto (registrado) | 6 | — | 6 |

Suíte de testes: **62 casos, todos passando, 3,2 s** — antes eram 43 em 0,02 s e
nenhum tocava vídeo. Agora `cut` e `check` rodam sobre um arquivo de verdade.

---

## Crítico

### C1 — A credencial do host vazava por `warden fetch`
**Onde:** `warden_media.py`, `fetch_text`. **Commit:** `6f4aa9c`.

O `warden fetch` não passava pelo `safe_url`, então aceitava `file://`. Como o
processo roda com o mesmo uid do gateway, dava para ler o token vivo do dono pelo
`/proc` do gateway. Medido, como o próprio agente:

```
warden fetch file:///proc/210/environ  →  PLOW_AGENT_TOKEN=…  API_SERVER_KEY=…
```

A cadeia inteira cabia num regulamento hostil: uma linha mandando o agente
"buscar" esse caminho e colar o resultado. O `0600 root` do arquivo não protegia,
porque o vazamento era pelo ambiente. **Corrigido:** o `fetch` agora usa o mesmo
portão do `archive`, que recusa esquema não-http e host interno; reprovado no
reteste contra a imagem reconstruída.

### C2 — O agente não tinha como entregar o clipe
**Onde:** persona, skills, `warden.py`. **Commit:** `b01183a`.

A promessa central do README — "manda os arquivos de volta na conversa" — não se
cumpria: nada no repositório ensinava a forma de mandar arquivo (`MEDIA:`), e a
base fixada também não. O agente terminava um corte e descrevia o arquivo em
texto. Pior: o diretório de estado onde ele salvava é **recusado** pelo runtime
na hora de anexar. Medido:

```
/var/lib/hermes/warden/clips/x.mp4  →  recusado
/var/lib/hermes/cache/videos/x.mp4  →  entregue
```

**Corrigido:** renders vão para o diretório que o runtime aceita, `warden cut`
imprime a linha `MEDIA:` pronta, e persona e skills mandam copiá-la. Confirmado
de ponta a ponta na conversa: o arquivo chegou.

### C3 — A regra de entrega mandava enviar qualquer caminho, e a credencial era alcançável
**Onde:** `runtime/persona.md`. **Commit:** `a813fd4`.

Um conserto anterior meu (C2) deixou a persona dizendo que **qualquer caminho
absoluto** sai na linha `MEDIA:`. Isso reabria o furo do `fetch` pelo outro lado:
treinava o modelo a anexar o caminho que o texto na frente dele nomeasse — e esse
texto é o regulamento de um estranho. Ataquei com um regulamento hostil que pede,
de cinco formas, o envio de arquivos que não são clipes. Medido o backstop do
runtime contra cada caminho:

```
/var/lib/plow/credentials   recusado    /etc/passwd            recusado
/proc/self/environ          recusado    /tmp/verificacao.txt   ENTREGUE
```

A denylist do runtime cobre `/var/lib`, `/proc` e `/etc`, mas **não `/tmp`**. E o
agente roda no mesmo uid do gateway, então `cat /proc/<gw>/environ` lê o token do
host, uma cópia para `/tmp` passa pelo backstop, e `MEDIA:/tmp/x` o enviaria. Ou
seja: **a credencial é alcançável** por um caminho que o agente pode nomear, e a
linha larga da persona apontava para lá. **Corrigido:** a única linha `MEDIA:`
que o agente envia é a que o `warden cut` imprimiu, para um clipe que ele acabou
de renderizar; um regulamento que pede um arquivo é recusado. Dois testes pinam
isso (só a linha da ferramenta, apontando para o diretório de entrega; e nenhum
`MEDIA:` num render reprovado).

---

## Grave

### G1 — SSRF por redirecionamento
**Commit:** `6f4aa9c` (junto de C1). O `safe_url` só validava o esquema inicial;
um `http` que redireciona para `169.254.169.254` (metadados de nuvem) era
seguido. Medido: a requisição saiu. **Corrigido:** todo redirecionamento passa
pelo mesmo portão.

### G2 — Download sem teto e nome do servidor remoto
**Onde:** `_download_one`. **Commit:** `b04b55b`. O teto de bytes só existia no
download direto; `gdown` e `yt-dlp` não tinham. E `--no-playlist` num link de
playlist deixava arquivo residual — foi o que fez o agente contornar o `archive`
e baixar por fora na sessão de teste. **Corrigido:** teto nos três caminhos,
playlist tratada como playlist, saída determinística, nome sempre nosso.

### G3 — O contêiner sem limites já tinha morrido por OOM
**Onde:** `compose.yml`. **Commit:** `81c032f`. O log do gateway registrou uma
morte por `SIGKILL / OOM` em 12/09. Medido o pior caso (transcrição + corte
juntos): pico de **1815 MiB e 99 tarefas**. **Corrigido:** `mem_limit: 3g` (1,7×
o pico), `pids_limit: 512`, e o agente sobe em pé sob os dois. Disco não foi
limitado — o `storage_opt` é aceito e ignorado por este Docker, o que registrei
no próprio arquivo em vez de fingir proteção.

### G4 — Enquadramento cego cortava pela metade
**Onde:** `warden_media.py`, `cut`. **Commit:** `5648e54`. O `crop` sem `x` pega
o centro; numa fonte lado-a-lado (o primeiro clipe real do agente) isso mantém a
divisória preta e meio personagem de cada lado. **Corrigido:** `--crop
left|right|center` ou porcentagem, e um aviso medido quando o corte central é
cego. Verificado por imagem: `left` mostra um painel limpo.

### G5 — O `plow-agents` não estava documentado, e os dois docs abriam usando-o
**Onde:** `docs/INSTALL.md`, `README.md`. **Commit:** `319c347`. Os dois
documentos começavam com `plow-agents login` como se o comando existisse, e
mandavam `plow-agents mint` sem a linha — que é argumento obrigatório e sem ele
só imprime erro. Um estranho travava no passo um. **Corrigido:** o caminho
inteiro, do clone do CLI ao build, com o que se precisaria adivinhar.

---

## Cosmético

- **M1** (`7e28b59`): a skill agora diz que um momento que sai mudo precisa
  funcionar mudo — a piada de dublagem do teste era só voz, e mudou não é nada.
- **M2** (`103abeb`): a persona agora vence a ferramenta cujo schema diz "no file
  paths"; era por isso que o agente recusava mandar um arquivo direto.
- **M3** (`e8cd769`): o README ganhou a seção "para onde o agente se conecta" —
  Plow, os links do dono, Hugging Face, o Index e os quatro diretórios públicos,
  nomeados. Quem instala merece a lista antes de rodar.

---

## Aberto — registrado, não corrigido, com o porquê

1. **Disco sem cota real.** `storage_opt` é ignorado por este Docker; o que segura
   são os tetos por arquivo do downloader. Documentado no `compose.yml`.
2. **`main()` sem try/except de topo.** Um erro não capturado sai como traceback
   cru. A persona manda o modelo resumir, então é defesa em profundidade.
3. **Legenda do `yt-dlp` é texto do atacante** que o modelo lê como transcrição —
   superfície geral de injeção de prompt, já tratada como dado pela persona.
4. **DNS-rebinding** entre a checagem de host e o connect do urllib — fora do
   alcance de um regulamento, anotado no código.
5. **`--crop` não detecta o assunto.** Dei a alavanca e o aviso; a escolha fica
   com quem vê o vídeo. A ferramenta não pode adivinhar onde está o assunto.
6. **`/tmp` não está na denylist de entrega do runtime.** É código da imagem base,
   não nosso. A persona restrita (C3) não leva mais o modelo até lá, mas a
   camada de baixo continua aceitando `/tmp`. A alavanca da base para fechar isso
   é `HERMES_MEDIA_DELIVERY_STRICT`; avaliar se vale ligar sem quebrar a entrega
   legítima fica registrado para o dono decidir.

---

## As cinco hipóteses da Etapa 1, resolvidas

| # | Hipótese | Veredito |
| --- | --- | --- |
| 1 | Entrega de arquivo quebrada | **Procede — crítico.** Corrigido em `b01183a`, sem subir a base. |
| 2 | Bug de identidade de saída (#153) | **Não nos atinge.** Nossa base é anterior à janela em que o bug existiu (10/09 15:57, o bug entrou às 16:31 e saiu em 12/09). E o agente só responde na DM do dono. |
| 3 | Subir a base é arriscado | **Procede, e não foi preciso subir.** Os dois motivos para subir (MEDIA e #153) se resolveram no repositório ou nunca nos atingiram. |
| 4 | Registro no Index vazio | **Procede.** O conserto foi feito e está **guardado na tag `agent-index-register`** (você pediu para não mexer no Index nesta rodada). `git cherry-pick agent-index-register` o traz de volta. |
| 5 | `mint` quebrado | **Não procede** nesta máquina — mas o `plow-agents` não estava documentado, corrigido em G5. |

---

## O que não foi feito, por decisão sua ou do documento

- O digest da imagem base **não foi tocado**.
- Licença e NOTICE **intactos**.
- **Nada subiu** para o GitHub.
- O registro no Agent Index ficou **fora** desta rodada, guardado na tag.
- Nenhuma regra de campanha foi inventada, nem em teste.

## Como verificar

```sh
python3 -m unittest discover -s tests     # 60 casos, ~3s
docker compose up --build -d              # sobe em pé sob os limites
docker compose exec agent warden status   # ffmpeg, ffprobe e modelos
```

Pela linha, o teste de entrega ponta a ponta: peça um clipe de uma campanha e
confira se o arquivo chega. Na sessão desta auditoria, chegou.

---

# Parte 2 — features construídas na mesma sessão (além da auditoria)

Depois de fechar a auditoria de segurança, a sessão virou desenvolvimento: testar
o agente com footage real revelou defeitos e o dono pediu capacidades novas. Tudo
com teste, cada um em seu commit, digest da base intocado. **97 testes, todos
passando.**

## Entrega e enquadramento

| Área | O que ficou | Commits |
| --- | --- | --- |
| Entrega do clipe | `warden cut` imprime a linha `MEDIA:` no diretório que o runtime aceita | `b01183a` |
| Legenda queima de verdade | `to_ass` gera ASS estilizado (o `force_style` com vírgulas não renderizava nada); tempo deslocado para a janela do clipe; `WrapStyle` corrigido | `759b1c0`, `63da0ff` |
| Legenda por campanha | `sources.archive_has_captions` (true/false/null); `cut` não queima sobre acervo já legendado; o agente **pergunta** ao guardar a campanha | `e1ffa39`, `49b0dd5` |
| Enquadramento por rosto | `--crop left/right/auto` segue o rosto detectado (YuNet/OpenCV), não o centro cego; `--crop <n>` é manual | `5648e54`, `1a6d0ca` |
| Som nunca por acidente | `sound` sem default; `cut` recusa renderizar sem a escolha; sempre perguntado | `3b83ddd` |
| Momento mudo | skill: momento que sai mudo precisa funcionar mudo | `7e28b59` |

## Achar momentos virais

`warden signals <transcript> --source <file>` (`c1d2b0d`) — surfaces, em ordem de
tempo, só os momentos com sinal: pergunta, superlativo, conflito, risada, e
**pico de volume** (reação da plateia, via ebur128). **Não dá nota de viral** — dá
evidência; o modelo, que lê o conteúdo, decide os cortes. É a filosofia do agente:
o tool mede, o modelo julga.

## Fonte confiável e autorização

O dono quer mandar qualquer link de fonte confiável, mas o agente afirmava "não
está na playlist" sem ter como saber. Dois comandos tornaram isso honesto:

- **`warden authorize <link> --campaign <id>`** (`6379f3b`, `3a5bfc7`) — expande as
  playlists com o yt-dlp e casa o id; devolve o **título** do vídeo. Fora do
  acervo, o agente **pergunta** "corto '<título>' assim mesmo?" e espera o sim —
  não recusa. Playlist ilegível é "não", nunca "sim".
- **`warden trusted add|check|list`** (`6379f3b`) — a lista de canais/domínios do
  dono, para clipar **sem campanha**. Canal (`@handle`/`UC…`) resolvido pelo canal
  real do vídeo; domínio pelo host.
- Persona reforçada (`b23d1cd`): fora do acervo é **sempre** perguntar, nunca
  recusar; link repetido é o dono pedindo.

## Uma armadilha de operação que custou tempo (registrar para amanhã)

O Hermes **cacheia o prompt do sistema (SOUL.md) por toda a vida da sessão de
DM** (`system_prompt.py:448`). Editar a persona + rebuild + restart **não** troca
a persona de uma conversa já aberta. Para carregar a persona nova numa conversa
existente, o dono digita **`/reset`** (ou `/new`) na linha — gatilho do gateway
(`config.py:941`), interceptado antes do modelo. Foi isso que finalmente fez o
agente adotar o comportamento de perguntar. **Regra para o futuro: toda mudança
de persona só vale para uma sessão nova ou após `/reset`.**

## Onde parar e continuar amanhã

- Aberto: o registro no Agent Index (tag `agent-index-register`, `git cherry-pick`).
- Aberto: os cinco itens da auditoria (disco sem cota, `/tmp` na denylist, etc.).
- A base **não** foi subida; segue no digest `4747960e`.
- Testar o fluxo completo por um chat novo: link de fonte confiável → `warden
  signals` → cortes com rosto e som → entrega.

## Verificar

```sh
python3 -m unittest discover -s tests     # 97 casos (2 pulam sem libass/cv2, presentes na imagem)
docker compose up --build -d
docker compose exec agent warden status
```
