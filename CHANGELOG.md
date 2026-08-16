# Changelog

All notable public releases are documented here.

## 1.0.2

- Corrected AeroDataBox secret-source logging so file-backed keys report `secret-file`.
- Added environment fallback if a configured secret file cannot be read.
- Public repository packaging now requires explicit `PUBLIC_ORIGIN`; no deployment-specific hostname is embedded.
- Standardized plugin/companion release metadata on 1.0.2.

## 1.0.1

- Security redesign: native share tokens exchanged once for HttpOnly guest sessions; later API URLs are token-free.
- Removed runtime TREK plugin-data mount from the recommended deployment.
- Added secret-file support for Immich and AeroDataBox.
- Added CSP and additional security headers.
- Added non-root/read-only/capability-dropped container defaults.
- Added minimal public health output and generic public errors.
- Pinned plugin iframe messaging to the established TREK parent origin.

## 0.3.x

Development series that introduced Mapbox Plan parity, date-grouped Photos, Flights/Reservations split, live flight refresh, rate limiting, logging, and persistent flight caching. Pre-1.0 deployments should follow the migration warning in `docs/UPGRADING.md`.
