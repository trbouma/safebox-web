# Docker Proxy and Forwarded HTTPS Trust

## Purpose

Safebox Web requires HTTPS for every non-loopback request. In the standard
deployment, TLS terminates at a reverse proxy on another Tailscale machine and
the proxy forwards plain HTTP over the private network to the Docker-published
Safebox Web port. The application accepts that request only when Uvicorn trusts
the immediate peer that supplied `X-Forwarded-Proto: https`.

Docker can introduce a non-obvious extra trust boundary. When `docker-proxy`
forwards the published host port into the container, Uvicorn may see the Docker
network gateway—not the reverse proxy's Tailscale address—as its immediate
peer. If that gateway is absent from Uvicorn's allowlist, the public site
returns:

```json
{"detail":"HTTPS is required. Plain HTTP is allowed only for direct development access at http://127.0.0.1:<port>."}
```

This note records how to diagnose and safely resolve that condition.

## Request and trust path

The deployed path has four distinct hops:

```text
Browser --HTTPS--> Nginx reverse proxy
        --HTTP over Tailscale--> published host port
        --docker-proxy--> Safebox Web container
        --trusted forwarded metadata--> FastAPI
```

Nginx is responsible for asserting the original transport:

```nginx
location / {
    proxy_pass http://100.70.55.66:8100;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Uvicorn must accept those headers only from its immediate trusted peer. It is
not enough for `FORWARDED_ALLOW_IPS` to contain the reverse proxy address if
Docker presents the connection to the container from a bridge gateway.

## Known working configuration

In the deployment that exposed this issue:

- reverse proxy (`ditto`): `100.101.156.95`;
- Safebox Web host (`beelink`): `100.70.55.66`;
- published Safebox Web port: `8100`;
- Docker gateway observed by the container: `172.20.0.1`.

The corresponding `.env` settings are:

```env
SAFEBOX_BIND_ADDRESS=0.0.0.0
SAFEBOX_PORT=8100
FORWARDED_ALLOW_IPS=100.101.156.95,172.20.0.1
```

An early version of this deployment bound the published port directly to the
host's Tailscale address. Intermittent TCP connection timeouts disappeared
after changing the bind address to `0.0.0.0` and recreating the container. For
a host whose application port is reachable only through the trusted VPN, this
is the recommended operational setting. On a host with other reachable
interfaces, pair it with a firewall or Tailscale ACL restricting port `8100`.

The Uvicorn command in `docker-compose.yaml` should pass the trust setting
explicitly:

```yaml
command:
  - uvicorn
  - app.main:app
  - --host
  - 0.0.0.0
  - --port
  - "8000"
  - --proxy-headers
  - --forwarded-allow-ips
  - "${FORWARDED_ALLOW_IPS}"
  - --workers
  - "${SAFEBOX_WEB_WORKERS:-1}"
```

After changing `.env` or the Compose command, recreate the container. A simple
restart does not replace its environment or command:

```sh
docker compose up -d --force-recreate safebox-web
```

## Diagnostic procedure

### 1. Confirm the reverse proxy header

The active TLS virtual host must set `X-Forwarded-Proto` and must have been
reloaded:

```sh
sudo nginx -t
sudo nginx -T | grep -A20 'server_name acorn.safebox.dev'
sudo systemctl reload nginx
```

### 2. Test the private upstream directly

From the reverse proxy machine:

```sh
curl -i \
  -H 'Host: acorn.safebox.dev' \
  -H 'X-Forwarded-Proto: https' \
  http://100.70.55.66:8100/health
```

A `200 OK` response proves that the application accepts the proxy assertion. A
`400 Bad Request` with the HTTPS-required message proves that the request
arrived but Uvicorn did not trust or apply the forwarded transport metadata.
It is not a DNS, TLS-certificate, or basic connectivity failure.

### 3. Confirm the running Uvicorn command

Do not rely only on the source Compose file. Inspect the running container:

```sh
docker inspect safebox-web --format '{{json .Config.Cmd}}'
```

The result must include:

```text
"--proxy-headers","--forwarded-allow-ips","100.101.156.95,172.20.0.1"
```

Also confirm the container environment:

```sh
docker compose exec safebox-web printenv FORWARDED_ALLOW_IPS
```

### 4. Identify Docker's immediate gateway

Inspect the address that may be presented as the immediate peer:

```sh
docker inspect safebox-web \
  --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'
```

If it returns `172.20.0.1`, that address must be included in
`FORWARDED_ALLOW_IPS` for this network layout. Use the value actually returned;
do not copy the example blindly.

Confirm that the intended container owns the published port:

```sh
sudo ss -ltnp | grep ':8100'
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Image}}'
```

### 5. Verify both paths

After recreating the container, repeat the private upstream test and then test
the public endpoint:

```sh
curl -i https://acorn.safebox.dev/health
```

Both should return `200 OK` and `{"status":"ok"}`.

## Security implications

Trusting a Docker gateway is broader than trusting the remote proxy address.
Every connection that Docker presents through that gateway can potentially
claim forwarded transport metadata. Compensating controls therefore matter:

- keep the published port reachable only through the trusted VPN, or use a
  host firewall when `0.0.0.0` also covers an untrusted interface;
- use a Tailscale ACL or host firewall to allow the upstream port only from the
  designated reverse proxy;
- retain the exact proxy and gateway allowlist; never use
  `FORWARDED_ALLOW_IPS=*` on a reachable service;
- do not expose the upstream HTTP port to the public internet; and
- keep TLS termination and nginx configuration under the trusted operator's
  control.

The Docker gateway may change if the Compose network is removed and recreated
or Docker allocates a different subnet. Recheck the gateway after deployment
or networking changes. If the HTTPS-required error suddenly returns after a
rebuild, network recreation, or host maintenance, this is one of the first
values to verify.

## Operational lesson

Forwarded-header trust follows the immediate network peer, not the conceptual
operator of the reverse proxy. Container networking can obscure that peer and
silently cause a correctly supplied `X-Forwarded-Proto` header to be ignored.
The reliable procedure is to inspect the running command, identify the Docker
gateway, restrict the published port's reachability where necessary, and test
the private upstream before testing the public hostname.

Application-level symptoms can also be misleading. A TCP connect timeout has
no Acorn, relay, mint, or FastAPI cause because none of those layers received
the request. Confirm repeated private `/health` connections before changing
wallet loading, record lookup, or relay verification behavior in response to
an apparent page timeout.
