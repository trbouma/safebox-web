# Asynchronous receipt: identity, interaction paths, and ownership

## The capability

Safebox Web provides an asynchronous receipt path to an Acorn identified by its
npub. A Lightning address is a service endpoint associated with that identity.
The gateway receives payments and delivers encrypted transfers for the Acorn
to retrieve and finalize later, without access to the recipient's wallet keys.

This gives an offline recipient an online receiving presence. It does not
require an unattended recipient session or a server-side copy of their wallet.
The service Acorn is the provider's own wallet, with its own key and obligations.

This note describes Lightning-address provider payments. An invoice created
directly for a connected recipient Acorn has a different quote owner and
recovery path; it must not be silently reassigned to the service Acorn.

## Identity remains distinct from the path

- **npub:** the stable public-key identifier of the receiving Acorn. It is not,
  by itself, a verified human identity. Replacing its key changes that identifier.
- **Lightning address:** a provider-controlled interaction path, such as
  `alice@example.org`, whose registration points to the recipient npub.
- **Recipient relay:** the delivery location recorded for that recipient.
- **Mint and quote:** the issuer and specific settlement obligation for a payment.

A person can change address or provider without changing their Acorn key.
Moving relays requires updating routing and preserving access to older transfers;
it does not happen automatically just because the npub remains unchanged.
Each queued payment preserves the recipient npub, relay, mint and quote captured
for that payment. Later configuration changes must not reinterpret that obligation.
A Lightning address can be reassigned by its provider and is not an immutable
substitute for the npub.

## The stages must remain distinguishable

| Stage | Evidence and meaning | Responsibility |
| --- | --- | --- |
| Invoice available | A durable mint quote exists; no receipt of funds is established yet | Web intake and provider worker |
| Lightning received | The mint reports PAID; proofs may still be unissued | Provider settlement discovery |
| Ecash issued | The service Acorn has completed the mint operation and preserved its proof state | Acorn kernel, invoked by the worker |
| Transfer delivered | The encrypted transfer has been published and delivery recorded | Acorn transfer operation and provider job |
| Recipient pending | The receiving Acorn discovers or stages the transfer | Recipient Acorn |
| Recipient finalized | Recipient acceptance completes and updates proofs and history | Recipient Acorn, under recipient authority |

`INVOICE_PENDING` and `SETTLEMENT_UNCONFIRMED` do not prove payment.
`PAID_RECONCILIATION_PENDING` means paid discovery has been recorded but issuance
remains pending. `SETTLED` queues delivery; `DELIVERING` is an in-progress
operation; `DELIVERED` is provider delivery completion, not recipient finalization.
An ambiguous delivery failure requires review rather than blindly issuing a
second transfer.

The incoming-transfer view can show provider-side paid jobs before relay delivery.
That is operational evidence of an incoming obligation, not yet a spendable
recipient balance. Relay arrivals and confirmed balance are also separate views.
Fees can make the final credited amount lower than the invoice amount.

## Safebox Web and safebox-acorn retain separate responsibilities

| Safebox Web owns | safebox-acorn owns |
| --- | --- |
| HTTP/LNURL intake and local handle registration | Wallet identity and key operations |
| Durable provider jobs and recipient routing captured for each job | Mint protocol operations, proof validation, and fee-aware swaps |
| Scheduling, polling backoff, mint cooldowns, and worker liveness | Wallet locks, proof persistence, and cryptographic recovery state |
| Operational diagnostics and presentation of pending/finalized stages | Encryption, signing, relay publication, and transfer acceptance |
| Invoking the appropriate service or recipient Acorn under its owner's authority | Wallet balance and transaction-history mutations |

The worker uses the kernel; it does not implement a second proof engine.
Concurrent `get_quote_state()` calls are read-only discovery. `check_quote()`
can issue proofs and remains within serialized service-wallet work. A healthy
heartbeat is evidence of worker liveness, not proof that either operation succeeds.

Application jobs are operational coordination state. They do not replace the
relay-backed wallet or store recipient recovery words, private records, or bearer
proofs. Kernel recovery remains kernel responsibility. The recent HTTP polling
hardening belongs entirely in Safebox Web: it governs when operations are called
and how their outcomes are reported, without moving wallet rules into the app.

## Clear: direct asynchronous receipt

Clear uses the same identity and offline-delivery principle through a different
path. A sender delivers encrypted Clear proofs to the recipient's relay; a
NUT-18 request can specify the npub transport, accepted mint, exact unit, and
requested amount. The recipient need not be connected during delivery.

There is no Lightning invoice to settle and no provider issuance queue in this
path. The recipient Acorn discovers and stages the transfer, then validates and
refreshes its proofs with the issuing mint during acceptance. Receipt of the
encrypted message alone does not establish a confirmed Clear balance.

Safebox Web runs acceptance in a session-authorized background job. Its lease,
phase, and safe error describe coordination; the kernel's relay-backed receipt,
proofs, recovery intent, and history remain authoritative. Retrying calls that
same kernel acceptance operation. The app neither reconstructs proofs nor
resubmits a swap itself. Friendly names are display metadata; balance identity
remains mint URL, unit, and keyset ID.

Unlike read-only Lightning quote polling, Clear acceptance may already have
mutated mint state when an HTTP error or timeout occurs. It therefore goes to
review without automatic mutation retries. Diagnostics expose status, host,
phase, and a restricted error summary without copying tokens or response bodies.
Only the precise missing-receipt result triggers relay discovery for that event,
without advancing the cursor; other “not found” errors do not trigger a second
acceptance call.

Regression coverage exercises outages, cancellation, stale job ownership,
duplicate acceptance, and a failure after proofs and history are saved but
before the receipt is marked accepted. The latter resumes from kernel state
without another swap or history entry. These are simulated failure tests, not a
guarantee against all mint or relay data loss; live multi-mint testing remains
necessary. See [Clear acceptance reliability](CLEAR-ACCEPTANCE-RELIABILITY.md).

## Authority and trust

The provider worker can receive and deliver using its own service key and the
recipient's public key. It cannot decrypt or finalize the recipient's wallet
without recipient authority. Finalization can occur through a connected web
session or another authorized Acorn client, including the CLI.

This is not a claim that the entire hosted application never handles recipient
secrets: during an authorized browser session, the web process decrypts the
session material needed to operate the recipient Acorn. That separate session
trust boundary remains. The asynchronous provider path does not need it.

The gateway must honor its delivery obligations, the mint must honor issuance
and redemption, and relays must make transfers available. The appropriate
description is **asynchronous receipt without giving the receiving worker access
to the recipient's wallet keys**, rather than an unqualified “trustless payment.”
Provider registration integrity and operator availability remain material.

## Reliability and limits

Every interruption must preserve enough evidence to distinguish “not started”
from “outcome unknown.” Persist quote ownership, record paid discovery, keep
issuance serialized, and preserve review states when mutation outcomes are
ambiguous. Do not confuse a failed status request with an unpaid invoice or
interpret a delivery event as proof of recipient acceptance.

Recipients can be offline, but relay retention is finite. The configured
gift-wrap expiration can make an unclaimed transfer unavailable; the lifecycle
note documents the default seven-day retention and its configuration. An npub
does not guarantee storage availability, automatic rerouting, or indefinite
recovery. Queued jobs, published transfers, and wallet recovery material have
different retention and backup requirements.

The field failures made these boundaries concrete: stale routing, missing
dependency methods, queue starvation, HTTP errors, mint fees, and interrupted
relay writes can all present as “payment missing.” Diagnosis must identify the
stage and its evidence before changing state. Increasing a timeout or resetting
the queue is not a substitute for that distinction.

## Related implementation notes

- [Lightning payments to Acorn handles](LIGHTNING-HANDLE-PAYMENTS.md)
- [Service Acorn lifecycle and retention](SERVICE-ACORN-LIFECYCLE.md)
- [Worker ownership and coordination milestone](SERVICE-ACORN-WORKER-HARDENING-MILESTONE-2026-09-14.md)
- [HTTP discovery reliability milestone](PROVIDER-SETTLEMENT-HTTP-RELIABILITY.md)
