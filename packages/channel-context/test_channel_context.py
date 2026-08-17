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
UUID = "12345678-1234-4234-8234-123456789abc"


def run_hook(payload, home):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "CODEX_HOME": str(home)},
        check=False,
    )


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


def deploy_command(action: str, home: Path, hook: Path = HOOK) -> list[str]:
    command = [sys.executable, str(DEPLOY), action, "--codex-home", str(home)]
    if action == "install":
        command.extend(("--hook", str(hook), "--codex-bin", str(fake_codex(home))))
    return command


class HookTests(unittest.TestCase):
    def test_extracts_channel_sorts_and_concatenates_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
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
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("contract context", encoding="utf-8")
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
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("live frame context", encoding="utf-8")
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
            directory = home / "channel-context" / UUID
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
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "large").write_bytes(b"x" * (128 * 1024 + 1))
            self.assertEqual(run_hook({"hook_event_name": "UserPromptSubmit", "prompt": f"Channel: x ({UUID})"}, home).stdout, b"")


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
            subprocess.run(deploy_command("install", home), check=True)
            installed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(installed["hooks"]["UserPromptSubmit"][0], original["hooks"]["UserPromptSubmit"][0])
            self.assertEqual(installed["hooks"]["UserPromptSubmit"][1]["hooks"][0]["additionalContextLimit"], 0)
            self.assertTrue((home / "hooks.json.buzz-customizations-backup").exists())
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('trusted_hash = "sha256:test"', config)
            self.assertIn('trusted_hash = "sha256:keep"', config)
            self.assertTrue((home / "config.toml.buzz-customizations-backup").exists())
            subprocess.run(deploy_command("uninstall", home), check=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            uninstalled_config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertNotIn('trusted_hash = "sha256:test"', uninstalled_config)
            self.assertIn('trusted_hash = "sha256:keep"', uninstalled_config)

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


if __name__ == "__main__":
    unittest.main()
