# NFC card and QR issuance, read-back and login

Status: issuance, owner-authenticated metadata read-back and PIN-gated public
login implemented, 2026-10-16. Physical-device validation remains separate from
automated security and workflow tests.

## Using the NFC page

Set `SAFEBOX_NFC_ALLOWED_HOSTS=crays.safebox.dev` (use your actual public hostname),
restart the application, and open **NFC Cards** from the wallet home page.
With `SAFEBOX_NFC_ISSUER_ORIGIN` blank, the issuer is the HTTPS request origin,
but only if its host matches this allowlist. Entries are comma-separated exact
DNS hosts, optionally with ports; wildcards are forbidden. An explicit
`SAFEBOX_NFC_ISSUER_ORIGIN` overrides the fallback and must match the request origin.
With neither setting, credential issuance, read-back and login remain disabled.

The reverse proxy must preserve the public Host header, reject unexpected hosts,
and overwrite client-supplied forwarded headers. Configure Uvicorn's
`FORWARDED_ALLOW_IPS` for trusted proxy addresses and prevent untrusted backend
access. NFC uses the ASGI scheme after trusted-proxy processing, never raw
`X-Forwarded-Host`, `X-Forwarded-Proto` or `Forwarded` headers. HTTP is rejected.
Use one stable hostname: a card issued on one origin cannot be read on another.
Resolved settings are request-local; the application configuration is not mutated.
Startup migrations create the `nfc_card_policy` table. Use a compatible Web NFC
browser/device and a writable NDEF card with at least 496 usable bytes.

1. Choose 30, 90 or 365 days of validity. The server generates a cryptographically
   random four-digit PIN (0000–9999); the user cannot select it.
2. Confirm preparation and safely record the displayed PIN separately from the
   card. This no-store preparation page is the only place it is displayed;
   read-back never reveals it. The existing active card remains unchanged.
3. Explicitly confirm replacement and tap the intended card. Writing replaces all
   existing NDEF contents, including any prior login credential. Keep another way
   to access that wallet. A failed or interrupted write may still change the card.
4. Remove the card, select **Read Card**, and tap again. The issuer validates the
   encrypted credential and displays metadata only, never the private key or PIN.
5. Explicitly **Activate Card** after reviewing read-back. This supersedes the old
   generation. **Revoke Cards** immediately clears active and pending generations;
   existing sessions are not terminated.

One pending candidate per wallet expires after 30 minutes. Preparing another
supersedes it. Policy updates use revision checks so stale activation cannot undo
revocation. Only generation IDs, encrypted-payload digest, deadlines and policy
revision are stored in this table—not wallet secrets, PINs or ciphertext.
New cards use generated four-digit PINs; read-back remains compatible with older
6–12 digit PIN cards. Four digits provide only 10,000 possibilities: persistent
attempt limits and lockout are enforced by the login flow described below.
The PIN is not an independent encryption factor protecting against issuer-key compromise.
Read-back is owner-authenticated and does not navigate to an issuer found on a card.
It is not physical-card attestation: an authenticated client can submit copied bytes.

The version-1 codec derives a dedicated NFC AES-GCM key from `SAFEBOX_COOKIE_KEY`.
Key ID is currently fixed at 1; changing that application secret makes existing
cards unreadable. Historical decryption-key rotation remains future work.
QR text transport uses the same encrypted envelope. Hardware tests must
still be performed on real devices; automated tests do not establish compatibility.

## Create a login QR from a card credential

To copy an existing card, open **NFC Cards**, select **Read Card**, and then
**Show Login QR**. This owner-authenticated, CSRF-protected POST validates the
credential and displays a locally generated QR and its text representation.
It does not generate a new PIN, re-encrypt the payload, change the generation,
or reset the PIN-attempt counter. Expired, revoked and superseded credentials
cannot be exported. A locked active credential can be exported, but remains locked.

**Show Login QR** is also available immediately after **Prepare Card**. Save the
generated PIN separately before leaving that page; the QR export page never
displays it. Pending credentials must be explicitly activated before use. The
same activation applies to both NFC and QR copies, and replacing or revoking
the generation invalidates all of them. A QR-only credential can use the same
activation flow without writing a physical card.

The QR is sensitive even though its payload is encrypted. Keep screenshots or
printed copies private; never capture/store the PIN alongside them. No remote
QR generator is used, and credentials never appear in HTTP URLs or query strings.
There is no public QR export endpoint and no plaintext PIN/private-key display.

## Login with an issued card or QR

Deploy a rebuilt application image. Startup migration `20261016_0015` adds the
persistent attempt counter and challenge/rate tables without changing existing
active generations. Previously activated version-1 cards remain usable; no
rewrite is required. All web workers must use the same database and application
secret. Keep recovery material available when testing the first login.

Select **Login with NFC or QR** on the home/connect page (or open `/nfc/login`).
Choose **Read Card for Login**, **Scan Login QR**, or paste the QR text and select
**Continue with QR**. Then enter the PIN in the server-rendered form. Only an
activated, unexpired credential issued for this exact origin is accepted. Legacy
safebox-2 cards and public test markers do not authenticate here. Card reading
does not create a session or contact a relay. Successful PIN validation issues
the normal protected session cookie and redirects to `/wallet`; wallet loading
then uses the normal application flow. Separately protected records still require
their own unlock material. No private key or decrypted PIN is returned in HTML.

Each attempt has a five-minute, browser-bound challenge. The database stores only
its token/browser hashes, issuer, encrypted-payload digest and expiry. Reading
binds one payload to that challenge; submitting the PIN consumes it once, even
when the PIN is wrong. Cookies are Secure, HttpOnly and SameSite=Strict. The
challenge is separate from the CSRF token; both are required. NFC and QR share
the same challenge, rate-limit and authorization code paths. The camera scanner
only acquires bounded credential text: it never follows a scanned URL or compares
the PIN. Camera access requires a button press; cancellation, backgrounding and
navigation stop scanning. Pasting works without camera access or JavaScript.
A new tab/login
start replaces the browser challenge cookie, so finish one attempt at a time.

Five consecutive wrong or missing PIN submissions lock the active card generation.
The counter is persistent and updated conditionally with the active generation and
policy revision, so concurrent requests cannot bypass revocation or the limit.
A successful login resets a below-limit counter; it cannot unlock a locked card.
Recovery requires connecting with recovery material (or an existing session),
then preparing, writing, reading and activating a replacement card. Preparation
alone does not unlock the old card. The owner NFC page reports a locked state.
There is deliberately no timed reset of the four-digit guessing budget.

Public NFC endpoints also share a persistent 30-request budget per source per
five-minute fixed window, including malformed attempts and page loads. Source
addresses are keyed-hashed with the application secret, not stored as plaintext.
Trusted proxy settings determine the source address; shared proxies/NAT may share
the budget. Expired challenge/rate rows are removed during subsequent requests.
Forms are bounded to 4096 bytes, envelopes to 400 bytes. Database failures fail
closed without issuing a session. Use edge request/body limits for broader denial-
of-service protection. Generation authorization commits before session issuance;
revocation after that point does not invalidate an already-authorized session.
Card cloning remains possible: a clone plus the PIN works until revocation/expiry.

## What should be written to a card?

Design decision (2026-10-16): the card will carry an **application-encrypted
Acorn bootstrap credential**, including the wallet's `nsec`, bootstrap relay
and a PIN. The user must enter the PIN during login; the application validates
it before creating a session. This supersedes the initial delegated-NWC-token
proposal. It is a root-authority credential, not a limited spending capability.

The PIN is inside the authenticated ciphertext, never in a plaintext NDEF
record or outer routing metadata. Routing information identifies the issuing
service. The service's HTTPS origin and the Acorn's bootstrap relay are different
endpoints with different roles. Do not copy the browser session token itself or
include recovery words or the separate record-protection key.

## Review of safebox-2

The following reflects inspected local source, not a security audit or a claim
about every deployed legacy version:

| Component | Current legacy behavior | Porting implication |
| --- | --- | --- |
| `app/routers/safebox.py`, `/issuecard` | Gets/creates an NWC secret mapped to the wallet; generates a PIN; encrypts `nwc_secret:pin` with NIP-44 to the service's own public key using `SERVICE_NSEC`. | The encrypted secret is an NWC credential, **not the wallet private key**. The issuer service can decrypt it. |
| Outer envelope | `{"h": hostname, "k": encrypted_token, "a": 21, "n": NFC_DEFAULT}` | `h` routes to the service; `a` supplies an amount default; `n` supplies card/record defaults. Neither is transaction consent. These outer fields are outside the encrypted `k`. |
| `app/utils.py`, `create_nembed_compressed` | JSON → gzip → Bech32 with `nembed` prefix. | Encoding/compression is not encryption. Use explicit version/purpose, strict size limits and bounded decoding in a new format. |
| `app/templates/issuecard.html` | `NDEFReader.write(text)` writes a text record; read controls display record contents. | Separate device success from credential activation; do not echo arbitrary card contents or tokens into logs. |
| `NWCSecret` model / `get_or_create_nwc_secret` | Active secret-to-npub mapping; rotation removes old mappings for that wallet. Multiple cards may share a secret. | Retain explicit lifecycle state; the new design uses one active bootstrap-card generation per Acorn, independently of NWC. |
| `/loginwithnfc` | Decrypts token, resolves mapped wallet, obtains a registered account access key, issues a session. | This depends on the legacy registered-wallet architecture. The inspected login code parses the embedded PIN but does not require a separately entered PIN. Do not describe that login as two-factor. |

Legacy issuance also publishes NWC capabilities and supports rotation from a
GET query parameter. A new design should use authenticated, CSRF-protected POSTs
for issuing/rotating credentials. Inspected legacy paths include token/secret
logging and browser console output; those patterns must not be copied.

Related legacy design references in the sibling safebox-2 repository:
`docs/specs/NFC-FLOWS-AND-SECURITY.md`,
`docs/specs/CARD-TOKENIZATION-AND-NFC-PAYMENT-STRATEGY.md`, and
`docs/specs/QR-AND-NFC-UNIFIED-FLOW-DESIGN.md`.

## What differs in safebox-web?

`app/security.py` defines `SessionCredentials` containing `nsec`,
`bootstrap_relay`, optional home mint, recovery state and record-protection key.
The current session cipher uses a domain-separated AES-GCM envelope (with legacy
Fernet decoding). These credentials are held in an encrypted browser cookie,
not exposed to page JavaScript. Acorn objects are built from the authenticated
session for individual requests or in-memory background jobs.

Safebox-web has operational database state and public handle mappings, but that
does **not** supply the old NFC-secret-to-unlocked-wallet execution path.
An opaque card ID can locate a registration; it cannot by itself reconstruct
the private key or authorize a payment when the user's session is absent.

The chosen bootstrap-card model supplies the missing private key from the
encrypted card at login. The application decrypts it transiently and, only
after successful PIN and lifecycle validation, creates a normal encrypted
browser session. No permanent server-side `nsec` mapping is introduced. Server
state is still required for card revocation and persistent login-attempt limits.

The application can decrypt both the key and PIN; this is not protection against
a compromised issuer. A thief with the card but without the application secret
cannot directly inspect those fields. If the application secret is compromised,
the PIN does not independently protect captured cards because it is encrypted
under the same application authority as the `nsec`.

## Credential envelope (version 1)

Encoding decision (2026-10-16): use **binary CBOR in one NDEF MIME record** for
the wallet card. Abandon `nembed`, Bech32, JSON and gzip for this NFC format.
The proposed MIME type is `application/vnd.safebox.card+cbor` (33 ASCII bytes;
an application convention, not a claim of MIME registration). Web NFC supports
binary MIME records; pass bytes as the record's `data`, not a text encoding.
See the [Web NFC specification](https://w3c.github.io/web-nfc/).

QR/manual transport uses a separate text representation of the **same
encrypted CBOR envelope**:
`safebox-card:v1:` followed by unpadded Base64url. NFC stores neither that prefix
nor Base64 characters. QR does not require decrypting or re-encrypting the
credential, and its parser must enforce the same version, purpose, PIN and
revocation checks. Do not put a credential in an HTTP URL. A QR screenshot is
another copy of the credential, with the same risks as a cloned card.

This is not compatible with legacy `nembed` card readers. Future readers need
explicit new-format support; do not reinterpret legacy tokens as this format.
The separate non-secret hardware test remains available alongside credential issuance.

Candidate logical fields:

| Field | Purpose |
| --- | --- |
| `v`, `type` | Format version and distinct purpose such as `safebox-web-card`. |
| `issuer` | Canonical HTTPS service origin; bind it cryptographically, not just as an editable routing hint. |
| `kid` | Key identifier for issuer encryption/signing key rotation. |
| `credential` | Authenticated encryption containing the Acorn private key as 32 raw bytes, `bootstrap_relay`, `pin`, random `card_generation_id`, issuance time and expiry. The outer version, purpose, issuer/audience and key ID are authenticated as AAD. |

Keep wallet identity, bootstrap relay and policy in the protected envelope or
issuer-side record where practical. Do not put a plaintext `nsec`, recovery
words, record-protection key, reusable browser cookie, or cleartext PIN on the
card. A bootstrap relay does not belong in `issuer` and must not be inferred
from the service hostname.

Derive a dedicated NFC encryption key from the application secret using an
explicit key-derivation domain, separate from session-cookie encryption and NWC
authority. Use a versioned authenticated-encryption envelope. Specify nonce generation,
authenticated metadata, key IDs, expiration, rotation, and migration test vectors
before implementation. Do not trust the outer CBOR header until authentication
succeeds. The private-key bytes represent the same secret as `nsec`; binary
representation reduces size, not authority or sensitivity.

### Measured candidate layout

The offline measurement uses canonical CBOR with integer keys and AES-256-GCM.
This is a concrete sizing proposal, not yet a production wire-format contract.
Version 1 fixes the cryptographic suite and login purpose; there is no attacker-
selected algorithm field or downgrade fallback.

| Layer | CBOR integer keys and values |
| --- | --- |
| Authenticated public header | `0`: version `1`; `1`: purpose `1` (bootstrap login); `2`: canonical issuer HTTPS origin; `3`: integer key ID. Issuer is also the audience for this single-service model. |
| Encrypted plaintext | `0`: private key, 32-byte byte string; `1`: bootstrap relay URL; `2`: PIN text, preserving leading zeros; `3`: random 16-byte card generation ID; `4`: issued-at Unix seconds; `5`: expires-at Unix seconds. |
| Complete outer envelope | Header keys `0`–`3`, plus `4`: 12-byte nonce; `5`: ciphertext with a 16-byte authentication tag. |

The measurement derives a 32-byte key using HKDF-SHA256 with info
`safebox-web/nfc-card/v1` followed by a zero byte. AES-GCM AAD is that same domain
plus the canonical CBOR header. Both CBOR maps are definite-length and have no
CBOR semantic tag or compression. Production must use fresh cryptographically
random encryption nonces, card generation IDs and an approved PIN policy. Real keys and credentials
are never inputs to the sizing script.

The prototype parser specification must enforce exact field types/lengths,
canonical encoding, duplicate-key rejection, private-key scalar validity,
maximum URL/envelope sizes and timestamp policy. Authentication must precede
using the header for anything beyond bounded local key selection. No arbitrary
network request should follow an unauthenticated issuer or relay hint.

### Measured byte budget

Run from the repository root:

```sh
poetry run python scripts/measure_nfc_card_size.py
poetry run pytest -q tests/test_nfc_card_size.py
```

The script creates and decrypts actual AES-GCM ciphertext with **fixed synthetic
fixtures**, validates a QR encoding round-trip, and constructs NDEF/TLV bytes.
It prints only sizes. Fixed fixture nonces/keys are for reproducibility, must
never be used for issuance, and are not imported by the application.

| Example | Plaintext CBOR | Ciphertext + authentication tag | Binary CBOR envelope | NDEF message | Type 2 storage incl. TLV/terminator | QR text characters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Short issuer/relay (17/15 UTF-8 bytes) | 91 | 107 | 150 | 186 | 189 | 216 |
| `https://crays.safebox.dev` + `wss://spurline.safebox.dev` | 103 | 119 | 171 | 207 | **210** | 244 |
| Same issuer + `ws://relay:7777` | 91 | 107 | 159 | 195 | 198 | 228 |
| Long issuer/relay (76/155 bytes), 8-digit PIN, key ID 256 | 234 | 250 | 355 | 394 | **399** | 490 |

The first three cases use a six-digit PIN, key ID 1, 32-byte key, 16-byte generation ID,
and two 32-bit timestamps. These are sizing assumptions, not a final PIN-length
decision. URL size is measured in UTF-8 bytes. The internal URL example measures
space only; it does not authorize internal network access on redemption.

For the typical **210-byte** case:

- Plaintext is 103 bytes: key, relay, PIN, generation ID, timestamps and CBOR overhead.
- Authentication adds 16 bytes, yielding 119 ciphertext bytes.
- Nonce, public header and outer CBOR framing bring the envelope to 171 bytes.
- A single MIME NDEF short record adds 36 bytes: 3 header/length bytes and the
  33-byte MIME type; no record ID or additional records are included.
- Type 2 NDEF Message TLV and terminator add 3 bytes.

NDEF payloads above 255 bytes use a four-byte payload-length field rather than
one byte. A Type 2 message length of 255 or more uses the extended TLV length,
so these two thresholds are different. The script/tests account for both.
No Android Application Record, URL record, signature beyond the AEAD tag,
home mint, record-protection key, recovery phrase or optional label is included.
Adding any of those changes the measured size. No compression is assumed;
encrypted output is not expected to compress usefully.

### Card capacity interpretation

NXP lists nominal user memory of 144/504/888 bytes for NTAG213/215/216, but its
default Capability Container advertises NDEF areas of **144/496/872 bytes**.
Use the advertised area and actual tag layout, not just marketing capacity.
[NXP NTAG213/215/216 datasheet, sections 8.5.4–8.5.6](https://www.nxp.com/docs/en/data-sheet/NTAG213_215_216.pdf).

| Card | Default advertised NDEF area | Typical 210-byte fixture | Long-route 399-byte fixture |
| --- | ---: | --- | --- |
| NTAG213 | 144 bytes | Does not fit | Does not fit |
| NTAG215 | 496 bytes | Fits model; 286 bytes remaining | Fits model; 97 bytes remaining |
| NTAG216 | 872 bytes | Fits model; 662 bytes remaining | Fits model; 473 bytes remaining |

These are modeled fits, not physical write results or guarantees. Existing
lock/memory-control TLVs, additional records, card formatting, browser encoding
choices and locked areas can reduce usable space. The model includes NDEF TLV
and terminator but not other reserved TLVs or physical page-write padding.
Validate the actual writable NDEF capacity and perform readback on the target
hardware. **NTAG215 is a reasonable initial target for this measured layout**;
arbitrarily long routes or future fields may exceed it. Keep issuance bounded
and reject oversized credentials rather than silently truncating them.

The plaintext host and payload size can leak information. Commodity NDEF tags
are copyable: encrypted credentials can still be replayed as opaque bytes.
Encryption alone does not prove physical card presence or prevent cloning.
Tag serial numbers are not an authentication factor.

### PIN-gated login contract

1. A deliberate NFC read acquires the opaque card envelope. It must not create
   a wallet session, display secrets or execute wallet operations.
2. The login page asks the user to enter the PIN separately. Never prefill it
   from the card, return the decrypted PIN to the browser, or compare it in
   JavaScript. Submit the envelope and PIN over HTTPS in a CSRF-protected POST.
3. The server bounds and validates the envelope, authenticates/decrypts it,
   checks version, purpose, issuer/audience and expiry, and checks the card's
   active generation for the npub derived from the decrypted private key and
   the persistent attempt budget. A missing or non-matching generation fails,
   even if the PIN is correct.
4. Compare the submitted PIN with the encrypted payload's PIN using an
   appropriate constant-time comparison. Treat PINs as strings so leading zeros
   are preserved; define length/character policy and generation at issuance.
   Missing or incorrect PINs never create a session or start an Acorn job.
5. Only a correct PIN for an active, unexpired, unblocked card permits creation
   of a fresh standard session. Recheck lifecycle state atomically with the
   authorization decision. Bootstrap relay validation still applies. Protected
   records retain their separate unlock requirement.

Attempt counters and generation lockouts must survive process restarts, concurrent
workers and browser changes. Key limits by the authenticated card generation ID, with
additional request/source controls against bulk guessing; do not rely on an
IP-only or browser-only counter. Malformed envelopes also require bounded work
and request throttling. The implemented limits and owner replacement procedure
are described above. Fail closed if lifecycle/attempt state cannot
be read or updated. User-facing errors should not expose secrets or unnecessary
credential-validity distinctions.

Requiring the PIN protects redemption of a stolen card against guessing only
when these controls are enforced. It does not make the static payload
non-replayable: anyone holding both a copied card and its PIN can redeem it
until revoked/expired. A server-issued single-use login challenge can prevent
duplicate submission of one captured login request, but cannot prevent a holder
of both credentials starting a new login. Card revocation blocks future logins;
whether it also terminates already-issued sessions requires explicit session
revocation support and must not be promised implicitly.

### Agreed revocation model: one active generation per Acorn

The application stores the active **card generation ID alongside the npub**.
The card carries the same ID inside its authenticated encrypted payload. At
redemption the server derives the npub from the decrypted private key; it must
not trust a separately supplied wallet identifier. Only an exact match with the
active generation allows PIN validation to lead to a session.

The generation ID is 16 cryptographically random bytes. It replaces the proposed
`card_id` at plaintext CBOR key `3`; it is not an additional field. **The measured
210-byte typical and 399-byte long-route card sizes are unchanged.**

Keep these three concepts separate:

| Value | Lifetime and role |
| --- | --- |
| Card generation ID (16 bytes, encrypted) | Stable for an issued credential; changing the database's active value revokes earlier generations. |
| AES-GCM nonce (12 bytes, outer envelope) | Fresh for every encryption under a given key; cryptographic nonce requirements are independent of revocation. |
| Login challenge/nonce | Short-lived, single-use request binding; not stored as a static card value and not a replacement for revocation. |

Proposed durable database state (no plaintext `nsec` or PIN):

- One wallet policy row, unique by npub: nullable active generation ID, a
  monotonically increasing policy revision, change timestamp and login-throttle
  state. Null means no card generation is authorized.
- The current implementation stores one pending candidate on the wallet policy
  row: random generation ID, encrypted-payload digest and preparation deadline.
  Preparation increments the policy revision and supersedes the previous pending
  candidate. It does not retain the encrypted payload or private key.
- Persistent attempt/challenge state and minimal audit events, without card
  ciphertext, PINs or wallet secrets. Apply the data-minimization/retention
  policy separately from the authority decision.

Issuance and management require an authenticated owner session, CSRF protection
and explicit POST confirmation. The issuing application must never enroll an
unknown card automatically merely because its payload decrypts successfully.

**Normal replacement:** prepare a fresh pending generation, write and read back
the new card, then explicitly activate it. Activation atomically checks ownership,
candidate expiry/status and the expected wallet policy revision, switches the
active generation and increments the revision. All older copies fail subsequent
card login checks. Failed/cancelled writes leave the existing active card usable;
the unused candidate expires or can be cancelled. Repeated activation of the
same already-active candidate may report success without another state change.

**Lost/stolen card:** provide a separate **Revoke Card** action. It immediately
clears the active generation and increments the revision, invalidating pending
candidates prepared under the older revision. The owner can prepare a replacement
afterwards; revocation does not depend on possessing or writing a new card.
Replacing a lost card should offer immediate revocation rather than silently
leaving it valid throughout preparation.

Use a transaction or compare-and-swap on the policy revision. Two concurrent
activations must not both succeed; a stale tab or delayed activation must not
restore an old generation after revocation. Login authorization, attempt counters
and activation/revocation must use authoritative state across workers. Define
the atomic authorization point: a login authorized before revocation may already
have created a session; revocation alone does not terminate that session.

This policy deliberately supports **one active generation, not necessarily one
physical card**. Copies of the current generation are indistinguishable and are
revoked together. Changing the PIN requires a newly encrypted, newly activated
generation; do not leave older PIN-bearing copies active. Independently managed
multiple cards would be a separate policy/schema extension.

Fail closed when policy state is unavailable or missing. Back up revocation state
as security-critical data: restoring stale state can resurrect old credentials.
Disaster recovery must invalidate affected generations before accepting logins
unless current revocation state can be established. Application-secret compromise
can expose the private key from captured cards; generation revocation cannot
revoke an already-extracted wallet key or prevent its use outside this application.

## Required controls before credential issuance

- Enforce the single-active-generation database policy above; fail closed if
  authoritative state is unavailable, with a recovery plan that cannot silently
  restore revoked generations.
- Scope this version to PIN-gated bootstrap login. Do not add card-triggered
  spending or record disclosure; those retain their normal session review flows.
- Fresh requester challenge, timestamp, one-time nonce and transaction binding
  for payment/disclosure operations. A static card is not a challenge signer.
  Revalidate authorization before irreversible execution.
- Require the separately entered PIN for every card login. Storing it encrypted
  on the card alone is not validation. No possession-only fallback is included.
- Explicit confirmation of recipient, amount, unit/mint and disclosure scope.
  No automatic payment from a tag's amount default.
- Treat service/relay hints as untrusted input: HTTPS origin validation,
  explicit cross-service confirmation, SSRF/redirect protections, no arbitrary
  private-network contact. Internal-only routing must be an explicit deployment
  policy, not a URL supplied by the card.
- Validate binary CBOR schema, sizes, canonical representation, AEAD
  authentication and supported versions. No gzip or Bech32 is used for this
  card format. Reject unknown purposes and legacy tokens by default.
- Credential payloads only in protected POST bodies, never URL parameters,
  logs, analytics, local storage or service-worker caches. Sensitive responses
  require `no-store` and deliberate user activation.
- Enforce actual encoded NDEF size against chosen cards. The measurements above
  support an NTAG215 target but do not replace hardware capacity/write checks.

Proposed issuance sequence: authenticated review → CSRF-protected preparation
POST → pending per-card registration → explicit device write → readback →
authenticated activation POST. Browser readback is a usability check, not proof
against a malicious client or cloned tag. Failed/uncertain writes remain pending
and revocable; do not automatically generate another active credential. Support
card loss, aborted activation, server-key rotation and service migration.

## First implementation: device readiness only

The `/nfc` page requires a session but does not load Acorn state, query relays,
generate a credential, contact a vault, or change server state. Opening it does
not request NFC permission. The browser writes only:

```text
SAFEBOX-WEB-NFC-TEST:v1
```

The user must select a confirmation checkbox and press **Write Test Card**.
Writing uses `overwrite: true` only after explicit replacement consent; existing
NDEF messages are replaced. This does not bypass write locks or browser permissions. No
permanent read-only operation is offered. A separate **Read Any NFC Card**
action accepts any NDEF card, including legacy safebox-2 cards, and displays
record count, types and byte sizes (first 16 records only). Payloads, custom type
values and serial numbers remain hidden; nothing is submitted or opened.
The known public test marker still gets a specific verification message.
Successful reading does not establish write permission or login validity. Both operations support
cancel, a 30-second deadline and cancellation on page hide/backgrounding. Late
callbacks cannot change the completed/cancelled state. Cancellation may leave
the write outcome uncertain; the page tells the user to read back before retrying.

This test does not issue a wallet card, establish a unique card identity or
guarantee that a larger future credential fits. It is deliberately not a legacy
`nembed`, so it cannot be mistaken for an existing wallet credential.

Web NFC has limited browser availability and requires a secure context.
Runtime checks and an unsupported-browser explanation remain necessary.
Chrome's documentation describes user-initiated writes, overwrite protection
and abort signals; MDN documents `NDEFReader` and its limitations.
[Chrome Web NFC guide](https://developer.chrome.com/docs/capabilities/nfc),
[MDN NDEFReader](https://developer.mozilla.org/en-US/docs/Web/API/NDEFReader).

## Validation and next steps

Automated checks should cover session enforcement, secret-free HTML, navigation,
unsupported contexts, explicit replacement confirmation, overwrite behavior, permission
denial, cancellation, timeout, late completion, readback mismatch, malformed text,
and non-disclosure of arbitrary card content. Hardware testing is separate:
test a blank tag, non-empty tag, locked tag, insufficient-capacity tag, interrupted
tap and browser backgrounding on real supported devices. No physical NFC result
is implied by mocked API tests.

Automated login tests cover correct and incorrect PINs, leading zeros, revocation,
expiry, replay, browser binding, persistent lockout, concurrent attempts, bounded
forms, request limits and database failure. Hardware checks should exercise the
complete issuance → activation → disconnect → read → PIN → wallet lifecycle,
plus revoked and locked cards. Do not test loss of access without retaining
independent recovery material.
