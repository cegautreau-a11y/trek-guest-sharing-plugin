# Architecture

TREK Guest Portal deliberately separates authenticated TREK administration from anonymous guest delivery.

## Components

### TREK plugin

The plugin runs inside TREK and requests only:

```text
db:own
db:read:trips
```

It stores per-trip Guest Portal configuration, including the native TREK share capability, optional Journey share capability, display title and configured guest base URL. It does not serve the anonymous guest application.

### Companion container

The companion is a small Python HTTP service that:

- serves static guest assets;
- validates native TREK/Journey public shares through TREK's anonymous endpoints;
- exchanges share capabilities for memory-only HttpOnly guest sessions;
- returns authorized trip/Journey data to the browser;
- proxies authorized Journey media;
- optionally queries AeroDataBox/adsb.fi for live flight data;
- optionally queries Immich for original asset capture dates;
- maintains its own persistent live-flight SQLite cache;
- emits structured operational logs and browser telemetry.

The companion does not mount or read TREK plugin databases at runtime.

## Guest timeline normalization

The Plan tab is the chronological guest overview. For each shared trip day, the browser merges planned place assignments with shared transport reservations, non-transport bookings, and accommodations. Accommodation ranges are represented on each covered day as check-in, stay, or check-out entries.

TREK accommodations normally have a linked `hotel` reservation. Guest Portal treats the native accommodation as authoritative and suppresses that linked Hotel record to prevent duplication. When TREK v4 omits `reservation_id` linkage, the companion also compares shared place identity, normalized name, and address before including a standalone Hotel record. Accommodation Check-in/Check-out values can be reconstructed from time-only fields plus TREK day IDs or place assignments; when only a start day exists, the following trip day is used for checkout. A standalone reservation whose type is `hotel` is classified as an accommodation so it appears under **Accommodations** rather than **Bookings**. The dedicated Flights, Cars & Taxis, and Reservations tabs remain available for richer detail.

## Docker topology

The companion is deployed as its own Docker Compose project and attaches to TREK's existing Docker network:

```text
TREK Compose project                  Guest Portal Compose project
┌──────────────────────┐              ┌──────────────────────────┐
│ app:3000             │              │ trek-guest-portal:8080   │
│ alias: app           │◄─────────────│ TREK_HOST=app            │
└──────────┬───────────┘ shared       └────────────┬─────────────┘
           │               network                 │
           └───────────────────────────────────────┘
```

`TREK_DOCKER_NETWORK` tells the Guest Portal Compose project which existing Docker network to join.

The companion publishes container TCP/8080 as host TCP/8088 on a controlled interface. The HTTPS reverse proxy is the only intended consumer of that published port.

## Browser/session flow

The owner-generated guest URL contains native share capabilities after `#`:

```text
https://guest.example.com/#trip=...&journey=...
```

URL fragments are not included in the browser's initial HTTP request. The guest application reads the fragment and sends the values once in a JSON body:

```text
POST /api/session
```

The companion validates the native shares with TREK and returns a random session cookie:

```text
Secure
HttpOnly
SameSite=Strict
```

The browser intentionally keeps the share capabilities in the URL fragment so a refresh can repeat the session exchange if the in-memory session or cookie is gone. The fragment is not included in ordinary HTTP request URLs. Later Guest Portal API requests use only the session cookie:

```text
GET /api/trip
GET /api/journey
GET /api/flights/<reservation-id>
GET /api/photo-dates
GET /api/photos/<photo-id>/thumbnail
GET /api/photos/<photo-id>/original
```

Sessions are memory-only. A page refresh can reconstruct a session from the retained fragment. Native TREK/Journey share revocation remains authoritative, and the complete owner-generated Guest Portal URL must be protected as a bearer capability.

## Calendar tab

The Calendar tab provides an iCal/WebCal feed URL for subscribing to the trip itinerary in calendar applications. The feed is generated server-side and validated against TREK on every request.

```
GET /calendar/<trip_token>.ics
GET /api/ical-link
```

The iCal feed:
- follows RFC 5545 with VTIMEZONE components for proper timezone support;
- converts flight departure/arrival times to the respective airport's local timezone;
- uses `ICAL_TIMEZONE` as fallback for accommodations and when per-event timezone resolution fails;
- supports Apple Calendar (macOS/iOS) and any CalDAV client that respects VTIMEZONE;
- requires a valid guest session.

The companion also serves a WebCal URL for direct calendar subscription:
```
GET /ical/<trip_token>
```
This redirects to the `webcal://` variant of the iCal feed URL.

## Origin separation

Recommended browser origins:

```text
https://trek.example.com/   authenticated TREK PWA
https://guest.example.com/  anonymous Guest Portal
```

The dedicated guest origin isolates TREK's Service Worker, authenticated browser state and host-only cookies from Guest Portal.

`PUBLIC_ORIGIN` identifies the guest browser origin. `TREK_PUBLIC_ORIGIN` identifies the TREK browser origin used for server-side Host/X-Forwarded-Host validation.

## Data boundaries

### Browser receives

- shared trip/Journey data already authorized by native public shares;
- short-lived/live provider data needed for the guest UI;
- public Mapbox configuration;
- media responses authorized by the guest session.

### Browser does not receive

- AeroDataBox API key;
- Immich API key;
- TREK plugin database contents;
- authenticated TREK APIs;
- raw server-side guest session IDs via JavaScript (cookie is HttpOnly).

## Persistent state

The companion's only normal writable application state is:

```text
/cache/guest-portal.db
```

This database stores Guest Portal's live-flight cache. Sessions remain in memory and are intentionally lost on container restart.

Provider secrets are mounted read-only from dedicated host files.

## Optional key extraction

The `tools/` directory contains one-shot administrative helpers that can scan an existing Flight Tracker plugin database while the administrator explicitly runs them. Their purpose is to copy only the provider key into Guest Portal's dedicated secret file. The public companion container is not granted that plugin-data mount.

## Security boundaries

- TREK remains authoritative for public-share validity.
- The reverse proxy terminates public HTTPS.
- The companion validates exact browser origin for session-mutating operations.
- Forwarded client IPs are accepted only from configured trusted proxy CIDRs.
- Provider keys remain server-side.
- Browser telemetry is authenticated, same-origin, rate-limited and sanitized.
- Static file resolution is constrained to `PUBLIC_ROOT`.
