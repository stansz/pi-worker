#!/usr/bin/env python3
"""Update pi-worker. Git pull, npm update Pi, restart service."""

import subprocess
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).parent.resolve()
PI_VERSION = "0.73.1"  # pinned — bump manually

def run(cmd, **kwargs):
    print(f"+ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"  FAILED ({result.returncode}): {result.stderr}")
        return False
    print(f"  OK")
    return True

def main():
    print("=== pi-worker update ===\n")

    # 1. Update pi-worker repo
    print("1. Update pi-worker repo")
    if not run(["git", "-C", str(WORKER_DIR), "pull", "origin", "main"], cwd=str(WORKER_DIR)):
        print("  git pull failed — continuing anyway")

    # 2. Update Pi (pinned version)
    print(f"\n2. Update Pi (pinned @ {PI_VERSION})")
    run(["npm", "update", "-g", f"@mariozechner/pi-coding-agent@{PI_VERSION}"])

    # 3. Restart service
    print("\n3. Restart pi-worker service")
    run(["systemctl", "--user", "restart", "pi-worker"])

    print("\n=== Done ===")

if __name__ == "__main__":
    main()
