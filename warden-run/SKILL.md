---
name: warden-run
description: The whole path from one message to finished clips. Use when someone sends a campaign link, asks which campaigns are open, asks for clips, or says anything that means "make me something to post". This is the front door; the other skills are its steps.
---

# One message in, clips out

**Answer in the language the person wrote to you in**, every message, including
the first. No sentence in this repository is a line to copy out.

**Before you run a single command, send ONE short line saying you are on it** —
two or three words, the instant you have the link and know what they want. Then
work without stopping. Measured 15/09: the owner waited ten minutes with no sign
the request had arrived.

**And nothing you do after that is a message.** Not which window you picked, not
that a clip was approved, not a second you adjusted.

**The order of work is in `warden-clip`, at the top of that file, and nowhere
else.** This skill is the front door; a second copy of the order is the one that
goes stale.

## 1. The campaign

A link: `warden-campaign` turns it into a stored rule set.

No link, they want you to look: `warden prefs ask --group search` lists only the
questions not answered yet. Ask them in ONE numbered message, never one at a
time, store each with `warden prefs set`, and never ask twice.

Then `warden discover` returns the public directories as text and the filtering
is yours: three or four options, what each pays, what footage it gives, the link
to each, and which one you would take and why. Never describe a campaign you
did not read. **Then wait — the choice is theirs.**

**When a search answer is still missing, the command says so and you do not
filter by it**; you never invent the criterion. When a page did not open, say
which and move on.

## 2. What is never asked

Every edit preference has a default:

| | |
|---|---|
| how many | 2 |
| how long | 20s, inside the campaign's limits |
| sound | what the campaign says, else the original |
| language of hook and captions | **the video's own**, never translated unless they ask |
| captions | burn them; footage that already has burned text gets ONE set, and `cut` measures that |
| the moments | you choose them; no approval round trip |
| bars, for an edit on the beat | the count closest to the duration asked; you say which you took |

**Do not run `warden prefs ask --group edit` before the first clip.** The
persona's single question — how many clips and how long, and only when the
message names neither — is the only one that may come before a cut. `warden
prefs show --campaign <id>` is read SILENTLY: what the campaign overrules is one
clause alongside the clip, never a question.

## 3. Footage, text, moments

`warden-clip` is the whole of it, in order, with the measured reason for each.
Do not work from a summary and do not write one here. There is no approval
round trip: render the moments you chose.

## 4. Render and send

`warden lote prep` then `warden lote render` is the road; `warden cut` directly
is for a single odd clip. **Both go in the BACKGROUND, and how to wait for them
is in `warden-clip`, at the top.**

Three rules live in `warden-clip` and nowhere else, and none is ever a question
to the person: the duration they named (`--seconds <n>`, 5b), who signs the
captions (5), and **`warden delivered` before you render anything** ("Deliver
first, cut second"). The contact sheet is not a step: a warning about anything
but the hook and the caption is repassed in ONE line beside the clip.

**A gate that names a fix gets ONE attempt at it**, and then you hand over what
cleared and say in one line what you could not do. Captions are the exception: a
clip without the words is not the product.

**Fix what the gate named. Never drop what was asked for.** Dropping `--hook`,
swapping the srt for the approval signature or trading `--seconds` for
`--any-length` until nothing complains ships a clip missing the product: 15/09,
both clips went out with no captions and no hook.

**How a file is handed over is in the persona, under "Handing the file over".**
With every clip goes the caption from `warden-package` and the one thing they do
on the platform, almost always the sound.

## 5. Afterwards

When they say they posted one, run `warden log`: the cap is counted from that
ledger and a clipper past it works for free. Do not ask which they posted.

## What you never do

Take footage nobody sent you. State a number you did not get from `warden`. Fill
a rule the brief did not state. Ask for a licence, for rights or for permission
to caption.
