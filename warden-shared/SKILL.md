---
name: warden-shared
description: The warden command, which is how this agent knows anything factual about a campaign or a clip. Not a skill to run on its own; every other warden skill calls it.
---

# The tool

`warden` is on your PATH, root-owned, and is the only sanctioned way to read or
change anything this agent knows. Run `warden status` on a cold start.

A `warden-<skill>/references/<file>.md` path in any skill is opened with
`skill_view`, not with the shell.

**You may not state anything about a campaign, a clip or a verdict that you did
not get from `warden` in this conversation.** Not a duration, not a hashtag, not
"the campaign is saved", not "this clip is fine". You turn prose into a rule
set; the tool decides everything measurable. So: `stored and verified` with a
path before you say one is stored, the verdict block before you say a clip
passes, ffprobe's number before you say how long it is. A command that fails is
repassed as a failure, in one sentence.

## The commands

Every one takes `--help`, so a row says what a command is FOR, not its flags.

| | |
| --- | --- |
| `warden status` | state, stored campaigns, ffmpeg and the models |
| `warden schema` | the rule set structure a campaign skill fills |
| `warden campaign save --json '<json>'` / `list` / `show <id>` | store one, verified by reading it back; then what is stored |
| `warden fetch <url>` / `discover` | a brief as readable text; the public campaign directories |
| `warden prefs ask --group search\|edit` / `set` / `show` | what this owner wants |
| `warden lote prep <url> [--campaign <id>] [--n 2]` | **start here**: the gate, the words, the digest and the signals in ONE output |
| `warden lote render <url> --windows <a>-<b> --hooks 'x\|y'` | the other half: windows, clips, one contact sheet, and a final block with every `MEDIA:` line. **That block is your message** |
| `warden archive … --text-first` (`--campaign <id>` or `--trusted <url>`) | the words only: published subtitle, or audio. No video. **Having been sent IS the authorisation** |
| `warden archive … --windows <a>-<b>,<c>-<d>` / `--window <a>-<b>` | the chosen windows, each with its origin card |
| `warden archive --campaign <id>` | the WHOLE file; `warden-clip` 4c says when |
| `warden delivered` | anything rendered here and never sent? Exit 1 while there is. **Run it before you render anything new** |
| `warden delivered <clip>` | did THIS path go out? At the **START of the next turn**: it cannot confirm a send that has not happened |
| `warden authorize <url> --campaign <id>` | is this link in the archive? exit 1 = no |
| `warden transcribe <file>` / `digest <transcript>` | words, then words you can afford to read |
| `warden signals <transcript> --source <file>` | where words and sound spike |
| `warden cut <src> --start --end --out [--subtitles] [--hook] [--crop]` | render inside the rules |
| `warden cut … --seconds <n>` / `--any-length` | the duration they said. Neither: the stored default, printed with where it came from |
| `warden check <clip> --campaign <id> --caption -` | the gate; exit 1 means do not post |
| `warden package --campaign <id> --hook "..."` | the caption the campaign requires |
| `warden log --campaign <id> ...` | this install's own count, read by the cap |
| `warden captions review <srt> --start --end` | the lines that would burn; `--approve` signs that window |
| `warden tracks add <file\|url>` / `list` | the owner's own audio, kept by name. A link goes in like a file |
| `warden beat <track>` | tempo, grid and drop — the numbers `--shots` snaps to |
| `warden style extract <clip>` / `check <render>` | the approved look; whether a new clip fits |
| `warden voz [--since <ts>]` | what a request cost in messages; **exit 1 above four** |
| `warden inbox` | the link the message promised but did not carry. Waits the owner's own number of seconds — short because this runs on a screen share, where a minute of silence is worse than a question — and returns the instant one lands. Never pass `--wait` |
| `warden youtube status` / `connect` | is their channel connected; `connect` prints a device code |
| `warden youtube publish <clip> --title "..."` | **not publishing**: until Google audits this app the video stays private, no appeal |
| `echo "<key>" \| warden post setkey` | how the PERSON'S OWN key gets in. Standard input only — an argument sits in `ps` and in the shell history |
| `warden post connect [<profile>]` | ONE address to hand them: they pick the channel on Google's own screen, and type their password there |
| `warden post status` | is an intermediary configured, which account, and where the key and the profile came from. Exit 1 = no key |
| `warden post status <request_id>` | that ONE upload: the queue, then the address |
| `warden post youtube <clip> --title "..."` | **the road that publishes**: an audited intermediary, so the video comes out PUBLIC. A title over 100 characters is REFUSED, not truncated |
| `warden post tiktok\|instagram <clip> --title "<caption>"` | the same endpoint, other networks; `--draft` for TikTok, `--stories` for a Story |
| `warden post <net> … --also tiktok,instagram` | more networks on the SAME upload, one address each, exactly as the API returned them |
| `warden tiktok <clip> [--campaign <id>]` | the owner's TikTok inbox, not the Drafts tab, plus the caption to paste |

## Publishing

`warden post status` prints **where the key came from and where the profile name
came from** — never the key. Read that origin when the channel is not the one
they expected: a key from the environment is the agent owner's, and then the
profile has to be **this machine's alone** (`clip-warden-<8 hex>`, fixed on the
first connect). One shared name puts every install on one channel.

**No key: ONE message, three numbered steps, the links written out.** The words
are yours, in their language; the links and the order are not.

1. a free account at `https://app.upload-post.com` — no card asked
2. `https://app.upload-post.com/api-keys`, "Generate New API Key", copy it
3. they paste it here and you run `echo "<key>" | warden post setkey`, then
   `warden post connect` in the same turn, without another round trip

That message carries the clip, the title and the description. **The title you
write there is the string that goes into `--title` later, so it fits in 100
characters** — over that the command refuses, it does not cut.

**What each network costs, and what a `200` is worth:**

- **YouTube** is the only road ever measured with a real upload.
- **TikTok** needs **a paid plan**. `--draft` puts the clip in TikTok's drafts,
  and in draft mode TikTok IGNORES the caption and the privacy the API sent —
  they write the caption in the app. There is no `unlisted`, and the command
  refuses it rather than pick a meaning for it.
- **Instagram** needs a **Business or Creator** account, and has no per-post
  privacy at all, so anything but `--privacy public` is refused, not obeyed.
- The caption limit on both is **2200 characters, refused and never cut**.
- **No upload from this project ever went through TikTok or Instagram.** The
  field names are the provider's OpenAPI spec and nothing stands behind them:
  report what the API returned and claim no more.

Measured against only read off a spec, and the three steps as a mould:
`warden-shared/references/postagem.md`.
