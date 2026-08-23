# TREK Guest Portal Companion

The companion is the public, read-only gateway used by TREK Guest Portal. It serves the guest web application, validates native TREK/Journey public shares, creates HttpOnly guest sessions, proxies authorized Journey media, and performs optional server-side flight and Immich lookups.

## Deploy with Docker Compose

This directory contains the Compose service used by the release package. The packaged companion places `docker-compose.yml` at its root so deployment is:

```bash
cp .env.example .env
nano .env
docker compose config
docker compose up -d
```

The service attaches to TREK's existing Docker network using `TREK_DOCKER_NETWORK`. This lets the companion run as its own Compose project while still reaching the TREK service name configured by `TREK_HOST`.

See [docs/INSTALL.md](../docs/INSTALL.md) in the source tree, or `docs/INSTALL.md` inside the companion release ZIP.

## Local files

Create these before the first deployment:

```text
public/config.js
cache/
secrets/aerodatabox_api_key
secrets/immich_api_key
```

Copy `public/config.js.example` to `public/config.js` and add a public Mapbox `pk...` token. Empty provider secret files are valid when the related integration is disabled.

## Dedicated guest origin

A separate guest hostname is recommended:

```dotenv
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

This keeps the anonymous guest browser origin separate from TREK's PWA Service Worker and authenticated browser state. Same-origin `/guest-portal/` deployments remain supported.

## Runtime security

The long-running companion does not mount or inspect TREK plugin databases. The one-shot helpers under `tools/` may be run manually with read-only access to an existing plugin-data directory to copy a provider key into the dedicated Guest Portal secret file; that access is not part of normal runtime.

The Compose service runs as an unprivileged UID/GID, uses a read-only root filesystem, drops all Linux capabilities, enables `no-new-privileges`, and mounts only the Guest Portal cache as writable application state.

## Health and logs

```bash
docker compose ps
docker compose exec trek-guest-portal python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health').read().decode())"
docker compose logs -f trek-guest-portal
```

The public `/health` endpoint intentionally returns only `ok` and the current release version.
