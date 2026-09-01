#!/usr/bin/env bash
#
# Webify systemd setup — creates a system-level service for the web dashboard.
# Run with: sudo ./setup-systemd.sh
#
set -euo pipefail

info() { printf '\033[36m>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32mOK\033[0m  %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run this with sudo."

# 1. Create webify user (no login shell, no home dir)
if ! id webify &>/dev/null; then
    info "Creating 'webify' system user..."
    useradd --system --no-create-home --shell /usr/sbin/nologin webify
    ok "User 'webify' created."
else
    ok "User 'webify' already exists."
fi

# 2. Create data directory
info "Setting up /var/lib/webify..."
mkdir -p /var/lib/webify
chown webify:webify /var/lib/webify
chmod 755 /var/lib/webify
ok "Data directory ready."

# 3. Ensure webify is installed and on PATH
if ! command -v webify &>/dev/null; then
    # Check common install locations
    for p in /usr/bin/webify /usr/local/bin/webify; do
        [[ -x "$p" ]] && break
    done
    die "webify not found on PATH. Install it first (e.g. sudo dpkg -i webify.deb)."
fi
ok "webify found at $(which webify)"

# 4. Install the systemd unit
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/webify.service" /etc/systemd/system/webify.service
systemctl daemon-reload
ok "Unit file installed."

# 5. Enable and start
systemctl enable webify.service
systemctl start webify.service
sleep 1

if systemctl is-active --quiet webify.service; then
    ok "webify.service is running."
else
    echo "WARNING: service may have failed. Check: journalctl -u webify -e"
fi

echo ""
echo "Dashboard: http://localhost:8000"
echo "Status:    systemctl status webify"
echo "Logs:      journalctl -u webify -f"
echo "Stop:      systemctl stop webify"
