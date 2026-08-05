# Safebox Web Deployment Runbook

## Deployment model

Safebox Web builds **one Docker image** and runs it as **two containers with
different process entry points**:

| Compose service | Process | Responsibility |
| --- | --- | --- |
| `safebox-web` | `uvicorn app.main:app ...` | Web routes, handle resolution, and durable job creation |
| `service-acorn-worker` | `python -m app.service_acorn_worker run` | Exclusive provider Acorn ownership, mint settlement, and ecash delivery |

```text
one image: safebox-web:local
    |
    +-- safebox-web container
    |      `-- one or more Uvicorn web worker processes
    |
    `-- service-acorn-worker container
           `-- exactly one service Acorn process
```

The containers have separate memory. They communicate through the durable
`provider_payment` table in the shared database. Both mount the named
`safebox-web-data` volume at `/app/data`. The volume contains:

- `database.db`, including claimed handles and provider-payment jobs; and
- `service-acorn.json`, the provider wallet recovery secret while that wallet
  exists.

This is process ownership separation, not yet strict filesystem isolation. The
web code does not open the recovery file, but the current web container can
technically read the shared volume. Treat compromise of either container as a
provider-key risk. A production hardening step should move service-Acorn
recovery material to a worker-only volume while leaving the database on shared
storage. Do not change the path casually on an existing deployment: migrate the
recovery file while the worker is stopped or the next start will create a
different wallet and abandon the old authority.

Never run more than one `service-acorn-worker` container against that state.
`SAFEBOX_WEB_WORKERS` may be greater than one because those processes never own
the provider wallet. Multiple web workers improve request capacity but do not
remove SQLite or singleton-worker limits. See
[Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md).

## 1. Prepare the Acorn dependency

The Docker image installs the Safebox Acorn Git commit pinned in `poetry.lock`.
When deploying newer Acorn work, update and review the lock before building:

```sh
poetry update safebox-acorn
git diff poetry.lock
```

Commit the intended lock-file change so the deployment does not silently
follow a moving branch.

## 2. Prepare runtime configuration

Create the private environment file:

```sh
cp .env.example .env
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
chmod 600 .env
```

Place the generated value in `SAFEBOX_COOKIE_KEY` and review at least:

```env
SAFEBOX_COOKIE_KEY=<new private key>
SAFEBOX_DATABASE_URL=sqlite:///data/database.db

SAFEBOX_BIND_ADDRESS=127.0.0.1
SAFEBOX_PORT=8000
FORWARDED_ALLOW_IPS=127.0.0.1
SAFEBOX_WEB_WORKERS=1

SAFEBOX_SERVICE_ACORN_ENABLED=true
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://relay.getsafebox.app
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_SERVICE_ACORN_STATE_FILE=data/service-acorn.json
SAFEBOX_SERVICE_ACORN_POLL_SECONDS=0.5
SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS=604800
```

If `SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS` is absent, both the
application settings and Compose configuration default it to seven days. Set
it explicitly to `0` to omit the NIP-40 expiration tag. Non-zero values must be
between `3600` seconds (one hour) and `2592000` seconds (30 days).

Use the actual proxy address for `FORWARDED_ALLOW_IPS` when TLS terminates on
another machine. Never commit `.env`.

## 3. Validate and build the image

Validate the complete two-service configuration and build the shared image:

```sh
docker compose config --quiet
docker compose build
```

Because both services use the same image, Docker performs one application
build and uses it with two different process commands.

## 4. Create and run both containers

Build and start both roles in one command:

```sh
docker compose up --detach --build
```

The web container starts first, runs database migrations, and must become
healthy before Compose starts the service Acorn worker. Verify both:

```sh
docker compose ps
docker compose logs --follow safebox-web service-acorn-worker
```

Expected roles include:

```text
safebox-web             uvicorn app.main:app ...
service-acorn-worker    python -m app.service_acorn_worker run
```

Both services are part of the normal Compose project. For an intentional
web-only development run, target the web service explicitly:

```sh
docker compose stop service-acorn-worker
docker compose up --detach safebox-web
```

Lightning payments to handles require both services and
`SAFEBOX_SERVICE_ACORN_ENABLED=true`.

## 5. Verify the deployed path

Verify the public transport and application health:

```sh
curl -i https://acorn.example.com/health
```

For a claimed handle, verify LNURL-pay discovery:

```sh
curl -s https://acorn.example.com/.well-known/lnurlp/alice
```

The response should contain an HTTPS `callback`, `tag: "payRequest"`, and the
configured send limits. A small real Lightning payment should produce worker
log transitions for invoice creation, settlement, and gift-wrapped ecash
delivery. The recipient must still explicitly receive incoming ecash through
its Acorn interface.

Use small test amounts until the remaining release gates in
[Lightning Payments to Acorn Handles](LIGHTNING-HANDLE-PAYMENTS.md) are closed.

## Routine operations

Restart both roles without rebuilding:

```sh
docker compose restart
```

Rebuild and recreate both roles after a code or lock-file change:

```sh
docker compose up --detach --build
```

Follow only the provider worker:

```sh
docker compose logs --follow service-acorn-worker
```

Stop both roles:

```sh
docker compose stop
```

Remove the containers and network while retaining the named data volume:

```sh
docker compose down
```

Do **not** add `--volumes` or run `docker compose down -v` unless permanent
destruction of the directory, job history, and provider recovery material is
explicitly intended.

A routine stop does not burn the service Acorn. The worker retains
`service-acorn.json` and restores the same wallet on restart.

## Explicit provider-wallet retirement

Retirement is not ordinary shutdown. First stop the singleton worker and set a
safe recovery recipient:

```env
SAFEBOX_SERVICE_ACORN_SHUTDOWN_RECIPIENT=<npub-or-nip05>
SAFEBOX_SERVICE_ACORN_SHUTDOWN_RELAY=wss://relay.getsafebox.app
```

Then run:

```sh
docker compose stop service-acorn-worker
docker compose run --rm service-acorn-worker \
  python -m app.service_acorn_worker retire
```

Successful retirement sweeps remaining funds, burns relay state, and removes
the recovery file. Failures retain the file for reconciliation.

## Backup and restore boundary

The named data volume is operationally sensitive. Back it up only through an
operator-controlled procedure, preferably while both containers are stopped.
The backup contains the plaintext service `nsec` protected by file permissions
and therefore has the authority to recover or spend provider funds.

Restoring a volume and starting a second worker while the original is active
would create two owners of the same proofs. A restore procedure must guarantee
that only one worker instance can run.
