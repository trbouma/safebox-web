# Receive Funds: Cash Payments and Clear Transfers

Status: Lightning cash and initial NUT-18 Clear receive methods implemented

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

## Route boundary

The canonical routes are:

```text
GET  /receive-funds
POST /receive-funds
POST /receive-funds/check
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
- Method-specific state is authenticated and carried by the representation or
  persisted at the protocol-owned boundary, not in browser application state.
- A completed request returns to the wallet for a freshly loaded balance.

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
