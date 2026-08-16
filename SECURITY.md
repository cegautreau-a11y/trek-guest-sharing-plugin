# Security Policy

## Project security model

TREK Guest Portal is an internet-facing read-only presentation layer built on TREK's native public-share capabilities. TREK remains authoritative for whether a trip or Journey is shared.

### Native bearer capabilities

The owner-generated Guest Portal URL contains the native TREK/Journey share capabilities only in its URL fragment. On first load:

1. the browser POSTs those values in a JSON body to `/api/session`;
2. the companion validates them against TREK;
3. the companion creates a bounded server-side session;
4. it sets a random `HttpOnly; Secure; SameSite=Strict` cookie;
5. the browser removes the original fragment from the visible URL/history;
6. later APIs contain no native share token in their URL.

Do not configure your reverse proxy to log request bodies for `/guest-portal/api/session`.

### Runtime filesystem boundary

The recommended container mounts only:

- Guest Portal static files read-only;
- Guest Portal server code read-only;
- Guest Portal's own cache read/write;
- one AeroDataBox secret file read-only;
- one Immich secret file read-only.

It does not need TREK's database, uploads, plugin source, or plugin-data tree at runtime.

### Secrets

Provider API keys should be mounted from files. They must not be committed to Git, placed in `public/config.js`, returned from `/health`, or intentionally logged.

The Mapbox `pk...` token is a public browser token by design; create a dedicated token and add URL restrictions.

### Container hardening

The provided Compose example uses:

- non-root numeric UID/GID;
- read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- memory and PID limits;
- hardened tmpfs;
- long-form bind mounts with `create_host_path: false`.

## Known limitations

- Anyone with an unrevoked owner-generated Guest Portal link can establish a read-only guest session because the underlying TREK/Journey share URLs are bearer capabilities.
- Guest sessions are in-memory and are lost on companion restart.
- Third-party services receive network requests required to provide enabled features.
- The project is custom integration code and should be revalidated after major TREK changes.

## Migrating from pre-1.0 builds

Some 0.3.x builds placed native share tokens in later API URLs. If those builds were used with request-line access logging, historical logs may contain old bearer capabilities. After installing a v1.x build, rotate the native TREK/Journey shares and handle old logs according to your retention policy.

## Reporting a vulnerability

Do not post API keys, active guest URLs, credentials, or exploit details in a public issue.

If the GitHub repository has **Private Vulnerability Reporting** enabled, use that channel. Otherwise contact the repository maintainer privately and provide the minimum information needed to reproduce the problem.

If you suspect active exposure:

1. revoke/rotate affected TREK and Journey public shares;
2. stop the companion if necessary;
3. rotate provider API keys that may have been exposed;
4. preserve relevant logs with restricted access;
5. patch and verify before generating replacement public links.
