# Tailscale Reverse-Proxy Deployment

## Summary

Safebox Web can run on one private machine while a separate machine provides
its public HTTPS endpoint. Tailscale supplies the encrypted private network
between them, and Safebox Web explicitly trusts only the designated reverse
proxy to describe the original browser transport.

The first validated deployment used:

```text
browser
  -> HTTPS https://acorn.example.com
  -> Nginx on the proxy host (example Tailscale IP 100.64.0.10)
  -> HTTP over Tailscale
  -> Safebox Web host (example Tailscale IP 100.64.0.20), host port 8100
  -> Safebox Web container port 8000
```

The public browser connection is HTTPS. The proxy-to-application connection is
HTTP inside the Tailscale network. Safebox Web accepts that internal request as
secure only when Uvicorn receives `X-Forwarded-Proto: https` from the configured
proxy address.

## Two independent controls

These settings solve different problems:

```env
SAFEBOX_BIND_ADDRESS=0.0.0.0
FORWARDED_ALLOW_IPS=100.64.0.10
```

`SAFEBOX_BIND_ADDRESS=0.0.0.0` makes the published container port reachable on
all host interfaces. It does not grant proxy authority and does not make plain
HTTP acceptable to the application.

`FORWARDED_ALLOW_IPS=100.101.156.95` tells Uvicorn that only the immediate peer
at that address may supply authoritative forwarded headers. Another reachable
client cannot make an HTTP request secure merely by adding its own
`X-Forwarded-Proto` header.

The surrounding Tailscale ACL or host firewall should additionally restrict
the application port to the proxy machine. That network restriction and the
application's proxy allowlist are complementary layers.

## Safebox Web configuration

On the machine running Safebox Web, create `.env` from `.env.example`, generate
a new cookie key, and use:

```env
SAFEBOX_COOKIE_KEY=<new private URL-safe 32-byte application key>
SAFEBOX_SESSION_TTL_HOURS=720
SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS=20
SAFEBOX_PAYMENT_TIMEOUT_SECONDS=90
SAFEBOX_DEFAULT_BOOTSTRAP_RELAY=wss://relay.getsafebox.app
SAFEBOX_DEFAULT_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_DATABASE_URL=sqlite:///data/database.db
RECEIVE_PROOF_MAINTENANCE_ENABLED=false

SAFEBOX_SERVICE_ACORN_ENABLED=true
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://relay.getsafebox.app
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_SERVICE_ACORN_STATE_FILE=data/service-acorn.json
SAFEBOX_SERVICE_ACORN_POLL_SECONDS=0.5
SAFEBOX_PROVIDER_INVOICE_WAIT_SECONDS=10
SAFEBOX_LNURL_MIN_SENDABLE_MSAT=1000
SAFEBOX_LNURL_MAX_SENDABLE_MSAT=100000000
SAFEBOX_LNURL_COMMENT_ALLOWED=256

SAFEBOX_BIND_ADDRESS=0.0.0.0
SAFEBOX_PORT=8100
FORWARDED_ALLOW_IPS=100.64.0.10
SAFEBOX_IMAGE=safebox-web:local
```

Docker Compose runs the same image as two containers: `safebox-web` runs
Uvicorn, while `service-acorn-worker` exclusively owns the provider wallet.
Both mount the named volume `safebox-web-data` at `/app/data`. The volume holds
the NIP-05 directory, provider-payment jobs, and service Acorn recovery file.
Keep it when recreating containers and protect its backups as wallet secrets.
Removing it can destroy claimed-handle mappings, job history, and provider
wallet recovery authority.

The `100.64.0.10` and `100.64.0.20` addresses in this guide are examples.
Replace them with the proxy and application hosts' actual Tailscale addresses.
Keep those deployment-specific values in `.env` and private infrastructure
configuration rather than committing them to the repository.

Generate the cookie key without installing another package:

```sh
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
chmod 600 .env
```

Never commit or share the completed `.env`. Anyone with the cookie key and a
captured session cookie can recover the `nsec` contained in that session.

Start the application:

```sh
docker compose config --quiet
docker compose build
docker compose up --detach
docker compose ps
docker compose logs --follow safebox-web service-acorn-worker
```

The web container becomes healthy first; Compose then starts the provider
worker. Repeated web log
entries such as the following are its internal loopback health check:

```text
127.0.0.1:<port> - "GET /health HTTP/1.1" 200 OK
```

The complete build, restart, stop, volume, and explicit wallet-retirement
commands are in the [Deployment Runbook](DEPLOYMENT.md).

## Nginx configuration

On the trusted reverse proxy, the HTTPS virtual host should contain:

```nginx
location / {
    client_max_body_size 11m;
    proxy_pass http://100.64.0.20:8100;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Hard-coding `https` is appropriate only in the TLS virtual host. If the same
location can receive plain HTTP, redirect that traffic to HTTPS rather than
forwarding it with a false secure scheme.

Safebox Web currently has no browser-facing WebSocket route. `Upgrade` and
`Connection: upgrade` headers are therefore unnecessary. Acorn's Nostr relay
connections are outbound from the application and do not traverse this Nginx
location.

Validate and reload Nginx:

```sh
nginx -t
systemctl reload nginx
```

## Verification sequence

Run these commands on the proxy machine.

### 1. Confirm private reachability

```sh
tailscale ping safebox-web-host
```

Tailscale's CLI may recognize a peer name even when the operating system does
not have Tailscale MagicDNS configured. If `curl http://beelink:8100` reports
that it cannot resolve the host, use the peer's Tailscale IP or enable
MagicDNS. The IP is valid as the reverse-proxy upstream.

### 2. Confirm direct HTTP rejection

```sh
curl -i http://100.64.0.20:8100/health
```

Expected result:

```text
HTTP/1.1 400 Bad Request
{"detail":"HTTPS is required..."}
```

This negative test proves that private-network reachability alone does not
bypass Safebox Web's transport policy.

### 3. Confirm trusted proxy interpretation

```sh
curl -i \
  -H "X-Forwarded-Proto: https" \
  http://100.64.0.20:8100/health
```

Expected result:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

This proves that the designated proxy address is trusted to describe the
original transport.

### 4. Confirm the public endpoint

```sh
curl -i https://acorn.example.com/health
```

Expected behavior includes `200 OK`, `{"status":"ok"}`, `Cache-Control:
no-store`, the configured content-security headers, and HSTS. Open the public
URL in a browser only after this test passes.

## Troubleshooting

### Public request reports that HTTPS is required

The application received the upstream request as plain HTTP. Confirm that:

- Nginx sends `X-Forwarded-Proto: https` in the TLS virtual host;
- `FORWARDED_ALLOW_IPS` contains the immediate address Uvicorn actually sees;
- Docker or another proxy layer has not changed that source address; and
- Nginx was reloaded after validation.

Do not solve the problem by setting `FORWARDED_ALLOW_IPS=*` on a reachable
service. Inspect `docker compose logs --follow safebox-web`, identify the
immediate proxy address, and allow only that address or a narrowly scoped
private network.

When the published port is handled by `docker-proxy`, the immediate peer seen
inside the container may instead be the Docker network gateway. Follow
[Docker Proxy and Forwarded HTTPS Trust](DOCKER-PROXY-FORWARDED-HEADER-TRUST.md)
to identify that address, pass the allowlist explicitly to Uvicorn, and limit
the upstream port to the Tailscale interface and designated proxy.

### Container remains in `health: starting`

Wait through the configured health-check startup period and run:

```sh
docker compose ps
docker compose logs --tail 50 safebox-web
```

Application startup followed by internal `/health` responses with status 200
indicates a healthy service.

## Trust statement

This deployment trusts different layers for different purposes:

- the public reverse proxy for TLS termination and public routing;
- the domain owner and DNS provider for directing NIP-05 discovery to the
  intended HTTPS service;
- Tailscale and its access policy for the private machine-to-machine path;
- the exact proxy allowlist for forwarded transport metadata;
- Docker and the host for process isolation and service availability;
- the Safebox Web process and cookie key for temporary browser-session custody;
- the Safebox Web operator and directory database for truthful NIP-05
  name-to-key and relay mappings;
- Acorn for wallet, relay, record, mint, and payment behavior; and
- the user for protecting the recovery material entered during connection.

The VPN does not make every peer a trusted reverse proxy. The bind address does
not define proxy authority. The forwarded-header allowlist does not replace a
firewall. Keeping these controls separate makes the deployment easier to test
and the trust assumptions easier to explain.

The allowlist also does not constrain what an authorized reverse proxy does
before a request reaches Safebox. That proxy terminates browser TLS and chooses
the upstream service. It can route the domain to another application, replace
responses, or present a counterfeit connection surface without the intended
Safebox process receiving a request at all. The application can authenticate
forwarded metadata from its immediate peer; it cannot prove that the proxy
routed public traffic to the intended backend. Proxy administration,
configuration review, and independent monitoring of the public domain are
therefore part of the service trust model.
