# Flights

Guest Portal renders transport reservations from the native TREK public share. Optional live data is enriched server-side with AeroDataBox and adsb.fi.

## Sparse TREK v4 flight records

TREK v4 reservations may provide only partial flight metadata. Guest Portal builds ordered legs from explicit metadata when available and fills missing first, intermediate, or final segments from ordered reservation endpoints and adjacent reservation fields. Normalization is capped at six legs per reservation to prevent malformed shared data from expanding the itinerary indefinitely.

## Cars and taxis

Car and taxi reservations remain part of the Plan timeline but are excluded from the Flights tab. When present, they appear in the dedicated **Cars & Taxis** tab with their route, dates, location, status, and notes.

## Native sharing requirement

The TREK public share must enable Bookings. If Bookings are not shared, Guest Portal cannot expose Flights or Reservations.

## Browser checks vs provider calls

Guest Portal deliberately separates **browser/local checks** from **external provider calls**.

Default scheduling:

- more than 48 hours before departure: browser checks every 10 minutes; AeroDataBox is not queried;
- 12–48 hours: provider data may refresh about every 30 minutes;
- 3–12 hours: provider data may refresh about every 5 minutes;
- less than 3 hours: provider data may refresh about every minute;
- active/boarding/en-route: provider data may refresh about every minute;
- completed flights: automatic live refreshing stops.

The server's cache/provider TTL is authoritative. Multiple guests watching the same flight do not independently consume provider quota each time their browsers poll.

## Caches

Guest Portal uses:

1. in-memory cache for hot requests;
2. its own persistent SQLite cache at `/cache/guest-portal.db` for restart-safe provider reuse.

The companion does not read another plugin's cache database at runtime.

## Flight-number sources

Flight numbers are derived from the shared TREK reservation metadata/endpoints. Previously normalized Guest Portal cache data may be reused as a reference when available.

## AeroDataBox secret

The supplied Compose deployment reads the provider key from:

```text
./secrets/aerodatabox_api_key
```

inside the container:

```text
/run/secrets/aerodatabox_api_key
```

An empty file disables live AeroDataBox lookup.

## adsb.fi

When AeroDataBox returns enough aircraft identity information, Guest Portal may query adsb.fi for current aircraft position/registration/callsign details. adsb.fi failures do not invalidate the underlying scheduled flight card.

## Error behavior

Provider failures are logged server-side. The browser receives generic status information rather than raw provider errors. A previously cached Guest Portal payload can be used as a temporary fallback when a refresh fails.

## Configuration

See [CONFIGURATION.md](CONFIGURATION.md) for:

- `FLIGHT_API_WINDOW_HOURS`
- `FLIGHT_UPCOMING_POLL_SECONDS`
- `FLIGHT_ACTIVE_POLL_SECONDS`
- `FLIGHT_ERROR_POLL_SECONDS`
- `LIVE_FLIGHT_MAX_CACHE`
- `AERODATABOX_TIMEOUT`
- `AERODATABOX_MIN_INTERVAL`
- `AERODATABOX_429_RETRIES`
- `AERODATABOX_429_BACKOFF`
- `ADSB_TIMEOUT`
- `GUEST_CACHE_MAX_ROWS`
