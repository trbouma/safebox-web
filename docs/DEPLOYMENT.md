# Safebox Web Deployment Runbook

## Deployment model

Safebox Web builds **one Docker image** and runs it as **two containers with
different process entry points**:

| Compose service | Process | Responsibility |
| --- | --- | --- |
| `safebox-web` | `uvicorn app.main:app ...` | Web routes, handle resolution, and durable job creation |
| `service-acorn-worker` | `python -m app.service_acorn_worker run` | Exclusive provider Acorn ownership, mint settlement, and ecash delivery |

The singleton worker also refreshes optional informational currency rates. That
task is isolated from the service Acorn object: failures retain last-known-good
rows and do not interrupt provider-payment processing. See
[Informational Currency Rate Cache](CURRENCY-RATE-CACHE.md).

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
- non-secret attached-Acorn Cash-finalization and Clear-acceptance leases and
  progress; and
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

For a proposed next step that replaces plaintext provider-secret storage with
separate, audited, least-privilege secret delivery, see the
[OpenBao Integration Note](OPENBAO-INTEGRATION-NOTE.md). OpenBao is not part of
the current deployment and should not be introduced without completing the
documented migration and recovery tests.

Never run more than one `service-acorn-worker` container against that state.
`SAFEBOX_WEB_WORKERS` may be greater than one because those processes never own
the provider wallet. Multiple web workers improve request capacity but do not
remove SQLite or singleton-worker limits. See
[Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md).

The web process can also run a session-bound background task when a connected
user asks to finalize pending cash payments. This is not service-Acorn work:
the recipient nsec remains only in that web process's memory. SQLite or
PostgreSQL stores only a public-key-scoped lease and non-secret progress. A
deployment restart interrupts that task, after which the user can reconnect
and resume from relay-backed payment receipts. Each web process also maintains
a non-secret database heartbeat. Graceful shutdown marks its in-memory jobs
interrupted immediately; after forced termination, another process treats the
old owner as stopped after about one minute and can reclaim the job without
waiting for the longer safety lease. A busy asyncio loop does not create a
false failure signal because the process heartbeat runs in a dedicated thread.
Do not delete the database volume merely to clear a job.

Clear transfer acceptance uses the same boundary. The database stores only the
recipient npub, transfer event id, phase, result metadata, and lease—not the
nsec, Clear bearer token, or proofs. A restart may interrupt the in-memory task;
the connected user can start acceptance again after the former process is
confirmed stopped, and Acorn resumes from its relay-backed receipt and proof
state.

## 1. Prepare the Acorn dependency

The Docker image installs the Safebox Acorn Git commit pinned in `poetry.lock`.
When deploying newer Acorn work, update and review the lock before building:

```sh
poetry update safebox-acorn
git diff poetry.lock
```

Commit the intended lock-file change so the deployment does not silently
follow a moving branch.

Safebox Web requests Acorn's `bitcoin` extra. After publishing a new Acorn
revision that introduces or changes this capability, update Acorn first and
confirm that the resulting lock contains `btclib` but no `openetr` package:

```sh
poetry update safebox-acorn
grep -E 'name = "(safebox-acorn|btclib|openetr)"' poetry.lock
```

Do not build the deployment image from a Safebox Web lock that predates the
corresponding Acorn commit. The web source may import the new API successfully
from an editable checkout while a clean Docker build still resolves the older
Git revision.

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
SAFEBOX_ALLOWED_WS_RELAYS=
SAFEBOX_OPENETR_RELAYS=wss://relay.openetr.org
SAFEBOX_OPENETR_PUBLIC_BASE_URL=https://openetr.org/etr
SAFEBOX_OPENETR_QUERY_TIMEOUT_SECONDS=5
SAFEBOX_OPENETR_QUERY_LIMIT=100
SAFEBOX_BLOSSOM_HOME_SERVER=https://blossom.getsafebox.app
SAFEBOX_MAX_BLOB_BYTES=10485760

SAFEBOX_BITCOIN_API_BASE=https://blockstream.info/api
SAFEBOX_BITCOIN_LOOKUP_TIMEOUT_SECONDS=10
SAFEBOX_BITCOIN_SWEEP_FEE_RATE=2.0

SAFEBOX_BIND_ADDRESS=127.0.0.1
SAFEBOX_PORT=8000
FORWARDED_ALLOW_IPS=127.0.0.1
SAFEBOX_WEB_WORKERS=1
SAFEBOX_BACKGROUND_JOB_THREADS=2

SAFEBOX_SERVICE_ACORN_ENABLED=true
SAFEBOX_SERVICE_ACORN_MIGRATE=false
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://relay.getsafebox.app
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_SERVICE_ACORN_STATE_FILE=data/service-acorn.json
SAFEBOX_SERVICE_ACORN_POLL_SECONDS=0.5
SAFEBOX_SERVICE_ACORN_DELIVERY_RETRY_ATTEMPTS=4
SAFEBOX_SERVICE_ACORN_DELIVERY_RETRY_BASE_SECONDS=2
SAFEBOX_SERVICE_ACORN_DELIVERY_RETRY_MAX_SECONDS=60
SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS=604800
SAFEBOX_NIP57_REQUIRE_DESCRIPTION_HASH=false
```

The persisted service-Acorn recovery file overrides later relay and mint
environment changes. Use `SAFEBOX_SERVICE_ACORN_MIGRATE=true` only for a
deliberate, drained-wallet replacement. A failed migration falls back to the
persisted service Acorn unless that existing wallet is itself unavailable. The
guarded startup flow and its operational prerequisites are documented in
[Standalone Service Acorn Worker](SERVICE-ACORN-LIFECYCLE.md#migrating-the-service-acorn).

The delivery retry settings apply only when Acorn proves that a transient
failure occurred before a mint swap was submitted. The defaults make four
total delivery attempts with bounded exponential backoff. An uncertain mint
swap or relay publication remains held for operator review and is never
automatically repeated.

Secure `wss://` relay URLs need no allowlist entry. To deliberately use one or
more non-TLS relays on localhost, a private network, or a protected VPN, list
each exact URL with an explicit port:

```env
SAFEBOX_ALLOWED_WS_RELAYS=ws://localhost:8735,ws://beelink:8735
```

Any `ws://` value used as `SAFEBOX_DEFAULT_BOOTSTRAP_RELAY` or
`SAFEBOX_SERVICE_ACORN_HOME_RELAY` must appear in this list. The connection is
made by the Safebox process, so `localhost` refers to that process's host
namespace; inside Docker it normally refers to the container itself.

`SAFEBOX_BACKGROUND_JOB_THREADS` is the per-Uvicorn-process bound for
session-held Cash finalization and Clear acceptance. The default of two keeps
slow relay or mint work away from the HTTP event loop while limiting concurrent
wallet jobs. With two Uvicorn workers and the default setting, at most four
such jobs can execute concurrently across different Acorns. Database leases
still prevent concurrent mutation of the same Acorn.

If `SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS` is absent, both the
application settings and Compose configuration default it to seven days. Set
it explicitly to `0` to omit the NIP-40 expiration tag. Non-zero values must be
between `3600` seconds (one hour) and `2592000` seconds (30 days).

Use the actual proxy address for `FORWARDED_ALLOW_IPS` when TLS terminates on
another machine. Never commit `.env`.

The Bitcoin settings support the experimental txid-targeted Silent Payment
detection and self-sweep workflow. `SAFEBOX_BITCOIN_API_BASE` must expose the
Blockstream-compatible transaction, address-UTXO, and broadcast endpoints used
by OpenETR. The backend can observe submitted txid lookups, although private
Acorn and NSP key material remains inside the Safebox Web request. The fixed
fee rate is shown to the user before the separately confirmed broadcast. Test
with small controlled amounts before enabling this workflow for users. Receipt
detection and sweep preview have been validated with a controlled mainnet
payment; broadcast and destination settlement should still be treated as a
separate operator validation gate.

Docker may present proxied connections to Uvicorn from the container network
gateway instead of the reverse proxy's original address. See
[Docker Proxy and Forwarded HTTPS Trust](DOCKER-PROXY-FORWARDED-HEADER-TRUST.md)
for the diagnostic procedure, explicit Uvicorn configuration, and the required
compensating network controls.

If encrypted blob upload is enabled, configure the reverse proxy's request-body
limit at or slightly above `SAFEBOX_MAX_BLOB_BYTES` plus multipart overhead. For
the 10 MiB default, an Nginx HTTPS virtual host can use:

```nginx
client_max_body_size 11m;
```

This protects the application before multipart parsing. Safebox Web still
enforces the exact plaintext file limit itself.

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

Fund the service Acorn's mint-fee operating reserve while the singleton worker
is stopped:

```sh
docker compose stop service-acorn-worker
docker compose run --rm service-acorn-worker \
  python -m app.service_acorn_worker fund 100
docker compose up -d service-acorn-worker
```

Do not run the funding command concurrently with the normal worker. The
command uses the configured recovery state file and home mint without exposing
the service private key. See
[Service Acorn Lifecycle](SERVICE-ACORN-LIFECYCLE.md#funding-the-operating-reserve)
for fee behavior, timeout recovery, and reserve ownership.

The field-tested migration, operating-reserve failure, safe requeue, and
end-to-end settlement outcome are documented in
[Service Acorn Migration and Operating Reserve Lessons](SERVICE-ACORN-MIGRATION-AND-OPERATING-RESERVE-LESSONS.md).

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
