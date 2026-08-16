# Guest Portal Companion

The companion is the public, hardened read-only web application that runs next to TREK.

It provides:

- Plan / Flights / Reservations / Photos;
- short-lived HttpOnly guest sessions;
- Mapbox Plan display;
- optional Immich capture-date lookups;
- optional AeroDataBox + adsb.fi live-flight data;
- persistent Guest Portal-owned flight cache;
- structured container logging.

## Recommended deployment

Use `docker-compose.yml` as a service in the same Portainer/Compose stack as TREK. Copy `public/config.js.example` to a local `public/config.js` and set a dedicated public Mapbox token.

See [docs/INSTALL.md](../docs/INSTALL.md) and [docs/CONFIGURATION.md](../docs/CONFIGURATION.md).
