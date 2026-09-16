# Publishing: what was measured, and what was only read off a spec

`warden-shared/SKILL.md` points here after the `warden post` table. The table
gives the command; this page gives the **provenance of every claim** — because
the difference between "measured with a real upload" and "read in the provider's
OpenAPI" is exactly what separates what you may promise from what you may not.

## The provenance table

| The claim | The proof |
|---|---|
| `warden post youtube` publishes, and the video comes out **PUBLIC** | **measured 15/09/2026 with a real upload**: the YouTube Data API reported `privacyStatus: public` and the watch page opens in a signed-out browser. Not documentation — a video that exists. What makes the difference is the intermediary, whose own app already passed Google's audit |
| a YouTube title over 100 characters is **REFUSED**, never truncated | measured — the command refuses before it uploads |
| `warden post tiktok` / `warden post instagram` | **never ran from here.** The field names come from the intermediary's official OpenAPI (`docs.upload-post.com/openapi.json`, read 16/09/2026) and from nothing else |
| a caption over **2200** characters is refused, not cut | the documented limit of both networks. The refusal is ours and it is the rule: a silently truncated caption publishes a sentence nobody wrote |
| TikTok needs a **paid plan**; Instagram needs a **Business or Creator** account | the provider's own terms, from his own documentation — not ours. You find this out at the worst moment if you do not know it first |
| `--privacy unlisted` is refused on TikTok | TikTok has no "public to anyone with the link"; its values are `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR` and `SELF_ONLY`, and picking one yourself would be deciding who sees the video |
| on Instagram anything but `--privacy public` is refused | Instagram has no per-post privacy: the profile is public or private as a whole |
| `--draft` on TikTok: TikTok **IGNORES** the caption and the privacy the API sent | TikTok's own documentation. The caption is written by them, in the app, and you never imply it went up with the file |
| `warden tiktok <clip>` lands in the **inbox**, not the profile's Drafts tab | **measured**, not inferred: the criteria were written 14/09/2026 BEFORE the test, and the log records `inbox/video/init/` -> HTTP 200, the `PUT` of 8,933,576 bytes -> HTTP 201, and the final state `SEND_TO_USER_INBOX` with no error — with a sandbox app, never audited, without the `video.publish` scope. It needs that one account's token, and the token belongs to a person, not to the image |

**Never read a `200` from TikTok or Instagram as the same kind of evidence as
one from YouTube.** The command says so in its own output on every send that
includes either, and this page will not say less.

**No upload from this project ever went through TikTok or Instagram.**

## The three steps, as a mould

When there is **no key** the command comes back saying the intermediary is
switched off — and then nothing was sent, and you never say it was. Most people
who run this have no key because nobody told them one existed, not because they
decided against it. So the final message carries the clip (the `MEDIA:` line),
the title, the description ready to paste, and this:

> `<one line offering to publish straight to their channel>`
> 1. `<a free account at>` https://app.upload-post.com `<— no card asked>`
> 2. `<open>` https://app.upload-post.com/api-keys `<, press "Generate New API
>    Key" and copy the key>`
> 3. `<paste the key here and I switch it on, then send you the link to connect
>    the channel>`

The links and the order are fixed. **The words are yours, in their language** —
the angle brackets are slots, never text to copy out.

The title you write in that message is the same string that later goes into
`warden post youtube --title`, so it has to fit in **100 characters**: over that
the command refuses, it does not cut.

When the key arrives: `echo "<the key>" | warden post setkey` — it is read from
standard input on purpose, so it never lands in the shell history — and then
straight into `connect`, without another round trip. What you send back is one
line, and the key is not in it.

**You never repeat the key back. Not the value, not a prefix, not its length.**

### This rule CHANGED on 16/09, and a text that hides that gets "re-fixed" next week

The old page forbade asking for the key in the conversation and told the person
to put `WARDEN_POST_API_KEY=` into a `.env` beside a `docker compose up -d`.
Two things were wrong. The `.env` **does not exist on Plow's cloud** — there is
no `.env` and no `compose.yml` there, the environment carries what Plow puts in
it and nothing else — so the instruction pointed at a file they cannot create,
and the key had no way in. And the secrecy it protected was the wrong one: this
machine is **theirs**, alone, and the key is on **their** account, with their
quota and their bill.

## The ORIGIN of the key, and the per-machine profile

`warden post status` prints **where the key came from** and where the profile
name came from — the environment or this machine's own file — **never the value
of the key**.

Read the origin when the channel is not the one they expected: **a key from the
environment is the agent owner's**, and then the profile here has to be this
machine's alone.

The profile name is `clip-warden-<8 hex>`, decided the first time and kept in
the volume. It used to be `clip-warden` for every install — which, with one
shared key, would have put every machine on the same channel.

Exit 1 from `status` means there is no key, and the three steps above are what
you offer.

## `--also`, and why it exists

`--also tiktok,instagram` adds networks to the **same** upload: the file goes up
once and the intermediary fans it out, instead of the clip leaving this machine
three times. One address per network, exactly as the API returned it — **never
assembled here out of an id**.

## The two TikTok roads do not replace each other

- `warden tiktok` talks **straight** to TikTok with the person's token and
  leaves a draft in the inbox. No paid plan, no intermediary. **Measured limit:
  the inbox does not accept a caption** — it is written in the app.
- `warden post tiktok` goes through the intermediary with its key, and can
  publish instead of only filling the inbox — with the warning that none of it
  was measured from here. It needs TikTok's paid plan.

Neither makes the other unnecessary: this one needs no paid plan, that one needs
no TikTok developer token.
