# Fresh Installation

This is the complete first-time installation procedure for TREK Guest Portal 3.5.0 using Docker Compose.

## Step 1 — Download the release files

Download these two files from the release:

```text
trek-guest-portal-3.5.0.zip
trek-guest-portal-companion-3.5.0.zip
```

- `trek-guest-portal-3.5.0.zip` is uploaded directly to TREK. Do not unzip it first.
- `trek-guest-portal-companion-3.5.0.zip` is extracted on the Docker host.

Optionally verify the published SHA-256 checksums before installation.

## Step 2 — Install the TREK plugin

In TREK:

1. Sign in as an administrator.
2. Open **Admin → Plugins**.
3. Choose **Upload**.
4. Select `trek-guest-portal-3.5.0.zip`.
5. Review the requested permissions:

```text
db:own
db:read:trips
```

6. Enable **Guest Portal**.

The plugin stores per-trip Guest Portal configuration and generates the owner guest URL. The anonymous guest site is served by the companion container.

## Step 3 — Extract the companion

This guide uses `/opt/trek-guest-portal`:

```bash
sudo mkdir -p /opt/trek-guest-portal
sudo unzip trek-guest-portal-companion-3.5.0.zip -d /opt/trek-guest-portal
cd /opt/trek-guest-portal
```

Expected top-level layout:

```text
/opt/trek-guest-portal/
├── .env.example
├── docker-compose.yml
├── README.md
├── SECURITY.md
├── VERSION
├── public/
│   ├── app.js
│   ├── config.js.example
│   ├── index.html
│   └── style.css
├── server/
│   └── server.py
├── tools/
├── docs/
└── examples/
```

## Step 4 — Create the Mapbox browser configuration

```bash
sudo cp public/config.js.example public/config.js
sudo nano public/config.js
```

Set a browser-safe public Mapbox token:

```javascript
window.GUEST_PORTAL_CONFIG = {
  mapboxAccessToken: 'pk.YOUR_PUBLIC_MAPBOX_TOKEN',
  mapboxStyle: 'mapbox://styles/mapbox/standard',
  mapbox3d: true,
  mapboxHighQuality: false
};
```

For the recommended dedicated guest hostname, restrict the token to:

```text
https://guest.example.com/*
```

Do not use a secret `sk...` Mapbox token.

## Step 5 — Create writable cache and secret files

The companion runs as UID/GID `65532:65532` by default.

```bash
sudo install -d -o 65532 -g 65532 -m 0700 cache secrets
sudo install -o 65532 -g 65532 -m 0600 /dev/null secrets/aerodatabox_api_key
sudo install -o 65532 -g 65532 -m 0600 /dev/null secrets/immich_api_key
```

Empty secret files are valid when the optional integration is disabled. The Compose bind mounts intentionally require these paths to exist before startup.

### Optional AeroDataBox key

```bash
umask 077
read -rsp "AeroDataBox API key: " ADB_KEY; echo
printf '%s' "$ADB_KEY" | sudo tee secrets/aerodatabox_api_key >/dev/null
unset ADB_KEY
sudo chown 65532:65532 secrets/aerodatabox_api_key
sudo chmod 600 secrets/aerodatabox_api_key
```

If an existing Flight Tracker plugin database contains the provider key in its SQLite `kv` table, use the bundled one-shot extractor instead of exposing the key on screen:

```bash
docker run --rm \
  -v /PATH/TO/TREK/data/plugins-data:/scan:ro \
  -v /opt/trek-guest-portal/tools:/tools:ro \
  -v /opt/trek-guest-portal/secrets:/out \
  python:3.12-alpine \
  python /tools/extract-flight-tracker-key.py /scan /out/aerodatabox_api_key
```

Then restore ownership/permissions:

```bash
sudo chown 65532:65532 secrets/aerodatabox_api_key
sudo chmod 600 secrets/aerodatabox_api_key
```

The long-running Guest Portal container never mounts the TREK plugin-data directory.

### Optional Immich key

```bash
umask 077
read -rsp "Immich API key: " IMMICH_KEY; echo
printf '%s' "$IMMICH_KEY" | sudo tee secrets/immich_api_key >/dev/null
unset IMMICH_KEY
sudo chown 65532:65532 secrets/immich_api_key
sudo chmod 600 secrets/immich_api_key
```

## Step 6 — Find TREK's Docker network

Identify the TREK application container:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
```

Then list its networks:

```bash
docker inspect <TREK_CONTAINER> \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
```

Choose the network on which TREK's application service is reachable. The companion joins this existing network as a separate Compose project.

If you are unsure which service name TREK exposes on that network, inspect aliases:

```bash
docker inspect <TREK_CONTAINER> --format '{{json .NetworkSettings.Networks}}'
```

The standard configuration uses `TREK_HOST=app` and `TREK_PORT=3000`.

## Step 7 — Create `.env`

```bash
sudo cp .env.example .env
sudo nano .env
```

At minimum, set the actual Docker network and public origins:

```dotenv
TREK_DOCKER_NETWORK=<exact-trek-network-name>
TREK_HOST=app
TREK_PORT=3000

GUEST_PORTAL_BIND_IP=127.0.0.1

PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

If your reverse proxy is on another host, change only the bind address to one explicit LAN IP:

```dotenv
GUEST_PORTAL_BIND_IP=10.0.0.20
```

Firewall TCP/8088 so only the reverse proxy can reach it. Avoid `0.0.0.0` unless you have a deliberate firewall design.

For a same-origin deployment instead:

```dotenv
PUBLIC_ORIGIN=https://trek.example.com
TREK_PUBLIC_ORIGIN=
COOKIE_PATH=/guest-portal/
```

See [CONFIGURATION.md](CONFIGURATION.md) for every setting.

## Step 8 — Validate Docker Compose before starting

Run from the directory containing `docker-compose.yml` and `.env`:

```bash
cd /opt/trek-guest-portal
docker compose config
```

Confirm that:

- the external network name is the real TREK network;
- `PUBLIC_ORIGIN` and `TREK_PUBLIC_ORIGIN` contain no path;
- the published port is bound only to the intended host interface;
- `TREK_HOST` and `TREK_PORT` match the reachable TREK service.

Start the companion:

```bash
docker compose up -d
```

Check container and health status:

```bash
docker compose ps
docker compose logs --tail=100 trek-guest-portal
```

A healthy startup includes a line similar to:

```text
INFO startup version=3.5.0 ... public_origin=https://guest.example.com trek_public_origin=https://trek.example.com cookie_path=/
```

The Compose healthcheck queries the internal `/health` endpoint automatically.

## Step 9 — Verify TREK connectivity from the companion

Confirm Docker DNS can resolve the configured TREK service:

```bash
docker compose exec trek-guest-portal python -c \
  "import socket; print(socket.gethostbyname('app'))"
```

If you changed `TREK_HOST`, substitute that name.

A failed lookup normally means `TREK_DOCKER_NETWORK` is wrong or the configured TREK service name is not an alias on that network.

## Step 10 — Configure the reverse proxy

The recommended public URL is:

```text
https://guest.example.com/
```

### Apache dedicated guest vhost

A ready-to-edit file is included at `examples/apache-guest-vhost.conf`:

```apache
<VirtualHost *:443>
    ServerName guest.example.com

    SSLEngine on
    # Your certificate directives.

    ProxyPreserveHost On
    ProxyPass        "/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
    ProxyPassReverse "/" "http://127.0.0.1:8088/"
</VirtualHost>
```

If the reverse proxy is remote, replace `127.0.0.1` with the Docker host address configured by `GUEST_PORTAL_BIND_IP`.

Validate and reload Apache:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
```

### Nginx dedicated guest server

A ready-to-edit file is included at `examples/nginx-guest-server.conf`:

```nginx
server {
    listen 443 ssl;
    server_name guest.example.com;

    # Your TLS certificate directives.

    location / {
        proxy_pass http://127.0.0.1:8088/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

For real client-IP logging and Cloudflare handling, follow [REVERSE-PROXY.md](REVERSE-PROXY.md) rather than trusting arbitrary forwarded headers.

## Step 11 — Verify the companion and public endpoint

For the default local bind:

```bash
curl -s http://127.0.0.1:8088/health
```

Expected response:

```json
{"ok":true,"version":"2.1.1"}
```

Then verify the HTTPS guest origin:

```bash
curl -I https://guest.example.com/
```

You should receive `200 OK` with Guest Portal security headers.

## Step 12 — Create the native TREK public share

Open the trip in TREK and create its normal public share.

Guest Portal uses only data exposed by that native share:

- Plan requires the map/plan permission.
- Flights, reservation details, and booking/accommodation entries in the Plan timeline require Bookings sharing.

Copy the TREK public share URL.

## Step 13 — Optional Journey public share

For Photos:

1. Link/create the Journey for the trip.
2. Create a Journey public share.
3. Enable Gallery on that share.
4. Copy the Journey public share URL.

Immich is not required merely to display Journey photos; it is optional capture-date enrichment.

## Step 14 — Configure Guest Portal in the trip

Open the **Guest Portal** tab inside the TREK trip.

For the recommended dedicated hostname, set **Guest Portal web address** to:

```text
https://guest.example.com/
```

Then provide:

- **TREK Trip Share URL or token** — required.
- **Journey public share URL or token** — optional.
- **Guest portal title** — optional.

Click **Save Guest Portal** and copy the generated guest link.

The owner link resembles:

```text
https://guest.example.com/#trip=...&journey=...&title=...
```

The native share capabilities remain after `#` and are POSTed to create an HttpOnly session. The fragment intentionally remains in the address bar so a normal refresh can reconstruct the guest session after a companion restart or cookie loss. The fragment is not included in ordinary HTTP request URLs, but the complete guest URL should still be treated as a bearer credential.

## Step 15 — Test guest behavior

Test the generated link both:

- in the same browser where TREK is already signed in; and
- in a private/incognito browser.

Confirm:

- the dedicated guest hostname remains open rather than the TREK dashboard;
- Plan and Mapbox load correctly, with planned stops, flights, bookings, and accommodations merged chronologically by day;
- selecting a stop moves the map beneath that stop and frames about 1 km;
- standalone `hotel` bookings appear under Reservations → Accommodations rather than Bookings;
- matching native accommodation and Hotel partner records appear only once, even when TREK v4 omits `reservation_id` linkage;
- accommodation Check-in and Check-out show calendar dates, including for time-only v4 values reconstructed from trip days or place assignments;
- car and taxi reservations appear under Cars & Taxis and do not appear under Flights;
- Flights and Reservations are visible even when empty;
- live flight information appears when a provider key is configured;
- Photos load when a Journey gallery share is configured;
- Immich-backed photos use original capture dates when Immich is configured;
- after session creation the complete owner-generated fragment remains in the address bar so a refresh can recreate the guest session; the fragment is not sent in normal HTTP request URLs.

## Step 16 — Verify logging safety

```bash
docker compose logs --tail=200 trek-guest-portal
```

Normal access routes should look like:

```text
POST /api/session
GET /api/trip
GET /api/journey
GET /api/flights/42
GET /api/photo-dates
```

Native TREK/Journey bearer values should not appear in request URLs or normal companion logs. Do not enable request-body logging for `/api/session`, because the initial JSON body contains the native share capabilities.

## Installation complete

Continue with:

- [CONFIGURATION.md](CONFIGURATION.md)
- [GUEST-ORIGIN.md](GUEST-ORIGIN.md)
- [REVERSE-PROXY.md](REVERSE-PROXY.md)
- [LOGGING.md](LOGGING.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [SECURITY.md](../SECURITY.md)
