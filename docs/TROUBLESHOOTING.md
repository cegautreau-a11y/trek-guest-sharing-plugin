# Troubleshooting

Start with:

```bash
docker logs --tail 200 trek-guest-portal
```

Temporary deep logging:

```yaml
- LOG_LEVEL=DEBUG
```

Return to `INFO` after troubleshooting.

## Companion does not start: `PUBLIC_ORIGIN must be an https:// origin`

Set an exact HTTPS origin:

```yaml
- PUBLIC_ORIGIN=https://trek.example.com
```

Do **not** use:

```text
https://trek.example.com/guest-portal/
```

because `PUBLIC_ORIGIN` must not have a path.

## Portainer reports file-vs-directory mount errors

Example:

```text
not a directory: Are you trying to mount a directory onto a file (or vice-versa)?
```

Check that secret paths are actual files. The provided Compose uses long bind syntax with `create_host_path: false` so missing paths fail instead of silently becoming directories.

## `cache.persistent_unavailable ... unable to open database file`

The non-root UID must be able to write the cache directory:

```bash
sudo chown 65532:65532 /opt/trek-guest-portal/cache
sudo chmod 700 /opt/trek-guest-portal/cache
```

Test directly:

```bash
docker run --rm --user 65532:65532 \
  -v /opt/trek-guest-portal/cache:/cache \
  python:3.12-alpine \
  sh -c 'touch /cache/.test && rm /cache/.test && echo CACHE_WRITE_OK'
```

## `Service Unavailable` / HTTP 503

First test the companion from the Docker/reverse-proxy host:

```bash
curl -I http://127.0.0.1:8088/
```

Then test public:

```bash
curl -I https://trek.example.com/guest-portal/
```

If the first fails, inspect container logs/port mapping. If only the second fails, inspect reverse-proxy rule order and network/firewall access.

## Guest Portal says no public Mapbox token

Check the host file:

```bash
cat /opt/trek-guest-portal/public/config.js
```

The property must be exactly:

```javascript
mapboxAccessToken: 'pk....'
```

Check what the companion serves:

```bash
curl -s http://127.0.0.1:8088/config.js
```

If it returns HTML instead of JavaScript, `config.js` is missing.

## Mapbox returns 403 after URL restriction

Check the allowed URL pattern and make sure the proxy/browser still sends a referrer compatible with Mapbox URL restrictions. Guest Portal uses `Referrer-Policy: strict-origin-when-cross-origin` for this reason.

## Flights tab is empty

Flights is always visible. If it has no scheduled transport cards, make sure the underlying native TREK public trip share has **Bookings** enabled and that the trip actually has shared transport reservations.

## Live flight information is missing

Check startup log:

```text
integration.aerodatabox configured=True key_source=secret-file
```

Verify the secret exists without printing it:

```bash
stat /opt/trek-guest-portal/secrets/aerodatabox_api_key
```

If using the extractor, remember it only works when Flight Tracker stored the key in its own SQLite `kv` table. Otherwise create the secret manually.

## AeroDataBox rate-limit errors

Guest Portal globally serializes provider calls and uses a persistent shared cache. The default minimum spacing is 1.6 seconds.

Confirm you have not reduced:

```yaml
AERODATABOX_MIN_INTERVAL=1.6
```

Also check the provider's current plan quota/rate limit. Multiple unrelated applications using the same provider key can still consume the same account-level quota.

## Next auto refresh looks wrong

In v1.0.4 the Flights header separates the browser check from the provider schedule:

- more than 48 hours out: **Next auto refresh** should be about 10 minutes, while the detail line says when the live-provider window opens;
- inside 48 hours: the browser checks every minute, but AeroDataBox is only called when the 30m / 5m / 1m provider TTL actually expires;
- completed flights stop continuous refresh.

Check the correlated scheduler event:

```text
flight.refresh_decision ... decision=suppress-aerodatabox ... api_window_open=False ... poll_after=600
```

If a flight more than 48 hours away logs `decision=call-aerodatabox`, capture the surrounding events with:

```bash
docker logs --since 10m trek-guest-portal | grep -E 'flight\.|aerodatabox\.'
```

and include them in a bug report. Full share tokens and API keys should not appear in those logs.

## Photos show `Undated`

If the Journey is Immich-backed, verify:

```text
integration.immich configured=True
```

and confirm `IMMICH_URL` is reachable from the companion. The Immich API key must be allowed to read asset metadata.

Without a resolvable Immich date, Guest Portal falls back to Journey entry/photo timestamps.

## Guest session required

Sessions are memory-only. They expire and are lost when the companion restarts. Reopen the **original owner-generated Guest Portal URL** containing the fragment to establish a fresh session.

## Session origin rejected

The browser's `Origin` must exactly match `PUBLIC_ORIGIN`. Check scheme and hostname, including whether your canonical hostname redirects from another hostname.

## Plan click does not update map

Hard refresh to ensure the latest `app.js` is loaded. Confirm Mapbox initialized successfully. Selecting a Plan stop should move the single live map below that item and fit a geographic 1 km radius even on phones.
