#!/usr/bin/env bash
#
# Rebuild the APT repository after adding/updating a .deb, then sign it.
# Run after every release: ./repo/rebuild-index.sh
#
# Flat repo layout (Packages at the repo root) so apt fetches one index and
# doesn't probe a dists/<suite>/main tree on raw.githubusercontent.com.
# The index is signed (InRelease/Release.gpg) so clients using [signed-by=...]
# fully trust it without any 'Ign:' probing.
#
set -euo pipefail
cd "$(dirname "$0")"

GNUPGHOME="$(pwd)/.gnupg"
export GNUPGHOME
KEYID="webify@localhost"

echo "Rebuilding APT package index (flat repo)..."
rm -f Packages Packages.gz Release InRelease Release.gpg

# 1. Index all .deb files in this directory.
dpkg-scanpackages . /dev/null > Packages 2>/dev/null
gzip -9c Packages > Packages.gz

# 2. Build the Release metadata describing the index.
#    (flat repo: webify-apt-key is not itself listed as an index)
SHA256="$(sha256sum Packages | cut -d' ' -f1)"
GZSHA256="$(sha256sum Packages.gz | cut -d' ' -f1)"
PSIZE="$(stat -c%s Packages)"
GZSIZE="$(stat -c%s Packages.gz)"
DATE="$(date -u -R)"

cat > Release <<EOF
Origin: Webify
Label: Webify
Suite: stable
Codename: stable
Date: ${DATE}
Architectures: all amd64
Components: main
Description: Webify apt repository
SHA256:
 ${SHA256} ${PSIZE} Packages
 ${GZSHA256} ${GZSIZE} Packages.gz
EOF

# 3. Sign: InRelease (clearsigned, modern apt) + Release.gpg (detached, old apt).
gpg --batch --yes --clearsign -o InRelease Release
gpg --batch --yes --detach-sign -o Release.gpg Release

nfuncs="$(grep -c '^Package:' Packages || true)"
echo "Index updated: ${nfuncs} package(s)"
echo "Signed: InRelease, Release.gpg"
echo "Packages size: $(du -h Packages | cut -f1)"
