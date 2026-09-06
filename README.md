# TREK Guest Portal

> Hardened guest-sharing extension for the self-hosted [TREK travel planner](https://github.com/liketrek/TREK), providing a polished mobile-friendly portal for shared trips, flights, reservations, and Journey photos.

TREK Guest Portal turns TREK's native public trip and Journey shares into a richer guest site without modifying the TREK application image. The project has two components: an Admin-uploadable TREK plugin that stores per-trip share configuration, and a hardened companion container that serves the anonymous guest experience.

**Current release:** `2.1.0`
**TREK compatibility:** `>=4.0.0 <5.0.0`
**Deployment:** Docker Compose  
**License:** MIT

## Guest experience

Guest Portal intentionally exposes four sections:

- **Plan** — a unified day-by-day timeline combining planned stops, flights/transport, bookings, and accommodation **Check-in / Stay / Check-out** events. Place-linked bookings remain attached to their stop, flights are positioned between matching departure/arrival airport places when possible, and Mapbox retains inline selected-stop focus with a fixed 1 km framing radius.
- **Flights** — TREK transport cards plus optional AeroDataBox/adsb.fi live data, quota-aware provider scheduling, in-memory and persistent caching, and separate browser/provider countdowns.
- **Reservations** — accommodations (including standalone `hotel` bookings) and other non-transport bookings in separate sections. Linked TREK Hotel partner records are deduplicated.
- **Photos** — Journey gallery media grouped chronologically; optional Immich integration resolves original asset capture dates server-side.

Flights and Reservations remain visible even when empty. Journal, Packing, Budget, Collab, and external "Open in Maps" links are intentionally omitted from the guest view.

> Share reminder: Guest Portal only shows flights, reservations, and photos when the native TREK share permissions are enabled. Make sure Bookings is shared for the trip and Gallery is shared for the Journey before expecting those sections to appear.

Booking confirmation codes, confirmation numbers, booking references, and equivalent reservation reference identifiers are intentionally **not exposed anywhere in the Guest Portal UI or guest `/api/trip` response**.

## Architecture

```text
TREK authenticated UI
       │
       │ Admin-uploadable Guest Portal plugin
       ▼
Per-trip Guest Portal configuration
       │ generates owner guest URL
       ▼
https://guest.example.com/#trip=...&journey=...
       │
       ▼
HTTPS reverse proxy
       │
       ▼
TREK Guest Portal companion container
       │
       ├── validates native TREK/Journey public shares
       ├── exchanges share capabilities for an HttpOnly guest session
       ├── renders Plan / Flights / Reservations / Photos
       ├── talks to Mapbox from the browser
       ├── queries Immich server-side for capture dates (optional)
       └── queries AeroDataBox/adsb.fi server-side for live flights (optional)
```

The companion does **not** mount or read TREK plugin databases at runtime. Provider credentials are stored in dedicated mounted secret files. A bundled one-shot helper can read an existing Flight Tracker database manually to copy only its AeroDataBox key before the public container starts.

## Recommended origin layout

TREK is a PWA and may register a Service Worker on its own origin. For reliable guest navigation in the same browser that is already signed in to TREK, use a dedicated Guest Portal hostname:

```text
TREK:         https://trek.example.com/
Guest Portal: https://guest.example.com/
```

Recommended `.env` values:

```dotenv
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

A same-origin `/guest-portal/` deployment remains supported when required. See [docs/GUEST-ORIGIN.md](docs/GUEST-ORIGIN.md) and [docs/REVERSE-PROXY.md](docs/REVERSE-PROXY.md).

## Docker Compose deployment

The companion release is a standalone Docker Compose project. It joins TREK's existing Docker network so `TREK_HOST=app` can resolve through Docker DNS without modifying TREK's Compose file.

After extracting the companion release:

```bash
cd /opt/trek-guest-portal
cp .env.example .env
nano .env
```

Set `TREK_DOCKER_NETWORK` to the Docker network used by the running TREK application, then validate and start:

```bash
docker compose config
docker compose up -d
docker compose ps
docker compose logs --tail=100 trek-guest-portal
```

The full first-time procedure is in [docs/INSTALL.md](docs/INSTALL.md).

## Start here

New installations should follow these documents in order:

1. [Prerequisites](docs/PREREQUISITES.md)
2. [Fresh installation](docs/INSTALL.md)
3. [Configuration reference](docs/CONFIGURATION.md)
4. [Dedicated guest origin](docs/GUEST-ORIGIN.md)
5. [Reverse proxy](docs/REVERSE-PROXY.md)
6. [Logging](docs/LOGGING.md)
7. [Troubleshooting](docs/TROUBLESHOOTING.md)

Existing installations should read [UPGRADING.md](docs/UPGRADING.md) before replacing files.

## Repository layout

```text
.
├── README.md
├── CHANGELOG.md
├── SECURITY.md
├── VERSION
├── .env.example
├── companion/
│   ├── docker-compose.yml
│   ├── README.md
│   ├── public/
│   ├── server/
│   └── tools/
├── plugin/
│   ├── client/
│   ├── server/
│   ├── trek-plugin.json
│   └── package.json
├── docs/
├── examples/
├── scripts/
└── tests/
```

`companion/public/config.js` is deployment-local and intentionally excluded from source control. Copy `config.js.example` during installation.

## Release artifacts

A release build produces:

```text
trek-guest-portal-2.1.0.zip
trek-guest-portal-companion-2.1.0.zip
trek-guest-portal-2.1.0-complete-bundle.zip
```

- `trek-guest-portal-2.1.0.zip` — upload directly through **TREK → Admin → Plugins**.
- `trek-guest-portal-companion-2.1.0.zip` — extract on the Docker host and deploy with `docker compose`.
- `trek-guest-portal-2.1.0-complete-bundle.zip` — plugin, companion and repository documentation in one archive.

`dist/SHA256SUMS` is generated alongside the release artifacts.

## Security model

Native TREK/Journey share capabilities appear only in the owner-generated URL fragment. The browser sends them in the JSON body of `POST /api/session`; the companion validates the underlying shares and issues a `Secure; HttpOnly; SameSite=Strict` guest-session cookie. The fragment is intentionally retained in the address bar so refreshing the page can reconstruct a lost/restarted session. URL fragments are not included in normal HTTP request URLs, and later Guest Portal API requests use the session cookie rather than placing bearer tokens in API URLs. Anyone who obtains the complete owner-generated URL has the same read-only bearer access as the underlying shares.

Additional hardening includes:

- exact HTTPS origin validation;
- same-origin checks for session creation, logout and browser telemetry;
- bounded/rate-limited memory-only guest sessions;
- non-root container execution;
- read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- hardened tmpfs;
- dedicated secret-file mounts;
- persistent Guest Portal-owned SQLite cache only;
- trusted-proxy CIDR validation before forwarded client IPs are accepted;
- CSP and additional security response headers;
- structured logging with sensitive-field filtering and token fingerprinting.

Read [SECURITY.md](SECURITY.md) before exposing Guest Portal publicly.

## Logging and diagnostics

The companion emits correlated operational logs for HTTP requests, guest sessions, TREK public-share reads, Mapbox/browser activity, live flight scheduling/providers, reservations, Journey/Immich photo handling, caches, proxy identity and runtime health. Browser telemetry uses a session-protected same-origin endpoint. Sensitive values are excluded, redacted, or represented only by short hashes.

```bash
docker compose logs -f trek-guest-portal
```

See [docs/LOGGING.md](docs/LOGGING.md) for configuration and event families.

## Development and validation

Run the source validation and regression suite before packaging:

```bash
./scripts/validate.sh
python3 -m unittest discover -s tests -v
./scripts/package-release.sh
```

The validation script checks Python/JavaScript syntax, version consistency, Docker Compose/documentation consistency, release package references, forbidden deployment-specific strings, and stale project version references.

## Project status

TREK Guest Portal is an unofficial third-party extension and is not part of the upstream TREK project. Re-test compatibility before upgrading to a new TREK major release.
