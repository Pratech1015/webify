"""Webify dashboard — Flask web UI for managing deployed sites."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import __version__, daemon, state
from .config import DEFAULT_PORT, SERVICES_DIR, ensure_dirs
from .core import WebifyError, find_free_port
from .services import build_service

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)


@app.context_processor
def inject_version():
    return dict(version=__version__)
app.secret_key = os.urandom(24)


def _get_port() -> int:
    return int(os.environ.get("WEBIFY_WEB_PORT", 8000))


def _service_info(name: str) -> dict:
    info = state.get_service(name)
    if not info:
        return None
    svc = build_service(name, info.get("mode", "local"))
    status = svc.status()
    url = svc.url()
    pid = None
    try:
        pid = daemon.unit_pid(info.get("unit", daemon.http_unit_name(name)))
    except WebifyError:
        pass
    return {
        "name": name,
        "mode": info.get("mode", "local"),
        "port": info.get("port"),
        "repo": info.get("repo"),
        "url": url,
        "status": status,
        "running": "running" in status,
        "deploy": deploy_status(name, info=info, running="running" in status),
        "deploying": deploy_active(name),
        "deploy_error": info.get("deploy_error") or "",
        "pid": pid,
        "served": info.get("served"),
        "command": info.get("command"),
        "unit": info.get("unit"),
        "dir": info.get("dir"),
        "netlify_functions": info.get("netlify_functions") or [],
        "site_port": info.get("site_port"),
    }


def deploy_active(name: str) -> bool:
    """True if a deploy is currently running for this site."""
    return daemon.unit_active(daemon.deploy_unit_name(name))


def deploy_status(name: str, info: dict = None, running: bool = False) -> str:
    """Describe the deploy state: deploying | published | failed | not-deployed.

    Uses the state-recorded deploy_status (written by the deploy service) as the
    source of truth, falling back to live systemd checks. Does not re-read state
    here (a background deploy may be rewriting it, which can surface as a
    transient None); the caller passes the known info dict.
    """
    if running:
        return "published"
    if daemon.unit_active(daemon.deploy_unit_name(name)):
        return "deploying"
    if info and info.get("deploy_status") == "failed":
        return "failed"
    if info and info.get("deploy_status") == "ok":
        return "published"
    return "not-deployed"


def _trigger_deploy(name: str) -> None:
    """Queues a deploy by handing the command to systemd and returning.

    This is strictly fire-and-forget: no blocking calls that wait on an
    in-flight build, so the page reloads freely while a deploy runs.
      1. write the deploy unit (cheap file write)
      2. reload once so systemd sees the (possibly new) unit
      3. clear any prior failed state so 'start' will take effect
      4. start the unit — for a running oneshot, systemd returns immediately
    """
    daemon.write_deploy_unit(name)
    daemon.daemon_reload()
    daemon.reset_failed_unit(daemon.deploy_unit_name(name))
    daemon.start_unit(daemon.deploy_unit_name(name), enable=False)


@app.route("/")
def index():
    services = state.list_services()
    sites = []
    for s in services:
        info = _service_info(s["name"])
        if info:
            sites.append(info)
    return render_template("index.html", sites=sites)


@app.route("/new", methods=["GET", "POST"])
def new_site():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        repo = request.form.get("repo", "").strip()
        mode = request.form.get("mode", "local")
        port_raw = request.form.get("port", "").strip()

        if mode == "cloudflared" and not daemon.is_cloudflared_ready():
            return render_template("new.html", error="Cloudflared is not configured (not logged in).")

        if not name or not repo:
            return render_template("new.html", error="Name and repo URL are required.")
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return render_template("new.html", error="Invalid name. Use letters, numbers, dash, underscore.")
        if state.get_service(name):
            return render_template("new.html", error=f"A service named '{name}' already exists.")

        port = int(port_raw) if port_raw and port_raw.isdigit() else find_free_port(DEFAULT_PORT)

        svc = build_service(name, mode)
        svc.dir.mkdir(parents=True, exist_ok=True)
        (svc.dir / "logs").mkdir(parents=True, exist_ok=True)

        # Register state first, then kick off a background deploy service.
        state.save_service(name, {
            "name": name,
            "mode": mode,
            "repo": repo,
            "port": port,
            "dir": str(svc.dir),
        })
        try:
            _trigger_deploy(name)
        except WebifyError as exc:
            return render_template("new.html", error=str(exc))

        return redirect(url_for("site_detail", name=name))

    return render_template("new.html")


@app.route("/site/<name>")
def site_detail(name):
    info = _service_info(name)
    if not info:
        abort(404)
    return render_template("site.html", site=info)


@app.route("/site/<name>/logs")
def site_logs(name):
    info = state.get_service(name)
    if not info:
        abort(404)
    lines = int(request.args.get("lines", 200))

    # Deploy logs (cloning/building) first — that's what the Deploys tab shows.
    deploy_log = daemon.journal(daemon.deploy_unit_name(name), lines)

    # Runtime logs from the site's serve unit.
    unit = info.get("unit") or daemon.http_unit_name(name)
    run_log = daemon.journal(unit, lines)

    parts = []
    if deploy_log.strip():
        parts.append("── Deploy ──\n" + deploy_log)
    if run_log.strip():
        parts.append("── Runtime ──\n" + run_log)
    if not parts:
        parts.append("No deploy has been run yet for this site.")
    log_text = "\n\n".join(parts)
    return jsonify({
        "logs": log_text,
        "deploying": deploy_active(name),
        "failed": deploy_active(name) is False and deploy_status(
            name, info=info, running=info.get("deploy_status") == "ok"
        ) == "failed",
    })


@app.route("/site/<name>/deploy", methods=["POST"])
def site_deploy(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    try:
        _trigger_deploy(name)
    except WebifyError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "deploying"})


@app.route("/site/<name>/start", methods=["POST"])
def site_start(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    try:
        _trigger_deploy(name)
    except WebifyError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "deploying"})


@app.route("/site/<name>/stop", methods=["POST"])
def site_stop(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    svc = build_service(name, info.get("mode", "local"))
    svc.stop()
    return jsonify({"status": "stopped"})


@app.route("/site/<name>/restart", methods=["POST"])
def site_restart(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    try:
        _trigger_deploy(name)
    except WebifyError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "deploying"})


@app.route("/site/<name>/delete", methods=["POST"])
def site_delete(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    svc = build_service(name, info.get("mode", "local"))
    daemon.stop_unit(daemon.http_unit_name(name), disable=True)
    daemon.stop_unit(daemon.tunnel_unit_name(name), disable=True)
    daemon.stop_unit(daemon.deploy_unit_name(name), disable=True)
    daemon.stop_functions_unit(name)
    daemon.stop_watcher_unit(name)
    daemon.remove_units(name)
    daemon.daemon_reload()
    import shutil
    shutil.rmtree(svc.dir, ignore_errors=True)
    state.remove_service(name)
    return jsonify({"status": "deleted"})


@app.route("/site/<name>/rename", methods=["POST"])
def site_rename(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    new_name = request.json.get("name", "").strip() if request.is_json else ""
    if not new_name:
        return jsonify({"error": "new name required"}), 400
    if not re.match(r"^[a-zA-Z0-9_-]+$", new_name):
        return jsonify({"error": "invalid name"}), 400
    if state.get_service(new_name):
        return jsonify({"error": f"'{new_name}' already exists"}), 400

    was_running = "running" in build_service(name, info.get("mode", "local")).status()
    if was_running:
        try:
            svc = build_service(name, info.get("mode", "local"))
            svc.stop()
        except WebifyError:
            pass

    daemon.rename_units(name, new_name)
    old_dir = info.get("dir")
    if old_dir:
        old_path = Path(old_dir)
        new_path = old_path.parent / new_name
        if old_path.exists():
            old_path.rename(new_path)
            info["dir"] = str(new_path)
    info["unit"] = daemon.http_unit_name(new_name)
    state.rename_service(name, new_name)
    daemon.daemon_reload()

    if was_running:
        try:
            _trigger_deploy(new_name)
        except WebifyError:
            pass

    return jsonify({"status": "renamed", "new_name": new_name})


def run_web(host: str = "0.0.0.0", port: int = None):
    """Start the dashboard in foreground using the waitress production server."""
    ensure_dirs()
    p = port or _get_port()
    try:
        from waitress import serve
    except ImportError:
        raise WebifyError(
            "The dashboard needs 'waitress' and 'flask'. "
            "Install them with: pip install webify[web]  (or: sudo apt install python3-flask python3-waitress)"
        )
    print(f"Webify dashboard running at http://localhost:{p}")
    serve(app, host=host, port=p, threads=4)
