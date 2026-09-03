# Webify — build, install, and package for multiple Linux distributions.
#
# Targets:
#   make install         Install into a Python venv + symlink (any distro)
#   make uninstall       Remove the venv install
#   make package-arch    Build an AUR-style package (Arch/EndeavourOS)
#   make package-deb    Build a .deb (Debian/Ubuntu)
#   make package-rpm    Build an .rpm (Fedora/RHEL)
#   make package-all    Build everything possible on this machine
#   make test            Import-check the package
#
# The resulting scripts/builds work across Arch, Ubuntu/Debian, Fedora/RHEL,
# and any other Linux with Python 3.8+ and a systemd user session.

PYTHON   ?= python3
PREFIX   ?= /usr/local
BINDIR   ?= $(PREFIX)/bin
VENV     ?= .venv

.PHONY: install uninstall test clean package-all \
	package-arch package-deb package-rpm

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install .
	@echo
	@echo "Installed. Add the venv to your PATH or symlink it:"
	@echo "  sudo ln -sf $(CURDIR)/$(VENV)/bin/webify $(BINDIR)/webify"

uninstall:
	rm -rf $(VENV)
	rm -f $(BINDIR)/webify
	@echo "Removed. Note: deployed services remain; stop them with 'webify kill <name>'."

test:
	$(PYTHON) -m compileall -q webify
	$(VENV)/bin/python -c "import webify, webify.cli, webify.daemon, webify.services" 2>/dev/null || \
		$(PYTHON) -c "import webify, webify.cli, webify.daemon, webify.services"

clean:
	rm -rf build dist *.egg-info webify/__pycache__ webify/services/__pycache__ \
		packaging/dist .venv

# -- Automatically choose native package builder for this distro -------------
package-all:
	@echo "Detected distro: $$(grep -E '^(ID=|ID_LIKE=)' /etc/os-release | tr '\n' ' ')"
	@if command -v makepkg >/dev/null 2>&1; then \
		echo "==> Building Arch/AUR package"; $(MAKE) -C packaging/aur package; \
	elif command -v dpkg-buildpackage >/dev/null 2>&1 || command -v dpkg-deb >/dev/null 2>&1; then \
		echo "==> Building Debian/Ubuntu package"; $(MAKE) package-deb; \
	elif command -v rpmbuild >/dev/null 2>&1; then \
		echo "==> Building Fedora/RHEL package"; $(MAKE) package-rpm; \
	else \
		echo "No system packager found; doing a venv install instead."; $(MAKE) install; \
	fi

# -- Debian/Ubuntu (.deb) -----------------------------------------------------
DEB_BUILD_DIR = packaging/dist
VERSION := $(shell $(PYTHON) -c "from webify import __version__; print(__version__)")
DIST_DIR := $(DEB_BUILD_DIR)/webify-$(VERSION)

deb-stage: clean
	$(PYTHON) -m build --sdist --outdir $(DEB_BUILD_DIR)
	rm -rf $(DIST_DIR)
	tar -xzf $(DEB_BUILD_DIR)/webify-$(VERSION).tar.gz -C $(DEB_BUILD_DIR)
	cp -r packaging/debian $(DIST_DIR)/debian
	@# Keep the debian changelog version in sync with the source version,
	@# otherwise the .deb is versioned with the stale changelog version.
	printf 'webify (%s-1) unstable; urgency=medium\n\n  * Release %s.\n\n -- Webify Contributors <webify@localhost>  %s\n' \
		"$(VERSION)" "$(VERSION)" "$$(date -u -R)" > $(DIST_DIR)/debian/changelog

package-deb: deb-stage
	@echo "==> Building .deb (needs dpkg-buildpackage)"
	cd $(DIST_DIR) && dpkg-buildpackage -us -uc -b
	@echo "==> .deb written to: $(CURDIR)/$(DEB_BUILD_DIR)/"

# -- Fedora/RHEL (.rpm) -------------------------------------------------------
package-rpm: clean
	$(PYTHON) -m build --sdist --outdir packaging/rhel
	cp packaging/rhel/webify.spec packaging/rhel/
	@echo "==> Building .rpm (needs rpmbuild)"
	@echo "On Fedora/RHEL, copy packaging/rhel/webify.spec and the sdist into"
	@echo "~/rpmbuild/SOURCES then run: rpmbuild -bb packaging/rhel/webify.spec"
