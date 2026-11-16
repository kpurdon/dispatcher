---
name: dispatch
description: Launch, inspect, message, and retire initiative-scoped agent sessions through iTerm2 or T3, with explicit membership and stable backend identities.
---

# Dispatch agent sessions

Use [registry.py](scripts/registry.py) with Python 3.10+ to keep one private local registry outside repositories. Consult `--help` for commands. The caller supplies the initiative, verified lead identity, assignment, and authority. Builder owns delivery policy; Dispatcher owns membership and native session mechanics. It does not restart agents or decide completion.

## Select the interface

Honor an explicit interface request. Otherwise, continue the initiative's recorded backend. For a new initiative, verify the originating interface: in T3, `t3_thread_list` reports `currentThreadId` and `orchestrator_capabilities` reports the bound caller; in iTerm, use the [tabs integration](../tabs/references/codex.md) to verify the origin. A `T3CODE_THREAD_ID` or terminal environment variable is only a hint. Installed CLIs and reachable MCP tools do not establish origin. Use the user's configured default when origin is unknown; resolve conflicting evidence before launching. Never switch backend after a failed or uncertain request.

Initialize with `init --backend ... --workspace ... --record-path ...`. Supply the same `--registry`, `--initiative`, and `--actor` on later calls. Use a native caller ID, not its title; the actor must equal the recorded lead. An explicit per-launch backend override affects that member only. Existing members retain their backend.

## Membership and native calls

`list` returns only this initiative's members. Never discover PM assignments by enumerating all host sessions or matching repositories, directories, titles, or UI groups. The initiative and the UI group are distinct. Adoption requires an explicit user selection or authorized transfer, an exact backend inspection result, and recorded authority/evidence. A dependency blocker involving another initiative does not confer membership.

For a new assignment, `prepare --action launch --request-key ... --assignment ... --spec-file ...` reserves one member and returns a native call. For an existing member, use `prepare --member ... --action inspect|message|retire`. The helper checks scope before returning a call. Execute that exact `call` through the selected backend, save the native JSON result privately, then `complete --request-id ... --result-file ...`. The helper returns a command argument array or a tool name and arguments; never interpolate either into shell source. Use the installed native tool with that name, including its server prefix. If unavailable, report the limitation and retain the request; do not substitute another interface.

Reusing a request key returns its existing identity without another executable call. `list` includes pending request IDs; `request --request-id ...` reads the saved plan for reconciliation, not redispatch. For a lost T3 launch reply, `replay-launch` returns only the original call with unchanged `clientRequestId`, which T3 deduplicates. This does not apply to iTerm launches or follow-up messages. Do not retry with a new key or assignment label to bypass uncertainty. Reconcile on the original backend. Retain native results on registry failure so they can complete the same request. Only `cancel --no-effect-evidence ...` when the call was never dispatched or the backend proves no resource/action resulted. A timeout, missing acknowledgement, or failed read is not that proof.

Launches carry the member and request IDs in their initial assignment. For iTerm, Dispatcher preserves the caller's brief and makes a private durable copy beside the registry with this context. Associate the receiver's Codex ID through that exact launch context and native evidence, never through a similar title. Do not add routine progress or completion reporting to establish membership.

For transfers, stop the previous writer and obtain the named successor's acceptance of remaining obligations before `transfer`. Record that evidence. Only the recorded lead performs registry mutations; receivers can supply their verified native identities through a permitted session channel when needed.

## Backend details

Read the selected reference before preparing its calls:

- [iTerm2](references/iterm2.md): background tabs, grouping, Codex identity, and guarded closure.
- [T3](references/t3.md): native project/worktree binding, queued messages, and verified archiving.

Before retirement, the delivery owner verifies acceptance, cancellation, or accepted transfer and checks active descendants and unfinished work. Put evidence in `--evidence`. After executing retirement, `complete` requires exact-target closure/archive readback. Session retirement and worktree deletion are separate; Dispatcher never deletes worktrees.

The registry is a coordination boundary, not a sandbox. Caller identities and receipts must come from observed native results. Direct native tools remain available, so do not use them to bypass membership checks. Existing standalone `$tabs` operations remain available for explicit terminal work outside PM management.
