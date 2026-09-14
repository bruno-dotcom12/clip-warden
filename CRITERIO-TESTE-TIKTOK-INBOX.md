# Critério do teste do inbox do TikTok

**Escrito em 14/09/2026, ANTES de rodar o teste.** Existe para que o resultado
não seja interpretado à conveniência depois. Nada neste arquivo pode ser
alterado depois que o teste rodar; se algo estiver mal definido, a correção
entra como adendo datado no fim, dizendo o que mudou e por quê.

## A pergunta

O envio para rascunhos (escopo `video.upload`, endereço
`/v2/post/publish/inbox/video/init/`) escapa da trava que obriga clientes não
auditados a postar só em `SELF_ONLY`?

## O teste

Um clipe (`mbl-02-votaria-nele.mp4`, contact sheet olhado em 14/09), enviado por
um app em modo de teste (sandbox), nunca auditado, para a conta TikTok do dono,
que é uma conta **pública**.

## O que conta como prova, e o que não conta

**Não conta como prova:** o seletor de privacidade do app oferecer "Todos". O
TikTok pode deixar escolher e forçar privado depois. A única prova de que o
vídeo é público é ele aparecer para quem não é o dono.

**Conta como prova:** abrir o vídeo publicado numa segunda conta, ou com o
TikTok deslogado, e vê-lo lá.

## Os três resultados, decididos antes

### A. O rascunho chega e publica como "Todos"

Critério: o rascunho aparece na caixa de entrada do app, o seletor oferece
"Todos", e depois de publicado o vídeo **é visto de outra conta ou deslogado**.

**Veredito: hipótese CONFIRMADA. Conclusivo.**

A trava de `SELF_ONLY` não alcança o envio para rascunhos. Vale notar que isso
foi provado com app em modo de teste, que é o ambiente **mais** restrito; um app
em produção não pode ser mais travado que ele. Então o resultado vale para os
dois.

O que se abre: o Bloco F2 fica de pé. Sobra o portão do App Review, que é
problema de alcance (10 contas sem ele) e não de mecanismo.

### B. O rascunho chega mas só publica como "Só eu"

Critério: o rascunho aparece, mas o seletor não oferece "Todos", **ou** oferece,
e o vídeo publicado não é visto de fora.

**Veredito: hipótese DERRUBADA na versão barata. Conclusivo o bastante para
parar, não o bastante para fechar a porta.**

A ressalva honesta: o modo de teste tem um aviso oficial ambíguo — *"Sandbox
mode does not offer access to Content Posting API for public videos"*. Se a
trava aparecer aqui, não dá para saber se veio da auditoria ou do modo de teste.

O que isso decide mesmo assim: **não se constrói o Bloco F2 em cima disso.**
Provar o contrário exigiria um app aprovado no App Review, e gastar semanas
nisso apostando numa inferência que já falhou uma vez é mau negócio. Se um dia
houver App Review aprovado por outro motivo, o teste se refaz de graça.

Sub-caso que é pior e precisa ser anotado separado: o seletor oferece "Todos",
o app aceita publicar, e o vídeo não aparece de fora. Isso é trava silenciosa,
o pior dos mundos, e vira aviso no README.

### C. O rascunho não chega

Aqui é preciso separar dois casos, porque eles significam coisas diferentes.

**C1. O pedido falha com erro explícito.** A API recusa e diz o motivo (falta de
escopo, restrição de sandbox, o que for).

**Veredito: conclusivo sobre o modo de teste, INCONCLUSIVO sobre a hipótese.**

O erro que voltar é a informação. Se ele nomear o sandbox, aprendemos que o
teste barato não existe e a decisão passa a ser sobre encarar o App Review. Se
ele nomear auditoria, aí sim é resultado B.

**C2. O pedido é aceito, volta identificador e status de entregue, e nada
aparece no celular.**

**Veredito: INCONCLUSIVO sobre a hipótese, e descoberta grave por si só.**

É falha silenciosa: a API diz que entregou e não entregou. Já existe relato
público disso (issues 1300 e 1338 do postiz-app, de março e maio de 2026).
Qualquer implementação futura teria que reconferir o status horas depois em vez
de confiar na resposta imediata.

Antes de declarar C2, esperar **15 minutos** e reconferir o status; depois
reconferir **uma vez algumas horas mais tarde**. Só então é C2.

## O que este teste não responde, em nenhum dos casos

- Se o App Review aprova um agente open source. É outro processo, outro portão.
- Se a entrega pelo rascunho é confiável ao longo do tempo. Um envio não mede
  isso, e há relato público de ela parar sozinha e voltar sozinha.
- Quanto tempo o rascunho sobrevive antes de expirar.
- O que acontece ao estourar o limite de 5 rascunhos pendentes em 24 horas.

## Registro

O log cru de toda requisição e resposta fica em `log-tiktok.txt`, no diretório
temporário da sessão. O relatório final só pode afirmar o que estiver nesse log
ou o que o dono viu no celular e descreveu.
