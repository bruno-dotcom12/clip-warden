---
name: warden-run
description: The whole path from one message to finished clips. Use when someone sends a campaign link, asks which campaigns are open, asks for clips, or says anything that means "make me something to post". This is the front door; the other skills are its steps.
---

# One message in, clips out

Someone sends a link, or asks you to go find a campaign. What comes back is
files they can upload, with the caption to paste. Everything between is yours.

**The order of work is written in `warden-clip`, at the top of that file, and
nowhere else.** Not here, not in the persona, not in the README. This skill is
the front door -- what happens at each door -- and it does not carry a second copy
of the order, because the second copy is the one that goes stale and the model
obeys whichever it reads first.

## 1. The campaign

A link: `warden-campaign` turns it into a stored rule set.

No link, they want you to look: ask the search questions first.
`warden prefs ask --group search` lists only the ones not answered yet, which is
which audience they post to, what footage they can actually work with, where they
post, and what makes a campaign worth their time. Ask them in ONE numbered message, never one at a time -- the persona's rule
about that covers these too -- store each with `warden prefs set`, and never ask
a second time. This is the one place questions still belong before a search,
because nobody can measure what a person wants to spend their afternoon on. It
does not reopen the question of clips: the count and the length are still the
only thing ever asked before a cut.

Then `warden discover`, which returns the public campaign directories as text
with those answers printed on top. The filtering is yours: read the listings,
keep what matches what they told you, and come back with a short list of what is
open, what it pays, and what footage it gives you, with the link to each. Three
or four options, not thirty, and say plainly which one you would take and why.
Then wait. You are choosing where their next hours go, and that is their call.

If a search answer is still missing, the command says so and you do not filter on
it. If a page could not be read, say which and move on. Never describe a campaign
you did not read.

## 2. Nothing to ask

**Do not run `warden prefs ask --group edit` before the first clip.** Every edit
preference has a default now, and the command says so. The persona's single
question -- how many clips and how long, and only when the message names neither
-- is the only question that may come before a clip.

`warden prefs show --campaign <id>` is read SILENTLY, for what the campaign
overrules. You repeat that to them in one clause alongside the clip, not as a
question: "essa campanha põe o som na plataforma, então esses saem mudos."

Store a preference only when the person volunteers one (`warden prefs set`).

## 3. Footage, text, moments

`warden-clip` is the whole of it: which command, in which order, with the
measured reason for each. Do not work from a summary of it, and do not write one
here.

The gate is in `warden-clip` 1, and it is one sentence there: a link someone
sent you is authorised by their having sent it. Do not restate the mechanics
here; that is the summary this section just told you not to write.

Render the moments you chose. There is no approval round trip.

## 4. Render and send

`warden lote` is the short road and it is the default one: `lote prep <url>`
gives you the words, the signals and the defaults in one output, and `lote render`
cuts the windows, checks them on one combined contact sheet, and prints every
`MEDIA:` line at the end. Use `warden cut` directly only for a single odd clip.

The length rule: when THEY said a number of seconds it goes on the command line
as `--seconds <n>`. When nobody named one, `cut` uses the stored default and
prints which number it used and where it came from. You repeat that number only
if it differs from what they asked for. Why, and what it cost, is in
`warden-clip` 5b.

Who signs the captions -- the tool on the `lote` road, you on the `cut` road --
is written in `warden-clip` 5, "Who signs the captions", and nowhere else. Read
it there. It is never a question to the person.

Open the contact sheet before you send. `cut` will not print a `MEDIA:` line
without one. The checklist, and why it rejects a clip every check passed, is in
`warden-clip` 6.

**How a file is handed over is written in the persona, under "Handing the file
over", and it is written there and nowhere else.** Read it there before you end
a turn that carries a clip. Nothing about it is repeated here, because a second
copy is the one that goes stale.

With every clip goes the caption from `warden-package` and the one thing they do
on the platform, which is almost always the sound.

## 5. Afterwards

When they say they posted one, run `warden log`: the campaign's cap is counted
from that ledger and a clipper past the cap is working for free. Do not ask which
ones they posted.

## What you never do

Take footage nobody sent you. State a number you did not get from `warden`. Fill
a rule the brief did not state. Ask for a licence, for rights, or for permission
to caption.
