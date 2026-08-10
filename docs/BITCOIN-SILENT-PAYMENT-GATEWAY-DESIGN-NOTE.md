# Bitcoin Silent Payment Gateway Design Note

## Status

Safebox Web now implements the first user-controlled slice of this design. An
authenticated user can submit a txid, detect an NSP output belonging to the
attached Acorn, review the miner fee and destination, and explicitly broadcast
a self-sweep to a conventional Bitcoin address. Detection and signing occur in
request-scoped memory through the pinned OpenETR component. Private NSP material
and signed transaction bytes are not rendered into the browser or persisted by
Safebox Web.

This implementation is experimental and has unit coverage but has not yet been
validated end to end with small mainnet receipts. It does not create a provider
quote, sweep to the service treasurer, deliver ecash, or settle Lightning. Those
provider obligations remain proposed design work.

The design assumes that the final user payout is sat-denominated Cashu ecash
delivered to the attached Acorn, matching the existing service-worker delivery
model. A later on-chain payout product would be a different workflow with
different fee, custody, and confirmation semantics.

The service may also support a direct Lightning or home-mint-invoice payout
through a signed, non-atomic provider swap. That treasurer role, quote contract,
liquidity reservation, transfer-of-control warning, and settlement state
machine are specified separately in the
[Silent Payment to Lightning Swap Design Note](SILENT-PAYMENT-LIGHTNING-SWAP-DESIGN-NOTE.md).

## Purpose

Safebox Web should let an attached Acorn:

1. derive its Nostr Silent Payments address from its public key;
2. display the address as text and QR for receiving Bitcoin;
3. submit a Bitcoin transaction id as a targeted receipt hint;
4. verify locally that the transaction contains an output controlled by the
   attached Acorn key;
5. explicitly sweep that output to a Bitcoin Silent Payments address
   controlled by the persistent Safebox service worker; and
6. receive the corresponding amount as ecash, less disclosed miner and service
   fees, after the worker detects and confirms the sweep.

This creates a Bitcoin-to-Acorn gateway. It is not a trustless atomic swap. The
user transfers confirmed Bitcoin to a provider-controlled output and receives
a provider-delivered ecash payment in return. The operator therefore assumes a
real settlement obligation and must disclose its fee, confirmation, liquidity,
mint, failure, and recovery policies.

## Upstream protocol basis

The design follows OpenETR's
[Nostr Silent Payments specification](https://github.com/trbouma/openetr/blob/main/docs/specs/NOSTR_SILENT_PAYMENTS_SPEC.md),
[derivation decision](https://github.com/trbouma/openetr/blob/main/docs/specs/SILENT_PAYMENTS_DERIVATION_DECISION_NOTE.md),
and [Silent Payments design note](https://github.com/trbouma/openetr/blob/main/docs/specs/SILENT_PAYMENTS_DESIGN_NOTE.md).
OpenETR supplies an identity-derived receiver-key contract on top of the
transaction-level behavior specified by
[BIP-352](https://github.com/bitcoin/bips/blob/master/bip-0352.mediawiki).

Under the OpenETR Nostr Silent Payments (NSP) contract, let:

- `d` be the Nostr private key;
- `P = dG` be its public key;
- `t_scan = H_tag("nostr-sp/scan", P)`; and
- `t_spend = H_tag("nostr-sp/spend", P)`.

The public receiver keys are:

- `ScanPub = P + t_scan G`; and
- `SpendPub = P + t_spend G`.

The private receiver keys are:

- `scan_priv = d + t_scan mod n`; and
- `spend_priv = d + t_spend mod n`.

The `sp1...` address encodes the scan and spend public keys. It can be derived
from an `npub` without access to the matching `nsec`. Receipt detection and
output-private-key reconstruction require the private material derived from the
matching `nsec`.

Safebox Web must call the installable OpenETR component for this derivation,
receipt detection, and transaction construction. It must not duplicate the
domain tags, point arithmetic, BIP-352 transaction rules, address encoding, or
private-output reconstruction in route functions.

The initial implementation pins OpenETR to a reviewed Git revision whose
Silent Payment operations match current OpenETR main while avoiding an
unrelated QR-code dependency conflict with Safebox Acorn. OpenETR imports are
lazy and boundary-wrapped because its Bitcoin dependency currently mutates the
process-wide Python decimal context during import. Safebox restores that
context immediately after loading the component. This containment should be
removed when the upstream import side effect is corrected.

The configured Bitcoin HTTP backend receives the submitted txid during
transaction and UTXO lookup. It can therefore correlate the service address,
request time, and txid. It receives neither the Acorn `nsec` nor derived private
NSP material. Operators should disclose this metadata boundary and may replace
the default public backend with a controlled compatible endpoint.

## Critical scan-key constraint

The NSP private scan key is root-equivalent. Because `t_scan` is public, anyone
who learns `scan_priv` can calculate the original Nostr private key and then
derive the NSP spend key.

Consequently:

- the user scan key must never be sent to the service worker;
- it must never be sent to an untrusted Frigate or remote scanner;
- it must never be stored in the Safebox database, job payload, browser page,
  log, URL, subprocess argument, or relay event;
- the service worker's scan key must likewise remain inside its trusted worker
  boundary; and
- txid-targeted local scanning is the preferred first implementation.

Fetching a public transaction from an Esplora-compatible endpoint does not
require disclosing the scan key. Safebox Web retrieves the transaction by txid
and performs NSP detection locally in request-scoped process memory. A
self-hosted Bitcoin node or Esplora service is preferable because querying a
third party still reveals which txid the Safebox operator is investigating.

## Component and process boundaries

| Component | Responsibility |
| --- | --- |
| Browser | Display the derived address, submit a txid, review a quote, and explicitly authorize the sweep |
| Safebox Web | Authenticate the attached Acorn, use its request-scoped `nsec`, validate the txid locally, request a worker quote/reservation, construct and broadcast the user-authorized sweep, and create a durable settlement job |
| OpenETR component | Derive NSP material, inspect the source transaction, detect matched outputs, reconstruct the spend key, construct the BIP-352 payment output, and sign the sweep |
| Bitcoin/Esplora infrastructure | Return public transaction and UTXO data, fee estimates, confirmations, and broadcast responses |
| Service Acorn worker | Control the persistent provider `nsec`, derive and scan its own NSP receiver, monitor the durable job, enforce confirmation policy, calculate final settlement, and deliver ecash |
| Safebox database | Coordinate idempotent quotes and jobs without storing either party's private key |
| Acorn | Persist and deliver the user's resulting ecash proofs and transaction history |

The service worker cannot sweep the user's original NSP receipt unless it is
given the user's root-equivalent private material. That is prohibited. The
source sweep is therefore constructed and signed inside the authenticated
Safebox Web request where the attached Acorn key is already temporarily
available from the secure session cookie.

The worker becomes involved after the user authorizes a transaction paying the
worker's NSP address. It detects that provider-owned output using its own
persistent key and fulfills the ecash settlement obligation.

## Address generation

### Attached Acorn receive address

Safebox Web can derive the user's NSP address from the attached Acorn `npub`
alone:

```text
attached Acorn npub
    -> OpenETR NSP public derivation
    -> ScanPub + SpendPub
    -> mainnet sp1... address
```

The wallet page may show:

- the address as selectable text;
- a black-and-white QR code;
- the component `npub` from which it was derived;
- the network and derivation label, such as `mainnet / OpenETR NSP v1`;
- a statement that anyone can derive the same address from the `npub`; and
- a **Check Bitcoin payment** action accepting a txid.

Public derivability proves that the address corresponds to the key under the
OpenETR derivation convention. It does not prove that the key holder knowingly
published the address or agreed to receive a particular payment.

### Service-worker receive address

The service worker derives its own NSP address from its persistent service
Acorn key. Its public service descriptor should contain only:

- service `npub`;
- derived NSP address;
- derivation/profile version;
- Bitcoin network;
- configured confirmation policy; and
- activation or rotation metadata.

The web tier can independently rederive the service address from the service
`npub` and OpenETR rule. It never needs the service `nsec`.

Routine worker restarts must restore the same service Acorn key and therefore
the same NSP address. An operator rotation requires an explicit transition:
stop new quotes, drain or resolve pending jobs, publish the new descriptor, and
retain the old key until every potentially payable old transaction is handled.
Burning the service Acorn on normal shutdown would make pending Bitcoin
receipts undiscoverable and unspendable.

The worker's NSP wallet should be treated as a hot gateway, not the final
Bitcoin treasury. Once a user forwards a payment to the service NSP address,
the only new receipt-specific discovery input the worker needs is the txid. It
still needs its persistent service `nsec`, Bitcoin backend, fee policy, and
network configuration to detect and spend the resulting output.

The proposed cold-treasury implementation stores a public receive output
descriptor as an encrypted reserved record in the service Acorn and delegates
fresh-address allocation to a Bitcoin Core watch-only descriptor wallet. It
never places the treasury seed or extended private key in Safebox. See the
[Service Acorn Treasury Descriptor Design Note](SERVICE-ACORN-TREASURY-DESCRIPTOR-DESIGN-NOTE.md).

## User workflow

### 1. Display the receive address

An authenticated GET derives the address from the attached Acorn `npub` and
renders it. It does not scan the blockchain or expose private key material.

### 2. Receive a txid hint

After a sender pays the address, the attached user supplies the 64-character
hex txid. Manual entry is sufficient for the first implementation. A later
NIP-17 DM may carry the same txid hint, but it is a discovery convenience and
not payment proof.

### 3. Detect the receipt locally

Safebox Web uses the attached `nsec` only in process memory to:

1. derive the NSP private scan and spend material;
2. fetch the transaction identified by the txid;
3. execute BIP-352 receipt detection locally;
4. enumerate matched output indexes and values; and
5. check UTXO and confirmation status.

If multiple outputs match, the user must select or explicitly confirm the
intended `vout`. A txid by itself is not enough to identify one spendable
receipt.

### 4. Obtain a settlement quote

Before the user transfers Bitcoin to the provider, Safebox Web requests a
durable quote/reservation from the worker. The quote binds:

- network, source txid, and source vout;
- gross matched value;
- source confirmation state;
- proposed Bitcoin fee rate;
- estimated miner fee;
- amount expected at the worker output;
- fixed or proportional provider fee;
- final ecash payout amount;
- payout mint and unit;
- recipient Acorn `npub` and delivery relay;
- service NSP address and derivation version;
- minimum required confirmations;
- quote expiry; and
- a unique quote id.

The worker should reserve sufficient ecash liquidity before returning an
actionable quote. A provider must not invite an irreversible Bitcoin sweep and
only afterward discover that it cannot pay the user.

The displayed accounting should be explicit:

```text
gross matched Bitcoin receipt
- Bitcoin miner fee for the user-to-service sweep
= value delivered to the service worker
- Safebox service fee
= ecash amount delivered to the attached Acorn
```

Any mint fee, conversion fee, or minimum payout must be included before
confirmation. “One sat of Bitcoin equals one sat of ecash” is a provider quote,
not a protocol guarantee; ecash also carries the credit and availability risk
of the identified mint.

### 5. Confirm and broadcast the sweep

The confirmation page shows the complete quote, destination service identity,
source outpoint, recognition fingerprint, and expiry. A CSRF-protected POST is
required.

Safebox Web then reloads the transaction and UTXO, verifies that the quote is
still current, and asks OpenETR to construct a transaction that:

- spends only the confirmed matched NSP output approved by the user;
- creates the destination output using the service worker's `sp1...` receiver
  keys and the BIP-352 sender algorithm;
- deducts the disclosed miner fee;
- does not create an undisclosed change output; and
- signs with the reconstructed private key for that matched user output.

The current OpenETR `create_silent_payment_sweep_result` accepts an ordinary
on-chain destination address. A Silent Payments address is not itself an
on-chain script and cannot be passed through an ordinary address-to-script
decoder. Before Safebox Web implements this design, OpenETR needs a component
operation that constructs a BIP-352 destination output from the sweep input,
for example:

```python
create_silent_payment_forward_result(
    nsec: str,
    source_txid: str,
    source_vout: int,
    destination_silent_payment_address: str,
    fee_rate: float,
    api_base: str,
) -> SilentPaymentForwardResult
```

The result must include the signed transaction, source outpoint, gross value,
miner fee, destination value, expected destination output key and vout, txid,
network, and enough deterministic metadata for the service worker to validate
the job without exposing the user's private material.

Broadcast has an indeterminate failure mode: a timeout can occur after a node
accepted the transaction. Safebox Web must retain the locally calculated txid,
query for it before retrying, and never construct a second conflicting sweep
automatically.

### 6. Worker detection and confirmation

The web process records the sweep txid as a targeted hint in the durable job.
The service worker fetches that public transaction and locally scans it with
its own NSP private material. It must verify:

- the transaction contains the expected service-owned output;
- the detected output value equals the quote's expected provider receipt;
- the expected txid/vout has not already been claimed or settled;
- the transaction meets the configured confirmation depth;
- the transaction remains in the accepted chain; and
- the quote and recipient binding match the durable job.

The worker must not trust destination metadata supplied by the browser or web
route when its own BIP-352 scan can verify the output.

### 7. Provider treasury sweep

After detecting a service-owned NSP output, the worker may sweep it out of the
hot gateway wallet to a fresh address belonging to a separate Bitcoin treasury.
The preferred treasury boundary is a watch-only output descriptor or account
`xpub` from which the worker can derive fresh non-hardened receive addresses.
The corresponding `xprv` or wallet seed remains offline or in a separate
signing boundary.

This creates a useful separation:

```text
user NSP output
    -> user-authorized BIP-352 payment
service-worker NSP output (hot, detected from txid)
    -> provider-controlled treasury sweep
fresh treasury bc1p address (cold or separately controlled)
```

The worker needs its service `nsec` and the forwarding txid to identify and
spend the provider NSP output. It does not need the treasury private key to send
to an address derived from a watch-only descriptor.

The confidentiality rules differ by material:

- the service `nsec` is a spending secret and must be protected as critical key
  material;
- the treasury `xprv` or seed is a spending secret and should not be available
  to the gateway worker;
- an account `xpub` or watch-only descriptor cannot spend funds, but it is
  privacy-sensitive because disclosure can reveal the treasury's derived
  addresses and transaction history; and
- an individual `bc1p` receive address is public routing information, not a
  secret. Reusing one fixed address would make gateway consolidation much more
  visible.

A Bitcoin Core descriptor wallet is preferable to manually incrementing an
`xpub` index. It can own the watch-only descriptor, derive a fresh address for
each sweep, preserve the derivation cursor, enforce the network, and support
rescan and recovery. If Safebox derives addresses itself, index allocation must
be atomic, persisted before use, backed up, and recoverable with an adequate
gap limit.

Treasury movement is an operator custody operation rather than part of the
user's proof of payment. The user's quoted ecash amount should not change later
because the operator selected a different treasury fee rate. The provider must
either absorb this second miner fee from its disclosed service fee or include a
conservative treasury-cost component in that fee before the user confirms.

Ecash settlement may become eligible once the service NSP receipt reaches the
promised confirmation depth. It need not wait for the asynchronous treasury
sweep unless the operator explicitly makes successful cold-storage movement a
published settlement condition. Keeping the operations separate prevents a
treasury outage from silently changing an already accepted user obligation.

Fresh treasury addresses reduce address reuse, but they do not defeat
common-input heuristics if the operator later consolidates many outputs in one
transaction. Treasury coin selection, batching, and consolidation therefore
remain part of the provider's privacy policy.

### 8. Ecash settlement

After the Bitcoin confirmation threshold is met, the singleton worker delivers
the quoted net amount through Acorn's gift-wrapped ecash transfer mechanism to
the recipient `npub` and relay captured in the quote.

The recipient explicitly receives the transfer through the ordinary Safebox
Web transaction-history action. Its transaction comment should identify the
settlement without revealing unnecessary on-chain data, for example:

```text
Bitcoin Silent Payment settlement: <short sweep fingerprint>
```

The provider database retains the full txid for reconciliation, but public
ecash event content should not publish the Bitcoin txid unless that disclosure
is required and explicitly accepted.

## Durable job state and idempotency

The job state should be more explicit than a boolean paid flag. A candidate
state machine is:

```text
quoted
  -> user_confirmed
  -> sweep_broadcast
  -> provider_output_detected
  -> awaiting_confirmations
  -> settlement_ready
  -> ecash_delivery_started
  -> delivered
```

Exceptional states include:

```text
quote_expired
source_spent
broadcast_indeterminate
transaction_replaced
reorg_hold
liquidity_hold
delivery_ambiguous
manual_review
reconciled
```

The database must enforce a uniqueness constraint over at least
`(network, source_txid, source_vout)` and separately over the provider receipt
outpoint. One Bitcoin output can fund at most one settlement.

The worker must follow the same no-double-delivery rule used for Lightning
provider jobs: once ecash delivery becomes ambiguous, automatic retries stop
until reconciliation establishes whether the existing transfer was accepted.

Suggested durable fields include:

- quote id and state;
- network, source txid, and source vout;
- sweep txid and provider output vout;
- treasury address, descriptor index or reservation id, and treasury sweep
  txid where applicable;
- recipient `npub` and delivery relay;
- service `npub`, NSP address, and derivation version;
- gross value, miner fee, provider received value, service fee, and net payout;
- payout mint and unit;
- confirmation target and observed height/depth;
- signed-transaction txid and broadcast observations;
- ecash delivery event id and reconciliation state; and
- created, quoted, confirmed, delivered, and updated timestamps.

The database must never store the user `nsec`, either NSP private key, the
service `nsec`, the session cookie, or reconstructed source-output private key.
It should store a descriptor index or Bitcoin Core reservation reference rather
than copying a complete confidential treasury `xpub` into every job.

## Confirmation, replacement, and reorganization policy

The current OpenETR sweep requires the source output to be a confirmed UTXO.
Safebox Web should retain that conservative rule initially.

The worker should also wait for a configured confirmation depth on the sweep
before delivering ecash. The exact depth is an operator risk decision based on
value and environment; it must not be silently hard-coded. Larger payments may
require more confirmations or manual review.

The design must account for:

- source transactions that are unconfirmed, replaced, or double-spent;
- a source output spent before the confirmed POST;
- sweep broadcast timeouts;
- opt-in replacement policy;
- chain reorganizations before settlement;
- a sweep that confirms after the quote's nominal expiry; and
- a reorganization after ecash was delivered.

The first implementation should avoid replace-by-fee complexity by constructing
a single-output sweep with a deliberately selected fee rate and no automatic
fee bump. A later child-pays-for-parent or replacement strategy requires a
separate design and must preserve the same settlement idempotency key.

## Fee policy

The service fee must be deterministic and disclosed before the user signs.
Possible policies include:

- a fixed sat fee;
- a percentage fee with a stated rounding rule;
- the greater of a fixed minimum and percentage; or
- zero service fee during a pilot.

Miner fee and service fee are separate. The miner fee is consumed by the
Bitcoin transaction. The service fee is retained by the operator from the
value it actually detects at its output.

The service must define:

- minimum gross receipt;
- minimum net ecash payout;
- maximum accepted fee rate or miner fee;
- dust handling;
- quote lifetime;
- supported Bitcoin network;
- supported ecash mint and unit; and
- rounding behavior using integers only.

If the net payout would be zero, negative, dust-equivalent, or below the
operator's minimum, Safebox Web must reject the quote before sweep signing.

## Privacy and trust model

Silent Payments prevent the static `sp1...` receiver address from appearing as
a reusable on-chain address. They do not make the gateway invisible to its
participants.

Safebox Web learns the association among:

- the authenticated attached Acorn `npub`;
- the submitted source txid and matched outpoint;
- the sweep txid;
- the provider-owned output;
- the amount and fee; and
- the ecash recipient and delivery relay.

The reverse proxy, application operator, database operator, Bitcoin backend,
and service worker therefore occupy meaningful trust boundaries. Logs should
use short fingerprints and internal job ids rather than full txids where full
values are not operationally required.

The service worker is a custodial bridge for the interval between receiving the
Bitcoin output and completing ecash delivery. Users rely on the operator to:

- preserve the persistent service key;
- maintain Bitcoin and ecash liquidity;
- apply the quoted fee correctly;
- recognize confirmations and reorganizations correctly;
- deliver only once; and
- provide a reconciliation and recovery process.

The persistent service `nsec` and any treasury watch-only descriptor should be
available only to the worker role. They should not be placed in the web
container, returned through an application endpoint, or shared through the
ordinary database. The service `nsec` requires secret-manager or owner-only
file protection. The descriptor or account `xpub` merits confidential
configuration handling even though it cannot authorize a spend.

Deployments must evaluate the legal and compliance implications of accepting
Bitcoin and delivering ecash for a fee. The protocol design does not determine
whether a particular operator is acting as an exchange, custodian, money
transmitter, or another regulated service.

## Hypermedia workflow

The user-facing flow remains server-directed:

- `GET /bitcoin/silent-payment` displays the derived user address;
- `POST /bitcoin/silent-payment/check` validates a txid and returns a receipt
  report;
- `POST /bitcoin/silent-payment/quote` creates a durable worker-backed quote;
- `GET /bitcoin/silent-payment/quote/{id}` displays exact fees and destination;
- `POST /bitcoin/silent-payment/quote/{id}/confirm` signs and broadcasts only
  after CSRF validation and explicit confirmation; and
- `GET /bitcoin/silent-payment/settlement/{id}` displays confirmation and ecash
  delivery status.

POST/Redirect/GET should prevent accidental resubmission. JavaScript may show
progress and disable buttons, but address derivation, txid validation, sweep
authorization, signing, job transitions, and payout correctness remain
server-side responsibilities.

The settlement page may be refreshed manually at first. WebSockets and browser
polling are not required for correctness and should not be introduced merely
to make the status page feel live.

## Required OpenETR component work

Safebox Web should not start route implementation until the OpenETR component
has stable typed operations for:

1. NSP address derivation from `npub`;
2. targeted receipt detection from `nsec` and txid;
3. selected matched-output reconstruction;
4. BIP-352 sending from the selected sweep input to another `sp1...` address;
5. deterministic fee and transaction summaries;
6. signed transaction construction without implicit broadcast;
7. explicit broadcast with idempotent txid handling; and
8. service-worker receipt detection for the resulting txid.

The existing `create_silent_payment_sweep_result` is a useful foundation for
source receipt detection and spending but does not yet satisfy item 4.

## Testing strategy

### OpenETR component tests

- public `npub` and private `nsec` derivations produce the same NSP address;
- known BIP-352 vectors and real wallet-produced transactions are detected;
- one-byte or one-character changes to keys, txids, and addresses fail safely;
- a matched output can be spent to a service NSP destination;
- the service key detects the exact resulting output and value;
- miner fees, vsize, dust, and output indexes are deterministic;
- mainnet and testnet material can never be mixed; and
- no test exposes either scan key in output or logs.

### Safebox Web unit tests

- address display requires no private derivation;
- txid validation requires an authenticated attached Acorn;
- malformed txids, unmatched transactions, ambiguous vouts, spent outputs, and
  insufficient confirmations are rejected;
- quotes bind the user, outpoint, service descriptor, fees, mint, and expiry;
- CSRF and explicit confirmation are required before signing;
- private keys never enter database rows, rendered pages, or captured logs;
- broadcast-indeterminate paths do not create a second sweep; and
- duplicate callback or refresh behavior cannot create a second payout.

### Live regtest or signet tests

The preferred integration harness uses Bitcoin Core regtest first, then signet:

1. create disposable user and service Nostr keys;
2. derive both NSP addresses;
3. send a real BIP-352 test payment to the user;
4. submit its txid and detect the receipt;
5. quote and sweep it to the service NSP address;
6. mine the configured confirmations;
7. detect the service output;
8. deliver disposable ecash to the user Acorn;
9. verify exact gross, miner fee, service fee, and net amount; and
10. replay every request to prove idempotency.

Mainnet testing should begin only with explicit low-value limits and after the
regtest/signet state machine, recovery, and reconciliation paths pass.

## Phased implementation

1. **OpenETR forward primitive:** implement and test NSP-to-NSP transaction
   construction in the reusable component.
2. **Address-only Safebox surface:** display the attached Acorn NSP address and
   network information without scanning.
3. **Targeted receipt verification:** accept a txid and show matched confirmed
   outputs without spending them.
4. **Persistent worker descriptor:** expose the stable service `npub`, derived
   NSP address, network, and rotation metadata.
5. **Quote and liquidity reservation:** implement durable fee quotes and reserve
   ecash capacity before presenting an actionable sweep.
6. **Explicit sweep:** add user confirmation, construction, broadcast, and
   indeterminate-broadcast handling.
7. **Worker settlement:** detect the provider output, wait for confirmations,
   and deliver ecash exactly once.
8. **Treasury separation:** sweep hot service NSP outputs to fresh addresses
   from a watch-only descriptor while keeping treasury spending keys offline.
9. **Operational hardening:** add reconciliation, backup, rotation, reorg,
   liquidity, limits, monitoring, and legal/compliance procedures.
10. **Later discovery:** consider NIP-17 txid hints and bounded historical scans
   after the targeted path is reliable.

## Decision summary

The NSP address for an attached Acorn is publicly derivable from its `npub`,
but receipt detection and spending require private material derived from its
`nsec`. Safebox Web may use that private material only inside the existing
request-scoped trusted execution boundary. It must never delegate the user's
root-equivalent scan key to the service worker.

The user explicitly signs a Bitcoin sweep whose destination output is created
for the persistent worker's NSP address. The worker detects and confirms its
own output using its persistent key and the txid hint. It can then move the hot
receipt to a fresh address derived from a confidential watch-only treasury
descriptor while keeping treasury spending keys offline. The worker delivers
the quoted net value as ecash independently of that internal custody movement.
Durable job state, liquidity reservation, explicit fee accounting,
confirmation policy, and idempotent delivery are required because this is a
provider settlement service, not merely an address-display feature.
