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
        print(f"  Pulling latest changes...", flush=True)
        run(["git", "pull", "--ff-only"], cwd=dest)
        return
    if dest.exists():
        raise WebifyError(f"Destination already exists and is not a git repo: {dest}")
    print(f"  Cloning {repo_url} ...", flush=True)
    proc = run(["git", "clone", "--depth", "1", repo_url, str(dest)], check=False, capture=True)
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


DEFAULT_FUNCTIONS_DIRS = ("netlify/functions", "functions")


def detect_netlify_functions(repo_dir: Path, build: dict = None) -> dict:
    """Locate Netlify Functions and return an inventory for the gateway runtime.

    Netlify serves each file in the functions directory as
    ``/.netlify/functions/<basename>``. The default location is
    ``netlify/functions`` (or ``functions``), overridable via the
    ``build.functions`` key in ``netlify.toml``.

    Return a dict with:
      - ``dir``: absolute path of the functions directory (or None)
      - ``functions``: list of function names (file basenames, extension stripped)
      - ``files``: list of absolute paths to each function file
    """
    func_dir = None
    if build and build.get("functions"):
        candidate = repo_dir / str(build["functions"])
        if candidate.is_dir():
            func_dir = candidate
    if func_dir is None:
        for rel in DEFAULT_FUNCTIONS_DIRS:
            candidate = repo_dir / rel
            if candidate.is_dir():
                func_dir = candidate
                break
    if func_dir is None:
        return {"dir": None, "functions": [], "files": []}

    files = []
    for p in sorted(func_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() in (".js", ".mjs", ".cjs", ".ts"):
            files.append(p)
    functions = []
    for p in files:
        name = p.stem
        if p.suffix.lower() == ".ts" and name.endswith((".d",)):
            continue
        functions.append(name)
    return {"dir": str(func_dir), "functions": functions, "files": [str(p) for p in files]}


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


def build_repo(repo_dir: Path, port: int = 7070, user_env: dict = None) -> dict:
    """Detect project type, install deps, build if needed.
    ...
    """
    # netlify.toml drives the build (Netlify-compatible deployment).
    toml_path = repo_dir / "netlify.toml"
    build = {}
    if toml_path.exists():
        import toml
        build = toml.load(toml_path).get("build", {})

    # Functions runtime: locate netlify/functions (or build.functions from toml).
    funcs = detect_netlify_functions(repo_dir, build)
    functions_meta = {
        "netlify_functions": {
            "dir": funcs["dir"],
            "functions": funcs["functions"],
        }
    }

    if "command" in build:
        toml_env = build.get("environment", {})
        base = build.get("base", "")
        workdir = repo_dir / base if base else repo_dir

        if not workdir.is_dir():
            raise WebifyError(f"Build base directory '{base}' does not exist in repo.")

        # Install dependencies first if package.json exists
        if (workdir / "package.json").exists():
            _npm_install(workdir)
        print(f"  Running netlify build command: {build['command']}", flush=True)
        import subprocess, os
        merged = {**os.environ, **(toml_env or {}), **(user_env or {}), "PORT": str(port)}
        proc = subprocess.run(build["command"], shell=True, cwd=workdir, env=merged, check=False)
        if proc.returncode != 0:
            raise WebifyError(f"Build command '{build['command']}' failed.")

        # Static site — publish dir is relative to base
        publish = build.get("publish", ".")
        served = workdir / publish if publish != "." else workdir

        # If it's a node project with a start script and no publish dir, run as server
        if (workdir / "package.json").exists():
            scripts = _npm_scripts(workdir)
            if "start" in scripts and publish == ".":
                return {
                    "mode": "server",
                    "command": "npm run start",
                    "port": port,
                    "env": toml_env,
                    **functions_meta,
                }

        return {
            "mode": "static",
            "served": served,
            "port": port,
            **functions_meta,
        }

    project = detect_project_type(repo_dir)
    if project["type"] == "node":
        return {**_handle_node(repo_dir, project, port), **functions_meta}
    if project["type"] == "make":
        return {**_handle_make(repo_dir), **functions_meta}
    if project["type"] == "cargo":
        return {**_handle_cargo(repo_dir, port), **functions_meta}
    if project["type"] == "go":
        return {**_handle_go(repo_dir, port), **functions_meta}
    return {**_handle_unknown(repo_dir, port), **functions_meta}


def _handle_unknown(repo_dir: Path, port: int = 7070) -> dict:
    """Fallback for unrecognized projects — serve whatever is present."""
    return {"mode": "static", "served": detect_served_dir(repo_dir), "port": port}


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


def _handle_node(repo_dir: Path, project: dict, port: int = 7070) -> dict:
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
        return {"mode": "static", "served": detect_served_dir(repo_dir), "port": port}

    if action in ("dev", "start"):
        # Dev server — pass PORT env var so it binds to our port.
        cmd = f"{npm} run {action}"
        print(f"  Starting {cmd} on port {port}...", flush=True)
        detected = _detect_server_port(repo_dir, [npm, "run", action], port=port)
        return {"mode": "server", "command": cmd, "port": detected}

    # No useful scripts — just serve the repo.
    return {"mode": "static", "served": detect_served_dir(repo_dir), "port": port}


def _detect_server_port(repo_dir: Path, cmd: list, port: int = 7070, timeout: float = 15.0) -> int:
    """Run a command with PORT env var, detect the actual port it listens on."""
    import re
    import time

    env = {**subprocess.os.environ, "PORT": str(port)}

    proc = subprocess.Popen(
        cmd, cwd=repo_dir, env=env,
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


def _handle_cargo(repo_dir: Path, port: int = 7070) -> dict:
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
    return {"mode": "server", "command": binary, "port": port}


def _handle_go(repo_dir: Path, port: int = 7070) -> dict:
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
    return {"mode": "server", "command": str(binary), "port": port}


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


def write_env_file(repo_dir: Path, env: dict) -> None:
    """Write a .env file to repo_dir from a dict of key=value pairs."""
    if not env:
        return
    lines = []
    for k, v in env.items():
        # Shell-safe quoting: wrap values with spaces or special chars in quotes
        val = str(v)
        if any(c in val for c in " \t\"'\\$`"):
            val = '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(f"{k}={val}")
    (repo_dir / ".env").write_text("\n".join(lines) + "\n")


def parse_env_file(repo_dir: Path) -> dict:
    """Parse a .env file into a dict. Handles KEY=VALUE and KEY='value'."""
    env = {}
    env_path = repo_dir / ".env"
    if not env_path.exists():
        return env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        env[key] = val
    return env
