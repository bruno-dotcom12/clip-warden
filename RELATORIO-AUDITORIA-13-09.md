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
| Crítico | 2 | 2 | 0 |
| Grave | 5 | 5 | 0 |
| Cosmético | 3 | 3 | 0 |
| Aberto (registrado) | 5 | — | 5 |

Suíte de testes: **60 casos, todos passando, 3,2 s** — antes eram 43 em 0,02 s e
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
