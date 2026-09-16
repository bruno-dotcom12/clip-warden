# Style calibration: the numbers, and what each one cost

`warden-style/SKILL.md` points here twice: from the caption constants, and from
the short list `warden style check` rejects on. This page describes what the
renderer already applies by itself and why. You decide nothing here — the skill
kept only what you decide. It is here so that nobody "simplifies" a constant
back into a defect that has already shipped once.

## The caption constants

- **Body `height // 16`, which is 120 on a 1920 frame, and that number is
  CALIBRATED — do not "simplify" it back to a round divisor.** What matters is
  the height of the LETTER, which is what a person sees: 120 measures **67px of
  ink, 3.49% of the frame** on capitals and ~3.12% on ordinary mixed case. The
  ASS `Fontsize` is not the same unit as a PIL font size, and that cost a
  delivered clip: when the caption moved from PIL to ASS the number stayed at 73
  and the letter shrank 45%, from ~3.2% of the frame to 1.88%. Nothing caught
  it, because everything checked the number and nothing measured the ink.
- 26 characters a line still fit at that body: 21 capitals measure 664px, so 26
  are ~822px against 854px usable.
- **The highlight is white -> yellow**: a word is white until it is said and
  yellow after. In ASS that is PrimaryColour for the *said* word and
  SecondaryColour for the one still coming, which is the opposite of what the
  names suggest — inverting them ships the whole caption yellow. The yellow is
  **not** from the approved corpus: that corpus is scenepacks and contains no
  running caption at all. It is the owner's call of 14/09, recorded as a
  decision and not as a measurement.

## Why `check` rejects on three things only

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
