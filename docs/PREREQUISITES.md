# Prerequisites

Before installing TREK Guest Portal 2.1.1, confirm the following.

## 1. Supported TREK deployment

- TREK `>=4.0.0 <5.0.0` is running in Docker.
- You can sign in to TREK as an administrator and upload plugins.
- You can identify the running TREK application container/service and its Docker network.

The companion normally connects to TREK using Docker DNS:

```dotenv
TREK_HOST=app
TREK_PORT=3000
```

The service name and port are configurable if your TREK deployment differs.

## 2. Docker Compose

Docker Engine and the Docker Compose v2 plugin must be available on the host that will run the companion:

```bash
docker version
docker compose version
```

You need permission to:

- create `/opt/trek-guest-portal` and its cache/secret files;
- start one additional container;
- attach that container to TREK's existing Docker network;
- publish TCP/8088 on loopback or one explicit LAN interface;
- view Docker Compose logs.

## 3. TREK Docker network name

The companion is intentionally deployed as its own Compose project. It joins the network already used by TREK.

Find the network attached to the TREK application container:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
docker inspect <TREK_CONTAINER> \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
```

Record the network that contains the TREK `app` service. You will set it as:

```dotenv
TREK_DOCKER_NETWORK=<exact-network-name>
```

## 4. HTTPS reverse proxy

Guest Portal should be exposed only through HTTPS.

Recommended layout:

```text
TREK:         https://trek.example.com/
Guest Portal: https://guest.example.com/
```

The dedicated guest hostname avoids interference from TREK's PWA Service Worker and keeps guest cookies/browser state isolated from the authenticated TREK origin.

A same-origin path remains supported:

```text
https://trek.example.com/guest-portal/
```

See [GUEST-ORIGIN.md](GUEST-ORIGIN.md) and [REVERSE-PROXY.md](REVERSE-PROXY.md).

## 5. Mapbox public token

Plan maps require a browser-safe Mapbox public token beginning with `pk...`.

Use a dedicated token with the minimum required scopes and URL restriction for the guest origin, for example:

```text
https://guest.example.com/*
```

Never put a secret `sk...` Mapbox token in `public/config.js`.

## 6. Optional live-flight provider

Live flight status uses AeroDataBox through RapidAPI plus adsb.fi aircraft data. This integration is optional.

If enabled, store the AeroDataBox/RapidAPI key only in:

```text
/opt/trek-guest-portal/secrets/aerodatabox_api_key
```

The provider's quota remains authoritative. Guest Portal suppresses AeroDataBox calls outside the configured live-data window and applies rate limiting/cache TTLs inside the window.

## 7. Optional Immich integration

Immich is optional. It is used to resolve original capture dates for Immich-backed Journey assets.

If enabled, create a dedicated least-privilege Immich API key and store it only in:

```text
/opt/trek-guest-portal/secrets/immich_api_key
```

## 8. DNS and firewall

For the recommended dedicated guest hostname:

- public DNS should point the guest hostname to the HTTPS reverse proxy;
- internal/split DNS can point the same hostname directly to the local proxy;
- TCP/8088 should not be internet-accessible;
- when the reverse proxy is local, keep `GUEST_PORTAL_BIND_IP=127.0.0.1`;
- when the reverse proxy is remote, bind TCP/8088 to one explicit LAN address and firewall it to the proxy host only.

## 9. Required TREK share permissions

The trip owner must create a native TREK public share. Guest Portal can only display data allowed by that underlying share.

- Plan requires the map/plan data to be shared.
- Flights, Reservations, and booking/accommodation items shown in the Plan timeline require Bookings sharing.
- Photos require a Journey public share with Gallery enabled.

Use this as the reminder before publishing a trip: enable Bookings in the native TREK trip share and enable Gallery in the native Journey share. If either is off, the guest portal intentionally hides the corresponding section.

Revoking/regenerating the native TREK/Journey share remains the authoritative way to revoke Guest Portal access.
