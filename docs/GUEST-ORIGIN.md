# Dedicated Guest Origin

## Why a separate hostname is recommended

TREK is a Progressive Web App and can register a Service Worker on its application origin. When Guest Portal is served under that same origin, a browser that already uses TREK may allow TREK's Service Worker to handle navigation before the reverse proxy or companion receives it.

The robust layout is:

```text
TREK application: https://trek.example.com/
Guest Portal:     https://guest.example.com/
```

Both DNS names may point to the same reverse proxy. The separation is at the browser-origin level, not necessarily at the server level.

Benefits:

- TREK's Service Worker cannot control the guest hostname;
- the guest session cookie is host-scoped to the guest hostname;
- authenticated TREK browser state is isolated from the guest site;
- guests can open links in the same browser where TREK is already signed in.

## Companion settings

Use:

```dotenv
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

`PUBLIC_ORIGIN` is the exact origin guests visit. `TREK_PUBLIC_ORIGIN` is used only for the Host/X-Forwarded-Host values on server-side validation calls to TREK.

The two origins must use HTTPS and must not include paths, query strings, or fragments.

## Docker exposure

When the reverse proxy runs on the same Docker host:

```dotenv
GUEST_PORTAL_BIND_IP=127.0.0.1
```

When the reverse proxy runs on another machine, bind to one explicit LAN interface:

```dotenv
GUEST_PORTAL_BIND_IP=10.0.0.20
```

Then firewall TCP/8088 so only the reverse proxy can connect.

## Apache dedicated vhost

```apache
<VirtualHost *:443>
    ServerName guest.example.com

    SSLEngine on
    # Certificate directives here.

    ProxyPreserveHost On
    ProxyPass        "/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
    ProxyPassReverse "/" "http://127.0.0.1:8088/"

    ErrorLog ${APACHE_LOG_DIR}/trek-guest-error.log
    CustomLog ${APACHE_LOG_DIR}/trek-guest-access.log combined
</VirtualHost>
```

If Apache is remote, replace `127.0.0.1` with the Docker host address configured by `GUEST_PORTAL_BIND_IP`.

## Nginx dedicated server

```nginx
server {
    listen 443 ssl;
    server_name guest.example.com;

    # TLS certificate directives here.

    location / {
        proxy_pass http://127.0.0.1:8088/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If the proxy is remote, use the Docker host's explicit LAN address instead of loopback.

## Public and local DNS

For internet access, point `guest.example.com` to the public reverse proxy. For local access, split DNS may point the same hostname directly to the local reverse proxy.

The browser should always see the same guest origin:

```text
https://guest.example.com/
```

This avoids changing cookie scope, Mapbox URL restrictions, or origin validation between internal and external use.

## Plugin configuration

Inside the TREK Guest Portal plugin, set **Guest Portal web address** to:

```text
https://guest.example.com/
```

The generated owner link then looks like:

```text
https://guest.example.com/#trip=...&journey=...&title=...
```

Native TREK/Journey share capabilities do not need to change merely because the guest hostname changes.

## Mapbox restriction

If your Mapbox public token is URL-restricted, allow the guest hostname:

```text
https://guest.example.com/*
```

## Migration checklist

1. Create public/internal DNS for the guest hostname.
2. Add the HTTPS reverse-proxy vhost/server.
3. Set `PUBLIC_ORIGIN` to the guest origin.
4. Set `TREK_PUBLIC_ORIGIN` to the TREK origin.
5. Set `COOKIE_PATH=/`.
6. Confirm `GUEST_PORTAL_BIND_IP` matches the proxy topology.
7. Run `docker compose config`.
8. Run `docker compose up -d`.
9. Update the plugin's **Guest Portal web address**.
10. Update Mapbox URL restrictions if enabled.
11. Test the generated guest link in the same browser where TREK is already signed in.
