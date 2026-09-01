#!/usr/bin/env python3
"""Reversible worker service installation; live execution is operator-controlled."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile

MARKER = "ENOTIFY_MANAGED"
UNIT_NAME = "enotify.service"


def install(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o770)
    try:
        account = pwd.getpwnam("enotify")
    except KeyError:
        account = None
    if account is not None and os.geteuid() == 0:
        os.chown(state_dir, account.pw_uid, account.pw_gid)
    marker = state_dir / MARKER
    marker.write_text(
        "Managed by packages/enotify/deploy.py.\n",
        encoding="utf-8",
    )


def unit(state_dir: Path, release_dir: Path = Path("/opt/enotify")) -> str:
    return """[Unit]
Description=enotify notification worker
After=network-online.target

[Service]
Type=simple
EnvironmentFile=-{state}/enotify.env
Environment=ENOTIFY_DB={state}/enotify.db
User=enotify
Group=enotify
WorkingDirectory={root}
ExecStart=/usr/bin/env python3 {root}/enotify-worker.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths={state}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
""".format(state=state_dir, root=release_dir)


def service_action(action: str, unit_dir: Path, runner=subprocess.run) -> None:
    command = ["systemctl", action] if action == "daemon-reload" else ["systemctl", action, UNIT_NAME]
    result = runner(command, check=False)
    if getattr(result, "returncode", 0) != 0:
        raise RuntimeError(f"systemctl {action} failed")


def _absolute_scoped(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_absolute() or str(path) in ("/", ""):
        raise ValueError(f"{label} must be an absolute scoped path")
    return path


def stage_release(release_dir: Path) -> None:
    release_dir = _absolute_scoped(release_dir, "release-dir")
    source = Path(__file__).resolve().parent
    parent = release_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{release_dir.name}.", dir=parent))
    try:
        shutil.copytree(source / "enotify", temporary / "enotify")
        shutil.copy2(source / "enotify-worker.py", temporary / "enotify-worker.py")
        shutil.copytree(source / "migrations", temporary / "migrations")
        backup = release_dir.with_name(release_dir.name + ".previous")
        if release_dir.exists():
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(release_dir, backup)
        os.replace(temporary, release_dir)
        for path in release_dir.rglob("*"):
            if path.is_file():
                path.chmod(0o644)
        release_dir.chmod(0o755)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def deploy(action: str, state_dir: Path, unit_dir: Path, runner=subprocess.run, release_dir: Path = Path("/opt/enotify")) -> None:
    state_dir = _absolute_scoped(state_dir, "state-dir")
    unit_dir = _absolute_scoped(unit_dir, "unit-dir")
    release_dir = _absolute_scoped(release_dir, "release-dir")
    state_dir.mkdir(parents=True, exist_ok=True)
    destination = unit_dir / UNIT_NAME
    if action == "install":
        install(state_dir)
        stage_release(release_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.with_suffix(".service.bak").write_bytes(destination.read_bytes())
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(unit(state_dir, release_dir), encoding="utf-8")
        os.replace(temporary, destination)
        service_action("daemon-reload", unit_dir, runner)
        service_action("enable", unit_dir, runner)
        service_action("start", unit_dir, runner)
        return
    if action == "rollback":
        backup = destination.with_suffix(".service.bak")
        previous = release_dir.with_name(release_dir.name + ".previous")
        if not backup.exists() or not previous.exists():
            raise RuntimeError("no previous enotify service unit to roll back")
        service_action("stop", unit_dir, runner)
        failed_release = release_dir.with_name(release_dir.name + ".failed")
        if failed_release.exists():
            shutil.rmtree(failed_release)
        os.replace(release_dir, failed_release)
        os.replace(previous, release_dir)
        destination.write_bytes(backup.read_bytes())
        backup.unlink()
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
    parser.add_argument("--release-dir", default=os.environ.get("ENOTIFY_RELEASE_DIR", "/opt/enotify"))
    args = parser.parse_args(argv)
    state_dir = Path(args.state_dir)
    if args.action == "uninstall":
        uninstall(state_dir)
    else:
        deploy(args.action, state_dir, Path(args.unit_dir), release_dir=Path(args.release_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
