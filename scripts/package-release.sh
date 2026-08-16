#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
./scripts/validate.sh
python3 -m unittest discover -s tests -v
PLUGIN="$(./scripts/package-plugin.sh)"
COMPANION="$(./scripts/package-companion.sh)"
VERSION="$(tr -d '\r\n' < VERSION)"
BUNDLE="dist/trek-guest-portal-${VERSION}-complete-bundle.zip"
rm -f "$BUNDLE"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp "$PLUGIN" "$COMPANION" "$TMP/"
cp README.md SECURITY.md LICENSE CHANGELOG.md "$TMP/"
cp docs/INSTALL.md docs/PREREQUISITES.md docs/CONFIGURATION.md docs/FLIGHTS.md docs/LOGGING.md docs/TROUBLESHOOTING.md "$TMP/"
(
  cd "$TMP"
  zip -qr "$OLDPWD/$BUNDLE" .
)
sha256sum "$PLUGIN" "$COMPANION" "$BUNDLE" > dist/SHA256SUMS
cat dist/SHA256SUMS
echo "Release artifacts written to dist/"
