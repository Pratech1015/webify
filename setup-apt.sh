#!/usr/bin/env bash
#
# Webify APT repository setup for Ubuntu/Debian.
# Adds the Webify repo so you can install/update with apt.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Pratech1015/webify/main/setup-apt.sh | sudo bash
#
set -euo pipefail

info() { printf '\033[36m>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32mOK\033[0m  %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run this with sudo."

REPO_URL="https://raw.githubusercontent.com/Pratech1015/webify/main"
SOURCES_FILE="/etc/apt/sources.list.d/webify.list"
KEYRING="/usr/share/keyrings/webify-apt-keyring.gpg"

# Install the public signing key so apt can verify the repo.
info "Installing Webify APT signing key..."
curl -fsSL "${REPO_URL}/repo/webify-apt-key.asc" -o /tmp/webify-apt-key.asc
gpg --dearmor --yes -o "$KEYRING" /tmp/webify-apt-key.asc
rm -f /tmp/webify-apt-key.asc
chmod 644 "$KEYRING"
ok "Key installed to ${KEYRING}"

# Write the source list
info "Adding Webify APT repository..."
cat > "$SOURCES_FILE" <<SOURCES
# Webify — self-hosted Netlify alternative
# Signed flat repo: fetches exactly one verified Packages index.
deb [signed-by=${KEYRING}] ${REPO_URL}/repo ./
SOURCES
ok "Repository added to ${SOURCES_FILE}"

# Update package lists
info "Updating package lists..."
apt-get update -qq 2>/dev/null
ok "Package lists updated."

# Install or upgrade
if dpkg -l webify 2>/dev/null | grep -q "^ii"; then
    info "Webify is already installed. Upgrading..."
    apt-get install --only-upgrade -y webify
else
    info "Installing webify..."
    apt-get install -y webify
fi

ok "Done. Webify is ready."
echo ""
echo "  webify --version          # check installed version"
echo "  webify create mysite ...  # deploy a site"
echo "  webify web                # start dashboard"
echo ""
echo "To upgrade later:  sudo apt update && sudo apt upgrade webify"
