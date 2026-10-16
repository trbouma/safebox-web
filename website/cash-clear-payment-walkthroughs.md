# Cash Payment Walkthroughs with Open Mint Acceptance

Status: design review draft, 2026-10-16. No payment authorization implementation
is introduced by this note. The proposed protocol must pass the conceptual
reviews and decision gates below before coding begins.

This companion to [NFC Payment Authorization Design](nfc-payment-authorization-design.md)
describes what each participant knows, what the customer approves, when value
moves, and how a payment can recover without being executed again. NWC is out
of scope. NFC and QR acquire credentials; they are not payment instructions.

## Scope and decision status

**Established direction:** open admission of signed merchant requests, optional
instance restrictions, explicit customer approval, no general wallet session for
payment, sat-denominated Cash with open mint acceptance, and no silent change of
approved terms. The issuer is the
customer's Safebox Web instance, not necessarily the mint.

**Current focus:** a merchant requesting net sats into its Safebox Acorn wallet,
whether delivered as Cashu ecash or through Lightning. Previously unseen mints
are admitted by default. Clear credits are deferred; their examples below are
future design material, not initial implementation requirements.

**Proposed first experiment:** customer-device approval, memory-only wallet
credentials, controlled service instances and bounded sat-denominated test value.
The first adapter to implement remains to be selected after reviewing both Cash
paths. Merchant-device approval remains a
separate, higher-trust profile; phone-free checkout is not promised by this scope.

**Still to agree:** wire schemas, merchant execution grants, quantitative limits,
credential recovery policy, settlement evidence, and operational retention.
Requirements below describe the proposed design, not existing guarantees.

## Cash is the asset and settlement is the route

| Payment method | What the merchant requests | What completes settlement |
| --- | --- | --- |
| Cash ecash transfer | Net integer sats into the merchant Acorn; new mints accepted by default | Recipient verifies, redeems and persists the required net spendable sats |
| Cash Lightning payment | The same net sats, using a resolved invoice and receiving-wallet funding path | Payer payment outcome verified and merchant Acorn funding finalized for the required net sats |
| Clear transfer, deferred | Specific non-Cash unit and mint | Recipient accepts the required net spendable credits |

The customer approves the merchant, net sat amount and maximum total debit, without
having to choose ecash versus Lightning. The issuer selects a supported route
before approval and binds its resolved details into the intent. Technical details
can be disclosed without forcing the customer to configure routing. Changing the
bound plan after approval requires renewed approval in this initial design.
An uncertain submission never permits automatic fallback to another route.

### Open mint acceptance policy

Here, “sat-backed Cash” means sat-denominated ecash claims or value transferred
through Lightning. The protocol does not establish independent proof of a mint's
reserves or guarantee future redemption. The merchant accepts mint counterparty
risk, including unfamiliar mints, under the open default.

No advance registration or mint allowlist is required. A previously unseen mint
is not rejected merely because it is new. The receiver still validates supported
protocol behavior, the sat unit, keysets, proofs, fees and actual redemption, then
verifies persistence of the resulting spendable wallet state. Arrival of a token,
a mint's claimed balance or a new mint URL is not settlement evidence.

New mint acceptance does not authorize unrestricted server network access.
Apply destination validation, DNS/redirect controls, resource limits and request
timeouts before contacting unknown URLs. Internal mints require explicit operator
routes; never infer a shared mint from matching deployment-local hostnames.
Network, protocol, fee or proof rejection must be distinguished from mint policy
rejection. Operators may later restrict mint admission, but the default is open.

Open merchant-instance admission and open mint acceptance are independent policies.
Neither grants spending authority. A successful redemption at an unfamiliar mint
does not demonstrate that mint's solvency or its ability to pay Lightning later.

## Issuer vault responsibilities

The vault is an authorization and execution boundary, not a public remote-control
API for an Acorn. It can decrypt a valid card envelope and temporarily obtain the
nsec and bootstrap relay, but it must only dispatch an approved payment operation.

| Information | Authoritative location | Handling rule |
| --- | --- | --- |
| Encrypted wallet credential | Card or equivalent QR | Sensitive reusable material; never put in URLs, logs, merchant receipts or durable intents |
| Credential decryption key | Issuer secret management | Separate purpose from service signing; versioned rotation and controlled access |
| Active card generation and PIN budget | Issuer policy database | Shared across login, NFC, QR and payments; unavailable state fails closed |
| Merchant request and order | Merchant service | Signed terms and durable order state; display name is not proof of identity |
| Approved terms and execution state | Issuer payment-intent store | Durable, immutable approval binding and atomic worker ownership |
| Wallet proofs and recovery records | Acorn's protected wallet state | Use Acorn safety and reconciliation methods; do not duplicate proofs in status records |
| Recipient acceptance evidence | Receiving wallet/service | Correlated to request, transfer, asset and net amount |

The merchant normally presents a request reference. The customer supplies the
card and PIN directly to a recognizable issuer origin. A merchant forwarding
encrypted card bytes is a different exposure profile, not a prerequisite.

### Credential lifetime and process loss

The proposed initial vault holds decrypted credentials only for the active
operation. Do not place them in job arguments stored in a database, exception
reports, task queues, traces or analytics. Minimize copies and lifetime, but do
not claim guaranteed memory erasure in a managed runtime.

Before submission, process loss requires fresh authentication and approval if
credentials are needed again. After possible submission, inspect existing
operation evidence first. Reauthentication resumes reconciliation of the same
intent; it does not grant permission for a second debit. Status remains available
without retaining the private key, subject to access controls.

Encrypted durable execution credentials are an unresolved alternative, not part
of the baseline. Encrypting an nsec and attaching an intent ID does not turn it
into a cryptographically restricted spending key. Unattended restart recovery
would require a separate threat model, key lifecycle and deletion policy.

### Compromise and recovery

Card revocation blocks future use at the issuer; it cannot revoke an exposed
wallet private key. An attacker with a card copy and its issuer decryption key
can recover that wallet key. Respond with affected-wallet assessment and migration
where necessary, not just card replacement. Plan routine key rotation separately
from emergency compromise: old decryption keys must not remain indefinitely
available merely to preserve card convenience.

Backups of revocation, PIN-attempt and consumed-intent state are security-critical.
Restoring an old snapshot must not silently resurrect cards or payment mandates.
Pause new execution until these states and unresolved payments are reconciled;
if freshness cannot be established, require invalidation/re-enrollment or another
explicit fail-closed recovery procedure. User wallet recovery and issuer service
recovery are separate responsibilities.

## Negotiation contract

Negotiation means finding an acceptable, executable payment option and freezing
its terms. It does not mean allowing the LLM, browser or merchant to select new
terms after customer approval. No LLM is required in this flow.

1. The merchant signs a versioned request with a strong random request ID,
   order reference, recipient, method, amount, asset, routing constraints and
   expiry. IDs are scoped to the merchant signing key; use at least 128 bits of
   randomness for new protocol IDs. An embedded legacy request ID is a correlation
   field, not the sole payment authorization identifier.
2. The issuer verifies the request and admission policy before obtaining secrets.
   Signatures bind the recipient; changing DNS/address resolution later must not
   change the approved destination. Whether recipient co-signing is needed remains
   a schema decision; a merchant signature alone is not proof it owns that wallet.
3. After credential authentication, inspect the live wallet and allowed routes.
   Freeze the selected mint, exact unit, relevant keyset constraints and fee plan.
   Cached balances are advisory, never a reason to claim settlement or conclusively
   reject funds that may have arrived since the cache was refreshed.
4. Render the exact payment and maximum debit. Bind approval to a canonical digest
   of resolved terms, not mutable form fields or a friendly name.
5. Revalidate before submission. A changed recipient, asset, method or increased
   maximum debit requires fresh approval. Lower actual fees within the approved
   ceiling are permitted. Stale quotes must not trigger silent substitution.

For ecash, define `A` as the merchant's required net receipt, `R` as receiver input
fees funded by the sender, and `S` as sender mint fees. The execution plan must
ensure net receipt at least `A` and debit no more than approved `Dmax`. In a simple
exact-fee example, exported value is `A + R` and debit is `A + R + S`.
Real proof selection and fee schedules must establish these values; arithmetic
alone is not a quote. A fee ceiling does not permit reducing the merchant's net
receipt. Any unavoidable excess receipt needs an explicit policy before approval.

For Lightning, distinguish maximum temporary balance reservation from final
debit after returned fees. Amount precision and rounding rules must be explicit;
never use floating-point money in the signed contract.

## Merchant coordination across issuers

An issuer can deduplicate its own intent but cannot prevent another issuer from
paying the same order. The proposed merchant coordinator atomically grants one
execution claim, bound to merchant request ID, issuer npub and intent digest.
After customer approval and before value movement, the issuer obtains and verifies
that signed grant. A failed claim causes no debit and releases no wallet data.

Retransmitting the same claim returns its existing state. Changed terms under the
same ID are rejected. Expiry limits starting new work; it does not prove an old
submission failed. Do not release an uncertain claim to another payer just because
a timer elapsed. Unsubmitted abandoned claims need an explicit cancellation and
reconciliation rule before reuse. This trades some availability for duplicate
payment safety and requires abuse controls against orders being held hostage.

This coordination is not global exactly-once settlement. A stale worker, malicious
sender or unsolicited token can still deliver value. The receiver must identify
late/duplicate payments, record actual funds received, fulfill an order at most
once, and route discrepancies to review. It must not silently discard value or
automatically refund to an unverified address.

## Primary walkthrough Merchant requests 100 sats

The merchant's signed order says: **credit my Acorn with net 100 sats**. It binds
the merchant instance, recipient wallet, order ID, expiry and supported receiving
routes. Open mint acceptance is the default. It does not require the payer to
already hold proofs at a mint the merchant has used before.

### Request and route preparation

1. The merchant creates the order and provides its authenticated inbox route and,
   if supported, a way to obtain a Lightning invoice tied to this order's wallet
   funding operation. The service must be authorized to receive/finalize for that
   merchant wallet; a public address alone does not provide its private key.
2. The customer opens the request at their recognized issuer, presents their
   credential and authenticates. The issuer validates request signatures, expiry,
   policy and recipient binding, without creating a general login session.
3. The issuer inspects spendable sat balances and supported routes. A mint new to
   the merchant remains eligible. The chosen route must meet protocol, network,
   amount and fee constraints. An aggregate wallet balance does not guarantee an
   executable payment if suitable proofs/keysets cannot fund the selected route.
4. Prepare the exact plan before approval. Route priority—such as preferring direct
   ecash when executable—is still an open design choice, not a hidden policy.

### Branch A Direct Cashu ecash from a new mint

Assume the payer holds sats at mint M, which the merchant has never used. Suppose
the actual proof plan needs 1 sat of receiving input fees and 1 sat of sender fees.
These are illustrative values, not a mint quote.

The issuer prepares 101 sats of delivered proofs so the merchant can redeem net
100. The planned customer debit is 102 sats. The authoritative review page shows:

| Approval field | Example |
| --- | --- |
| Pay to | Identified merchant and bound receiving wallet |
| Merchant receives | 100 sats net |
| Payment fees | Up to 2 sats |
| Maximum total debit | 102 sats |
| Payment details | Direct Cashu ecash via mint M; resolved request expiry |

5. The customer enters the PIN and explicitly approves those terms. An insufficient
   balance cannot be fixed by reducing the merchant's net receipt to 99 sats.
6. The issuer obtains the proposed merchant execution grant, rechecks generation,
   expiry and fees, serializes wallet mutation and persists the operation identity
   before executing. Changes outside the approved plan require renewed approval.
7. Acorn exports and privately delivers the proofs to the merchant's advertised
   inbox. Both interfaces show **sent; awaiting wallet acceptance**, not paid.
8. The receiving Acorn validates M's protocol, sat unit and proofs, redeems them,
   and verifies net 100 sats in persisted spendable wallet state. The proposed
   service automatically attempts acceptance under its merchant-wallet authority;
   open mint policy alone is not that authority. No manual mint enrollment is needed.
9. The merchant marks its order settled exactly once and issues the correlated
   result. The payer records actual fees and debit. The receipt identifies the
   mint so the merchant can account for the newly accepted mint exposure.

If M is unavailable, malicious or refuses redemption, the merchant has not received
the required spendable sats. Preserve the pending/uncertain outcome and recovery
evidence. Do not mark paid merely because M was accepted by policy, and do not send
a second Lightning payment automatically.

### Branch B Lightning funding into the merchant Acorn

This branch is selected during preparation, not as a retry after uncertain ecash
delivery. The merchant's receiving service obtains an invoice tied to a funding
operation for its Acorn. Resolve and bind the invoice, payment hash, amount,
expiry, receiving operation and intended net wallet credit before approval.

The invoice amount may need to exceed 100 sats if the receiving path deducts fees.
For example, if that path has a known 1-sat deduction, an invoice for 101 sats
would be required to deliver net 100. Do not assume this fee exists or that a
one-sat increment is sufficient: derive the amount from the selected adapter's
actual rules. If the path cannot establish the required net credit and bounded
cost, it is not eligible for this checkout contract.

The customer sees merchant receives 100 sats, quoted fees, maximum final debit,
and any larger temporary balance reservation. After approval and order claim,
the payer Acorn submits through its existing payment machinery once.

There are then two distinct confirmations:

1. **Lightning paid:** the payer verifies the payment outcome and the receiving
   service verifies its invoice was paid. A timeout is reconciled against the
   same operation; invoice replacement is not a retry strategy.
2. **Merchant Acorn credited:** the receiving service completes minting/delivery
   and acceptance as applicable, then verifies net 100 spendable sats persisted
   in the merchant wallet. Only this completes the wallet-credit checkout contract.

A paid invoice with delayed wallet finalization must display **payment received;
wallet finalization pending**. Do not ask the customer to pay again. An externally
settled invoice is not undone because final wallet credit is still unavailable.
Reconciliation must track both stages independently and retain their evidence.

### Same instance and different instances

On one instance, keep customer and merchant wallet identities and authorization
separate. Across instances, verify both signing identities, use the recipient's
advertised relay routes, and correlate settlement to the same order and intent.
The merchant verifies its own wallet receipt rather than trusting the payer's
success screen. Unknown merchant admission and new mint acceptance remain separate
from signatures, PIN checks and transaction approval.

### Questions for the conceptual review

- Is net 100 sats in the merchant wallet always the checkout completion condition,
  even when Lightning payment is already final? This is the proposed contract here.
- Which service context supplies merchant-wallet authority for automatic acceptance
  and how does it recover after restart without assuming database-held nsecs?
- Should direct ecash be preferred, or should route selection compare fees and
  availability? What information may the two instances exchange before approval?
- What happens if receiving fees change after preparation, causing underpayment?
  Record the actual amount and review; automatic top-ups are not authorized.
- How long should checkout wait before showing a pending receipt, and what can the
  merchant safely fulfill while finalization is pending? Waiting limits are not
  evidence of failure and must not authorize another debit.

## Deferred walkthrough Same instance Clear purchase

Illustrative purchase: merchant requests **10 Crays Credits** at an exact configured
mint and unit. Assume `R = 1`, `S = 1` and approved `Dmax = 12` credits. These are
example fees only.

1. Merchant creates an order and signed request for net 10 credits.
2. Customer opens the issuer's review page and reads their card locally. Even on
   the same service, merchant and customer roles remain separate.
3. Issuer authenticates the credential, checks the wallet's compatible balance,
   and prepares a plan exporting 11 credits for a maximum debit of 12.
4. Customer sees merchant/recipient, exact credit identity, net 10, fees up to 2,
   maximum debit 12, and expiry. PIN plus explicit approval authorizes that digest.
5. Issuer claims the merchant order, rechecks revocation and terms, serializes
   wallet mutation, and durably records the execution/recovery identity before
   submitting through Acorn.
6. Private transfer delivery changes the displayed state to awaiting acceptance.
   It is not a successful checkout yet.
7. Recipient accepts the proofs and verifies net 10 spendable credits. Merchant
   records settlement and fulfills the order once. Issuer records the correlated
   confirmation and actual debit. Duplicate notifications only refresh status.

Review question: if the customer balance is only 11, the plan must fail before
approval or offer a newly calculated valid plan. It must not send a net-9 payment.

## Deferred walkthrough Cross instance Clear purchase

Customer issuer A and merchant B have different instance keys. B is initially
unknown to A. Open mode accepts B's valid request for review, labeled unrecognized.
Restricted mode rejects it unless permitted; neither mode bypasses approval.

1. B advertises its exact Clear asset and recipient inbox route.
2. A verifies terms and checks mint access from the executor and recipient's
   declared acceptance capability. A browser need not reach the mint itself.
3. A authenticates the customer and prepares the quote. Customer approves at A.
4. B grants the order claim to A's intent. A delivers to B's declared inbox, not
   A's default relay, and waits for B's authenticated acceptance evidence.
5. B accepts at the issuing mint and signs a result correlated to the request and
   delivery. A validates the expected B identity and terms. B's statement remains
   an assertion by B; the merchant's own acceptance record is its settlement basis.

If B's mint is `http://clear:3339`, A must not assume its own hostname `clear`
refers to the same mint. Internal names are deployment-local. Cross-instance use
requires explicit operator configuration that establishes the intended mint and
routes; automatic rewriting to a guessed public hostname is prohibited. Unknown
private targets must not become server-side network probes. If either necessary
executor cannot access the intended mint, decline before value movement.

## Failure and recovery walkthroughs

| Interruption | Required result | Prohibited shortcut |
| --- | --- | --- |
| Customer leaves before approval | No authorized spend; review can expire | Treating a tap or PIN alone as consent |
| Customer leaves after approval | Existing worker may finish; authenticated status can be revisited | Depending on an open browser to execute |
| Double click or repeated request | Return same intent/result | Creating another debit |
| Two issuers approve same order | Only one merchant execution claim succeeds | Assuming local uniqueness is global |
| Card revoked before submission | Recheck and stop unsubmitted payment | Treating an old approval as indefinite authority |
| Revocation after submission | Reconcile existing operation | Promising cancellation of in-flight value |
| Worker dies before known submission | Establish absence of side effects; require credentials/approval if needed | Restarting blindly from an ambiguous checkpoint |
| Worker dies after possible submission | Reconcile existing operation, requesting customer authentication if necessary | Creating replacement token or melt |
| Relay acknowledges but recipient unavailable | Await acceptance; preserve recovery evidence | Reporting settled or reclaiming on timeout alone |
| Recipient accepts but callback is lost | Query durable correlated result | Sending value again |
| Receiver gets less than requested | Record actual receipt and review | Automatic top-up or marking order paid |
| Payment arrives after expiry/cancellation | Record late value and review order outcome | Discarding value or fulfilling twice |
| Policy or replay database unavailable | Fail closed for new spending | Treating missing state as unused/allowed |
| Issuer database restored from old backup | Quarantine execution and reconcile security state | Re-enabling revoked cards or consumed intents |

Approval expiry, quote expiry, order-claim expiry, worker lease expiry and the
browser monitoring window are different clocks. None proves non-payment after
submission. Worker takeover must fence stale workers and consult Acorn recovery
state; a new owner token alone cannot undo an already issued external request.

## Status privacy and operator intervention

Customer status requires an authorized session or scoped status capability;
merchant status requires the appropriate merchant identity. Opaque IDs are not
authorization. Receipts expose approved terms and settlement references, not
wallet balances, card ciphertext, PINs, proofs or unnecessary customer identity.
Callbacks use approved destinations and bounded retries with backoff; consumers
can query durable status if notification fails.

Operators need a pause switch, stuck-intent inspection and reconciliation tooling.
Administrative closure must be distinct from settlement, failure or refund. It
must preserve the audit trail and never reset a possibly paid order for automatic
retry. Recovery artifacts stay protected in their existing wallet/security context.
Defer refunds, tips, recurring mandates and offline authorization to separate designs.

## Lessons carried forward from Safebox 2

The source documents in the sibling `safebox-2` repository are:

- `docs/specs/NWC-NFC-VAULT-EXTENSION.md`: direct vault validation, requester/service
  binding and durable nonces; do not carry forward NWC dispatch or fallback.
- `docs/specs/CARD-TOKENIZATION-AND-NFC-PAYMENT-STRATEGY.md`: revocable token lifecycle;
  distinguish its lookup-secret model from the new encrypted root-key credential.
- `docs/specs/PAYMENT-ERROR-HANDLING-AND-RESILIENCE.md`: wallet serialization,
  persistence verification and explicit uncertainty. Do not apply rollback
  indiscriminately when delivery may already have succeeded.
- `docs/specs/QR-AND-NFC-UNIFIED-FLOW-DESIGN.md` and
  `docs/HTMX-NFC-CONSIDERATIONS.md`: one server-owned flow after acquisition,
  preserving browser permission gestures and page initialization.
- `docs/specs/BACKUP-AND-RECOVERY-PLAN.md`,
  `docs/specs/INCIDENT-RESPONSE-AND-KEY-COMPROMISE-RUNBOOK.md` and
  `docs/specs/DATA-RETENTION-AND-DELETION-POLICY.md`: operational recovery,
  compromise response and minimal retention, extended here for replay-state rollback.

These are design precedents, not a claim that legacy implementation guarantees
apply unchanged to Safebox Web. Legacy NFC/QR revocation semantics also differ:
equivalent copies of the new card credential share its generation and revocation.

## Decisions required before implementation

| Decision | Proposed position | What remains to resolve |
| --- | --- | --- |
| Payment scope | Sat-denominated Cash; Clear deferred | First adapter after reviewing both Cash routes |
| Mint admission | Open, including new mints | Safe network access, optional restrictions and exposure reporting |
| Route selection | Issuer prepares route before approval | Priority, capabilities and fee comparison |
| Merchant wallet authority | Scoped receive/finalize context | Credential lifecycle, restart and offline acceptance |
| Approval device | Customer device at issuer | Confirm phone-free merchant-device flow is deferred |
| Credential persistence | Memory-only | Reauthentication UX and restart recovery boundaries |
| Merchant coordination | Signed atomic execution grant | Claim/cancel protocol, stale claims, abuse limits and fencing |
| Request/receipt encoding | Versioned canonical signed payloads | Field definitions, signature scheme, domain separation and test vectors |
| Recipient binding | Merchant signs exact recipient | Need for additional recipient proof and key rotation rules |
| Settlement evidence | Net spendable sats persisted in merchant Acorn | Correlating Lightning payment with subsequent wallet finalization |
| Fees | Net merchant amount plus explicit debit ceiling | Precision, rounding, reserves and excess-receipt policy |
| Expiry and limits | Separate clocks and bounded work | Numeric TTLs, amount/rate limits and skew tolerance |
| Key management | Separate purposes and versioned encryption keys | Routine rotation, compromise response and recovery drills |
| Retention | Minimal metadata, protected recovery evidence | Retention periods, status access and backup deletion |
| Internal routes | Operator-configured only | Stable mint identity across deployment-local names |

For each walkthrough, agree on the customer screen, exact signed terms,
authorization commit, first external side effect, settlement evidence, and next
safe action after interruption. Then derive protocol fixtures and negative tests.
Do not begin value-moving implementation until the applicable decisions above
have explicit answers. A reviewed design is not a claim of production security.
