# Architecture

## Components

### TREK plugin

The Admin-uploadable `trip-page` plugin:

- runs inside TREK's plugin sandbox;
- stores per-trip Guest Portal configuration in its own isolated plugin database;
- requires `db:own` and `db:read:trips`;
- lets the trip owner paste the native trip share and optional Journey share;
- produces one owner guest URL.

It does **not** serve the anonymous Guest Portal page from a plugin API route.

### Companion

The companion is the internet-facing read-only presentation layer. It:

- serves the static HTML/CSS/JavaScript guest UI;
- validates native public shares against TREK;
- creates short-lived guest sessions;
- proxies only the public data needed by the guest UI;
- performs optional server-side Immich and AeroDataBox lookups;
- stores only its own persistent flight cache.

## Authorization flow

```text
owner-generated URL fragment
  #trip=<native-share>&journey=<native-share>
                │
                ▼
POST /api/session JSON body
                │
                ▼
companion validates TREK trip share
and optional Journey share
                │
                ▼
random server-side session
                │
                ▼
HttpOnly Secure SameSite=Strict cookie
                │
                ▼
token-free guest API URLs
```

The browser removes the original fragment after a successful session exchange.

## Data sources by tab

### Plan

TREK native public trip share + Mapbox GL JS in the guest browser.

### Flights

TREK public transport data, optional AeroDataBox live status, optional adsb.fi aircraft data, and Guest Portal's persistent cache.

### Reservations

TREK public accommodations and non-transport booking data.

### Photos

TREK public Journey gallery data. If an Immich provider/asset ID is present and Immich is configured, the companion retrieves asset metadata server-side to determine original capture dates.

## Runtime mounts

Recommended runtime mounts only:

```text
public/                  -> /srv/public:ro
server/                  -> /srv/server:ro
cache/                   -> /cache:rw
aerodatabox secret file -> /run/secrets/aerodatabox_api_key:ro
immich secret file      -> /run/secrets/immich_api_key:ro
```

TREK databases, uploads, plugin source, and `plugins-data` are not mounted into the long-running public container.
