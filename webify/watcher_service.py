"""Watcher runner — executed as a systemd service (webify-<name>-watcher.service).

Polls the repository every 60 seconds for updates. If a change is detected,
triggers the deploy service (webify-<name>-deploy.service).

Running: python -m webify.watcher_service <name>
"""

import sys
import time
import subprocess
from pathlib import Path
from . import state, daemon
from .core import WebifyError

def check_for_updates(name: str):
    info = state.get_service(name)
    if not info:
        return

    repo_dir = Path(info["dir"]) / "repo"
    if not (repo_dir / ".git").exists():
        return

    # Fetch latest
    subprocess.run(["git", "fetch", "origin"], cwd=repo_dir, capture_output=True)
    
    # Compare HEAD with origin/main (or origin/master)
    # Using rev-parse to get commit hashes
    local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
    remote = subprocess.run(["git", "rev-parse", "origin/main"], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
    if not remote: # try master if main fails
        remote = subprocess.run(["git", "rev-parse", "origin/master"], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
        
    if local and remote and local != remote:
        print(f"Update detected for {name}. Triggering deploy...", flush=True)
        daemon.start_unit(daemon.deploy_unit_name(name), enable=False)

def main():
    if len(sys.argv) < 2:
        return 2
    name = sys.argv[1]
    print(f"Watcher started for {name}", flush=True)
    while True:
        try:
            check_for_updates(name)
        except Exception as e:
            print(f"Watcher error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
