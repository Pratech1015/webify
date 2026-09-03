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
    except Exception as exc:
        # Build failed: write a safe, disabled placeholder unit so 'webify start'
        # doesn't throw "unit not found", then record the failure.
        try:
            svc.start_no_build(info.get("repo"), port)
        except Exception:
            pass

        state.save_service(name, {
            **info,
            "deploy_status": "failed",
            "deploy_error": str(exc),
            "url": svc.url(),
        })
        raise

    # Cloudflared tunnel setup — only runs after a successful build.
    if svc.mode == "cloudflared":
        domain = info.get("domain", "")
        tunnel_name = name
        try:
            print("  Setting up cloudflared tunnel...", flush=True)

            # Check cloudflared is logged in
            if not daemon.is_cloudflared_ready():
                raise WebifyError(
                    "Cloudflared is not logged in. Run 'cloudflared tunnel login' first,\n"
                    "or change the site mode to 'local'."
                )

            # Create tunnel if it doesn't exist
            if not daemon.cloudflared_tunnel_exists(tunnel_name):
                print(f"  Creating tunnel '{tunnel_name}'...", flush=True)
                tunnel_id = daemon.cloudflared_tunnel_create(tunnel_name)
                print(f"  Tunnel created (ID: {tunnel_id})", flush=True)

                # Write config.yml
                daemon.cloudflared_write_config(tunnel_name, tunnel_id)
                print("  Wrote tunnel config", flush=True)
            else:
                print(f"  Tunnel '{tunnel_name}' already exists", flush=True)

            # Route DNS if domain is provided
            if domain:
                print(f"  Routing DNS: {domain} -> {tunnel_name}...", flush=True)
                daemon.cloudflared_route_dns(tunnel_name, domain)
                # Update config.yml with the correct hostname
                daemon.cloudflared_update_config_ingress(domain, port)
                print(f"  DNS routed: {domain}", flush=True)

            # Write and start the tunnel systemd unit
            daemon.write_tunnel_unit(name, tunnel_name)
            daemon.daemon_reload()
            daemon.start_tunnel_unit(name)
            print("  Tunnel started", flush=True)

        except WebifyError as exc:
            # Tunnel setup failed — the site is still running locally,
            # but we record the tunnel error so the user can see it.
            print(f"  WARNING: Tunnel setup failed: {exc}", flush=True)
            state.save_service(name, {
                **info, **new_info,
                "deploy_status": "ok",
                "deploy_error": f"Site is running locally. Tunnel setup failed: {exc}",
                "url": svc.url(),
            })
            print(f"Deploy complete (local only): {name} -> {svc.url()}", flush=True)
            return

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
