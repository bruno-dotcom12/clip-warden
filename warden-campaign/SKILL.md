---
name: warden-campaign
description: Read a clipping campaign's brief and store what it demands as a rule set the checker can judge. Use when the person sends a campaign link, pastes a brief, asks what a campaign requires, or names a campaign this agent has not seen.
---

# From a brief to a rule set

A brief is prose. The checker needs numbers. You are the step between, and you
are the only step that is allowed to interpret.

**This skill is not finished until `warden campaign save` has printed
`stored and verified`.** Extracting the rules in your head and telling the person
about them is not this skill; it is the failure this skill exists to prevent.

## Do this

1. `warden schema` prints the structure to fill. Read it before reading the brief.
2. Get the text. A link goes through `warden fetch <url>`; a pasted brief is
   already text. If the page needs a login and you cannot read it, say so and
   ask them to paste what they see.
3. Fill the structure. Copy the brief's own spelling for hashtags and mentions,
   including case, and put the links to the authorised footage in
   `sources.archive_urls`.
4. Everything the brief does not settle goes in `unknown`, by name.
5. `warden campaign save --json '<the json>'`. Inline, not on stdin: a heredoc
   that silently arrives empty is how an agent ends up believing it saved
   something. The command refuses a rule set whose shape is wrong, and the
   complaint names the key.
6. Read it back with `warden campaign show <id>` before you say a word about it.
   The save prints `stored and verified` with the path when it is really there.
   If you did not see that line, it is not stored: say so, do not tell the
   person their campaign is saved, and do not carry on from memory. Everything
   downstream, every check on every clip, reads this file and not this
   conversation.
7. Show the person what you stored, in their language, and say what the brief
   left open.

## The rule you do not bend

**Never fill a field the brief did not state.** A duration nobody wrote down is
`null`, not the number that is usual for the platform. The checker reports an
unset field as unchecked, which is true and useful; a guessed field reads as a
rule and gets a clipper rejected for obeying you.

Same for hashtags. `#AD` and `#ad` are the same to the checker, but if the brief
writes `#PrimeVideoBR` you store `#PrimeVideoBR`, because the person reads it.

## What good looks like

The person should be able to read the stored rule set and recognise their own
campaign in it, including the parts you could not settle.
