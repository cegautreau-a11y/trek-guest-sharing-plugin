# Flights

## What the tab contains

The Flights tab contains TREK transportation reservations. Air reservations can additionally show live schedule/status data. Transportation is deliberately excluded from the Reservations tab.

## Provider flow

```text
TREK public share transport
       │
       ├── scheduled card always available when shared
       │
       └── optional AeroDataBox refresh
                 │
                 ├── normalized response
                 ├── optional adsb.fi position
                 └── Guest Portal SQLite cache
```

## Refresh cadence

| State / time to departure | Target refresh |
|---|---:|
| >48 hours | 2 hours |
| 12–48 hours | 30 minutes |
| 3–12 hours | 5 minutes |
| <3 hours | 1 minute |
| boarding / departed / en route / approaching / diverted | 1 minute |
| unknown departure | 5 minutes |
| completed/past | no continuous polling |

The page displays **Next auto refresh** and counts down every second while Flights is open.

## Rate-limit protection

- one global AeroDataBox request queue per companion;
- default minimum interval 1.6 seconds;
- automatic HTTP 429 backoff/retry;
- persistent cache shared by all guests;
- cached data reused across restarts until stale according to the refresh policy.

Provider quotas are account-level. If another application uses the same API key, it can still contribute to quota/rate-limit pressure.

## Persistent cache

Default:

```text
/cache/guest-portal.db
```

The cache belongs only to Guest Portal. The runtime never writes the Flight Tracker plugin database.
