# Upgrading to 2.0.3

This procedure upgrades an existing TREK Guest Portal installation to the current Docker Compose layout.

## Refreshable guest links in 2.0.3

Version 2.0.3 keeps the complete owner-generated Guest Portal URL fragment after session establishment instead of rewriting the browser back to the bare Guest Portal root. This means:

- `#trip=...`, optional `journey=...`, and `title=...` metadata remain in the address bar;
- a normal browser refresh can POST the same native share capabilities to `/api/session` and rebuild the guest session automatically;
- a companion restart or lost session cookie can be recovered by refreshing the already-open Guest Portal URL instead of finding the original share link again;
- the fragment still is not included in normal HTTP request URLs or companion API URLs.

Because the fragment contains bearer share capabilities, anyone who can copy the complete Guest Portal URL has the same read-only access as the underlying TREK/Journey public shares. Avoid publishing active guest URLs in logs, tickets, screenshots, or public repositories.

Version 2.0.3 also retains the v1.2.1 privacy/timeline behavior: confirmation/reference identifiers are stripped before browser delivery, place-linked events remain attached to their Plan stops, flights use airport/assignment ordering, and accommodations show separate **Check-in**, intermediate **Stay**, and **Check-out** entries.

## Important deployment change

The companion is now a standalone Docker Compose project. Do **not** merge its service into TREK's Compose file. Instead, the Guest Portal project joins TREK's existing Docker network using:

```dotenv
TREK_DOCKER_NETWORK=<exact-trek-network-name>
TREK_HOST=app
TREK_PORT=3000
```

This keeps the Guest Portal lifecycle independent while preserving Docker-DNS access to TREK.

## 1. Back up deployment-local state

From the current installation directory, preserve:

```text
.env
public/config.js
cache/
secrets/
```

Example:

```bash
cd /opt
sudo cp -a trek-guest-portal trek-guest-portal.backup
```

Do not publish or commit the backup because it may contain provider credentials and cached trip/flight metadata.

## 2. Download the current release

Download:

```text
trek-guest-portal-2.0.3.zip
trek-guest-portal-companion-2.0.3.zip
```

The first file is the TREK plugin. The second is the companion deployment.

## 3. Update the TREK plugin

Upload `trek-guest-portal-2.0.3.zip` through **TREK → Admin → Plugins** and enable/update Guest Portal.

Existing per-trip Guest Portal configuration remains in the plugin's own TREK-managed database.

## 4. Stop the companion

```bash
cd /opt/trek-guest-portal
docker compose down
```

If the old deployment was managed in a different Compose project, stop/remove only the old Guest Portal companion service before continuing.

## 5. Replace application files

Preserve `.env`, `public/config.js`, `cache/` and `secrets/`, then replace the distributed application/docs files from the new companion ZIP.

One safe approach is to extract into a temporary directory, then copy the distributed files across:

```bash
rm -rf /tmp/trek-guest-portal-new
mkdir -p /tmp/trek-guest-portal-new
unzip trek-guest-portal-companion-2.0.3.zip -d /tmp/trek-guest-portal-new
```

Copy the new application and documentation while leaving local state intact:

```bash
sudo cp -a /tmp/trek-guest-portal-new/public/app.js /opt/trek-guest-portal/public/
sudo cp -a /tmp/trek-guest-portal-new/public/index.html /opt/trek-guest-portal/public/
sudo cp -a /tmp/trek-guest-portal-new/public/style.css /opt/trek-guest-portal/public/
sudo cp -a /tmp/trek-guest-portal-new/public/config.js.example /opt/trek-guest-portal/public/
sudo cp -a /tmp/trek-guest-portal-new/server /opt/trek-guest-portal/
sudo cp -a /tmp/trek-guest-portal-new/tools /opt/trek-guest-portal/
sudo cp -a /tmp/trek-guest-portal-new/docs /opt/trek-guest-portal/
sudo cp -a /tmp/trek-guest-portal-new/examples /opt/trek-guest-portal/
sudo cp -a /tmp/trek-guest-portal-new/docker-compose.yml /opt/trek-guest-portal/
sudo cp -a /tmp/trek-guest-portal-new/.env.example /opt/trek-guest-portal/
sudo cp -a /tmp/trek-guest-portal-new/README.md /tmp/trek-guest-portal-new/SECURITY.md /tmp/trek-guest-portal-new/VERSION /opt/trek-guest-portal/
```

## 6. Reconcile `.env`

Do not overwrite your working `.env` blindly. Compare it with the new template:

```bash
cd /opt/trek-guest-portal
diff -u .env.example .env || true
```

Ensure at least these settings are present and correct:

```dotenv
TREK_DOCKER_NETWORK=<exact-trek-network-name>
TREK_HOST=app
TREK_PORT=3000
GUEST_PORTAL_BIND_IP=127.0.0.1
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

Use the actual network name from:

```bash
docker inspect <TREK_CONTAINER> \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
```

Review [CONFIGURATION.md](CONFIGURATION.md) for new/changed optional settings.

## 7. Verify local files and permissions

The Compose file requires these paths to exist:

```text
public/config.js
cache/
secrets/aerodatabox_api_key
secrets/immich_api_key
```

Repair ownership/permissions if necessary:

```bash
sudo chown -R 65532:65532 cache secrets
sudo chmod 700 cache secrets
sudo chmod 600 secrets/aerodatabox_api_key secrets/immich_api_key
```

## 8. Validate Compose

```bash
cd /opt/trek-guest-portal
docker compose config
```

Do not continue until the external TREK network resolves and interpolation succeeds.

## 9. Start the current companion

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 trek-guest-portal
```

Verify the health endpoint:

```bash
curl -s http://127.0.0.1:8088/health
```

Expected:

```json
{"ok":true,"version":"2.0.3"}
```

Use the configured LAN bind address instead of loopback when the reverse proxy is remote.

## 10. Recheck the reverse proxy and origin settings

The recommended layout remains:

```text
TREK:         https://trek.example.com/
Guest Portal: https://guest.example.com/
```

with:

```dotenv
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

If migrating from a same-origin `/guest-portal/` path to a dedicated hostname, update DNS, reverse-proxy rules, Mapbox URL restrictions and the plugin's **Guest Portal web address**.

## 11. Test an existing trip

Open a configured trip in the TREK plugin, copy its generated Guest Portal link, and verify:

- guest session creation succeeds;
- Plan/Mapbox loads and the Plan timeline merges planned stops, flights/transport, bookings, and accommodations chronologically by trip day;
- Flights/Reservations render, with standalone `hotel` bookings classified under Accommodations and linked Hotel partner records deduplicated;
- Photos render if Journey is configured;
- optional live providers function;
- the complete owner-generated URL fragment remains present after session creation;
- refreshing that URL successfully re-establishes the guest session;
- no bearer tokens appear in normal logs.

A companion restart invalidates memory-only guest sessions, but an existing browser tab can now refresh the retained owner-generated URL to establish a fresh session.

## 12. Remove the backup only after verification

Keep the backup until the new deployment has been tested. When it is no longer needed, remove it according to your normal secret/cache retention policy.
