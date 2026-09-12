---
name: warden-run
description: The whole path from one message to finished clips. Use when someone sends a campaign link, asks which campaigns are open, asks for clips, or says anything that means "make me something to post". This is the front door; the other skills are its steps.
---

# One message in, clips out

Someone sends a link, or asks you to go find a campaign. What comes back is
files they can upload, with the caption to paste. Everything between is yours.

```
find or read the campaign  ->  ask what they like  ->  pull the archive
   ->  choose on the text  ->  render  ->  check  ->  send
```

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

Never ask twice. `warden prefs show --campaign <id>` also tells you what the
campaign overruled, and you repeat that to them in one line: "you asked for the
track inside the file, this campaign adds the sound on the platform, so these
ship silent." Taste loses to the rule set every time, because taste is taste and
the rule set is the payment.

## 3. Footage, text, moments

`warden archive --campaign <id>`, then `warden transcribe`, then
`warden digest`. The order is in `warden-clip` and it is not negotiable: the
moments are chosen on the words, before any video is opened.

If their preference is `approval: yes`, send the chosen moments first, as
timestamps with the line that carries each one, and wait. If it is `no`, render
and send.

## 4. Render and send

`warden cut` for each chosen window, honouring `delivery`, `captions`, `hook`
and `target_s` from `warden prefs show`. Then `warden-package` for the caption.

Send each clip as it finishes, never the batch at the end. A person watching a
progress message for twenty minutes assumes you died.

With every clip: the file, the caption, and the one thing they do on the
platform, which is almost always the sound.

## 5. Afterwards

Ask which ones they posted and run `warden log`, because the campaign's cap is
counted from that ledger and a clipper past the cap is working for free.

## What you never do

Post for them. Take footage from outside the archive the brief published. State
a number you did not get from `warden`. Fill a rule the brief did not state.
