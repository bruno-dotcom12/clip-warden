# Varredura e auditoria do Clip Warden, 13/09/2026

Você é o auditor deste repositório. O dono não é desenvolvedor, então tudo que
você concluir precisa ser explicado em linguagem comum, e todo número que você
afirmar precisa vir de um comando que você rodou, nunca da sua leitura do código.

O contexto que importa: este agente vai ser instalado e rodado à mão por
estranhos (os hosts do Hermes Hackathon) a partir de 14/09. A verificação deles
olha se o agente é seguro e se ele faz o que promete. Um agente que quebra na
mão do host não é verificado, e só agente verificado pode vencer.

## Como trabalhar

Trabalhe em etapas e pare no fim de cada uma para me mostrar o que achou antes
de corrigir qualquer coisa. Não refatore por gosto. Só mexa no que estiver
quebrado, inseguro, ou em desacordo com o que o README e as skills prometem.
Cada correção vira um commit próprio, com mensagem que diz o que estava errado.

No fim, escreva `RELATORIO-AUDITORIA-13-09.md` na raiz, com uma tabela de tudo
que você achou, classificado em crítico, grave, cosmético e aberto, e com o que
você corrigiu e o que deixou de propósito.

## Etapa 1: o que já sabemos, e que você precisa confirmar ou derrubar

Estes cinco pontos vieram de uma pesquisa externa hoje. Trate cada um como
hipótese, não como verdade. Confirme no código e diga se procede.

1. O agente promete mandar os clipes de volta na conversa, e não existe nenhuma
   linha na persona nem nas skills explicando como ele faz isso. `grep -rn MEDIA`
   no repositório retorna vazio. A forma correta de mandar arquivo na plataforma
   Plow é escrever `MEDIA:/caminho/absoluto/arquivo.mp4` sozinho numa linha da
   resposta. Isso só foi ensinado ao modelo no plugin a partir do commit
   `e2c57bc`, de 12/09 21:28 PT. Nosso Dockerfile fixa a base no commit
   `4747960e` do plow-hermes-agent, de 10/09, que carrega o plugin `3a7858d`.
   Ou seja: pela nossa base, o modelo não sabe que esse caminho existe.
   Confirme, e me diga o que acontece hoje quando o agente termina um corte:
   ele fica com um arquivo no disco do contêiner e nenhuma forma de entregar?
   Essa é a promessa central do README. Se estiver quebrada, é crítico.

2. Junto com isso vem um bug de identidade de saída, corrigido no plugin em
   `08cdc4ea` de 12/09, que também não está na nossa base: mensagens saíam do
   número do dono em vez da linha do agente e eram reportadas como enviadas sem
   terem sido. Confirme se nossa base é anterior a esse fix.

3. Subir a base para o commit mais novo tem um risco documentado no README do
   plugin: o caminho de mídia depende de uma API de anexos (`plow-pbc/plow#1435`)
   que é repositório privado e não dá para confirmar se está no ar. Se não
   estiver, o declare do anexo devolve 404. Portanto: não suba a base às cegas.
   Descubra qual tag de base existe hoje no ECR público e me diga o comando
   exato para eu testar, numa cópia separada, antes de trocar o digest do repo
   principal. A receita está no README do plow-hermes-agent, seção de publicação.

4. O nosso registro no Agent Index está incompleto. Hoje a API
   `https://agent-index-server.vercel.app/v1/agents` mostra `clip-warden` com
   blurb vazio, sem `install_url`, 1 usuário e 0 instalações bem sucedidas.
   Confira você mesmo com curl, e me diga exatamente qual comando do
   `agent_index_client.py` preenche blurb, repo, install-url, imagem e vídeo,
   e se dá para reenviar por cima sem duplicar o registro.

5. As issues #19, #20 e #21 do repo `plow-pbc/plow-agents` descrevem um `mint`
   quebrado, mas o código do `main` já não chama a rota morta desde `eaa0c8d`,
   de 08/09. Verifique qual versão do `plow-agents` está no PATH desta máquina
   e me diga se o instalador que eu mandar para um estranho hoje funciona.

## Etapa 2: varredura de segurança, com olhos de quem quer quebrar

A auditoria de ontem já corrigiu três coisas críticas: execução de comando pelo
link do regulamento, nome de arquivo remoto vazando para a cadeia do ffmpeg, e
travessia de caminho pelo id da campanha. Sua tarefa é achar o que passou.

Parta do princípio de que o regulamento da campanha é texto de um estranho e
pode ser hostil. Siga o texto desse regulamento por todo o caminho que ele
percorre: `warden fetch`, o JSON que o modelo monta, `campaign save`, os links
em `sources.archive_urls`, o download em `_download_one`, o nome do arquivo, a
legenda, o caption, e principalmente a linha de filtros do ffmpeg em `cut`.
Para cada ponto onde um texto controlado pelo atacante vira argumento de
processo, caminho de arquivo, ou string de filtro, escreva um teste que tenta
abusar dele e mostre o resultado.

Olhe com atenção para:

- `warden_media.py`, função `_download_one`: o caminho do `gdown` e o caminho do
  `yt-dlp` recebem tratamento igual ao do download direto? O nome que o `gdown`
  escreve vem de nós ou do servidor remoto?
- O `urlopen` do download direto: ele segue redirecionamento? Um link http que
  redireciona para `file://` ou para um endereço interno da máquina do host é
  recusado? Tem teto de tempo e de tamanho nos dois caminhos, ou só num?
- O limite de recursos do contêiner. A auditoria de ontem deixou isso em aberto
  por medo de quebrar o supervisor. Meça: quanto o contêiner consegue crescer
  com um arquivo grande e uma transcrição rodando? Proponha limites de memória,
  processos e disco no compose e me mostre o agente subindo com eles. Se
  quebrar o supervisor, reverta e registre.
- A credencial do host. Ela entra por variável de ambiente no serviço de
  reporte, herdado do exemplo oficial. Ela aparece em `ps`, em log, em mensagem
  de erro, em traceback, ou em qualquer arquivo que o agente possa abrir e
  mandar pela conversa? Tente ler a credencial usando só as ferramentas que o
  próprio agente tem.
- Cada `except` do repositório: algum ainda deixa traceback cru chegar ao
  usuário, ou engole um erro e segue como se tivesse dado certo?

## Etapa 3: o agente cumpre o que o README promete

Leia o README, a persona e as cinco skills como se fossem o contrato. Depois
leia o código e aponte cada lugar onde o contrato não é cumprido.

Pontos que eu já desconfio:

- `warden discover` sai para quatro diretórios públicos na internet e o README
  não diz isso em lugar nenhum. Alguém instalando isso merece saber para onde o
  agente se conecta. Escreva essa seção.
- O README diz que os modelos de transcrição são baixados em segundo plano no
  primeiro boot e que `warden status` diz se chegaram. Confirme que é verdade,
  e que o agente não trava esperando.
- A skill `warden-run` manda perguntar preferências antes de renderizar e nunca
  perguntar duas vezes. O `warden prefs ask` de fato omite o que já foi
  respondido? Teste.
- A skill `warden-campaign` diz que `campaign save` imprime `stored and
  verified`. Confirme que imprime mesmo, e que falha ruidosamente quando não
  salvou.
- O `check` é o portão. Ele sai com código 1 quando reprova? Cada regra que o
  README lista como verificável está de fato implementada, ou alguma delas cai
  silenciosamente em "não verificado"?

## Etapa 4: os testes

Hoje são 43 testes e passam todos em 0,02 s, o que significa que nenhum deles
toca vídeo de verdade. Isso é rápido e é frágil ao mesmo tempo.

- Liste o que está coberto e, principalmente, o que não está.
- Escreva os testes que faltam para o caminho de mídia, usando um arquivo de
  vídeo minúsculo gerado pelo próprio ffmpeg no setup do teste. Quero ver
  `cut` rodando de verdade pelo menos uma vez, e `check` julgando o arquivo que
  o `cut` produziu.
- Escreva um teste que reproduz a armadilha do dono root na pasta de estado, já
  que ela derrubou o agente uma vez.
- Rode a suíte inteira e me mostre o resultado, incluindo o tempo.

## Etapa 5: instalação por um estranho

Leia `docs/INSTALL.md` fingindo que você nunca viu este projeto, tem um Mac com
Docker e vinte minutos. Siga ao pé da letra e anote todo lugar onde você
precisaria adivinhar alguma coisa. Se um passo depende de algo que não está
escrito, o documento está errado, não o leitor.

Conte quanto tempo leva o build, do zero, sem cache. Se passar de dez minutos,
diga o que está pesando.

## O que você não faz

Não troque o digest da imagem base sem eu aprovar. Não mexa na licença nem no
NOTICE. Não suba nada para o GitHub. Não invente regra de campanha que o
regulamento não disse, nem mesmo em teste, porque isso é exatamente o erro que
este agente existe para impedir.
