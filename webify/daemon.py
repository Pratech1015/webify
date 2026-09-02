"""Manage deployed services as systemd *user* units.

Each Webify service maps to a systemd user unit named:

    webify-<name>.service        (the python http.server)
    webify-<name>-tunnel.service (optional cloudflared quick tunnel)

systemd handles the daemonization, PID tracking, automatic restart, and
status reporting, giving easy start/stop/enable/disable from the CLI.
"""

import subprocess
from pathlib import Path

from .config import BASE_DIR, ensure_dirs
from .core import WebifyError, is_systemd_user_available, run

USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


class SystemdUnavailable(WebifyError):
    pass


def ensure_systemd():
    if not is_systemd_user_available():
        raise SystemdUnavailable(
            "A systemd user session is not available. "
            "Ensure a login session is active (e.g. from a normal user shell)."
        )


def _unit_dir():
    USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    return USER_UNIT_DIR


def http_unit_name(name: str) -> str:
    return f"webify-{name}.service"


def web_unit_name() -> str:
    return "webify-web.service"


def tunnel_unit_name(name: str) -> str:
    return f"webify-{name}-tunnel.service"


def deploy_unit_name(name: str) -> str:
    return f"webify-{name}-deploy.service"


def watcher_unit_name(name: str) -> str:
    return f"webify-{name}-watcher.service"


def write_watcher_unit(name: str) -> None:
    """Write systemd unit for background repo watcher."""
    python = _python_bin()
    content = f"""# Managed by Webify — do not edit manually.
[Unit]
Description=Webify watcher for {name}
After=network.target

[Service]
ExecStart={python} -m webify.watcher_service {name}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
    _write_unit(_unit_dir() / watcher_unit_name(name), content)


def start_watcher_unit(name: str) -> None:
    start_unit(watcher_unit_name(name), enable=False)


def stop_watcher_unit(name: str) -> None:
    stop_unit(watcher_unit_name(name), disable=True)


def write_deploy_unit(name: str) -> None:
    """Write the systemd user unit that clones/builds/starts a site in the background.

    Deploys run as their own service so the dashboard & CLI never block on
    cloning/building, the page can be reloaded freely, and the build output is
    captured in the deploy unit's journal (shown in the Deploys tab).
    """
    python = _python_bin()
    content = f"""# Managed by Webify — do not edit manually.
[Unit]
Description=Webify deploy {name}
After=network.target

[Service]
Type=oneshot
RemainAfterExit=no
ExecStart={python} -m webify.deploy_service {name}

[Install]
WantedBy=default.target
"""
    _write_unit(_unit_dir() / deploy_unit_name(name), content)


def write_web_unit(port: int) -> None:
    """Write the systemd user unit for the Webify web dashboard."""
    python = _python_bin()
    content = f"""# Managed by Webify — do not edit manually.
[Unit]
Description=Webify Web Dashboard
After=network.target

[Service]
Type=simple
Environment=WEBIFY_WEB_PORT={port}
ExecStart={python} -m webify.web_service {port}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""
    _write_unit(_unit_dir() / web_unit_name(), content)


def _systemctl(args, check=True):
    ensure_systemd()
    proc = run(["systemctl", "--user", *args], check=False)
    if check and proc.returncode != 0:
        raise WebifyError(
            f"'systemctl --user {' '.join(args)}' failed:\n{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def write_http_unit(name: str, served_dir: Path, port: int) -> None:
    """Write the http.server systemd user unit for a service."""
    python = _python_bin()
    content = f"""# Managed by Webify — do not edit manually.
[Unit]
Description=Webify service {name} (static site on port {port})
After=network.target

[Service]
Type=simple
WorkingDirectory={served_dir}
ExecStart={python} -m http.server {port} --bind 0.0.0.0
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    path = _unit_dir() / http_unit_name(name)
    _write_unit(path, content)


def write_custom_unit(name: str, command: str, workdir: Path = None, port: int = None) -> None:
    """Write a systemd user unit that runs an arbitrary command (dev server, binary, etc.)."""
    desc = f"Webify service {name}"
    if port:
        desc += f" (port {port})"
    workdir_line = f"WorkingDirectory={workdir}" if workdir else ""
    env_line = f"Environment=PORT={port}" if port else ""
    content = f"""# Managed by Webify — do not edit manually.
[Unit]
Description={desc}
After=network.target

[Service]
Type=simple
{workdir_line}
{env_line}
ExecStart={command}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    path = _unit_dir() / http_unit_name(name)
    _write_unit(path, content)


def write_tunnel_unit(name: str, port: int) -> None:
    """Write a cloudflared quick-tunnel unit bound to the service's http port."""
    cloudflared = _which("cloudflared")
    content = f"""# Managed by Webify — do not edit manually.
[Unit]
Description=Webify tunnel for {name} (cloudflared)
After=network.target {http_unit_name(name)}
Requires={http_unit_name(name)}

[Service]
Type=simple
ExecStart={cloudflared} tunnel --url http://localhost:{port} --no-autoupdate
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    _write_unit(_unit_dir() / tunnel_unit_name(name), content)


def remove_units(name: str) -> None:
    for unit in (http_unit_name(name), tunnel_unit_name(name), deploy_unit_name(name), watcher_unit_name(name)):
        p = _unit_dir() / unit
        if p.exists():
            p.unlink()


def rename_units(old_name: str, new_name: str) -> None:
    """Rename systemd unit files from old_name to new_name."""
    for suffix in ("", "-tunnel", "-deploy", "-watcher"):
        old_path = _unit_dir() / f"webify-{old_name}{suffix}.service"
        new_path = _unit_dir() / f"webify-{new_name}{suffix}.service"
        if old_path.exists():
            old_path.rename(new_path)


def _write_unit(path: Path, content: str):
    _unit_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def daemon_reload():
    _systemctl(["daemon-reload"])


def start_unit(unit: str, enable: bool = True):
    _systemctl(["start", unit])
    if enable:
        _systemctl(["enable", unit], check=False)


def stop_unit(unit: str, disable: bool = True):
    _systemctl(["stop", unit], check=False)
    if disable:
        _systemctl(["disable", unit], check=False)


def restart_unit(unit: str):
    _systemctl(["restart", unit])


def reset_failed_unit(unit: str) -> None:
    """Clear a unit's failed state so 'start' can be issued again. Non-blocking."""
    _systemctl(["reset-failed", unit], check=False)


def unit_active(unit: str) -> bool:
    proc = _systemctl(["is-active", unit], check=False)
    return proc.returncode == 0


def unit_status(unit: str) -> str:
    proc = _systemctl(["is-active", unit], check=False)
    state = proc.stdout.strip() or "inactive"
    enabled = _systemctl(["is-enabled", unit], check=False).stdout.strip()
    if state != "active":
        return {"failed": "failed", "activating": "starting", "inactive": "stopped"}.get(state, state)
    if enabled == "enabled":
        return f"running (enabled)"
    return "running"


def unit_pid(unit: str):
    """Return the main PID of a unit via systemctl show, or None."""
    proc = _systemctl(["show", "-p", "MainPID", "--value", unit], check=False)
    if proc.returncode == 0:
        val = proc.stdout.strip()
        if val.isdigit() and int(val) > 0:
            return int(val)
    return None


def journal(unit: str, lines: int = 50) -> str:
    """Return recent journal lines for a unit using journalctl directly."""
    import subprocess
    cmd = ["journalctl", "--user", "-u", unit, "--no-pager", "-n", str(lines)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _python_bin() -> str:
    import sys
    return sys.executable


def _which(binary: str) -> str:
    import shutil
    path = shutil.which(binary)
    if not path:
        raise WebifyError(f"'{binary}' not found on PATH.")
    return path


def parse_status_name(name: str) -> str:
    return name


def is_cloudflared_ready() -> bool:
    """Check if cloudflared is installed and configured."""
    try:
        # 'cloudflared tunnel list' fails if not logged in
        subprocess.run(["cloudflared", "tunnel", "list"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def write_tunnel_unit(name: str, tunnel_name: str) -> None:
    """Write systemd unit for persistent cloudflared tunnel."""
    config = Path.home() / ".cloudflared" / "config.yml"
    content = f"""[Unit]
Description=Webify tunnel for {name}
After=network.target webify-{name}.service
Requires=webify-{name}.service

[Service]
ExecStart=/usr/bin/cloudflared tunnel --config {config} run {tunnel_name}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
    _write_unit(_unit_dir() / tunnel_unit_name(name), content)


def start_tunnel_unit(name: str) -> None:
    start_unit(tunnel_unit_name(name), enable=False)


def stop_tunnel_unit(name: str) -> None:
    stop_unit(tunnel_unit_name(name), disable=True)
