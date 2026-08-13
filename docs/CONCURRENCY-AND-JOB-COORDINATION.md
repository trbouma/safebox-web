# Concurrency and Provider-Job Coordination

## Scope

This is a Safebox Web application and deployment concern, not primarily a
Safebox Acorn component concern.

Acorn supplies wallet, mint, proof, relay, encryption, and transfer primitives.
Safebox Web decides how HTTP requests, database transactions, process startup,
provider jobs, retries, and multiple application workers coordinate around
those primitives.

The central ownership rule is:

```text
many web request workers
        |
        v
durable provider-payment queue
        |
        v
exactly one service Acorn owner
```

The web tier may scale independently because it does not load or mutate the
provider wallet. The service Acorn process remains a singleton because Cashu
proof state must not have competing process owners.

## Current concurrency model

The Docker deployment runs one image with two roles:

```text
safebox-web container
    |-- Uvicorn worker 1
    |-- Uvicorn worker 2
    |-- ...
    `-- Uvicorn worker N

service-acorn-worker container
    `-- one provider Acorn process
```

Web workers insert and read `provider_payment` rows. The singleton worker polls
the same database and advances payments through:

```text
QUOTE_PENDING
    -> INVOICE_PENDING
    -> SETTLED
    -> DELIVERING
    -> DELIVERED
    -> RECEIPT_PENDING -> DELIVERED | RECEIPT_FAILED
```

SQLite uses WAL mode, a 30-second busy timeout, and short database sessions.
This permits concurrent reads and makes serialized writes practical for local
development and light pilot traffic.

## What works under light concurrency

- Multiple web workers can resolve handles and insert independent payment jobs.
- Readers can inspect payment state while the singleton worker commits updates.
- Database uniqueness constraints arbitrate concurrent handle claims.
- Provider wallet mutations remain serialized because only one worker owns the
  service Acorn.
- An ambiguous ecash publication stops at `DELIVERY_FAILED`; it is not blindly
  retried and therefore does not intentionally create a duplicate payment.

## Attached-Acorn background finalization

Receiving funds into an attached user Acorn is deliberately separate from the
singleton service Acorn. The recipient's private key comes from the encrypted
browser session and is available only to the web process handling that user.
It must not be copied into the provider worker or written to the application
database.

When the user chooses **Finalize Pending Transactions**, Safebox Web now:

1. atomically claims a database lease keyed by the component public key;
2. starts one in-memory asynchronous task in the claiming web worker;
3. discovers visible relay-backed transfers and finalizes them sequentially;
4. updates non-secret progress and result fields in the database; and
5. lets the HTTP request return immediately so the user can leave the page and
   return later to inspect the result.

Before finalization, the transaction page provides immediate payment
assurance from the relay-visible evidence. It lists each incoming transfer
directly below the confirmed balance with its amount, relay timestamp, and
shortened sender and event references. A transfer already staged in the
continuity journal is labelled as awaiting mint confirmation; a newly
discovered gift-wrapped event is labelled as received on the relay. These
cards are deliberately separate from confirmed transaction history and never
increase the displayed spendable balance. If the same event is visible through
both sources during a transition, its event id deduplicates the presentation.

The database row contains the public key, phase, counts, amounts, timestamps,
status, error summary, and an opaque lease token. It never contains the nsec,
mnemonics, Cashu proofs, transfer tokens, or record-protection material. The
request-scoped Acorn object—and therefore its private key—remains in memory
only until the task completes or the web process stops.

The lease prevents two Uvicorn workers or browser tabs from starting competing
proof mutations for the same Acorn. A heartbeat renews ownership while work is
active. Finalization remains sequential within the job because concurrent
proof swaps against one wallet are unsafe. If the process stops, the task is
marked interrupted when possible; otherwise its lease expires. The user can
reconnect and start finalization again. Relay-backed transfer and continuity
events remain the authoritative recovery queue, so the database status row is
coordination and presentation state rather than wallet state.

This removes the browser request timeout from long relay and mint verification
without weakening canonical publish checks. It does not promise completion in
exactly one minute: network and mint conditions still determine duration. The
intended experience is that the user starts the work, leaves the page, and
returns after roughly a minute to see whether it completed or requires another
check.

This is sufficient for development and carefully bounded small-value pilot
traffic. It is not yet a production concurrency guarantee.

## Known concurrency gaps

### Duplicate LNURL callbacks

The same LNURL callback can currently create more than one database row and
more than one Lightning invoice. Wallet retries, double-clicks, proxy retries,
or payer behavior can therefore create duplicate payable invoices.

The discovery response should eventually include a short-lived opaque callback
token. A uniqueness constraint must bind that token and the requested payment
terms to at most one invoice. Repeating an identical callback should return the
existing invoice rather than enqueue new work.

Validated NIP-57 callbacks now close one part of this gap: the signed kind-9734
event id is unique in `provider_zap`, and a repeated request returns the
existing provider invoice. Ordinary LNURL callbacks still lack an equivalent
idempotency key and remain subject to the limitation above.

### Sequential provider throughput

The service Acorn deliberately processes wallet operations sequentially. A
burst of quote requests can wait behind earlier mint calls. The web callback
currently waits a bounded period for `INVOICE_PENDING`; a sufficiently large
queue can exceed that timeout even when the system is healthy.

The design should measure queue depth and quote latency, apply admission
control, and return a deliberate busy response before work cannot complete
inside the LNURL client's expected window.

### SQLite write serialization

WAL improves reader/writer coexistence but SQLite still has one writer at a
time. More web workers do not create more database write capacity. Frequent
callback polling also creates avoidable read pressure.

PostgreSQL is the intended production database. It enables row locking,
transactional job claiming, better observability, and more predictable
concurrency under sustained traffic.

### Migration startup races

Each Uvicorn worker currently enters the FastAPI lifespan and can invoke
Alembic. On a new database, several processes may attempt the same migration at
once. An existing database at the current revision is less likely to expose the
race, but migration ownership should still be singular.

Production startup should run migrations as a separate one-shot command before
starting any web or provider workers:

```text
migrate -> start web workers -> start service Acorn worker
```

Application processes should verify the expected schema revision rather than
independently upgrade it.

### Atomic job claiming

The current queue assumes exactly one provider worker and therefore selects the
next eligible row without a cross-process claim lease. Accidentally starting a
second worker could let both observe the same job.

The production queue should atomically claim work with a worker identifier,
lease expiry, attempt number, and compare-and-set state transition. PostgreSQL
can use `SELECT ... FOR UPDATE SKIP LOCKED`. The singleton rule should remain
even after claims are added because job coordination alone does not make proof
ownership multi-writer safe.

### Other attached-Acorn mutations

Request-scoped user Acorns are separate from the provider Acorn. Two browser
requests can nevertheless attempt to mutate the same attached wallet at the
same time, particularly with multiple web workers or multiple browser tabs.
Relay-backed advisory locks help, but the application should not treat them as
a complete local scheduling mechanism.

Incoming-funds finalization now has a wallet-scoped database lease. Deposits,
payments, record updates, and other proof maintenance do not yet participate
in that lease, so they can still overlap with finalization from another tab or
worker. The shared policy should eventually cover every attached-Acorn
mutation.

### Ambiguous delivery outcomes

A crash after ecash issuance or relay publication but before the database
commit creates an ambiguous state. Automatically repeating the entire delivery
could issue a second transfer. Leaving it permanently stopped can strand a
legitimate payment.

The delivery protocol needs an idempotency identifier, recipient
acknowledgement, reconciliation query, and operator decision path. Until then,
`DELIVERING` and `DELIVERY_FAILED` require manual review.

## Target production model

```text
TLS proxy
    |
    v
multiple stateless web workers
    |
    v
PostgreSQL
    |-- registrations
    |-- idempotent invoice requests
    |-- payment and settlement states
    |-- claimed jobs and leases
    `-- delivery outbox and acknowledgements
    |
    v
one service Acorn worker
    |-- provider key
    |-- provider proofs
    |-- mint interaction
    `-- gift-wrapped delivery
```

Database concurrency protects application workflow. Singleton ownership
protects wallet state. Neither substitutes for the other.

## Hardening sequence

1. Move Alembic execution into a one-shot deployment step.
2. Add short-lived LNURL callback tokens and invoice idempotency constraints.
3. Add queue depth, state age, and transition latency metrics.
4. Replace callback polling with database notification or a bounded internal
   wait mechanism.
5. Move production state from SQLite to PostgreSQL.
6. Add atomic job claims, leases, and stale-claim recovery.
7. Extend the existing incoming-funds lease into wallet-scoped serialization
   for every attached-Acorn mutation.
8. Add delivery acknowledgement, reconciliation, and operator review tooling.
9. Exercise concurrency and crash points with deterministic integration tests.

## Operating constraints until hardened

- Run exactly one `service-acorn-worker` container.
- Use small payment amounts and bounded pilot traffic.
- Treat increasing `SAFEBOX_WEB_WORKERS` as request capacity, not database or
  provider-wallet capacity.
- Monitor `QUOTE_PENDING`, `INVOICE_PENDING`, `DELIVERING`, and
  `DELIVERY_FAILED` row ages.
- Do not automatically replay an ambiguous delivery.
- Do not start another wallet mutation while attached-Acorn background
  finalization is running.
- Stop accepting new provider payments before maintenance that may interrupt
  settlement or delivery.
