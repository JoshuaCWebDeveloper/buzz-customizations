# custom-grok-acp

This independent customization is a drop-in `grok-acp` wrapper. It speaks ACP stdio (newline-delimited JSON-RPC), forwards every message to the real `grok` binary, and injects per-channel context into `session/prompt` turns that are framed by Buzz.

The wrapper reads the same file contract as [`channel-context`](../channel-context/README.md): a `[Context]` block with `Scope: channel|thread` and `Channel: ... (<UUID>)`, then concatenates regular files in filename order. Missing, empty, malformed, oversized, unreadable, non-UTF8, or non-Buzz inputs fail open and the original line is forwarded unchanged. Context is bounded at 128 KiB; oversized context is skipped rather than partially injected. Already-injected prompts are left alone.

Injection is an extra ACP text block:

```json
{"type": "text", "text": "[Channel Context]\n..."}
```

appended to `params.prompt`. Grok 1.0.5 has no `UserPromptSubmit` `additionalContext` path; this is the per-turn input it actually honors.

## File lookup

The first non-empty match wins:

1. `$BUZZ_CHANNEL_CONTEXT_HOME/<UUID>/` when that environment variable is set
2. `$GROK_HOME/channel-context/<UUID>/`
3. `$CODEX_HOME/channel-context/<UUID>/`, defaulting `CODEX_HOME` to `~/.codex`

Codex-deployed files therefore work without a second copy.

## Install and rollback

Run from this directory:

```sh
python3 deploy.py install
```

Use `--destination PATH` for a staging command or an explicitly selected install path. The default destination is `/var/lib/buzz-server/custom-grok-acp`. Install copies this package's wrapper byte-for-byte and marks it executable. It does not replace the Buzz `grok-acp` runtime shim.

Point Grok-managed agents at the installed command instead of `grok-acp`. Arguments are forwarded unchanged, so Buzz can keep launching:

```sh
custom-grok-acp agent --always-approve stdio
```

The wrapped binary is `$CUSTOM_GROK_ACP_INNER` or `$GROK_BIN` if set, otherwise `grok` on `PATH`. When `GROK_HOME` is unset and `/var/lib/buzz/grok` exists, the wrapper sets `GROK_HOME` to that path (same host convention as `grok-acp`).

To remove the installed command:

```sh
python3 deploy.py uninstall --destination PATH
```

Uninstall deletes only the selected destination file.

## Contract

ACP stdio messages are newline-delimited JSON-RPC and must not contain embedded newlines. The wrapper re-serializes a line only when it injects; every other line is forwarded as received, including `initialize`, `session/new`, `session/cancel`, heartbeats without a Buzz `[Context]` frame, and `Scope: dm`.

Run tests from the repository root with:

```sh
python3 -m unittest discover -s packages/custom-grok-acp -p 'test_*.py'
```
