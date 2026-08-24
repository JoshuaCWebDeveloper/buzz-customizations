# channel-context

This independent customization injects per-channel context into Codex turns that are framed by Buzz. The hook reads the `prompt` field from Codex's `UserPromptSubmit` JSON input, accepts a channel only from a `[Context]` block containing a valid `Scope: channel|thread` line and `Channel: ... (<UUID>)` line, and concatenates regular files in `$CODEX_HOME/channel-context/<UUID>/` in filename order.

Missing, empty, malformed, oversized, unreadable, or non-Buzz inputs fail open with no output. Context is bounded at 128 KiB; oversized context is skipped rather than partially injected.

## Install and rollback

Run from this directory:

```sh
python3 deploy.py install
```

Use `--codex-home PATH` for a staging home or an explicitly selected user home. Installation updates only the `UserPromptSubmit` array, preserves unrelated JSON, and writes `hooks.json.buzz-customizations-backup` before replacement. The installed handler sets `additionalContextLimit` to `0` so Codex passes the complete channel context to the model instead of spilling oversized output to disk and substituting a truncated preview. It asks the installed Codex app server for the hook's exact key and current hash, then atomically records that hash under `[hooks.state]` in `config.toml` so the hook is trusted and runnable. Existing `config.toml` content is preserved, with a one-time `config.toml.buzz-customizations-backup`. Both backups remain stable across repeated installs. The marked hook group is replaced on repeated installs and its trust hash is refreshed. Use `--codex-bin PATH` when `codex` is not on `PATH`.

To remove the customization while preserving other hooks:

```sh
python3 deploy.py uninstall --codex-home PATH
```

Uninstall removes both the marked hook group and its matching trust-state entry while preserving unrelated hook and Codex configuration. Restoring the backups is an additional rollback option.

## Contract

Codex CLI 0.147.0 exposes `hooks` as a stable feature. Its installed native implementation dispatches `UserPromptSubmit` command hooks with JSON stdin and accepts `hookSpecificOutput.additionalContext`. The input schema and output shape are covered by the executable tests in this directory and the upstream Codex schema/source.

Run tests from the repository root with:

```sh
python3 -m unittest discover -s packages/channel-context -p 'test_*.py'
```

Grok Build 1.0.5 does not honor Codex `UserPromptSubmit` hook output. Use [`custom-grok-acp`](../custom-grok-acp/README.md) to inject this same file contract into Grok ACP `session/prompt` turns.
