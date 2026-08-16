# Changelog

All notable changes to TREK Guest Portal are documented in this file.

This changelog is intended to be updated with every release. Add new work under **Unreleased** while developing, then move those entries into a versioned section when publishing a release.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows semantic versioning where practical.

## Unreleased

### Added

- Nothing yet.

### Changed

- Nothing yet.

### Fixed

- Nothing yet.

### Security

- Nothing yet.

---

## 1.0.4 - 2026-08-16

v1.0.4 is a major observability and diagnostics release. It includes the flight-refresh and trusted-proxy improvements developed for v1.0.3, then expands logging across the entire Guest Portal runtime so administrators can trace browser actions, companion requests, TREK lookups, caches, providers, media operations, and failures using correlated events.

### Added

#### Full-system structured logging

- Added structured logging across the complete guest-facing runtime, including:
  - HTTP requests and responses.
  - Guest session creation, validation, expiration, logout, and rate limiting.
  - TREK public-share validation and upstream requests.
  - Plan and itinerary rendering activity.
  - Mapbox initialization, stop selection, map focus, route requests, route completion, and map errors.
  - Flight rendering and refresh lifecycle.
  - Flight scheduler decisions.
  - AeroDataBox requests, responses, throttling, retries, and failures.
  - adsb.fi aircraft lookups and failures.
  - In-memory and persistent flight cache decisions.
  - Reservation rendering.
  - Journey and Immich photo metadata lookups.
  - Photo-date enrichment.
  - Gallery and lightbox activity.
  - Browser JavaScript errors and unhandled Promise rejections.
  - Browser online/offline state changes.
  - Browser visibility changes.
  - Startup, runtime, and periodic service health information.

#### Request correlation

- Added a unique request ID to Guest Portal requests.
- Added `X-Guest-Request-ID` to responses.
- Added request IDs to server log events so a request can be traced through TREK, cache, provider, and response processing.
- Added browser telemetry correlation so user-interface activity can be connected to the server request that handled it.

#### Browser telemetry

- Added the session-protected `POST /api/client-log` endpoint for operational browser telemetry.
- Added browser-side events for navigation, Plan, Mapbox, Flights, Reservations, Photos, gallery/lightbox activity, JavaScript errors, connectivity, and visibility changes.
- Added telemetry rate limiting.
- Added server-side telemetry sanitization and sensitive-field filtering before events are written to the container log.

#### Runtime heartbeat

- Added periodic `runtime.heartbeat` events.
- Heartbeat reporting can include:
  - service uptime;
  - active guest sessions;
  - thread count;
  - process RSS memory;
  - in-memory cache entry counts;
  - persistent cache row count and database size;
  - available cache filesystem space;
  - HTTP request/response counters;
  - browser telemetry counters;
  - session creation counters;
  - TREK/provider request counters;
  - warning/error counters.
- Added configurable heartbeat interval through `LOG_HEARTBEAT_SECONDS`.

#### Log output options

- Added `LOG_FORMAT=kv` for human-readable key/value logs.
- Added `LOG_FORMAT=json` for ingestion into centralized logging systems such as Loki, Graylog, Splunk, or Elasticsearch.
- Added `FULL_LOGGING` and additional logging configuration controls.
- Added optional static-request logging.
- Added safe request-header logging.
- Added expanded startup diagnostics describing logging, scheduler, integration, and runtime configuration without exposing credentials.

#### TREK Admin plugin audit logging

- Added configuration read/write audit events through TREK's own plugin logger.
- Admin-side events remain in the TREK application log instead of creating an additional cross-container logging/authentication channel.

### Changed

#### Flight scheduling behavior rolled forward from v1.0.3

- Separated **Guest Portal polling cadence** from **live-provider API refresh cadence**.
- Flights more than 48 hours before departure are checked locally by the Guest Portal every 10 minutes, but **AeroDataBox is not queried**.
- Once a flight enters the 48-hour live-data window, the Guest Portal checks flight state every minute while provider calls remain governed by the provider cache TTL.
- Provider refresh behavior is now:
  - **more than 48 hours:** no AeroDataBox calls;
  - **12-48 hours:** up to one AeroDataBox refresh every 30 minutes;
  - **3-12 hours:** up to one AeroDataBox refresh every 5 minutes;
  - **less than 3 hours:** up to one AeroDataBox refresh every 1 minute;
  - **active/boarding/en-route flights:** up to one refresh every 1 minute;
  - **completed flights:** automatic live refreshing stops.
- Updated the Flights UI so the browser's **next auto check** is distinct from the **next live-provider refresh/window**.
- Added explicit indication of when the 48-hour live-provider window will open for far-future flights.

#### Enhanced flight diagnostics rolled forward from v1.0.3

- Flight refresh decisions now log the reason behind each action, including fields such as:
  - flight phase;
  - hours until departure;
  - whether the live-provider window is open;
  - browser poll interval;
  - provider TTL;
  - cache age;
  - next provider refresh time;
  - memory-cache hit/miss;
  - persistent-cache hit/miss;
  - AeroDataBox suppression reason;
  - provider rate-limit wait;
  - provider fallback behavior.
- Far-future flights now produce an explicit `suppress-aerodatabox` decision instead of appearing to schedule an AeroDataBox call every two hours.

#### Trusted proxy/client-IP logging rolled forward from v1.0.3

- Added trusted-proxy-aware client IP resolution.
- Added support for a sanitized proxy-supplied client address header such as `X-Guest-Client-IP`.
- Added `TRUST_PROXY_HEADERS`, `CLIENT_IP_HEADER`, and `TRUSTED_PROXY_CIDRS` configuration.
- Added optional proxy-detail logging.
- Documented Apache `mod_remoteip` + Cloudflare handling so:
  - Internet requests can log the original public visitor address.
  - Local split-DNS requests can log the actual LAN client address.
  - The companion trusts forwarded client information only when the immediate TCP peer is a configured trusted proxy.
- Untrusted direct clients cannot spoof the logged client address by submitting their own forwarded-client headers.

### Fixed

- Fixed the previous interpretation of the far-future flight cache TTL as a live AeroDataBox refresh interval.
- Fixed unnecessary AeroDataBox API usage for flights outside the 48-hour live-data window.
- Fixed misleading Flights UI countdown behavior that could imply a provider API call was scheduled when only a local/browser check was due.
- Improved troubleshooting visibility for provider failures, cache decisions, TREK validation failures, Immich lookups, media requests, and browser-side errors.

### Security

- Full logging continues to intentionally exclude or redact sensitive values, including:
  - TREK native share tokens;
  - Journey share tokens;
  - guest session cookies/session IDs;
  - AeroDataBox API keys;
  - Immich API keys;
  - passwords;
  - authorization headers;
  - cookie headers;
  - reservation confirmation numbers in browser telemetry;
  - email addresses in browser telemetry;
  - phone numbers in browser telemetry.
- Browser telemetry requires a valid Guest Portal session and a valid same-origin request.
- Browser telemetry fields with sensitive names such as `token`, `secret`, `password`, `api_key`, `authorization`, `cookie`, `session`, `confirmation`, `email`, or `phone` are discarded before logging.
- Forwarded client addresses are trusted only from explicitly configured trusted proxy networks.

### Configuration

New or expanded logging settings include:

```yaml
LOG_LEVEL=INFO
LOG_FORMAT=kv
FULL_LOGGING=true
LOG_STATIC_REQUESTS=true
LOG_SAFE_REQUEST_HEADERS=true
CLIENT_EVENT_LOGGING=true
CLIENT_EVENT_RATE_PER_MINUTE=240
LOG_HEARTBEAT_SECONDS=300
```

For proxy-aware deployments:

```yaml
LOG_CLIENT_IP=true
TRUST_PROXY_HEADERS=true
CLIENT_IP_HEADER=X-Guest-Client-IP
TRUSTED_PROXY_CIDRS=<trusted-proxy-address>/32
LOG_PROXY_DETAILS=true
```

### Validation

v1.0.4 includes regression/security coverage for:

- Guest session security.
- Token and secret redaction.
- Browser telemetry authorization and sanitization.
- Trusted proxy handling.
- Forwarded-IP spoofing prevention.
- Persistent cache behavior.
- Flight scheduler behavior.
- Suppression of AeroDataBox calls outside the 48-hour window.
- Python and JavaScript syntax validation.
- Release package integrity.

---

## 1.0.3 - 2026-08-16

v1.0.3 introduced the corrected flight-refresh model and trusted-proxy client-IP logging that are included in v1.0.4. It is retained here as a historical development/release entry even though v1.0.4 supersedes it.

### Added

- Added detailed flight refresh-decision logging.
- Added separate browser-check and provider-refresh countdown information.
- Added request correlation IDs for server-side operations.
- Added proxy-aware client IP logging.
- Added trusted proxy CIDR configuration and spoofing protection.
- Added Cloudflare + Apache `mod_remoteip` deployment guidance.

### Changed

- Flights more than 48 hours away now perform local/browser state checks without querying AeroDataBox.
- Browser check cadence changed to 10 minutes for far-future flights and 1 minute inside the live-data window.
- AeroDataBox provider TTL behavior changed to 30 minutes / 5 minutes / 1 minute depending on proximity to departure and active-flight state.

### Fixed

- Fixed incorrect two-hour AeroDataBox refresh behavior for flights more than 48 hours away.
- Fixed misleading flight-refresh countdown semantics.
- Fixed logging of the reverse proxy address as the visitor address when a trusted proxy chain is configured correctly.

---

## 1.0.2 - 2026-08-16

### Fixed

- Corrected AeroDataBox secret-source logging so file-backed keys report `secret-file` rather than `environment`.
- Added environment fallback if a configured secret file cannot be read.

### Changed

- Public repository packaging requires an explicit `PUBLIC_ORIGIN`; no deployment-specific hostname is embedded.
- Standardized plugin and companion release metadata on 1.0.2.

---

## 1.0.1 - 2026-08-16

### Added

- Security redesign using short-lived Guest Portal sessions.
- Secret-file support for Immich and AeroDataBox.
- Content Security Policy and additional response-security headers.
- Minimal public health endpoint.

### Changed

- Native TREK/Journey share tokens are exchanged once through a JSON POST and later guest API URLs are token-free.
- Recommended runtime no longer mounts TREK plugin-data.
- Container defaults use a non-root user, read-only root filesystem, dropped capabilities, and `no-new-privileges`.
- Public errors are generic while detailed failures are retained in server logs.
- Plugin iframe messaging is pinned to the established TREK parent origin.

---

## 0.3.x

Development series that introduced Mapbox Plan parity, date-grouped Photos, Flights/Reservations separation, live flight refresh, provider rate limiting, operational logging, and persistent flight caching.

Pre-1.0 deployments should follow the migration guidance in `docs/UPGRADING.md`.

---

## Maintaining this file

For future work:

1. Add pending user-visible changes under **Unreleased**.
2. Use the headings **Added**, **Changed**, **Fixed**, **Security**, and **Removed** as appropriate.
3. Do not put credentials, share tokens, API keys, production hostnames, or private infrastructure details in changelog entries.
4. When publishing a release, replace the Unreleased entries with a new section:

   ```text
   ## X.Y.Z - YYYY-MM-DD
   ```

5. Recreate empty **Unreleased** headings for the next development cycle.
6. Keep older release entries in this file so GitHub users have one continuous project history.
