# Webify

**A self-hosted Netlify alternative for Linux.**

Webify deploys sites straight from a Git repository and serves them on your own
machine — no cloud, no vendor lock-in. Each deployed site runs as its own
**systemd user service**, so it gets automatic restart, PID tracking, logging,
and start-on-login out of the box.

```
$ webify create catalyst
Creating service 'catalyst'
Enter Repo link: https://github.com/acme/catalyst.git
Enter Repo link again: https://github.com/acme/catalyst.git
Available modes:
  🌐 local       localhost only
  ☁️  cloudflared public URL via Cloudflare (requires cloudflared)
  🚀 nginx       behind nginx reverse proxy (requires nginx + root)
Select mode [local] local
Use custom Port (Y/N) [N]
Deploying https://github.com/acme/catalyst.git → catalyst (port 7070) in the background …
✓ Service catalyst registered on port 7070. Deploy running in background.
  Watch progress with: webify logs catalyst
  Or check status with:    webify status catalyst
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

Webify ships native packages for the major Linux families plus a universal
installer that auto-detects your distro.

**One-line installer (auto-detects your distro):**

```bash
curl -fsSL https://github.com/Pratech1015/webify/raw/main/install.sh | bash
```

**Arch / EndeavourOS / Manjaro** — official AUR-style package (built & verified):

```bash
cd packaging/aur && make install      # builds a webify-*.pkg.tar.zst with makepkg
# or via an AUR helper once published:  yay -S webify
```

**Debian / Ubuntu** — native `.deb` (or the signed APT repo):

```bash
# Option A — build it locally
make package-deb                      # requires dpkg-buildpackage, debhelper
sudo dpkg -i packaging/dist/webify_*.deb

# Option B — use the official signed APT repo (simplest, auto-upgrades)
curl -fsSL https://raw.githubusercontent.com/Pratech1015/webify/main/setup-apt.sh | sudo bash
```

**Fedora / RHEL / Rocky** — native `.rpm`:

```bash
make package-rpm                      # requires rpmbuild
sudo dnf install packaging/rhel/webify-*.rpm
```

**Any other Linux** — generic venv install:

```bash
make install                          # python -m venv + pip install
sudo ln -sf "$PWD/.venv/bin/webify" /usr/local/bin/webify
```

> Requires an active **systemd user session** (a normal desktop login) and
> `python3` + `git`. Every Webify service runs as its own systemd user unit.

### Optional mode dependencies

| Mode         | Binary needed    | Arch                | Debian/Ubuntu   | Fedora        |
|--------------|------------------|---------------------|-----------------|---------------|
| `local`      | git              | (preinstalled)      | (preinstalled)  | (preinstalled)|
| `cloudflared`| `cloudflared`    | `pacman -S cloudflared` | `apt install cloudflared` | `dnf install cloudflared` |
| `nginx`      | `nginx` + root   | `pacman -S nginx`   | `apt install nginx` | `dnf install nginx` |

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

## Netlify Functions (serverless at home)

Netlify sites often ship a `netlify/functions/` directory — serverless handlers
served at `/.netlify/functions/<name>`. Webify **understands and runs them**, so
a self-hosted app keeps working even if its frontend calls `/.netlify/functions/*`
on the same origin.

When Webify finds a `netlify/functions` directory (or the `build.functions` path
from `netlify.toml`), it launches a **Netlify-compatible gateway** in front of
the app:

```
Browser ──> webify-<name>-functions.service (gateway, PUBLIC port)
                 ├─ /.netlify/functions/*   -> invokes the real handler
                 └─ everything else         -> proxied to the app (internal port)
```

* Handlers use the standard Netlify / Lambda signature
  `exports.handler = async (event, context) => ({ statusCode, headers, body })`.
  CommonJS (`.js`/`.cjs`) and ESM (`.mjs`) are both supported.
* The frontend's `/.netlify/functions/api?action=whatever` calls hit the gateway
  on the **same URL as the site** — no frontend changes needed.
* The gateway runs as `webify-<name>-functions.service`, requires Node.js, and
  reverse-proxies all non-function traffic to the real app on an internal port.
* `webify status <name>` and the dashboard list the detected functions.

```bash
webify create prismtv --repo https://github.com/you/prismtv.git --mode cloudflared
# / .netlify/functions/api now resolves on the deployed site
```

```bash
webify create <name> [--repo URL] [--port N] [--mode MODE]  # register + trigger a deploy
webify list                                                  # show all services
webify status <name>                                         # status, PID, URL, unit
webify url <name>                                            # print just the URL
webify start <name>                                          # trigger a (re)deploy in background
webify stop <name> / kill <name>                             # stop / stop+disable the units
webify restart <name>                                        # trigger a redeploy
webify enable <name> / disable <name>                        # auto-start on login
webify logs <name> [--lines N]                               # show deploy + runtime logs
webify remove <name>                                         # stop, disable, and delete
webify rename <old> <new>                                    # rename a service
webify web                                                   # start/stop the dashboard (toggle)
webify web --status                                          # show dashboard service status
webify web --stop / --restart                                # stop / restart the dashboard
webify web --port 9000                                       # change port, then start
webify web --serve [--port N]                                # run in foreground (production)
webify --version
```

> **Deploys run as services.** Cloning, dependency install, and building happen
> inside a dedicated systemd unit `webify-<name>-deploy.service`, *not* inside
> the web request or CLI process. So you can reload the dashboard or re-run
> `webify start` as many times as you like — it never blocks or half-starts.
> The build output is captured in that unit's journal and shown in the
> dashboard **Deploys** tab (auto-refreshing) or via `webify logs <name>`.
> A failed deploy leaves the site registered so you can simply fix the issue
> and click **Deploy** again.

### Web dashboard

The dashboard is its own **systemd user service** (`webify-web.service`) and runs
a production WSGI server (waitress) — not Flask's dev server:

```bash
webify web               # toggle: start if stopped, stop if running
webify web --status      # stopped | running (enabled)
webify web --stop        # stop the dashboard
webify web --restart     # restart it
```

Open http://localhost:8000 to manage all your sites from the browser. The
dashboard and the CLI share the same state and systemd units, so changes in one
are reflected in the other.

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
│  ├─ web.py                 # Flask dashboard app (routes + views)
│  ├─ web_service.py         # waitress production entry point for the dashboard unit
│  └─ services/
│     └─ __init__.py         # Local / Cloudflared / Nginx services
├─ packaging/
│  ├─ aur/PKGBUILD           # Arch/EndeavourOS (AUR) package
│  ├─ debian/                # Debian/Ubuntu .deb packaging
│  └─ rhel/webify.spec       # Fedora/RHEL .rpm packaging
├─ install.sh                # auto-detecting one-line installer
├─ Makefile                  # platform/target/build helpers
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
