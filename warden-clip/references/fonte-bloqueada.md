# When the source refuses to come down

`warden-clip/SKILL.md` section 2 points here. The short rule is there: repass
`warden archive`'s diagnosis with no cause of your own in front of it. Here are
the two roads, the cookies, and — above all — **what was never measured**.

## Two different errors, and the tool keeps them apart

| what `archive` prints | what it is | what you do |
|---|---|---|
| the bot-check diagnosis ("Sign in to confirm you're not a bot") | it is about **every link, not this one**: the refusal is on the request, so it fires even on `--text-first`, before any window exists. Another YouTube link comes back the same | repass the diagnosis and offer them their own file |
| an ordinary yt-dlp error, in yt-dlp's own words | then it **IS** that video: age-restricted, private, members-only, or removed | repass that error, in one line |

**Never upgrade one into the other.** Asking for another link in the first case
costs a round trip and lands here again.

What you send is one message offering them their own file, and its shape is in
the persona under "The failure you repass".

## The two roads the tool names

`archive` prints the diagnosis itself, including whether a cookies file was
configured and used. The two roads it names are:

1. **A different outgoing address.**
2. **A cookies file at `/var/lib/hermes/warden/cookies.txt`**, from a
   **throwaway account** that is already signed in — **never theirs**, because
   yt-dlp's own warning is that this can get the account banned. That is a
   security rule, not a preference: the account that pays for a cookies file is
   the one whose cookies are in it.

## What was measured, on 15/09

**A different outgoing address fixes it.** This is a measurement, not a manual's
promise: the home address was refusing every link, and over a phone's connection
the same container, with no cookies and nothing else changed, pulled the
published subtitle and downloaded a window normally.

## What was NOT measured — so it is never claimed

**Whether a flagged address ever clears by itself.** Nobody measured it. So
**you never say it will, and you never say it will not.**

Say what the tool measured, offer them their own file, and if they send one you
are cutting in seconds instead of waiting.
