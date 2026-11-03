---
description: Show the dsh-router routing state for this session (band, stage, task, why) and how to change it
---

Report the current `dsh-router` routing state for this session.

Read these two files with the Read tool (the directory is
`~/.zcode/cli/plugins/data/dsh-router@local-dsh-router/`):

1. `config.json` — the plugin settings
2. `sessions/<current_session_id>.json` — this session's routing state

If either is missing, say which and report the defaults (enabled, mode
`auto`, stage 0, no band locked yet). Never claim a state you did not read.

Then give the user one compact block:

- **band** — `react` (hands-on production), `spec` (inspect-and-plan),
  `weak` (route yourself), `mixed` (transition), or "not locked yet"
- **stage** — 0/3 understanding, 1/3 planning, 2/3 development,
  3/3 verification
- **reason** — `classified:first`, `classified:ambiguous`, `locked`,
  `classified:upgrade-from-weak`, or `config:<value>`
- **task** — the echoed first task, if recorded
- **settings** — enabled / verbosity / mode

Then explain the bands briefly and, if the user wants a change, edit
`config.json` and tell them it applies from the next message. Changing the
band of a session that has already locked requires deleting
`sessions/<session_id>.json`; mention that only if they ask for it, since it
resets the stage too.

Note for the user's benefit: the injected routing block is **not visible in the
chat UI** — it is delivered to the model as additional context for that
request. This file is the way to see what was routed.

Be explicit that this plugin conditions the working style near-field and does
**not** gate tools — upstream gates the tool catalog per stage, ZCode exposes
no hook that can add or remove tools, so this port never restricts tools.
