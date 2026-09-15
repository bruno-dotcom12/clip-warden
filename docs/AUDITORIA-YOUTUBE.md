# Auditoria da YouTube API — respostas prontas

Este arquivo é para copiar e colar no formulário
`https://support.google.com/youtube/contact/yt_api_form`.

Nada aqui é um agente que preenche o formulário. O Google exige que quem envia
seja a pessoa responsável pelo projeto no Google Cloud, logada na conta dele.

## Antes de abrir o formulário: leia isto

**NÃO EXISTE PRAZO.** O Google não publica nenhum tempo de resposta para a
auditoria da YouTube API, e este arquivo não vai inventar um. Pode voltar em
dias, pode voltar em meses, pode voltar pedindo mais coisa. Enquanto não voltar
aprovada, **todo vídeo enviado pela API continua trancado como privado**, sem
recurso e sem o dono conseguir torná-lo público — é por isso que o caminho que
funciona hoje é reenviar o arquivo pelo app ou pelo site do YouTube.

**A VERIFICAÇÃO DA TELA DE CONSENTIMENTO DO OAUTH É OUTRA COISA.** São dois
processos separados, na mesma conta do Google, com formulários diferentes:

| | verificação do OAuth (Google Cloud) | auditoria da YouTube API |
| --- | --- | --- |
| onde | Google Cloud Console → tela de permissão OAuth | `support.google.com/youtube/contact/yt_api_form` |
| o que resolve | o aviso "app não verificado" e o **limite de 100 usuários** enquanto não verificada | o vídeo enviado pela API ficar **trancado como privado** |
| uma substitui a outra? | **não** | **não** |

Passar na verificação do OAuth e não passar na auditoria continua dando vídeo
privado. Passar na auditoria e não verificar o OAuth continua dando a tela de
aviso e o teto de 100 usuários. Se o objetivo é publicar de verdade, as duas
precisam ser feitas.

---

## O que falta, e só o dono pode fazer

Em linguagem de quem não programa. Nada disto dá para um agente resolver.

### 1. Terminar a limpeza da credencial vazada

**O que já foi feito, em 15/09/2026:**

- O cliente OAuth antigo **já foi trocado**. O par novo não está no
  repositório nem na imagem.
- O código já foi corrigido para nunca mais embutir credencial no build: o
  `Dockerfile` não tem mais `ARG`/`ENV` para isso, o
  `.github/workflows/publish.yml` não passa mais `build-args`, e o par entra
  pelo `environment:` do `compose.yml`, da máquina de quem instala.

**Por que a limpeza ainda não acabou.** O par ANTIGO ficou gravado dentro das
imagens públicas já publicadas no ghcr, onde foi lido com um pull **anônimo**.
Apagar do código não apaga do que já foi para o registro. Isso é a política
III.D.1 dos Serviços de API do YouTube, que proíbe literalmente
"embed your API Credentials in open source projects", e é exatamente o tipo de
coisa que uma auditoria olha.

Falta:

1. Apagar os segredos `WARDEN_YT_CLIENT_ID` e `WARDEN_YT_CLIENT_SECRET` nas
   configurações do repositório no GitHub: **Settings → Secrets and variables →
   Actions**. Nada mais os usa, e enquanto existirem eles são uma porta aberta
   para um build futuro voltar a embuti-los por engano.
2. Apagar no ghcr as versões publicadas que carregam o par antigo — **e a ORDEM
   importa, senão a instalação de quem chegar no meio quebra.**

   Estado medido em 15/09/2026 às 18:50, por pull **anônimo** do registro (sem
   nenhuma credencial, que é o ponto):

   | versão | ainda carrega o par |
   | --- | --- |
   | `latest` | sim |
   | `sha-acb379e064c73bb0d781a2993f9bb8521fa7973e` | sim |
   | `sha-7873305d7f502a829293fc3f36fe337404a16437` | não |

   O valor lá dentro está **morto**: o cliente foi apagado no Google e a
   renovação devolve `deleted_client`. Ele não abre nada. O que sobra é o sinal
   ruim de ter credencial publicada num projeto que está pedindo auditoria.

   A ordem:

   1. commitar este trabalho — o `Dockerfile` e o `.github/workflows/publish.yml`
      já não embutem mais nada;
   2. deixar o CI publicar uma imagem nova, que sai limpa e assume a tag
      `latest`;
   3. **só então** apagar as versões antigas, em
      `https://github.com/users/bruno-dotcom12/packages/container/clip-warden/versions`.

   Apagar o `latest` antes do passo 2 deixa o `install.sh` sem imagem para puxar.

   Para conferir sozinho, depois, sem instalar nada: o mesmo pull anônimo lê o
   `Env` da configuração da imagem. Se `WARDEN_YT_CLIENT_SECRET` não aparecer
   com valor, está limpa.
3. Guardar o par NOVO num `.env` ao lado do `compose.yml`, com `chmod 600`.
   `docs/INSTALL.md` tem o passo a passo.
4. Rodar `warden youtube connect` de novo. A autorização guardada em
   `youtube.json` foi emitida pelo cliente antigo e parou de valer junto com
   ele.

### 2. Consertar o site — o revisor vai ler essa página

O endereço público do projeto é `https://bruno-dotcom12.github.io/clip-warden/`
(já existe, já é HTTPS, medido: responde 200). Mas hoje ele **não serve** como
resposta do formulário, por dois motivos:

- Ele **não fala em YouTube em lugar nenhum**. O revisor vai abrir a página para
  ver o que o app faz com a API do YouTube, e não vai achar nada.
- Ele **promete Instagram** ("Clip Warden can publish a Reel to your own
  Instagram professional account") e **isso não existe no agente**. Não há
  comando de Instagram. Prometer um recurso que não existe, numa página que o
  revisor lê, é o caminho mais curto para uma recusa.

O que fazer: editar `docs/index.html` para (a) descrever o que o
`warden youtube` faz, (b) tirar a promessa de Instagram. Depois empurrar para
`main` — o GitHub Pages republica sozinho.

### 3. Consertar a política de privacidade — isto é obrigatório

`https://bruno-dotcom12.github.io/clip-warden/privacy.html` existe e é HTTPS,
mas **não menciona o YouTube**. As políticas dos Serviços de API do YouTube
exigem que a política de privacidade do app diga que ele usa a API do YouTube e
aponte para os documentos do Google. Sem isso a auditoria é recusada.

Peça para acrescentar em `docs/privacy.html` uma seção com este conteúdo (em
inglês, como o resto da página):

> **YouTube.** Clip Warden uses YouTube API Services to upload videos to a
> channel you connect yourself. By using that feature you agree to the
> [YouTube Terms of Service](https://www.youtube.com/t/terms). Google's use of
> information it receives is described in the
> [Google Privacy Policy](http://www.google.com/policies/privacy). You can
> revoke Clip Warden's access to your YouTube account at any time through the
> [Google security settings page](https://security.google.com/settings/security/permissions).
> Clip Warden stores the resulting token only on your own computer, in the
> container's volume, and it is never sent anywhere else.

E acrescentar o YouTube nas duas listas que já existem na página: a de
permissões pedidas (escopo `youtube.upload`) e a de "How to revoke access".

### 4. As capturas de tela

O formulário pede imagens. São três, tiradas por você, da sua tela:

1. **A tela de consentimento do OAuth** — é o que aparece quando você roda
   `warden youtube connect` e abre o endereço que ele imprime, no navegador. A
   captura tem que mostrar o nome do app e a permissão sendo pedida.
2. **A tela onde o upload acontece** — aqui não há tela: é uma conversa. Tire a
   captura da conversa no Plow, mostrando você mandando o pedido e o agente
   respondendo com o resultado do envio, **incluindo o aviso de que o vídeo
   subiu privado**. Explique isso na legenda, porque o revisor espera uma
   interface e vai ver um chat.
3. **O resultado no YouTube Studio**, com o vídeo lá e a visibilidade privada.

Guarde os arquivos numa pasta sua, no formato que o formulário pedir (ele aceita
anexo; se pedir link, suba num Google Drive e deixe "qualquer pessoa com o link
pode ver").

### 5. A conta de demonstração

O formulário pede credenciais de uma conta que o revisor possa usar para ver o
app funcionando. **Não mande a sua conta principal.**

Este app não tem site nem login próprio — é um container que roda na máquina de
quem instala. Então o que a conta de demonstração significa aqui é:

- uma **conta do Google descartável**, com um canal do YouTube criado, cuja
  senha você vai escrever no formulário; e
- a instrução de como chegar ao app, que é o `docs/INSTALL.md`.

Crie a conta, crie o canal, faça o `warden youtube connect` com ela pelo menos
uma vez para provar que funciona, e só então preencha o formulário. Se o
formulário deixar, diga por escrito que o app não é um site e que a conta serve
para o revisor entrar no YouTube e conferir o vídeo que a demonstração enviou.

---

## As respostas, seção por seção

O formulário tem 7 seções. Os títulos mudam de tempos em tempos; o que importa é
o conteúdo. Onde estiver `<PREENCHER>`, é coisa que só você tem.

### 1. Sobre você / tipo de aplicação

- **A aplicação pertence a uma organização ou a uma pessoa física?**
  **Pessoa física (individual developer).** Não há empresa por trás disto. É um
  projeto pessoal, de código aberto, licença MIT.
- **Nome do desenvolvedor:** `<PREENCHER: seu nome completo>`
- **E-mail de contato:** o mesmo da conta do Google Cloud.
- **País:** Brasil.

### 2. Identificação do projeto de API

- **Nome do projeto no Google Cloud:** `<PREENCHER: o nome que aparece no
  console>`
- **Número do projeto / Project number:** `<PREENCHER: Google Cloud Console →
  página inicial do projeto>`
- **Client ID do OAuth:** `<PREENCHER: o do cliente NOVO, criado em 15/09 — é o
  que termina em .apps.googleusercontent.com, e está no seu `.env`>`
- **Tipo de cliente OAuth:** TVs e dispositivos com entrada limitada
  (device authorization grant).
- **APIs usadas:** YouTube Data API v3, e somente ela.
- **Endpoints usados:** `videos.insert` (upload) e `channels.list` com
  `mine=true` (para confirmar em qual canal o upload vai cair, e mostrar isso à
  pessoa antes de enviar).
- **Escopos pedidos:** `https://www.googleapis.com/auth/youtube.upload` e
  `https://www.googleapis.com/auth/youtube.readonly`.

### 3. Nome e descrição da aplicação

**Nome:** Clip Warden

**Descrição (para copiar):**

> Clip Warden is an open-source agent (MIT) that a person runs on their own
> computer, inside a Docker container. It turns a long video into short vertical
> clips: it transcribes the source, selects the moments that stand on their own,
> renders 1080x1920 clips with the captions burned in, and checks each clip
> against the written rules of the paid clipping campaign the person is
> submitting to — duration, aspect ratio, required hashtags, audio policy.
>
> The YouTube Data API is used for one thing only: uploading a clip the person
> asked to upload, to the person's own channel, after that person has approved
> a device code on Google's own screen. Clip Warden never reads a feed, never
> reads analytics, never touches a channel other than the one the person
> connected, and never uploads without an explicit command naming the file.
>
> There is no Clip Warden server and no Clip Warden account. Every credential
> lives on the user's own machine.

### 4. URL de acesso principal (HTTPS obrigatório)

```
https://bruno-dotcom12.github.io/clip-warden/
```

Já é HTTPS e já responde (conferido). **Só depois de corrigida** como diz o
item 2 acima.

Se o formulário insistir num endereço onde o revisor "usa" o app, escreva junto:

> Clip Warden is not a hosted service. It is software the user installs and runs
> on their own machine, from a public repository. The URL above describes it and
> the install instructions are at
> https://github.com/bruno-dotcom12/clip-warden/blob/main/docs/INSTALL.md

### 5. URL da política de privacidade (HTTPS obrigatório)

```
https://bruno-dotcom12.github.io/clip-warden/privacy.html
```

Já é HTTPS e já responde. **Só depois de acrescentada a seção de YouTube** como
diz o item 3 acima; hoje a página não menciona o YouTube e a auditoria checa
isso.

Termos de uso, se o formulário pedir:

```
https://bruno-dotcom12.github.io/clip-warden/terms.html
```

### 6. Conta de demonstração e capturas de tela

- **E-mail da conta demo:** `<PREENCHER: a conta descartável do item 5 acima>`
- **Senha da conta demo:** `<PREENCHER>`
- **Como usar:** o app não tem site. O revisor pode (a) abrir o canal dessa
  conta no YouTube e ver o vídeo que a demonstração enviou, privado, ou (b)
  seguir `docs/INSTALL.md` e rodar o container.
- **Capturas:** as três do item 4 acima.

### 7. Valor independente e significativo para o ecossistema do YouTube

O formulário pergunta como a aplicação agrega valor próprio, em vez de só
replicar o que o YouTube já faz. Texto para copiar:

> Clip Warden does work that happens before a video reaches YouTube, and that
> YouTube does not do.
>
> Paid clipping campaigns — the market this serves — reject a submission after
> the views have already accrued, and almost always for a mechanical reason: a
> second over the limit, a missing hashtag, the wrong audio policy, footage from
> a source the brief did not authorise. Clip Warden reads the campaign's written
> brief, records the rules it actually states, and refuses to hand over a clip
> that breaks one. It transcribes the source locally, selects moments that stand
> on their own out of context, frames vertically with face detection so the
> speaker is not cut off, and burns word-by-word captions.
>
> None of that is a YouTube feature, and none of it is a wrapper over a YouTube
> feature. The API is used only at the very end, to place a file the user
> already approved onto the user's own channel — one `videos.insert` call, from
> an explicit command, after a device-code approval on Google's own screen.
>
> The tool also does not compete with YouTube for attention or data: it runs
> entirely on the user's machine, has no server, collects nothing, and sells
> nothing. It is MIT-licensed and the whole source is public at
> https://github.com/bruno-dotcom12/clip-warden

Se houver campo para "quantos usuários", a resposta honesta hoje é **1**
(o Agent Index registra `users: 1`). Não invente número.

---

## Depois de enviar

- **Não prometa a ninguém uma data.** Nenhuma existe.
- Enquanto a resposta não chega, o que publica de verdade continua sendo:
  o agente entrega o MP4 na conversa e a pessoa sobe pelo app ou pelo site do
  YouTube. `README.md` e `docs/INSTALL.md` dizem isso nessas palavras.
- Se a auditoria voltar pedindo mais coisa, é normal — o formulário pode ser
  reenviado.
