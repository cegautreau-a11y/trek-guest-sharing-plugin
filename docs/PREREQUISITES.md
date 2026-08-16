# Prerequisites

This guide assumes TREK itself is already installed and working. If it is not, install TREK first using the upstream [TREK Docker documentation](https://github.com/liketrek/TREK/wiki/Install-Docker).

## Required

### 1. TREK 3.4.x

Guest Portal's plugin manifest allows:

```text
>=3.4.0 <4.0.0
```

The current code was developed against TREK 3.4.x. Do not force the plugin onto TREK 4.x without testing the plugin API and public-share payloads first.

### 2. Docker or Portainer access

You need permission to create one additional container and persistent host directories. The recommended deployment puts the companion in the same Compose/Portainer stack as TREK so `TREK_HOST=app` resolves automatically.

### 3. HTTPS reverse proxy and DNS

Guests should reach one HTTPS origin such as:

```text
https://trek.example.com
```

The reverse proxy must send `/guest-portal/` to the companion and normal `/` requests to TREK. HTTPS is required because guest sessions use a `Secure` cookie.

Apache and Nginx examples are provided in [REVERSE-PROXY.md](REVERSE-PROXY.md).

### 4. A Mapbox public token

The Plan tab uses Mapbox. Create a dedicated **public (`pk...`)** token and preferably restrict it to the Guest Portal web origin, for example:

```text
https://trek.example.com/*
```

Mapbox documents URL restrictions for public web tokens. Do not use a secret Mapbox token in browser `config.js`.

## Native TREK share prerequisites

Guest Portal does not bypass TREK sharing. It reuses the permissions of native public shares.

For each trip you want to expose:

- Create a normal TREK public trip share.
- Keep **Bookings** enabled if you want Flights and Reservations data. TREK's public-share documentation states that `share_bookings` controls reservations and transport exposure.
- Map/Plan sharing remains part of TREK's public trip share.

If you want Photos:

- Enable the TREK Journey addon.
- Create/link a Journey for the trip.
- Create a Journey public share with **Gallery** enabled.

## Optional: Immich

Immich is only required if you want Guest Portal to resolve the original capture date for Journey photos imported from Immich.

You need:

- an Immich base URL reachable from the companion container;
- a dedicated Immich API key with the minimum asset-read permissions needed to retrieve asset metadata.

Immich allows API keys to have scoped permissions. Keep the key in a mounted secret file, not `config.js`.

## Optional: live flight information

TREK's scheduled transport cards work without AeroDataBox. For live flight schedule/status information you need an AeroDataBox subscription/key.

The companion is tuned conservatively for 1-request/second plans by serializing provider calls, spacing them by at least 1.6 seconds by default, caching results, and backing off on HTTP 429 responses. Check your current provider plan because quotas/rate limits can change.

If you already use the third-party TREK Flight Tracker plugin, Guest Portal includes a one-shot helper that can extract the existing key **only when Flight Tracker stored it in its own SQLite `kv` table**. If the key is managed through TREK's encrypted Admin plugin configuration, create the Guest Portal secret manually instead.

## Host access checklist

Before starting, confirm you can:

- upload a plugin ZIP through TREK Admin;
- edit/redeploy the TREK Portainer/Compose stack;
- create directories on the Docker host;
- edit the Apache/Nginx reverse-proxy configuration;
- restart/reload the reverse proxy;
- view `docker logs trek-guest-portal` or Portainer container logs.

Continue with [INSTALL.md](INSTALL.md).
