---
name: router-mode
description: Inspect or override the dsh-router reasoning-mode routing for this session — show the current band, stage, and working-style directive, or force a band (spec / react / mixed / weak), or turn the router on or off. Use when the user asks how the session is being routed, wants a different working style for this session, asks to check or change the router mode, says the router is misrouting the task, or wants the router disabled.
---

# Router mode control

You are inspecting or changing the `dsh-router` plugin, which classifies each
user prompt and injects a working-style directive near-field.

## Current state

Read the state file for this session and the plugin config. Both live in the
plugin data directory ZCode gives the hooks as `ZCODE_PLUGIN_DATA`:

- Directory: `~/.zcode/cli/plugins/data/dsh-router@local-dsh-router/`
- Config: `config.json` in that directory
- Session state: `sessions/<session_id>.json` in that directory

Read both with the Read tool (the session id is in your environment as the
current session). If a file is missing, the defaults apply: router enabled,
mode `auto`, stage 0.

Report in one short block: **band** (the routed working style), **stage**
(0-3 and its name), **reason** (why the band was chosen: `classified:first`,
`classified:ambiguous`, `locked`, `config:*`, or `classified:upgrade-from-weak`),
and **task** (the echoed first task).

## The four bands

| band | what it conditions | when it is chosen |
|---|---|---|
| `react` | hands-on production: build, verify, fix; no unrequested ceremony | prompt is build-heavy (`创建`/`写一个`/`build`/`create`/`实现`…) |
| `spec` | inspect-and-plan: ground in the real code, minimal compatible change | prompt is fix-heavy (`修复`/`调试`/`重构`/`fix`/`debug`…) |
| `weak` | route yourself: the model decides per task | keywords tie or none match — the ambiguous fallback |
| `mixed` | transition band | never chosen automatically; explicit opt-in only |

`mixed` is deliberately never auto-selected. Upstream measurement found the
transition band unstable (behavior mixes trajectories), so it is reachable
only by asking for it.

## Changing the mode

Write `config.json` in the plugin data directory
(`~/.zcode/cli/plugins/data/dsh-router@local-dsh-router/`). Only the keys you set
are used; the rest keep their defaults.

```json
{ "mode": "react", "verbosity": "full", "enabled": true }
```

- `mode`: `auto` (classify per session, the default) or a forced band —
  `spec`, `react`, `mixed`, `weak`, or a number (`0` spec, `30`/`0.3` mixed,
  `100`/`1` react). A forced mode is stamped into every injected block as
  `route=config:<value>`.
- `verbosity`: `full` (directive + stage guide + proactivity), `compact`
  (routing decision and stage line only), `off` (inject nothing).
- `enabled`: `false` disables every hook.

**The change takes effect on the next user message** — hooks are read per
event, so no restart is needed. The band already locked for a running session
stays locked unless you also reset the session state file; delete
`sessions/<session_id>.json` in the plugin data directory to force a fresh
classification on the next message.

Tell the user what you changed and that it applies from the next message.

**The injected block is not visible in the chat UI.** It is delivered to the
model as additional context on that request, so "I don't see it" is expected.
Point the user at this state file, or at `/router-status`, to see the routing.

## What this plugin does NOT do

State this plainly if the user asks about tool gating: upstream
(`dsh-routing-suite`) also gates the tool catalog per stage — tools stay
invisible until their stage opens. ZCode exposes no hook that can add or
remove tools, so this port reports the stage and conditions the working style
but **never restricts which tools are available**. Do not claim otherwise.
