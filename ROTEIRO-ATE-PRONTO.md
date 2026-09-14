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

Hook que sai da tela depois de ~3s em vez de ficar os 20 segundos. Quebra de
cue por fronteira sintática e não só por tempo. Destaque palavra a palavra.
Mais as três pendências do briefing anterior (renomear o spec de scenepack,
cruzamento da métrica de pixel, varredura de descarte silencioso).

Fecha quando: os mesmos dois cortes saem de novo e nenhum item da lista abaixo
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

Os 20 aprovados são scenepack de animação. O agente corta podcast. Falta a
faixa medida do formato que ele realmente produz.

Fecha quando: `SPECS/estilo-aprovado-fala.json` existe, medido sobre 8 a 10
cortes de fala de clipadores que pontuam, e o `style check` reprova fora da
faixa em vez de só relatar.

## Bloco F. Pacote de publicação

Descrição, hashtags dentro das regras da campanha e horário sugerido, prontos
para colar. Sem API de postagem: TikTok, YouTube e Instagram travam publicação
automática em privado até auditoria, e cada credencial a mais derruba a taxa de
instalação.

Fecha quando: o agente entrega o clipe e o texto junto, e o texto passa pelo
fiscal de regras da campanha antes de aparecer.

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
