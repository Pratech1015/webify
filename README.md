# Webify

**A self-hosted Netlify alternative for Linux.**

Webify deploys static sites straight from a Git repository and serves them
behind a `python http.server` on your own machine — no cloud, no vendor lock-in.
Each deployed site runs as its own **systemd user service**, so it gets automatic
restart, PID tracking, logging, and start-on-login out of the box.

```
$ webify create catalyst
Enter Repo link: https://github.com/repo.git
Do you want to use custom Port (Y/N) N
✓ Service catalyst started on PID 9999 at port 7070
   URL: http://localhost:7070
```

## Quick start (non-interactive)

Everything can be supplied as arguments, so it works in scripts too:

```bash
# Git repo + name, port + mode are all optional flags
webify create my-site --repo https://github.com/you/site.git
webify create api --repo https://github.com/you/site.git --port 9000 --mode local
webify create blog --repo https://github.com/you/blog.git --mode cloudflared
```

If you pass no flags, Webify asks you interactively.

## Installation

```bash
pip install .            # system-wide
# or
pip install --user .     # per-user
```

Run `webify --version` to confirm.

### Optional mode dependencies

| Mode         | Binary needed    | Install (Arch)              |
|--------------|------------------|-----------------------------|
| `local`      | git              | (preinstalled)              |
| `cloudflared`| `cloudflared`    | `pacman -S cloudflared`     |
| `nginx`      | `nginx` + root   | `pacman -S nginx`           |

## Modes

Webify ships with three hosting modes.

### 1. `local` (default) — localhost hosting
The cloned site is served by `python -m http.server` bound to `0.0.0.0`.
Reachable at `http://localhost:<port>` (and from your LAN via your machine's IP).

### 2. `cloudflared` — public URL via Cloudflare
Runs the same local server **and** launches a Cloudflare quick tunnel, giving you
a public `https://<random>.trycloudflare.com` URL instantly — no DNS or account
needed. Requires the `cloudflared` binary.

### 3. `nginx` — behind an nginx reverse proxy
Runs the local server **and** writes + reloads an nginx vhost that reverse-proxies
port 80 → your service. Requires `nginx` and root access (run with `sudo`).
Served at `http://webify-<name>.localhost`.

> `nginx` mode needs root to write `/etc/nginx/conf.d`. Run `sudo webify create ...`.

## Commands

```bash
webify create <name> [--repo URL] [--port N] [--mode MODE]  # create + start
webify list                                                  # show all services
webify status <name>                                         # status, PID, URL, unit
webify url <name>                                            # print just the URL
webify start <name> / stop <name>                            # start / stop a service
webify kill <name>                                           # stop + disable the units
webify restart <name>                                        # restart the units
webify enable <name> / disable <name>                        # auto-start on login
webify logs <name> [--lines N]                               # tail service (journal) logs
webify remove <name>                                         # stop, disable, and delete
webify --version
```

## How it works

- Each Webify service maps to a **systemd user unit** `webify-<name>.service`
  under `~/.config/systemd/user/`, so it is a first-class systemd service:
  `systemctl --user status webify-<name>.service` shows its full state.
- Cloudflared mode adds a companion unit `webify-<name>-tunnel.service`.
- Service payloads and clones live under `~/.webify/services/<name>/`
  (override with the `WEBIFY_HOME` env var).
- The repo is shallow-cloned into `services/<name>/repo`.
- Webify auto-detects the served root: `index.html` in the repo root, then
  `dist`, `public`, `build`, `static`, `site`, `out`, `docs` in that order.
- A free port is chosen starting at `7070`, incrementing by one until one opens.
- systemd handles daemonization, restart-on-crash, and can enable start-on-login.

## State & logs

- State (repo, mode, port, unit) → `~/.webify/state.json`
- Live logs → `journalctl --user -u webify-<name>.service` (also via `webify logs`)
- Project dirs → `~/.webify/services/<name>/`

## Project layout

```
webify/
├─ webify/
│  ├─ cli.py                 # argparse CLI + interactive prompts
│  ├─ config.py              # paths, defaults, constants
│  ├─ core.py                # git, port/path detection, systemd detection
│  ├─ daemon.py              # systemd user-unit writer + systemctl wrapper
│  ├─ state.py               # persistent JSON state
│  └─ services/
│     └─ __init__.py         # Local / Cloudflared / Nginx services
├─ pyproject.toml
├─ README.md
└─ LICENSE
```

## Development

```bash
pip install -e .
webify create demo --repo https://github.com/example/static-site.git
```

## Contributing

PRs and issues welcome. Please follow the existing style, keep it dependency-free
(stdlib only), and document any behavior changes in the README.

## License

[MIT](LICENSE)
