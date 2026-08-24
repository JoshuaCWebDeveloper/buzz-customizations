# channel-context

This independent customization injects per-channel context into Buzz-framed turns for Codex and Grok.

The loader accepts a channel only from a `[Context]` block containing a valid `Scope: channel|thread` line and `Channel: ... (<UUID>)` line, then concatenates regular files in `/var/lib/buzz/channel-context/<UUID>/` in filename order. Override the root with `BUZZ_CHANNEL_CONTEXT_HOME`. Missing, empty, malformed, oversized, unreadable, or non-Buzz inputs fail open. Context is bounded at 128 KiB; oversized context is skipped rather than partially injected.

- **Codex:** a `UserPromptSubmit` command hook that returns `hookSpecificOutput.additionalContext`.
- **Grok:** a [`custom-grok-acp`](../custom-grok-acp/README.md) `session/prompt` hook that returns `additionalContext` as a `[Channel Context]` text block. `custom-grok-acp` must be the agent command so the hook runs.

## File location

Canonical path:

```text
/var/lib/buzz/channel-context/<UUID>/
```

Put one regular file per concern, named so filename order is the intended concat order. This is shared by Codex and Grok. The previous `$CODEX_HOME/channel-context/<UUID>/` location is no longer read.

## Install and rollback

Run from this directory:

```sh
python3 deploy.py install
```

Default deploy installs both runtimes:

- Codex: updates `UserPromptSubmit` in `$CODEX_HOME/hooks.json`, preserves unrelated JSON, writes `hooks.json.buzz-customizations-backup` before replacement, sets `additionalContextLimit` to `0`, trusts the hook hash in `config.toml`.
- Grok: registers this package's script as a `session/prompt` command hook in `$CUSTOM_GROK_ACP_HOME/hooks.json` (default `/var/lib/buzz-server/custom-grok-acp.d/hooks.json`) and creates `/var/lib/buzz/channel-context` when possible.
- `--runtime codex` or `--runtime grok` installs one side. `--codex-home`, `--custom-grok-acp-home`, `--context-home`, `--hook`, and `--codex-bin` override paths.

Grok injection still requires pointing the agent at the installed `custom-grok-acp` command. This package only registers the hook.

To remove the customization while preserving other hooks:

```sh
python3 deploy.py uninstall
```

Uninstall removes the marked Codex group and its trust-state entry, and the marked Grok hook group. Restoring the backups is an additional rollback option.

## Contract

Codex CLI 0.147.0 exposes `hooks` as a stable feature. Its installed native implementation dispatches `UserPromptSubmit` command hooks with JSON stdin and accepts `hookSpecificOutput.additionalContext`. Grok Build 1.0.5 does not honor that hook output; the Grok adapter uses the custom-grok-acp hook interface instead.

Run tests from the repository root with:

```sh
python3 -m unittest discover -s packages/channel-context -p 'test_*.py'
```
