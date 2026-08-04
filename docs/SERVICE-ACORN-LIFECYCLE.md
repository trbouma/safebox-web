# Standalone Service Acorn Worker

Safebox Web includes a standalone, single-owner worker for the provider Acorn.
Its intended future role is to receive settled Lightning funds on behalf of
other Acorns, convert those funds into ecash, and deliver gift-wrapped transfers
to the recipients' public keys and relays.

This process is distinct from both the web tier and user-attached Acorns:

```text
browser -> one or more stateless web workers -> durable provider jobs
                                                    |
                                                    v
                                         one service Acorn worker

attached Acorn -> reconstructed per web request from the encrypted session
service Acorn  -> persistent provider wallet owned by exactly one process
```

The standalone process exposes its Acorn internally as the module-level
`app.service_acorn_worker.service_acorn`. Its lock-bearing runtime is available
as `service_acorn_runtime`. Those objects are global only inside the singleton
worker process; web workers cannot access them directly. The LNURL routes submit
durable `provider_payment` jobs rather than importing the worker's globals.

## Starting the worker

Set the service variables in `.env`, including:

```env
SAFEBOX_SERVICE_ACORN_ENABLED=true
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://relay.getsafebox.app
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_SERVICE_ACORN_STATE_FILE=data/service-acorn.json
```

Run it directly during development:

```sh
poetry run python -m app.service_acorn_worker run
```

Or start the opt-in Compose profile:

```sh
docker compose --profile service-acorn up -d
docker compose logs -f service-acorn-worker
```

Both Compose services use the same image with different commands. See the
[Deployment Runbook](DEPLOYMENT.md) for the complete build, startup, logging,
restart, volume, and retirement procedure.

On its first start the worker:

1. generates a fresh seed phrase and `nsec` in memory;
2. atomically writes minimum recovery state to an owner-only file;
3. creates wallet metadata on the configured home relay;
4. verifies relay readback with `Acorn.load_data()`; and
5. makes the Acorn available to worker code.

The recovery file defaults to `data/service-acorn.json` in the persistent
Docker volume. It contains the service `nsec` in plaintext with filesystem mode
`0600`. Treat it as a production secret: never commit, log, expose, or casually
copy it. It is written before relay initialization so an interrupted first
start does not abandon the key.

The current SQLite Compose deployment mounts the same `/app/data` volume into
both containers. The web process does not load the recovery file, but this is
not strict filesystem isolation. Moving the file to a worker-only volume is a
production hardening item and requires a deliberate stopped-worker migration.

## Routine shutdown and restart

`Ctrl-C`, `docker compose stop`, and routine deployments stop the worker but do
**not** burn its Acorn. The recovery file remains, and the next singleton worker
restores the same key, relay, mint, proofs, and unfinished operational context.

This persistence is necessary because a Lightning invoice may settle during or
after a restart. Burning on every process stop could destroy the wallet while a
provider obligation is still outstanding.

Never run two service Acorn worker processes against the same recovery file.
The Acorn proof state has one process owner. Multiple web workers are allowed
because they do not load or mutate this provider wallet.

## Explicit retirement

Retirement is deliberately separate from routine shutdown. First stop the
singleton worker, configure a recovery recipient, and then run:

```env
SAFEBOX_SERVICE_ACORN_SHUTDOWN_RECIPIENT=<npub-or-nip05>
SAFEBOX_SERVICE_ACORN_SHUTDOWN_RELAY=wss://relay.getsafebox.app
```

```sh
poetry run python -m app.service_acorn_worker retire
```

For Docker Compose:

```sh
docker compose --profile service-acorn run --rm service-acorn-worker \
  python -m app.service_acorn_worker retire
```

The retirement command restores the existing wallet, reloads its relay state,
sweeps any balance, publishes advisory deletion requests, and removes the local
recovery file only after a successful burn. It refuses to create and retire a
new wallet when no recovery file exists. If sweeping or burning fails, the
recovery file remains for reconciliation and retry.

## Provider-payment limitation

The worker now consumes the first durable LNURL provider-payment jobs: invoice
creation, settlement checking, and gift-wrapped ecash delivery. This is still
not a production Lightning gateway. Remaining work includes:

- invoice expiry and request throttling;
- complete crash and settlement reconciliation;
- idempotent delivery acknowledgement and retry;
- refund and operator review tooling;
- production monitoring and alerting; and
- worker-only filesystem isolation for provider recovery material.

Do not accept meaningful third-party payments until that state machine and its
outbox are implemented and tested. Use only small test amounts during worker
development.

See [Lightning Payments to Acorn Handles](LIGHTNING-HANDLE-PAYMENTS.md) for the
implemented routes, durable states, testing flow, and release gates.
