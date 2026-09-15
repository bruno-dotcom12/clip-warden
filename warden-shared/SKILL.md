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
| `warden archive --campaign <id> --text-first` | **start here.** the words only: published subtitle, or audio. No video. |
| `warden archive --trusted <url> --text-first` | the same, for a bare link the owner vouched for. One of the two gates is required |
| `warden archive … --windows <a>-<b>,<c>-<d>` | the whole batch of windows in one run: 31s against 38s for two |
| `warden archive … --window <a>-<b>` | one window, with the origin card beside it |
| `warden archive --campaign <id>` | the WHOLE file, 382 MB. The exception -- `warden-clip` 4c says when |
| `warden cut … --seconds <n>` / `--any-length` | the duration the person said. One of the two is **required**: no default, like sound |
| `warden delivered [<clip>]` | confirm a send that came back; with no path, **exit 1 while any cleared clip is still unsent** |
| `warden authorize <url> --campaign <id>` | is this link in the campaign's archive? exit 1 = no |
| `warden trusted add\|check\|list <x>` | the owner's trusted channels and domains, for clipping without a campaign |
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
| `warden inbox --wait <s>` | the link the message promised but did not carry, when it arrives a second later. Exit 1 if none came |

Every one of them accepts `--help`. None of them needs a path you have to guess.
