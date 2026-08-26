---
name: draft-voice
description: "Apply the user's own voice and formatting rules when drafting an outgoing informal peer message on their behalf — a Teams reply, a Slack-style ping, a PR comment to a teammate. Use when asked to 'draft a reply', 'write a Teams message', 'draft a Slack message to my teammate', 'write a PR comment', 'respond to this PR comment', 'draft a message to my coworker', or similar. Do not use for formal drafts (email to a director, a written PR description, a public README) — ask before applying these rules there."
---
Scope check first: informal peer chat only. If the draft is for anything
more formal — email to a director, a written PR description, a public
README — stop and ask before applying anything below.

1. Match the user's voice, not your own. This governs the drafted outgoing
   text only — your own conversational reply back to the user still follows
   your normal output style, unchanged.

2. Lead with the cover the user already has. If they've told you why they
   did something (an issue author pointed them at a branch, a prior
   decision, etc.), open the draft with that context. Don't bury it under a
   neutral framing that makes them look like they're justifying a solo call.

3. Apply these mechanics:
   - No dashes at all: no em-dashes, en-dashes, or ASCII hyphens as
     sentence-connectors. Break into two sentences or use a comma instead.
   - Lowercased. Sentence-initial lowercase is the default. Proper nouns
     stay capitalized where dropping the cap would read wrong (real names,
     product names).
   - Short sentences. Prefer two short sentences over one compound one.
   - Drop softeners, filler, and throat-clearing openers: no "I was just
     thinking maybe we could...", no "actually/essentially/basically", no
     "Yeah, so...". "fyi", "lmk", "ah ok", "sounds good" are fine.
   - Plain verbs, no cute phrasing. If a metaphor would need explaining, or
     it reads like something you generated rather than a phrase the user
     would naturally use, cut it.
   - Structure only when it earns its keep. Bullets are fine for 2+
     genuinely parallel items. Don't bullet a single point, don't use
     headers in a chat message.

4. Hand back the draft for the user to review and send themselves — this
   skill drafts, it doesn't send.
