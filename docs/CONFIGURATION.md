# Configuration Reference

## Companion environment variables

| Variable | Default | Required | Description |
|---|---:|:---:|---|
| `PUBLIC_ORIGIN` | none | **Yes** | Exact external HTTPS origin, e.g. `https://trek.example.com`. No path/query/fragment. |
| `COOKIE_PATH` | `/guest-portal/` | Yes | Must match the reverse-proxy path. |
| `TREK_HOST` | `app` | Yes | TREK backend hostname on the Docker network. |
| `TREK_PORT` | `3000` | Yes | TREK backend port. |
| `LISTEN_PORT` | `8080` | No | Companion container listener. |
| `PUBLIC_ROOT` | `/srv/public` | No | Static guest app root. |
| `LOG_LEVEL` | `INFO` | No | `INFO` recommended; temporary `DEBUG` for troubleshooting. |
| `SESSION_TTL_SECONDS` | `43200` | No | Guest-session lifetime; bounded by the application. |
| `SESSION_MAX` | `2048` | No | Maximum in-memory guest sessions. |
| `SESSION_CREATE_PER_MINUTE` | `120` | No | Session creation rate limit. |
| `GUEST_CACHE_DB` | `/cache/guest-portal.db` | No | Guest Portal-owned persistent flight cache. |
| `GUEST_CACHE_MAX_ROWS` | `512` | No | Maximum persistent live-flight cache rows. |
| `AERODATABOX_API_KEY_FILE` | none | No | Recommended mounted secret path. |
| `AERODATABOX_API_KEY` | none | No | Compatibility environment fallback; secret file preferred. |
| `AERODATABOX_TIMEOUT` | `10` | No | Provider timeout seconds. |
| `AERODATABOX_MIN_INTERVAL` | `1.6` | No | Global minimum spacing between provider calls. |
| `AERODATABOX_429_RETRIES` | `2` | No | Retry count after HTTP 429. |
| `AERODATABOX_429_BACKOFF` | `2.5` | No | Backoff base in seconds. |
| `IMMICH_URL` | blank | No | Immich base URL. Blank disables direct Immich metadata lookup. |
| `IMMICH_API_KEY_FILE` | none | No | Recommended Immich mounted secret. |
| `IMMICH_API_KEY` | none | No | Compatibility environment fallback. |
| `IMMICH_VERIFY_TLS` | `true` | No | Keep `true` unless you understand the TLS risk. |
| `IMMICH_TIMEOUT` | `15` | No | Immich timeout seconds. |
| `IMMICH_DATE_CACHE_TTL` | `86400` | No | Immich capture-date cache seconds. |

`PUBLIC_ORIGIN` is intentionally required in the public repository build. This prevents an accidentally published companion from trusting an unrelated default hostname.

## Mapbox `config.js`

`companion/public/config.js` is local deployment configuration and is ignored by Git.

Create it from `config.js.example`:

```javascript
window.GUEST_PORTAL_CONFIG = {
  mapboxAccessToken: 'pk.YOUR_PUBLIC_TOKEN',
  mapboxStyle: 'mapbox://styles/mapbox/standard',
  mapbox3d: true,
  mapboxHighQuality: false
};
```

Use a public browser token, not a Mapbox secret token. Restrict the token to the external origin when possible.

## Secret files

Recommended paths inside the container:

```text
/run/secrets/aerodatabox_api_key
/run/secrets/immich_api_key
```

Recommended host modes:

```text
secrets directory: 0700
secret file:       0600
owner:             65532:65532
```

Empty secret files are valid and simply disable the associated integration.

## Persistent cache

Guest Portal owns its own SQLite file:

```text
/cache/guest-portal.db
```

The cache stores normalized flight refresh payloads and timestamps. It does not store provider API keys or native TREK/Journey share tokens.

## Guest sessions

Guest sessions are memory-only by design. Restarting the companion invalidates them. A guest can establish a new session by reopening the original owner-generated Guest Portal URL.

The session cookie is scoped to `COOKIE_PATH` and set `HttpOnly`, `Secure`, and `SameSite=Strict`.

## Logging

Use:

```bash
docker logs -f trek-guest-portal
```

At `INFO`, the companion records startup, provider configuration state, session lifecycle, flight/cache events, provider failures, and Immich date resolution summaries.

The application is designed not to log provider keys or full native share tokens. Avoid adding reverse-proxy debug modules that record request bodies, because the initial `POST /api/session` body contains native share capabilities.
