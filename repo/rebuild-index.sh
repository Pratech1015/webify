#!/usr/bin/env bash
#
# Rebuild the APT Packages index after adding/updating a .deb.
# Run this after every release: ./repo/rebuild-index.sh
#
# Uses a FLAT repository layout so apt fetches exactly one Packages file and
# doesn't probe the whole dists/<suite>/main/{binary-*,Translation,Components}
# tree (which caused repeated "Ign:" fetches on raw.githubusercontent.com).
#
set -euo pipefail
cd "$(dirname "$0")"

echo "Rebuilding APT package index (flat repo)..."

# Generate Packages for all .deb files in this directory.
dpkg-scanpackages . /dev/null > Packages 2>/dev/null
gzip -9c Packages > Packages.gz

echo "Index updated: $(grep '^Package:' Packages | wc -l) package(s)"
echo "Packages size: $(du -h Packages | cut -f1)"
echo
echo "Client sources line (flat repo):"
echo "  deb [trusted=yes] $PWD ./"
