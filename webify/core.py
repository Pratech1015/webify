"""Core helpers: git, ports, and path detection."""

import socket
import subprocess
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
    """Figure out which subdirectory to serve (supports classic static layouts)."""
    for marker in ("index.html", "index.htm"):
        if (repo_dir / marker).exists():
            return repo_dir
    for candidate in ("dist", "public", "build", "static", "site", "out", "docs"):
        if (repo_dir / candidate).exists():
            return repo_dir / candidate
    return repo_dir


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
