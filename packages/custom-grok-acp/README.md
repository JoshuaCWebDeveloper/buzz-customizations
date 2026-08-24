# custom-grok-acp

This independent customization is a drop-in `grok-acp` wrapper. It speaks ACP stdio (newline-delimited JSON-RPC), forwards every message to the real `grok` binary, and runs command hooks that can control the prompt and extra context Grok Build sees on `session/prompt`.

It does not implement channel context itself. [`channel-context`](../channel-context/README.md) registers a hook through this interface.

Missing, empty, malformed, crashing, timed-out, or oversized hooks fail open and the original line is forwarded unchanged. A hook's `additionalContext` is skipped when it exceeds 128 KiB. Already-valid ACP lines are re-serialized only when a hook changes the prompt.

## Hook interface

Hooks are configured in `$CUSTOM_GROK_ACP_HOME/hooks.json`, defaulting to `/var/lib/buzz-server/custom-grok-acp.d/hooks.json`:

```json
{
  "hooks": {
    "session/prompt": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/python3 /path/to/hook.py"
          }
        ]
      }
    ]
  }
}
```

Groups run in order. Each command receives JSON on stdin:

```json
{
  "method": "session/prompt",
  "params": {
    "sessionId": "sess-1",
    "prompt": [{"type": "text", "text": "..."}]
  }
}
```

Later hooks see the prompt after earlier hooks have applied. Stdout must be a JSON object. Empty stdout is a no-op. Supported fields:

| Field | Effect |
| --- | --- |
| `prompt` | Replace the entire ACP `params.prompt` array |
| `prepend` | Content blocks inserted at the front of the prompt |
| `append` | Content blocks inserted at the end of the prompt |
| `additionalContext` | Non-empty string appended as `{"type": "text", "text": "..."}` |

Non-zero exit, invalid JSON, a timeout (default 5s, override with `CUSTOM_GROK_ACP_HOOK_TIMEOUT`), or a non-string `additionalContext` skips that hook.

ACP stdio messages must not contain embedded newlines. The wrapper forwards `initialize`, `session/new`, `session/cancel`, and any `session/prompt` that no hook changes as received.

## Install and rollback

Run from this directory:

```sh
python3 deploy.py install
```

Use `--destination PATH` for a staging command. The default destination is `/var/lib/buzz-server/custom-grok-acp`. Install copies this package's wrapper byte-for-byte, marks it executable, and creates `$CUSTOM_GROK_ACP_HOME` (default `/var/lib/buzz-server/custom-grok-acp.d`) if needed. It does not replace the Buzz `grok-acp` runtime shim and does not write `hooks.json`.

Point Grok-managed agents at the installed command instead of `grok-acp`. Arguments are forwarded unchanged, so Buzz can keep launching:

```sh
custom-grok-acp agent --always-approve stdio
```

The wrapped binary is `$CUSTOM_GROK_ACP_INNER` or `$GROK_BIN` if set, otherwise `grok` on `PATH`. When `GROK_HOME` is unset and `/var/lib/buzz/grok` exists, the wrapper sets `GROK_HOME` to that path (same host convention as `grok-acp`).

To remove the installed command:

```sh
python3 deploy.py uninstall --destination PATH
```

Uninstall deletes only the selected destination file. Hook config under `--home` is left in place so other customizations can keep their registrations.

## Tests

Run tests from the repository root with:

```sh
python3 -m unittest discover -s packages/custom-grok-acp -p 'test_*.py'
```
