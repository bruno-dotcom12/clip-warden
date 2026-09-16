# Re-registro no Agent Index

Este arquivo existe porque o registro **não roda dentro do container**. Ele roda
no host, no checkout, com o `./plow-credentials` que o `plow-agents mint`
escreveu. Nada aqui é executado por um agente: é para o dono copiar e colar.

## O que está errado no registro de hoje

Lido em 15/09/2026 em `https://agent-index-server.vercel.app/v1/agents`:

| campo | valor hoje |
| --- | --- |
| `blurb` | **cortado no meio da frase**, em "It pulls only from the archive " |
| `blessed_at` | `""` — ou seja, **não verificado** |
| `has_video` | `false` |
| `image_count` | `0` |
| `users` | 1 |
| `install_success` | 50 |
| `install_url` | `.../blob/main/docs/INSTALL.md` |

O `blurb` foi truncado em 300 caracteres pelo servidor. Registrar de novo
sobrescreve o campo; o servidor trata campo ausente como "deixe o que está" e
`""` como "apague".

## O blurb novo

**267 caracteres** (o limite é 300; contado com `len()` em Python, não
estimado — e `isascii()` verdadeiro, então nada se perde no caminho):

```
Send a video link and what you want. It finds the moments that stand alone, cuts them 9:16 with the captions burned in, and checks each against the campaign's rules. The files come back in the chat; with your publishing key, it posts them to your own YouTube channel.
```

Ele descreve o que o agente faz **de verdade**: recebe um link e um pedido,
acha os momentos, corta 9:16 com a legenda já queimada, confere contra as regras
da campanha, entrega os arquivos na própria conversa — e **publica no canal do
YouTube da pessoa quando ela liga a chave**.

A última oração é condicional de propósito. Publicar de verdade depende de uma
chave de um intermediário de publicação com app auditado pelo YouTube, e a conta
é de quem instala. **Sem a chave, o comando fica desligado** e o agente só
entrega o arquivo na conversa. "with your publishing key" é o que impede o blurb
de prometer para um estranho uma coisa que ele não tem. Não tire essa oração.

A chave tem DUAS entradas desde 16/09/2026, e o texto acima diz "a chave" em vez
de nomear a variável por isso: `WARDEN_POST_API_KEY` no ambiente, que é como o
dono do agente põe a dele, e o arquivo desta máquina, escrito por
`warden post setkey` a partir da chave que a própria pessoa cola na conversa. A
segunda existe porque na nuvem da Plow não há `.env` nem `compose.yml` — sem ela,
quem instala pela Plow não teria por onde pôr uma chave própria, e o blurb ficaria
condicional a uma coisa que só o dono do agente pode ligar.

> **Escrito em 15/09/2026.** O que este blurb afirma e que precisa continuar
> verdadeiro: (1) a legenda é queimada no clipe, não é faixa opcional;
> (2) o corte é 9:16; (3) existe conferência contra as regras da campanha;
> (4) os arquivos voltam na conversa; (5) `warden post youtube` publica público
> no canal da pessoa quando há chave configurada, pelo ambiente ou pelo arquivo
> desta máquina. O item (5)
> foi **medido** em 15/09/2026 com um envio real: a API do YouTube devolveu
> `privacyStatus: public` e a página abre deslogada
> (`https://www.youtube.com/watch?v=lx9J_hD7nGA`, canal Pod Cortes). Se
> qualquer um dos cinco deixar de valer, o blurb mente e tem de ser reescrito
> antes do próximo registro.
>
> Atenção ao que o blurb **não** diz: `warden youtube publish`, o caminho da API
> direta deste projeto, sobe TRANCADO como privado porque o projeto não passou
> pela auditoria. São comandos diferentes e o blurb fala só do que publica.

## O comando, exato

No host, dentro do checkout (`/Users/brunoarantes/Projetos/clip-warden`), com o
`plow-credentials` já mintado:

```sh
cd /Users/brunoarantes/Projetos/clip-warden

# O mesmo commit que a imagem usa (vendor/client.pin). O arquivo já está
# no .gitignore, então ele fica solto aqui sem entrar no repositório.
curl -O https://raw.githubusercontent.com/plow-pbc/agent-index-client/f900ff144076f0a766584b6ec4d0993600779b16/standalone/agent_index_client.py

set -a; . ./plow-credentials; set +a

python3 agent_index_client.py --register --agent clip-warden \
  --name "Clip Warden" \
  --blurb "Send a video link and what you want. It finds the moments that stand alone, cuts them 9:16 with the captions burned in, and checks each against the campaign's rules. The files come back in the chat; with your publishing key, it posts them to your own YouTube channel." \
  --repo "https://github.com/bruno-dotcom12/clip-warden" \
  --install-url "https://github.com/bruno-dotcom12/clip-warden/blob/main/docs/INSTALL.md"
```

`set -a; . ./plow-credentials; set +a` é o que o README do `plow-agents` manda:
exporta `PLOW_API_BASE` e `PLOW_AGENT_TOKEN` para o processo. O registro é
recusado a uma chave que o próprio cliente tenha emitido — ele exige o token do
Plow, que só o Plow avaliza.

## O vídeo e as imagens

São **duas linhas a mais no mesmo comando**, e só o dono pode produzir o
conteúdo delas.

```sh
  --video "Q_RAgwbsjGw" \
  --image "https://bruno-dotcom12.github.io/clip-warden/img/corte-1.png" \
  --image "https://bruno-dotcom12.github.io/clip-warden/img/corte-2.png"
```

Duas armadilhas medidas no código do cliente
(`standalone/agent_index_client.py`, linhas 839-847):

- **`--video` recebe o ID do vídeo no YouTube, NÃO a URL.** De
  `https://www.youtube.com/watch?v=Q_RAgwbsjGw`, o que entra é `Q_RAgwbsjGw`.
  O cliente recusa e sai com erro se houver `/` ou `:` no valor — a página do
  Index embute `youtube-nocookie.com/embed/<id>`, e uma URL ali vira um player
  quebrado numa página pública.
- **`--image` recebe uma URL pública**, uma por imagem, repetindo a flag. Não é
  caminho de arquivo local: o Index não sobe nada, só guarda o endereço. O
  GitHub Pages deste repositório já serve HTTPS
  (`https://bruno-dotcom12.github.io/clip-warden/`), então colocar os PNGs em
  `docs/img/` e empurrar para `main` é suficiente.

O que ainda falta e só o dono faz:

1. **Gravar o vídeo** e subir no YouTube (público ou não listado), para ter o
   ID. Um corte de tela do fluxo real — link entra, clipes voltam na conversa,
   e o `warden post youtube` publica.

   Se não der tempo de gravar, já existe um vídeo **público, produzido e
   publicado por este agente**, que serve de vitrine enquanto não houver outro:
   `lx9J_hD7nGA` (canal Pod Cortes, enviado em 15/09/2026). Não é uma
   demonstração do fluxo, é o produto — decida qual dos dois conta melhor a
   história antes de registrar.
2. **Escolher as capturas** dos clipes prontos, salvá-las em `docs/img/` e
   empurrar para `main`, para que as URLs acima existam.
3. Rodar o comando com as flags `--video` e `--image` incluídas. Rodar sem elas
   é seguro: campo ausente deixa o que está no registro.

## `blessed_at` vazio

`blessed_at: ""` significa **não verificado pela organização do Index**. Não é
algo que este comando preenche e não há flag para isso. Registrar direito, com
vídeo e imagens, é o que dá a eles o que olhar.
