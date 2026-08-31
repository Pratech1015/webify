"""Core helpers: git, ports, and path detection."""

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_PORT, MAX_PORT, MIN_PORT


class WebifyError(Exception):
    """Raised for user-facing failures."""


def run(cmd, cwd=None, check=True, capture=True):
    """Run an external command and return the completed subprocess result."""
    return subprocess.run(
        cmd, cwd=cwd, check=check, capture_output=capture, text=True,
    )


def clone_repo(repo_url: str, dest: Path) -> None:
    """Clone a git repo into dest (shallow), or pull if already present."""
    if dest.exists() and (dest / ".git").exists():
        run(["git", "pull", "--ff-only"], cwd=dest)
        return
    if dest.exists():
        raise WebifyError(f"Destination already exists and is not a git repo: {dest}")
    proc = run(["git", "clone", "--depth", "1", repo_url, str(dest)], capture=True)
    if proc.returncode != 0:
        raise WebifyError(
            f"Failed to clone repo. Is it public and valid?\n{proc.stderr.strip()}"
        )


def is_port_free(port: int) -> bool:
    """Return True if the given port is not in use on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    """Return preferred if free, otherwise the next free port at/after it."""
    port = preferred
    while port <= MAX_PORT:
        if is_port_free(port):
            return port
        port += 1
    port = MIN_PORT
    while port < preferred:
        if is_port_free(port):
            return port
        port += 1
    raise WebifyError("No free port available on this system.")


def detect_served_dir(repo_dir: Path) -> Path:
    """Figure out which subdirectory to serve (supports classic static layouts).

    Checks build output dirs first (dist/, build/, etc.) before falling back
    to the repo root. This ensures built assets are served over raw sources.
    """
    for candidate in ("dist", "build", "public", "static", "site", "out", "docs",
                       ".next", ".output", "_build"):
        if (repo_dir / candidate).exists():
            return repo_dir / candidate
    for marker in ("index.html", "index.htm"):
        if (repo_dir / marker).exists():
            return repo_dir
    return repo_dir


def build_repo(repo_dir: Path) -> Path:
    """If the repo has a build system, install deps and build.

    Returns the directory that should be served (e.g. dist/, build/, or repo root).
    Raises WebifyError if the build fails.
    """
    if (repo_dir / "package.json").exists():
        _npm_build(repo_dir)
    return detect_served_dir(repo_dir)


def _npm_build(repo_dir: Path):
    """Run npm install + npm run build in the repo directory."""
    npm = shutil.which("npm")
    if not npm:
        raise WebifyError(
            "This repo contains package.json but 'npm' was not found on PATH. "
            "Install Node.js first (e.g. 'pacman -S nodejs npm')."
        )

    print(f"  Installing npm dependencies...", flush=True)
    proc = run(
        [npm, "install", "--prefer-offline"],
        cwd=repo_dir, check=False,
    )
    if proc.returncode != 0:
        raise WebifyError(
            f"npm install failed:\n{proc.stderr.strip()}"
        )

    # Check if a build script exists.
    import json
    try:
        pkg = json.loads((repo_dir / "package.json").read_text())
        scripts = pkg.get("scripts", {})
    except (json.JSONDecodeError, OSError):
        scripts = {}

    if "build" not in scripts:
        print("  No 'build' script in package.json — skipping build step.", flush=True)
        return

    print("  Running npm run build...", flush=True)
    proc = run(
        [npm, "run", "build"],
        cwd=repo_dir, check=False,
    )
    if proc.returncode != 0:
        # Show the error and the service status hint.
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        error_detail = stderr or stdout or "(no output)"
        raise WebifyError(
            f"npm run build failed (exit {proc.returncode}):\n{error_detail}\n\n"
            f"Check the build output above and fix the errors, then retry."
        )


def is_systemd_user_available() -> bool:
    """Return True if a user systemd manager is reachable."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, timeout=8,
        )
        # reachable if it returns either a state or a known error
        out = (result.stdout + result.stderr).strip()
        return bool(out) and "System has not been booted" not in out
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
