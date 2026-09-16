# O edit na batida

> Explicação humana, para quem mantém este repositório. **Este arquivo NÃO entra
> na imagem**: o Dockerfile copia `runtime/persona.md` (:237), as sete
> `warden-*/` (:242-248) e `SPECS/` (:250), e nada mais. O agente nunca lê o que
> está escrito aqui. Regra que o agente precisa seguir mora em
> `runtime/persona.md`, no corpo de uma `warden-*/SKILL.md`, ou em
> `warden-<skill>/references/<nome>.md` — as três coisas que a imagem carrega.

Um edit **não é uma janela com música por cima** — é um produto diferente.

O procedimento que o agente segue mora dentro da imagem, em `warden-clip`
(no corpo da SKILL.md ou em `references/`), junto do comando `warden beat`. Aqui
ficam a medição que definiu o que é um edit e as contas que sustentam cada
número — o porquê, não a ordem.

## A medição que define o que é um edit

Medido na referência que o dono deu (`youtube.com/shorts/QmrLImO6fus`),
129,2 BPM:

| | A referência | O "edit" de 15/09 |
|---|---|---|
| Trocas de cena | 10 a 13 em 15,8 s = **12,6 a 16,4 por 20 s** | 12 em 20,8 s |
| Erro contra a batida | **mediana 33 ms**, 8 de 10 abaixo de 80 ms | mediana 122 ms, 4 de 12 |
| De onde vieram os cortes | escolhidos | **os cortes que o vídeo-fonte já tinha** |
| Legenda | nenhuma — um gráfico de placar fixo | nenhuma |

4 de 12 abaixo de 80 ms é o que o acaso dá numa grade de 650 ms. Ou seja: o edit
antigo era uma janela contínua com música em cima, e as trocas de cena nele eram
as que a imagem já trazia.

## O comando

```sh
warden cut <src> --campaign <id> --shots 71.4-74.0,77.0-79.5,80.5-83.0 \
  --track <name> --subtitles <srt> --any-length --out edit.mp4
```

`--shots` é a lista de pedaços a emendar, **no relógio do próprio arquivo**, como
o `--start`. A duração de cada um é ajustada a um número inteiro de batidas da
faixa, então toda troca de cena cai na música: medido **0 ms de erro num edit de
7 shots**. Duas batidas é o shot curto, quatro é o longo — que é a gramática da
própria referência. O sidecar registra cada corte e o erro dele, então "corta na
batida" é um número e não uma impressão.

Cada shot ganha um zoom leve próprio, alternando para dentro e para fora. O
enquadramento é feito uma vez, depois da emenda: enquadrar shot a shot daria à
mesma cena um quadro diferente a cada vez.

## Duas coisas que o renderizador RECUSA, e as duas são o ponto

- **A legenda vira uma colagem.** Com shots pegos de todo lado da fonte, cada um
  abre no meio de uma frase e a legenda lê *"portas, a que vai pra sala / e a que
  vai Você lembra,"* — medido, de um render real. Quando mais da metade dos
  trechos emendados abre no meio de uma frase, ele entrega sem legenda e diz que
  fez isso. Conserta-se começando cada shot onde começa uma fala, ou tirando o
  `--subtitles` e pondo uma linha escrita com `--hook`, que é o que a referência
  faz.
- **A fonte já queima a própria legenda em um dos shots.** Os frames que ele
  amostra vêm dos SHOTS, não de um trecho contínuo — amostrar
  `start..start+length` num edit olha imagem que o clipe nunca mostra, e foi
  assim que uma segunda legenda entrou num render em 15/09 e só foi pega no frame
  em resolução cheia.

## O áudio

A fala da fonte é **descartada** num edit emendado: fala contínua sobre imagem
que salta é falar por cima da cena errada. A faixa é o áudio, e o que a fala
tinha a dizer está na legenda.

## A faixa e a grade

Para um edit, acrescente `--track <name|file>` — uma faixa já guardada por
`warden tracks add` é encontrada pelo nome. `warden beat <track>` mostra o que ele
achou: tempo, onde a grade começa, quanto dura um compasso, e onde a faixa ganha
corpo. O corte é então um número inteiro de compassos, e a faixa entra no drop em
vez de entrar na introdução.

**Um edit é cortado no compasso mesmo quando o arquivo sai mudo.** Uma campanha
que exige o som ser posto na plataforma ainda ganha um edit, porque um clipe com
duração de um número inteiro de compassos cai na batida assim que o dono escolhe
a faixa no aplicativo. Diga a ele em que segundo começar o som — que é o `drop_s`
do `warden beat`.

## O resto

Ele limita a duração à janela da campanha e avisa quando limitou. Tira o áudio
quando a campanha põe som próprio na plataforma. E mantém o texto queimado dentro
da área segura, porque a mobília da própria plataforma cobre o rodapé e a coluna
da direita, e nenhum briefing menciona isso.
