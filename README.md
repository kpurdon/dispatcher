# Dispatcher

A local Codex plugin for initiative-scoped agent sessions in iTerm2 and T3. Builder owns assignments and delivery acceptance; Dispatcher records explicit membership, selects native session operations, and retains identities through launch, follow-up, and retirement. Sharing a directory, repository, title, or UI group never establishes membership.

| Skill | Purpose | Example requests |
| --- | --- | --- |
| [dispatch](skills/dispatch/SKILL.md) | Record initiative membership and prepare scoped native calls. | “Launch an executor in this initiative.”<br>“Inspect this initiative's sessions.” |
| [tabs](skills/tabs/SKILL.md) | Inspect destinations, open grouped background tabs, and close tabs by ID. | “Open a new tab in this project group.”<br>“Close the tab opened for this assignment.” |

iTerm2 requires macOS, `uv`, and its Python API enabled. T3 uses the existing native tools, injected inside T3 or through a configured MCP connection for outside callers. The registry uses Python 3.10+ and SQLite from its standard library. Install through the repository marketplace:

```sh
codex plugin marketplace add kpurdon/dispatcher
codex plugin add dispatcher@kpurdon-dispatcher
```

Builder can supply the assignment and retain delivery ownership while this plugin opens the terminal. See [Codex integration](skills/tabs/references/codex.md) for command construction and origin selection.

The registry lives at an explicit private local path outside repositories. Initialize an initiative with its lead's native identity, backend, workspace, and owning project record. The [dispatch skill](skills/dispatch/SKILL.md) documents launch specifications and native-result handling. It prepares calls rather than running another agent server. Run returned command arrays directly or call the named native tool, then record actual results. Pending requests survive caller failure and block duplicate assignment launches; reconcile them before retrying.

This replaces the `iterm2` plugin's package identity. Existing installed versions and running terminals remain unchanged until explicitly migrated. Install Dispatcher, verify `dispatch` and `tabs` discovery, then disable the old plugin for fresh sessions to avoid duplicate skill discovery. Do not uninstall or close running work as part of migration. The `tabs` helper and its existing command line remain available in Dispatcher. Existing sessions are not automatically adopted into initiatives.

To close a tab, inspect its ID with `list`, then pass that ID explicitly:

```sh
uv run --quiet --script skills/tabs/scripts/tabs.py list
uv run --quiet --script skills/tabs/scripts/tabs.py close --tab-id TAB_ID
```

Closure includes every split pane and can terminate running programs. Add `--force` to bypass iTerm's confirmation, or `--dry-run` to validate arguments without connecting. The helper reports `close: confirmed` only after verifying the tab is gone; an uncertain result exits with status 3 and should be reconciled with `list` before retrying. Closing alone does not require tab-group support.

Dispatcher's scoped retirement adds `--only-session` to refuse closing a tab with additional or replaced panes. `inspect --tab-id ID` returns only that tab. Run regression tests with `python3 -m unittest discover -s tests -v`.
