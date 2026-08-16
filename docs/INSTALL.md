# Fresh Installation

This procedure is written for someone who has **never installed TREK Guest Portal before**.

## Before you begin

Read [PREREQUISITES.md](PREREQUISITES.md). You should already have:

- a working TREK 3.4.x Docker/Portainer deployment;
- an HTTPS hostname for TREK;
- a Mapbox public token;
- administrator access to TREK and the Docker host.

Immich and live AeroDataBox flight data are optional and can be added later.

---

## Step 1 — Download the release files

From the GitHub release, download:

```text
trek-guest-portal-1.0.2.zip
trek-guest-portal-companion-1.0.2-portainer.zip
```

The first file is the TREK plugin. **Do not unzip it before uploading it to TREK.**

The second file is extracted on the Docker host.

---

## Step 2 — Install the TREK plugin

In TREK:

1. Sign in as an administrator.
2. Open **Admin → Plugins**.
3. Choose **Upload**.
4. Select `trek-guest-portal-1.0.2.zip`.
5. Review the requested permissions:

```text
db:own
db:read:trips
```

6. Enable **Guest Portal**.

The plugin does not serve the anonymous site itself. Its job is to save the native share tokens for a trip and generate the single owner guest URL.

---

## Step 3 — Choose a companion host directory

This documentation uses:

```text
/opt/trek-guest-portal
```

You may use another path, but change `GUEST_PORTAL_ROOT`/volume sources accordingly.

Create it and extract the companion ZIP:

```bash
sudo mkdir -p /opt/trek-guest-portal
sudo unzip trek-guest-portal-companion-1.0.2-portainer.zip -d /opt/trek-guest-portal
```

Expected files:

```text
/opt/trek-guest-portal/
├── public/
│   ├── app.js
│   ├── index.html
│   ├── style.css
│   └── config.js.example
├── server/
│   └── server.py
├── tools/
│   ├── extract-flight-tracker-key.py
│   └── find-flight-tracker-db.py
├── docker-compose.yml
├── README.md
└── SECURITY.md
```

---

## Step 4 — Create the local Mapbox configuration

Copy the example:

```bash
sudo cp /opt/trek-guest-portal/public/config.js.example \
        /opt/trek-guest-portal/public/config.js
sudo nano /opt/trek-guest-portal/public/config.js
```

Set your **public `pk...` Mapbox token**:

```javascript
window.GUEST_PORTAL_CONFIG = {
  mapboxAccessToken: 'pk.YOUR_PUBLIC_MAPBOX_TOKEN',
  mapboxStyle: 'mapbox://styles/mapbox/standard',
  mapbox3d: true,
  mapboxHighQuality: false
};
```

Recommended: create a token specifically for Guest Portal and restrict it to your HTTPS origin, for example:

```text
https://trek.example.com/*
```

`config.js` is intentionally ignored by Git and must never contain a Mapbox secret (`sk...`) token.

---

## Step 5 — Create cache and secret files

The companion runs as UID/GID `65532:65532` by default.

```bash
sudo install -d -o 65532 -g 65532 -m 0700 /opt/trek-guest-portal/cache
sudo install -d -o 65532 -g 65532 -m 0700 /opt/trek-guest-portal/secrets

# Create empty optional secret files so long-form Compose mounts are valid.
sudo install -o 65532 -g 65532 -m 0600 /dev/null /opt/trek-guest-portal/secrets/aerodatabox_api_key
sudo install -o 65532 -g 65532 -m 0600 /dev/null /opt/trek-guest-portal/secrets/immich_api_key
```

The persistent flight cache will be created later as:

```text
/opt/trek-guest-portal/cache/guest-portal.db
```

### Optional: configure AeroDataBox now

If you have an AeroDataBox/RapidAPI key, write it without echoing it to the terminal:

```bash
umask 077
read -rsp "AeroDataBox API key: " ADB_KEY; echo
printf '%s' "$ADB_KEY" | sudo tee /opt/trek-guest-portal/secrets/aerodatabox_api_key >/dev/null
unset ADB_KEY
sudo chown 65532:65532 /opt/trek-guest-portal/secrets/aerodatabox_api_key
sudo chmod 600 /opt/trek-guest-portal/secrets/aerodatabox_api_key
```

#### Optional Flight Tracker key extractor

If you already have the third-party Flight Tracker TREK plugin and entered its key through the plugin's own in-widget key field, the helper can copy it into Guest Portal's dedicated secret file:

```bash
docker run --rm \
  -v /PATH/TO/TREK/data/plugins-data:/scan:ro \
  -v /opt/trek-guest-portal/tools:/tools:ro \
  -v /opt/trek-guest-portal/secrets:/out \
  python:3.12-alpine \
  python /tools/extract-flight-tracker-key.py /scan /out/aerodatabox_api_key
```

Then:

```bash
sudo chown 65532:65532 /opt/trek-guest-portal/secrets/aerodatabox_api_key
sudo chmod 600 /opt/trek-guest-portal/secrets/aerodatabox_api_key
```

**Important:** the extractor cannot recover a key stored only in TREK's encrypted Admin plugin configuration. In that case use the manual secret method above.

### Optional: configure Immich now

Create a dedicated Immich API key with only the asset-read permissions Guest Portal needs, then:

```bash
umask 077
read -rsp "Immich API key: " IMMICH_KEY; echo
printf '%s' "$IMMICH_KEY" | sudo tee /opt/trek-guest-portal/secrets/immich_api_key >/dev/null
unset IMMICH_KEY
sudo chown 65532:65532 /opt/trek-guest-portal/secrets/immich_api_key
sudo chmod 600 /opt/trek-guest-portal/secrets/immich_api_key
```

---

## Step 6 — Add the companion service to Portainer / Compose

**Recommended:** put Guest Portal in the same stack as TREK's service named `app`. Then `TREK_HOST=app` works through Docker DNS.

Copy the service from [`examples/portainer-service.yml`](../examples/portainer-service.yml) into the existing `services:` block of the TREK stack.

At minimum, set these values:

```yaml
ports:
  - "127.0.0.1:8088:8080"   # use when reverse proxy is on same host

environment:
  - TREK_HOST=app
  - TREK_PORT=3000
  - PUBLIC_ORIGIN=https://trek.example.com
  - COOKIE_PATH=/guest-portal/
  - IMMICH_URL=https://photos.example.com   # leave empty if unused

volumes:
  # change /opt/trek-guest-portal if you chose another host path
```

### If your reverse proxy is on another server

Do not bind `8088` to all interfaces unless necessary. Bind it to the Docker host's specific LAN IP:

```yaml
ports:
  - "10.0.0.20:8088:8080"
```

Then firewall TCP/8088 so only the reverse-proxy server can connect.

### If Guest Portal is in a separate Compose stack

`TREK_HOST=app` will not resolve unless both stacks share a Docker network. Either:

- attach Guest Portal to the existing TREK Docker network and use the TREK service/container DNS name; or
- use a private reachable TREK backend address for `TREK_HOST`/`TREK_PORT`.

Keeping it in the same stack is simpler and preferred.

---

## Step 7 — Configure the reverse proxy

Guest Portal should be reachable at:

```text
https://trek.example.com/guest-portal/
```

For Apache, add this **before TREK's final `/` catch-all**:

```apache
ProxyPass        "/guest-portal/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/guest-portal/" "http://127.0.0.1:8088/"
RedirectMatch 302 ^/guest-portal$ /guest-portal/
```

If the proxy is remote, replace `127.0.0.1` with the specific Docker host address.

Do not remove TREK's existing `/ws`, `/mcp`, or `/` proxy rules. Rule order matters. See [REVERSE-PROXY.md](REVERSE-PROXY.md).

Reload Apache after a syntax check:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
```

---

## Step 8 — Deploy and verify the companion

Redeploy the Portainer stack, then:

```bash
docker logs --tail 100 trek-guest-portal
```

A healthy startup resembles:

```text
INFO cache.persistent_ready db=/cache/guest-portal.db
INFO startup version=1.0.2 ...
INFO integration.aerodatabox configured=True key_source=secret-file
INFO integration.immich configured=True ...
INFO integration.persistent_cache enabled=True ...
```

If optional providers are not configured, their corresponding line may show `configured=False`; the portal can still start.

Public health intentionally reveals only:

```bash
curl -s http://127.0.0.1:8088/health
```

```json
{"ok":true,"version":"1.0.2"}
```

Then test through the public origin:

```bash
curl -I https://trek.example.com/guest-portal/
```

You should receive `200 OK` and security headers including CSP, `nosniff`, and HSTS when served over HTTPS.

---

## Step 9 — Create the native TREK public share

Open the trip in TREK and create its normal public share link.

For Guest Portal:

- **Plan** is based on the native trip share.
- **Flights and Reservations require TREK's Bookings sharing permission** so the native public response contains transport/reservation data.

Copy the normal TREK public share URL.

---

## Step 10 — Optional: create a Journey public share for Photos

If using Photos:

1. Enable/configure Journey in TREK.
2. Link/create the Journey for this trip.
3. Create a Journey public share.
4. Enable **Gallery** on that share.
5. Copy the Journey share URL.

Immich is not required merely to display shared Journey photos. It is used by Guest Portal to resolve original capture dates for Immich-backed assets.

---

## Step 11 — Configure Guest Portal inside the trip

Open the new **Guest Portal** tab in the TREK trip.

Enter:

**Guest Portal web address**

```text
/guest-portal/
```

**TREK Trip Share URL or token**

Paste the native public trip link from Step 9.

**Journey public share URL or token**

Paste the Journey share from Step 10, or leave blank if Photos are not used.

Click **Save Guest Portal** and copy the generated guest link.

The generated owner URL resembles:

```text
https://trek.example.com/guest-portal/#trip=...&journey=...&title=...
```

The native share capabilities are kept after `#`. On first load they are POSTed once in a JSON body to create a short-lived guest session, then removed from the visible browser URL/history.

---

## Step 12 — Test from a private/incognito browser

Use a browser where you are **not signed in to TREK**.

Confirm:

- the trip header remains visible when switching tabs;
- Plan loads Mapbox;
- clicking a Plan stop moves the map under that stop;
- the selected point remains framed at approximately a 1 km radius on phone and desktop;
- Flights and Reservations always appear, even if empty;
- live flight data appears if AeroDataBox is configured;
- the Flights countdown shows the next auto refresh;
- Photos are grouped by date if a Journey share is configured;
- Immich-backed photos use original capture dates when Immich is configured.

After the first successful load, the address bar should no longer contain the `#trip=` / `#journey=` values.

---

## Step 13 — Verify logs do not expose bearer tokens

The companion redacts native share tokens in its own logs. Because v1.x uses token-free API URLs after session creation, ordinary reverse-proxy access logs should show routes such as:

```text
POST /guest-portal/api/session
GET /guest-portal/api/trip
GET /guest-portal/api/journey
GET /guest-portal/api/flights/42
GET /guest-portal/api/photo-dates
```

They should not contain native share-token values in those URLs.

---

## Installation complete

Next read:

- [CONFIGURATION.md](CONFIGURATION.md) for all settings;
- [SECURITY.md](../SECURITY.md) before exposing the site publicly;
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if any provider or map fails.
