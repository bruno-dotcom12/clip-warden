# Quando a fonte se recusa a descer

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

A regra — repassar o diagnóstico do `warden archive` sem pôr causa própria na
frente — mora dentro da imagem, em `warden-clip`. Aqui estão os endereços, os
cookies, e — principalmente — **o que NÃO foi medido**.

## Dois erros diferentes, e a ferramenta os separa

| o que o `archive` imprime | o que é | o que você diz |
|---|---|---|
| o diagnóstico de bot-check ("Sign in to confirm you're not a bot") | é sobre **todo link, e não sobre este**: a recusa é no pedido, então dispara até no `--text-first`, antes de existir qualquer janela. Outro link do YouTube volta igual | repassa o diagnóstico e oferece o arquivo da pessoa |
| um erro comum do yt-dlp, nas palavras do próprio yt-dlp | aí **É** aquele vídeo: com restrição de idade, privado, só para membros, ou removido | repassa aquele erro, em uma linha |

**Nunca promova um no outro.** Pedir outro link no primeiro caso custa uma ida e
volta e cai aqui de novo.

O que você diz é uma mensagem só, oferecendo o arquivo da própria pessoa, e está
escrito na persona sob "The failure you repass".

## Os dois caminhos que a ferramenta nomeia

O `archive` imprime o diagnóstico ele mesmo, inclusive se um arquivo de cookies
estava configurado e foi usado. Os dois caminhos que ele nomeia são:

1. **Um endereço de saída diferente.**
2. **Um arquivo de cookies em `/var/lib/hermes/warden/cookies.txt`**, de uma
   conta descartável já logada — **nunca a do dono**, porque o aviso do próprio
   yt-dlp é que isso pode derrubar a conta.

## O que foi medido, em 15/09

**Um endereço de saída diferente resolve.** Isto é medição, não promessa de
manual: o endereço residencial estava recusando todo link; pela conexão de um
celular o mesmo container, sem cookies e sem mais nada mudado, puxou a legenda
publicada e baixou uma janela normalmente.

## O que NÃO foi medido — e por isso não se afirma

**Se um endereço marcado alguma vez se limpa sozinho.** Não foi medido. Então
nunca se diz que vai, e nunca se diz que não vai.

Diga o que a ferramenta mediu, ofereça o arquivo, e se a pessoa mandar um você
está cortando em segundos em vez de esperando.

Ver também `docs/AUDITORIA-YOUTUBE.md` para a distinção entre "o YouTube recusa o
endereço" e "o YouTube recusa o vídeo".
