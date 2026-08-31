# OpenETR Issue and Verify Integration Design Note

> **Terminology migration:** OpenETR now distinguishes Anchor Events, signer
> evidence, recognition, standing, and Digital Originals. The corresponding
> Safebox Web terminology, projection, compatibility, and implementation
> changes are defined in the
> [OpenETR Anchor, Recognition, and Standing Migration Note](OPENETR-ANCHOR-STANDING-TERMINOLOGY-MIGRATION-NOTE.md).

## Status

This note is a proposed design. It does not describe functionality currently
available in Safebox Web.

The first integration should be intentionally narrow: issue an OpenETR origin
event for an existing Acorn record artifact, and independently verify the
artifact against OpenETR signed evidence. Record transmission, WebSockets,
transfer negotiation, background subscriptions, and the wider control-event
lifecycle are not prerequisites and are outside this initial scope.

## Purpose

Safebox Web already lets an attached Acorn safeguard private records and their
Record File attachments. OpenETR adds a different capability: a portable,
signed control layer around an artifact.

The integration should let a user answer two practical questions:

1. **Issue:** How can this exact artifact be brought into the OpenETR scheme by
   a particular signing key?
2. **Verify:** Does the artifact presented now match the object identified by
   the signed OpenETR evidence, and what does the control graph say about it?

These are not storage operations. Acorn remains responsible for safeguarding
keys, private records, encrypted attachments, relay-backed availability, and
recovery. OpenETR remains responsible for object digests, origin and control
events, graph traversal, structural verification, and verifier-policy output.
Safebox Web presents the workflows and mediates the trusted execution boundary.

## Presentation verification and deep verification

The product should distinguish two verification levels explicitly.

**Presentation verification** lets a holder display a record and present a QR
code to a verifier. The verifier receives a temporary, read-only presentation
of the record and its Control History, without an import action. The durable
OpenETR link can be followed independently. This is the preferred path for
routine inspection because it minimizes disclosure, retention, and workflow
overhead.

**Deep verification** lets the verifier receive the exact record into its own
safekeeping and verification environment. The verifier can then calculate the
digest locally, run native format validation, compare the artifact with signed
OpenETR evidence, consult recognition inputs, retain evidence where authorized,
and apply its own policy. This is appropriate when the consequence of relying
on the record justifies possession and deeper analysis.

The distinction is about verification depth, not legal effect:

| Property | Presentation verification | Deep verification |
| --- | --- | --- |
| Exact artifact retained by verifier | No | Yes |
| Human-readable record inspection | Yes | Yes |
| Control History available | Yes | Yes |
| Independent digest calculation | Optional through durable verifier | Yes, locally |
| Native-format or domain validation | Limited | Yes |
| Verifier applies its own recognition and policy | Yes | Yes, with the complete artifact |
| Transfers control or ownership | No | No |

Receiving a record copy must never be interpreted as an OpenETR control
transfer. Control changes require their own authorized, signed control events.
Likewise, presentation proves only what the displayed artifact and queried
evidence support; it does not create a universal recognition claim.

The two levels form an escalation path:

```text
present -> inspect -> follow durable evidence
                    |
                    +-> consequence warrants deeper review
                        -> receive exact record
                        -> verify independently
```

The canonical policy discussion is the OpenETR
[Graduated Disclosure brief](https://trbouma.github.io/openetr/policy-briefs/graduated-disclosure/).
The corresponding Safebox user-facing explanation is
[Graduated Disclosure in Safebox Web](../website/graduated-disclosure.md).

## Product rationale: control graph, social graph, and funds

Safebox is intended to operate at the conjunction of three portable
capabilities:

1. **Control graph:** OpenETR provides signed evidence about the origin,
   transfer, attestation, encumbrance, redemption, and termination of an
   object. It answers: *What happened to this object, in what order, and which
   keys authorized the events?*
2. **Social graph:** Nostr Web of Trust and other recognition inputs let a
   verifier ask whether the issuers, controllers, attestors, and counterparties
   are known or trusted from a particular community or institutional viewpoint.
   It answers: *Who recognizes these actors, and why should this verifier give
   weight to their actions?*
3. **Spendable funds:** Acorn carries user-controlled funds beside the keys and
   records. This lets an application pay, settle, charge, reward, bond, or
   otherwise coordinate value without requiring the control or recognition
   model to be owned by the payment provider.

The simple proposition is powerful: if a verifier can establish the history of
an object and see that the relevant actions were signed by people or
organizations it recognizes, it can make a useful decision without returning
to the application or database that originally created the record. Funds add
the ability to act economically on that decision.

This is not a claim that social proximity makes an event true or that payment
makes a record valid. The control graph preserves signed evidence. The social
graph supplies viewpoint-dependent recognition inputs. A verifier policy keeps
those inputs separate and explains the conclusion it reaches. Spendable funds
remain a distinct user-controlled resource. Their conjunction creates a
general capability that can cross applications, institutions, communities,
devices, and systems of record.

Safebox therefore should not become the authoritative registry for the objects
it helps users hold or verify. Its role is to provide a practical safekeeping
and execution environment where portable control evidence, recognized actors,
private records, and spendable funds can be used together.

## Architectural boundary

| Layer | Responsibility |
| --- | --- |
| Browser | Submit ordinary forms and display complete issue or verification results |
| Safebox Web | Authenticate the attached Acorn, enforce CSRF and confirmation, retrieve artifact bytes, invoke components, and render results |
| Safebox Acorn | Safeguard the signing key and private record; retrieve, authenticate, and decrypt the Record File |
| OpenETR component | Calculate the object identity, construct and sign events, publish and read back evidence, query the graph, and apply verifier policy |
| Nostr relays | Carry signed OpenETR events; they are transport and availability infrastructure, not recognition authorities |
| Recognition layer | Decide what effect to give the signed evidence under a community, institutional, contractual, or legal rule book |

The mature Safebox Web integration should consume OpenETR through an installable
Python component API. Event kinds, tag construction, graph traversal, and
verifier rules must not accumulate in route functions. The initial read-only
experiment described below is a bounded exception: it keeps the minimum wire
contract in one lightweight adapter so that the user experience can be tested
before the final packaging boundary is selected. The OpenETR CLI `--json`
contract remains useful for interoperability tests, but spawning the CLI is not
the preferred production boundary.

The current OpenETR working registry assigns regular event kind `1415` to the
origin event and kind `1416` to the control-event family. Safebox Web should
obtain these semantics from a pinned compatible OpenETR component rather than
scattering kind constants through the application. Deprecated `31415` and
`31416` events may be reported by compatibility tooling but must not be issued
as new graph nodes.

Relevant upstream references include the OpenETR
[event-kind registry](https://github.com/trbouma/openetr/blob/main/docs/specs/EVENT_KIND_REGISTRY.md),
[layered architecture](https://github.com/trbouma/openetr/blob/main/docs/specs/OPENETR_LAYERED_ARCHITECTURE_NOTE.md),
and [generic verifier policy](https://github.com/trbouma/openetr/blob/main/docs/specs/OPENETR_GENERIC_VERIFIER_POLICY.md).

## Object identity and artifact bytes

The OpenETR Digital Artifact is anchored to the digest of the exact plaintext
artifact. For an Acorn record with an encrypted Blossom attachment:

- the encrypted Blossom object digest is not the OpenETR object digest;
- the Acorn private-record event id is not the OpenETR object digest;
- the Record File plaintext digest is the appropriate object anchor; and
- verification must hash the exact bytes being presented, not a filename,
  label, browser URL, or mutable metadata field.

Acorn already authenticates and decrypts a Record File before returning
its bytes. Safebox Web should pass those bounded bytes directly to the OpenETR
component where its API permits. If an early OpenETR API requires a file path,
Safebox Web may use an owner-only temporary file with guaranteed cleanup, but a
bytes or stream API is the target because it avoids an unnecessary plaintext
filesystem boundary.

The existing eight-character Record File Fingerprint remains a recognition
aid only. It must never substitute for the complete digest used by OpenETR.

## Signing-key decision

The attached Acorn already has a component key available in request-scoped
process memory after the secure session cookie is decrypted. The smallest
initial implementation can use that key as the OpenETR issuer profile key.
Safebox Web must make this choice explicit before publication because a public
OpenETR signature links the Acorn public key to the object digest and graph.

The initial confirmation page should display:

- the signing `npub`;
- the full object digest and short recognition fingerprint;
- the OpenETR relay destinations;
- the selected domain adapter or `generic` profile;
- the metadata that will be public; and
- a warning that issuance is a signed publication, not a private Acorn write.

The `nsec` must not be written to an OpenETR config file, subprocess argument,
database, temporary file, log, event content, or result page. It is supplied to
the component in memory for the duration of the request.

A later signer interface may support a dedicated OpenETR profile key, hardware
signer, remote signer, or a protected key record. That extension must not be
required for the first issue/verify implementation, and it must not silently
change which key exercises authority.

## Issue workflow

The proposed hypermedia flow is:

1. The user opens an existing private record that has a Record File.
2. The record representation offers **Issue with OpenETR**.
3. A GET request retrieves and hashes the artifact and presents a confirmation
   page. It does not publish anything.
4. Safebox Web performs a preflight OpenETR query for the object digest and
   reports existing origin events, including competing or same-signer origins.
5. The user explicitly confirms a CSRF-protected POST.
6. Safebox Web reloads the artifact and recomputes the digest. It must match the
   digest shown at confirmation time.
7. The OpenETR component constructs and signs the origin event, publishes it to
   the configured OpenETR relays, and queries for exact event readback.
8. Safebox Web returns a complete result representation containing the object
   id, digest, event id, signer, kind, relays, readback status, and warnings.

The confirmation state should be authenticated and short-lived so a changed
artifact cannot be substituted between preview and POST. The simplest safe
form is a server-authenticated token containing the record label, complete
digest, signing `npub`, selected profile, relays, issuance purpose, and issuance
time. It must contain no private key or artifact bytes.

Duplicate-origin handling must fail closed by default. If an origin already
exists, Safebox Web should direct the user to verification rather than silently
issue another event. Any future override must be explicit and must show the
existing origin evidence first.

Relay acknowledgement alone is not conclusive. Exact post-publication readback
is the stronger success condition. If publication may have occurred but
readback times out, the outcome is **indeterminate**, not failed. The route must
show the signed event id and must not automatically sign and publish a second
origin event.

## Verification workflow

The first verification surface should work against an attached Acorn record.
An unauthenticated upload-only verifier can be considered later after resource,
privacy, abuse, and temporary-storage controls are defined.

The proposed flow is:

1. The user selects **Verify with OpenETR** from an existing record.
2. Safebox Web retrieves and decrypts the Record File through Acorn.
3. The OpenETR component calculates its complete digest and object id.
4. It queries the configured relays for origin and control events anchored by
   the object-wide `o` tag and traverses exact prior-event `e` links.
5. It verifies event ids, signatures, required tags, graph continuity, and the
   selected generic or domain verifier policy.
6. Safebox Web renders signed evidence and policy conclusions separately.

The result must not collapse all findings into a single green “valid” label.
It should distinguish at least:

- **Artifact match:** the presented bytes match the queried object digest.
- **Cryptographic evidence:** event ids and signatures verify.
- **Structural graph:** required tags and event links form candidate chains.
- **Derived state:** current candidate controller and lifecycle state.
- **Policy findings:** warnings, ambiguous branches, duplicate origins, unknown
  participants, or non-recognition under the selected verifier policy.
- **Recognition:** whether a selected community, institution, registry, or
  legal rule book gives effect to that evidence, if such a policy was actually
  applied.

“Cryptographically verified” must not be presented as “authentic government
document,” “legally valid,” or “recognized issuer.” A signature proves that a
key signed an event. Recognition of the actor and effect of the event belong to
the selected recognition layer.

## Public evidence and private records

An Acorn record and attachment may remain private while its OpenETR control
graph is publicly queryable. That separation is useful, but publication of a
plaintext artifact digest is not anonymous.

Anyone who obtains a candidate copy of the artifact can hash it and test
whether it corresponds to the public OpenETR object. This confirmation risk is
especially important for passports, birth certificates, health records, and
other predictable or externally available documents.

Therefore:

- OpenETR issuance must never happen automatically when a record is saved;
- the issue confirmation must explain that the digest, signer, event time,
  tags, and graph relationships may become public;
- event content and tags must not contain private record payloads, Acorn keys,
  recovery material, Blossom authorization data, or decrypted artifact bytes;
- a private Blossom URL or Acorn record label should not be published unless a
  defined OpenETR profile requires it and the user explicitly accepts that
  disclosure; and
- future privacy-preserving commitments must be standardized in OpenETR rather
  than invented only in Safebox Web.

## Local linkage metadata

The relay graph is the signed evidence and remains independently queryable by
object digest. Safebox Web does not need a new server-side user database table
to make issuance work.

For convenience, Acorn may later store an encrypted companion record in a
reserved namespace containing:

- object id and complete digest;
- origin event id and signer `npub`;
- relay hints and readback observations;
- OpenETR component and schema versions;
- selected domain and verifier policy; and
- the time Safebox observed the result.

This companion record is a local index, not the authority for verification. It
must be filtered from ordinary private-record listings, must not modify the
user's original payload, and must be safe to rebuild from the artifact and
public OpenETR evidence.

## Relay configuration

The Acorn home relay and OpenETR publication relays serve different purposes.
Safebox Web must not assume that a private home relay is an appropriate public
OpenETR evidence relay.

A later implementation should introduce explicit configuration such as:

```dotenv
SAFEBOX_OPENETR_ENABLED=false
SAFEBOX_OPENETR_RELAYS=wss://relay.example
SAFEBOX_OPENETR_POLICY=generic
SAFEBOX_OPENETR_TIMEOUT_SECONDS=20
```

Production OpenETR relays should use `wss://`. Any local `ws://` exception
should reuse the exact allowlist discipline already applied to Acorn bootstrap
relays. User-selectable relay destinations must be chosen from an operator
allowlist to prevent server-side request forgery.

Verification should report which relays were queried and whether evidence was
missing, inconsistent, unavailable, or returned successfully. Relay absence is
not proof that an event never existed.

## Hypermedia approach

Issue and verify remain ordinary server-directed workflows:

- GET renders a confirmation or report;
- POST performs issuance only after CSRF validation and explicit confirmation;
- POST/Redirect/GET prevents accidental resubmission; and
- every result is a complete HTML representation with stable links to the
  record, object report, and raw public evidence where safe.

JavaScript is not required for hashing, signing, publication, verification, or
policy evaluation. It may later improve copy controls or progress presentation,
but the server remains authoritative. Long operations need bounded timeouts and
clear indeterminate outcomes rather than browser-managed workflow state.

## Failure and safety requirements

The integration must:

- reject issue requests without an authenticated attached Acorn;
- require a Record File with a supported, bounded byte size;
- recompute the complete digest immediately before signing;
- never log the `nsec`, session cookie, RPK, artifact bytes, or sensitive event
  metadata;
- preserve signed event ids when publication outcome is uncertain;
- avoid automatic republishing after a timeout;
- separate relay failures from cryptographic and policy failures;
- render malformed or hostile event content as escaped text;
- keep decrypted artifact responses and issue/verify pages non-cacheable; and
- preserve the existing execution-environment warning: Safebox Web necessarily
  sees the attached key and plaintext artifact while performing the request.

An operator controlling Safebox Web or its trusted execution environment could
substitute bytes, misuse the attached key, or misrepresent a report. The page
should expose complete digest and event identifiers so important results can be
checked with an independent OpenETR implementation.

## Component contract required before route implementation

The OpenETR component should expose typed asynchronous or safely bounded
operations equivalent to:

```python
issue_origin(
    artifact: bytes,
    signer_nsec: str,
    relays: list[str],
    profile: str,
    metadata: dict[str, str],
) -> IssueResult

verify_artifact(
    artifact: bytes,
    relays: list[str],
    policy: str,
) -> VerificationReport
```

The exact API belongs in OpenETR. Results should be structured objects rather
than console text and should expose raw signed events, event ids, acknowledgements,
exact readback, graph state, and warnings without private key material.

## Test strategy

### Safebox Web unit tests

- issue and verify routes require an authenticated session;
- issue GET is read-only and POST requires valid CSRF and confirmation;
- exact artifact bytes and the expected attached signer reach the component
  boundary without appearing in rendered output or logs;
- digest changes between confirmation and POST are rejected;
- duplicate origins and indeterminate publication are rendered distinctly;
- verification separates artifact, cryptographic, graph, policy, and
  recognition findings;
- unsafe relay destinations and oversized artifacts are rejected; and
- all event-controlled text is escaped.

### Component and live integration tests

- issue a disposable artifact to a controlled relay and verify exact readback;
- verify the same artifact through the OpenETR CLI and Python component;
- prove that a one-byte artifact change produces a different object and does
  not match the issued graph;
- exercise missing, malformed, competing, and broken-chain events;
- confirm behavior against an allowlisted third-party relay; and
- confirm that timeout handling never produces an automatic duplicate origin.

Live tests should use disposable signing keys and non-sensitive fixture files.
They must never publish a real passport, birth certificate, health record, or
other personal artifact digest.

## Phased implementation

1. **Stabilize the component boundary:** pin an installable OpenETR version,
   define bytes-oriented issue/verify results, and add interoperability fixtures.
2. **Read-only verification:** add the authenticated artifact-verification page
   before allowing publication.
3. **Explicit issuance:** add preflight, confirmation, signing, publication,
   exact readback, and indeterminate-result handling.
4. **Local linkage:** add optional encrypted Acorn companion metadata only if
   it materially improves navigation and recovery.
5. **Recognition adapters:** add selected community or institutional policies
   without changing the underlying signed evidence.
6. **Later lifecycle work:** consider attestation, transfer, encumbrance,
   discharge, redemption, and termination as separate reviewed capabilities.

### Initial read-only projection implemented

Safebox Web now contains a deliberately small `app.openetr` adapter as an
intermediate integration step. It does not depend on the OpenETR Python package
and it does not sign or publish anything. For an Acorn Record File, the
record page uses the complete plaintext SHA-256 value already authenticated by
Acorn as the object-wide `o` identifier. An on-demand hypermedia link then
queries the operator-configured relays for current origin kind `1415` and
control kind `1416` events.

The projection:

- validates every returned Nostr event signature before displaying it;
- selects the earliest signed origin when more than one origin exists;
- follows exact prior-event `e` links from that origin;
- requires an explicit `origin` tag, when present, to agree with the selected
  origin;
- queries the same configured relays for the origin signer's latest valid kind
  `0` profile and presents selected recognition metadata;
- combines the origin and issuer into one human-first presentation: issuer
  display name, name, description, and other populated profile claims appear
  before the statement made about the Record File at anchoring, while event
  identifiers, kinds, signer key, object digest, and profile-event metadata
  follow under protocol details;
- derives an operator-configured durable verifier link from the complete
  object digest and presents the identical URL as a clickable link and
  server-rendered QR code in a collapsible section;
- displays the origin and related control events without making a recognition
  or legal-effect claim; and
- reports competing origins, broken or orphaned chains, invalid signatures,
  and relay failures as distinct cautions.

The lookup is intentionally on demand so merely opening a private record does
not contact public OpenETR infrastructure. Expanding the pane presents a normal
server-rendered link; following it reloads the record with the history pane
open. No client-side graph logic or OpenETR key material is introduced.

Issuer profile enrichment is recognition context rather than control evidence.
The profile event must have a valid signature from the exact origin key. Its
display name, name, description, and safe HTTP(S) links may help a reader
recognize that key, but self-published NIP-05, Lightning-address, website, and
profile-image values remain claims unless separately resolved or verified. A
missing or malformed profile does not invalidate or hide an otherwise valid
origin event.

Operators configure the experimental query boundary with:

```dotenv
SAFEBOX_OPENETR_RELAYS=wss://relay.openetr.org
SAFEBOX_OPENETR_PUBLIC_BASE_URL=https://openetr.org/etr
SAFEBOX_OPENETR_QUERY_TIMEOUT_SECONDS=5
SAFEBOX_OPENETR_QUERY_LIMIT=100
```

This implementation is a proving surface, not the final OpenETR component
boundary described above. In particular it does not yet apply full structural
or recognition policy, resolve profiles for later control-event participants,
derive an authoritative current controller across ambiguous branches, support
legacy kinds, or issue events. Experience with this projection should
determine whether the mature query implementation is imported as a package or
retained behind a small, versioned compatibility module.

WebSockets, record transmission, live subscriptions, and peer-to-peer workflow
coordination should receive their own design decisions. Issue and verify remain
useful, interoperable operations without them.

## Decision summary

Safebox Web should become a thin OpenETR issue-and-verify adapter, not a second
OpenETR implementation. Acorn safeguards the key and artifact. OpenETR creates
and verifies the portable signed control evidence. Safebox Web joins them for
one authenticated request and presents the difference between cryptographic
evidence, derived control state, and external recognition clearly to the user.
