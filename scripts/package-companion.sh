#!/usr/bin/env sh
# Build the standalone Docker Compose companion release archive.
set -eu
cd "$(dirname "$0")/.."

VERSION="$(tr -d '\r\n' < VERSION)"
mkdir -p dist
OUT="dist/trek-guest-portal-companion-${VERSION}.zip"
rm -f "$OUT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/public" "$TMP/server" "$TMP/tools" "$TMP/docs" "$TMP/examples"

# Runtime application files.
cp companion/public/app.js companion/public/index.html companion/public/style.css companion/public/config.js.example "$TMP/public/"
cp companion/server/server.py "$TMP/server/"
cp companion/tools/*.py companion/tools/README.md "$TMP/tools/"

# Standalone Compose deployment and project documentation.
cp companion/docker-compose.yml "$TMP/docker-compose.yml"
cp companion/README.md "$TMP/README.md"
cp .env.example SECURITY.md CHANGELOG.md LICENSE VERSION "$TMP/"
cp docs/*.md "$TMP/docs/"
cp examples/apache-guest-vhost.conf examples/apache-vhost.conf examples/nginx-guest-server.conf examples/nginx-location.conf "$TMP/examples/"

# companion/README.md is one directory below docs/ in source but moves to the
# ZIP root, so rewrite only its source-tree-relative documentation links.
sed -i 's#](../docs/#](docs/#g; s#`../docs/#`docs/#g' "$TMP/README.md"

(
  cd "$TMP"
  zip -qr "$OLDPWD/$OUT" .
)

echo "$OUT"
