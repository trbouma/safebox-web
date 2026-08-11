# NIP-57 Zap Callback Design Note

## Status

Implemented and live-tested on August 11, 2026. A zap initiated by a Nostr
social client successfully obtained an invoice and completed through the
Safebox Web provider flow after this design was deployed.

## Decision summary

Safebox Web uses a dedicated handler for LNURL callbacks that contain a
`nostr` parameter. The parameter identifies a NIP-57 zap request: a signed
kind-9734 event supplied by the payer's client.

The handler validates and durably records the zap request, then creates the
mint quote synchronously within the callback. This intentionally follows the
proven Safebox 2 interaction pattern because social clients expect the callback
to return a BOLT11 invoice promptly.

Only invoice creation moves into the request path. The web process never owns
the service Acorn or its private key. The standalone service worker continues
to check settlement, deliver gift-wrapped ecash to the registered recipient,
sign the kind-9735 receipt, and publish that receipt.

## Why zaps have a separate handler

An ordinary LNURL payment can tolerate a short wait while a background worker
claims a durable job and creates an invoice. In practice, zap clients impose a
tighter and less predictable callback latency budget. The original uniform
queue design inserted a `QUOTE_PENDING` job and polled for the worker's result.
That was architecturally tidy but introduced an avoidable scheduling boundary
between a social client and the invoice it needed immediately.

The dedicated handler preserves the durable architecture while removing that
boundary:

```text
Nostr client
    |
    | LNURL callback + signed kind 9734
    v
Safebox Web zap handler
    | validate request
    | persist payment + zap context as QUOTE_CREATING
    | request mint quote synchronously
    | persist invoice as INVOICE_PENDING
    v
Nostr client receives BOLT11 invoice

Standalone service Acorn worker
    | detect settlement
    | deliver gift-wrapped ecash
    | sign and publish kind 9735 receipt
    v
Recipient Acorn + requested receipt relays
```

## Validation and compatibility boundary

The handler verifies the event signature, kind, required `p` and `relays`
tags, optional tag cardinality, callback amount, provider key when supplied,
and LNURL when supplied. The signed social recipient is preserved in the zap
receipt but is not required to equal the Acorn receiving the funds. The
authenticated handle mapping independently selects the destination Acorn and
home relay.

Relay hints are advisory input, not authority. Safebox Web ignores malformed,
plaintext, localhost, private-address, and duplicate hints. It retains at most
ten public `wss://` targets and rejects the request only when no safe receipt
relay remains. This avoids letting one bad hint invalidate an otherwise usable
request without weakening outbound network policy.

`SAFEBOX_NIP57_REQUIRE_DESCRIPTION_HASH` controls invoice binding:

- `false` is compatibility mode for mints that cannot bind the exact kind-9734
  JSON into the BOLT11 description hash;
- `true` requires the returned invoice to commit to
  `SHA256(zap_request_json)`.

Both the web and worker processes must receive the same setting. Compatibility
mode improves payment interoperability but does not turn an unbound invoice
into a fully verifiable NIP-57 receipt.

## Durable and idempotent behavior

The kind-9734 event id is unique in `provider_zap`. A repeated callback reuses
the already-created invoice instead of requesting another quote. New zap jobs
start in `QUOTE_CREATING`, preventing the worker from racing the callback and
creating a second invoice.

Once the invoice is stored, the established worker states resume:

```text
QUOTE_CREATING -> INVOICE_PENDING -> SETTLED -> DELIVERING
    -> RECEIPT_PENDING -> DELIVERED
                       -> RECEIPT_FAILED
```

Invoice creation errors become `FAILED` and return a generic LNURL error. The
public response does not expose mint, database, or internal exception details.
An ambiguous ecash publication remains `DELIVERY_FAILED` and is not retried
automatically because an automatic retry could pay twice.

## Operational verification

After deployment, a real social-client zap successfully requested and paid an
invoice. Automated coverage verifies:

- discovery advertises `allowsNostr` and the provider public key;
- valid zaps are persisted before an invoice is returned;
- duplicate callbacks reuse one invoice;
- mint failures produce a clean LNURL error and durable `FAILED` state;
- unsuitable relay hints do not poison valid public `wss://` hints;
- settlement, ecash delivery, receipt construction, and receipt failure remain
  distinct worker transitions.

The complete Safebox Web suite passed after the change: 185 tests passed, with
two unrelated dependency deprecation warnings.

## Residual risks and future work

- A process termination during `QUOTE_CREATING` can leave a job requiring
  operator reconciliation; automatic recovery must not accidentally request a
  second invoice.
- Compatibility-mode invoices are not cryptographically bound to the zap
  request and may not satisfy strict clients or independent receipt auditors.
- Successful payment and ecash delivery do not prove that every requested
  relay accepted the kind-9735 receipt.
- Public callback throttling, invoice expiry, abandoned-job cleanup, and
  PostgreSQL-backed concurrent job claiming remain release hardening work.
- Live interoperability should continue to be tested against multiple social
  clients, mints, Lightning backends, and public relays.

See [Lightning Payments to Acorn Handles](LIGHTNING-HANDLE-PAYMENTS.md) for the
complete provider flow and
[Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md)
for worker ownership and deployment constraints.
