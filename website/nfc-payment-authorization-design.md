# NFC Payment Authorization with Open Instance Trust

Status: design proposal for research and prototyping, 2026-10-16. Payment
authorization described here is not implemented. The existing
[NFC and QR credential flow](nfc-card-design.md) supports issuance, activation,
PIN-gated login and revocation.

The proposed model separates requesting money from authorizing its movement:
**merchant instances sign payment requests; the customer's Safebox issuer applies
its instance trust policy and obtains customer authorization for one specific payment.**
The merchant receives a verifiable payment result, not the customer's private
key or a general wallet session.

Interoperability is open by default: a previously unknown instance may submit a
valid signed request without prior registration, allowlisting or certification.
An operator can restrict admission to specific instance npubs and block individual
npubs in either mode. Admitting a request never grants permission to spend.

This does not require Nostr Wallet Connect (NWC) as an intermediary. Safebox Web
can invoke Acorn directly after validating the request and obtaining approval.
Removing NWC simplifies transport and delegation for this flow; it does not
remove the need for explicit authorization, durable execution tracking or
independent confirmation of settlement.

The companion [Cash payment walkthroughs](cash-clear-payment-walkthroughs.md)
defines proposed vault boundaries, example negotiations, failure transitions and
the decisions that must be resolved before implementation. Its numerical examples
are hypothetical, not live quotes. Design review must cover both documents.

[Card Record Retrieval Design](card-record-retrieval-design.md) applies the same
credential foundation to owner access and separately authorized record sharing.
Payment authorization, owner login and record disclosure have distinct purposes;
approval for one must never authorize either of the others.

## Scope and terminology

The current focus is **sat-denominated Cash payments into the merchant's Acorn**,
regardless of whether settlement uses direct Cashu ecash or Lightning. Clear credits
are deferred. Mint admission is open by default, including previously unseen mints;
optional restrictions are separate from merchant-instance trust policy. Open mint
acceptance does not bypass safe URL handling, protocol/proof validation, fee checks
or recipient finalization, and does not prove reserves or guarantee future redemption.

The merchant requests a net sat amount. The issuer chooses a supported settlement
plan before approval; the customer approves the recipient, amount and maximum debit
without needing to choose the rail. Resolved route details remain bound to that
approval. An uncertain attempt must be reconciled before any alternative payment.

The **customer's issuer** is the Safebox Web service that encrypted the card or
QR credential and maintains its active generation and PIN-attempt policy. It is
not necessarily the mint issuing the customer's ecash or Clear credits. Merchant
software and customer issuer may be different services or applications operated
by the same organization; their authority boundaries remain separate.

Each instance uses its own npub as its signing identity. The npub identifies
the key to which operators attach trust through relationships, experience and
local policy. A valid signature proves control of that key, not the operator's
legal identity, trustworthiness or the software currently running on its host.

Software certification is optional supporting evidence for a local trust
decision, not an admission requirement or a prerequisite for interoperability.
No certification authority, accreditation, compliance equivalence or approved
software list is established by this note. Remote software assessment cannot
be assumed merely because an instance presents an npub or a certificate.

The first prototype should authorize one exact payment to one merchant. It
should exclude recurring mandates, offline spending, refunds, tips added after
approval, preauthorizations and open-ended spending limits. NFC and QR are
alternative ways to present the same credential, not different spending rights.

## Roles and trust boundaries

| Participant | Responsibility | Authority it must not acquire |
| --- | --- | --- |
| Merchant instance | Sign a request with its instance key, display its status and verify the result. | Customer private key, PIN, reusable wallet session or permission to change approved terms. |
| Customer issuer | Apply instance trust policy, validate the request and credential, present authoritative terms, enforce PIN and lifecycle policy, and record approval. | Permission inferred merely from reading a card, recognizing an npub or seeing certification evidence. |
| Customer | Approve the identified recipient, amount, asset and fee limit through an explicit action. | A requirement to trust an unexplained success message on the merchant screen. |
| Acorn payment executor | Execute the approved intent within its limits and reconcile uncertain outcomes. | A fresh payment mandate inferred from retries, browser refreshes or timeouts. |
| Optional certification authority or registry | Supply verifiable assessment evidence associated with instance keys, validity and revocation status. | Mandatory gatekeeping in default open mode, customer spending authority or a guarantee about the device's current state. |

The issuer already handles the wallet key transiently inside its trusted
execution environment. This design retains that trust, described in
[Trust Boundary](trust-boundary.md). Application compromise can expose the
encrypted card's underlying root credential. Certification of the merchant
does not reduce that issuer responsibility.

## Cardholder trust and recognizable cues

The cardholder cannot ordinarily determine which software is running on a
merchant device or issuer host. This model must not require them to inspect
build signatures, compare npubs or assess software certification before paying.
Their decision necessarily depends in part on recognizable service identities,
established relationships and out-of-band context: evidence outside the payment
message itself.

### Human trust and protocol trust have different jobs

**Instance-to-instance trust** answers whether a request or receipt is signed by
the claimed key and whether local policy admits that instance for a particular
role. An npub provides a stable identity for those decisions. Neither a valid
signature nor an allowlist entry proves that the instance is honest or its
current software and host are uncompromised.

**Cardholder trust** concerns whom the customer believes they are dealing with
and whether the interaction matches their intended purchase. A familiar issuer
domain, a bookmark established during onboarding, an existing merchant
relationship, a known shop and staff, or a checkout matching the order can
support that judgment. These cues are useful even when the cardholder has no
knowledge of the signing keys or software implementation.

**Transaction authorization** is a separate decision: the customer approves the
specific recipient, amount, asset and fee limit shown by the issuer. Familiarity
with the merchant does not authorize an altered request. Likewise, an unfamiliar
but valid signing instance may be admitted in open mode without becoming trusted
or gaining permission to spend.

The issuer connects these layers by applying cryptographic and policy checks
and communicating their results in understandable terms. It must not turn a
machine-level result such as “signature valid” into an unsupported human claim
such as “safe merchant” or “verified software.”

### What out-of-band cues can and cannot establish

A familiar issuer domain helps the customer recognize the service they intended
to use. Prefer a previously established bookmark or entry point over an address
supplied only by the current merchant request. The actual browser origin matters:
a domain printed inside a page, a logo or a padlock illustration is merely page
content. HTTPS authenticates the connection to a domain, not the trustworthiness
of its operator or the safety of the software serving it. A lookalike domain can
also use HTTPS.

Physical presence supplies context, not runtime verification. A known storefront,
staff member, checkout location, order or receipt may corroborate the intended
merchant and purchase. None proves that the device is uncompromised. A replaced
QR sticker, tampered device or misleading signage can exploit an otherwise
familiar setting. Several cues copied from the same attacker-controlled page
are not independent corroboration.

On a merchant-controlled device, even an apparent issuer page and address bar
depend on the integrity of that device. A separate issuer origin cannot guarantee
that the displayed amount is genuine or that an entered PIN is private. This
is why customer-device approval remains the preferred profile, not why it is
risk-free. The customer's device and issuer still form part of the trusted
execution environment.

### Requirements for the approval experience

The proposed approval interface should:

- Keep the customer on a recognizable issuer origin before reading a credential
  or entering a PIN. Explain unexpected changes of issuer; never infer consent
  to send secrets from a merchant-supplied domain or branding.
- Show the merchant, order reference, resolved recipient, amount, unit/mint,
  fees and maximum debit together so the customer can compare them with the
  intended purchase and physical checkout.
- Distinguish **unrecognized signing instance**, **recognized by this issuer**
  and any independently supported merchant identity claim. Recognition means
  a local relationship or policy decision, not proof of safe software. Offer
  npub and evidence details for inspection without making them a prerequisite
  for ordinary use.
- Label merchant-supplied names, logos and descriptions as claims where they
  have not been independently established. Optional certification evidence
  must identify its scope and source rather than appear as an unconditional
  safety badge.
- Provide a clear cancel path before authorization. If the domain, merchant or
  payment terms are unexpected, the customer can stop and confirm through an
  established contact or their own issuer entry point, rather than a contact
  supplied only by the suspicious request.

These requirements communicate trust rather than manufacture it. Out-of-band
cues and local npub policy remain complementary; neither may bypass PIN limits,
credential lifecycle checks, immutable payment terms or duplicate-payment
protection. Where those assumptions fail, the system must not claim that a valid
signature or customer click guarantees informed consent on an honest display.

## Open admission and optional instance restrictions

The proposed default is **open mode**. The issuer verifies request integrity and
the signing instance npub, applies blocks and safety checks, then admits supported
requests for customer review. A valid signature from an unfamiliar npub is shown
as **unrecognized**, not rejected solely for being unfamiliar and not labelled
trusted or certified. Display names supplied by that instance remain unverified
unless the issuer has separate evidence for them.

| Policy | Request admission | Customer authorization |
| --- | --- | --- |
| Open mode, default | Any valid signing instance may submit a supported request unless blocked. No allowlist or certificate is required. | Explicit approval of bound terms, PIN, active credential and all payment safeguards remain mandatory. |
| Restricted mode | The signing instance npub must have an enabled, unexpired local grant for the merchant-request role. An empty allowlist admits none. | The same mandatory approval and safeguards apply; an allowlist entry is not a spending mandate. |
| Blocklist, both modes | An explicitly blocked npub is rejected even if allowlisted or accompanied by valid certification evidence. | No new authorization for that request. |

Trust is directional, role-scoped and non-transitive. An issuer trusting a
merchant to request payment does not imply that the merchant trusts that issuer's
receipts, that either trusts the other's peers, or that either may act as a
certification authority. Merchant instances can independently apply open or
restricted policy to issuer receipts. Receipt admission still does not establish
that funds settled.

A local trust entry should record the instance npub, allowed role, enabled state,
optional expiry, asset/payment-method limits and the operator's reason or evidence.
Bind approved endpoints to the identity where cross-service communication is
needed; a changed hostname must not silently become a new trusted identity.
Normalize npubs to their validated public keys for comparison, not display names.
Certification references may enrich an entry but are not required to create one.

Admission order should be deterministic: validate the signed request, reject
blocked keys, enforce the selected mode and any applicable local limits, then
proceed to customer review. Always enforce signature, purpose, expiry, replay,
asset/route, fee and request-size checks. Rate-limit unknown as well as recognized
instances. Newly generated keys can evade a per-npub block, so open deployments
also need source/global abuse controls; operators can switch to restricted mode
when stronger admission control is needed.

Recheck the current instance policy at authorization commit and before submission.
Blocking a key, removing a restricted-mode grant, or changing to restricted mode
must affect requests that have not yet been authorized; it must not retroactively
claim to undo settled payments. Define handling of authorized but unsubmitted
intents consistently with card revocation policy below. Preserve enough policy
version information to explain the decision without making it a reusable grant.

Key rotation creates a new signing identity. Do not transfer restricted grants
automatically: require an operator-approved transition with evidence of continuity,
and disable a compromised predecessor. In open mode a new key may submit requests
as an unrecognized identity, but it does not inherit reputation, permissions or
an exemption from controls. An instance signature alone cannot establish that a
claimed replacement key is a trustworthy successor.

## What the existing card provides

The current credential carries the application-encrypted wallet private key,
bootstrap relay, PIN, generation ID, issuance time and expiry, with an
authenticated version, purpose, issuer and key identifier. This is sufficient
to recover the wallet identity and check whether the credential is current.
It contains no merchant, amount, asset, fee limit or payment intent.

Consequently, **card possession and PIN knowledge authenticate a credential;
they do not identify which payment the customer approved**. A payment-specific
authorization must bind that credential to independently verified transaction
terms. The existing login POST and its browser challenge must not double as a
payment authorization endpoint.

For an initial experiment, the issuer could accept the existing card as an
authentication input to a separate payment flow. That would be an explicit
policy decision to reuse a root bootstrap credential, not a claim that its
current login-purpose envelope already grants payment permission. Before
production, decide whether payment use requires a new purpose/version or a
separate limited credential. A login-capable card or QR copied together with
its PIN remains usable for login, even if a particular payment page creates no
wallet session.

## Choosing NTAG215 and managing its risks

### Cost and reader compatibility

NTAG215 is the baseline for this prototype because the design needs an inexpensive,
replaceable carrier for a compact encrypted credential, not a card that executes
wallet cryptography. NXP specifies NTAG215 as an NFC Forum Type 2 tag with 504
bytes of user memory; the existing [credential sizing analysis](nfc-card-design.md#measured-byte-budget)
uses a conservative 496-byte NDEF capacity target. The current bounded payload
fits that target. See the [NXP NTAG213/215/216 data sheet](https://www.nxp.com/docs/en/data-sheet/NTAG213_215_216.pdf).

The economic objective is low-cost issuance and replacement using ordinary
compatible NFC devices, avoiding a dedicated payment-terminal requirement.
Compare total deployed cost, including cards, provisioning, reader/software
integration and support; exact prices depend on suppliers and volume. This is
a selection rationale, not a claim that every secure-card alternative costs
more or that every phone/browser can perform Web NFC reads and writes.

Cryptographic cards may require additional key provisioning, authentication
protocols and reader/application integration. They do not universally require
specialized reader hardware. For example, NXP's NTAG424 DNA supports authenticated
dynamic messages through standard NDEF reads; its mobile use illustrates why
secure-card capabilities and reader compatibility must be evaluated separately.
See [NXP NTAG424 DNA](https://www.nxp.com/products/NTAG424DNA).

### What hygiene mitigates and what hardware can add

The NTAG215 payload in this design is static and copyable. Encryption conceals
its contents but does not prevent copying the ciphertext. Keeping the PIN
separate makes a stolen card or copied QR less useful to an attacker who does
not know the PIN. The application still needs persistent attempt limits,
single-use challenges, transaction-bound approval and current revocation checks.

“Do not write the PIN on the card” means do not print, label or store it there
in plaintext. The current credential intentionally includes the PIN inside its
issuer-encrypted payload. Do not photograph the PIN beside the card or login QR,
keep it in the same sleeve, or disclose it to merchant software. The PIN is not
an independent encryption layer: compromise of the issuer's encryption key can
expose both the wallet key and the embedded PIN.

Distinguish replay of an already-used authorization from reuse of a copied
credential. Single-use challenges and durable payment identities can reject
duplicate authorization submissions. They do not stop someone holding a clone
and the correct PIN from requesting a fresh authorization. PIN hygiene reduces
that risk; it does not eliminate replay, cloning or capture on a compromised
PIN-entry device.

A cryptographic card can add device-held secrets and dynamic authentication
that make copying stored bytes insufficient to impersonate the original card,
when the issuer validates that mechanism and its freshness. It cannot by itself
guarantee an honest terminal display, an uncompromised issuer or informed customer
approval. Merely placing the existing static credential on a more expensive card,
without using its cryptographic functions, does not confer those protections.
Likewise, retaining a static QR fallback with equivalent authority would retain
a copyable route around a newly introduced hardware-possession requirement.

The design choice is therefore to accept a defined static-credential risk in
exchange for simpler deployment and replacement—not to claim that secure cards
offer no security advantage. Reassess the choice if the required assurance level
demands resistance to cloned credentials or card-bound authorization.

### Revocation and replacement

The cardholder can revoke a lost or suspect generation through an independently
authenticated issuer session and issue a replacement without possessing the old
card. The database change takes effect for subsequent online checks once committed;
all NFC and QR copies of that generation are invalidated together. Normal
replacement activation supersedes the old generation, but preparing or writing
a new card alone does not revoke it. For loss or suspected compromise, revoke
first rather than waiting for successful replacement issuance.

This control depends on reaching the issuer and authenticating through recovery
material or a retained session. It is not an offline kill switch, does not erase
copies, and does not terminate existing sessions or reverse submitted payments.
Use separate incident procedures for those cases. If the underlying wallet
private key has been exposed, rotating a card generation is insufficient; wallet
key migration and protection of the affected funds and records are separate work.
Revocation and replacement are architectural benefits, not advantages exclusive
to inexpensive tags—cryptographic-card systems can implement them too.

### Recovery responsibility

The cardholder is ultimately responsible for maintaining and testing independent
recovery access. Before relying on the card, preserve the Acorn recovery mnemonic
or private key and bootstrap relay securely, together with any separate protected-
record recovery material needed for those records. Keep these apart from the
card and PIN, and verify that recovery works before treating the card as the
normal access method.

The card, a QR copy and its PIN are convenience credentials, not a replacement
for recovery material. If all usable sessions and independent recovery access
are lost, this design promises no operator reset or guaranteed wallet recovery.
The PIN alone cannot reconstruct the wallet. Issuer unavailability or loss of
the application encryption key can also make the card unusable; independent
Acorn recovery must not depend on decrypting that card.

The issuer must explain this responsibility during issuance and offer a clear
recovery check and loss-response procedure. Cardholder responsibility does not
remove the operator's obligations to protect the application secret, preserve
revocation state, enforce authorization policy and communicate service limitations.

## The merchant payment request

The merchant should authenticate a canonical, versioned request with its instance
signing key. The issuer must verify the signature against the claimed npub and
apply its current admission policy. Open mode requires no prior registration.
A certification label or familiar merchant name cannot substitute for signature
verification or override a local block.

The request should include:

- A protocol version and payment-request purpose, the merchant instance npub,
  any separately supported merchant identity claims, and a globally unambiguous
  request ID scoped to that signing instance.
- Creation and expiry times, a fresh nonce, the intended issuer/audience where
  applicable. Certification references may be supplied as optional evidence;
  omitting them does not invalidate an open-mode request.
- The amount as an integer with an explicit unit and precision. For Clear,
  include the exact mint and CMU or required keyset constraints; a friendly
  currency name is not an asset identifier.
- The recipient and settlement method, including a concrete destination or
  constraints for resolving it, and a merchant order reference.
- A fee policy stating the maximum customer debit and whether any permitted
  fees are added to or included in the displayed amount.

The wire schema, canonical encoding and signature scheme remain to be specified
with test vectors. Unknown critical fields or unsupported versions must fail
closed; the issuer must not guess a payment's meaning from display text.

If a Lightning address or other identifier resolves to a dynamic invoice, the
issuer must resolve and validate that invoice before approval and bind the
result to the authorization. The same applies to mint selection and fee quotes.
The resulting **payment intent** is the issuer's immutable record of the exact
request, resolved destination and approved cost constraints. A changed recipient,
amount, unit, mint or fee ceiling requires fresh approval.

An internal or local-only mint is not made accessible by a signed request.
Check route reachability and asset compatibility before presenting an approval
screen. Never substitute another mint, asset or destination silently.

## Customer approval flow

1. Merchant software creates the signed request and presents a request reference
   to the customer. It does not request a wallet session.
2. The customer opens the issuer's payment flow through a recognizable entry
   point and checks that the context matches the intended merchant and purchase.
   The issuer verifies the signing
   npub, request authenticity, local admission policy, expiry and supported
   settlement route, then prepares the immutable intent. Unknown but valid
   instances proceed in open mode; certification is not a required lookup.
3. The customer presents the NFC card or its QR copy to the issuer. The issuer
   authenticates the envelope, derives the wallet identity and checks expiry,
   active generation and persistent attempt limits.
4. The issuer renders the signing instance and its local recognition status,
   recipient, amount, unit/mint, permitted fees and maximum debit. Self-asserted
   merchant names are distinguished from independently verified identity claims;
   the customer can compare the displayed terms with the order and checkout.
5. The customer enters the PIN separately and explicitly approves the displayed
   intent. The browser submits a CSRF-protected POST with a short-lived,
   browser-bound, single-use **payment-purpose challenge**.
6. The issuer atomically checks the intent version/digest, credential generation,
   PIN budget, current instance trust policy, request expiry and unconsumed
   challenge, and records authorization for that payment only. It issues no
   general wallet session.
7. Acorn executes the authorized payment. Customer and merchant obtain the
   result through the issuer's status and receipt interface.

Keep the authoritative workflow in server-rendered pages and forms. JavaScript
should acquire NFC/QR data and, if useful, refresh status; it must not compare
PINs, determine payment validity or own the payment state machine. A browser GET
may observe status but must never authorize or initiate spending.

Both authentication and approval must be checked again at the authorization
commit point. If revocation commits first, authorization must fail. Once an
intent is authorized and submitted, later card revocation cannot promise to
cancel a payment already in flight. Whether an authorized but unsubmitted
intent remains executable after revocation must be an explicit issuer policy;
the initial prototype should recheck revocation before submission.

## Customer device and merchant device profiles

### Preferred profile with customer device approval

The merchant presents the payment request; the customer scans it, reads their
card or QR locally, and enters the PIN on their own device at the trusted issuer.
The merchant never receives the credential or PIN. The issuer's confirmation
screen, rather than the merchant's screen, is the authority for what is approved.

This profile can support ordinary merchant devices running interoperable software
without treating those devices as trusted PIN-entry terminals. It still depends
on the customer's device, browser and issuer remaining trustworthy.

### Higher trust profile with merchant device approval

A merchant device can read the encrypted credential and present an issuer-hosted
approval page. A separate issuer origin provides a web-origin boundary, but it
does not protect against a compromised operating system, deceptive browser or
PIN-capturing device. The merchant may also be able to retain a copy of the
encrypted credential it reads.

If the customer enters the PIN on that device, the security profile must say so
explicitly. Capturing both credential and PIN can enable later login or payment
attempts until revocation or expiry. Software certification alone cannot turn
an arbitrary host into a trusted display or PIN-entry device.

Do not include this profile in the first experiment. Investigate deployment
attestation, device controls, transaction limits and customer-device confirmation
as separate assurance measures. Any future claim of terminal-equivalent
assurance needs its own assessment; it does not follow from this architecture.

## Issuer discovery and request transport

The routing issuer in a scanned envelope is a hint until authenticated. Never
post a credential or PIN to an arbitrary URL obtained from a card, QR, merchant
request or redirect. The customer should select a known issuer, or discovery
must resolve through a configured trust registry and approved HTTPS origins.
The receiving issuer must still authenticate the envelope and enforce audience
binding. Open admission of signed merchant requests does not mean open forwarding
of customer secrets to an arbitrary issuer. A discovery registry, if used, is
not a mandatory merchant-software certification registry.

Public payment-request references may be URLs or opaque IDs. Customer credentials
must travel only in protected request bodies, not query strings, redirect URLs,
analytics events or access logs. Cross-service request retrieval and receipt
callbacks need explicit destination policy, redirect restrictions and protection
against server-side requests to unintended internal services. Internal payment
routes should be deliberately configured, not authorized by arbitrary request
fields.

## Durable execution and uncertain outcomes

Use one durable payment intent per merchant request and customer authorization,
with a uniqueness constraint preventing duplicate execution within an issuer,
including by concurrent customers or workers. This does not prevent a different
issuer from paying the same request. The proposed cross-instance flow therefore
requires a merchant-owned, atomically claimed order and a signed execution grant
bound to one issuer and intent. Grant expiry cannot make an already submitted
payment safe to retry; uncertain orders remain unavailable for a new execution
until reconciled. Unsolicited or late payments still require explicit handling.
See the companion walkthroughs for this proposed coordination contract. Bind execution
and approval records to the canonical intent digest. A retry with the same ID
but different terms must fail rather than overwrite the original.

The proposed progression is:

`REQUESTED → REVIEWED → AUTHORIZED → SUBMITTING → AWAITING_SETTLEMENT → SETTLED`

`AWAITING_SETTLEMENT` means submission or delivery is known, but recipient
acceptance is not yet confirmed. `RECONCILING` means the outcome itself is
uncertain. Neither state permits automatic replacement payment.

Before submission an intent may instead expire, be declined or be cancelled.
After submission, an ambiguous response enters `RECONCILING`, not a retryable
failure. `FAILED` means there is evidence that the operation did not settle;
the payment adapter must define what evidence is sufficient.

Persist submission/recovery information before an external side effect. If a
process crashes after submission but before recording the result, reconcile
using the existing operation identity, quote or protocol evidence. Never issue
a second payment merely because the browser timed out or the worker restarted.
Local idempotency does not guarantee exactly-once execution at a remote service;
each settlement method needs an explicit recovery strategy. An unresolved
outcome remains pending or requires operator review.

Durable records should contain operational identifiers, approved terms, state
and bounded evidence references—not private keys, PINs, Cashu proofs or reusable
card ciphertext. The initial prototype can hold wallet credentials in memory
for execution, but must then handle process loss honestly: reconcile existing
submission first and require renewed authorization when credentials are needed
again. Restartable execution is not permission to persist a general wallet
credential. A narrowly scoped recovery capability is a separate design task.

Payment PIN attempts must share the credential's existing persistent budget
across login, NFC, QR and payment endpoints. Otherwise introducing payment
creates another guessing allowance. Add request/source throttling and bounded
parsing, and fail closed when authorization or attempt state is unavailable.

## Receipts and settlement meaning

The merchant verifies a signed issuer receipt against the signing issuer npub
and its own receipt-admission policy, not a browser success screen. In open mode,
an unfamiliar issuer may submit a valid receipt but must not be presented as
trusted merely because its signature verifies. Restricted mode can require a
specific issuer npub and role grant; blocks take precedence in both modes.
Bind the receipt to the expected issuer for that payment and the merchant/request ID,
intent digest, recipient, amount, asset/mint, actual fees, settlement state and
time, plus an appropriate evidence reference. Do not disclose the customer's
private key, PIN, proofs or unnecessary wallet identity.

Distinguish request acceptance, authorization, submission, delivery and completed
settlement. For example, publishing a private ecash transfer to a relay is not
the same as the recipient accepting spendable value. The merchant acceptance
policy must define which state is sufficient for each payment method.

An issuer signature authenticates the issuer's statement; it is not independent
proof of settlement by itself. Where the payment method supports it, the
merchant should verify settlement against its own receipt or settlement service.
Signed notifications are hints to reconcile the durable status, and duplicate
notifications must not create duplicate orders or credits.

## Certification as optional supporting evidence

The protocol must function without software certification. Operators primarily
decide whom to trust by instance npub; certification can support that decision
but cannot prove the remote instance's current runtime or automatically confer
authority. Lack of a certificate is not a failure condition in default open mode.

A certification profile should identify the assessed software version/build,
merchant signing keys, deployment assumptions, permitted payment methods and
validity period. It should include update review, key rotation, vulnerability
handling, certificate revocation and an issuer-verifiable status mechanism.
Self-declared product names or version strings are insufficient.

Assessment should test request signatures and canonicalization, merchant and
recipient binding, accurate amount/fee presentation, tamper rejection, credential
non-retention, replay and duplicate-payment handling, receipt verification,
privacy-preserving logs, and behavior during outages or uncertain settlement.
Separate the software artifact's assessment from evidence about the deployed
instance. A signed build or certificate does not by itself prove what code is
currently executing on a merchant's device.

Issuers decide which optional certification sources and profiles they consider
credible. Expired, revoked or unavailable evidence must not be displayed as valid.
It does not automatically block an otherwise admissible open-mode request. If an
operator explicitly requires current certification for a particular local grant,
that additional policy must define freshness and fail-closed behavior and be
visible to the operator; it is not a protocol-wide default. Certification must
never override a block or replace customer approval.

Regulatory obligations, liability and any
claim of equivalence to existing payment-terminal certification remain separate
questions for the eventual scheme.

## Prototype plan and acceptance criteria

Start with a development merchant and customer issuer under the tester's control,
using synthetic identities and bounded sat-denominated test value. Keep the
customer-device profile. Review both direct ecash and Lightning funding into
the merchant Acorn before choosing the first adapter to implement. Clear is deferred.
Test acceptance of a previously unseen mint without an allowlist, while rejecting
unsafe destinations and invalid proofs. A paid receiving invoice must remain
awaiting wallet finalization until the required net sats are accepted and persisted.

1. Specify signed request, canonical intent, authorization challenge and receipt
   schemas with cross-implementation test vectors. Start in open mode and accept
   a valid request from a previously unknown development npub without any
   certificate. Verify that the UI accurately labels the instance unrecognized.
2. Build an issuer review and approval flow with no money movement. Demonstrate
   that a tap, login session, PIN submission alone or changed request cannot
   authorize payment without the bound approval action. Evaluate whether users
   can identify the issuer, distinguish recognition from certification, compare
   the displayed terms with their order and cancel an unexpected request without
   needing to understand npubs.
3. Add bounded test-asset execution and durable reconciliation. Demonstrate
   recovery from process loss before submission, after submission and before
   result persistence without duplicate payment.
4. Add a second issuer/merchant deployment and test restricted-mode grants,
   blocklist precedence, directional trust, key rotation, discovery, issuer
   mismatch, internal/external route incompatibility and verified receipts.
5. Test optional certification evidence without making it a prerequisite for
   the baseline flow. Define governance before making certification claims,
   and assess merchant-device PIN entry separately from interoperability.

Required negative tests include altered amounts/recipients/mints, fee increases
beyond approval, expired or revoked requests and cards, incorrect PINs across
all credential transports, challenge reuse, concurrent approval, double clicks,
duplicate callbacks, blocked npubs, unknown npubs in restricted mode, policy
changes during approval, unavailable policy storage,
malicious issuer/callback URLs and fabricated settlement messages. Test revocation
at each state transition and verify that merchant interfaces cannot obtain a
general session or protected-record access.

Positive tests must show that an unknown, valid signing npub works in open mode
without certification, and that explicit customer authorization is still needed.
Test missing or unavailable certification both when it is merely informational
and when a particular local grant explicitly requires it. Neither a trusted npub
nor certification may bypass PIN limits, approval binding or settlement checks.

For the NTAG215 profile, test a copied credential both without and with the PIN,
shared lockout across NFC/QR/payment paths, revocation of all copies, replacement
without the original card, and independent recovery after issuer/card loss.
Record the expected limitation: a clone with the correct PIN can authenticate
before revocation. Do not count duplicate-request rejection as proof of clone
resistance. Test loss of the issuer encryption key only with disposable credentials.

Include cardholder-facing scenarios involving lookalike domains, copied branding,
replaced checkout QR codes, a familiar merchant with an unrecognized instance,
unexpected issuer redirects, mismatched order amounts and misleading trust
badges. Use simulated credentials and no live funds. Separate interface findings
from protocol guarantees: a usability test can reveal confusing cues but cannot
prove that a compromised device shows an honest approval screen.

The outcome sought is narrow and measurable: an authenticated merchant request
can become one customer-approved payment through the customer's issuer, with
clear evidence of what was approved and what actually settled. A functioning
prototype does not by itself establish certification or safe operation on a
compromised merchant device.
