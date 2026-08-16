# Flights

## What the tab contains

The Flights tab contains TREK transportation reservations. Air reservations can additionally show live schedule/status data from AeroDataBox and airborne position data from adsb.fi. Transportation is deliberately excluded from the Reservations tab.

## Refresh model

Guest Portal v1.0.4 separates **browser checks** from **external provider calls**. This is important for AeroDataBox BASIC quotas.

### Browser check cadence

| Flight state | Guest browser check |
|---|---:|
| More than 48 hours before departure | 10 minutes |
| Within 48 hours of departure | 1 minute |
| Completed / all legs arrived | stopped |
| Temporary provider error | 5 minutes by default |

A browser check does **not** necessarily call AeroDataBox. Most checks are served from memory or the persistent SQLite cache.

### AeroDataBox call cadence

Guest Portal does not call AeroDataBox at all while the best-known departure time is more than 48 hours away.

Once inside the 48-hour live-data window, the provider cache follows the same time-to-departure curve used by Flight Tracker:

| State / time to departure | Provider cache TTL |
|---|---:|
| More than 48 hours | **No AeroDataBox request** |
| 12–48 hours | 30 minutes |
| 3–12 hours | 5 minutes |
| Less than 3 hours | 1 minute |
| Boarding / departed / en route / approaching / diverted | 1 minute |
| Unknown departure time | 5 minutes |
| Completed / all legs arrived | no continuous refresh |

This means a guest may see **Next auto refresh in 1 minute** while the companion still serves a 20-minute-old provider result because its 30-minute provider TTL has not expired.

## Flights header

The page shows two timing concepts:

- **Next auto refresh** — the next browser/server check.
- **Live provider window opens in ...** or **Next provider refresh due in ...** — when AeroDataBox may actually be contacted.

For a flight 72 hours away, a typical display is approximately:

```text
Next auto refresh
in 10m 0s
Live provider window opens in 1d 0h 0m
```

## Provider flow

```text
TREK public share transport
       │
       ├── >48h: schedule-only response, no AeroDataBox call
       │
       └── <=48h: provider refresh allowed
                 │
                 ├── memory cache
                 ├── persistent Guest Portal SQLite cache
                 ├── AeroDataBox when provider TTL expires
                 ├── optional adsb.fi position near/in flight
                 └── normalized response
```

## Rate-limit protection

- one process-wide AeroDataBox request queue;
- default minimum interval 1.6 seconds;
- sequential flight-leg lookups;
- automatic HTTP 429 backoff/retry;
- persistent cache shared by all guests;
- browser polling does not bypass provider TTLs;
- no AeroDataBox calls outside the configured live-data window.

Provider quotas are account-level. Another application using the same API key can still contribute to rate-limit pressure.

## Scheduler settings

| Variable | Default | Meaning |
|---|---:|---|
| `FLIGHT_API_WINDOW_HOURS` | `48` | Hours before departure when AeroDataBox calls may begin. |
| `FLIGHT_UPCOMING_POLL_SECONDS` | `600` | Guest check interval before the live window. |
| `FLIGHT_ACTIVE_POLL_SECONDS` | `60` | Guest check interval inside the live window. |
| `FLIGHT_ERROR_POLL_SECONDS` | `300` | Retry interval after a temporary live-data failure. |

## Persistent cache

Default:

```text
/cache/guest-portal.db
```

The cache belongs only to Guest Portal and persists normalized provider payloads across container restarts. It does not store AeroDataBox or Immich API keys.

## Diagnosing refresh decisions

At `LOG_LEVEL=INFO`, each flight request produces scheduler events containing fields such as:

```text
flight.refresh_decision ... decision=suppress-aerodatabox phase=upcoming hours_to_departure=93.4 api_window_open=False api_window_opens_in=163440 api_ttl=7200 api_due_in=163440 poll_after=600
```

or:

```text
flight.refresh_decision ... decision=call-aerodatabox phase=active hours_to_departure=20.3 api_window_open=True api_ttl=1800 api_due_in=0 poll_after=60
```

See [LOGGING.md](LOGGING.md) for the complete logging guide.
