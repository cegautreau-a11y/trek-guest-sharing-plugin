# TREK Guest Portal

> Unofficial, third-party guest sharing extension for the self-hosted [TREK travel planner](https://github.com/liketrek/TREK).

TREK Guest Portal turns TREK's native public trip/Journey shares into a richer, mobile-friendly guest site without modifying the TREK application image. It is designed for Docker/Portainer deployments and keeps the guest-facing web app in a separate hardened companion container.

**Current release:** `1.0.2`  
**TREK compatibility:** `>=3.4.0 <4.0.0`  
**License:** MIT

## What guests see

Guest Portal intentionally exposes only four sections:

- **Plan** — day-by-day itinerary with a Mapbox map rendered directly under the selected stop; the selected location is framed to a fixed 1 km geographic radius on desktop and mobile.
- **Flights** — scheduled TREK transport cards plus optional live AeroDataBox/adsb.fi status, rate-limited auto refresh, persistent caching, and a visible next-refresh countdown.
- **Reservations** — accommodations and non-transport bookings. Transportation stays under Flights.
- **Photos** — Journey photos grouped chronologically; with Immich configured, the companion resolves original capture dates server-side from the Immich asset metadata.

Flights and Reservations remain visible even when empty. Journal, Packing, Budget, Collab, and external “Open in Maps” links are not shown.

## Why there are two components

```text
TREK authenticated UI
       │
       │ Admin-uploadable trip-page plugin
       ▼
Guest Portal configuration
       │ creates owner guest URL
       ▼
https://trek.example.com/guest-portal/#trip=...&journey=...
       │
       ▼
Reverse proxy (/guest-portal/)
       │
       ▼
Hardened companion container
       │
       ├── validates native TREK/Journey public shares
       ├── creates short-lived HttpOnly guest sessions
       ├── renders Plan / Flights / Reservations / Photos
       ├── talks to Mapbox from the browser
       ├── queries Immich server-side for capture dates (optional)
       └── queries AeroDataBox/adsb.fi server-side for live flights (optional)
```

TREK plugins are intentionally sandboxed and are not meant to serve arbitrary anonymous HTML from plugin API routes. The companion exists so the public guest site can be served normally while the TREK plugin remains update-safe.

## Start here

If you have never installed Guest Portal before, follow these in order:

1. **[Prerequisites](docs/PREREQUISITES.md)** — confirm TREK, Docker, HTTPS, Mapbox, and optional provider requirements.
2. **[Fresh installation](docs/INSTALL.md)** — complete first-time install from zero.
3. **[Configuration reference](docs/CONFIGURATION.md)** — environment variables, secret files, Mapbox settings, and sessions.
4. **[Reverse proxy](docs/REVERSE-PROXY.md)** — Apache and Nginx examples.
5. **[Troubleshooting](docs/TROUBLESHOOTING.md)** — startup logs, 503s, missing Mapbox, Immich dates, flight rate limits, and cache permissions.

Existing pre-1.0 users should read **[UPGRADING.md](docs/UPGRADING.md)** before replacing files.

## Repository layout

```text
.
├── README.md                       # project overview / entry point
├── SECURITY.md                     # security model + reporting guidance
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── VERSION
├── plugin/                         # TREK Admin-uploadable trip-page plugin
│   ├── trek-plugin.json
│   ├── package.json
│   ├── client/
│   └── server/
├── companion/                      # public guest-site companion
│   ├── public/
│   ├── server/
│   ├── tools/
│   ├── docker-compose.yml
│   └── Dockerfile
├── docs/                           # installation/operations documentation
├── examples/                       # copy/paste proxy + Portainer examples
├── scripts/                        # validation and release packaging
├── tests/                          # security/session smoke tests
├── .github/                        # CI, issue templates, release workflow
└── dist/                           # locally generated release ZIPs (gitignored)
```

## Security design

The v1.x line was redesigned so native TREK/Journey bearer tokens do not appear in ordinary guest API URLs:

1. the owner-generated URL carries native share capabilities only in the URL fragment (`#...`);
2. the browser sends them once in a JSON body to `POST /api/session`;
3. the companion validates them against TREK;
4. the browser receives a random `HttpOnly; Secure; SameSite=Strict` session cookie;
5. the fragment is removed from the visible URL/history;
6. later requests use token-free endpoints such as `/api/trip`, `/api/flights/<id>`, and `/api/photo-dates`.

The recommended runtime does **not** mount TREK's database, uploads directory, plugin directory, or plugin-data directory. Provider credentials are mounted as read-only secret files. See [SECURITY.md](SECURITY.md) for the full threat model.

## Build release artifacts

No npm install is required to package the current source.

```bash
./scripts/validate.sh
./scripts/package-release.sh
```

Generated artifacts appear under `dist/`:

```text
trek-guest-portal-1.0.2.zip
trek-guest-portal-companion-1.0.2-portainer.zip
trek-guest-portal-1.0.2-complete-bundle.zip
```

The first ZIP is uploaded through TREK Admin. The second is extracted onto the Docker host. The complete bundle contains both plus the install documentation.

## External services

Guest Portal can operate with only TREK + Mapbox. Additional integrations are optional:

- **Immich** — original capture-date lookup for Journey photos.
- **AeroDataBox** — live flight schedule/status information.
- **adsb.fi** — live aircraft position information when available.
- **Flight Tracker TREK plugin** — not required at runtime. The bundled one-shot helper can copy its existing AeroDataBox key into Guest Portal's dedicated secret file when that key is stored in the plugin's own SQLite `kv` table.

## Compatibility and support boundary

This project is not part of TREK and is not endorsed by its maintainers. TREK's plugin API and public-share schema can change. The manifest intentionally prevents installation on TREK `4.x` until compatibility is reviewed.

Useful upstream documentation:

- [TREK Wiki](https://github.com/liketrek/TREK/wiki)
- [TREK Plugins](https://github.com/liketrek/TREK/wiki/Plugins)
- [TREK Public Share Links](https://github.com/liketrek/TREK/wiki/Public-Share-Links)
- [TREK Security Hardening](https://github.com/liketrek/TREK/wiki/Security-Hardening)
- [Mapbox access-token security](https://docs.mapbox.com/help/dive-deeper/how-to-use-mapbox-securely/)
- [Immich API documentation](https://docs.immich.app/api/)
- [AeroDataBox API documentation](https://doc.aerodatabox.com/)

## License

MIT. See [LICENSE](LICENSE).
