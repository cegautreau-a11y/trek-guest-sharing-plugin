# Configuration Reference

TREK Guest Portal is configured primarily through `.env`, provider secret files, and `public/config.js`.

## Docker Compose workflow

Create the local environment file once:

```bash
cp .env.example .env
nano .env
```

Validate after every change:

```bash
docker compose config
```

Apply changes with:

```bash
docker compose up -d
```

Provider API keys are intentionally **not** stored in `.env`.

## Docker / TREK connectivity

| Variable | Default/example | Purpose |
|---|---:|---|
| `TREK_DOCKER_NETWORK` | `trek_default` example | Exact existing Docker network used by TREK. The companion joins this external network. |
| `TREK_HOST` | `app` | TREK service/DNS name on that Docker network. |
| `TREK_PORT` | `3000` | TREK internal HTTP port. |
| `UPSTREAM_TIMEOUT` | `20` | Timeout in seconds for TREK upstream requests. |
| `GUEST_PORTAL_BIND_IP` | `127.0.0.1` | Host interface used to publish companion TCP/8088. Use one explicit LAN address only when the reverse proxy is remote. |
| `GUEST_PORTAL_UID` | `65532` | UID used by the companion process. |
| `GUEST_PORTAL_GID` | `65532` | GID used by the companion process. |

Find TREK's network with:

```bash
docker inspect <TREK_CONTAINER> \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
```

The Compose project will fail to start if `TREK_DOCKER_NETWORK` does not name an existing Docker network.

## Public origins and cookie path

| Variable | Recommended | Purpose |
|---|---|---|
| `PUBLIC_ORIGIN` | `https://guest.example.com` | Exact HTTPS origin guests visit. Required. No path, query, fragment, or trailing slash is needed. |
| `TREK_PUBLIC_ORIGIN` | `https://trek.example.com` | Main TREK browser origin. Used for the Host/X-Forwarded-Host values on server-side public-share validation requests. |
| `COOKIE_PATH` | `/` | Guest session cookie path. Use `/` for a dedicated guest hostname. |

Recommended dedicated-host settings:

```dotenv
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

Same-origin alternative:

```dotenv
PUBLIC_ORIGIN=https://trek.example.com
TREK_PUBLIC_ORIGIN=
COOKIE_PATH=/guest-portal/
```

Both `PUBLIC_ORIGIN` and the effective TREK origin must be HTTPS origins without a path, query, or fragment. The server refuses to start when these checks fail.

## Guest sessions

| Variable | Default | Purpose |
|---|---:|---|
| `SESSION_TTL_SECONDS` | `0` | Server-side session age limit. `0` disables age-based expiry. Positive values are clamped to a safe range. |
| `SESSION_COOKIE_MAX_AGE_SECONDS` | `315360000` | Browser cookie Max-Age when sessions have no server-side age expiry. |
| `SESSION_MAX` | `2048` | Maximum in-memory guest sessions. |
| `SESSION_CREATE_PER_MINUTE` | `120` | Global session-creation rate limit. |
## iCal feed

| Variable | Default | Purpose |
|---|---|---|
| `ICAL_TIMEZONE` | `UTC` | IANA timezone name (e.g. `America/New_York`) used as fallback for accommodation events and when per-event airport timezone resolution fails. |
| `GUEST_PLUGIN_PATH` | `/guest-plugin/` | Base URL path prefix if the companion is mounted under a URL prefix. Must start and end with a slash. Set this to match the reverse-proxy mount point so iCal/WebCal URLs are generated correctly. |
Sessions are intentionally memory-only. Restarting/redeploying the companion invalidates them. Guests re-establish access by opening the original owner-generated share URL.

## Logging and browser telemetry

| Variable | Default | Purpose |
|---|---:|---|
| `LOG_LEVEL` | `INFO` | Python log level. `DEBUG` adds lower-level diagnostic events. |
| `LOG_FORMAT` | `kv` | `kv` for human-readable key/value logs or `json` for structured ingestion. |
| `FULL_LOGGING` | `true` | Enables the full operational event set. |
| `LOG_STATIC_REQUESTS` | `true` | Includes static asset request events. |
| `LOG_SAFE_REQUEST_HEADERS` | `true` | Logs a small allowlist of non-secret request headers. |
| `CLIENT_EVENT_LOGGING` | `true` | Enables session-protected browser telemetry. |
| `CLIENT_EVENT_RATE_PER_MINUTE` | `240` | Per-session browser telemetry event limit. |
| `CLIENT_EVENT_MAX_BODY` | `8192` | Maximum accepted telemetry JSON body size in bytes. |
| `LOG_HEARTBEAT_SECONDS` | `300` | Runtime heartbeat interval. || `LOG_PROXY_DETAILS` | `true` | Include proxy peer/source information when client-IP logging is enabled. |
Browser telemetry is sanitized server-side. Fields whose names imply tokens, secrets, passwords, authorization, cookies, sessions, confirmations, email addresses, or phone numbers are rejected rather than logged.

## Client IP / reverse-proxy trust

| Variable | Default | Purpose |
|---|---|---|
| `LOG_CLIENT_IP` | `false` | Include resolved client IP information in logs. |
| `TRUST_PROXY_HEADERS` | `false` | Accept the configured client-IP header only from trusted proxy peers. |
| `CLIENT_IP_HEADER` | `X-Guest-Client-IP` | Application-specific header populated by the trusted reverse proxy. |
| `TRUSTED_PROXY_CIDRS` | empty | Comma/space-separated immediate proxy peer CIDRs allowed to supply the header. |
| `LOG_PROXY_DETAILS` | `true` | Include proxy peer/source information when client-IP logging is enabled. |

Do not enable `TRUST_PROXY_HEADERS` without setting `TRUSTED_PROXY_CIDRS` to the actual immediate proxy address(es) seen by the companion. See [REVERSE-PROXY.md](REVERSE-PROXY.md).

## Flight scheduler

| Variable | Default | Purpose |
|---|---:|---|
| `FLIGHT_API_WINDOW_HOURS` | `48` | Live-provider window before scheduled departure. |
| `FLIGHT_UPCOMING_POLL_SECONDS` | `600` | Browser/local check interval before the live-provider window. |
| `FLIGHT_ACTIVE_POLL_SECONDS` | `60` | Browser/local check interval inside the live/active window. |
| `FLIGHT_ERROR_POLL_SECONDS` | `300` | Retry interval after provider errors. |
| `LIVE_FLIGHT_MAX_CACHE` | `256` | Maximum in-memory live-flight cache entries. |

The browser poll interval does not equal the provider API interval. The server's provider TTL is authoritative and can suppress external calls even when guests check more frequently.

## AeroDataBox and adsb.fi

| Variable | Default | Purpose |
|---|---:|---|
| `AERODATABOX_TIMEOUT` | `10` | AeroDataBox request timeout in seconds. |
| `AERODATABOX_MIN_INTERVAL` | `1.6` | Minimum interval between AeroDataBox calls across the process. |
| `AERODATABOX_429_RETRIES` | `2` | Number of retries after HTTP 429. |
| `AERODATABOX_429_BACKOFF` | `2.5` | Initial 429 retry backoff in seconds. |
| `ADSB_TIMEOUT` | `8` | adsb.fi request timeout in seconds. |
| `GUEST_CACHE_MAX_ROWS` | `512` | Maximum rows retained in the persistent live-flight cache. |

The AeroDataBox key is read from:

```text
./secrets/aerodatabox_api_key
```

The Compose service mounts it inside the container as `/run/secrets/aerodatabox_api_key`. The server also contains a direct environment-variable fallback for non-standard deployments, but the supplied Docker Compose configuration deliberately does not expose provider keys through `.env`.

## Immich and photo metadata

| Variable | Default | Purpose |
|---|---:|---|
| `IMMICH_URL` | empty | Immich base URL. Leave blank to disable Immich enrichment. |
| `IMMICH_VERIFY_TLS` | `true` | Verify Immich TLS certificates. Keep enabled in production. |
| `IMMICH_TIMEOUT` | `15` | Immich request timeout in seconds. |
| `IMMICH_DATE_CACHE_TTL` | `86400` | Immich asset capture-date cache TTL in seconds. |
| `PHOTO_METADATA_PREFIX_BYTES` | `2097152` | Maximum prefix read from an original shared photo when parsing embedded EXIF/XMP date metadata. |
| `PHOTO_DATE_CACHE_TTL` | `21600` | Embedded photo-date cache TTL in seconds. |

The Immich API key is read from:

```text
./secrets/immich_api_key
```

The Compose service mounts it inside the container as `/run/secrets/immich_api_key`.

## Browser Mapbox configuration

Copy:

```text
public/config.js.example
```

to:

```text
public/config.js
```

Then configure:

```javascript
window.GUEST_PORTAL_CONFIG = {
  mapboxAccessToken: 'pk.YOUR_PUBLIC_TOKEN',
  mapboxStyle: 'mapbox://styles/mapbox/standard',  // or custom style URL
  mapbox3d: true,        // enable 3D terrain and 45° pitch
  mapboxHighQuality: false  // true = globe projection + antialiasing
};
```

| Property | Default | Purpose |
|---|---|---|
| `mapboxAccessToken` | (required) | Public Mapbox `pk...` token. |
| `mapboxStyle` | `mapbox://styles/mapbox/standard` | Mapbox style URL or built-in style name. |
| `mapbox3d` | `true` | Enable 3D terrain and 45° pitch on selected stops. |
| `mapboxHighQuality` | `false` | Use globe projection and antialiasing for higher visual quality. |

`config.js` is public browser configuration and must not contain server secrets.

## Container-managed settings

The supplied Compose file fixes these internal values because normal deployments should not change them:

| Setting | Value |
|---|---|
| `LISTEN_PORT` | `8080` |
| `PUBLIC_ROOT` | `/srv/public` |
| `GUEST_CACHE_DB` | `/cache/guest-portal.db` |
| `AERODATABOX_API_KEY_FILE` | `/run/secrets/aerodatabox_api_key` |
| `IMMICH_API_KEY_FILE` | `/run/secrets/immich_api_key` |
| `PYTHONDONTWRITEBYTECODE` | `1` |
| `PYTHONUNBUFFERED` | `1` |

The Python server also supports `LISTEN_HOST`; the supplied container intentionally uses its internal default (`0.0.0.0`) while Docker controls host exposure through `GUEST_PORTAL_BIND_IP`.

## Apply configuration changes

After changing `.env` or Compose settings:

```bash
docker compose config
docker compose up -d
```

After changing only `public/config.js`, restart is not normally required because the file is bind-mounted, but guests may need a hard refresh if the browser cached an older asset.
