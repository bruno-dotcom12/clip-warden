---
name: warden-package
description: Write the caption a campaign requires, with its hashtags, mentions and posting conditions. Use when a clip is ready to post, or when the person asks what to write under it.
---

# The caption

`warden package --campaign <id> --hook "<their line>"`

The hook is theirs. Everything under it is the campaign's, in the campaign's own
spelling, in the order the brief gives.

## Writing the hook when they ask you to

Short, spoken, and tied to the moment in the clip rather than to the show in
general. It is read in one pass on a moving feed. Write in the language of the
audience the campaign targets, not the language of the brief.

Offer two or three and let them pick. Never write a hook that promises something
the clip does not deliver in its first seconds.

## What you always repeat

The sound policy, if the campaign has one, because it is the step that happens
on the platform and is the one people forget. The same goes for how long the
post must stay up.

## After they post

`warden log --campaign <id> --clip <name> --platform <p> --url <url>` keeps this
install's own count, which is what the checker reads to know whether they have
hit the campaign's cap. A clipper past the cap is working for free.
