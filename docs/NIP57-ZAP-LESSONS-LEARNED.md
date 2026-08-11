# NIP-57 Zap Integration: Lessons Learned

## Outcome

The same Safebox Web Lightning address could receive ordinary payments and an
Acorn CLI zap, yet some Nostr social clients failed immediately while fetching
an invoice. The deployed fix was a dedicated zap callback handler that creates
the invoice synchronously and leaves settlement and delivery with the durable
service worker. A real social-client zap succeeded after deployment.

## What the earlier tests proved—and did not prove

Successful profile payments, ordinary LNURL payments, or CLI-generated zaps
proved that the address, mint, and downstream ecash delivery could work. They
did not prove that a latency-sensitive social client would accept the entire
discovery and callback exchange.

The important distinction was:

- **No callback log:** investigate client discovery, cached profile metadata,
  event availability, or the Lightning address advertised by the profile.
- **Zap rejected:** inspect kind-9734 validation and relay hints.
- **Invoice wait timed out:** the web-to-worker scheduling boundary delayed the
  callback response.
- **Invoice creation failed:** investigate the mint quote endpoint and
  description-hash support.
- **Invoice returned but rejected:** investigate BOLT11 description-hash and
  client-specific NIP-57 requirements.

This staged diagnosis prevented relay lookup, provider validation, mint quote,
settlement, ecash delivery, and receipt publication from being treated as one
indistinguishable “zap failed” problem.

## The architectural lesson

Uniform asynchronous processing is not automatically the best boundary for
every protocol interaction. A durable queue is valuable for settlement and
value delivery, but invoice creation is part of a synchronous LNURL contract
with the requesting client. Moving that one operation into the callback made
the external protocol reliable without moving the service Acorn key or wallet
state into the web process.

The resulting split is deliberately narrow:

- the **web handler** validates, persists, requests, and returns the invoice;
- the **service worker** detects payment, delivers value, and publishes the zap
  receipt;
- the **database** is the durable boundary between them.

This is a useful general pattern: place the minimum latency-critical operation
on the synchronous path and keep ambiguous, retry-sensitive, or key-bearing
operations in their appropriate durable owner.

## Compatibility without abandoning boundaries

Safebox 2 was valuable as a working reference, but copying its permissive
behavior wholesale was unnecessary. Safebox Web retained signature and tag
validation, safe outbound `wss://` relay policy, idempotency, and explicit
failure states.

The compatibility improvement was to treat relay entries as hints. Invalid or
unsafe hints are filtered rather than allowing one entry to reject the whole
request. At least one safe public receipt relay is still required.

Similarly, description-hash binding remains an explicit operator choice. The
system can interoperate with current mint infrastructure in compatibility mode
while accurately documenting that the resulting receipt lacks strict invoice
binding.

## Testing lesson

Unit and integration tests must cover protocol timing and ownership decisions,
not only JSON shape. The regression suite now confirms immediate invoice
creation, duplicate callback reuse, sanitized failures, durable state, safe
relay filtering, worker delivery, and receipt behavior. Live testing remains
essential because social-client timeouts and validation policies are not fully
represented by local test clients.

## Practical conclusion

The final implementation is simpler at the external boundary and disciplined
internally. The client gets the invoice when it needs it; the database retains
the obligation; and the singleton worker remains the only owner of provider
funds and signing authority. This is the balance Safebox Web should preserve as
additional payment protocols are integrated.

See the [NIP-57 Zap Callback Design Note](NIP57-ZAP-CALLBACK-DESIGN-NOTE.md)
for the formal decision and state model.
