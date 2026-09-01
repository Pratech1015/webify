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


def build_repo(repo_dir: Path) -> dict:
    """Detect project type, install deps, build if needed.

    Returns a dict with:
      - "mode": "static" (serve a dir) or "server" (run a command)
      - "served": the directory to serve (for static)
      - "command": the command to run (for server)
      - "port": detected port (for server, or None for static)
    Raises WebifyError if the build fails.
    """
    project = detect_project_type(repo_dir)

    if project["type"] == "node":
        return _handle_node(repo_dir, project)
    elif project["type"] == "make":
        return _handle_make(repo_dir)
    elif project["type"] == "cargo":
        return _handle_cargo(repo_dir)
    elif project["type"] == "go":
        return _handle_go(repo_dir)

    # No recognized build system — just serve the repo.
    return {"mode": "static", "served": detect_served_dir(repo_dir)}


def detect_project_type(repo_dir: Path) -> dict:
    """Detect what kind of project this is."""
    if (repo_dir / "package.json").exists():
        scripts = _npm_scripts(repo_dir)
        if "build" in scripts:
            return {"type": "node", "action": "build", "scripts": scripts}
        if "dev" in scripts:
            return {"type": "node", "action": "dev", "scripts": scripts}
        if "start" in scripts:
            return {"type": "node", "action": "start", "scripts": scripts}
        return {"type": "node", "action": "none", "scripts": scripts}

    if (repo_dir / "Makefile").exists() or (repo_dir / "makefile").exists():
        return {"type": "make"}
    if (repo_dir / "Cargo.toml").exists():
        return {"type": "cargo"}
    if (repo_dir / "go.mod").exists():
        return {"type": "go"}

    return {"type": "unknown"}


def _npm_scripts(repo_dir: Path) -> dict:
    import json
    try:
        pkg = json.loads((repo_dir / "package.json").read_text())
        return pkg.get("scripts", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _npm_install(repo_dir: Path):
    npm = shutil.which("npm")
    if not npm:
        raise WebifyError(
            "This repo contains package.json but 'npm' was not found on PATH. "
            "Install Node.js first (e.g. 'pacman -S nodejs npm')."
        )
    print("  Installing npm dependencies...", flush=True)
    proc = run([npm, "install", "--prefer-offline"], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        raise WebifyError(f"npm install failed:\n{proc.stderr.strip()}")
    return npm


def _handle_node(repo_dir: Path, project: dict) -> dict:
    """Handle Node.js projects: build, dev, or start."""
    npm = _npm_install(repo_dir)
    action = project["action"]

    if action == "build":
        # Static site — run build, serve dist/
        print("  Running npm run build...", flush=True)
        proc = run([npm, "run", "build"], cwd=repo_dir, check=False)
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or "(no output)"
            raise WebifyError(
                f"npm run build failed (exit {proc.returncode}):\n{err}\n\n"
                f"Check the build output above and fix the errors, then retry."
            )
        return {"mode": "static", "served": detect_served_dir(repo_dir)}

    if action in ("dev", "start"):
        # Dev server — run directly, detect port from output.
        cmd = f"{npm} run {action}"
        print(f"  Starting {cmd}...", flush=True)
        port = _detect_server_port(repo_dir, [npm, "run", action])
        return {"mode": "server", "command": cmd, "port": port}

    # No useful scripts — just serve the repo.
    return {"mode": "static", "served": detect_served_dir(repo_dir)}


def _detect_server_port(repo_dir: Path, cmd: list, timeout: float = 15.0) -> int:
    """Run a command, capture output, and detect the port it listens on."""
    import re
    import time

    proc = subprocess.Popen(
        cmd, cwd=repo_dir,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.time() + timeout
    port = None
    output_lines = []

    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        output_lines.append(line)
        print(f"    {line}", flush=True)

        # Common patterns: "Local: http://localhost:3000"
        # "listening on port 3000", "http://localhost:3000"
        # "Ready on http://localhost:3000"
        m = re.search(r"localhost:(\d+)", line)
        if m:
            port = int(m.group(1))
            break
        m = re.search(r"port\s+(\d+)", line, re.IGNORECASE)
        if m:
            port = int(m.group(1))
            break
        m = re.search(r":(\d{4,5})", line)
        if m:
            port = int(m.group(1))
            break

    if port is None:
        proc.terminate()
        proc.wait(timeout=5)
        raise WebifyError(
            "Could not detect the port from the server output.\n"
            "Output was:\n" + "\n".join(output_lines[-20:]) +
            "\n\nMake sure the server prints its URL (e.g. 'http://localhost:3000')."
        )

    proc.terminate()
    proc.wait(timeout=5)
    return port


def _handle_make(repo_dir: Path) -> dict:
    make = shutil.which("make")
    if not make:
        raise WebifyError("'make' not found on PATH.")
    print("  Running make...", flush=True)
    proc = run([make], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "(no output)"
        raise WebifyError(f"make failed (exit {proc.returncode}):\n{err}")
    served = detect_served_dir(repo_dir)
    if served == repo_dir:
        raise WebifyError(
            "make succeeded but no serveable output found "
            "(no dist/, build/, or index.html)."
        )
    return {"mode": "static", "served": served}


def _handle_cargo(repo_dir: Path) -> dict:
    cargo = shutil.which("cargo")
    if not cargo:
        raise WebifyError("'cargo' not found on PATH.")
    print("  Running cargo build --release...", flush=True)
    proc = run([cargo, "build", "--release"], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or "(no output)"
        raise WebifyError(f"cargo build failed:\n{err}")
    # Find the built binary in target/release/
    import glob
    bins = glob.glob(str(repo_dir / "target" / "release" / "*"))
    bins = [b for b in bins if not b.endswith(".d") and not b.endswith(".rlib")]
    if not bins:
        raise WebifyError("cargo build succeeded but no binary found in target/release/.")
    binary = bins[0]
    return {"mode": "server", "command": binary, "port": None}


def _handle_go(repo_dir: Path) -> dict:
    go = shutil.which("go")
    if not go:
        raise WebifyError("'go' not found on PATH.")
    print("  Running go build...", flush=True)
    proc = run([go, "build", "-o", "webify-app"], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or "(no output)"
        raise WebifyError(f"go build failed:\n{err}")
    binary = repo_dir / "webify-app"
    if not binary.exists():
        raise WebifyError("go build succeeded but binary not found.")
    return {"mode": "server", "command": str(binary), "port": None}


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
