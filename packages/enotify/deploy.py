#!/usr/bin/env python3
"""Explicit, reversible service-directory scaffolding; no live deployment by default."""
import argparse
from pathlib import Path
def main():
    p=argparse.ArgumentParser(); p.add_argument("action",choices=("install","uninstall")); p.add_argument("--state-dir",default=str(Path.home()/".local/state/enotify")); a=p.parse_args()
    target=Path(a.state_dir)
    if a.action=="install": target.mkdir(parents=True,exist_ok=True); (target/"README").write_text("enotify state directory; run the worker explicitly.\n",encoding="utf-8")
    elif target.exists(): (target/"README").unlink(missing_ok=True)
    return 0
if __name__=="__main__": raise SystemExit(main())
