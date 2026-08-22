# Funds Arrival and Finalization Milestone — 2026-08-13

## Summary

Safebox Web now separates three facts that were previously too easy to
collapse into one user-visible result:

1. a transfer event has arrived on the recipient's relay;
2. its bearer proofs are being finalized with the issuing mint; and
3. the resulting proofs are mint-confirmed and spendable.

This lets the application reassure the user quickly without overstating their
balance. It also removes a long relay-and-mint workflow from the lifetime of
one browser request.

The work was completed alongside a critical Acorn fund-safety correction. See
the [Acorn Fund-Safety Hardening and Interoperability Milestone](https://github.com/trbouma/safebox-acorn/blob/main/docs/FUND-SAFETY-HARDENING-MILESTONE-2026-08-13.md)
for the NUT-00 proof-compatibility incident and kernel-level controls.

## Immediate assurance without false finality

When the user selects **Check Balance and Incoming Transfers**, Safebox Web
asks Acorn for two read-only views:

- newly visible gift-wrapped transfer events that have not yet been staged;
  and
- provisional continuity receipts that have been staged but not yet accepted
  by their mint.

The refreshed balance pane reports the aggregate pending amount and event
count. Directly below it, individual pending transaction cards show:

- amount;
- relay arrival time;
- shortened sender and event references;
- an available payment comment; and
- either `Received on relay; finalization pending` or
  `Awaiting mint confirmation`.

Event IDs deduplicate an arrival that is briefly visible in both views during
a state transition. Pending cards never increase the displayed spendable
balance and remain separate from confirmed transaction history.

This is the intended assurance statement:

```text
Safebox has evidence that funds were sent to this Acorn.
The funds are not yet represented as spendable until mint finalization succeeds.
```

## Background finalization

Selecting **Finalize Pending Transactions** now returns the browser response
immediately and starts an in-memory asynchronous task in the web worker. The
user can leave the page and return after roughly a minute to see whether work
is running, complete, partial, interrupted, or failed.

The task:

1. stores newly visible transfers as provisional relay-backed receipts;
2. processes all provisional receipts sequentially;
3. asks each issuing mint to accept and refresh the bearer proofs;
4. waits for verified proof and transaction-history persistence; and
5. records only non-secret progress for later presentation.

Sequential processing is deliberate. Parallel proof mutation against the same
Acorn would create avoidable races and stale snapshots.

## Key and state boundary

The recipient nsec originates in the encrypted browser session. It exists in
clear form inside the trusted web process while Acorn performs the requested
work, but it is never stored in the finalization table or sent to the singleton
service Acorn worker.

The database stores only:

- component public key;
- job status and phase;
- pending and confirmed counts and amounts;
- timestamps and bounded error text; and
- an opaque ownership token and lease expiry; and
- an opaque worker-lifetime identifier with process heartbeat timestamps.

The cross-worker lease prevents another browser tab or Uvicorn worker from
starting a second incoming-funds finalizer for the same public key. A job
heartbeat keeps the lease current, while a separate process heartbeat
distinguishes a dead worker from a merely busy event loop. Graceful shutdown
interrupts the job immediately. After forced termination, the old process is
considered stopped after about one minute and a connected replacement worker
can atomically reclaim its job without waiting for the 15-minute fallback
lease. Relay-backed transfer events and provisional receipts remain the
authoritative recovery queue.

The application database is therefore coordination state, not wallet state.
It contains neither the nsec, Cashu proofs, transfer tokens, recovery phrases,
nor record-protection material.

## Relationship to the service Acorn

Incoming Lightning payments to a registered handle follow the provider path:

```text
external Lightning payer
  -> Safebox Web invoice
  -> singleton service Acorn settles the mint quote
  -> gift-wrapped transfer to the registered recipient relay
  -> recipient sees pending arrival
  -> recipient Acorn finalizes with the mint
```

The singleton service Acorn is the application treasurer and delivery agent.
It does not possess the recipient's key and cannot finalize the recipient's
wallet. Recipient finalization therefore belongs to the authenticated web
session and its temporary in-memory Acorn instance.

## Live interoperability evidence

On August 13, 2026, after the associated Acorn proof-safety corrections, a
connected Safebox Web wallet successfully made an outgoing Lightning payment
to an independently operated Swiss Bitcoin Pay application.

This confirms that the user-facing payment path can move fresh compatible
Acorn funds through a mint and the Lightning Network to external
infrastructure. It complements earlier incoming handle-payment and zap tests.
It does not certify the external provider, eliminate mint trust, or prove every
ambiguous timeout and crash-recovery path.

## Remaining engineering work

- Extend wallet-scoped serialization beyond incoming finalization to deposits,
  outgoing payments, record writes, and proof maintenance.
- Add deterministic process-stop and lease-expiry tests around live task phases.
- Move production coordination from SQLite to PostgreSQL before sustained
  multi-worker traffic.
- Add idempotent outgoing delivery acknowledgement and operator reconciliation.
- Measure relay discovery, mint acceptance, proof persistence, and history
  persistence independently.
- Continue live tests with small amounts across independent wallets, relays,
  mints, and Lightning recipients.

## Related documents

- [Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md)
- [Lightning Payments to Acorn Handles](LIGHTNING-HANDLE-PAYMENTS.md)
- [Deployment Runbook](DEPLOYMENT.md)
- [NIP-57 Zap Integration: Lessons Learned](NIP57-ZAP-LESSONS-LEARNED.md)
