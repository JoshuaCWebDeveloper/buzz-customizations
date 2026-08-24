import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
WRAPPER = HERE / "custom_grok_acp.py"
DEPLOY = HERE / "deploy.py"


def write_fake_grok(home: Path, extra: str = "") -> Path:
    path = home / "fake-grok"
    path.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log = Path(os.environ["FAKE_GROK_LOG"])
log.write_text(" ".join(sys.argv) + "\\nGROK_HOME=" + os.environ.get("GROK_HOME", "") + "\\n", encoding="utf-8")
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


def write_hook(home: Path, body: str) -> Path:
    path = home / "hook.py"
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def hook_command(path: Path) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def write_hooks(home: Path, commands) -> None:
    home.mkdir(parents=True, exist_ok=True)
    groups = [{"hooks": [{"type": "command", "command": command}]} for command in commands]
    (home / "hooks.json").write_text(
        json.dumps({"hooks": {"session/prompt": groups}}),
        encoding="utf-8",
    )


def prompt_message(text: str, method: str = "session/prompt") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "method": method,
        "params": {"sessionId": "sess-1", "prompt": [{"type": "text", "text": text}]},
    }


def run_wrapper(messages, home: Path, inner: Path, extra_env=None, raw: bytes = None):
    if raw is None:
        raw = b"".join(json.dumps(message, separators=(",", ":")).encode() + b"\n" for message in messages)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["GROK_HOME"] = str(home)
    env["CUSTOM_GROK_ACP_HOME"] = str(home)
    env["CUSTOM_GROK_ACP_INNER"] = str(inner)
    env["FAKE_GROK_LOG"] = str(home / "fake.log")
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


class WrapperTests(unittest.TestCase):
    def test_passthrough_without_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            inner = write_fake_grok(home)
            initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
            prompt = prompt_message("hello")
            output = run_wrapper([initialize, prompt], home, inner)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(decode_lines(output.stdout), [initialize, prompt])
            self.assertEqual(
                (home / "fake.log").read_text(encoding="utf-8"),
                f"{inner} agent --always-approve stdio\nGROK_HOME={home}\n",
            )
            self.assertIn(b"child-stderr\n", output.stderr)

    def test_additional_context_is_appended_as_text_block(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            hook = write_hook(
                home,
                """
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({"additionalContext": "from-hook"}))
""",
            )
            write_hooks(home, [hook_command(hook)])
            output = run_wrapper([prompt_message("hello")], home, write_fake_grok(home))
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(
                decode_lines(output.stdout)[0]["params"]["prompt"],
                [{"type": "text", "text": "hello"}, {"type": "text", "text": "from-hook"}],
            )

    def test_prompt_replace_prepend_and_append(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            hook = write_hook(
                home,
                """
import json, sys
json.load(sys.stdin)
print(json.dumps({
    "prompt": [{"type": "text", "text": "replaced"}],
    "prepend": [{"type": "text", "text": "before"}],
    "append": [{"type": "resource_link", "uri": "file:///tmp/x", "name": "x"}],
    "additionalContext": "after",
}))
""",
            )
            write_hooks(home, [hook_command(hook)])
            output = run_wrapper([prompt_message("hello")], home, write_fake_grok(home))
            self.assertEqual(
                decode_lines(output.stdout)[0]["params"]["prompt"],
                [
                    {"type": "text", "text": "before"},
                    {"type": "text", "text": "replaced"},
                    {"type": "resource_link", "uri": "file:///tmp/x", "name": "x"},
                    {"type": "text", "text": "after"},
                ],
            )

    def test_hooks_run_in_order_and_see_prior_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            first = write_hook(
                home,
                """
import json, sys
print(json.dumps({"additionalContext": "one"}))
""",
            )
            second = home / "hook2.py"
            second.write_text(
                """#!/usr/bin/env python3
import json, sys
payload = json.load(sys.stdin)
texts = [block.get("text") for block in payload["params"]["prompt"]]
print(json.dumps({"additionalContext": ",".join(texts)}))
""",
                encoding="utf-8",
            )
            second.chmod(0o700)
            write_hooks(home, [hook_command(first), hook_command(second)])
            output = run_wrapper([prompt_message("hello")], home, write_fake_grok(home))
            self.assertEqual(
                decode_lines(output.stdout)[0]["params"]["prompt"][-1]["text"],
                "hello,one",
            )

    def test_malformed_timeout_crash_and_oversized_hooks_fail_open(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            inner = write_fake_grok(home)
            prompt = prompt_message("hello")
            bad = write_hook(home, "import sys\nsys.stdout.write('{not-json')\n")
            write_hooks(home, [hook_command(bad)])
            output = run_wrapper([prompt], home, inner)
            self.assertEqual(decode_lines(output.stdout), [prompt])

            crash = write_hook(home, "raise SystemExit(2)\n")
            write_hooks(home, [hook_command(crash)])
            output = run_wrapper([prompt], home, inner)
            self.assertEqual(decode_lines(output.stdout), [prompt])

            huge = write_hook(
                home,
                f"""
import json
print(json.dumps({{"additionalContext": "{'x' * (128 * 1024 + 1)}"}}))
""",
            )
            write_hooks(home, [hook_command(huge)])
            output = run_wrapper([prompt], home, inner)
            self.assertEqual(decode_lines(output.stdout), [prompt])

            slow = write_hook(home, "import time\ntime.sleep(5)\n")
            write_hooks(home, [hook_command(slow)])
            output = run_wrapper([prompt], home, inner, extra_env={"CUSTOM_GROK_ACP_HOOK_TIMEOUT": "0.2"})
            self.assertEqual(decode_lines(output.stdout), [prompt])

            raw = run_wrapper([], home, inner, raw=b"{not-json\n")
            self.assertEqual(raw.stdout, b"{not-json\n")

    def test_missing_inner_binary_exits_127(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            output = run_wrapper([prompt_message("x")], home, home / "missing")
            self.assertEqual(output.returncode, 127)
            self.assertIn(b"failed to spawn", output.stderr)


class DeploymentTests(unittest.TestCase):
    def test_install_copies_executable_creates_home_and_uninstall_keeps_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "nested" / "custom-grok-acp"
            home = Path(temp) / "hooks-home"
            keep = Path(temp) / "keep"
            keep.write_text("keep\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(DEPLOY), "install", "--destination", str(destination), "--home", str(home)],
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
            self.assertTrue(home.is_dir())
            (home / "hooks.json").write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(DEPLOY), "uninstall", "--destination", str(destination), "--home", str(home)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(destination.exists())
            self.assertEqual((home / "hooks.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep\n")


class ApplyHookOutputTests(unittest.TestCase):
    def test_invalid_additional_context_skips_the_hook(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("custom_grok_acp", WRAPPER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        prompt = [{"type": "text", "text": "hello"}]
        self.assertEqual(module.apply_hook_output(prompt, {"additionalContext": 1}), prompt)
        self.assertEqual(module.apply_hook_output(prompt, {"prompt": "nope"}), prompt)
        self.assertEqual(
            module.apply_hook_output(prompt, {"additionalContext": "ok"}),
            [{"type": "text", "text": "hello"}, {"type": "text", "text": "ok"}],
        )


if __name__ == "__main__":
    unittest.main()
