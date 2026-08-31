# Build a native Arch/EndeavourOS package from this checkout.
#
#   make            # stage the source tarball and build with makepkg
#   make install    # build and install via makepkg -si (prompts for sudo)
#   make clean      # remove build artifacts
#
# Mirrors the AUR submission (PKGBUILD + .SRCINFO) in this directory.

PKGVER := 0.1.0
TAR    := webify-$(PKGVER).tar.gz

.PHONY: all install stage clean

all: stage
	makepkg -f

install: stage
	makepkg -si

# Build a clean source tarball with the required top-level dir name using
# git archive (no Python build module needed, works offline and on any distro).
stage:
	@git -C ../.. archive --prefix=webify-$(PKGVER)/ -o "$(TAR)" HEAD
	@test -f "$(TAR)" && echo "Staged $(TAR)"

clean:
	rm -rf src pkg "$(TAR)" *.pkg.tar.zst *.tar.gz webify.egg-info dist build
