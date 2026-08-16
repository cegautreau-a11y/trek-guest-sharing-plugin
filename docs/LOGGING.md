# Logging and observability

TREK Guest Portal v1.0.4 provides full operational logging for the guest-facing system without logging bearer share tokens, API keys, cookies, passwords, confirmation numbers, email addresses, or phone numbers.

## Log streams

There are two intentional log streams:

1. **Guest Portal companion (`trek-guest-portal`)** — all public guest/runtime activity, browser telemetry, TREK reads, Mapbox/plan interactions, flights, AeroDataBox, adsb.fi, reservations, photos, Immich, cache activity, security decisions, HTTP timing, and runtime heartbeat.
2. **TREK `app` container** — Admin plugin configuration reads/writes performed inside TREK. The plugin uses TREK's `ctx.log` API so these events stay inside TREK's trusted plugin runtime.

No separate logging service is required.

## Viewing logs

```bash
docker logs --tail 200 -f trek-guest-portal
```

In Portainer: **Containers → trek-guest-portal → Logs**.

For Admin-plugin events:

```bash
docker logs --tail 200 -f trek
```

## Recommended full logging configuration

```yaml
- LOG_LEVEL=INFO
- LOG_FORMAT=kv
- FULL_LOGGING=true
- LOG_STATIC_REQUESTS=true
- LOG_SAFE_REQUEST_HEADERS=true
- CLIENT_EVENT_LOGGING=true
- CLIENT_EVENT_RATE_PER_MINUTE=240
- LOG_HEARTBEAT_SECONDS=300
```

Client IP logging is separate because it is personal data and proxy trust must be configured correctly:

```yaml
- LOG_CLIENT_IP=true
- TRUST_PROXY_HEADERS=true
- CLIENT_IP_HEADER=X-Guest-Client-IP
- TRUSTED_PROXY_CIDRS=192.0.2.10/32
- LOG_PROXY_DETAILS=true
```

Use the actual Apache/Nginx peer IP or CIDR visible to the container. Do not trust Cloudflare IP ranges directly in the application; normalize the client IP at the reverse proxy first. See [REVERSE-PROXY.md](REVERSE-PROXY.md).

## Log formats

Human-readable key/value format (default):

```yaml
- LOG_FORMAT=kv
```

Example:

```text
2026-08-16T21:40:00Z INFO flight.refresh_decision req=9e50ef14 client=198.51.100.24 trip_id=1 reservation_id=42 phase=upcoming hours_to_departure=72.1 api_window_open=False decision=suppress-aerodatabox poll_after=600
```

JSON format for Loki, Splunk, Elastic, Graylog, Vector, Fluent Bit, etc.:

```yaml
- LOG_FORMAT=json
```

Example message body:

```json
{"event":"flight.refresh_decision","req":"9e50ef14","trip_id":"1","reservation_id":"42","decision":"suppress-aerodatabox"}
```

The container runtime still adds the timestamp/severity prefix unless your log collector parses the message field separately.

## Correlation IDs

Every HTTP request receives a random request ID such as:

```text
req=9e50ef14
```

JSON/API/static responses also include:

```http
X-Guest-Request-ID: 9e50ef14
```

Browser telemetry captures that response ID as `ui_api_req`, allowing a browser action to be correlated with the exact server request that serviced it.

## HTTP and security events

Typical events:

```text
http.request_start
http.response
http.route_not_found
static.serve
session.create_attempt
session.created
session.auth_ok
session.auth_failed
session.deleted
session.cleanup
session.origin_rejected
session.rate_limited
client.event_rejected
client.event_rate_limited
```

`FULL_LOGGING=true` adds safe HTTP metadata such as Host, Accept, Accept-Language, Sec-Fetch-* fields, user agent, and proxy details when enabled. It never logs Cookie or Authorization headers.

## Browser/client events

v1.0.4 introduces a session-protected telemetry endpoint:

```text
POST /api/client-log
```

The browser uses it automatically. It is same-origin, requires a valid HttpOnly guest session, validates the Origin header, rate-limits events per session, and sanitizes all fields server-side.

Typical browser events include:

```text
client.event name=session.established
client.event name=portal.load_start
client.event name=portal.data_loaded
client.event name=portal.load_failed
client.event name=navigation.tab_select
client.event name=plan.render
client.event name=plan.day_select
client.event name=plan.search
client.event name=plan.stop_select
client.event name=map.init_start
client.event name=map.loaded
client.event name=map.error
client.event name=map.route_start
client.event name=map.route_complete
client.event name=map.route_failed
client.event name=flights.render
client.event name=flights.refresh_start
client.event name=flights.refresh_complete
client.event name=flights.refresh_failed
client.event name=reservations.render
client.event name=photos.render
client.event name=photos.date_enrichment_start
client.event name=photos.date_enrichment_complete
client.event name=photos.date_enrichment_failed
client.event name=photos.lightbox_open
client.event name=photos.lightbox_move
client.event name=photos.lightbox_close
client.event name=browser.error
client.event name=browser.unhandled_rejection
client.event name=browser.network
client.event name=browser.visibility
```

Browser telemetry includes only operational fields such as tab name, item counts, viewport size, reservation/place identifiers, response status, timing, and state names. Sensitive-looking field names are discarded by both the browser and server.

## TREK upstream events

```text
trek.upstream_start
trek.upstream_response
trek.upstream_failed
trek.share_fetch
trek.share_cache_hit
trek.journey_fetch
trek.media_proxy_start
trek.media_proxy_response
trek.media_proxy_complete
trek.media_proxy_failed
trip.read_start
trip.read_complete
trip.read_invalid
trip.read_failed
journey.read_start
journey.read_complete
journey.read_invalid
journey.read_failed
```

Payload summaries log counts and status, not reservation confirmations or private content.

## Flight logging

Flight logging exposes every scheduling decision so API usage can be audited.

```text
flight.request
flight.refresh_decision
flight.leg_refresh_plan
flight.live_refresh_start
flight.live_refresh_complete
flight.live_refresh_failed
flight.cache_fallback
flight.response
flight.unavailable
flight.adsb_decision
aerodatabox.request
aerodatabox.response
aerodatabox.rate_wait
aerodatabox.rate_limited
aerodatabox.rate_limit_exhausted
adsb.request
adsb.response
```

Important scheduler fields:

- `phase`
- `hours_to_departure`
- `api_window_open`
- `api_window_opens_in`
- `poll_after`
- `api_ttl`
- `api_due_in`
- `decision`
- `source`

For flights more than 48 hours away, expect:

```text
decision=suppress-aerodatabox api_window_open=False poll_after=600
```

This means the browser will check again in 10 minutes, but AeroDataBox is **not** called.

## Cache logging

```text
cache.persistent_ready
cache.persistent_unavailable
cache.persistent_hit
cache.persistent_miss
cache.persistent_write
cache.persistent_read_failed
cache.persistent_write_failed
flight.memory_cache_evict
```

The persistent Guest Portal cache is separate from TREK and Flight Tracker.

## Immich/photo logging

```text
photos.date_request
photos.date_response
photos.date_resolve_failed
photo.embedded_lookup
photo.embedded_cache_hit
immich.asset_lookup_start
immich.asset_lookup
immich.asset_lookup_failed
immich.date_cache_hit
```

Immich asset IDs are represented by short SHA-256 fingerprints rather than logged verbatim where appropriate. API keys are never logged.

## Runtime heartbeat

Every 300 seconds by default:

```text
runtime.heartbeat
```

It reports:

- uptime
- active sessions
- active Python threads
- process RSS high-water mark
- in-memory TREK share cache entries
- embedded photo-date cache entries
- Immich date cache entries
- live flight memory cache entries
- persistent SQLite row count and file size
- free space on the cache filesystem
- total HTTP requests/responses
- browser telemetry count
- session creation count
- AeroDataBox / adsb.fi / Immich / TREK request counters
- warning/error counters

Change the interval with:

```yaml
- LOG_HEARTBEAT_SECONDS=300
```

Minimum is 60 seconds.

## Admin plugin logging

The TREK plugin logs configuration actions without the actual share tokens:

```text
Guest Portal config read start trip=1
Guest Portal config read complete trip=1 configured=true journey=true elapsed_ms=4
Guest Portal config write start trip=1 has_trip_share=true has_journey_share=true has_portal_base=true
Guest Portal config write complete trip=1 journey=true portal_base=/guest-portal/ elapsed_ms=7
```

These events appear in the TREK `app` container log, not the public companion container.

## Privacy and redaction

The logger intentionally does **not** record:

- TREK public share bearer tokens
- Journey share bearer tokens
- guest session cookie values
- Immich API keys
- AeroDataBox/RapidAPI keys
- passwords
- Authorization or Cookie headers
- booking/reservation confirmation numbers
- email addresses or phone numbers from browser telemetry

Native share tokens that must be referenced in logs are represented by a short SHA-256 fingerprint.

## DEBUG logging

For temporary diagnosis:

```yaml
- LOG_LEVEL=DEBUG
```

DEBUG includes additional cache hits, internal HTTP server messages, and lower-value provider details. Do not leave DEBUG enabled permanently unless your log retention/storage is sized for it.

## Log retention

Guest Portal writes to stdout/stderr and relies on Docker's log driver. Configure Docker/Portainer log rotation on production systems. For the `json-file` driver, an example daemon configuration is:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
```

Changing Docker daemon logging settings can affect all containers; evaluate it for your environment first.
