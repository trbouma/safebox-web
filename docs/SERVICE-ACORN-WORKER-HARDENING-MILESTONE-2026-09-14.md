# Service Acorn Worker Hardening Milestone — 2026-09-14

Follow-up: [HTTP settlement polling reliability](PROVIDER-SETTLEMENT-HTTP-RELIABILITY.md)
documents error visibility, mint cooldowns, and progressive unpaid polling.

## Why this work was necessary

Field testing exposed a class of failures that looked unrelated: settled
Lightning invoices remained pending, a worker container appeared to be running
without announcing readiness, delivery could stop after a mint or relay delay,
and restarting components sometimes changed which symptom was visible. Relay,
mint, reverse-proxy, and library defects were real contributors, but they also
revealed an application-level weakness: service-wallet ownership and payment
phase ownership were conventions rather than enforced invariants.

For a provider wallet, that is not strong enough. A duplicate invoice is
confusing; a duplicate bearer-token delivery is a financial error. Recovery
must distinguish operations known not to have started from operations whose
outcome is unknown.

## Invariants established

### One service wallet, one process owner

The service Acorn worker now holds a non-blocking file lock in its shared data
volume for its full lifetime. The lock name does not depend on the recovery
filename, so a configuration mismatch cannot create two owners of the same
provider queue. Maintenance commands use the same lock, preventing an operator
command from racing live proof mutation.

This is strong fencing for containers sharing one host volume. It is not a
multi-host distributed lock; moving the worker between machines requires a
deliberate stop-and-transfer procedure until distributed fencing exists.

### One owner for each mutation phase

Queue mutations now use compare-and-set transitions. The worker first changes
the exact expected state into an in-progress state, then performs the external
operation, and finally commits the result only if it still owns that state.

```text
QUOTE_PENDING -> WORKER_QUOTE_CREATING -> INVOICE_PENDING
SETTLED       -> DELIVERING            -> DELIVERED | DELIVERY_FAILED
RECEIPT_PENDING -> RECEIPT_PUBLISHING  -> DELIVERED | RECEIPT_FAILED
```

Settlement polling is read-only at the mint, but its database result is also
guarded so an older response cannot overwrite newer payment state.

Zap callback invoice creation retains its separate `QUOTE_CREATING` state. This
matters because it is owned synchronously by a web request, not by the service
worker; startup recovery must not quarantine a legitimate in-flight zap.

### Unknown outcomes are not automatic retries

On process startup, an in-progress worker-owned state proves that the previous
owner stopped before recording a final result. These rows are quarantined:

- interrupted quote creation becomes `FAILED` for mint review;
- interrupted ecash delivery becomes `DELIVERY_FAILED` for recipient and wallet
  review; and
- interrupted zap receipt publication becomes `RECEIPT_FAILED` without
  reissuing ecash.

Only an error explicitly classified as occurring before mint swap submission is
eligible for bounded automatic delivery retry. An unknown post-swap or relay
publication outcome is never replayed blindly.

### Worker progress is observable

The worker writes a heartbeat from its asyncio event loop. Compose reports the
container unhealthy when readiness never completes, the loop wedges, or the
heartbeat becomes stale. This closes the observability gap where a process was
running but not processing provider payments.

Health means the worker loop is progressing. Mint reachability, relay
reachability, queue depth, and aged review states remain separate operational
signals.

## Verification added

Tests now prove that:

- a payment phase can be claimed only once;
- a stale expected state cannot overwrite the current row;
- quote ownership is recorded before contacting the mint;
- abandoned financial operations are quarantined;
- a second service-wallet process owner is rejected;
- a fresh heartbeat is accepted and a missing or stale heartbeat fails; and
- the existing invoice, settlement, delivery, retry, and zap paths still pass.

## Remaining architectural boundary

The worker still calls an Acorn operation that both exports Cashu value and
publishes the recipient event. Acorn persists changed proof state before
Safebox Web can durably store the exact signed outgoing event. Therefore a
narrow crash window remains between proof commitment and replayable outbox
storage.

Closing that window is an Acorn-level protocol change, not just another worker
retry. The future design must durably retain the exact encrypted, signed event
and its relay set as part of the transfer saga, then allow publication of that
same event id until acknowledgement. It must not mint a fresh token or construct
a different gift wrap on retry.

Until that exists, `DELIVERY_FAILED` is a manual reconciliation boundary. This
is intentional and safer than risking duplicate value.

## Operational release gate

Before deploying this milestone:

1. build the image and run the complete test suite;
2. confirm only one service-worker container is configured;
3. deploy with the shared state volume intact;
4. confirm `service Acorn ready` in logs and healthy status in Compose;
5. inspect any quarantined review rows before manually changing them; and
6. test one small Lightning-address payment and one zap through settlement,
   ecash delivery, recipient finalization, and transaction history.

See also:

- [Standalone Service Acorn Worker](SERVICE-ACORN-LIFECYCLE.md)
- [Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md)
- [Lightning Handle Payments](LIGHTNING-HANDLE-PAYMENTS.md)
