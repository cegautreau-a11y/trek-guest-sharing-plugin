#!/usr/bin/env sh
# Build the Admin-uploadable TREK plugin ZIP without deployment-local files.
set -eu
cd "$(dirname "$0")/.."

VERSION="$(tr -d '\r\n' < VERSION)"
mkdir -p dist
OUT="dist/trek-guest-portal-${VERSION}.zip"
find dist -maxdepth 1 -type f -name 'trek-guest-portal-[0-9]*.zip' ! -name "trek-guest-portal-${VERSION}.zip" -delete
rm -f "$OUT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/client" "$TMP/server" "$TMP/docs"

# Copy the plugin runtime/metadata plus documentation that remains useful when
# the release ZIP is inspected independently from the GitHub source tree.
cp plugin/client/index.html "$TMP/client/"
cp plugin/server/index.js "$TMP/server/"
cp plugin/trek-plugin.json plugin/package.json plugin/README.md plugin/LICENSE "$TMP/"
cp SECURITY.md "$TMP/SECURITY.md"
cp docs/*.md "$TMP/docs/"

# plugin/README.md is nested one directory below docs/ in source but moves to
# the ZIP root, so make its installation link package-relative.
sed -i '' 's#](../docs/#](docs/#g' "$TMP/README.md"

(
  cd "$TMP"
  zip -qr "$OLDPWD/$OUT" .
)

echo "$OUT"
