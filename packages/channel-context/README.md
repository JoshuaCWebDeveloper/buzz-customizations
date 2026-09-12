# channel-context

This independent customization injects per-channel context into Buzz-framed turns for Codex, Grok, and Claude Code.

The loader accepts a channel only from a `[Context]` block containing a valid `Scope: channel|thread` line and `Channel: ... (<UUID>)` line, then concatenates regular files in `/var/lib/buzz/channel-context/<UUID>/` in filename order. Override the root with `BUZZ_CHANNEL_CONTEXT_HOME`. Missing, empty, malformed, oversized, unreadable, or non-Buzz inputs fail open. Context is bounded at 128 KiB; oversized context is skipped rather than partially injected.

- **Codex:** a `UserPromptSubmit` command hook that returns `hookSpecificOutput.additionalContext`.
- **Grok:** a [`custom-grok-acp`](../custom-grok-acp/README.md) `session/prompt` hook that returns `additionalContext` as a `[Channel Context]` text block. `custom-grok-acp` must be the agent command so the hook runs.
- **Claude Code:** the same `UserPromptSubmit` command hook as Codex, registered in `$CLAUDE_CONFIG_DIR/settings.json`. No wrapper and no separate handler.

## File location

Canonical path:

```text
/var/lib/buzz/channel-context/<UUID>/
```

Put one regular file per concern, named so filename order is the intended concat order. This is shared by Codex, Grok, and Claude Code. The previous `$CODEX_HOME/channel-context/<UUID>/` location is no longer read.

## Install and rollback

Run from this directory:

```sh
python3 deploy.py install
```

Default deploy installs all three runtimes, Claude Code first:

- Claude Code: updates `UserPromptSubmit` in `$CLAUDE_CONFIG_DIR/settings.json` (default `/var/lib/buzz/claude-code/settings.json`), preserves unrelated JSON, writes `settings.json.buzz-customizations-backup` before replacement. No trust step and no `additionalContextLimit`.
- Codex: updates `UserPromptSubmit` in `$CODEX_HOME/hooks.json`, preserves unrelated JSON, writes `hooks.json.buzz-customizations-backup` before replacement, sets `additionalContextLimit` to `0`, trusts the hook hash in `config.toml`.
- Grok: registers this package's script as a `session/prompt` command hook in `$CUSTOM_GROK_ACP_HOME/hooks.json` (default `/var/lib/buzz-server/custom-grok-acp.d/hooks.json`) and creates `/var/lib/buzz/channel-context` when possible.
- `--runtime claude`, `--runtime codex`, or `--runtime grok` installs one side. `--claude-config-dir`, `--codex-home`, `--custom-grok-acp-home`, `--context-home`, `--hook`, and `--codex-bin` override paths.

Install is sequential and not transactional. Claude Code runs first because `install_codex` aborts the whole run when `codex app-server` does not report the hook back, and it is the only step that depends on an external process.

Grok injection still requires pointing the agent at the installed `custom-grok-acp` command. This package only registers the hook.

Codex reads a per-agent `$CODEX_HOME`, so one run covers one Codex agent. `CLAUDE_CONFIG_DIR` is shared by every Claude agent using it, so one run covers all of them and there is no per-agent opt-out at the user tier.

To remove the customization while preserving other hooks:

```sh
python3 deploy.py uninstall
```

Uninstall removes the marked Codex group and its trust-state entry, the marked Grok hook group, and the marked Claude Code group. Restoring the backups is an additional rollback option.

## Contract

Codex CLI 0.147.0 exposes `hooks` as a stable feature. Its installed native implementation dispatches `UserPromptSubmit` command hooks with JSON stdin and accepts `hookSpecificOutput.additionalContext`. Grok Build 1.0.5 does not honor that hook output; the Grok adapter uses the custom-grok-acp hook interface instead.

Claude Code 2.1.240 dispatches `UserPromptSubmit` with the same stdin shape as Codex — `hook_event_name` plus `prompt` as a plain string — and honors `hookSpecificOutput.additionalContext`, delivering it to the model as a `hook_additional_context` attachment. It has no hook-trust model and does not define `additionalContextLimit`. Hook config is re-read per turn, so installing does not require restarting a running agent.

Under `claude-agent-acp`, injection depends on the bridge loading filesystem settings: 0.70.0 runs the session query with `settingSources: ["user", "project", "local"]`. If a future version drops that, the hook simply stops being called and context goes missing silently, because the hook fails open. Verify after upgrading the runtime.

Run tests from the repository root with:

```sh
python3 -m unittest discover -s packages/channel-context -p 'test_*.py'
```
