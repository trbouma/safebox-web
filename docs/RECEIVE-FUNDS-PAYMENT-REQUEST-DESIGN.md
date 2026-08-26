# Receive Funds: Cash Payments and Clear Transfers

Status: Lightning cash and bidirectional NUT-18 Clear request methods implemented

## Purpose

Safebox Web presents one receiver-initiated workflow for bringing funds into an
attached Acorn:

```text
Receive Funds
  -> choose receive method
  -> enter amount
  -> create payment request
  -> present or transmit request
  -> confirm and finalize receipt
```

The canonical hypermedia resource is `/receive-funds`. The older `/deposit`
paths redirect to it for compatibility.

The receive form presents one balance selector. Lightning SAT through the Acorn
home mint is the first choice, followed by each confirmed Clear mint/unit balance
available to the component. Selecting a balance determines the server-side
request protocol; users do not have to choose a protocol first and an asset in a
second control.

## Current Lightning method

The available method asks the Acorn home mint for a Lightning deposit quote.
The returned BOLT11 invoice is one representation of a broader payment request.
Safebox displays it as QR and text, stores the encrypted quote state in a
short-lived hidden form token, and waits for an explicit user action before
checking and finalizing the payment.

No polling or browser-side wallet logic is introduced. The server remains the
authority for form validation and Acorn performs mint and proof mutations.

## Clear CMU transfer method

The page discovers confirmed Clear Mint Unit (CMU) balances held by the
connected Acorn and offers them as eligible receive units. Selecting one creates a
Cashu NUT-18 transfer request with:

- the requested amount;
- the exact `cmu-<keyset-id>` as its unit;
- a strict accepted-mint list;
- a receiver-generated request ID;
- single-use behavior; and
- a Nostr NIP-17 transport addressed to the Acorn component key.

The resulting `creqA...` request is displayed as a QR code and copyable text.
It is not a Lightning invoice. The amount is the net amount the receiver asks
to obtain after mint input fees, as defined by NUT-18.

Compatible senders deliver the NUT-18 payment payload as a NIP-17 kind `14`
message inside its private gift-wrap transport. Acorn recognizes the standard
`id`, `memo`, `mint`, `unit`, and `proofs` payload, validates its bearer proof
structure, and records it in the existing pending Clear receipt pipeline. The
user then opens Clear Balances and explicitly accepts the transfer. It becomes
confirmed only after the issuing mint accepts and refreshes its proofs.

## Scanning and paying a Clear request

The shared scanner recognizes the `creqA...` prefix and submits the acquired
request to Safebox through its ordinary CSRF-protected form. The server:

1. decodes and validates the NUT-18 CBOR request;
2. requires a supported Nostr NIP-17 transport;
3. matches the requested unit and mint policy against one confirmed Clear
   balance and one sufficient keyset;
4. reads that keyset's current mint input fee;
5. shows the amount, description, mint, receiver fee, and total proof value in
   a review page; and
6. sends only after explicit confirmation.

Confirmation claims the same session-bound outgoing-payment job used for
Lightning transfers and returns a status page immediately. A bounded background
worker loads a fresh Acorn instance, revalidates the request against current
Clear balances and mint fees, exports the proofs, and performs NIP-17 delivery.
The user can leave the status page and return later. Concurrent submissions for
the same Acorn are rejected by the existing per-component job lease.

The job stores only coordination state, the requested amount, and a safe display
unit. The encoded NUT-18 request is passed directly to the in-memory worker and
is not copied into the job row or browser session. Acorn remains authoritative
for relay-backed Clear proof state and Clear transaction history. An uncertain
failure is reported as requiring review and must not be retried blindly because
proof export may already have completed before delivery was interrupted.

NUT-18 defines the requested amount as net of input fees. Acorn therefore adds
enough proof value for the receiving mint to deduct its input fee while leaving
the requested amount. It emits the standard NUT-18 payment payload as a private
NIP-17 kind `14` message. The browser never decodes CBOR, selects proofs, or
constructs the payment event.

## Route boundary

The canonical routes are:

```text
GET  /receive-funds
POST /receive-funds
POST /receive-funds/check
POST /scan/lightning
POST /scan/payment-request
```

The submitted `payment_method` is server validated. `lightning` and `clear`
dispatch to separate protocol operations while returning representations
through this same route family.

The existing `DepositQuoteState` and `DepositQuoteCipher` remain internal names
for the Lightning-specific quote state. A future generalized request-state
envelope should use an explicit method discriminator rather than overloading
the Lightning structure.

## Hypermedia boundary

- The server renders the available methods and their forms.
- Each form submits through ordinary HTTP navigation.
- JavaScript may display progress and disable duplicate submission, but it does
  not create requests, hold proofs, poll mints, or decide finality.
- A scanned NUT-18 request is public capability material carried back by the
  confirmation form and fully decoded, fee-checked, and balance-checked again
  at the Acorn boundary before proofs are spent.
- The confirmation redirects to a server-rendered status resource; refresh is
  an ordinary link and does not require browser polling or application state.

## Compatibility and migration

- `GET /deposit` redirects to `/receive-funds`.
- Legacy deposit POST routes use method-preserving redirects.
- Existing encrypted Lightning quote tokens remain valid because their schema
  and purpose are unchanged.
- Transaction history uses the generalized description `safebox web funds
  received` for newly finalized Lightning requests.

## Current boundary

- Safebox requests one exact CMU from one exact mint. It does not combine
  balances or treat similarly named units from different mints as equivalent.
- The request uses a strict mint list and currently advertises only Nostr
  NIP-17 transport.
- The sender must support NUT-18 and supply enough proofs for the receiver to
  obtain the requested net amount after input fees.
- Receipt and finalization use Acorn's relay-backed Clear state. The Web app
  does not hold bearer proofs in browser application state.
- This first implementation carries the generated request ID into the pending
  receipt, but does not yet maintain a durable outstanding-request registry or
  automatically mark a single-use request complete. Replay resistance remains
  anchored in proof refresh and mint double-spend enforcement.

## References

- [Cashu NUT-18 Payment Requests](https://github.com/cashubtc/nuts/blob/main/18.md)
- [Clear CMU Transfer Request Design](https://github.com/trbouma/clear/blob/main/docs/CMU-PAYMENT-REQUEST-DESIGN.md)
