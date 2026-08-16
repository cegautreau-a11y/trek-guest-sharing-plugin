#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."

VERSION="$(tr -d '\r\n' < VERSION)"
echo "Validating TREK Guest Portal ${VERSION}"

python3 -m py_compile companion/server/server.py companion/tools/*.py
node --check plugin/server/index.js
node --check companion/public/app.js
python3 - <<'PY'
import json, pathlib, re
root=pathlib.Path('.')
ver=(root/'VERSION').read_text().strip()
manifest=json.loads((root/'plugin/trek-plugin.json').read_text())
package=json.loads((root/'plugin/package.json').read_text())
server=(root/'companion/server/server.py').read_text()
index=(root/'companion/public/index.html').read_text()
assert manifest['version']==ver, (manifest['version'],ver)
assert package['version']==ver, (package['version'],ver)
assert f'VERSION = "{ver}"' in server
assert all(f'?v={ver}' in line for line in index.splitlines() if 'style.css?v=' in line or 'config.js?v=' in line or 'app.js?v=' in line)
deployment_marker = 'wherearemy' + 'packetsgoing'
assert deployment_marker not in '\n'.join(p.read_text(errors='ignore') for p in root.rglob('*') if p.is_file() and '.git' not in p.parts and p.stat().st_size < 2_000_000)
print('Version and deployment-specific string checks passed')
PY

# No deployment-local real config file should be tracked in source.
if [ -e companion/public/config.js ]; then
  echo "ERROR: companion/public/config.js is deployment-local and must not be committed." >&2
  exit 1
fi

echo "Validation passed"
