# channel-context

This independent customization injects per-channel context into Codex turns that are framed by Buzz. The hook reads the `prompt` field from Codex's `UserPromptSubmit` JSON input, accepts a channel only from a `[Context]` block containing a valid `Scope: channel|thread` line and `Channel: ... (<UUID>)` line, and concatenates regular files in `$CODEX_HOME/channel-context/<UUID>/` in filename order.

Missing, empty, malformed, oversized, unreadable, or non-Buzz inputs fail open with no output. Context is bounded at 128 KiB; oversized context is skipped rather than partially injected.

## Install and rollback

Run from this directory:

```sh
python3 deploy.py install
```

Use `--codex-home PATH` for a staging home or an explicitly selected user home. Installation updates only the `UserPromptSubmit` array, preserves unrelated JSON, and writes `hooks.json.buzz-customizations-backup` before replacement. That backup is created only once and remains the stable pre-customization rollback artifact across repeated installs. Config replacement is atomic. It marks its own group so repeated installs replace only the customization's previous group.

To remove the customization while preserving other hooks:

```sh
python3 deploy.py uninstall --codex-home PATH
```

Restoring the backup is an additional rollback option. This project was not deployed to agent-1's active Codex home.

## Contract

Codex CLI 0.147.0 exposes `hooks` as a stable feature. Its installed native implementation dispatches `UserPromptSubmit` command hooks with JSON stdin and accepts `hookSpecificOutput.additionalContext`. The input schema and output shape are covered by the executable tests in this directory and the upstream Codex schema/source.

Run tests with:

```sh
python3 -m unittest discover -s channel-context -p 'test_*.py'
```
