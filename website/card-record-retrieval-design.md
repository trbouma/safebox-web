# Card Based Record Retrieval and Sharing

Status: design review proposal, 2026-10-16. Existing card issuance and PIN-gated
login provide the credential foundation. The walkthroughs below specify desired
behavior, not confirmation that the complete flows are implemented or tested.
No NWC intermediary is required.

The card enables the issuer to recover an Acorn context. Acorn uses the nsec and
bootstrap relay to resolve private records and the relay-backed information needed
to fetch and decrypt their Blossom attachments. The card does not need a record
catalog, separate blob-decryption keys or an attachment-server inventory.

This design separates **owner access**, which lets the cardholder view their own
records, from **disclosure**, which sends an explicitly selected record to another
party. Authentication is not blanket permission to share records.

## Relationship to existing designs

- [Card issuance and login](nfc-card-design.md) governs envelope validation, PIN
  attempts, expiry, active card generation and revocation.
- [Payment authorization](nfc-payment-authorization-design.md) defines issuer
  trust boundaries and transaction-bound approval. Record sharing reuses those
  principles, not payment mandates or financial settlement states.
- [Cash walkthroughs](cash-clear-payment-walkthroughs.md) describe transient
  credentials, process loss and recovery. The same confidentiality constraints
  apply, but record delivery must have its own operation and evidence model.

The architectural premise is that Acorn can determine the required decryption
information from the nsec and its relay-backed records. Retrieval can still fail
when relays, metadata or blobs are unavailable. Do not interpret “derivable” as
a guarantee that dependencies are always reachable or retained.

## Roles and authority

| Role | Permitted responsibility | Boundary |
| --- | --- | --- |
| Cardholder | Authenticate, select records and approve disclosure | A tap or PIN alone does not approve a third-party request |
| Customer issuer | Validate credentials and enforce owner/disclosure authorization | Never expose the nsec or a reusable owner session to the requester |
| Acorn | Resolve, decrypt and retrieve records and attachments | No need to expose internal decryption keys to browser or requester |
| Requester or recipient | Request a defined record and receive an approved disclosure | Cannot browse the owner's catalog or retrieve unrelated records |
| Relay and Blossom services | Store and transport the relevant protected data | Availability or successful transport does not establish authorization |

The preferred profile uses the owner's device at a recognizable issuer origin.
Displaying private records on a merchant or shared device exposes their contents
to that device, regardless of issuer-hosted pages. Treat that as a separate,
higher-trust deployment profile, not an equivalent default.

## Shared credential handling

NFC and the equivalent card QR normalize into the same issuer credential check.
The issuer authenticates the envelope, checks issuer binding, generation, expiry
and persistent PIN limits, then recovers the Acorn context. All credential entry
points share the existing attempt budget; record access must not create another
PIN-guessing allowance.

Keep private keys and decrypted content out of request URLs, logs, analytics,
durable job arguments and error reports. Sensitive responses should use private,
no-store caching behavior. Record/blob URLs must not become public bearer access
merely because a user successfully logged in once. Validate authorization on each
fetch; protect any download capability with a short lifetime and narrow scope.

Use server-rendered pages and explicit forms for decisions. Browser JavaScript
only acquires card/QR data and updates presentation. Ordinary authorized GETs may
retrieve owner content; GETs must not create sharing grants or initiate disclosure.
Untrusted record content must be rendered safely, never executed as application
instructions or active markup. Apply content-type and size limits to previews.

## Walkthrough 1 Owner opens a record

Example: the owner wants to open their eye prescription and its PDF attachment.

1. The owner opens their known issuer and selects card login or record access.
2. They read the card locally and enter the PIN. The issuer performs all current
   credential lifecycle and attempt checks. No requester or merchant is involved.
3. The issuer establishes owner access using its normal authenticated-session
   policy. This is an owner flow; unlike a payment-purpose approval, it can create
   an owner session. Its lifetime and logout behavior must remain explicit.
4. Acorn loads the owner's relay-backed record index. The issuer renders the list
   only to that owner; record titles and categories are themselves private data.
5. The owner selects the prescription. The issuer authorizes that retrieval,
   and Acorn resolves and decrypts the chosen record.
6. If it references an attachment, Acorn resolves the necessary location and
   decryption information, fetches the blob, and applies its integrity/decryption
   checks. The issuer displays or streams the result to the authorized owner.
7. The owner can close the viewer or log out. Avoid persistent decrypted caches
   and unnecessary temporary files. An intentional download is an owner-controlled
   copy; logging out cannot erase that copy from the device.

This flow creates no external disclosure, grant or payment. Opening a record must
not silently initiate a legacy offer/presentation handshake. Existing owner login
permissions should not be expanded merely to support record viewing.

If the blob is missing, report the attachment unavailable while preserving any
retrievable record metadata. An authentication/decryption or integrity failure
must not fall back to displaying unauthenticated bytes. The source remains unchanged.

## Walkthrough 2 Owner shares a selected record

Example: an optician requests the prescription. Sharing begins as a separate
operation, even when the owner is already logged in and viewing that record.

1. The recipient supplies a versioned signed request identifying its signing key,
   receiving identity, desired record scope, purpose, request ID and expiry.
   Alternatively, the owner initiates sharing and explicitly selects a recipient;
   recipient resolution must be frozen before approval in either case.
2. The issuer verifies the request and admission policy. Open admission of a valid
   unfamiliar requester may allow review, but conveys no record access. Label
   self-asserted names separately from independently supported identity claims.
3. The owner authenticates at the issuer. The requester does not receive a session,
   nsec, PIN, private record catalog or an automatic answer about which records exist.
4. The owner chooses the prescription. The issuer prepares a preview binding its
   exact version/content digest, selected fields if supported, and whether the
   attachment is included. The initial scope can share a complete selected record;
   do not promise selective disclosure unless the format and adapter support it.
5. The issuer shows recipient, record, attachment scope and intended delivery.
   The owner explicitly approves a short-lived, single-use disclosure-purpose
   challenge using a CSRF-protected POST. Fresh card/PIN confirmation for disclosure
   is the proposed baseline; an owner session alone must not silently approve it.
6. The issuer records the approved digest and recipient, then uses Acorn to retrieve
   the selected version and prepare recipient-protected delivery. A changed record,
   attachment scope or recipient requires fresh approval. If the approved version
   is no longer retrievable, stop rather than substitute the latest version.
7. Deliver only the approved material. Use a supported recipient-specific encrypted
   representation for any copied attachment; do not send the owner's root key,
   reusable source decryption material or an unrestricted source download URL.
   Exact wrapping, re-encryption and transport are adapter decisions still to specify.
8. Record delivery evidence and, when supported, recipient acknowledgment. A relay
   publish acknowledgment means submitted, not necessarily received or read. The
   recipient receives no ability to enumerate or fetch other owner records.

The source record and attachment remain unchanged. Delivery is a disclosure of a
copy, not a transfer of exclusive ownership. Once the recipient has decrypted or
saved it, revoking the card or expiring the request cannot recall that copy.
Request-purpose text does not technically enforce how a recipient later uses data.

## Separate purposes and state

| Action | Authorization | Result |
| --- | --- | --- |
| Owner login | Valid current credential and PIN | Owner session under existing policy |
| Owner retrieval | Authorized owner session and record selection | Content displayed to owner |
| External disclosure | Exact recipient/version/scope plus explicit disclosure approval | Only the approved record copy delivered |
| Payment | Separate approved payment intent | No record access or disclosure authority |

Proposed disclosure progression:

`REQUESTED → REVIEWED → APPROVED → SENDING → DELIVERED`

Recipient acknowledgment may be tracked separately as `ACKNOWLEDGED`; neither
delivery nor acknowledgment proves a human read the content. Pre-send operations
can be declined, cancelled or expire. Ambiguous sends become `RECONCILING`, not
permission to construct a new disclosure. Stable operation IDs and recipient-side
deduplication should permit recovery of the same delivery without creating new
grants. The wire protocol and evidence for each state remain to be specified.

## Failure and revocation walkthroughs

| Situation | Expected behavior |
| --- | --- |
| Card expired, revoked or PIN locked | Reject new credential authentication using shared policy |
| Owner has an existing session when card is revoked | Apply explicit session-revocation policy; do not claim card revocation automatically closes sessions |
| Record index or relay unavailable | Show a bounded retrieval error; do not report an empty wallet as authoritative |
| Blob unavailable | Preserve source and show attachment failure; no automatic unrelated-host probing |
| Record modified during sharing approval | Reject stale digest and obtain renewed approval |
| Requester changes destination | Reject signature/binding mismatch; never redirect secrets to the new destination |
| Owner leaves before approval | No sharing occurs |
| Owner leaves after approval | Existing authorized work may finish; browser presence is not required |
| Worker restarts without credentials | Reconcile existing delivery first; obtain owner authentication if retrieval must resume |
| Card revoked before sharing starts | Recheck lifecycle and stop unsubmitted disclosure |
| Card revoked after sending | Prevent new authorizations; cannot recall disclosed copies |
| Duplicate request or callback | Return/update the same operation; do not create additional disclosure rights |
| Recipient is offline | Preserve bounded pending state and evidence; no claim of receipt/read |

Apply safe network access controls to attachment resolution and recipient routes,
including explicitly configured internal endpoints. Open request admission does
not authorize arbitrary server-side fetches or reveal the owner's storage topology.

## Decisions and acceptance criteria before implementation

The owner walkthrough is the first conceptual target. Sharing remains a separate
design stage and must not block basic owner retrieval by importing the entire
legacy offer/grant system.

Decide the following before coding the respective stage:

- Owner session lifetime, logout and whether card revocation also terminates sessions.
- Exact preview/download limits and handling of active or untrusted document formats.
- Record identity/version binding and behavior for replaceable records whose older
  versions are unavailable.
- Disclosure request schema, signature/audience binding, requester recognition and
  recipient identity verification.
- Recipient-specific record/blob encryption and transport, with explicit supported
  attachment formats and no reusable source-secret disclosure.
- Disclosure approval lifetime, restart recovery, deduplication and receipt evidence.
- Minimal audit fields and retention; no record bodies or sensitive labels in routine logs.

Owner tests should cover card/QR parity, unauthorized record IDs, missing metadata,
unavailable blobs, failed integrity/decryption, logout and session expiration.
Sharing tests should additionally cover modified records, changed recipients,
attachment-scope changes, cross-purpose challenge reuse, replay, offline recipients,
worker loss, and revocation before and after send. Verify that a requester cannot
list records, obtain a general session or turn disclosure approval into payment.

The success condition is two distinct guarantees: the authenticated owner can
retrieve their records through Acorn, and another party receives only the exact
material the owner separately approved for that recipient.
