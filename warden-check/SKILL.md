---
name: warden-check
description: Decide whether a finished clip may be posted to a campaign. Use before any post, when the person asks if a clip is within the rules, after an edit, or when a submission was rejected and they want to know why.
---

# The gate

`warden check <file> --campaign <id> --caption -` with the caption on stdin.

Exit 0 means nothing mechanical blocks it. Exit 1 means do not post.

## Reading the verdict out

Lead with the answer, then the blocking lines, then what only they can confirm.
Do not paraphrase a measurement; the tool already stated it in seconds and
pixels and those are the words to use.

The `CHECK` lines are not filler and you never bury them. They are the rules no
script can see: whether the footage really came from the authorised archive,
whether official material fills the share of screen the brief demands, whatever
the brief left open. A verdict that hides its blind spots is how someone learns
a rule from a rejection notice.

## When it rejects

Say which single change fixes it. "Four seconds too long, cut it to 30" beats a
list. If the fix is a re-render, offer to do it: you know the source and the
window.

## When they were rejected by the campaign itself

Ask what the campaign said, then put that reason into the rule set so it is
caught next time. A rejection reason that does not end up in `unknown` or in a
field is a rejection that will happen again.
