"""Service mode abstractions: local, cloudflared, and nginx-managed.

Each mode launches the http.server systemd unit (and, for cloudflared, a
companion tunnel unit, and for nginx, a reverse-proxy vhost).
"""

import re
import shutil
import subprocess
from pathlib import Path

from ..config import SERVICES_DIR
from ..core import WebifyError, clone_repo, detect_served_dir
from .. import daemon
from ..daemon import (
    http_unit_name, tunnel_unit_name, unit_active, unit_pid, unit_status,
)


class BaseService:
    mode = "base"

    def __init__(self, name: str):
        self.name = name
        self.dir = SERVICES_DIR / name
        self.repo_dir = self.dir / "repo"
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)
        self.logfile = self.dir / "logs" / "server.log"

    # ---- lifecycle (subclass hooks) ----
    def start(self, repo_url: str, port: int, **kw) -> dict:
        served = self.ensure_cloned(repo_url)
        served = detect_served_dir(served)
        self.precheck(port, served)
        daemon.write_http_unit(self.name, served, port)
        daemon.daemon_reload()
        daemon.start_unit(http_unit_name(self.name))
        self.extra_after_start(port, served)
        info = {
            "mode": self.mode, "repo": repo_url, "port": port,
            "dir": str(self.dir), "served": str(served),
            "unit": http_unit_name(self.name),
        }
        return info

    def precheck(self, port: int, served: Path):
        """Validate prerequisites before launching anything. Override per mode."""

    def extra_after_start(self, port: int, served: Path):
        pass

    def stop(self) -> bool:
        running = unit_active(http_unit_name(self.name))
        daemon.stop_unit(http_unit_name(self.name))
        self.extra_after_stop()
        return running

    def extra_after_stop(self):
        pass

    def status(self) -> str:
        return unit_status(http_unit_name(self.name))

    def url(self) -> str:
        return f"http://localhost:{_port(self)}"

    # ---- helpers ----
    def ensure_cloned(self, repo_url: str) -> Path:
        clone_repo(repo_url, self.repo_dir)
        return self.repo_dir


class LocalService(BaseService):
    mode = "local"


class CloudflaredService(BaseService):
    mode = "cloudflared"

    def precheck(self, port: int, served: Path):
        _require("cloudflared")

    def extra_after_start(self, port: int, served: Path):
        _require("cloudflared")
        daemon.write_tunnel_unit(self.name, port)
        daemon.daemon_reload()
        daemon.start_unit(tunnel_unit_name(self.name))

    def extra_after_stop(self):
        daemon.stop_unit(tunnel_unit_name(self.name))

    def status(self) -> str:
        base = unit_status(http_unit_name(self.name))
        if unit_active(tunnel_unit_name(self.name)):
            return f"{base}, tunnel up"
        return base

    def url(self) -> str:
        return _tunnel_url(self.name) or self._local_url()

    def _local_url(self):
        return f"http://localhost:{_port(self)}"


class NginxService(BaseService):
    mode = "nginx"

    def extra_after_start(self, port: int, served: Path):
        nginx = _require("nginx")
        conf_path = self._write_nginx_conf(nginx, port)
        _test_and_reload(nginx)

    def precheck(self, port: int, served: Path):
        _require("nginx")
        conf_dir = Path("/etc/nginx/conf.d")
        if not conf_dir.is_dir():
            raise WebifyError(
                "nginx is not configured with a conf.d directory at /etc/nginx/conf.d."
            )
        import os
        if not os.access(str(conf_dir), os.W_OK):
            raise WebifyError(
                "Cannot write /etc/nginx/conf.d (need root). Re-run this command with sudo."
            )

    def extra_after_stop(self):
        nginx = shutil.which("nginx")
        conf = Path("/etc/nginx/conf.d") / f"webify-{self.name}.conf"
        if nginx and conf.exists():
            try:
                conf.unlink()
                _test_and_reload(nginx)
            except WebifyError:
                pass

    def _write_nginx_conf(self, nginx: str, port: int) -> Path:
        conf_path = Path("/etc/nginx/conf.d") / f"webify-{self.name}.conf"
        server_name = f"webify-{self.name}.localhost"
        body = (
            f"# Managed by Webify — do not edit manually.\n"
            f"server {{\n"
            f"    listen 80;\n"
            f"    server_name {server_name};\n"
            f"    location / {{\n"
            f"        proxy_pass http://127.0.0.1:{port};\n"
            f"        proxy_set_header Host $host;\n"
            f"        proxy_set_header X-Real-IP $remote_addr;\n"
            f"    }}\n"
            f"}}\n"
        )
        try:
            conf_path.write_text(body)
        except PermissionError:
            raise WebifyError(
                "Cannot write /etc/nginx/conf.d. Run this command with sudo."
            )
        return conf_path

    def status(self) -> str:
        base = unit_status(http_unit_name(self.name))
        if (Path("/etc/nginx/conf.d") / f"webify-{self.name}.conf").exists():
            return f"{base}, nginx enabled"
        return base

    def url(self) -> str:
        return f"http://webify-{self.name}.localhost"


# ---- factory & helpers ------------------------------------------------------

def build_service(name: str, mode: str = "local") -> BaseService:
    klass = {
        "local": LocalService,
        "cloudflared": CloudflaredService,
        "nginx": NginxService,
    }.get(mode and mode.lower())
    if not klass:
        raise WebifyError(
            f"Unknown mode '{mode}'. Choose one of: local, cloudflared, nginx."
        )
    return klass(name)


def _port(service) -> int:
    try:
        import json
        from .. import state
        info = state.get_service(service.name) or {}
        return int(info.get("port"))
    except (TypeError, ValueError):
        return 0


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise WebifyError(
            f"'{binary}' is required for this mode but was not found on PATH."
        )
    return path


def _test_and_reload(nginx: str):
    ret = subprocess.run([nginx, "-t"], capture_output=True, text=True)
    if ret.returncode != 0:
        raise WebifyError(f"nginx config test failed:\n{ret.stderr}")
    subprocess.run([nginx, "-s", "reload"], capture_output=True, check=False)


_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _tunnel_url(name: str):
    """Best-effort extraction of the public tunnel URL for a specific service.

    Supports both quick tunnels (random .trycloudflare.com) and authenticated
    tunnels (user's own domain when logged into Cloudflare).
    """
    unit = f"webify-{name}-tunnel.service"
    lines = []

    # Query the specific tunnel unit's journal.
    try:
        proc = subprocess.run(
            ["journalctl", "--user", "-u", unit, "--no-pager", "-n", "800", "-o", "cat"],
            capture_output=True, text=True, timeout=8,
        )
        lines = proc.stdout.splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Also check the fallback capture file.
    capture = SERVICES_DIR / name / "logs" / "cloudflared.log"
    if capture.exists():
        try:
            lines.extend(capture.read_text(errors="ignore").splitlines())
        except OSError:
            pass

    # Scan lines in reverse (newest first) for the actual tunnel URL.
    for line in reversed(lines):
        # Quick tunnel: "Your quick Tunnel has been created! Visit it at ...: https://..."
        m = re.search(
            r"(?:Visit it at|visit it at)[^:]*:\s*(https?://\S+)",
            line, re.IGNORECASE,
        )
        if m:
            url = m.group(1).rstrip(".,;)")
            if "trycloudflare.com" in url or "." in url.split("//")[-1]:
                return url

    # Fallback: look for authenticated tunnel URL in connection registration lines.
    # Pattern: "INF Connection registered ... url=https://your-domain.com"
    for line in reversed(lines):
        m = re.search(r"\burl=(https?://\S+)", line)
        if m:
            url = m.group(1).rstrip(".,;)")
            if url != "http://localhost" and "trycloudflare.com" not in url:
                return url

    # Last resort: any trycloudflare.com URL that looks like a real tunnel.
    for line in reversed(lines):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
        if m:
            return m.group(0)

    return None
