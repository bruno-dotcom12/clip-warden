# Postagem: o que foi medido e o que foi só lido numa especificação

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

O procedimento de publicação — a tabela do `warden post`, os três passos do
upload-post com os endereços escritos, e as regras de quem se pode publicar —
mora dentro da imagem, em `warden-shared`. Este arquivo dá a **procedência de
cada afirmação**, porque a diferença entre "medido com upload real" e "lido no
OpenAPI do fornecedor" é exatamente o que separa o que se pode prometer do que
não se pode.

O passo a passo para a pessoa que instala (endereços, chaves, perfis) está em
`docs/INSTALL.md`, seção *Handing a clip to YouTube, TikTok or Instagram* —
outra leitura humana. Aqui está o porquê.

## A tabela da procedência

| a afirmação | a prova |
|---|---|
| `warden post youtube` publica, e o vídeo sai **PÚBLICO** | **medido em 15/09/2026, com um envio real**: a YouTube Data API reportou `privacyStatus: public` e a página de assistir abre num navegador deslogado (`docs/INSTALL.md:579-582`). Não é leitura de documentação — é um vídeo que existe. O que faz a diferença é o intermediário, cujo próprio app já passou na auditoria do Google |
| título do YouTube acima de 100 caracteres é **recusado**, nunca cortado | medido — o comando recusa antes de subir |
| `warden post tiktok` / `warden post instagram` | **nunca rodou daqui**. Os nomes dos campos vêm do OpenAPI oficial do intermediário (`docs.upload-post.com/openapi.json`, lido em 16/09/2026) e de mais nada |
| legenda acima de **2.200** caracteres é recusada, não cortada | é o limite documentado das duas redes. A recusa é nossa e é a regra: uma legenda truncada em silêncio publica uma frase que ninguém escreveu |
| TikTok exige **plano pago**; Instagram exige conta **Business ou Creator** | são os termos do próprio fornecedor, da documentação oficial dele — não nossos. Você descobre isso no pior momento se não souber antes |
| `--privacy unlisted` é recusado no TikTok | o TikTok não tem "público para quem tem o link"; os valores dele são `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR` e `SELF_ONLY`, e escolher um deles por conta própria seria decidir quem vê o vídeo |
| no Instagram, qualquer coisa fora de `--privacy public` é recusada | o Instagram não tem privacidade por post: o perfil é público ou privado inteiro |
| `--draft` no TikTok: o TikTok **IGNORA** a legenda e a privacidade que a API mandou | documentação do próprio TikTok. A legenda é escrita por eles, no aplicativo |
| `warden tiktok <clip>` põe o clipe no **inbox**, não na aba Rascunhos do perfil | **medido**, não inferido: `CRITERIO-TESTE-TIKTOK-INBOX.md` foi escrito em 14/09/2026 ANTES do teste, e o log registra `inbox/video/init/` → HTTP 200, o `PUT` de 8.933.576 bytes → HTTP 201, e o estado final `SEND_TO_USER_INBOX` sem erro — com um app em sandbox, nunca auditado, sem escopo `video.publish` (linhas 110-135). O dono publicou e viu o vídeo de outra conta. Precisa do token daquela conta, e o token é de uma pessoa, não da imagem |

**Nunca leia um `200` do TikTok ou do Instagram como a mesma espécie de evidência
que o do YouTube.** O comando diz isso na própria saída em todo envio que inclui
uma das duas, e esta página não vai dizer menos.

## Os três passos, com os links escritos

Quando **não há chave**, o comando volta dizendo que o intermediário está
desligado — e então nada foi enviado, e você nunca diz que foi. A maioria de quem
roda isto não tem chave porque ninguém contou que existia uma, não porque decidiu
contra. Então a mensagem final carrega o clipe (a linha `MEDIA:`), o título, a
descrição pronta para colar, e isto:

> "Dá pra eu postar direto no seu canal. Três passos:
>  1. cria uma conta grátis em https://app.upload-post.com (não pede cartão)
>  2. abre https://app.upload-post.com/api-keys, clica em "Generate New API Key"
>     e copia a chave
>  3. cola a chave aqui que eu ligo e já te mando o link pra conectar o canal"

Quando a chave chega: `echo "<a chave>" | warden post setkey` — ela é lida da
entrada padrão de propósito, para nunca cair no histórico do shell — e daí
direto para o `connect`, sem outra ida e volta. O que você diz é uma linha, e a
chave não está nela:

> ✓ "Guardei. Abre esse link e conecta o canal: <endereço>."

**Você nunca repete a chave de volta. Nem o valor, nem um prefixo, nem o tamanho
dela.**

### Esta regra MUDOU em 16/09, e um texto que esconde isso é "reconsertado" semana que vem

A página antiga proibia pedir para colar a chave na conversa, e mandava a pessoa
escrever `WARDEN_POST_API_KEY=` num `.env` ao lado de um `docker compose up -d`.
Duas coisas estavam erradas. O `.env` **não existe na nuvem da Plow** — não há
`.env` nem `compose.yml` lá, o ambiente carrega o que a Plow põe nele e mais nada
— então a instrução apontava para um arquivo que a pessoa não pode criar, e a
chave não tinha por onde entrar. E o sigilo que ela protegia era o errado: esta
máquina é **dela**, sozinha, e a chave é da conta **dela**, com o limite dela e a
conta de luz dela.

## A ORIGEM da chave, e o perfil por máquina

`warden post status` imprime **de onde a chave veio** e de onde veio o nome do
perfil — do ambiente ou do arquivo desta máquina — **nunca o valor da chave**.

Leia a origem quando o canal não for o que a pessoa esperava: **uma chave vinda
do ambiente é a do dono do agente**, e nesse caso o perfil daqui tem de ser desta
máquina sozinha.

O nome do perfil é `clip-warden-<8 hex>`, decidido na primeira vez e guardado no
volume. Já foi `clip-warden` para toda instalação — o que, com uma chave
compartilhada, teria posto toda máquina no mesmo canal.

Saída 1 do `status` significa que não há chave, e a seção "Publishing" da persona
diz o que oferecer.

## `--also`, e por que ele existe

`--also tiktok,instagram` acrescenta redes ao **mesmo** envio: o arquivo sobe uma
vez e o intermediário distribui, em vez de o clipe sair desta máquina três vezes.
Um endereço por rede, exatamente como a API devolveu — **nunca montado aqui a
partir de um id**.

## As duas estradas do TikTok não se substituem

- `warden tiktok` fala **direto** com o TikTok, com o token da pessoa, e deixa um
  rascunho no inbox. Não precisa de plano pago. Não precisa de intermediário.
  **Limite medido: o inbox não aceita legenda** — ela é escrita no aplicativo.
- `warden post tiktok` passa pelo intermediário, com a chave dele, e pode
  publicar em vez de só encher o inbox — com o aviso de que nada disso foi
  medido daqui. Precisa do plano pago do TikTok.

Nenhuma torna a outra desnecessária: esta não precisa de plano pago, aquela não
precisa de token de desenvolvedor do TikTok.
