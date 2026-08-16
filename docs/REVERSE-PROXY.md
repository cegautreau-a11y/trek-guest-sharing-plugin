# Reverse Proxy

Guest Portal is designed to be served under the same HTTPS origin as TREK, normally at:

```text
https://trek.example.com/guest-portal/
```

This keeps the public site same-origin with TREK and allows the hardened session-origin check to use one exact `PUBLIC_ORIGIN`.

## Apache

Enable the modules your existing TREK proxy already needs (`proxy`, `proxy_http`, and normally `headers`; TREK WebSockets may also use proxy WebSocket support).

Add Guest Portal **before** the final TREK `/` catch-all:

```apache
# Existing TREK /ws and /mcp rules, if present, remain before the catch-all.

ProxyPass        "/guest-portal/" "http://127.0.0.1:8088/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/guest-portal/" "http://127.0.0.1:8088/"
RedirectMatch 302 ^/guest-portal$ /guest-portal/

# Existing TREK catch-all must remain last.
ProxyPass        "/" "http://127.0.0.1:3300/" connectiontimeout=5 timeout=300 retry=0
ProxyPassReverse "/" "http://127.0.0.1:3300/"
```

If Apache is on another host, replace `127.0.0.1` with the specific Docker host IP and restrict TCP/8088 at the firewall to the Apache host.

Validate and reload:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
```

## Nginx

Inside the HTTPS TREK server block:

```nginx
location /guest-portal/ {
    proxy_pass http://127.0.0.1:8088/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

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

The initial session exchange intentionally puts native TREK/Journey share tokens in a JSON POST body so they are absent from normal request URLs. Standard Apache `combined` access logs do not log request bodies. Do not add custom debug/request-body logging for `/guest-portal/api/session`.

## Direct-port exposure

Prefer:

```yaml
ports:
  - "127.0.0.1:8088:8080"
```

when proxy and Docker are on the same machine.

If a remote proxy needs the port, bind to one explicit LAN address rather than `0.0.0.0`, and firewall the port to the proxy source address.
