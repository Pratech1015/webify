"""Command-line interface for Webify."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import __version__, state
from .config import (
    BOLD, CYAN, DEFAULT_PORT, DIM, GREEN, MODES, RED, RESET, SERVICES_DIR,
    YELLOW, ensure_dirs, mode_icon,
)
from .core import WebifyError, find_free_port
from .services import build_service
from . import daemon

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _e(msg):
    print(f"{RED}error:{RESET} {msg}", file=sys.stderr, flush=True)


def _info(msg):
    print(f"{CYAN}‹{RESET} {msg}", flush=True)


def _ok(msg):
    print(f"{GREEN}✓{RESET} {msg}", flush=True)


def _input(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    try:
        val = input(f"{prompt}{suffix} ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return val or default


def _validate_name(name):
    if not NAME_RE.match(name):
        raise WebifyError(
            f"Invalid name '{name}'. Use only letters, numbers, dash, underscore."
        )


def _ask_mode(default="local", interactive=True):
    if not interactive:
        return default
    print(f"{DIM}Available modes:{RESET}")
    for m in MODES:
        print(f"  {mode_icon(m)} {m:<12} {_mode_desc(m)}")
    while True:
        mode = _input("Select mode", default) or default
        mode = mode.lower()
        if mode in MODES:
            return mode
        print(f"{RED}Unknown mode '{mode}'. Choose one of: {', '.join(MODES)}.{RESET}")


def _mode_desc(mode):
    return {
        "local": "localhost only",
        "cloudflared": "public URL via Cloudflare (requires cloudflared)",
        "nginx": "behind nginx reverse proxy (requires nginx + root)",
    }[mode]


def _port_for(port, interactive=True):
    if port:
        return port
    if not interactive:
        return find_free_port(DEFAULT_PORT)
    while True:
        raw = _input("Use custom Port (Y/N)", "N")
        if raw.lower() in ("n", "no", ""):
            return find_free_port(DEFAULT_PORT)
        if raw.lower() in ("y", "yes"):
            while True:
                try:
                    p = int(_input("Port", str(DEFAULT_PORT)))
                    if 1 <= p <= 65535:
                        return p
                    print(f"{RED}Port must be 1-65535.{RESET}")
                except ValueError:
                    print(f"{RED}Port must be a number.{RESET}")
        else:
            print(f"{RED}Please answer Y or N.{RESET}")


def cmd_create(args):
    ensure_dirs()
    name = args.name
    _validate_name(name)

    if (SERVICES_DIR / name).exists():
        _e(f"A service named '{name}' already exists.")
        sys.exit(1)

    mode = args.mode
    port = args.port
    interactive = not args.repo  # only need to prompt for repo if not given

    if interactive:
        print(f"{BOLD}Creating service '{name}'{RESET}")

    repo = args.repo
    if not repo:
        repo = _input("Enter Repo link") or _input("Enter Repo link again")
        if not repo:
            _e("A repo link is required.")
            sys.exit(1)

    if not mode:
        mode = _ask_mode(default="local", interactive=interactive)
    if not port:
        port = _port_for(port, interactive=interactive)

    svc = build_service(name, mode)
    svc.dir.mkdir(parents=True, exist_ok=True)
    (svc.dir / "logs").mkdir(parents=True, exist_ok=True)

    # Register state first, then run the deploy as a background systemd service.
    state.save_service(name, {
        "mode": mode,
        "repo": repo,
        "port": port,
        "dir": str(svc.dir),
    })

    _info(f"Deploying {repo} → {name} (port {port}) in the background …")
    try:
        _deploy_svc(name)
    except WebifyError as exc:
        _e(str(exc))
        _cleanup_partial(svc)
        state.remove_service(name)
        sys.exit(1)

    _ok(f"Service {name} registered on port {port}. Deploy running in background.")
    _info("Watch progress with: webify logs " + name)
    _info("Or check status with:    webify status " + name)


def _deploy_svc(name):
    """Write the deploy unit and start it (non-blocking)."""
    daemon.write_deploy_unit(name)
    daemon.daemon_reload()
    daemon.stop_unit(daemon.deploy_unit_name(name), disable=False)
    daemon.start_unit(daemon.deploy_unit_name(name), enable=False)


def cmd_list(_args):
    services = state.list_services()
    if not services:
        print(f"{DIM}No services yet. Create one with: webify create <name>{RESET}")
        return
    print(f"{BOLD}{'NAME':<16}{'MODE':<12}{'PORT':<8}{'STATUS':<28}URL{RESET}")
    for s in services:
        try:
            svc = build_service(s["name"], s.get("mode", "local"))
            status = svc.status()
            url = svc.url()
        except WebifyError:
            status, url = "unknown", "-"
        port = s.get("port", "-")
        print(
            f"{s['name']:<16}{s.get('mode','local'):<12}{str(port):<8}"
            f"{status:<28}{url}"
        )


def _require_service(name):
    svc = build_service(name, state.get_service(name)["mode"]
                        if state.get_service(name) else "local")
    info = state.get_service(name)
    if not info:
        raise WebifyError(f"No service named '{name}'. Run 'webify create {name}'.")
    return svc, info


def _cleanup_partial(svc):
    """Remove leftover dirs/units after a failed create."""
    import shutil
    daemon.remove_units(svc.name)
    try:
        daemon.daemon_reload()
    except WebifyError:
        pass
    shutil.rmtree(svc.dir, ignore_errors=True)


def cmd_stop(args):
    _validate_name(args.name)
    try:
        svc, _ = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    if svc.stop():
        _ok(f"Service {args.name} stopped.")
    else:
        _info(f"Service {args.name} was not running.")


def cmd_kill(args):
    """Force-stop (kill) a service and disable its units."""
    _validate_name(args.name)
    try:
        svc, _ = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    daemon.stop_unit(daemon.http_unit_name(args.name), disable=True)
    daemon.stop_unit(daemon.tunnel_unit_name(args.name), disable=True)
    if "running" in svc.status():
        _e(f"Service {args.name} still has active units.")
        sys.exit(1)
    _ok(f"Service {args.name} killed and disabled.")


def cmd_start(args):
    _validate_name(args.name)
    try:
        svc, info = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    _info(f"Deploying {args.name} in the background …")
    try:
        _deploy_svc(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    _ok(f"Deploy started for {args.name}. Watch with: webify logs {args.name}")


def cmd_restart(args):
    _validate_name(args.name)
    try:
        _, info = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    _info(f"Redeploying {args.name} in the background …")
    try:
        _deploy_svc(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    _ok(f"Redeploy started for {args.name}. Watch with: webify logs {args.name}")


def cmd_enable(args):
    _validate_name(args.name)
    try:
        _, info = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    daemon._systemctl(["enable", info.get("unit") or daemon.http_unit_name(args.name)])
    _ok(f"Service {args.name} enabled (will start on login).")


def cmd_disable(args):
    _validate_name(args.name)
    try:
        _, info = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    daemon._systemctl(["disable", info.get("unit") or daemon.http_unit_name(args.name)], check=False)
    _ok(f"Service {args.name} disabled.")


def cmd_status(args):
    _validate_name(args.name)
    try:
        svc, info = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    print(f"{BOLD}{args.name}{RESET} ({info.get('mode')})")
    print(f"  status : {svc.status()}")
    print(f"  pid    : {pid_of(info)}")
    print(f"  url    : {svc.url()}")
    print(f"  repo   : {info.get('repo')}")
    print(f"  port   : {info.get('port')}")
    print(f"  unit   : {info.get('unit')}")
    print(f"  dir    : {info.get('dir')}")


def cmd_url(args):
    _validate_name(args.name)
    try:
        svc, _ = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    print(svc.url())


def cmd_logs(args):
    _validate_name(args.name)
    try:
        svc, info = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    unit = info.get("unit") or daemon.http_unit_name(args.name)
    deploy = daemon.journal(daemon.deploy_unit_name(args.name), args.lines)
    out = daemon.journal(unit, args.lines)
    printed = False
    if deploy.strip():
        print(f"{BOLD}── Deploy ──{RESET}")
        print(deploy)
        printed = True
    if out.strip():
        if printed:
            print()
        print(f"{BOLD}── Runtime ──{RESET}")
        print(out)
        printed = True
    if info.get("mode") == "cloudflared":
        tunnel = daemon.journal(daemon.tunnel_unit_name(args.name), args.lines)
        if tunnel.strip():
            if printed:
                print()
            print(f"{BOLD}── Tunnel ──{RESET}")
            print(tunnel)
            printed = True
    if not printed:
        print(f"{DIM}No logs yet for {args.name}.{RESET}")


def cmd_remove(args):
    _validate_name(args.name)
    try:
        svc, _ = _require_service(args.name)
    except WebifyError as exc:
        _e(str(exc)); sys.exit(1)
    daemon.stop_unit(daemon.http_unit_name(args.name), disable=True)
    daemon.stop_unit(daemon.tunnel_unit_name(args.name), disable=True)
    daemon.stop_unit(daemon.deploy_unit_name(args.name), disable=True)
    daemon.remove_units(args.name)
    daemon.daemon_reload()
    import shutil
    shutil.rmtree(svc.dir, ignore_errors=True)
    state.remove_service(args.name)
    _ok(f"Service {args.name} removed.")


def cmd_rename(args):
    _validate_name(args.old)
    _validate_name(args.new)
    if args.old == args.new:
        _e("Old and new names are the same.")
        sys.exit(1)

    info = state.get_service(args.old)
    if not info:
        _e(f"No service named '{args.old}'. Run 'webify list' to see services.")
        sys.exit(1)
    if state.get_service(args.new):
        _e(f"A service named '{args.new}' already exists.")
        sys.exit(1)

    was_running = "running" in build_service(args.old, info.get("mode", "local")).status()

    if was_running:
        _info(f"Stopping {args.old} …")
        try:
            svc, _ = _require_service(args.old)
            svc.stop()
        except WebifyError:
            pass

    _info(f"Renaming {args.old} → {args.new} …")

    daemon.rename_units(args.old, args.new)

    import shutil
    old_dir = info.get("dir")
    if old_dir:
        old_path = Path(old_dir)
        new_path = old_path.parent / args.new
        if old_path.exists():
            old_path.rename(new_path)
            info["dir"] = str(new_path)

    info["unit"] = daemon.http_unit_name(args.new)
    state.rename_service(args.old, args.new)
    daemon.daemon_reload()

    if was_running:
        _info(f"Restarting {args.new} …")
        try:
            svc = build_service(args.new, info.get("mode", "local"))
            svc.start(info.get("repo"), info.get("port"))
            state.save_service(args.new, info)
        except WebifyError as exc:
            _e(f"Renamed but could not restart: {exc}")
            state.save_service(args.new, info)
            return

    _ok(f"Service renamed: {args.old} → {args.new}")


def cmd_web(args):
    # --serve: run in foreground (production waitress server).
    if args.serve:
        ensure_dirs()
        try:
            from .web import run_web
        except ImportError:
            _e("Flask/waitress is required for the web dashboard.")
            _info("Install it with: pip install webify[web]")
            sys.exit(1)
        run_web(port=args.port)
        return

    port = args.port
    ensure_dirs()

    # Ensure the systemd unit exists with the current port.
    try:
        daemon.write_web_unit(port)
        daemon.daemon_reload()
    except WebifyError as exc:
        _e(f"Could not set up dashboard service: {exc}")
        sys.exit(1)

    unit = daemon.web_unit_name()

    if args.stop:
        daemon.stop_unit(unit)
        _ok("Webify dashboard stopped.")
        return
    if args.restart:
        daemon.restart_unit(unit)
        _ok("Webify dashboard restarted.")
        return
    if args.status:
        print(daemon.unit_status(unit))
        return

    # Default: toggle start/stop.
    if daemon.unit_active(unit):
        daemon.stop_unit(unit)
        _ok("Webify dashboard stopped.")
    else:
        daemon.start_unit(unit)
        _ok(f"Webify dashboard started at http://localhost:{port}")


def pid_of(info):
    try:
        unit = info.get("unit")
        if unit:
            pid = daemon.unit_pid(unit)
            if pid:
                return pid
    except WebifyError:
        pass
    return "—"


def build_parser():
    p = argparse.ArgumentParser(
        prog="webify",
        description="Webify — a self-hosted Netlify alternative for Linux.",
    )
    p.add_argument("--version", action="version", version=f"webify {__version__}")
    sub = p.add_subparsers(dest="command")

    create = sub.add_parser("create", help="Create and start a new service")
    create.add_argument("name", help="Service name")
    create.add_argument("--repo", help="Git repository URL (skip interactive prompt)")
    create.add_argument("--port", type=int, help="Port to use (default 7070)")
    create.add_argument("--mode", choices=MODES, help="local | cloudflared | nginx")
    create.set_defaults(func=cmd_create)

    ls = sub.add_parser("list", help="List all services")
    ls.set_defaults(func=cmd_list)

    # Simple single-name commands
    single = {
        "stop": cmd_stop, "start": cmd_start, "url": cmd_url,
        "remove": cmd_remove, "restart": cmd_restart,
        "enable": cmd_enable, "disable": cmd_disable, "kill": cmd_kill,
    }
    for name, fn in single.items():
        sp = sub.add_parser(name, help=f"{name.capitalize()} a service")
        sp.add_argument("name")
        sp.set_defaults(func=fn)

    status = sub.add_parser("status", help="Show status of a service")
    status.add_argument("name")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="Show logs for a service")
    logs.add_argument("name")
    logs.add_argument("--lines", type=int, default=50, help="Number of log lines")
    logs.set_defaults(func=cmd_logs)

    rename = sub.add_parser("rename", help="Rename a service")
    rename.add_argument("old", help="Current name")
    rename.add_argument("new", help="New name")
    rename.set_defaults(func=cmd_rename)

    web = sub.add_parser("web", help="Start/stop the web dashboard service")
    web.add_argument("--port", type=int, default=int(os.environ.get("WEBIFY_WEB_PORT", 8000)),
                     help="Dashboard port (default: 8000, or WEBIFY_WEB_PORT env)")
    web.add_argument("--serve", action="store_true",
                     help="Run in foreground (production server; used by systemd)")
    web.add_argument("--stop", action="store_true", help="Stop the dashboard service")
    web.add_argument("--restart", action="store_true", help="Restart the dashboard service")
    web.add_argument("--status", action="store_true", help="Show dashboard service status")
    web.set_defaults(func=cmd_web)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        build_parser().print_help()
        return 0
    try:
        args.func(args)
    except WebifyError as exc:
        _e(str(exc))
        return 1
    except KeyboardInterrupt:
        _info("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
