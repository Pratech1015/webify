"""Deploy runner — executed as a systemd oneshot unit (webify-<name>-deploy.service).

Runs cloning, dependency install, and building in the background so the
dashboard/CLI never block. On success it writes the site's serve unit, starts
it, and updates state. All output is captured in the deploy unit's journal.

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
    new_info = svc.start(info.get("repo"), port)
    state.save_service(name, {**info, **new_info})
    print(f"Deploy complete: {name} -> {new_info.get('url') or svc.url()}", flush=True)


def main():
    if len(sys.argv) < 2:
        print("usage: python -m webify.deploy_service <name>", file=sys.stderr)
        return 2
    name = sys.argv[1]
    try:
        deploy(name)
    except WebifyError as exc:
        print(f"Deploy failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
