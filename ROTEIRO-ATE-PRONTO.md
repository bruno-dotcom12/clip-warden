# Roteiro até o agente estar pronto

Escrito em 14/09/2026, depois da rodada em que os dois cortes saíram certos
(`~/clipagem/revisao-14-09/`). O agente já produz clipe publicável. O que falta
é ele produzir clipe publicável **na máquina de outra pessoa**, **sem
supervisão**, e **escolhendo bem o momento**, que são três coisas diferentes.

Oito blocos. Um bloco por sessão de trabalho. Não pule a ordem: cada bloco
depende do anterior estar fechado, e os primeiros são os que impedem os
seguintes de serem desperdício.

Fechar um bloco significa: o teste que ele define passa, o contact sheet foi
olhado, e o relatório descreve o arquivo que existe e não o que se pretendia.

---

## Bloco A. Instalação limpa

O portão do registro no Agent Index. Enquanto este bloco não fechar, não
registre: install malsucedido de host fica gravado e install é a coluna do
placar.

O defeito concreto que abriu este bloco: na rodada de 14/09 a primeira
renderização saiu com o rosto na borda porque não havia OpenCV na máquina e a
detecção de rosto não rodou. Isso acontece na máquina de todo host.

Fecha quando: alguém que nunca viu o repo sai de zero ao primeiro clipe
seguindo só o `docs/INSTALL.md`, em três comandos ou menos, e nenhuma
dependência ausente causa silêncio.

## Bloco B. Qualidade visual

Os seis itens estão no código, todos verificáveis no arquivo:

- **O hook sai da tela aos 3s**, com fade de 0,4s (`HOOK_SECONDS`,
  `HOOK_FADE_S` em `warden_style`), e o sidecar anota `seconds_on_screen`. Em
  14/09 a frase estava nos oito quadros do mosaico, de 1,2s a 18,8s.
- **A cue quebra por fronteira sintática**, não só por tempo: `NAO_FECHA_CUE`
  lista as palavras que não podem fechar uma cue, e a cue pode estender até
  `CUE_TOLERANCIA_S = 1,0` além do alvo de 2,2s para alcançar uma vírgula.
- **Destaque palavra a palavra**: a legenda deixou de ser PNG do PIL e virou ASS
  queimado pelo libass, com `{\k}`. O hook continua PNG, porque é medido antes
  de ser desenhado.
- **O spec foi renomeado** para `SPECS/estilo-aprovado-scenepack.json`, e
  `specs_path_antigo()` existe só para dizer onde ela foi parar.
- **A métrica de pixel é cruzada com o `-estilo.json`**: reprova quando os dois
  instrumentos apontam largura demais, e quando discordam diz qual é qual em vez
  de calar um.
- **O descarte deixou de ser silencioso**: `_merge_spans` anexa em `descartes` o
  que jogou fora e por quê, e o `cut` recusa queimar legenda sem libass em vez
  de entregar clipe sem legenda.

**O bloco NÃO está fechado.** O critério dele é os mesmos dois cortes saírem de
novo e nenhum item aparecer no contact sheet, e essa rodada não aconteceu: o que
existe é código e teste verde, não clipe olhado. Testes verdes não são um clipe
visto.

Fecha quando: os mesmos dois cortes saem de novo e nenhum item da lista acima
aparece no contact sheet.

## Bloco C. Fidelidade do relatório

O agente descreve o arquivo que está no disco, nunca o que planejou. Na rodada
de 14/09 o relatório disse que o clipe 2 fechava em "VOTARIA MESMO. POR QUÊ?" e
o último cue do arquivo é "CARA PARA SER PRESIDENTE.".

Fecha quando: toda afirmação do relatório sobre o conteúdo do clipe é lida do
arquivo renderizado ou do `-estilo.json`, e nenhuma vem da memória do turno.

## Bloco D. Escolha do momento

O maior salto de qualidade percebida que ainda não foi tocado. Hoje `signals`
dá pistas e a escolha da janela é do modelo, sem nada que verifique se aquela
janela tem gancho, meio e fecho. Um corte tecnicamente impecável no momento
errado não roda.

Fecha quando: para cada janela escolhida o agente consegue nomear o gancho, o
que sustenta o meio e o que fecha, e recusa a janela quando não consegue.

## Bloco E. Corpus de fala

O corpus medido é scenepack de animação. O agente corta podcast. Falta a faixa
medida do formato que ele realmente produz.

A pasta PRIME saiu de cena por decisão do dono. O corpus de referência interno
passa a ser `~/clipagem/clipes cowork prime/` (lotes 11-09 e 12-09), e o corpus
de fala vem de fora: 8 a 10 cortes de podcast de clipadores que pontuam nas
campanhas.

Fecha quando: `SPECS/estilo-aprovado-fala.json` existe, medido sobre esses
cortes, e o `style check` reprova fora da faixa em vez de só relatar.

## Bloco F. Pacote de publicação

Descrição, hashtags dentro das regras da campanha e horário sugerido, prontos
para colar, com o texto passando pelo fiscal de regras antes de aparecer.

Fecha quando: o agente entrega o clipe e o texto junto, e o fiscal aprovou o
texto.

## Bloco F2. Postar de verdade

Verificado em 14/09/2026, ver `ANALISE-POSTAGEM-REDES.md` para as fontes. O
agente PODE ser ponta a ponta no TikTok, para qualquer pessoa, sem auditoria e
sem intermediário, pelo fluxo de inbox (escopo `video.upload`): o clipe cai nos
rascunhos do app e o dono publica com um toque, na visibilidade que quiser. A
restrição de `SELF_ONLY` vale para o Direct Post, não para o inbox.

O Instagram publica Reel público direto, sem App Review, quando o app serve a
conta profissional do próprio dono. O YouTube está descartado: vídeo de projeto
não auditado fica travado em privado sem saída manual, e embarcar credenciais em
projeto open source é proibido pela política deles.

Antes de qualquer código, um teste barato tem que provar a hipótese do inbox,
porque ela é inferência por convergência e não declaração oficial.

Fecha quando: um clipe sai do agente e chega publicado no TikTok com um toque, e
o README diz a verdade sobre as três redes.

## Bloco G. Campanha ponta a ponta

Achar a campanha, ler o regulamento, gravar a ficha de regras, listar o que o
regulamento não define e perguntar. Parte já existe em `warden-campaign`;
falta endurecer contra regulamento mal escrito e contra campanha que muda regra
no meio.

Fecha quando: duas campanhas reais diferentes entram e saem com ficha correta
sem intervenção.

## Bloco H. Ensaio com terceiro

Alguém que não é o dono instala e usa, sem ajuda, com o dono calado olhando.
Toda dúvida que a pessoa tiver é um defeito do agente, não dela.

Fecha quando: a pessoa chega ao primeiro clipe publicável sozinha.

---

## Regra que vale em todos os blocos

Nenhum clipe sai sem contact sheet olhado. Nenhum caminho de código descarta
conteúdo em silêncio. Nenhum relatório afirma o que não foi lido do arquivo.

Estas três já custaram uma entrega reprovada cada uma.
