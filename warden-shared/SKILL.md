---
name: warden-shared
description: The warden command, which is how this agent knows anything factual about a campaign or a clip. Not a skill to run on its own; every other warden skill calls it.
---

# The tool

You have a shell in this container, and `warden` is on its PATH. It is installed
by the image at `/usr/local/bin/warden`, root-owned, and it is the only sanctioned
way to read or change anything this agent knows.

```
warden status
```

Run that when a conversation starts cold. It prints where state lives, which
campaigns are stored, and whether ffmpeg and the transcription models are ready.

## The rule this agent rests on

**You may not state anything about a campaign, a clip or a verdict that you did
not get from `warden` in this conversation.** Not a duration, not a hashtag, not
"the campaign is saved", not "this clip is fine". You read prose and turn it into
a rule set; the tool decides everything measurable. A model that answers from its
own reading is guessing with someone's unpaid work.

This is not a style preference. The stored rule set is what every later step
reads: the checker, the renderer, the caption, the cap. A campaign that lives
only in this conversation does not exist, and the next clip is rendered against
nothing.

## What that means in practice

Before you say a campaign is stored, you have seen `stored and verified` with a
path. Before you say a clip passes, you have seen the verdict block. Before you
say how long a clip is, you have seen the number ffprobe returned. If a command
fails, the person hears that it failed, in one sentence, and never hears a result
you did not get.

## The commands

| | |
| --- | --- |
| `warden status` | state, stored campaigns, whether ffmpeg and the models are ready |
| `warden schema` | the rule set structure a campaign skill fills |
| `warden campaign save --json '<json>'` | store one, verified by reading it back |
| `warden campaign list` / `show <id>` | what is actually stored |
| `warden fetch <url>` | a brief as readable text |
| `warden discover` | the public campaign directories, as text |
| `warden prefs ask --group search\|edit` / `set` / `show` | what this owner wants |
| `warden lote prep <url> [--campaign <id>] [--n 2] [--seconds 20]` | **start here.** the gate, the words, the digest and the signals in ONE output, with the defaults it will use printed. No questions |
| `warden lote render <url> --windows <a>-<b>,<c>-<d> --hooks 'x\|y'` | the other half: pulls the windows, renders the clips, ONE combined contact sheet (written, not a gate since 16/09), and every `MEDIA:` line printed together at the end |
| `warden archive --campaign <id> --text-first` | the words only, by hand: published subtitle, or audio. No video |
| `warden archive --trusted <url> --text-first` (or `--link`) | the same, for a bare link. **The link having been sent IS the authorisation** -- there is no list to check and nothing to ask |
| `warden archive … --windows <a>-<b>,<c>-<d>` | the whole batch of windows in one run: 31s against 38s for two |
| `warden archive … --window <a>-<b>` | one window, with the origin card beside it |
| `warden archive --campaign <id>` | the WHOLE file. The exception -- `warden-clip` 4c says when, and it is the only place that number lives |
| `warden cut … --seconds <n>` / `--any-length` | the duration the person said. Neither one: `cut` uses the stored default (20s), inside the campaign's limits, and prints which number it used and where it came from |
| `warden delivered [<clip>]` | reads the conversation's own record: did THIS path go out, in a final message? Run it at the **START of the next turn** -- it cannot confirm a send that has not happened yet. When it cannot verify, it says so instead of confirming |
| `warden authorize <url> --campaign <id>` | is this link in the campaign's archive? exit 1 = no |
| `warden trusted add\|check\|list <x>` | legacy. A link someone sent needs no entry here |
| `warden transcribe <file>` / `digest <transcript>` | words, then words you can afford |
| `warden signals <transcript> --source <file>` | where the words and the sound spike: hooks, conflict, reactions |
| `warden beat <track>` | tempo, grid and drop |
| `warden cut <src> --campaign <id> --start --end --out` | render inside the rules |
| `warden check <clip> --campaign <id> --caption -` | the gate; exit 1 means do not post |
| `warden package --campaign <id> --hook "..."` | the caption the campaign requires |
| `warden log --campaign <id> ...` | this install's own count, which the cap reads |
| `warden captions review <srt> --start --end` | the lines that would burn; `--approve` signs exactly that window |
| `warden tracks add <file\|url>` / `list` | the owner's own audio, kept by name, measured once. A link goes in like a file |
| `warden style extract <clip>` / `check <render>` | the measured style of an approved clip, and whether a new one is inside it |
| `warden voz [--since <ts>]` | how many messages each request actually cost the person; **exit 1 above four** |
| `warden inbox --wait 12` | the link the message promised but did not carry. It ALWAYS comes as its own message a moment later, so you wait instead of asking — but twelve seconds and not more: this gets shown on a screen-share call, where a minute of silence is worse than a question. Returns the instant the link lands. Exit 1 if none came |
| `warden youtube status` / `connect` | is the owner's channel connected; `connect` prints a google.com/device code and waits for the approval |
| `warden youtube publish <clip> --title "..."` | uploads to their channel. **This is not publishing**: until Google audits this app the video is locked as private, the owner cannot make it public and cannot appeal. The privacy it prints is what YouTube RETURNED |
| `warden post status` | is a publishing intermediary configured on this install, and which account is connected. Exit 1 here means no key, and the persona's "Publishing" section says what to offer |
| `warden post youtube <clip> --title "..." [--description "..."]` | **the road that actually publishes.** Goes through an intermediary whose app Google already audited, so the video comes out PUBLIC on their channel. Measured 15/09 with a real upload. Exits 1 saying it is switched off when this install has no key |
| `warden tiktok <clip> [--campaign <id>] [--hook "..."]` | puts the clip in the owner's TikTok inbox (not the Drafts tab) and prints the caption to paste. Needs a token for that one account |

Every one of them accepts `--help`. None of them needs a path you have to guess.
