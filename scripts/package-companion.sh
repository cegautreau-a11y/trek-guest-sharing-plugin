#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
VERSION="$(tr -d '\r\n' < VERSION)"
mkdir -p dist
OUT="dist/trek-guest-portal-companion-${VERSION}-portainer.zip"
rm -f "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/public" "$TMP/server" "$TMP/tools" "$TMP/docs"
cp companion/public/app.js companion/public/index.html companion/public/style.css companion/public/config.js.example "$TMP/public/"
cp companion/server/server.py "$TMP/server/"
cp companion/tools/*.py companion/tools/README.md "$TMP/tools/"
cp companion/docker-compose.yml companion/README.md SECURITY.md "$TMP/"
cp docs/INSTALL.md docs/CONFIGURATION.md docs/FLIGHTS.md docs/LOGGING.md docs/TROUBLESHOOTING.md "$TMP/docs/"
(
  cd "$TMP"
  zip -qr "$OLDPWD/$OUT" .
)
echo "$OUT"
