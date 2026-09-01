"""Webify dashboard — Flask web UI for managing deployed sites."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import daemon, state
from .config import DEFAULT_PORT, SERVICES_DIR, ensure_dirs
from .core import WebifyError, find_free_port
from .services import build_service

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
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
        "pid": pid,
        "served": info.get("served"),
        "command": info.get("command"),
        "unit": info.get("unit"),
        "dir": info.get("dir"),
    }


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

        try:
            info = svc.start(repo, port)
        except WebifyError as exc:
            try:
                info = svc.start_no_build(repo, port)
                state.save_service(name, info)
                return redirect(url_for("site_detail", name=name))
            except WebifyError:
                return render_template("new.html", error=str(exc))

        state.save_service(name, info)
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
    unit = info.get("unit") or daemon.http_unit_name(name)
    lines = int(request.args.get("lines", 100))
    log_text = daemon.journal(unit, lines)
    if info.get("mode") == "cloudflared":
        tunnel = daemon.journal(daemon.tunnel_unit_name(name), 50)
        if tunnel.strip():
            log_text += "\n--- tunnel ---\n" + tunnel
    return jsonify({"logs": log_text})


@app.route("/site/<name>/start", methods=["POST"])
def site_start(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    svc = build_service(name, info.get("mode", "local"))
    if "running" in svc.status():
        return jsonify({"status": "already running"})
    try:
        svc.start(info.get("repo"), info.get("port"))
    except WebifyError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "started"})


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
    daemon.restart_unit(info.get("unit") or daemon.http_unit_name(name))
    if info.get("mode") == "cloudflared":
        daemon.restart_unit(daemon.tunnel_unit_name(name))
    return jsonify({"status": "restarted"})


@app.route("/site/<name>/delete", methods=["POST"])
def site_delete(name):
    info = state.get_service(name)
    if not info:
        return jsonify({"error": "not found"}), 404
    svc = build_service(name, info.get("mode", "local"))
    daemon.stop_unit(daemon.http_unit_name(name), disable=True)
    daemon.stop_unit(daemon.tunnel_unit_name(name), disable=True)
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
            svc = build_service(new_name, info.get("mode", "local"))
            svc.start(info.get("repo"), info.get("port"))
        except WebifyError:
            pass

    return jsonify({"status": "renamed", "new_name": new_name})


def run_web(host: str = "0.0.0.0", port: int = None):
    """Start the dashboard in foreground using the waitress production server."""
    ensure_dirs()
    p = port or _get_port()
    print(f"Webify dashboard running at http://localhost:{p}")
    from waitress import serve
    serve(app, host=host, port=p, threads=4)
