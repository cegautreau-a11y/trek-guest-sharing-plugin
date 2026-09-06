# Changelog

All notable project changes represented by TREK Guest Portal releases are documented here. Earlier pre-1.1 development history remains consolidated in the 1.1.0 feature/security baseline.

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

## 3.0.3

### Fixed

- **Base-path routing**: Fixed iCal feed route when the companion is mounted under a URL prefix (e.g. `/guest-plugin/`). The companion now strips the `GUEST_PLUGIN_PATH` prefix before route matching.
- **iCal times**: Removed incorrect UTC (`Z`) suffix from iCal datetimes. Times are now emitted as naive local values with `X-WR-TIMEZONE` property. Set `ICAL_TIMEZONE` env var to match the trip's primary timezone.
- **webcal_url**: The URL returned by `/api/ical-link` now correctly includes the `GUEST_PLUGIN_PATH` prefix.

### Changed

- Added `GUEST_PLUGIN_PATH` env var (default `/guest-plugin/`). Set this to match the reverse-proxy mount point.
- Added `ICAL_TIMEZONE` env var (default `UTC`). Set to the IANA timezone of the trip (e.g. `America/New_York`).
- Companion server VERSION bumped to 3.0.1.
- Companion public app.js and config.js cache-busting version bumped to 3.0.1.
- Plugin version bumped to 3.0.1.

---

## 3.0.0

---

## 2.1.1 - 2026-09-06

### Added

- Added a dedicated **Cars & Taxis** tab for car and taxi reservations.

### Changed

- Removed cars and taxis from the Flights tab while retaining them in the Plan timeline.

### Validation

- Synchronized active package, runtime, documentation, and cache-busting version markers to 2.1.1.
- Updated the documentation audit to cover current v4 behavior, root-level Compose usage, and release packaging cleanup.

---

## 2.1.0 - 2026-09-06

This release consolidates the guest-portal navigation and TREK v4 accommodation fixes after the 2.0.x maintenance releases.

### Added

- Added a dedicated **Cars & Taxis** tab for car and taxi reservations.

### Changed

- Removed cars and taxis from the Flights tab while retaining them in the Plan timeline.
- Updated the release documentation and artifact names to the current 2.1.1 version.
- Packaging now removes older versioned ZIPs and stale checksums before generating current release artifacts.

### Fixed

- Deduplicated native accommodations and standalone TREK v4 hotel reservations when `reservation_id` linkage is absent but the records share a place, name, or address.
- Restored correct accommodation Check-in and Check-out dates by combining time-only values with TREK day and place-assignment dates.

### Validation

- Revalidated Python and JavaScript syntax, TREK/plugin version consistency, Compose configuration, documentation links, package contents, and release hygiene.
- Rebuilt the plugin, companion, complete bundle, and checksum artifacts for 2.1.1.

---

## 2.0.4 - 2026-09-06

### Fixed

- Deduplicated native accommodations and standalone TREK v4 hotel reservations by shared place, name, and address identity when `reservation_id` linkage is absent, preventing duplicate hotel cards and conflicting date/time entries.
- Combined time-only accommodation values with TREK day dates so Check-in and Check-out show the correct stay dates instead of standalone times.

### Validation

- Rebuilt the plugin and companion release archives for 2.0.4.

---

## 2.0.3 - 2026-09-06

### Fixed

- Restored accommodation Check-in and Check-out dates for TREK v4 hotel reservations that provide dates through day IDs or place assignments instead of explicit reservation date fields.

### Validation

- Rebuilt the plugin and companion release archives for 2.0.3.

---

## 2.0.2 - 2026-09-06

### Added

- Added a root-level `docker-compose.yml` for deployments started from the project root.
- Added a required-version bug-report template and documented root-level Compose setup.

### Changed

- Updated plugin compatibility to support TREK `>=4.0.0 <5.0.0`.
- Updated release metadata, documentation, cache-busting markers, and package artifacts to 2.0.2.
- Added all Compose interpolation defaults to `.env.example`, including proxy-trust, cache, provider, and upstream timeout settings.
- Added clear Guest Portal guidance for enabling TREK Bookings and Journey Gallery sharing; native share permissions remain authoritative.
- Added accommodation fallback handling for TREK v4 shares that expose standalone `hotel` reservations instead of a separate accommodations collection.

### Fixed

- Completed sparse TREK v4 flight metadata from ordered endpoints so first, intermediate, and final itinerary legs render consistently.
- Filled missing leg fields from adjacent endpoint and reservation metadata while preserving the six-leg safety limit.
- Restored accommodation Check-in and Check-out dates for TREK v4 hotel records using shared day and place-assignment data when explicit reservation dates are absent.
- Kept local tests, virtual environments, and generated release archives out of Git.

### Validation

- Added regression coverage for endpoint-derived flight segments and partial explicit leg metadata.
- Revalidated Python and JavaScript syntax, TREK/plugin version consistency, Compose configuration, documentation links, package contents, and release hygiene.
- Rebuilt the plugin and companion archives as `trek-guest-portal-2.0.2.zip` and `trek-guest-portal-companion-2.0.2.zip`.

### Security

- Guest Portal remains read-only and continues to exchange native TREK/Journey share capabilities for an HttpOnly guest session.
- Share permissions are checked server-side; disabling Bookings or Gallery sharing prevents the corresponding guest data from being exposed.

---

## 1.2.2 - 2026-08-23

This maintenance release keeps the owner-generated Guest Portal share metadata in the browser URL after session establishment so an already-open guest page can be refreshed and reconstruct its session without requiring the user to locate the original share link again.

### Changed

- Guest Portal no longer rewrites a successful owner-generated URL back to the bare Guest Portal root after `POST /api/session`.
- The `#trip=...`, optional `journey=...`, and `title=...` fragment metadata remain in the address bar for the lifetime of the page/navigation entry.
- Refreshing the retained Guest Portal URL now reuses those fragment capabilities to establish a fresh HttpOnly guest session before loading trip/Journey data.
- Updated security, architecture, installation, upgrade, and troubleshooting documentation to describe the retained-fragment refresh model accurately.

### Fixed

- Fixed browser refreshes failing after the first successful page load because the share fragment had been removed with `history.replaceState(...)`.
- Fixed companion restarts or lost guest cookies forcing users to recover the original owner-generated share URL even when the Guest Portal tab was still open.

### Validation

- Added regression coverage requiring the share fragment to remain intact after session establishment and prohibiting the old fragment-stripping `history.replaceState(...)` behavior.
- Revalidated JavaScript/Python syntax, Docker Compose/documentation consistency, release metadata, archive hygiene, and the complete automated test suite.

### Security

- Native TREK/Journey capabilities remain in the URL fragment, which browsers do not include in normal HTTP request URLs. Subsequent Guest Portal API calls continue to use only the HttpOnly session cookie.
- Because the retained fragment is a bearer capability, anyone who obtains the complete owner-generated URL can establish the same read-only guest session. Documentation now calls out the need to keep active guest URLs out of public logs, screenshots, tickets, and repositories.

---

## 1.2.1 - 2026-08-23

This maintenance release removes booking confirmation/reference identifiers from the entire anonymous Guest Portal surface and restores intermediate accommodation **Stay** cards without changing the separate Check-in/Check-out behavior introduced in 1.2.0.

### Changed

- Booking confirmation codes, confirmation numbers, booking references, generic reservation reference fields, and equivalent confirmation-style identifiers are no longer exposed anywhere in the Guest Portal.
- The companion now sanitizes the browser-facing `/api/trip` payload server-side, including confirmation/reference values nested inside reservation/accommodation metadata JSON, so hiding the values does not rely on CSS or browser rendering logic.
- Flights, Bookings, and Accommodations detail cards no longer display confirmation/reference rows.
- Intermediate accommodation days once again render **Stay** cards in the unified Plan timeline. Check-in and Check-out remain separate cards on their respective boundary days, and same-day stays still render exactly two boundary cards.
- Intermediate **Stay** entries are treated as all-day context and sort above timed Plan events unless TREK explicitly links the accommodation to a place/assignment.

### Fixed

- Fixed confirmation/reference information remaining available on the detailed Flights and Reservations tabs after it had already been removed from Plan.
- Fixed confirmation/reference values still being present in the browser-visible shared-trip JSON payload.
- Restored the intended hotel timeline context for nights between Check-in and Check-out.

### Validation

- Added server-side regression coverage proving confirmation/reference values are removed from direct fields and JSON-encoded metadata while non-sensitive flight/provider metadata remains intact.
- Updated Guest Portal source regressions to require zero confirmation rendering in Plan, Flights, Bookings, and Accommodations.
- Updated accommodation timeline regressions to require separate Check-in/Check-out boundaries plus intermediate Stay entries.
- Revalidated JavaScript/Python syntax, Docker Compose/documentation consistency, release metadata, archive hygiene, and the complete automated test suite.

### Security

- Reduced anonymous data exposure by removing confirmation/reference identifiers before shared trip data crosses the companion-to-browser API boundary.
- Native TREK data remains available only server-side where required for internal provider/runtime operations; guest session, secret-file, proxy-trust, and authorization behavior are otherwise unchanged.

---

## 1.2.0 - 2026-08-23

This release refines the unified Plan timeline so its ordering and relationships mirror TREK's day plan more closely while keeping the dedicated Flights and Reservations tabs for detailed information.

### Changed

- Plan no longer displays reservation confirmation codes, booking references, or confirmation numbers. Detailed Flights and Reservations cards continue to expose those values where appropriate.
- Plan ordering now honors TREK reservation-to-assignment/place links and the `day_plan_position` / `day_positions` fields before falling back to event times.
- Flight placement now matches transport endpoints to planned airport places using IATA/airport codes, airport names, and close coordinates. A matched flight is placed immediately after its departure airport and before the following destination stop instead of being collected at the bottom of the day.
- Planned place times now use assignment-level `assignment_time` / `assignment_end_time` overrides when TREK provides them.
- Place-linked bookings and other reservation events are rendered directly beneath the associated Plan stop with a responsive visual inset.
- Accommodation timeline rendering now creates boundary events only: one **Check-in** entry on the arrival day and one **Check-out** entry on the departure day. Same-day accommodations create two separate entries.
- Removed intermediate **Stay** entries from the Plan timeline.
- Retained the mobile-first Plan layout, narrow-screen wrapping, compact marker column, safe-area padding, and horizontal-overflow protection.

### Fixed

- Fixed transport cards appearing as a group below airport places even when the trip already contains matching departure and arrival airport stops.
- Fixed reservations/events attached to a TREK place appearing at the bottom of the day instead of beside that place.
- Fixed same-day hotel stays being represented by a combined **Check-in / check-out** card instead of separate check-in and check-out events.
- Fixed confirmation information leaking into the summary-oriented Plan tab.

### Validation

- Added regression checks for Plan confirmation suppression, TREK place/assignment attachment fields, airport endpoint anchoring, separate accommodation boundary events, and responsive attached-event styling.
- Revalidated JavaScript/Python syntax, Docker Compose/documentation consistency, release metadata, archive hygiene, and the complete automated test suite.

### Security

- No authorization, guest-session, secret handling, provider, or proxy-trust behavior changed in this release.

---

## 1.1.2 - 2026-08-23

This maintenance release corrects lodging classification and expands the Plan tab into a unified chronological guest timeline while retaining the existing mobile-first layout and dedicated detail tabs.

### Changed

- Plan now combines planned stops, flights/transport, non-transport bookings, and accommodations in one chronological day-by-day timeline.
- Accommodation ranges appear on every covered trip day as **Check-in**, **Stay**, or **Check-out** entries.
- Plan search now searches the unified timeline rather than only planned places.
- Dedicated Flights and Reservations tabs remain available for detailed transport/provider and booking views.
- Added responsive timeline styling for narrow phones, including single-column content, wrapping metadata, compact markers, safe-area-aware page spacing, and no horizontal overflow.

### Fixed

- Standalone reservations with type `hotel` are now classified under **Reservations → Accommodations** instead of **Bookings**.
- TREK's linked Hotel partner reservation is still suppressed when a native accommodation with the matching `reservation_id` is present, preventing duplicate lodging cards.
- Hotel-style reservation fields now fall back correctly to reservation date/time, location, provider, confirmation, and metadata fields when rendered as an accommodation.

### Validation

- Added regression coverage for Hotel/accommodation classification and the unified Plan timeline source paths.
- Revalidated Python/JavaScript syntax, release metadata, Docker Compose/documentation consistency, package structure, and the complete automated test suite.

### Security

- No authorization or session model changes. Share capabilities, HttpOnly sessions, secret-file handling, proxy trust, and sensitive-log filtering remain unchanged.

---

## 1.1.0 - 2026-08-17

This release is a full code, packaging, deployment and documentation consolidation. It keeps the established guest experience while making Docker Compose the single documented deployment method, removing dormant runtime access to third-party plugin databases, synchronizing every configuration surface, and standardizing comments/documentation throughout the codebase.

### Added

#### Guest portal experience

- Added a mobile-friendly read-only guest interface with four intentional sections: Plan, Flights, Reservations and Photos.
- Added day-by-day itinerary rendering and place search.
- Added Mapbox planner support with configurable style, 3D mode and high-quality mode.
- Added inline map placement directly beneath the selected itinerary stop.
- Added a fixed geographic 1 km selected-stop framing radius that remains consistent across desktop/mobile viewport changes.
- Added route geometry for driving, walking and cycling profiles with graceful straight-line fallback when routing is unavailable.
- Added transport cards separated from non-transport reservations.
- Added accommodation deduplication when TREK exposes linked partner reservation records.
- Added Journey gallery display with chronological/date grouping.
- Added image/video lightbox navigation and touch/keyboard controls.
- Added best-effort embedded EXIF/XMP capture-date parsing for Journey media.
- Added optional server-side Immich asset-date lookup for original capture dates.

#### Guest session/security gateway

- Added one-time exchange of native TREK/Journey public-share capabilities through `POST /api/session`.
- Added random memory-only guest sessions carried by a `Secure`, `HttpOnly`, `SameSite=Strict` cookie.
- Added automatic removal of native share capabilities from the visible URL/history after session establishment.
- Added configurable session limits, creation-rate limiting and optional server-side session age expiry.
- Added persistent browser cookie Max-Age support when server-side session age expiry is disabled.
- Added same-origin enforcement for session creation, logout and browser telemetry.
- Added bounded JSON request handling and safe draining of rejected POST request bodies to preserve HTTP keep-alive framing.
- Added automatic `hashchange` handling so pasting a fresh owner share link into an already-open Guest Portal page re-establishes the session immediately.
- Added back/forward-cache (`pageshow`) recovery for restored pages containing a fresh share fragment.
- Added load-generation guards and runtime-state reset during share re-bootstrap to prevent stale overlapping navigation from replacing newer data.

#### Dedicated guest origin

- Added first-class support for a dedicated Guest Portal hostname separate from the main TREK browser origin.
- Added `TREK_PUBLIC_ORIGIN` so server-side TREK validation sends the TREK public Host header while guest browser validation continues to use `PUBLIC_ORIGIN`.
- Added plugin warnings when a configured guest URL shares the TREK origin and may be intercepted by TREK's PWA Service Worker.
- Added dedicated-host Apache and Nginx examples plus same-origin `/guest-portal/` alternatives.
- Added public/local split-DNS guidance.

#### Flights and provider scheduling

- Added optional AeroDataBox live flight enrichment and adsb.fi aircraft lookups.
- Added provider rate limiting, HTTP 429 retry/backoff and timeout controls.
- Added separate browser-check and provider-refresh scheduling.
- Added suppression of AeroDataBox calls outside the configurable live-data window.
- Added quota-aware provider TTLs that become progressively shorter as departure approaches and while a flight is active.
- Added explicit Flights UI countdowns for the next browser check and next provider refresh/window.
- Added in-memory live-flight caching.
- Added Guest Portal-owned persistent SQLite live-flight caching across companion restarts.
- Added stale-cache/provider-failure fallback using Guest Portal's own cached payloads.
- Added flight refresh-decision diagnostics including phase, hours to departure, live-window state, browser poll interval, provider TTL, cache age, cache source and suppression reason.
- Added a one-shot administrative key extractor that can read an existing Flight Tracker plugin database only while deliberately invoked and copy the provider key to Guest Portal's dedicated secret file.

#### Observability

- Added structured operational logging across HTTP, sessions, TREK upstream reads, static content, Mapbox/browser activity, flights, providers, reservations, photos, Immich, caches and runtime health.
- Added request correlation IDs and `X-Guest-Request-ID` response headers.
- Added session-protected browser telemetry with rate limiting and sensitive-field filtering.
- Added configurable key/value and JSON log formats.
- Added configurable runtime heartbeat events reporting uptime, sessions, threads, memory, cache sizes, persistent-cache state, disk space and request/provider counters.
- Added trusted-proxy-aware client-IP logging with explicit proxy CIDR validation and spoofing protection.
- Added optional proxy-peer/Cloudflare diagnostic fields without trusting arbitrary client headers.
- Added Admin plugin configuration read/write audit events through TREK's own plugin logger.

#### Docker Compose deployment

- Added a standalone Docker Compose deployment that joins TREK's existing Docker network via `TREK_DOCKER_NETWORK`.
- Added Docker-DNS connectivity to the configurable `TREK_HOST`/`TREK_PORT` without requiring Guest Portal to be merged into TREK's Compose project.
- Added a Compose healthcheck using Python's standard library, avoiding extra packages in the base image.
- Added `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1` runtime settings.
- Added explicit host bind selection through `GUEST_PORTAL_BIND_IP`.
- Added external-network, container hardening, read-only source mounts, writable cache mount and read-only secret mounts.
- Added complete `.env.example` coverage for every user-adjustable Compose/runtime setting.

### Changed

#### Deployment and packaging

- Standardized deployment on Docker Compose only.
- Changed the companion release name to `trek-guest-portal-companion-1.1.0.zip`.
- Changed the companion to run as an independent Compose project rather than relying on service merging.
- Changed source/cache/secret mounts to package-relative paths so the extracted release directory is self-contained.
- Changed documentation to use `docker compose config`, `docker compose up -d`, `docker compose ps` and `docker compose logs` consistently.
- Changed installation to require discovery/configuration of TREK's existing Docker network before first startup.
- Changed the recommended browser layout to a dedicated guest hostname while retaining a documented same-origin path alternative.
- Changed new plugin configurations to require an explicit **Guest Portal web address** instead of silently pre-filling the same-origin path; migrated saved rows without a base still retain their compatibility fallback.
- Changed absolute Guest Portal web addresses accepted by the plugin to HTTPS-only; same-origin absolute paths remain supported.
- Expanded release packages so the companion includes `.env.example`, Compose, all deployment docs, proxy examples, security/changelog/license files and administrative tools.
- Made the plugin ZIP documentation self-contained by bundling the project documentation/security guide and rewriting its packaged README link.
- Expanded complete-bundle validation to verify required files before checksums are generated.
- Standardized repository, plugin, companion, browser cache-busting, test and release metadata on `1.1.0`.

#### Runtime/database boundaries

- Changed provider-key handling so the public companion reads only its dedicated secret file (with the existing explicit environment fallback for non-standard deployments).
- Changed flight-number/reference reuse to use only native TREK reservation data and Guest Portal's own cached payloads.
- Changed live-provider failure fallback to Guest Portal's persistent/in-memory cache instead of any third-party plugin cache.
- Changed `PHOTO_DATE_CACHE_TTL` from a non-functional Compose-only setting to a real server-side configurable cache TTL.

#### Sessions

- Changed the default session policy to no server-side age expiration (`SESSION_TTL_SECONDS=0`).
- Kept optional positive TTL support for administrators who want finite session age.
- Changed session logging to distinguish non-expiring sessions from missing/invalid sessions.
- Changed browser telemetry to stop sending events after a `401` so a restarted companion does not receive repeated rejected telemetry from a stale page.

#### Flight scheduling

- Changed far-future flight handling so browser checks occur periodically without contacting AeroDataBox.
- Changed live-window browser checks to occur independently from provider cache TTL.
- Changed provider refresh cadence to use distance-to-departure/active-state TTLs rather than a single coarse refresh interval.
- Changed the Flights heading to distinguish the next automatic browser check from the next external provider refresh.

#### Documentation/code quality

- Rewrote the README as the canonical project/release entry point.
- Rewrote prerequisites, installation, configuration, architecture, guest-origin, reverse-proxy and upgrade documentation to match the actual standalone Compose deployment.
- Documented all user-adjustable environment variables and the container-managed internal variables.
- Added/standardized comments and docstrings across Python, JavaScript, shell scripts, Compose/YAML, proxy examples, CSS and HTML so security boundaries and non-obvious behavior are explained near the code that implements them.
- Refactored the one-shot database discovery/key extraction helpers into documented functions with bounded schema/key checks and explicit exit paths.
- Renamed guest-facing live-flight renderer terminology so the UI describes Guest Portal's own live-flight feature rather than implying runtime dependence on another plugin.
- Removed release-number labels from historical CSS comments and other stale source comments.

### Fixed

- Fixed standalone Compose deployments where `TREK_HOST=app` could not resolve because the companion had not joined TREK's Docker network.
- Fixed `PHOTO_DATE_CACHE_TTL` being advertised in Compose but ignored by the Python server.
- Fixed incomplete `.env.example` coverage for advanced but supported runtime controls.
- Fixed duplicate/stale deployment documentation paths and instructions.
- Fixed package documentation links and package-layout expectations.
- Fixed release packaging that referenced deployment examples not present in the generated ZIP.
- Fixed HTTP/1.1 keep-alive corruption after rejected browser telemetry POSTs by draining bounded unread request bodies before reusing the connection.
- Fixed misleading session-auth logging for missing sessions.
- Fixed unnecessary provider calls for flights outside the live-data window.
- Fixed misleading flight countdown semantics that could imply an external API call was scheduled when only a local/browser check was due.
- Fixed logging of reverse-proxy addresses as visitor addresses when a correctly configured trusted proxy provides a sanitized client address.
- Fixed same-document guest share navigation that could remain on the unavailable page after only the URL fragment changed.
- Fixed stale map/flight/gallery/runtime state during share-link re-bootstrap.
- Fixed file-backed provider secrets being described as environment-sourced in diagnostics.
- Fixed public repository packaging so no deployment-specific production hostname is embedded.

### Security

- Removed all runtime code paths that read a Flight Tracker or other TREK plugin database from the public companion container.
- Kept the one-shot key extractor isolated as an explicit administrative action; it is never mounted into the public container with TREK plugin data.
- Native TREK/Journey share capabilities are not placed in guest API request URLs.
- Guest browser JavaScript cannot read the HttpOnly session cookie.
- Guest sessions remain memory-only and are invalidated when the companion restarts/redeploys.
- Native TREK/Journey share revocation remains authoritative.
- Provider API keys remain server-side and are mounted from dedicated files.
- Public `/health` output remains minimal (`ok` and version only).
- Static path traversal is constrained by resolved-path validation under `PUBLIC_ROOT`.
- Persistent SQLite access uses parameterized queries.
- Container defaults use a non-root UID/GID, read-only root filesystem, dropped capabilities, `no-new-privileges`, resource limits and a hardened tmpfs.
- Dedicated guest-origin deployment isolates the Guest Portal Service Worker/cookie/browser context from authenticated TREK.
- Forwarded client addresses are trusted only from explicit proxy networks.
- Browser telemetry requires a valid session and allowed origin, is rate-limited, and discards sensitive field names.
- Structured logs intentionally exclude or fingerprint native share tokens, API keys, cookies, passwords and other secret-bearing fields.
- Content Security Policy and additional response security headers remain enabled for guest responses.
- Plugin iframe messaging is pinned to the established TREK parent origin instead of wildcard postMessage targets.

### Removed

- Removed all deployment-specific references and artifacts for alternative container-management UIs; Docker Compose is the sole documented deployment method.
- Removed the obsolete copied service example because `docker-compose.yml` is now directly runnable as its own project.
- Removed the unused companion Dockerfile from the supported deployment path; the Compose service runs the pinned Python base image with read-only application mounts.
- Removed dormant runtime Flight Tracker database discovery, cache reads, flight-number overrides and provider-key fallback from the public companion.
- Removed stale Guest Portal release-number references from source comments and historical documentation labels.

### Validation

The release validation covers:

- Python syntax compilation;
- JavaScript syntax checks;
- shell syntax checks;
- plugin/companion/browser version consistency;
- absence of stale Guest Portal release numbers;
- absence of unsupported container-management terminology/artifacts;
- absence of deployment-local `public/config.js` in source;
- `.env.example` duplicate-key detection;
- Compose interpolation coverage by `.env.example`;
- configuration-document coverage for `.env` variables;
- Docker Compose standalone-network/documentation consistency;
- guest session security and token-free API behavior;
- rejected POST body draining/keep-alive behavior;
- trusted proxy header acceptance/spoofing rejection;
- browser telemetry sensitive-field removal;
- same-document share-navigation recovery;
- dedicated TREK/guest origin behavior;
- far-future provider suppression;
- live-window provider TTL scheduling;
- release ZIP layout and checksum generation.

---

## Maintaining this file

For future work:

1. Add pending user-visible changes under **Unreleased**.
2. Use **Added**, **Changed**, **Fixed**, **Security**, **Removed** and **Validation** as appropriate.
3. Do not include credentials, share capabilities, provider keys, production hostnames or private infrastructure details.
4. When publishing a release, move Unreleased entries into a new current-release section and recreate the empty Unreleased headings.
