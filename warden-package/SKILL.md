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

Write ONE and use it. Do not offer two or three for them to pick: that is a
question before the delivery, and the hook is yours to decide. Change it only if
they ask. Never write a hook that promises something the clip does not deliver in
its first seconds.

The measured numbers behind all of this -- the usable width, the body size, how
long the hook stays -- live in `warden-style`, and that is the file to read
before you write a hook. What follows is only what `cut` will REFUSE.

### The three numbers the renderer imposes

They are not style advice. `cut` measures the hook with the real font before it
draws it, and a hook that does not fit is a REJECT rather than a smaller hook.

**About nine words, at most two lines.** The usable width is 854px of a 1080
frame -- the platform's right-hand column reserves 140px and the left margin
eats 86px -- and the hook is broken into at most two lines against it. Past
that, `cut` shrinks it to a floor and then drops words, and a hook missing words
is worse than one cropped at the edge: cropped shows on the contact sheet,
missing does not.

**The accent is `*assim*`**, a pair of marks around the word that takes the
accent colour. The choice of word is the hook writer's, so make it: a hook with
no marks gets no accent, and `**assim**` is two empty pairs, not bold, and the
word ends up with no accent at all.

**The hook leaves the screen at 3s**, with a short fade. Write it as a promise
that is spent in three seconds, not as a title for the clip: on 14/09 the line
sat in all eight frames of the contact sheet, from 1,2s to 18,8s, fighting the
spoken caption for seventeen seconds in which it added nothing.

## What you always repeat

The sound policy, if the campaign has one, because it is the step that happens
on the platform and is the one people forget. The same goes for how long the
post must stay up.

## Putting it in their TikTok drafts

```
warden tiktok <clip.mp4> --campaign <id> --hook "<their line>"
```

It uploads the finished clip to their TikTok **inbox** and then prints the
caption for them to paste. Both halves of that sentence matter.

**The draft lands in the Caixa de entrada -- the notification -- and NOT in the
Drafts tab of the profile.** Measured 14/09/2026: the owner looked in Drafts,
did not find it, and concluded it had never arrived. Say where it is when you
tell them it is there.

**The caption does not go up with it, and you never imply it did.** The inbox
endpoint has no field for one: its whole body is `source_info.source`,
`video_size`, `chunk_size` and `total_chunk_count`. Pre-filling a caption exists
only in Direct Post, which needs the `video.publish` scope and an audited app.
So the text this skill wrote is printed for them to paste, and the honest
sentence is "it is in your inbox, here is the caption to paste", never "it is
posted with the caption".

It needs a TikTok token for their account, which the image cannot carry -- it
belongs to a person, not to the agent. `warden status` has a `tiktok draft
upload:` line saying whether it is set up. When it is not, say so before
rendering a clip on the promise of posting it, not after.

## After they post

`warden log --campaign <id> --clip <name> --platform <p> --url <url>` keeps this
install's own count, which is what the checker reads to know whether they have
hit the campaign's cap. A clipper past the cap is working for free.
