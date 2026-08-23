# Reverse Proxy

Guest Portal supports a dedicated guest hostname and a same-origin path. A dedicated guest hostname is recommended because it isolates the anonymous portal from TREK's PWA Service Worker and authenticated browser state.

## Recommended dedicated hostname

```text
TREK:         https://trek.example.com/
Guest Portal: https://guest.example.com/
```

Companion settings:

```dotenv
PUBLIC_ORIGIN=https://guest.example.com
TREK_PUBLIC_ORIGIN=https://trek.example.com
COOKIE_PATH=/
```

### Apache

```apache
<VirtualHost *:443>
    ServerName guest.example.com

    SSLEngine on
    # Certificate directives here.

    ProxyPreserveHost On
    ProxyPass        "/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
    ProxyPassReverse "/" "http://127.0.0.1:8088/"
</VirtualHost>
```

### Nginx

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

## Same-origin alternative

For:

```text
https://trek.example.com/guest-portal/
```

use:

```dotenv
PUBLIC_ORIGIN=https://trek.example.com
TREK_PUBLIC_ORIGIN=
COOKIE_PATH=/guest-portal/
```

### Apache path routing

The Guest Portal rules must appear before TREK's catch-all `/` proxy:

```apache
ProxyPass        "/guest-portal/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/guest-portal/" "http://127.0.0.1:8088/"
RedirectMatch 302 ^/guest-portal$ /guest-portal/

# Existing TREK catch-all comes after Guest Portal.
ProxyPass        "/" "http://127.0.0.1:3300/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/" "http://127.0.0.1:3300/"
```

### Nginx path routing

```nginx
location = /guest-portal {
    return 302 /guest-portal/;
}

location /guest-portal/ {
    proxy_pass http://127.0.0.1:8088/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Local vs remote reverse proxy

When the reverse proxy is on the same Docker host:

```dotenv
GUEST_PORTAL_BIND_IP=127.0.0.1
```

When the reverse proxy is on another host:

```dotenv
GUEST_PORTAL_BIND_IP=10.0.0.20
```

Then restrict TCP/8088 at the firewall so only the reverse-proxy host can connect. Do not expose TCP/8088 directly to the internet.

## Real client IPs and trusted proxy headers

By default the companion logs the immediate TCP peer only when client-IP logging is enabled. It does **not** trust arbitrary `X-Forwarded-For` or `CF-Connecting-IP` headers.

The recommended design makes the reverse proxy the client-IP trust boundary:

1. the proxy resolves the real client address using its own trusted-proxy configuration;
2. the proxy removes any inbound `X-Guest-Client-IP` supplied by the client;
3. the proxy creates a fresh `X-Guest-Client-IP` from its resolved client address;
4. the companion accepts that header only when the TCP peer belongs to `TRUSTED_PROXY_CIDRS`.

Companion settings:

```dotenv
LOG_CLIENT_IP=true
TRUST_PROXY_HEADERS=true
CLIENT_IP_HEADER=X-Guest-Client-IP
TRUSTED_PROXY_CIDRS=192.0.2.10/32
LOG_PROXY_DETAILS=true
```

Replace the example CIDR with the **actual immediate proxy peer address as seen by the companion**.

### Apache trusted-client header

When Apache has already resolved the correct client address:

```apache
RequestHeader unset X-Guest-Client-IP
RequestHeader set X-Guest-Client-IP "expr=%{REMOTE_ADDR}"
ProxyAddHeaders On
```

If Cloudflare is in front of Apache, configure `mod_remoteip` to trust only Cloudflare's current published edge networks before creating the application-specific header:

```apache
RemoteIPHeader CF-Connecting-IP
RemoteIPTrustedProxyList /etc/apache2/cloudflare-ips.conf
```

Do not hard-code stale Cloudflare networks in this project.

### Nginx trusted-client header

After Nginx has resolved the real client address using its own `real_ip` configuration:

```nginx
proxy_set_header X-Guest-Client-IP $remote_addr;
```

The companion should trust only the Nginx peer CIDR, not Cloudflare/client networks directly.

## Forwarded Host behavior

The companion validates guest browser origin against `PUBLIC_ORIGIN`, but server-side TREK public-share requests use `TREK_PUBLIC_ORIGIN` for the Host and X-Forwarded-Host values. This distinction is required when the guest site uses a dedicated hostname but TREK itself validates requests based on its public host.

## Validate changes

Apache:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
```

Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Then verify:

```bash
curl -I https://guest.example.com/
```

or for a same-origin deployment:

```bash
curl -I https://trek.example.com/guest-portal/
```

## Do not log session request bodies

The initial session exchange places native TREK/Journey share capabilities in the JSON body of `POST /api/session` so they are absent from normal request URLs. Do not enable request-body logging for the session endpoint.
