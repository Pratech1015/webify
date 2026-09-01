#!/usr/bin/env bash
#
# Rebuild the APT Packages index after adding/updating a .deb.
# Run this after every release: ./repo/rebuild-index.sh
#
set -euo pipefail
cd "$(dirname "$0")"

echo "Rebuilding APT package index..."

# Generate Packages (architecture-independent)
dpkg-scanpackages pool/main /dev/null > dists/stable/main/binary-all/Packages 2>/dev/null

# Mirror to amd64 (native arch) so apt finds the package
mkdir -p dists/stable/main/binary-amd64
cp dists/stable/main/binary-all/Packages dists/stable/main/binary-amd64/Packages

# Compress both
gzip -9c dists/stable/main/binary-all/Packages > dists/stable/main/binary-all/Packages.gz
gzip -9c dists/stable/main/binary-amd64/Packages > dists/stable/main/binary-amd64/Packages.gz

echo "Index updated: $(grep '^Package:' dists/stable/main/binary-all/Packages | wc -l) package(s)"
echo "Packages size: $(du -h dists/stable/main/binary-all/Packages | cut -f1)"
