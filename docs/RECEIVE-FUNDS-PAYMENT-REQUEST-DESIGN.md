# Receive Funds: Cash Payments and Clear Transfers

Status: Lightning cash payment method implemented; Clear CMU transfer method proposed

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

## Proposed Clear CMU transfer method

The same page will later discover the Clear Mint Units (CMUs) the connected
Acorn can receive and offer them as transfer units. Selecting one creates a
Cashu NUT-18 transfer request with:

- the requested amount;
- the exact `cmu-<keyset-id>` as its unit;
- a strict accepted-mint list;
- a receiver-generated request ID;
- single-use behavior; and
- a Safebox-supported transport.

The resulting `creqA...` request can be displayed as a QR code or transmitted
through an explicitly supported transport. It is not a Lightning invoice.

When proofs arrive, Safebox validates the request ID, CMU, proof keyset, mint,
amount, and replay state. The transfer remains pending until Acorn
refreshes the proofs through the issuing Clear mint.

## Route boundary

The canonical routes are:

```text
GET  /receive-funds
POST /receive-funds
POST /receive-funds/check
```

The submitted `payment_method` is server validated. `lightning` is currently
implemented. The field remains a compatibility name for the current cash
payment form. A later Clear transfer method should dispatch to a separate
service-layer function while returning representations through this same route
family.

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

## Implementation sequence for Clear

1. Complete canonical CMU and keyset-ID support in Clear and Acorn.
2. Add shared NUT-18 request and payload codecs.
3. Discover eligible CMUs for the connected Acorn.
4. Render the selected CMU and logical mint clearly before confirmation.
5. Generate the NUT-18 request and QR representation.
6. Receive the transfer payload through a supported transport.
7. Validate and refresh the proofs through Acorn.
8. Mark a single-use request complete only after successful finalization.

## References

- [Cashu NUT-18 Payment Requests](https://github.com/cashubtc/nuts/blob/main/18.md)
- [Clear CMU Transfer Request Design](https://github.com/trbouma/clear/blob/main/docs/CMU-PAYMENT-REQUEST-DESIGN.md)
