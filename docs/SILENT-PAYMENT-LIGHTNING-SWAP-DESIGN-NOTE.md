# Silent Payment to Lightning Swap Design Note

## Status

This note remains a proposed provider-swap design. Safebox Web now derives and
displays an attached Acorn's public Nostr Silent Payments (NSP) address and
implements experimental txid-targeted detection plus an explicitly confirmed
self-sweep to a user-selected conventional Bitcoin address. It does not yet
quote a provider swap, reserve liquidity, sweep to the treasurer, or make the
corresponding Lightning payout.

The proposal builds on the
[Bitcoin Silent Payment Gateway Design Note](BITCOIN-SILENT-PAYMENT-GATEWAY-DESIGN-NOTE.md)
and the [Service Acorn Treasury Descriptor Design Note](SERVICE-ACORN-TREASURY-DESCRIPTOR-DESIGN-NOTE.md).
Those notes define the NSP key boundary and cold-treasury destination policy.
This note defines the treasurer's swap obligation.

## Purpose

An Acorn should be able to receive Bitcoin at the Silent Payment address
derived from its `npub`, prove to itself that a specified transaction contains
a matching output, and exchange that output for a Lightning payment through
the Safebox service Acorn.

The service Acorn acts as the application's operational treasurer and swap
agent. It does not receive the user's `nsec` or private NSP scan material. It
quotes the exchange, reserves Lightning liquidity, commits to a fresh Bitcoin
destination, verifies settlement, and pays the bound Lightning invoice.

This is a non-atomic provider swap. The user irreversibly transfers Bitcoin in
exchange for a signed Lightning settlement obligation. It must not be
presented as trustless, atomic, or guaranteed solely by the Bitcoin protocol.

## Protocol basis

The public receive address follows the OpenETR NSP derivation contract:

```text
P = BIP-340 even-y public point represented by the Acorn npub
t_scan  = H_tag("nostr-sp/scan", P)
t_spend = H_tag("nostr-sp/spend", P)

ScanPub  = P + t_scan G
SpendPub = P + t_spend G

address = bech32m("sp", v0 || ScanPub || SpendPub)
```

This derivation needs only the public `npub`. Safebox Web may therefore derive
and display the address without reading private key material beyond what its
ordinary authenticated session already contains.

Receipt detection and spending are different. The NSP private scan key is
root-equivalent because the public tweak can be subtracted to recover the
underlying Nostr private key. Safebox must never send the private scan key to
the treasurer, a remote scanner, reverse proxy, database, relay, browser page,
or job payload.

The initial discovery model is txid-targeted local inspection:

1. the sender or user provides a txid hint;
2. Safebox Web fetches only that public transaction;
3. the authenticated request derives private NSP material in process memory;
4. it detects matching outputs locally; and
5. private material is discarded when the request ends.

This is consistent with the original
[Nostr Silent Payments brief](https://gist.github.com/trbouma/77648ebe1005b181b67d1c4b42c7f31d):
the static address is publicly derivable, but receipt detection and fund
control remain private to the matching key holder.

## Roles and boundaries

| Component | Responsibility |
| --- | --- |
| Attached Acorn | Control the NSP receive keys and authorize spending a detected output |
| Browser | Display the address and quote, submit the txid and payout request, and obtain explicit user confirmation |
| Safebox Web | Perform request-scoped detection, verify the treasurer signature, and construct the authorized sweep |
| Safebox Acorn component | Implement private NSP detection, output-key reconstruction, and transaction construction |
| Service Acorn | Sign quotes and receipts and provide persistent treasurer authority |
| Service worker | Reserve liquidity, allocate destinations, monitor settlement, and pay Lightning exactly once |
| Bitcoin Core watch-only wallet | Allocate fresh descriptor-derived treasury addresses atomically |
| Offline Sparrow wallet | Retain the treasury seed and exclusive long-term Bitcoin spending authority |
| Lightning backend | Provide and execute outgoing Lightning liquidity |
| Database | Persist quotes, jobs, transitions, idempotency keys, and reconciliation evidence |

The web process is trusted with the attached Acorn's `nsec` while servicing an
authenticated request because it decrypts the browser-held session. The
service worker is trusted with the service Acorn `nsec`, but it must never be
given the user's `nsec`.

## User-visible flow

```text
sender
  -> pays the user's npub-derived sp1... address

user Acorn
  -> receives or enters a txid hint
  -> detects its output locally
  -> requests an on-chain-to-Lightning quote

service Acorn treasurer
  -> reserves Lightning liquidity
  -> allocates a fresh treasury address
  -> signs the complete quote

user Acorn
  -> verifies the signed quote
  -> explicitly authorizes the sweep
  -> signs and broadcasts the Bitcoin transaction

service Acorn treasurer
  -> verifies destination and confirmations
  -> pays the bound Lightning invoice exactly once

offline treasury
  -> receives and later controls the swept Bitcoin
```

## Lightning payout profiles

The treasurer may support two explicit payout profiles.

### Direct Lightning invoice

The user supplies a BOLT11 invoice. The quote binds its payment hash, amount,
expiry, and destination metadata. After Bitcoin settlement, the treasurer pays
that exact invoice.

### Acorn home-mint deposit

The attached Acorn requests a mint quote from its selected home mint and gives
the resulting Lightning invoice to the swap. After the treasurer pays it, the
Acorn completes minting and receives new ecash proofs.

The practical flow is:

```text
Bitcoin Silent Payment
  -> user-authorized sweep
  -> treasurer Lightning payment
  -> mint quote settlement
  -> Acorn ecash proofs
```

The interface must identify the selected mint and explain that the resulting
ecash carries that mint's credit and availability risk.

## Signed quote contract

The service Acorn must sign every actionable quote. The signature lets the
attached Acorn verify that the expected treasurer committed to the destination
and payout terms before the user transfers control of Bitcoin.

The signed payload should bind at least:

- schema and protocol version;
- unique quote and swap identifiers;
- service Acorn public key;
- Bitcoin network;
- source txid and output index;
- gross source value;
- destination address;
- treasury configuration identifier;
- miner-fee rate and estimated fee;
- service fee and any routing-fee policy;
- net Lightning payout amount;
- payout profile;
- BOLT11 invoice and payment hash;
- recipient Acorn public key;
- minimum Bitcoin confirmations;
- quote creation and expiry times; and
- nonce or replay-prevention identifier.

The quote must be signed only after the worker has:

1. validated the invoice and its amount;
2. reserved sufficient Lightning liquidity;
3. allocated and durably reserved the Bitcoin destination;
4. confirmed the source outpoint is not already claimed; and
5. persisted the proposed swap before returning it.

Safebox Web must independently verify the service signature, expected service
public key, quote expiry, source outpoint, destination, amount, and invoice
before rendering the confirmation action.

## Bitcoin destination choices

### Fresh descriptor-derived address

The first implementation should use a fresh `bc1p...` address allocated by a
Bitcoin Core watch-only wallet from the Sparrow receive descriptor stored in
the service Acorn.

Advantages:

- Bitcoin lands directly in offline-controlled treasury custody;
- the service worker never needs the treasury private key;
- address allocation and index state are handled atomically by Bitcoin Core;
- recovery follows standard descriptor-wallet procedures; and
- implementation is simpler than constructing a second Silent Payment.

The privacy limitation is that later treasury consolidation may permit
clustering even though each customer receives a fresh destination.

### Service Silent Payment destination

A later profile may send to the service Acorn's `sp1...` address. This can
reduce destination linkage, but the output is controlled by the hot service
key until another sweep moves it to cold custody. It also requires complete
BIP-352 sender construction rather than treating the `sp1...` string as an
ordinary on-chain address.

The two profiles must not be silently interchanged.

## Transfer-of-control warning

The NSP brief recommends sweeping received funds only to a fresh address the
receiver controls. A swap intentionally departs from that conservative rule:
the user transfers control to the treasurer in exchange for a signed Lightning
obligation.

Before authorization, the confirmation page must state plainly:

- the Bitcoin transaction cannot be reversed after broadcast;
- the destination is controlled by the provider's offline treasury;
- the Lightning payout is a contractual service obligation, not an atomic
  Bitcoin guarantee;
- the number of confirmations required before payout;
- the exact fee and net payout; and
- the operator's failure, support, and refund policy.

## State machine

The durable swap state should be monotonic:

```text
DETECTED
  -> QUOTE_RESERVED
  -> USER_AUTHORIZED
  -> SWEEP_BROADCAST
  -> BITCOIN_CONFIRMED
  -> LIGHTNING_PAYING
  -> COMPLETE
```

Terminal or review states include:

```text
QUOTE_EXPIRED
SOURCE_ALREADY_SPENT
SWEEP_FAILED
LIGHTNING_FAILED
LIGHTNING_AMBIGUOUS
MANUAL_REVIEW
```

The source outpoint is the primary claim key. One outpoint can fund at most one
swap. The Lightning payment hash is the payout idempotency key. A retry after
an ambiguous Lightning response must query payment status before attempting
another payment.

## Fee presentation

The quote must present complete sat-denominated accounting:

```text
gross value of the detected Bitcoin output
- Bitcoin sweep miner fee
- Safebox swap fee
- disclosed Lightning routing-fee allowance, if charged to the user
= net Lightning invoice amount
```

If the Lightning invoice has a fixed amount, the quote must solve and validate
the accounting before authorization. It must not deduct an undisclosed amount
after the Bitcoin sweep.

## Liquidity and treasury accounting

Bitcoin received into the cold treasury does not immediately create Lightning
outbound capacity. The treasurer pays from separately maintained operational
Lightning liquidity. This is therefore a balance-sheet swap.

The operator must monitor:

- available outbound Lightning liquidity;
- reserved but unpaid quote obligations;
- confirmed Bitcoin received;
- Lightning payments in progress or ambiguous;
- accumulated cold-treasury Bitcoin;
- fees and realized spread; and
- the need for deliberate rebalancing between on-chain and Lightning holdings.

The service Acorn should have bounded operational liquidity. The offline
treasury should remain the long-term authority over received Bitcoin.

## Public address presentation

Safebox Web may derive the attached Acorn's NSP address from its `npub` on each
wallet-page request. The address and QR are public receive information; neither
contains the `nsec`, private scan key, private spend key, or evidence that a
particular payment has been received.

The first interface uses a collapsed HTML `details` element so the address is
available without dominating the ordinary wallet view. The QR encodes the raw
`sp1...` address using a high-contrast black-and-white SVG. No client-side
application logic or private-key processing is required.

Public derivability establishes that the address corresponds to the displayed
Acorn key under the OpenETR NSP convention. It does not prove that the user
published the address intentionally or received any particular transaction.

## Proposed endpoints

The later swap implementation may use server-rendered hypermedia routes:

- `GET /bitcoin/silent-payment` — display the address and txid form;
- `POST /bitcoin/silent-payment/detect` — perform targeted local detection;
- `POST /bitcoin/swap/quote` — reserve liquidity and return a signed quote;
- `GET /bitcoin/swap/{swap_id}` — show durable status;
- `POST /bitcoin/swap/{swap_id}/authorize` — verify and broadcast the sweep;
- `POST /bitcoin/swap/{swap_id}/refresh` — reconcile Bitcoin and Lightning;
  and
- `GET /bitcoin/swap/{swap_id}/receipt` — show signed completion evidence.

Every state-changing route requires the authenticated Acorn session, same-origin
validation, CSRF protection, and idempotency. Neither the txid nor a quote id is
sufficient authorization on its own.

## Security requirements

- Never disclose or transmit the user's NSP private scan key.
- Never store reconstructed output private keys.
- Never place the service Acorn `nsec` in the web process or browser.
- Never place the treasury seed or xprv in Safebox.
- Never accept a destination address that is not bound into the signed quote.
- Never issue an actionable quote without a durable liquidity reservation.
- Never repay an outpoint or payment hash twice.
- Never retry an ambiguous Lightning payment without first checking status.
- Never log session cookies, nsecs, invoices containing sensitive descriptions,
  or complete confidential treasury descriptors.
- Treat the web process as trusted while it temporarily uses the session nsec.
- Treat a compromised worker as capable of redirecting future swaps and monitor
  signed configuration and destination changes.

## Implementation phases

### Phase 1 — public receive surface

- derive `npub -> sp1...` in a public-only module;
- pin it to OpenETR test vectors;
- show text and QR in a collapsed wallet panel; and
- document its public nature and limitations.

### Phase 2 — targeted detection

- integrate OpenETR as the private detection and sweep component;
- accept and validate txid hints;
- fetch transactions from a configured trusted Bitcoin backend;
- detect matching outputs inside request-scoped memory; and
- display outpoint, value, confirmations, and recognition fingerprint.

### Phase 3 — quote and reservation

- implement the signed quote schema;
- add Lightning-liquidity reservation;
- allocate descriptor-derived destinations;
- persist the state machine and idempotency keys; and
- render explicit transfer-of-control confirmation.

### Phase 4 — sweep and settlement

- construct and sign the exact authorized sweep;
- monitor confirmation and reorganization policy;
- pay the bound invoice exactly once;
- support home-mint completion; and
- produce signed receipts and operator reconciliation views.

## Release gates

Do not accept meaningful swaps until:

- public derivation matches independently produced OpenETR vectors;
- private scan material never crosses the request boundary;
- real wallet-produced NSP payments are detected correctly;
- signed quote alteration is rejected;
- source outpoints and payment hashes are uniquely constrained;
- concurrent quote requests cannot reuse destinations or liquidity;
- expired quotes cannot be authorized;
- ambiguous Lightning payments stop for reconciliation;
- crash recovery resumes every non-terminal state safely;
- treasury and Lightning balances reconcile against completed swaps; and
- an independent security review covers key handling, transaction construction,
  fee calculation, quote signatures, and settlement idempotency.
