import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
WRAPPER = HERE / "custom_grok_acp.py"
DEPLOY = HERE / "deploy.py"
UUID = "12345678-1234-4234-8234-123456789abc"


def write_fake_grok(home: Path, extra: str = "") -> Path:
    path = home / "fake-grok"
    path.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log = Path(os.environ["FAKE_GROK_LOG"])
log.write_text(" ".join(sys.argv) + "\\n", encoding="utf-8")
sys.stderr.write("child-stderr\\n")
sys.stderr.flush()
"""
        + extra
        + """
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def prompt_message(text: str, method: str = "session/prompt") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "method": method,
        "params": {"sessionId": "sess-1", "prompt": [{"type": "text", "text": text}]},
    }


def buzz_prompt(scope: str = "thread", uuid: str = UUID) -> str:
    return f"[Context]\nScope: {scope}\nChannel: buzz-customizations (#{uuid})\n\nhello"


def run_wrapper(messages, home: Path, inner: Path, extra_env=None, raw: bytes = None):
    if raw is None:
        raw = b"".join(json.dumps(message, separators=(",", ":")).encode() + b"\n" for message in messages)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["GROK_HOME"] = str(home)
    env["CUSTOM_GROK_ACP_INNER"] = str(inner)
    env["FAKE_GROK_LOG"] = str(home / "fake.log")
    env.pop("CODEX_HOME", None)
    env.pop("BUZZ_CHANNEL_CONTEXT_HOME", None)
    env.pop("GROK_BIN", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(WRAPPER), "agent", "--always-approve", "stdio"],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
        timeout=10,
    )


def decode_lines(output: bytes) -> list:
    return [json.loads(line) for line in output.splitlines() if line]


class InjectionTests(unittest.TestCase):
    def test_appends_sorted_channel_context_to_session_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "z-last").write_text("Z\n", encoding="utf-8")
            (directory / "a-first").write_text("A\n", encoding="utf-8")
            (directory / "middle").write_text("M", encoding="utf-8")
            inner = write_fake_grok(home)
            output = run_wrapper([prompt_message(buzz_prompt("channel"))], home, inner)
            self.assertEqual(output.returncode, 0, output.stderr)
            message = decode_lines(output.stdout)[0]
            self.assertEqual(
                message["params"]["prompt"],
                [
                    {"type": "text", "text": buzz_prompt("channel")},
                    {"type": "text", "text": "[Channel Context]\nA\nMZ\n"},
                ],
            )
            self.assertEqual((home / "fake.log").read_text(encoding="utf-8"), f"{inner} agent --always-approve stdio\n")
            self.assertIn(b"child-stderr\n", output.stderr)

    def test_accepts_live_buzz_thread_frame_with_hash_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("live frame context", encoding="utf-8")
            output = run_wrapper([prompt_message(buzz_prompt("thread"))], home, write_fake_grok(home))
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(
                decode_lines(output.stdout)[0]["params"]["prompt"][-1]["text"],
                "[Channel Context]\nlive frame context",
            )

    def test_reads_codex_home_when_grok_files_are_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            grok_home = home / "grok"
            codex_home = home / "codex"
            grok_home.mkdir()
            directory = codex_home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("from codex", encoding="utf-8")
            output = run_wrapper(
                [prompt_message(buzz_prompt())],
                grok_home,
                write_fake_grok(grok_home),
                extra_env={"GROK_HOME": str(grok_home), "CODEX_HOME": str(codex_home), "HOME": str(home)},
            )
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(
                decode_lines(output.stdout)[0]["params"]["prompt"][-1]["text"],
                "[Channel Context]\nfrom codex",
            )

    def test_override_home_wins_over_grok_and_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            override = home / "override"
            directory = override / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("override context", encoding="utf-8")
            grok_dir = home / "channel-context" / UUID
            grok_dir.mkdir(parents=True)
            (grok_dir / "context.md").write_text("grok context", encoding="utf-8")
            output = run_wrapper(
                [prompt_message(buzz_prompt())],
                home,
                write_fake_grok(home),
                extra_env={"BUZZ_CHANNEL_CONTEXT_HOME": str(override)},
            )
            self.assertEqual(
                decode_lines(output.stdout)[0]["params"]["prompt"][-1]["text"],
                "[Channel Context]\noverride context",
            )

    def test_preserves_non_prompt_and_unframed_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("should not appear", encoding="utf-8")
            inner = write_fake_grok(home)
            initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
            session_new = {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(home)}}
            dm = prompt_message(buzz_prompt("dm"))
            heartbeat = prompt_message("ok?")
            output = run_wrapper([initialize, session_new, dm, heartbeat], home, inner)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(decode_lines(output.stdout), [initialize, session_new, dm, heartbeat])

    def test_malformed_oversized_and_unreadable_inputs_fail_open(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "subdir").mkdir()
            inner = write_fake_grok(home)
            missing = run_wrapper([prompt_message(buzz_prompt())], home, inner)
            self.assertEqual(decode_lines(missing.stdout)[0]["params"]["prompt"], prompt_message(buzz_prompt())["params"]["prompt"])
            (directory / "large").write_bytes(b"x" * (128 * 1024 + 1))
            oversized = run_wrapper([prompt_message(buzz_prompt())], home, inner)
            self.assertEqual(len(decode_lines(oversized.stdout)[0]["params"]["prompt"]), 1)
            (directory / "large").unlink()
            (directory / "context.md").write_text("ok", encoding="utf-8")
            os.chmod(directory / "context.md", 0)
            try:
                unreadable = run_wrapper([prompt_message(buzz_prompt())], home, inner)
            finally:
                os.chmod(directory / "context.md", stat.S_IRUSR | stat.S_IWUSR)
            if os.geteuid() != 0:
                self.assertEqual(len(decode_lines(unreadable.stdout)[0]["params"]["prompt"]), 1)
            raw = run_wrapper([], home, inner, raw=b"{not-json\n")
            self.assertEqual(raw.stdout, b"{not-json\n")

    def test_does_not_double_inject(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("once", encoding="utf-8")
            already = prompt_message(buzz_prompt())
            already["params"]["prompt"].append({"type": "text", "text": "[Channel Context]\nalready"})
            output = run_wrapper([already], home, write_fake_grok(home))
            self.assertEqual(decode_lines(output.stdout)[0]["params"]["prompt"], already["params"]["prompt"])

    def test_forwards_other_content_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            directory = home / "channel-context" / UUID
            directory.mkdir(parents=True)
            (directory / "context.md").write_text("ctx", encoding="utf-8")
            message = {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "session/prompt",
                "params": {
                    "sessionId": "sess-1",
                    "prompt": [
                        {"type": "text", "text": buzz_prompt()},
                        {"type": "resource_link", "uri": "file:///tmp/x", "name": "x"},
                    ],
                },
            }
            output = run_wrapper([message], home, write_fake_grok(home))
            prompt = decode_lines(output.stdout)[0]["params"]["prompt"]
            self.assertEqual(prompt[1], {"type": "resource_link", "uri": "file:///tmp/x", "name": "x"})
            self.assertEqual(prompt[2]["text"], "[Channel Context]\nctx")

    def test_missing_inner_binary_exits_127(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            output = run_wrapper([prompt_message("x")], home, home / "missing")
            self.assertEqual(output.returncode, 127)
            self.assertIn(b"failed to spawn", output.stderr)


class DeploymentTests(unittest.TestCase):
    def test_install_copies_executable_bytes_and_uninstall_removes_only_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "nested" / "custom-grok-acp"
            keep = Path(temp) / "keep"
            keep.write_text("keep\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(DEPLOY), "install", "--destination", str(destination)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.read_bytes(), WRAPPER.read_bytes())
            self.assertEqual(
                hashlib.sha256(destination.read_bytes()).hexdigest(),
                hashlib.sha256(WRAPPER.read_bytes()).hexdigest(),
            )
            self.assertTrue(os.access(destination, os.X_OK))
            result = subprocess.run(
                [sys.executable, str(DEPLOY), "uninstall", "--destination", str(destination)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(destination.exists())
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
