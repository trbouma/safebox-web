# Lightning Payments to Acorn Handles

Safebox Web now has an initial LNURL-pay path that lets a conventional
Lightning wallet request an invoice for a claimed Safebox handle. The
standalone service Acorn accepts the mint deposit and then delivers the value
to the registered Acorn as a gift-wrapped ecash transfer.

This complete path has been deployed and verified with a real Lightning
payment. That validates the process boundary and interoperability path; it does
not remove the production release gates documented below.

On August 13, 2026, the opposite direction was also exercised: a connected
Safebox Web wallet successfully paid an independently operated Swiss Bitcoin
Pay application over Lightning after the Acorn NUT-00 proof-safety correction.
Together, the tests demonstrate inbound provider delivery and outbound payment
interoperability without limiting either end to Safebox-controlled software.
See the [Funds Arrival and Finalization Milestone](FUNDS-ARRIVAL-AND-FINALIZATION-MILESTONE-2026-08-13.md)
for the assurance, settlement, and residual-risk boundaries.

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

When the singleton service Acorn has registered its public signing key in the
shared database, discovery also advertises `allowsNostr=true` and
`nostrPubkey`. This enables the
[NIP-57 zap flow](https://github.com/nostr-protocol/nips/blob/master/57.md)
without giving the web process access to the provider private key.

## Incoming zaps

The callback treats the `nostr` query parameter as a signed kind-9734 zap
request, not as an ordinary comment. Before accepting a provider obligation it
verifies:

- the Nostr event signature and kind;
- exactly one valid `p` tag representing the social recipient;
- the optional `amount`, `lnurl`, `P`, `e`, `a`, and `k` tag cardinality and
  values required by NIP-57;
- one bounded `relays` tag containing only public `wss://` receipt targets; and
- a bounded zap comment and request size.

Relay hints are advisory. The handler ignores plaintext, localhost, private,
malformed, and duplicate hints, retains at most ten usable public `wss://`
targets, and rejects the request only if no safe receipt target remains. One
bad client-supplied hint therefore cannot poison an otherwise valid zap.

The social recipient key and destination Acorn key are intentionally separate.
For a post zap, the signed request's `p` tag identifies the social recipient
derived by the client from the zapped post. Safebox Web does not require that
key to equal the Acorn component key. The authenticated claimed-handle mapping
selects the destination Acorn and home relay, while the original `p`, `e`, and
`k` context is preserved in the zap receipt. This avoids an unnecessary NIP-05
lookup and supports a social identity that uses a distinct Acorn for funds.

The exact validated JSON is stored in a `provider_zap` row. Its event id is
unique, so a repeated callback for the same signed zap returns the existing
invoice instead of intentionally creating a second obligation.

Zap invoice creation has a dedicated compatibility handler. Unlike ordinary
LNURL payments, the zap callback creates the mint quote synchronously, matching
the proven Safebox 2 interaction pattern and avoiding a background-worker poll
in the latency-sensitive social-client request. The durable row is first marked
`QUOTE_CREATING`, which prevents the singleton worker from racing the callback
and requesting a second invoice. The worker continues to own settlement,
gift-wrapped ecash delivery, and kind-9735 receipt publication.

Invoice binding has two operating modes. The default compatibility mode
requests an ordinary Cashu mint quote after validating and storing the signed
zap request. The dedicated web handler performs this quote request; the worker
begins with settlement monitoring. This permits Lightning payment through mint backends that cannot
create a description-hash invoice for the exact kind-9734 JSON. The worker logs
that the invoice is unbound and continues delivery.

Set `SAFEBOX_NIP57_REQUIRE_DESCRIPTION_HASH=true` to enable strict mode. The
worker then includes the exact JSON as the Cashu NUT-23 mint-quote description,
decodes the returned BOLT11 invoice, and refuses to continue unless the
invoice's description hash equals `SHA256(zap_request_json)`. The configured
mint and Lightning backend must support that behavior.

Compatibility mode is deliberately described as a payment interoperability
measure, not as a fully verified NIP-57 receipt. The resulting kind-9735 receipt
contains the request and invoice, but the invoice does not cryptographically
commit to that request. Strict NIP-57 clients may therefore ignore or reject
the receipt even when the Lightning payment and ecash delivery succeed.

After settlement, the worker first delivers the value to the registered Acorn
as gift-wrapped ecash. It then signs a kind-9735 receipt with the persistent
service Acorn key and publishes it to the validated relays requested by the
payer. The receipt includes the original `p` and optional target tags, the
sender `P` tag, the BOLT11 invoice, and the exact zap request as `description`.

## Verified post-zap interoperability

On 2026-08-09, a live one-satoshi post zap was sent from the independently
installed Acorn CLI to the Safebox Web address
`trbouma@acorn.safebox.dev`. The target was event
`e338282e2a2a8e029b4b5d1f2e1ce6ea5f8aba62cdd42f29bdd87017b4d840a4`.
The event was not present on `wss://nos.lol` or `wss://relay.damus.io`, so
those attempts stopped safely before requesting an invoice. The event was
found on `wss://relay.primal.net`; Acorn then resolved the author payment
metadata, submitted the signed kind-9734 request, received and paid the
invoice, and the value arrived through the Safebox Web provider flow.

This establishes that a Safebox Web Lightning address can receive a real
NIP-57 post zap. It also separates recipient capability from client-specific
failures: when another client reports an invoice-fetch error without any
Safebox Web callback log, profile metadata, caching, or client discovery should
be investigated before the provider payment path. Event relay availability is
a separate prerequisite for senders that begin with an event id; a relay hint
must identify a relay that actually stores the event.

The test confirmed payment and recipient delivery. It did not independently
audit the public kind-9735 receipt on every requested relay or upgrade a
compatibility-mode invoice into a description-hash-bound invoice. Those remain
separate receipt-verification concerns.

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

## Direct Safebox recipients and Continuity Payments

When a connected user pays a Lightning address from Safebox Web, the app first
checks whether the address also resolves as a Safebox/NIP-05 recipient. It
looks up the address name through the recipient domain's NIP-05 document. If
that document supplies a recipient public key and relay, Safebox Web can ask
Acorn to send ecash directly to that Acorn instead of melting proofs through
Lightning.

This is still a connected-mode path. Before sending, Safebox Web verifies the
wallet's proof state with the issuing mint. If the mint cannot be reached, the
app blocks the payment for now and reports that Continuity Payments are not
implemented yet.

The future Continuity Payments path is different. When mints or wider payment
infrastructure are unavailable, Acorn should be able to preview a provisional
in-kind transfer of already issued proofs, show any non-exact amount, and ask
for explicit user approval. The receiving Acorn would treat those funds as
pending mint finality until connectivity returns and the proofs can be checked,
refreshed, or reconciled.

Safebox Web's role is to present the mode, confirmation, warning, and result.
Acorn remains responsible for proof selection, transfer construction, mint
verification, refresh, repair, and finality-sensitive wallet state.

## Durable states

The web process never calls the service Acorn directly. For ordinary payments,
it inserts a `provider_payment` row in `QUOTE_PENDING` and waits briefly for the
worker to store the invoice. For zaps, the dedicated handler inserts
`QUOTE_CREATING`, requests and stores the invoice synchronously, and hands the
worker an `INVOICE_PENDING` row. The worker advances the remaining states:

```text
QUOTE_PENDING
    -> INVOICE_PENDING
    -> SETTLED
    -> DELIVERING
    -> DELIVERED                         ordinary payment
    -> RECEIPT_PENDING -> DELIVERED      zap
                       -> RECEIPT_FAILED zap funds delivered; receipt needs review
```

The zap-specific decision and production experience are recorded in the
[NIP-57 Zap Callback Design Note](NIP57-ZAP-CALLBACK-DESIGN-NOTE.md) and
[NIP-57 Zap Integration: Lessons Learned](NIP57-ZAP-LESSONS-LEARNED.md).

Invoice creation failures become `FAILED`. Acorn distinguishes a connection or
persistence failure proven to have occurred before mint swap submission from
an outcome that became uncertain during the swap or relay publication. A safe
pre-swap failure returns to `SETTLED` and the singleton worker retries it with
bounded exponential backoff. The durable `delivery_attempts` counter is
separate from invoice-settlement polling.

An ambiguous swap or relay-publication exception becomes `DELIVERY_FAILED` and
is not retried automatically. This is intentional: blindly issuing another
ecash transfer could pay the recipient twice. Operator reconciliation and an
idempotent delivery protocol are still required.

`RECEIPT_FAILED` is deliberately distinct from `DELIVERY_FAILED`. The former
means ecash was already delivered but the public NIP-57 receipt could not be
published. The worker does not issue a second ecash transfer while resolving a
receipt problem.

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
- stronger DNS-resolution controls or a provider relay policy for untrusted
  zap receipt destinations.

The current callback accepts validated zaps but still rejects sub-satoshi
amounts. Zap relay URLs are restricted to public-looking `wss://` hosts and a
small maximum count, but DNS rebinding remains a residual outbound-network
risk. Production deployments should apply egress controls. Use small test
payments only until the complete release gates are addressed.
