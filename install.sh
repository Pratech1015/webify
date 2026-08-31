#!/usr/bin/env bash
#
# Webify installer — auto-detects your Linux distribution and installs via the
# most appropriate native method. Works on Arch/EndeavourOS, Debian/Ubuntu,
# Fedora/RHEL, and falls back to a venv install elsewhere.
#
# Usage:
#   curl -fsSL https://github.com/webify/webify/raw/main/install.sh | bash
#   # or from a checkout:
#   ./install.sh

set -euo pipefail

cd "$(dirname "$0")"

info() { printf '\033[36m>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32mOK\033[0m  %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

# --- detect distro -----------------------------------------------------------
distro_id=""
distro_like=""
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    distro_id="${ID:-}"
    distro_like="${ID_LIKE:-}"
fi

# --- universal venv fallback ---------------------------------------------------
venv_install() {
    local venv="${1:-.webify-venv}"
    info "No native package path detected; installing into a local venv ($venv)."
    has python3 || die "python3 is required."
    has git || die "git is required."
    python3 -m venv "$venv"
    "$venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
    "$venv/bin/pip" install .
    ok "Installed into $venv."
    info "Add it to your PATH, e.g.:  export PATH=\"$PWD/$venv/bin:\$PATH\""
    info "Or symlink:                   sudo ln -sf \"$PWD/$venv/bin/webify\" /usr/local/bin/webify"
    "$venv/bin/webify" --version
}

# --- per-family installers ------------------------------------------------------
arch_install() {
    if has makepkg && [[ -d packaging/aur ]]; then
        info "Building and installing an official Arch/AUR package..."
        ( cd packaging/aur && make install )
        return
    fi
    die "makepkg not found. Use an AUR helper (yay -S webify) or run: make install"
}

deb_install() {
    info "Installing for Debian/Ubuntu."
    if has dpkg-buildpackage && [[ -d packaging/debian ]]; then
        make package-deb
        sudo dpkg -i packaging/dist/webify_*.deb || sudo apt-get -f install -y
        return
    fi
    info "dpkg-buildpackage not available; falling back to a venv install."
    venv_install "/usr/local/lib/webify-venv"
}

rpm_install() {
    info "Installing for Fedora/RHEL."
    info "Run 'make package-rpm' and install the produced .rpm with 'sudo dnf install'."
    info "Falling back to a venv install for now."
    venv_install "/usr/local/lib/webify-venv"
}

# --- main -----------------------------------------------------------------------
info "Detected distribution: ${distro_id:-unknown} (ID_LIKE: ${distro_like:-none})"

case "$distro_id" in
    arch|artix|manjaro)            arch_install ;;
    debian|ubuntu|linuxmint|pop)   deb_install ;;
    fedora|rhel|centos|rocky|almalinux) rpm_install ;;
    *)
        case "$distro_like" in
            *arch*)   arch_install ;;
            *debian*) deb_install ;;
            *rhel*|*fedora*) rpm_install ;;
            *)        venv_install ;;
        esac
        ;;
esac

ok "Webify install complete."
