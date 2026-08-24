import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).parent
HOOK = HERE / "channel_context.py"
DEPLOY = HERE / "deploy.py"
WRAPPER = HERE.parent / "custom-grok-acp" / "custom_grok_acp.py"
UUID = "12345678-1234-4234-8234-123456789abc"


def run_hook(payload, home):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "BUZZ_CHANNEL_CONTEXT_HOME": str(home)},
        check=False,
    )


def write_context(home: Path, text: str = "context", name: str = "context.md") -> None:
    directory = home / UUID
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def fake_codex(home: Path) -> Path:
    path = home / f"fake-codex-{len(list(home.glob('fake-codex-*')))}"
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path

home = Path(os.environ["CODEX_HOME"])
config = json.loads((home / "hooks.json").read_text())
command = config["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["command"]
for line in __import__("sys").stdin:
    request = json.loads(line)
    if request.get("id") == 1:
        print(json.dumps({"id": 1, "result": {"codexHome": str(home)}}), flush=True)
    elif request.get("id") == 2:
        groups = config["hooks"]["UserPromptSubmit"]
        group_index = next(index for index, group in enumerate(groups) if group.get("__buzz_customization") == "buzz-customizations/channel-context")
        key = f"{(home / 'hooks.json').resolve()}:user_prompt_submit:{group_index}:0"
        hook = {"eventName": "userPromptSubmit", "command": command, "sourcePath": str((home / "hooks.json").resolve()), "key": key, "currentHash": "sha256:test"}
        print(json.dumps({"id": 2, "result": {"data": [{"hooks": [hook]}]}}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def deploy_command(action: str, home: Path, hook: Path = HOOK, runtime: str = "all") -> list:
    grok_home = home / "custom-grok-acp.d"
    context_home = home / "shared-context"
    command = [
        sys.executable,
        str(DEPLOY),
        action,
        "--codex-home",
        str(home),
        "--custom-grok-acp-home",
        str(grok_home),
        "--context-home",
        str(context_home),
        "--runtime",
        runtime,
    ]
    if action == "install":
        command.extend(("--hook", str(hook), "--codex-bin", str(fake_codex(home))))
    return command


def buzz_prompt(scope: str = "thread", uuid: str = UUID) -> str:
    return f"[Context]\nScope: {scope}\nChannel: buzz-customizations (#{uuid})\n\nhello"


def grok_payload(text: str) -> dict:
    return {
        "method": "session/prompt",
        "params": {"sessionId": "sess-1", "prompt": [{"type": "text", "text": text}]},
    }


class HookTests(unittest.TestCase):
    def test_extracts_channel_sorts_and_concatenates_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / UUID
            directory.mkdir(parents=True)
            (directory / "z-last").write_text("Z\n", encoding="utf-8")
            (directory / "a-first").write_text("A\n", encoding="utf-8")
            (directory / "middle").write_text("M", encoding="utf-8")
            output = run_hook(
                {"hook_event_name": "UserPromptSubmit", "prompt": f"[Context]\nScope: channel\nChannel: #buzz ({UUID})"},
                home,
            )
            self.assertEqual(output.returncode, 0)
            self.assertEqual(
                json.loads(output.stdout),
                {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "A\nMZ\n"}},
            )

    def test_accepts_codex_user_prompt_submit_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            write_context(home, "contract context")
            payload = {
                "agent_id": "agent-1",
                "agent_type": "codex",
                "cwd": "/workspace",
                "hook_event_name": "UserPromptSubmit",
                "model": "gpt-5.6-luna",
                "permission_mode": "default",
                "prompt": f"[Context]\nScope: channel\nChannel: #buzz ({UUID})",
                "session_id": "session-1",
                "transcript_path": None,
                "turn_id": "turn-1",
            }
            output = run_hook(payload, home)
            self.assertEqual(output.returncode, 0)
            result = json.loads(output.stdout)
            self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
            self.assertEqual(result["hookSpecificOutput"]["additionalContext"], "contract context")

    def test_accepts_live_buzz_channel_uuid_with_hash_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            write_context(home, "live frame context")
            output = run_hook(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": f"[Context]\nScope: thread\nChannel: buzz-customizations (#{UUID})\nThread root: event-id",
                },
                home,
            )
            self.assertEqual(output.returncode, 0)
            self.assertEqual(
                json.loads(output.stdout)["hookSpecificOutput"]["additionalContext"],
                "live frame context",
            )

    def test_missing_empty_and_non_regular_entries_are_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.assertEqual(run_hook({"prompt": f"Channel: x ({UUID})"}, home).stdout, b"")
            directory = home / UUID
            directory.mkdir(parents=True)
            (directory / "subdir").mkdir()
            self.assertEqual(run_hook({"hook_event_name": "UserPromptSubmit", "prompt": f"Channel: x ({UUID})"}, home).stdout, b"")

    def test_malformed_non_buzz_and_invalid_payloads_fail_open(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            for payload in (
                {"hook_event_name": "SessionStart", "prompt": f"Channel: x ({UUID})"},
                {"hook_event_name": "UserPromptSubmit", "prompt": "not a Buzz frame"},
                {"hook_event_name": "UserPromptSubmit", "prompt": f"Ordinary text\nChannel: x ({UUID})"},
            ):
                self.assertEqual(run_hook(payload, home).stdout, b"")
            result = subprocess.run([sys.executable, str(HOOK)], input=b"{", stdout=subprocess.PIPE, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_unreadable_or_oversized_context_fails_open(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / UUID
            directory.mkdir(parents=True)
            (directory / "large").write_bytes(b"x" * (128 * 1024 + 1))
            self.assertEqual(
                run_hook(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": f"[Context]\nScope: channel\nChannel: x ({UUID})",
                    },
                    home,
                ).stdout,
                b"",
            )

    def test_default_context_home_is_shared_buzz_directory(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("channel_context", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = os.environ.copy()
        env.pop("BUZZ_CHANNEL_CONTEXT_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(module.context_home(), Path("/var/lib/buzz/channel-context"))


class GrokHookTests(unittest.TestCase):
    def test_session_prompt_returns_labeled_additional_context(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            write_context(home, "grok context")
            output = run_hook(grok_payload(buzz_prompt("channel")), home)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(
                json.loads(output.stdout),
                {"additionalContext": "[Channel Context]\ngrok context"},
            )

    def test_skips_dms_heartbeats_and_already_injected_prompts(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            write_context(home, "should not appear")
            self.assertEqual(run_hook(grok_payload(buzz_prompt("dm")), home).stdout, b"")
            self.assertEqual(run_hook(grok_payload("ok?"), home).stdout, b"")
            already = grok_payload(buzz_prompt())
            already["params"]["prompt"].append({"type": "text", "text": "[Channel Context]\nalready"})
            self.assertEqual(run_hook(already, home).stdout, b"")


class DeploymentTests(unittest.TestCase):
    def test_install_preserves_unrelated_hooks_and_uninstall_removes_only_ours(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / "hooks.json"
            original = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "keep"}]}], "Stop": []}, "other": True}
            path.write_text(json.dumps(original), encoding="utf-8")
            unrelated_key = f"{path.resolve()}:user_prompt_submit:0:0"
            (home / "config.toml").write_text(
                f'[hooks.state.{json.dumps(unrelated_key)}]\ntrusted_hash = "sha256:keep"\n', encoding="utf-8"
            )
            grok_home = home / "custom-grok-acp.d"
            grok_home.mkdir()
            (grok_home / "hooks.json").write_text(
                json.dumps({"hooks": {"session/prompt": [{"hooks": [{"type": "command", "command": "keep-grok"}]}]}}),
                encoding="utf-8",
            )
            subprocess.run(deploy_command("install", home), check=True)
            installed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(installed["hooks"]["UserPromptSubmit"][0], original["hooks"]["UserPromptSubmit"][0])
            self.assertEqual(installed["hooks"]["UserPromptSubmit"][1]["hooks"][0]["additionalContextLimit"], 0)
            self.assertTrue((home / "hooks.json.buzz-customizations-backup").exists())
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('trusted_hash = "sha256:test"', config)
            self.assertIn('trusted_hash = "sha256:keep"', config)
            self.assertTrue((home / "config.toml.buzz-customizations-backup").exists())
            self.assertTrue((home / "shared-context").is_dir())
            grok_installed = json.loads((grok_home / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(grok_installed["hooks"]["session/prompt"][0]["hooks"][0]["command"], "keep-grok")
            our_grok = grok_installed["hooks"]["session/prompt"][1]
            self.assertEqual(our_grok["__buzz_customization"], "buzz-customizations/channel-context")
            self.assertIn(str(HOOK.resolve()), our_grok["hooks"][0]["command"])
            self.assertTrue((grok_home / "hooks.json.buzz-customizations-backup").exists())
            subprocess.run(deploy_command("uninstall", home), check=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            uninstalled_config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertNotIn('trusted_hash = "sha256:test"', uninstalled_config)
            self.assertIn('trusted_hash = "sha256:keep"', uninstalled_config)
            grok_uninstalled = json.loads((grok_home / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(
                grok_uninstalled["hooks"]["session/prompt"],
                [{"hooks": [{"type": "command", "command": "keep-grok"}]}],
            )

    def test_repeated_install_keeps_original_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / "hooks.json"
            original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep"}]}]}}
            path.write_text(json.dumps(original), encoding="utf-8")
            (home / "config.toml").write_text('[projects."/keep"]\ntrust_level = "trusted"\n', encoding="utf-8")
            subprocess.run(deploy_command("install", home), check=True)
            backup = home / "hooks.json.buzz-customizations-backup"
            first_backup = backup.read_bytes()
            config_backup = home / "config.toml.buzz-customizations-backup"
            first_config_backup = config_backup.read_bytes()
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "changed"}]})
            path.write_text(json.dumps(changed), encoding="utf-8")
            subprocess.run(deploy_command("install", home), check=True)
            reinstalled = json.loads(path.read_text(encoding="utf-8"))
            installed_group = next(
                group
                for group in reinstalled["hooks"]["UserPromptSubmit"]
                if group.get("__buzz_customization") == "buzz-customizations/channel-context"
            )
            self.assertEqual(installed_group["hooks"][0]["additionalContextLimit"], 0)
            self.assertEqual(backup.read_bytes(), first_backup)
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
            self.assertEqual(config_backup.read_bytes(), first_config_backup)
            self.assertEqual((home / "config.toml").read_text().count("trusted_hash"), 1)
            grok_reinstalled = json.loads((home / "custom-grok-acp.d" / "hooks.json").read_text(encoding="utf-8"))
            grok_groups = [
                group
                for group in grok_reinstalled["hooks"]["session/prompt"]
                if group.get("__buzz_customization") == "buzz-customizations/channel-context"
            ]
            self.assertEqual(len(grok_groups), 1)

    def test_atomic_replacement_leaves_active_config_unchanged_on_replace_failure(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("deploy", DEPLOY)
        deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deploy)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            original = b'{"hooks": {"Stop": []}}\n'
            path.write_bytes(original)
            with mock.patch.object(deploy.os, "replace", side_effect=OSError("simulated interruption")):
                with self.assertRaises(OSError):
                    deploy.write_atomic(path, {"hooks": {"Stop": [{"hooks": []}]}})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".hooks.json.*")), [])


class GrokAdapterIntegrationTests(unittest.TestCase):
    def test_custom_grok_acp_injects_shared_channel_context(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            context_home = home / "shared-context"
            write_context(context_home, "shared files")
            inner = home / "fake-grok"
            inner.write_text(
                """#!/usr/bin/env python3
import sys
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()
""",
                encoding="utf-8",
            )
            inner.chmod(0o700)
            subprocess.run(deploy_command("install", home, runtime="grok"), check=True)
            command = json.loads((home / "custom-grok-acp.d" / "hooks.json").read_text(encoding="utf-8"))
            self.assertTrue(command["hooks"]["session/prompt"])
            env = os.environ.copy()
            env["CUSTOM_GROK_ACP_HOME"] = str(home / "custom-grok-acp.d")
            env["CUSTOM_GROK_ACP_INNER"] = str(inner)
            env["BUZZ_CHANNEL_CONTEXT_HOME"] = str(context_home)
            env["GROK_HOME"] = str(home)
            env.pop("GROK_BIN", None)
            message = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {"sessionId": "sess-1", "prompt": [{"type": "text", "text": buzz_prompt()}]},
            }
            output = subprocess.run(
                [sys.executable, str(WRAPPER), "agent", "stdio"],
                input=json.dumps(message).encode() + b"\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
                timeout=10,
            )
            self.assertEqual(output.returncode, 0, output.stderr)
            injected = json.loads(output.stdout.splitlines()[0])
            self.assertEqual(
                injected["params"]["prompt"],
                [
                    {"type": "text", "text": buzz_prompt()},
                    {"type": "text", "text": "[Channel Context]\nshared files"},
                ],
            )


if __name__ == "__main__":
    unittest.main()
