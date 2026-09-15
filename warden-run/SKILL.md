---
name: warden-run
description: The whole path from one message to finished clips. Use when someone sends a campaign link, asks which campaigns are open, asks for clips, or says anything that means "make me something to post". This is the front door; the other skills are its steps.
---

# One message in, clips out

Someone sends a link, or asks you to go find a campaign. What comes back is
files they can upload, with the caption to paste. Everything between is yours.

**The order of work is written in `warden-clip`, section 2, and nowhere else.**
Not here, not in the persona, not in the README. This skill is the front door --
what happens at each door, and what to ask -- and it does not carry a second copy
of the order, because the second copy is the one that goes stale and the model
obeys whichever it reads first.

## 1. The campaign

A link: `warden-campaign` turns it into a stored rule set.

No link, they want you to look: ask the search questions first.
`warden prefs ask --group search` lists only the ones not answered yet, which is
which audience they post to, what footage they can actually work with, where they
post, and what makes a campaign worth their time. Ask them in the conversation,
store each with `warden prefs set`, and never ask a second time.

Then `warden discover`, which returns the public campaign directories as text
with those answers printed on top. The filtering is yours: read the listings,
keep what matches what they told you, and come back with a short list of what is
open, what it pays, and what footage it gives you, with the link to each. Three
or four options, not thirty, and say plainly which one you would take and why.
Then wait. You are choosing where their next hours go, and that is their call.

If a search answer is still missing, the command says so and you do not filter on
it. Guessing that someone only wants music campaigns and hiding the rest is worse
than showing too many.

If a page could not be read, say which and move on. Never describe a campaign
you did not read.

## 2. Ask what they like, once

`warden prefs ask --group edit` lists only what has not been answered yet. Ask those, in the
conversation, in their language, a couple at a time rather than as a form. Store
each answer as it arrives with `warden prefs set --key <k> --value <v>`.

Ask before rendering, not after. Someone who wanted an edit on a beat and got
clean cuts throws away the whole batch.

Sound is not optional to ask, and it has no default. Unless the campaign settles
the audio, `warden cut` refuses to render until the owner has chosen: the
original sound carried in the file, or silent for the platform to add its own. A
clip that shipped silent because nobody was asked is a clip nobody wanted silent.
Ask it in their words -- "keep the original audio, or does the platform add the
sound?" -- and store the answer.

Never ask twice. `warden prefs show --campaign <id>` also tells you what the
campaign overruled, and you repeat that to them in one line: "you asked for the
track inside the file, this campaign adds the sound on the platform, so these
ship silent." Taste loses to the rule set every time, because taste is taste and
the rule set is the payment.

## 3. Footage, text, moments

`warden-clip`, sections 2 to 4, is the whole of it: which command, in which
order, with the measured reason for each. Do not work from a summary of it, and
do not write one here.

The one thing this skill adds is the gate, because it is the front door and the
gate is the first question a link raises. `warden archive` takes `--campaign
<id>` when a campaign vouches for the footage, and `--trusted <url>` when the
owner's own list does. It refuses to run with neither, which is the point: there
is no third way to decide that a link may be cut.

If their preference is `approval: yes`, send the chosen moments first, as
timestamps with the line that carries each one, and wait. If it is `no`, render
and send.

## 4. Render and send

`warden cut` for each chosen window, honouring `delivery`, `captions`, `hook`
and `target_s` from `warden prefs show`. Then `warden-package` for the caption.

When THEY said a number of seconds, it goes on the command line as
`--seconds <n>`. `target_s` is their standing taste; `--seconds` is this
request, and with it the delivery gate rejects a file that misses the number
unless a campaign rule is to blame. When nobody named a number, you say that
too, with `--any-length`: `cut` will not render without one of the two. On
14/09 "20 segundos" came back as 24,5s and 15,4s because the number never
became a parameter and nothing missed it.

The captions are approved per WINDOW, never per file.
`warden captions review <srt> --start <s> --end <s> --approve` signs the lines
it just printed and nothing else; a cut outside that window renders WITHOUT
captions rather than burn a word nobody read. Approving one window never
approved the file -- that is how `jokovic jokovic` reached the screen.

`cut` writes a contact sheet of its own render and prints it as `SHEET:`, and it
does not print the `MEDIA:` line without one. Open that image and go through the
checklist it names before you send: the sheet is the only step in this whole
path that looks at the picture, and a file with ten visible defects was once
reported as passing because nobody opened it.

Send each clip as it finishes, never the batch at the end.

And that is the only reason to end a turn before the clips exist. The cure for
a long silence is a shorter path -- the words first, the windows only -- not a
stream of progress notes: each one is a turn of yours and seconds of theirs.
The persona, under "What you say, and how little of it", is where that rule
lives.

**How a file is handed over is written in the persona, under "Handing the file
over", and it is written there and nowhere else.** Read it there. In one line:
the `MEDIA:` line goes in the LAST message of a turn, one clip per turn, and
`warden delivered` reads the gateway's log to say whether the attachment really
left. A `MEDIA:` line written mid-turn attaches nothing in silence, and that cost
a clip on 14/09 and two more on 15/09.

With every clip goes the caption from `warden-package` and the one thing they do
on the platform, which is almost always the sound.

## 5. Afterwards

Ask which ones they posted and run `warden log`, because the campaign's cap is
counted from that ledger and a clipper past the cap is working for free.

## What you never do

Post for them. Take footage from outside the archive the brief published. State
a number you did not get from `warden`. Fill a rule the brief did not state.
