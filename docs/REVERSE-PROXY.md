# Reverse Proxy

Guest Portal is designed to be served under the same HTTPS origin as TREK, normally at:

```text
https://trek.example.com/guest-portal/
```

This keeps the public site same-origin with TREK and allows the hardened session-origin check to use one exact `PUBLIC_ORIGIN`.

## Apache

Enable the modules your existing TREK proxy already needs (`proxy`, `proxy_http`, `headers`; TREK WebSockets may also use proxy WebSocket support).

Add Guest Portal **before** the final TREK `/` catch-all:

```apache
ProxyPass        "/guest-portal/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/guest-portal/" "http://127.0.0.1:8088/"
RedirectMatch 302 ^/guest-portal$ /guest-portal/

ProxyPass        "/" "http://127.0.0.1:3300/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/" "http://127.0.0.1:3300/"
```

If Apache is on another host, replace `127.0.0.1` with the specific Docker host IP and restrict TCP/8088 at the firewall to the Apache host.

## Real client IPs: Cloudflare externally + local split DNS

A common deployment is:

```text
Internet client -> Cloudflare -> Apache -> Guest Portal
LAN client -------------------> Apache -> Guest Portal
```

Without additional configuration the companion sees the Apache proxy's IP. Do **not** solve this by blindly trusting `X-Forwarded-For` or `CF-Connecting-IP` in the application; those headers are spoofable when supplied by an untrusted peer.

The recommended design is to make Apache the client-IP trust boundary:

1. `mod_remoteip` trusts `CF-Connecting-IP` only when the TCP peer belongs to Cloudflare's published networks;
2. external requests therefore become the original public client IP inside Apache;
3. local split-DNS requests connect directly to Apache and retain their LAN source IP;
4. Apache removes any client-supplied `X-Guest-Client-IP` and creates a fresh one from its resolved `REMOTE_ADDR`;
5. the companion trusts that header only when the TCP peer is your known Apache server.

Enable the required modules:

```bash
sudo a2enmod remoteip headers proxy proxy_http
```

Create `/etc/apache2/cloudflare-ips.conf` containing Cloudflare's **current** IPv4 and IPv6 edge CIDRs, one per line. Cloudflare publishes the current ranges; do not freeze an old list in your application repository.

Then add to the Apache vhost/server configuration:

```apache
RemoteIPHeader CF-Connecting-IP
RemoteIPTrustedProxyList /etc/apache2/cloudflare-ips.conf

RequestHeader unset X-Guest-Client-IP
RequestHeader set X-Guest-Client-IP "expr=%{REMOTE_ADDR}"
ProxyAddHeaders On
```

For Apache access logs, `%a` is the resolved client/user-agent IP after `mod_remoteip`; `%{c}a` is the underlying TCP peer. A useful optional format is:

```apache
LogFormat "%a peer=%{c}a %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\"" trek_realip
CustomLog ${APACHE_LOG_DIR}/trek-access.log trek_realip
```

For the Guest Portal container set:

```yaml
- LOG_CLIENT_IP=true
- TRUST_PROXY_HEADERS=true
- CLIENT_IP_HEADER=X-Guest-Client-IP
- TRUSTED_PROXY_CIDRS=192.0.2.10/32
- LOG_PROXY_DETAILS=true
```

Replace `192.0.2.10/32` with the **actual Apache server IP as seen by the companion**. If more than one Apache/reverse-proxy node can connect to port 8088, list them comma-separated.

When configured correctly, container events look like:

```text
http.request_start req=... client=198.51.100.27 client_source=trusted-proxy-header proxy_peer=192.0.2.10 cf_ray=... method=GET target=/api/flights/42
```

A local split-DNS request instead records its LAN client address:

```text
http.request_start req=... client=10.20.30.44 client_source=trusted-proxy-header proxy_peer=192.0.2.10 method=GET target=/api/trip
```

If an untrusted machine connects directly to the companion, forwarded client-IP headers are ignored and the socket peer is logged instead.

Validate and reload Apache:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
```

## Nginx

Inside the HTTPS TREK server block, sanitize a dedicated header rather than trusting arbitrary browser XFF values in the application:

```nginx
location /guest-portal/ {
    proxy_pass http://127.0.0.1:8088/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Guest-Client-IP $remote_addr;
}
```

If Nginx is itself behind Cloudflare, configure Nginx's real-IP module with Cloudflare's current ranges first so `$remote_addr` is normalized safely.

## Required application settings

For:

```text
https://trek.example.com/guest-portal/
```

set:

```yaml
- PUBLIC_ORIGIN=https://trek.example.com
- COOKIE_PATH=/guest-portal/
```

`PUBLIC_ORIGIN` must not contain `/guest-portal/`.

## Do not log request bodies

The initial session exchange intentionally puts native TREK/Journey share tokens in a JSON POST body so they are absent from normal request URLs. Standard Apache access logs do not log request bodies. Do not add custom debug/request-body logging for `/guest-portal/api/session`.

## Direct-port exposure

Prefer:

```yaml
ports:
  - "127.0.0.1:8088:8080"
```

when proxy and Docker are on the same machine.

If a remote proxy needs the port, bind to one explicit LAN address rather than `0.0.0.0`, and firewall the port to the proxy source address. This is also what makes `TRUSTED_PROXY_CIDRS` a meaningful security boundary.
