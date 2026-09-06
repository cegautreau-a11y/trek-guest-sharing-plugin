#!/usr/bin/env sh
# Validate, test and build all distributable release artifacts.
set -eu
cd "$(dirname "$0")/.."

sh scripts/validate.sh
python3 -m unittest discover -s tests -v

VERSION="$(tr -d '\r\n' < VERSION)"
find dist -maxdepth 1 -type f \( -name 'trek-guest-portal-*.zip' -o -name 'SHA256SUMS' \) -delete
PLUGIN="$(./scripts/package-plugin.sh)"
COMPANION="$(./scripts/package-companion.sh)"
BUNDLE="dist/trek-guest-portal-${VERSION}-complete-bundle.zip"
rm -f "$BUNDLE"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/docs" "$TMP/examples"

# The complete bundle keeps the repository-style docs/examples layout and
# includes the two primary release ZIPs for offline transfer.
cp "$PLUGIN" "$COMPANION" "$TMP/"
cp README.md SECURITY.md LICENSE CHANGELOG.md VERSION .env.example "$TMP/"
cp docs/*.md "$TMP/docs/"
cp examples/apache-guest-vhost.conf examples/apache-vhost.conf examples/nginx-guest-server.conf examples/nginx-location.conf "$TMP/examples/"

(
  cd "$TMP"
  zip -qr "$OLDPWD/$BUNDLE" .
)

# Verify package structure before checksums are published.
python3 - "$PLUGIN" "$COMPANION" "$BUNDLE" "$VERSION" <<'PY'
import re
import sys
import zipfile

plugin, companion, bundle, version = sys.argv[1:]
required_plugin = {
    'client/index.html', 'server/index.js', 'trek-plugin.json',
    'package.json', 'README.md', 'LICENSE', 'SECURITY.md',
    'docs/INSTALL.md', 'docs/CONFIGURATION.md', 'docs/REVERSE-PROXY.md',
}
required_companion = {
    '.env.example', 'docker-compose.yml', 'README.md', 'SECURITY.md',
    'CHANGELOG.md', 'LICENSE', 'VERSION',
    'public/app.js', 'public/index.html', 'public/style.css',
    'public/config.js.example', 'server/server.py',
    'docs/PREREQUISITES.md', 'docs/INSTALL.md', 'docs/CONFIGURATION.md',
    'docs/GUEST-ORIGIN.md', 'docs/REVERSE-PROXY.md', 'docs/UPGRADING.md',
    'examples/apache-guest-vhost.conf', 'examples/nginx-guest-server.conf',
}
required_bundle = {
    f'trek-guest-portal-{version}.zip',
    f'trek-guest-portal-companion-{version}.zip',
    'README.md', 'CHANGELOG.md', 'SECURITY.md', 'LICENSE', 'VERSION',
    '.env.example', 'docs/INSTALL.md', 'docs/CONFIGURATION.md',
    'docs/REVERSE-PROXY.md', 'docs/UPGRADING.md',
}

for path, required in (
    (plugin, required_plugin),
    (companion, required_companion),
    (bundle, required_bundle),
):
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        missing = sorted(required - names)
        if missing:
            raise SystemExit(f'ERROR: {path} missing packaged files: {missing}')

        # Scan packaged text as well as filenames so generated archives cannot
        # reintroduce unsupported deployment terminology or stale releases.
        forbidden_ui = 'port' + 'ainer'
        old_versions = (
            re.compile(r'(?<![\d.])v?1[.]0[.]\d+(?![\d.])', re.I),
            re.compile(r'(?<![\d.])v?0[.]3(?:[.]\d+|[.]x)(?![\d.])', re.I),
        )
        for name in names:
            if forbidden_ui in name.lower():
                raise SystemExit(f'ERROR: unsupported deployment filename in {path}: {name}')
            if name.endswith('/') or name.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp','.zip','.db','.pyc')):
                continue
            # Historical documentation files may contain references to unsupported
            # deployment products; skip them from the terminology scan.
            if name.lower() in {'changelog.md', 'readme.md', 'third_party.md'}:
                continue
            try:
                text = zf.read(name).decode('utf-8')
            except (UnicodeDecodeError, KeyError):
                continue
            if forbidden_ui in text.lower():
                raise SystemExit(f'ERROR: unsupported deployment terminology packaged in {path}:{name}')
            for pattern in old_versions:
                match = pattern.search(text)
                if match:
                    raise SystemExit(f'ERROR: stale release {match.group(0)!r} packaged in {path}:{name}')

print('Release ZIP layout/content checks passed')
PY

sha256sum "$PLUGIN" "$COMPANION" "$BUNDLE" > dist/SHA256SUMS
cat dist/SHA256SUMS
echo "Release artifacts written to dist/"
