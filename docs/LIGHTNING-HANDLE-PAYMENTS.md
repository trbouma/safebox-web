# Lightning Payments to Acorn Handles

Safebox Web now has an initial LNURL-pay path that lets a conventional
Lightning wallet request an invoice for a claimed Safebox handle. The
standalone service Acorn accepts the mint deposit and then delivers the value
to the registered Acorn as a gift-wrapped ecash transfer.

This complete path has been deployed and verified with a real Lightning
payment. That validates the process boundary and interoperability path; it does
not remove the production release gates documented below.

![Illustrated flow from an external Lightning wallet through Safebox Web, a durable payment job, the singleton service Acorn and mint, a gift-wrapped relay delivery, and finally into the recipient Acorn balance.](assets/lightning-to-acorn-payment-flow.png)

```text
Lightning wallet
      |
      | GET /.well-known/lnurlp/alice
      | GET /lnpay/alice?amount=21000
      v
Safebox web tier
      |
      | durable provider_payment row
      v
singleton service Acorn worker
      |
      | mint invoice -> settlement -> gift-wrapped ecash
      v
alice's npub at alice's registered home relay
```

The implementation follows the basic two-request
[LNURL-pay flow](https://github.com/lnurl/luds/blob/luds/06.md): discovery
returns `callback`, `minSendable`, `maxSendable`, serialized `metadata`, and
`tag=payRequest`; the callback accepts `amount` in millisatoshis and returns a
BOLT11 invoice as `pr`. Comments are supported up to the advertised limit.

## Connected-wallet QR code

When this provider path is enabled, the connected-wallet page presents a QR
code for an Acorn that has claimed a handle. Safebox constructs the public
HTTPS discovery endpoint:

```text
https://example.com/.well-known/lnurlp/alice
```

It converts the UTF-8 URL bytes from 8-bit groups to 5-bit groups and encodes
them with Bech32 using the `lnurl` human-readable prefix. The uppercase
`LNURL1...` result is the QR payload. This follows the established Safebox 2
behavior and lets an ordinary Lightning wallet scan the address without
knowing anything about Acorn or the provider's internal ecash delivery.

The QR is displayed only when the connected Acorn has a claimed handle and
`SAFEBOX_SERVICE_ACORN_ENABLED=true`. Its URL is derived from the externally
visible request host and scheme, so production depends on the trusted reverse
proxy supplying the correct HTTPS forwarded headers.

## Recipient registration

The Lightning address uses the same authenticated handle mapping as NIP-05:

```text
claimed_handle -> component npub + home relay
```

The domain and application operator remain authoritative for that mapping. The
callback copies the current mapping into the payment row so a subsequent handle
change cannot redirect an already-issued invoice.

## Durable states

The web process never calls the service Acorn directly. It inserts a
`provider_payment` row in `QUOTE_PENDING` and waits briefly for the worker to
store the invoice. The worker advances the row through:

```text
QUOTE_PENDING
    -> INVOICE_PENDING
    -> SETTLED
    -> DELIVERING
    -> DELIVERED
```

Invoice creation failures become `FAILED`. A delivery exception becomes
`DELIVERY_FAILED` and is not retried automatically. This is intentional: after
an ambiguous relay publication, blindly issuing another ecash transfer could
pay the recipient twice. Operator reconciliation and an idempotent delivery
protocol are still required.

The opaque development status endpoint is:

```text
GET /lnpay/status/{payment_id}
```

It is not currently returned to ordinary LNURL wallets and is intended for
testing and operational integration.

## Running the development flow

Set `SAFEBOX_SERVICE_ACORN_ENABLED=true`, then use two terminals:

```sh
# terminal 1
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# terminal 2
poetry run python -m app.service_acorn_worker run
```

Both processes must use the same `SAFEBOX_DATABASE_URL`. With the default
configuration they share `data/database.db`. Docker Compose shares the same
persistent `/app/data` volume.

For the exact one-image/two-container build and operating commands, see the
[Deployment Runbook](DEPLOYMENT.md).

The behavior of concurrent callbacks, multiple web workers, SQLite, and the
singleton provider wallet is documented in
[Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md).

Resolve a registered development handle:

```sh
curl http://127.0.0.1:8000/.well-known/lnurlp/alice
```

Then invoke the returned callback with a whole-satoshi millisatoshi amount:

```sh
curl "http://127.0.0.1:8000/lnpay/alice?amount=21000&comment=test"
```

## Current release gates

This is an interoperable development slice, not yet a production payment
provider. Before accepting meaningful third-party funds, add:

- invoice expiration and cleanup;
- request throttling and database-growth controls;
- robust settlement reconciliation after a crash;
- an idempotent delivery acknowledgement and retry protocol;
- operator review and refund tooling;
- PostgreSQL-backed atomic job claiming for production concurrency;
- monitoring and alerting for stalled states;
- worker-only storage for provider recovery material; and
- Nostr zap validation and receipts if zap support is advertised later.

The current callback rejects zaps and sub-satoshi amounts explicitly. Use small
test payments only.
