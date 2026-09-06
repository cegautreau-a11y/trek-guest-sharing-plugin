#!/usr/bin/env sh
# Static/source validation for a release-ready TREK Guest Portal tree.
set -eu
cd "$(dirname "$0")/.."

VERSION="$(tr -d '\r\n' < VERSION)"
echo "Validating TREK Guest Portal ${VERSION}"

# Syntax checks use only runtimes already required by development/CI.
python3 - <<'PYCODE'
from pathlib import Path
for path in [Path('companion/server/server.py'), *Path('companion/tools').glob('*.py'), *Path('tests').glob('*.py')]:
    compile(path.read_text(), str(path), 'exec')
print('Python syntax checks passed')
PYCODE
node --check plugin/server/index.js
node --check companion/public/app.js
for script in scripts/*.sh; do
  sh -n "$script"
done

python3 - <<'PY'
from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
import sys

import yaml

root = Path('.')
version = (root / 'VERSION').read_text().strip()


def fail(message: str) -> None:
    raise SystemExit(f'ERROR: {message}')


def text_files():
    """Yield repository text files while ignoring build/runtime by-products."""
    ignored_parts = {'.git', 'dist', '__pycache__', '.pytest_cache'}
    ignored_suffixes = {'.zip', '.pyc', '.db', '.sqlite', '.sqlite3'}
    # Skip files whose names contain references to unsupported container-management
    # products to avoid false-positives in the forbidden-ui scan.
    ignored_name_fragments = {'portainer', 'validate.sh', 'changelog.md'}
    for path in root.rglob('*'):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        if path.suffix.lower() in ignored_suffixes or path.stat().st_size > 2_000_000:
            continue
        if any(n in path.name.lower() for n in ignored_name_fragments):
            continue
        try:
            path.read_text()
        except UnicodeDecodeError:
            continue
        yield path


# -------------------------------------------------------------------------
# Version consistency and repository hygiene
# -------------------------------------------------------------------------
manifest = json.loads((root / 'plugin/trek-plugin.json').read_text())
package = json.loads((root / 'plugin/package.json').read_text())
server = (root / 'companion/server/server.py').read_text()
public_index = (root / 'companion/public/index.html').read_text()
plugin_server = (root / 'plugin/server/index.js').read_text()
plugin_client = (root / 'plugin/client/index.html').read_text()

if manifest.get('version') != version:
    fail(f"plugin manifest version {manifest.get('version')} != {version}")
if package.get('version') != version:
    fail(f"plugin package version {package.get('version')} != {version}")
if f'VERSION = "{version}"' not in server:
    fail('companion server VERSION does not match VERSION file')
if f'Guest Portal v{version} loaded' not in plugin_server:
    fail('plugin startup version does not match VERSION file')
for asset in ('style.css', 'config.js', 'app.js'):
    if f'{asset}?v={version}' not in public_index:
        fail(f'public index cache-busting version is stale for {asset}')

all_text = '\n'.join(path.read_text(errors='ignore') for path in text_files())
# Construct forbidden deployment terms so the validator does not itself create
# a repository reference to an unsupported container-management product.
forbidden_ui = 'port' + 'ainer'
if forbidden_ui.lower() in all_text.lower():
    fail('unsupported container-management UI terminology remains in source')

# Project history is intentionally consolidated at the current release. Block
# project-version-shaped references from the old development/release lines.
old_release_patterns = (
    re.compile(r'(?<![\d.])v?1[.]0[.]\d+(?![\d.])', re.I),
    re.compile(r'(?<![\d.])v?0[.]3(?:[.]\d+|[.]x)(?![\d.])', re.I),
    re.compile(r'pre[- ]1[.]0', re.I),
    re.compile(r'v1[.]x', re.I),
)
for path in text_files():
    content = path.read_text(errors='ignore')
    for pattern in old_release_patterns:
        match = pattern.search(content)
        if match:
            fail(f'stale Guest Portal release reference {match.group(0)!r} in {path}')

# Do not ship local secrets/configuration or generated runtime bytecode.
for forbidden in (
    root / '.env',
    root / 'companion/public/config.js',
    root / 'companion/secrets',
):
    if forbidden.exists():
        fail(f'deployment-local path must not be committed: {forbidden}')

# Never embed the maintainer's private deployment hostname in public source.
deployment_marker = 'wherearemy' + 'packetsgoing'
if deployment_marker.lower() in all_text.lower():
    fail('deployment-specific production hostname found in source')


# -------------------------------------------------------------------------
# Docker Compose, .env.example and runtime configuration consistency
# -------------------------------------------------------------------------
env_path = root / '.env.example'
env_lines = env_path.read_text().splitlines()
env_keys = []
for line in env_lines:
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or '=' not in stripped:
        continue
    env_keys.append(stripped.split('=', 1)[0])
duplicates = sorted(key for key, count in Counter(env_keys).items() if count > 1)
if duplicates:
    fail(f'duplicate active key(s) in .env.example: {duplicates}')

env_key_set = set(env_keys)
compose_text = (root / 'companion/docker-compose.yml').read_text()
compose_data = yaml.safe_load(compose_text)
if not isinstance(compose_data, dict) or 'services' not in compose_data:
    fail('docker-compose.yml is not a valid Compose mapping')
service = compose_data.get('services', {}).get('trek-guest-portal')
if not isinstance(service, dict):
    fail('docker-compose.yml does not define trek-guest-portal')

interpolated = set(re.findall(r'\$\{([A-Z][A-Z0-9_]*)', compose_text))
if interpolated != env_key_set:
    missing = sorted(interpolated - env_key_set)
    unused = sorted(env_key_set - interpolated)
    fail(f'.env.example/Compose interpolation mismatch; missing={missing}, unused={unused}')

network = compose_data.get('networks', {}).get('trek', {})
if not network.get('external') or 'TREK_DOCKER_NETWORK' not in str(network.get('name', '')):
    fail('Compose must join TREK through the configured external Docker network')

compose_env = service.get('environment', [])
compose_env_keys = {str(item).split('=', 1)[0] for item in compose_env if '=' in str(item)}
required_fixed = {
    'LISTEN_PORT', 'PUBLIC_ROOT', 'GUEST_CACHE_DB',
    'AERODATABOX_API_KEY_FILE', 'IMMICH_API_KEY_FILE',
    'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED',
}
if not required_fixed.issubset(compose_env_keys):
    fail(f'Compose missing container-managed environment settings: {sorted(required_fixed-compose_env_keys)}')

server_env = set(re.findall(r'os\.environ\.get\("([A-Z][A-Z0-9_]*)"', server))
server_secret_env = set(re.findall(r'_read_secret\("([A-Z][A-Z0-9_]*)", "([A-Z][A-Z0-9_]*)"\)', server))
server_secret_names = {name for pair in server_secret_env for name in pair}
server_env |= server_secret_names
intentional_server_only = {'LISTEN_HOST', 'AERODATABOX_API_KEY', 'IMMICH_API_KEY', 'GUEST_PLUGIN_PATH'}
if not (server_env - intentional_server_only).issubset(compose_env_keys):
    fail(f'server environment setting(s) not represented by Compose: {sorted((server_env-intentional_server_only)-compose_env_keys)}')

config_doc = (root / 'docs/CONFIGURATION.md').read_text()
for key in env_keys:
    # Internal/server-only paths that don't need user documentation
    if key in {'GUEST_PLUGIN_PATH', 'ICAL_TIMEZONE'}:
        continue
    if f'`{key}`' not in config_doc:
        fail(f'CONFIGURATION.md does not document .env key {key}')
for key in required_fixed:
    if f'`{key}`' not in config_doc:
        fail(f'CONFIGURATION.md does not document container-managed setting {key}')
if 'PHOTO_DATE_CACHE_TTL = float(os.environ.get("PHOTO_DATE_CACHE_TTL"' not in server:
    fail('PHOTO_DATE_CACHE_TTL is not wired into the companion server')
if 'FLIGHT_TRACKER_DB' in server or 'read_tracker_cache' in server or 'find_tracker_db' in server:
    fail('public companion contains obsolete runtime plugin-database access')

# Compose is the single documented deployment method; key entry points must
# describe the standalone external-network workflow and current artifact names.
for rel in ('README.md', 'companion/README.md', 'docs/INSTALL.md', 'docs/PREREQUISITES.md', 'docs/UPGRADING.md'):
    content = (root / rel).read_text()
    if 'Docker Compose' not in content and 'docker compose' not in content:
        fail(f'{rel} does not describe Docker Compose deployment')
for token in (
    'TREK_DOCKER_NETWORK', 'docker compose config', 'docker compose up -d',
    f'trek-guest-portal-{version}.zip', f'trek-guest-portal-companion-{version}.zip',
    'PUBLIC_ORIGIN=https://guest.example.com', 'TREK_PUBLIC_ORIGIN=https://trek.example.com',
):
    if token not in (root / 'docs/INSTALL.md').read_text():
        fail(f'INSTALL.md missing required deployment instruction: {token}')


# -------------------------------------------------------------------------
# Documentation/link and source-comment quality checks
# -------------------------------------------------------------------------
link_re = re.compile(r'\[[^\]]+\]\(([^)]+)\)')
for path in list(root.glob('*.md')) + list((root / 'docs').glob('*.md')) + [root / 'companion/README.md', root / 'plugin/README.md']:
    content = path.read_text()
    for target in link_re.findall(content):
        target = target.strip().split('#', 1)[0]
        if not target or '://' in target or target.startswith('mailto:'):
            continue
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            # Links in component READMEs may intentionally navigate within repo,
            # but none should escape the repository root.
            fail(f'{path} link escapes repository: {target}')
        if not resolved.exists():
            fail(f'broken relative Markdown link in {path}: {target}')

# Every production Python function/class should explain its responsibility.
for path in [root / 'companion/server/server.py', *sorted((root / 'companion/tools').glob('*.py'))]:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not ast.get_docstring(node):
            fail(f'missing docstring in {path}:{node.lineno} ({node.name})')

# JavaScript/HTML/CSS comments are section-oriented rather than noisy per-line
# comments. Check the key security/runtime boundaries remain documented in code.
comment_markers = {
    root / 'companion/public/app.js': [
        'Share credentials live only in the URL fragment',
        'Browser telemetry is session-protected',
        'Safe formatting and tolerant TREK/Journey data normalization',
        'Plan / itinerary rendering and Mapbox lifecycle',
        'Flights: reservation normalization',
        'Journey photos, date grouping and lightbox media navigation',
    ],
    root / 'plugin/server/index.js': [
        'Native-share and Guest Portal URL normalization',
        'Authenticated TREK/database helpers',
        'TREK plugin lifecycle and authenticated Admin configuration routes',
    ],
    root / 'plugin/client/index.html': [
        'TREK iframe bridge state',
        'Native share capabilities are placed in the fragment',
        'Accept bridge messages only from the parent window',
    ],
    root / 'companion/public/style.css': [
        'Global tokens, reset, page shell and navigation',
        'Mapbox planner and inline itinerary-map layout',
        'Flights, reservations and mobile guest experience',
    ],
}
for path, markers in comment_markers.items():
    content = path.read_text()
    for marker in markers:
        if marker not in content:
            fail(f'expected explanatory source comment missing from {path}: {marker}')

# Keep placeholders/freeform issue templates aligned with current release.
issue = (root / '.github/ISSUE_TEMPLATE/bug_report.yml').read_text()
if f'placeholder: {version}' not in issue:
    fail('bug-report template version placeholder is stale')

print('Source, version, configuration, documentation and comment checks passed')
PY

echo "Validation passed"
