# Changelog

## 2.2.2 - 2026-10-27

- Recover structured model-capacity failures (including the observed `serverOverloaded` error) through the existing opt-in resume monitor, retaining the original model and manual-stop checks.
- Wait 3 minutes initially; carry exponential backoff only across the exact automatic continuation that failed, capped at 30 minutes and 6 attempts per continuous failure chain.
- Persist an attempt before dispatch, never requeue the same failed turn after an acknowledgement, and quarantine ambiguous legacy capacity records instead of resending them. Unchanged waits no longer rewrite the journal.
- Validation: all 136 tests passed, including 11 capacity-specific fixtures. Real Desktop snapshots confirmed the screenshot's error code; that task was already active and received no test continuation. Natural capacity recovery was not forced.

## 2.2.1 - 2026-10-21

- `usageLimited` goals are no longer treated as a dead end. The Desktop client resumes such a goal by setting it back to `active`, and its turn channel (`thread-follower-start-turn`) has no goal-status guard, so a normal continuation turn is legal; the old code disabled the failure identity for that status and parked the task at "暂不可恢复" forever. Those records now re-enter the waiting queue on load.
- Goal states the user or the runtime owns — `paused`, `blocked`, `budgetLimited`, `complete` — still disarm recovery, and a recorded `blocked` item migrates to `waiting` instead of being dropped.

## 2.2.0 - 2026-10-20 (local candidate)

- Add opt-in quota recovery for ordinary Desktop turns, using live owner snapshots, structured quota errors, fresh account permission checks, persistent cancellation and dispatch identities, and an OS singleton lock.
- Add a quota recovery window with a toggle, task state, cancel-this-recovery and check-now actions. Minimized console reduces statistics refresh and UI wakeups.
- Fail closed for manual interruption, paused goals, changed turns, unknown quota state and unconfirmed dispatch. Native usageLimited Goals remain blocked because this Desktop IPC version exposes no safe Goal-resume operation.
- Validation: 122 unit/integration fixture tests; live read-only owner and quota reads; Tk window lifecycle; 20-second read-only listening sample used 0.0469 CPU seconds. A real quota exhaustion/reset cycle and native Goal recovery remain unverified/unavailable.

## 2.1.0 - 2026-10-18

- The bridge now gives the client's tools to Codex as native `dynamicTools` (an experimental app-server surface, opted into at `initialize`). luna calls `write`/`pwsh` directly and the gateway answers each `item/tool/call` with "handed to the runtime", so the whole "emit decision JSON" protocol is gone from this path: no replaced system prompt, no schema-forced answers, no re-describing the tool catalogue every turn.
- Threads keep Codex's own agent prompt as `baseInstructions` for the native loop, and the sandbox stays `read-only` on purpose: Codex's own file and shell tools must fail, so every real action goes through the runtime instead of bypassing the client's permission policy.
- Verified: a design task produced a 19 KB `write` call in one turn, and the follow-up turn answered in 7.6 s.

## 2.0.3 - 2026-10-18

- Stop handicapping the model: the bridge protocol is now appended as `developerInstructions` instead of replacing Codex's `baseInstructions`. Measured on the same design task, replacing the base prompt took 268 s and produced a 16 KB payload, while keeping it took 110 s and produced 11.7 KB — the native prompt carries the planning and coding competence luna is tuned for.
- The protocol text also states that this turn's tools are the ones listed, so the model stops reaching for Codex's own shell/patch tools that the bridge does not execute.

## 2.0.2 - 2026-10-18

- Stop the "filesystem is read-only" answer: the bridge prompt now states that the runtime holds full permissions, that Codex's own sandbox is irrelevant because the model never executes anything, and that file creation must go through a `tool_calls` decision. Verified: a create-file task now returns `write` with the full content instead of pasting HTML into the answer.

## 2.0.1 - 2026-10-18

- Fix warm-mode quality: the persistent thread now receives the bridge protocol as its `baseInstructions` (previously Codex's own agent prompt stayed in charge while our rules sat in one old user message), every turn carries the decision JSON schema via `outputSchema`, and the tool catalog is re-sent each turn because it changes. A two-turn check now returns a proper `tool_calls` then a `final` that reads the tool result correctly.

## 2.0.0 - 2026-10-18

- The gateway now keeps Codex's `app-server` warm instead of spawning `codex exec` per request. Measured: first turn 13-15 s (thread creation), every following turn **1.5-2 s** instead of 25-45 s, with 23k of 23k prompt tokens served from the provider's prefix cache.
- Falls back to `codex exec` automatically when the app server cannot start or a turn fails (`CAC_DISABLE_WARM=1` forces the old path); a failed warm attempt is not retried for five minutes.

## 1.9.1 - 2026-10-18

- Report Codex's `cached_input_tokens` as `prompt_tokens_details.cached_tokens`, so the client's cache-hit indicator reflects reality instead of a permanent 0%.

## 1.9.0 - 2026-10-18

- DSH can now consult DeepSeek from inside a luna session: the one-click DSH wiring also appends an `insert` patch for `@deepseek-ai/dsh-mcp-client` pointing at `--mcp-advisor`, so the main model gets `ask_deepseek`. Verified end to end (9.5 s round trip, session memory).
- Auto-configures the advisor endpoint from a saved API by probing each candidate before choosing, and finds portable DSH installs by directory name when nothing is registered.
- MCP replies are ASCII-escaped so any parent decoder reads them correctly.

## 1.8.4 - 2026-10-18

- Stop the stray terminal window: a windowed build has no console, so every `codex exec` call used to allocate one. Gateway and advisor subprocesses now pass `CREATE_NO_WINDOW`.

## 1.8.3 - 2026-10-18

- Fix the empty-token case: the DSH provider route now always names `apiKeyEnv`, because pi-ai refuses any route without a credential reference even when the local gateway accepts unauthenticated calls. The console still writes the matching `CODEX_GATEWAY_TOKEN` into `~/.dsh/.env`.

## 1.8.2 - 2026-10-18

- One-click DSH wiring now also writes the credential: the model route only loads when `CODEX_GATEWAY_TOKEN` resolves, and DSH reads it from `$DSH_HOME/.env`. The gateway window rewrites both `cordis.patch.yml` (provider + subagents, replacing its own old entries instead of appending) and `~/.dsh/.env`, with backups.

## 1.8.1 - 2026-10-18

- Explain and fix the missing DSH model: the pi-ai route only loads when its credential resolves, so a gateway token that is not present in DSH's environment hides the model entirely. The gateway window now exposes "写入环境变量" (user-level `CODEX_GATEWAY_TOKEN` via setx) and states the requirement in the snippet hint.

## 1.8.0 - 2026-10-18

- The DSH snippet can now pin DSH's own subagent tools (`tool-subagent`, `tool-subagent-fork`) to the DeepSeek route via `agentOptions`, so the gateway's costly model stays the main model while delegated work runs on DeepSeek. Controlled by a checkbox in the gateway window.

## 1.7.2 - 2026-10-18

- Disambiguate the two "sub-models": the settings page now labels its second role "Codex 子代理" and states that the page configures Codex's own models, while the DeepSeek advisor lives in "接入 Codex". The advisor window also notes that the two chains are independent and recommends single-agent mode when using the advisor.

## 1.7.1 - 2026-10-18

- Clarify in the DSH gateway window that its model/effort only affect gateway calls, while the main window's 运行设置 controls the Codex app itself; the two no longer look like one setting.

## 1.7.0 - 2026-10-17

- Add the reverse bridge: Codex keeps the expensive model as the main model, while a persistent DeepSeek advisor runs as the sub-model. The `deepseek_advisor` MCP server exposes `ask_deepseek(question, context)` and `advisor_session(action)`, and resumes one long-lived `dsh --profile headless --session-id …` session per project.
- The advisor keeps its own tools and working directory, so it reads files itself and can edit them; the main model only sends the concrete question plus file paths.
- "接入 Codex" window: provider endpoint/model/key (DPAPI-encrypted), DSH install detection, one-click MCP registration in `config.toml` and an AGENTS.md policy block that defines when consultation is mandatory, per-project session list with reset, and a live trial question.
- Consultation failures return an error to the main model, which continues and states that the advisor was unavailable.
- The packaged executable doubles as the advisor MCP server (`--mcp-advisor`), and the MCP registration points at the exe itself when running frozen.
- Advisor thinking levels are limited to the three the endpoint accepts (`low`/`high`/`max`); `medium` and `xhigh` are rejected by `deepseek-v4.1-flash`.

## 1.6.0 - 2026-10-17

- Add "接入 DSH": a loopback OpenAI-compatible gateway (`/v1/models`, `/v1/chat/completions` with SSE) that serves a Codex model to another agent runtime. Each model call runs one `codex exec`, renders the caller's system prompt, transcript, tool results and tool catalogue into a decision prompt, and returns either a final message or OpenAI `tool_calls`.
- The calling runtime keeps executing tools; the gateway only decides. Requests can be bounded by a concurrency limit, a timeout and an optional bearer token, and the server binds to 127.0.0.1 only.
- The gateway window shows the endpoint, a ready-to-paste `cordis.patch.yml` provider entry, a live request log, an optional start-with-console toggle and a guarded "write DSH config" action that backs up and YAML-validates the profile before appending.

## 1.5.0 - 2026-10-17

- Reverse the dual-model roles: the subagent is now the high-capability planner and advisor (goal, ordered steps, files, risks, acceptance checks), while the primary model performs the hands-on implementation and verification, and escalates back to the planner only after repeated failures or a genuine design tradeoff.
- Populate the model pickers from Codex's own `models_cache.json` and filter the reasoning levels per model, so newer models such as `gpt-6.1-sol` are selectable and the loaded config value is always listed.
- Warn when the planner model is not a higher tier than the executor model.
- Default new installations to a cheaper executor plus a high-capability planner.
- Rebuild the main window as one page: a settings column (mode switch, executor/planner model pickers, concurrency) beside a statistics column (KPI cards and a model-distribution table). The previous tabbed layout hid half the app and left the settings page mostly empty.
- Size the initial window and the minimum size from the measured content and the real screen; the previous fixed 1040x720 window silently truncated the action buttons at 900 px width.
- Parse session logs incrementally: a growing rollout log now only reads the appended bytes, and the scan state is cached on disk. Refreshing after an append on a 213 MB log drops from ~3 s to ~0.02 s, and the first paint after a restart drops from ~6.3 s to ~0.13 s.
- Skip session lines that cannot contain session metadata, token counts, turn context or model settings before JSON decoding.
- Move worker-thread UI updates onto a main-thread queue so refreshes and diagnostics progress no longer call Tk from a background thread.
- Throttle persistent-cache writes to once per 30 s and flush on exit.
- Keep the automatic statistics refresh at 2 s and skip redundant repaints.
- Restore the one-click "启用双模型" action alongside "恢复普通模式".

## 1.4.1 - 2026-08-20

- Ignore SSE heartbeat/comment lines during custom API benchmarks so compatible Claude Messages responses are not incorrectly parsed as plain JSON errors.

## 1.4.0 - 2026-08-19

- Add native Claude Messages (Anthropic) API detection with `x-api-key` authentication, Anthropic version headers, model discovery, streaming response parsing, usage parsing, and prompt-cache measurement.

## 1.3.9 - 2026-08-19

- Add an opt-in custom API cache-hit benchmark with a cache warmup request, a repeated measurement request, and cache-hit reporting when the provider exposes input usage.
- Correct execution-agent Token totals for forked sessions that embed a primary-session usage history, while preserving clean forked-session totals.

## 1.3.8 - 2026-08-19

- Fix the “Today” Token filter to start at the local computer's midnight instead of including the previous 24 hours of sessions.
- Keep the 7-day and 30-day filters as rolling windows.

## 1.3.7 - 2026-08-19

- Fix execution-agent model inheritance: delegations now explicitly use `fork_turns = "none"` together with the configured subagent model and reasoning effort, instead of inheriting the primary model through a full-history fork.
- Change dual-model orchestration to a teacher-executor loop: the main model gives concise guidance, reviews the result, requests focused corrections, and only takes over remaining work when the executor cannot finish.
- Add a completion-first rule so implementation tasks must progress through available tools and verification instead of ending with only a plan or advice.
- Add an "Apply to Desktop" action that reloads only the detected Windows Desktop `app-server`, so new tasks can use saved settings without closing the Desktop window.

## 1.3.6 - 2026-08-19

- Correct execution-agent statistics when a forked subagent session embeds parent session metadata.
- Read the current Codex `effort` and `thread_settings.reasoning_effort` fields so actual main and execution model cards show the configured reasoning level.
- Preserve the execution subagent's own start time for accurate latest-model reporting.

## 1.3.5 - 2026-08-18

- Move dual-model routing from `config.toml` developer instructions to the documented global `~/.codex/AGENTS.md` instruction chain, using a non-empty `AGENTS.override.md` when it is active.
- Preserve user-authored global guidance while adding or removing only the console-owned policy block.
- Route clear, low-risk writing and transformation tasks to the execution subagent as well as coding tasks.
- Migrate and remove the ineffective legacy policy block from `developer_instructions` on the next save.

## 1.3.0 - 2026-08-18

- Add Chat Completions, Responses API, and legacy Completions endpoint types.
- Generate type-specific request bodies and parse Chat/Responses/Completions streaming events.
- Replace the required full endpoint URL with a provider base URL and automatic path completion.
- Add background model discovery through the OpenAI-compatible `/v1/models` endpoint.
- Improve entry-field contrast so entered values remain readable in the dark UI.

## 1.2.0 - 2026-08-18

- Add persistent multi-select benchmarking for OpenAI Chat Completions compatible APIs.
- Add Bearer, `x-api-key`, and unauthenticated endpoint support with Windows DPAPI encrypted API keys.
- Report success rate, first response, total latency, latency jitter, output Tokens, and Tokens/s.
- Rank the fastest and most stable APIs with reliability taking priority over raw speed.
- Add streaming SSE parsing, usage fallback estimation, cancellation, and detailed HTTP errors.
- Link official Codex subscription testing to the existing Codex CLI diagnostics flow.
- Clarify that ChatGPT subscription login and OpenAI Platform API keys are separate credentials.

## 1.1.0 - 2026-08-18

- Add a standalone API diagnostics window for main and subagent models.
- Add configurable targets, attempts, and request timeouts.
- Report connectivity rate, first response, API duration, CLI cleanup, Token usage, and output throughput.
- Classify authentication, unavailable-model, rate-limit, timeout, network, and CLI failures with per-request details.
- Discover the bundled Codex executable without reading authentication files or API keys.

## 1.0.1 - 2026-08-18

- Reduce automatic Token statistics refresh from five seconds to one second.
- Keep configuration changes immediate while clearly retaining new-task scope.

## 1.0.0 - 2026-08-18

- Add a native Windows GUI for Codex main-model and subagent defaults.
- Add dual-model orchestration and one-click normal-mode restoration.
- Add main/subagent token, cache-hit, and actual-model statistics.
- Add atomic `config.toml` updates with automatic backups.
- Add a portable one-file Windows executable build.
