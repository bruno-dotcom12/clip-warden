# Calibração do estilo: os números e o que cada um custou

Movido de `warden-style/SKILL.md` em 15/09/2026. Isto é referência: descreve o
que o renderizador já aplica sozinho e por quê. O agente não decide nada aqui —
a skill ficou só com o que ele decide. Nada foi apagado; só saiu do prompt, onde
pesava em todo turno sem mudar comportamento.

- **Body `height // 16`, which is 120 on a 1920 frame, and that number is
  CALIBRATED — do not "simplify" it back to a round divisor.** What matters is
  the height of the LETTER, which is what a person sees: 120 measures **67px of
  ink, 3.49% of the frame** on capitals and ~3.12% on ordinary mixed case. The
  ASS `Fontsize` is not the same unit as a PIL font size, and that cost a
  delivered clip: when the caption moved from PIL to ASS the number stayed at 73
  and the letter shrank 45%, from ~3.2% of the frame to 1.88%. Nothing caught it,
  because everything checked the number and nothing measured the ink.
- 26 characters a line still fit at that body: 21 capitals measure 664px, so 26
  are ~822px against 854px usable.
- **The highlight is white → yellow**: a word is white until it is said and
  yellow after. In ASS that is PrimaryColour for the *said* word and
  SecondaryColour for the one still coming, which is the opposite of what the
  names suggest — inverting them ships the whole caption yellow. The yellow is
  **not** from the approved corpus: that corpus is scenepacks and contains no
  running caption at all. It is the owner's call of 14/09, recorded as a
  decision and not as a measurement.

---

`check` reports every metric with the approved range beside it, and rejects on
three things only. Be clear about why the list is short:

- The corpus is twenty scenepack edits — one sustained anchor phrase, no running
  caption. A podcast cut has a hook **and** captions, so it has more text bands
  than any approved clip (measured: 2.75 against a corpus range of 0.9–2.1).
  Rejecting on that range would reject a legitimate mode the corpus does not
  contain.
- Measuring text width from pixels is unreliable. It catches the extreme case
  (1.22 on a deliberately cropped control, against 0.67–0.98 on most approved
  clips) but it read 1.14 on an approved clip whose text is small and clearly
  inside the frame — neon signs and high-contrast art look like white-on-black
  letters. A metric that would reject a clip the owner approved cannot reject
  alone. It was **not** deleted, because it sees something the `-estilo.json`
  cannot: the sidecar knows the hook, which PIL drew, and knows nothing about
  text that was already in the frame — the archive's burned captions, another
  clipper's mark. Two instruments, different blind spots. So `check` crosses
  them: it rejects when both say the text is too wide, and when they disagree it
  names which is which instead of picking one and silencing the other.
- The spec is called `estilo-aprovado-scenepack.json` and not
  `estilo-aprovado.json` for the same reason. A generic name over a specific
  corpus is how a range measured on one format ends up rejecting another. When
  you `check` a clip that carries burned speech captions, the command says out
  loud that this corpus does not contain that format. The speech corpus is
  Bloco E and does not exist yet.

---

## Por que estes textos saíram do prompt

A persona tinha 551 linhas e as sete skills somavam 93 KB. A persona é semeada
como identidade e é carregada inteira em todo turno (`Dockerfile`: `COPY
runtime/persona.md /opt/hermes/plow-seed/persona.md`). Em 15/09 cada chamada do
modelo carregou cerca de 400 mil tokens e a mediana de latência subiu de 5,65s
(abaixo de 50 mil tokens) para 10,0s (acima de 300 mil). Texto que não muda
decisão do agente é custo puro, em todo turno, para sempre.

O critério do corte foi um só: **uma regra mora num arquivo só, e o que fica no
prompt é o que já evitou um erro real.** Medição, história de depuração e
justificativa de constante vieram para cá.
