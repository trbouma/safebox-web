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
SAFEBOX_SERVICE_ACORN_MIGRATE=false
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://relay.getsafebox.app
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_SERVICE_ACORN_STATE_FILE=data/service-acorn.json
SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS=604800
SAFEBOX_NIP57_REQUIRE_DESCRIPTION_HASH=false
```

The NIP-57 setting defaults to compatibility mode so mints without support for
exact zap-request description hashes can still create invoices. Set it to
`true` only when the configured mint backend supports description-hash-bound
invoices. Compatibility receipts are unbound and may be rejected by strict
NIP-57 clients.

Run it directly during development:

```sh
poetry run python -m app.service_acorn_worker run
```

Or start the normal two-service Compose deployment:

```sh
docker compose up -d
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

The worker adds a NIP-40 `expiration` tag to each gift-wrapped ecash delivery.
Configuration behavior is explicit:

| Environment value | Behavior |
| --- | --- |
| Variable absent | Use the seven-day default (`604800` seconds). |
| `3600` through `2592000` | Expire from one hour through 30 days after publication. |
| `0`, blank, `none`, or `off` | Do not add an expiration tag. |

The expiration is signed into the transient kind `1059` outer event. A
supporting relay should stop serving the event after expiry and should delete
it, but enforcement and physical erasure remain relay policy.
NIP-40 expiration is therefore retention guidance, not a security guarantee.
The retention clock starts when the transfer is published, not when the
recipient accepts it. An expired, unclaimed gift wrap may make the ecash
delivery unavailable, so operators must choose a period appropriate to the
recipient workflow and retain the durable provider-payment record for review.

The current SQLite Compose deployment mounts the same `/app/data` volume into
both containers. The web process does not load the recovery file, but this is
not strict filesystem isolation. Moving the file to a worker-only volume is a
production hardening item and requires a deliberate stopped-worker migration.

## Migrating the service Acorn

Changing the relay or mint environment variables does not normally alter an
existing service Acorn. The persisted recovery file remains authoritative so
an ordinary restart cannot silently disconnect the provider wallet.

For an intentional replacement, first stop new provider-payment intake and
confirm that the existing service Acorn has no balance, unsettled invoices, or
unclaimed delivery obligations. Then configure the new endpoints and enable
the guarded migration switch:

```env
SAFEBOX_SERVICE_ACORN_MIGRATE=true
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://spurline.safebox.dev
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.safebox.dev
```

On startup the singleton worker compares those normalized endpoints with the
persisted state. If they already match, migration is a no-op. If they differ,
the worker:

1. loads the existing service Acorn and abandons migration if it holds funds;
2. creates and verifies a new Acorn against the configured relay and mint;
3. burns the old wallet's relay state; and
4. atomically replaces the local recovery file with the new key and endpoints.

If a precondition, replacement creation, readback, burning, or local state
update fails, migration is abandoned and the worker continues with the
persisted service Acorn. The reason is logged prominently. Startup can still
fail if the persisted Acorn itself cannot be loaded; there is no healthy
fallback in that case. The switch does not sweep funds and does not migrate
outstanding provider jobs. A replacement also changes the service Acorn public
key, so verify the provider identity and payment behavior after startup. Once
verified, set `SAFEBOX_SERVICE_ACORN_MIGRATE=false` and restart; the endpoint
comparison prevents repeated replacement, but disabling the flag records the
operator's completed intent.

## Secret ownership and isolation

The service Acorn `nsec` is an operator-owned production secret. It is distinct
from both an attached user's `nsec` and the `SAFEBOX_COOKIE_KEY`. Its stable
public key identifies the provider component, while its private key authorizes
provider-wallet events, controls any funds held by that wallet, delivers ecash,
and signs provider receipts such as NIP-57 zap receipts. Persistence is required
so restarts do not abandon proofs, outstanding invoices, delivery obligations,
or the provider's public continuity.

Compromise of this key can permit theft of service-wallet funds, provider
impersonation, false receipts, unauthorized transfers, and mutation or deletion
requests for provider events. The key must therefore never be placed in a
browser session cookie, exposed by an HTTP route, written to logs, committed to
the repository, or baked into a container image.

The default recovery file, `data/service-acorn.json`, contains the `nsec` in
plaintext and is created with owner-only permissions. File mode `0600` is a
minimum safeguard, not a complete custody design. Protect the host, volume,
backups, and runtime memory; keep exactly one worker process as the wallet owner;
and rotate the key only through deliberate retirement and reconciliation.

The preferred production boundary is:

```text
web process     -> SAFEBOX_COOKIE_KEY + required shared database access
service worker  -> service Acorn nsec + private worker state + shared jobs
```

The proposed Bitcoin treasury boundary follows the same separation. The
service Acorn may hold an encrypted, public receive descriptor as configuration,
while Bitcoin Core allocates watch-only addresses and an offline Sparrow wallet
or hardware signer retains exclusive spending authority. See the
[Service Acorn Treasury Descriptor Design Note](SERVICE-ACORN-TREASURY-DESCRIPTOR-DESIGN-NOTE.md).

The current shared `/app/data` mount provides logical process separation only.
It does not prevent the web container from reading the service recovery file.
Enforceable isolation requires a worker-only volume or a secret service with
separate workload identities and policies. If the `nsec` is moved to OpenBao or
another secret manager, keep non-secret worker metadata and durable job state
separate from the secret value. See the
[OpenBao Integration Note](OPENBAO-INTEGRATION-NOTE.md) for the target custody
model.

## Routine shutdown and restart

`Ctrl-C`, `docker compose stop`, and routine deployments stop the worker but do
**not** burn its Acorn. The recovery file remains, and the next singleton worker
restores the same key, relay, mint, proofs, and unfinished operational context.

This persistence is necessary because a Lightning invoice may settle during or
after a restart. Burning on every process stop could destroy the wallet while a
provider obligation is still outstanding.

The persisted service `nsec` is independent of gift-wrap expiration. Each
NIP-59 outer event still uses a one-use transient signing key for privacy. The
service key preserves the provider wallet and its operational continuity; the
NIP-40 tag gives the relay a signed expiry instruction without requiring the
worker to retain every transient private key.

Never run two service Acorn worker processes against the same recovery file.
The Acorn proof state has one process owner. Multiple web workers are allowed
because they do not load or mutate this provider wallet.

The service key rotates only through an explicit retirement. Stop the worker,
run the retirement command below, verify that any remaining balance has been
swept and the recovery file removed, and then start the worker again. The next
start generates and persists a new `nsec`. Ordinary restarts and deployments
must never be treated as key rotation.

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
docker compose run --rm service-acorn-worker \
  python -m app.service_acorn_worker retire
```

The retirement command restores the existing wallet, reloads its relay state,
sweeps any balance, publishes advisory deletion requests, and removes the local
recovery file only after a successful burn. It refuses to create and retire a
new wallet when no recovery file exists. If sweeping or burning fails, the
recovery file remains for reconciliation and retry.

## Provider-payment limitation

The worker now consumes the first durable LNURL provider-payment jobs: invoice
creation, settlement checking, gift-wrapped ecash delivery, and NIP-57 receipt
publication. At startup it writes only its public hex key to the
`provider_identity` table so web workers can advertise `nostrPubkey`; the
private key remains within the singleton worker's service-Acorn state.

Zap requests and receipt state are persisted separately in `provider_zap`.
This is still not a production Lightning gateway. Remaining work includes:

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
