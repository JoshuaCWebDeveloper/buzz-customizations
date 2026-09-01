#!/usr/bin/env python3
"""Reversible worker service installation; live execution is operator-controlled."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

MARKER = "ENOTIFY_MANAGED"
UNIT_NAME = "enotify.service"


def install(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / MARKER
    marker.write_text(
        "Managed by packages/enotify/deploy.py.\n",
        encoding="utf-8",
    )


def unit(state_dir: Path) -> str:
    return """[Unit]
Description=enotify notification worker
After=network-online.target

[Service]
Type=simple
EnvironmentFile=-{state}/enotify.env
WorkingDirectory={root}
ExecStart=/usr/bin/env python3 {root}/enotify-worker.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
""".format(state=state_dir, root=Path(__file__).resolve().parent)


def service_action(action: str, unit_dir: Path, runner=subprocess.run) -> None:
    result = runner(["systemctl", action, UNIT_NAME], check=False)
    if getattr(result, "returncode", 0) != 0:
        raise RuntimeError(f"systemctl {action} failed")


def deploy(action: str, state_dir: Path, unit_dir: Path, runner=subprocess.run) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    destination = unit_dir / UNIT_NAME
    if action == "install":
        install(state_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.with_suffix(".service.bak").write_bytes(destination.read_bytes())
        destination.write_text(unit(state_dir), encoding="utf-8")
        return
    if action == "rollback":
        backup = destination.with_suffix(".service.bak")
        if not backup.exists():
            raise RuntimeError("no previous enotify service unit to roll back")
        destination.write_bytes(backup.read_bytes())
        service_action("daemon-reload", unit_dir, runner)
        service_action("restart", unit_dir, runner)
        return
    if action in ("start", "status", "restart"):
        service_action(action, unit_dir, runner)
        return
    if action == "undeploy":
        service_action("stop", unit_dir, runner)
        destination.unlink(missing_ok=True)
        (state_dir / MARKER).unlink(missing_ok=True)
        service_action("daemon-reload", unit_dir, runner)
        return
    raise ValueError("unsupported deployment action")


def uninstall(state_dir: Path) -> None:
    # Preserve the database, logs, backups, and every unrelated file. Removal
    # of retained state is a separate operator decision.
    (state_dir / MARKER).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "start", "status", "restart", "rollback", "undeploy", "uninstall"))
    parser.add_argument(
        "--state-dir", default=str(Path.home() / ".local/state/enotify")
    )
    parser.add_argument("--unit-dir", default=os.environ.get("ENOTIFY_UNIT_DIR", "/etc/systemd/system"))
    args = parser.parse_args(argv)
    state_dir = Path(args.state_dir)
    if args.action == "uninstall":
        uninstall(state_dir)
    else:
        deploy(args.action, state_dir, Path(args.unit_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
