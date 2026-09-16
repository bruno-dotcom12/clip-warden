# Postar nas redes: o que é possível, verificado em 14/09/2026

Pesquisa feita com fonte oficial de cada plataforma. Onde a conclusão é
inferência e não declaração explícita, está dito.

O resumo em uma tabela:

| Rede | Conta do próprio dono | Estranho que instalou o agente |
|---|---|---|
| TikTok | funciona pelo fluxo de inbox | funciona pelo fluxo de inbox |
| Instagram | publica público direto, sem review | precisa App Review + Business Verification |
| YouTube | travado em privado, sem saída | inviável |

---

## TikTok: o fluxo de inbox é a saída

A Content Posting API tem dois caminhos, e eles têm regras diferentes.

**Direct Post**, escopo `video.publish`, endpoint `/v2/post/publish/video/init/`.
Publica direto no perfil. É aqui que mora a restrição: cliente não auditado só
posta em `SELF_ONLY`, com teto de 5 usuários por 24 horas.

**Upload to inbox**, escopo `video.upload`, endpoint
`/v2/post/publish/inbox/video/init/`. Manda o vídeo para os rascunhos do app. O
dono recebe notificação no celular, abre o TikTok, edita se quiser e publica
nativamente, escolhendo a visibilidade que quiser.

Quatro evidências de que a restrição não alcança o inbox:

A restrição está escrita dentro da seção "Direct Post API - Developer
Guidelines", e não existe seção equivalente para o upload.

O endpoint de inbox não tem campo `privacy_level`. Os únicos campos do corpo são
`source_info.source`, `video_size`, `chunk_size`, `total_chunk_count` e
`video_url`. Quem decide privacidade é o usuário, dentro do app.

A lista de erros do endpoint de inbox não inclui
`unaudited_client_can_only_post_to_private_accounts`, que está documentado no
Direct Post.

A definição oficial dos escopos separa os dois mundos: `video.upload` é "share
content to creator's account as a draft to further edit and post in TikTok";
`video.publish` é "directly post content to a user's TikTok profile".

**O que não foi confirmado.** Não existe frase oficial afirmando que o inbox está
isento da auditoria. O veredito é inferência por convergência, forte mas
inferência. Também não está documentado se o teto de 5 usuários por 24 horas
alcança o inbox. Prove com um teste real antes de arquitetar em cima.

Limite conhecido: 6 requisições por minuto por access token no init.

---

## Instagram: publica público hoje, sem pedir licença

Standard Access é o nível padrão automático de todo app. A documentação diz, com
todas as letras, que se o app serve apenas a conta profissional do próprio dono,
Standard Access é tudo de que ele precisa. App Review e Business Verification só
entram quando o app atende contas de terceiros.

Dois caminhos de permissão. Com Instagram Login, que é o recomendado e dispensa
Facebook: `instagram_business_basic` e `instagram_business_content_publish`. Com
Facebook Login: `instagram_basic`, `instagram_content_publish` e
`pages_read_engagement`.

Reels é suportado, `media_type=REELS`. A conta precisa ser profissional, business
ou creator; conta pessoal não serve. O limite é 100 posts publicados por API numa
janela móvel de 24 horas, e não 25, que é o número antigo que ainda circula em
tutorial velho.

Não existe rascunho como no TikTok. Existe um fluxo de dois passos, criar um
container com `POST /<IG_ID>/media` e publicar com `POST /<IG_ID>/media_publish`,
mas o container é staging de servidor, não aparece na interface do Instagram e
expira em 24 horas se não for publicado.

---

## YouTube: descartado, e o motivo importa

Vídeo enviado por `videos.insert` de projeto de API não verificado criado depois
de 28/07/2020 fica restrito a modo privado. A mesma nota de release diz que o
criador recebe e-mail explicando que o vídeo está "locked as private". As duas
expressões descrevem o mesmo estado; não são coisas diferentes.

O artigo de suporte diz que o dono não consegue mudar o estado do vídeo, e a
orientação para este caso é reenviar por um cliente auditado ou pelo app, não
apelar nem alternar a visibilidade. Não existe o "um toque para público".

Destravar exige auditoria de conformidade, pelo mesmo formulário da extensão de
quota. O formulário foi desenhado para empresa com produto público: pede nome
legal, endereço, modelo de monetização, homepage, termos de uso, política de
privacidade, conta demo com acesso total e capturas de tela. Não existe SLA
publicado, e os relatos em fórum oficial descrevem semanas a meses de silêncio.
Não encontramos um único relato verificável de pessoa física aprovada.

Quota deixou de ser o problema: desde 12/2025 o custo de um upload caiu de ~1600
para 1 unidade num bucket próprio, com 100 uploads por dia no padrão. O gargalo é
100% a trava privada.

E há uma proibição que fecha a porta da distribuição: a política de
desenvolvedores do YouTube, seção III.D.1, proíbe nominalmente embarcar
credenciais de API em projetos open source. Então não dá para o agente trazer o
próprio `client_id` embutido, e exigir que cada usuário crie e audite o próprio
projeto é inviável na prática.

### Sobre o agente concorrente video-cuts

Ele publica mesmo pela YouTube Data API, upload resumable, sem intermediário e
sem navegador, e manda `privacyStatus: private`. O README promete "clips land
private and you make them public in one tap". Essa promessa contradiz a
documentação oficial: o vídeo fica travado, não apenas privado.

O fluxo dele entrega o código de device ao usuário, o que só é possível com o
`client_id` embarcado na imagem, o que é o caso que a III.D.1 nomeia. Na data da
análise o repositório tinha zero estrela e zero fork, e o autor havia acabado de
adicionar termos e política de privacidade com a mensagem de commit "for OAuth
consent screen", o que indica submissão em preparo e trava ainda não encontrada.

Conclusão: não copiar. O YouTube não é vantagem dele, é dívida.

---

## O que isso decide para o Clip Warden

O agente pode ser ponta a ponta no TikTok para qualquer pessoa, sem auditoria,
sem mensalidade e sem intermediário, pelo fluxo de inbox. O TikTok é onde estão
as campanhas de CPM, então é a rede que importa.

O Instagram entra como segundo passo, valendo para o dono do agente e para quem
tiver conta profissional própria, publicando de verdade e em público.

O YouTube fica de fora, com o motivo escrito, e vira uma linha honesta no README
em vez de uma promessa que quebra na mão do usuário.

Existem revendedores que já passaram pelas auditorias (Ayrshare, Post for Me,
Postproxy, Upload-Post, bundle.social, Blotato), com entrada de grátis a
US$ 149/mês. Só a Ayrshare documenta oficialmente publicação pública no YouTube.
Isso fica como opção do dono, nunca como dependência padrão do agente, porque
cada credencial a mais é um ponto onde a instalação de um estranho falha.

---

## Fontes

TikTok: developers.tiktok.com/doc/content-sharing-guidelines ·
/doc/content-posting-api-reference-upload-video ·
/doc/content-posting-api-reference-direct-post · /doc/tiktok-api-scopes ·
/docs/en/content-posting-api-get-started-upload-content

YouTube: developers.google.com/youtube/v3/revision_history (28/07/2020) ·
/youtube/v3/docs/videos/insert · /youtube/terms/developer-policies ·
/youtube/v3/determine_quota_cost · support.google.com/youtube/answer/7300965 ·
support.google.com/youtube/contact/yt_api_form

Instagram: developers.facebook.com/docs/instagram-platform/overview ·
/docs/instagram-platform/content-publishing · /docs/graph-api/overview/access-levels ·
/docs/instagram-platform/app-review
