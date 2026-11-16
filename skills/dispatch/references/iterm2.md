# iTerm2 sessions

The existing [tabs helper](../../tabs/SKILL.md) retains its command line and background grouping behavior. Read its [Codex integration](../../tabs/references/codex.md) for origin verification. The scoped dispatcher launch returns a `uv run --quiet --script .../tabs.py open` argument array with `codex --` and an assignment file. Launch arguments are not shell source.

Supply a launch specification like this, using verified paths and IDs:

```json
{
  "title": "ISSUE-123: Implement cancellation",
  "cwd": "/workspace",
  "brief_file": "/private/brief.txt",
  "group_id": "verified-group-id"
}
```

Select exactly one of `group_id`, `origin_session`, `window_id`, `codex_origin: true`, or `desktop: true`. `group` can name an initial group when not passing `group_id`. Use `desktop` only without an originating terminal. Inspect destinations through the tabs skill when establishing or repairing the destination, not as a PM's session inventory. Preserve the caller's workspace for discovery/PM sessions; use the verified target worktree for a repository-specific executor when the assignment selects that model.

Complete the launch with the helper's JSON. Its `request_id` must match the prepared request. `launch: accepted` binds membership even if grouping needs repair; neither confirms agent startup. An uncertain launch stays pending. Use `reconcile --request-id ... --result-file ... --evidence ...` with an exact `tabs.py inspect` result only after correlating the receiver's native launch context with this request/member or obtaining an explicit user selection. Similar titles alone are insufficient. This binds the existing reservation without relaunching or creating another member.

`inspect` reads only the recorded tab. For Codex conversation status, use the host's native `read_thread` with the recorded Codex ID. The receiver must provide its native thread ID with evidence associating it with the launched terminal. The lead records it with `bind-codex`. Titles alone cannot establish that association. `message` then resolves to the host's `send_message_to_thread`; if the tool is absent, report messaging unavailable. The native tool accepts at most 1,000 UTF-8 bytes; reference a private assignment file for longer follow-ups.

Retirement uses normal iTerm confirmation. It refuses to close if the tab now has a different pane or additional panes, preserving unrelated work. Complete it with `close: confirmed` for the recorded tab or, after an uncertain close, an exact inspection reporting `present: false`. Archiving the native Codex conversation is a separate host action: verify the exact ID and descendant effects before doing it within authority, and keep it pending if that interface is unavailable. Do not claim closing a tab archived its conversation or deleted its worktree.

For explicit adoption, use `tabs.py inspect --tab-id ...` and save its JSON. Adoption requires one existing pane and explicit authority/evidence. Add a native Codex binding only from a verified receiver identity.
