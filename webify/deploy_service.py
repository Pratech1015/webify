"""Deploy runner — executed as a systemd oneshot unit (webify-<name>-deploy.service).

Runs cloning, dependency install, and building in the background so the
dashboard/CLI never block. On success it writes the site's serve unit, starts
it, and records `deploy_status: ok` in state. On failure it stops cleanly,
records `deploy_status: failed` + `deploy_error` in state (so the dashboard can
show the error and let the user fix + redeploy), and prints the error to its
journal. A failed deploy leaves the service DISABLED (nothing running) — never
half-started.

Running: python -m webify.deploy_service <name>
"""

import sys
import traceback

from . import daemon, state
from .core import WebifyError
from .services import build_service


def deploy(name: str):
    info = state.get_service(name)
    if not info:
        raise WebifyError(f"No service named '{name}'. Run 'webify create {name}' first.")

    port = info.get("port")
    if not port:
        from .core import DEFAULT_PORT, find_free_port
        port = find_free_port(DEFAULT_PORT)

    svc = build_service(name, info.get("mode", "local"))
    print(f"Deploying {name} (mode={svc.mode}) ...", flush=True)
    try:
        new_info = svc.start(info.get("repo"), port)
        if svc.mode == "cloudflared":
            print("  Starting cloudflared tunnel...", flush=True)
            daemon.write_tunnel_unit(name, name)
            daemon.daemon_reload()
            daemon.start_tunnel_unit(name)
    except Exception as exc:
        # Build failed: write a safe, disabled placeholder unit so 'webify start'
        # doesn't throw "unit not found", then record the failure.
        try:
            svc.start_no_build(info.get("repo"), port)
        except Exception:
            pass # If start_no_build fails (e.g. clone failed), we have no unit.

        state.save_service(name, {
            **info,
            "deploy_status": "failed",
            "deploy_error": str(exc),
            "url": svc.url(),
        })
        raise

    state.save_service(name, {**info, **new_info, "deploy_status": "ok", "deploy_error": ""})
    print(f"Deploy complete: {name} -> {new_info.get('url') or svc.url()}", flush=True)


def main():
    if len(sys.argv) < 2:
        print("usage: python -m webify.deploy_service <name>", file=sys.stderr)
        return 2
    name = sys.argv[1]
    try:
        deploy(name)
    except Exception as exc:
        # Print the error prominently so it's easy to read/share.
        print("", file=sys.stderr)
        print("──────────────────────────────────────────────", file=sys.stderr)
        print(f"DEPLOY FAILED for '{name}'", file=sys.stderr)
        print("──────────────────────────────────────────────", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        print("The service is kept registered but DISABLED. Fix the repo, then", file=sys.stderr)
        print(f"run 'webify start {name}' or click Deploy in the dashboard.", file=sys.stderr)
        # Exit 0 so systemd marks the unit as 'success', avoiding the 'failed' trap.
        # The actual error is recorded in state and printed to the journal above.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
