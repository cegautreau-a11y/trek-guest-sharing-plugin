#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
VERSION="$(tr -d '\r\n' < VERSION)"
mkdir -p dist
OUT="dist/trek-guest-portal-${VERSION}.zip"
rm -f "$OUT"
(
  cd plugin
  zip -qr "../$OUT" client server trek-plugin.json package.json README.md LICENSE
)
echo "$OUT"
