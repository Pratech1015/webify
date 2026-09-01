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

from . import state
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
    except Exception as exc:
        # Any failure (WebifyError, CalledProcessError, ...) -> record a clean
        # "failed" state so the dashboard shows the error and the service stays
        # DISABLED (nothing running) for the user to fix and redeploy.
        state.save_service(name, {
            **info,
            "deploy_status": "failed",
            "deploy_error": str(exc),
            "url": svc.url(),
        })
        if isinstance(exc, WebifyError):
            raise
        raise WebifyError(str(exc)) from exc

    state.save_service(name, {**info, **new_info, "deploy_status": "ok", "deploy_error": ""})
    print(f"Deploy complete: {name} -> {new_info.get('url') or svc.url()}", flush=True)


def main():
    if len(sys.argv) < 2:
        print("usage: python -m webify.deploy_service <name>", file=sys.stderr)
        return 2
    name = sys.argv[1]
    try:
        deploy(name)
    except WebifyError as exc:
        # Print the error prominently so it's easy to read/share, then exit non-zero.
        print("", file=sys.stderr)
        print("──────────────────────────────────────────────", file=sys.stderr)
        print(f"DEPLOY FAILED for '{name}'", file=sys.stderr)
        print("──────────────────────────────────────────────", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        print("The service is kept registered but DISABLED. Fix the repo, then", file=sys.stderr)
        print(f"run 'webify start {name}' or click Deploy in the dashboard.", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
