# Safebox Web Alignment with Draft DGSI 103 Part 4

## Purpose and conclusion

This note assesses the current Safebox Web implementation against the private
working draft `DRAFT-DGSI-103-4-D5-2026-09-04.docx`, titled *Digital trust and
identity Part 4 Digital Wallets*. It is an engineering gap analysis, not a
certification, legal opinion, or declaration of conformance. The source is a
draft and may change before publication.

Safebox Web substantially aligns with the draft's direction in several areas:
it establishes a distinct cryptographic wallet instance, gives the controller
affirmative control over consequential actions, keeps ordinary wallet state on
portable protocol infrastructure, provides human-readable records and
transaction outcomes, supports multiple open protocols, minimizes server-side
session data, and documents important trust boundaries and residual risks.

Safebox Web does not yet satisfy every requirement of the draft core-wallet or
cloud and hybrid profiles. The largest gaps are formal cryptographic-module and
key-protection assurance, complete wallet lifecycle states, holder binding
proportionate to assurance level, credential status management, real-time
proof-of-possession presentations, selective and derived disclosure,
delegation, multi-device management, complete English and French coverage,
WCAG 2.2 Level AA evidence, integrity-protected audit records, formal
monitoring and incident procedures, and a repeatable conformance evidence
package.

The accurate present statement is therefore:

> Safebox Web is architecturally aligned with many requirements in draft DGSI
> 103 Part 4 and provides implementation evidence for a meaningful subset, but
> it has not been assessed or certified as conformant.

## Assessment boundary

The proposed object of conformity is a defined Safebox Web deployment, not the
Python application in isolation.

```text
browser
    -> trusted TLS reverse proxy
    -> Safebox Web
        -> Safebox Acorn protocol component
        -> configured Nostr relays
        -> configured Cashu or Clear mints
        -> configured Grove or Blossom servers
        -> OpenETR evidence relays and recognition policies
        -> singleton service Acorn worker for provider operations
```

Within this boundary:

- **Safebox Web** owns the hypermedia interface, session-cookie protection,
  request authorization, user confirmations, application database, public
  handle service, background-job coordination, and presentation of results.
- **Safebox Acorn** owns the component key, relay-backed wallet and record
  operations, proof management, encryption, recovery derivation, and protocol
  invariants.
- **External relays, mints, Blossom servers, Lightning infrastructure,
  OpenETR services, the reverse proxy, browser, operating system, and container
  host** remain material dependencies. Their behavior is not made conformant
  merely because Safebox Web interoperates with them.

Safebox Web is closest to a hybrid or remote wallet deployment. The browser
holds an encrypted session capability, while the running web process decrypts
the attached Acorn key during authorized requests and executes wallet
operations through Acorn. Relay-backed state and external services make the
deployment distributed. It should not be described as a local-native wallet or
hardware wallet without a separate deployment profile and evidence.

## Vocabulary mapping

The draft uses *digital wallet* and *digital credential*. Safebox deliberately
uses the more direct primitives *keys, balances, and records*.

| Draft concept | Safebox interpretation |
| --- | --- |
| Wallet instance | One Acorn component controlled by a distinct Nostr keypair and configured bootstrap relay |
| Holder component | Safebox Web together with the attached Acorn instance and its authorized browser session |
| Digital credential | A supported kind of record containing or referencing issuer assertions and evidence |
| Other digitally represented object of value | Cashu proofs, Clear mint notes, transaction records, and non-credential records |
| Holder | The person or organization controlling the session and Acorn key; this is not necessarily the subject of every record |
| Issuer | An actor whose key signs an assertion, credential, OpenETR Anchor Event, or related control event |
| Verifier | Safebox Web's OpenETR inspection surface plus the external policy or relying party that decides recognition and effect |
| Trust registry | An external source of issuer, key, status, authority, or policy information; NIP-05 alone is a domain assertion, not a complete trust registry |

This mapping is compatible with the draft's broad scope, which includes
credentials and other objects of value and does not mandate one protocol or
identity architecture. Safebox balances are within that broad wallet scope,
but payment clearing obligations remain outside the draft and need their own
requirements.

## Status scale

- **Implemented** means current code and tests provide direct evidence for the
  principal outcome within the stated boundary.
- **Partial** means Safebox implements part of the outcome, or the outcome
  depends on deployment controls or incomplete protocol work.
- **Dependency** means another component must satisfy the requirement and
  Safebox Web must retain end-to-end evidence.
- **Not implemented** means the capability is absent or only described as
  future work.
- **Not applicable** means the role or optional function is outside the current
  claimed profile.

## Clause-level assessment

### Clause 4 Objects of conformity and conformance

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Defined object and boundary | Partial | The README and design notes document browser, application, Acorn, provider-worker, relay, mint, Blossom, reverse-proxy, and key boundaries. A release-specific object-of-conformity statement is still required. |
| Profile selection | Not implemented | No formal core, cloud, hybrid, offline, hardware, or delegation profile is declared. The likely starting claim is core plus applicable cloud and hybrid requirements. |
| Machine-readable capability statement | Not implemented | Protocols and formats are documented, but there is no versioned machine-readable manifest covering data models, proof formats, suites, identifier methods, status mechanisms, and extensions. |
| Typed and protected interchange | Partial | FastAPI validation, bounded fields, signed Nostr events, authenticated cookies, Cashu structures, OpenETR validation, and explicit parsers provide evidence. A systematic parser-differential and duplicate-field review is still needed. |
| Repeatable conformance evidence | Partial | The repository contains a large automated test suite and design notes. Tests are not yet organized into a clause-traceable package recording environment, configuration, expected result, actual result, deviation, and evidence. |

### Clause 5 Wallet ecosystem and roles

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Trust boundaries and data flows | Implemented | The stateless-session, service-worker, reverse-proxy, OpenBao, hypermedia, OpenETR, and deployment notes describe the principal boundaries and dependencies. |
| Separation of functions | Partial | FastAPI dependencies keep record, fund, deposit, receipt, and service-Acorn responsibilities explicit. Acorn is the protocol kernel and the service wallet runs separately. The web process can still decrypt user keys, and the current shared data volume is not a strict filesystem boundary. |
| Authoritative enforcement and secure failure | Partial | CSRF, origin checks, TLS enforcement, authenticated cookies, relay readback, mint verification, timeouts, explicit indeterminate outcomes, leases, and idempotent jobs provide meaningful controls. Some provider recovery, acknowledgement, expiry, and operator-review paths remain incomplete. |
| Holder control | Partial | Material actions generally require an authenticated session and affirmative POST confirmation. Possession of the browser session is currently the main authorization factor; risk-tiered or multi-factor holder authentication is not implemented. |
| Wallet-provider transparency | Partial | Documentation identifies architecture, data processing, recovery, portability, dependencies, secrets, and residual risks. A release support period, update policy, vulnerability process, and service commitments are not yet complete. |
| Embedded-wallet isolation | Partial | Acorn is a separate component with documented interfaces, but Safebox Web necessarily receives the `nsec` from the encrypted cookie in plaintext process memory. This does not meet a high-assurance interpretation of host-application isolation. |
| Transaction correlation and endpoint binding | Partial | Record transfers, presentations, deposits, outgoing transfers, provider payments, and background jobs use nonces, event IDs, quote IDs, job IDs, expiry, and replay-aware processing. There is not yet one uniform correlation contract covering every wallet-mediated interaction. |
| Trust-ecosystem separation | Partial | The UI and OpenETR model distinguish cryptographic evidence from issuer recognition, policy, and legal effect. Explicit ecosystem policy mapping, assurance comparison, and downgrade prevention are not implemented. |
| Cross-application correlation control | Gap | Acorn intentionally uses a stable public key for continuity. Pairwise or context-specific identifiers are not currently supported, and disclosure of the stable `npub` needs clearer transaction-level communication. |

### Clause 6 Wallet lifecycle

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Instantiation | Partial | Acorn creation establishes a fresh keypair, 12- or 24-word mnemonic, chosen relay and mint, initial relay-backed state, and readback verification. Externally supplied entropy is validated. A formal auditable creation event and software-package authenticity control are not part of the app. |
| Privacy-preserving defaults | Partial | No credential telemetry or analytics is enabled by default, cookies are narrowly scoped, pages carrying secrets use `no-store`, and external conveniences can be disabled. Stable public keys and configured external services still create correlation considerations. |
| Provisioning and holder binding | Partial | Connecting requires control of an `nsec` or valid recovery mnemonic. This proves control of the component key but does not establish a natural person's identity or a standardized level of assurance. |
| Recovery | Partial | Safebox supports offline Acorn mnemonics, deferred backup, externally supplied entropy, and a separate protected-record mnemonic ceremony. Cookie loss before deferred backup remains an explicitly documented risk. There is no independent recovery authority or multi-factor recovery policy. |
| Presentation | Partial | Present and Share are separate, user-initiated flows with temporary encrypted artifacts, QR descriptors, nonce-derived protection, expiry, and stop controls. Presentation is not yet a general credential protocol bound to a verifier challenge, audience, requested claims, retention terms, and assurance policy. |
| Credential integrity and status | Partial | Exact record bytes, SHA-256 digests, signed OpenETR events, issuer profiles, control history, and candidate graph validation preserve useful provenance. Safebox does not yet implement a general valid, suspended, revoked, expired, unknown, and unsupported credential-status model. |
| Migration and portability | Partial | An Acorn can be reconstituted from its mnemonic or key and bootstrap relay without dependence on one app or device. Formal source-to-destination authentication, completeness checks, non-portable-item disclosure, rollback, and duplicate-control testing remain. |
| Delegation | Not implemented | Assisted onboarding is not a delegation framework. Scope, duration, permitted actions, revocation, and non-escalation controls are absent. |
| Decommissioning | Partial | Disconnect clears the browser session after recovery warnings and confirmation. It does not necessarily destroy relay records, invalidate all copied cookies, revoke credentials, close recovery channels, or create a formal decommissioning record. |

### Clause 7 Interoperability

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Protocol interoperability | Implemented with qualifications | Safebox Web uses documented Nostr, NIP-05, NIP-44 and NIP-59, Cashu, Clear, Blossom, LNURL, NIP-57, Silent Payments, PKPASS preview, DID Web experimentation, and OpenETR interfaces. Successful transport is correctly not treated as proof of trust or legal recognition. Supported versions and extensions still need a formal manifest. |
| Wallet-to-wallet transfer | Partial | Funds use recipient keys and encrypted gift-wrapped events; records use explicit QR-mediated transfer descriptors and encrypted temporary blobs. The record-sharing design does not yet provide a general mutual endpoint-authentication and schema/status-validation framework. |
| Issuer-holder interaction | Partial | OpenETR exposes signed origin evidence and issuer profiles, while Clear and Cashu bind notes to mints. General issuer authorization, credential offers, schema validation, holder binding, status, and issuance-policy enforcement are incomplete. |
| Holder-verifier interaction | Partial | Check and Present separate artifact inspection, signed evidence, and recognition. A standardized audience-bound presentation request and real-time holder proof are not yet implemented. |
| Trust-registry interaction | Partial | NIP-05 and OpenETR relay queries provide authenticated signed material and source context. Cache source, retrieval time, validity period, ecosystem, and stale-data policy are not consistently exposed for all trust data. |
| Cross-ecosystem and jurisdiction | Partial | Safebox preserves keys, mint URLs, units, event times, issuers, and exact record bytes, and avoids claiming that technical validity equals recognition. It lacks a formal cross-ecosystem assurance and legal-policy mapping engine. |
| Offline operation | Not implemented | The broader product direction includes local relays, local mints, Mainstay, and mesh continuity, but Safebox Web currently requires reachable application and protocol services. No offline-capable conformance claim should be made yet. |
| Cross-platform operation | Partial | Responsive server-rendered pages, progressive enhancement, multiple browsers, QR scanning, image/PDF rendering, and localized HTML provide a broad base. Formal equivalence, accessibility, browser limitation, and device-compromise testing are incomplete. |

### Clause 8 Operational requirements

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Protection of credentials, keys, tokens and records | Partial | AES-256-GCM protects session cookies; TLS is required except direct loopback development; ordinary records use NIP-44; attachments are encrypted before Blossom storage; the optional RPK separates protected-record authority. Secrets are nevertheless present in web-process memory during use and protected-record encryption remains incomplete. |
| Wallet lifecycle states | Gap | Safebox has creation, connected session, recovery, deferred-backup, and disconnect concepts, but not the required initialization, active, locked, suspended, recovery, and decommissioned state machine with authorized transitions. |
| Key generation and cryptographic boundary | Gap | Acorn generates secp256k1 keys from validated entropy and supports recovery. Safebox Web does not provide evidence of an assessed cryptographic module, hardware-backed protection, non-exportability, secure zeroization, key rotation, or cryptographic agility. The current recovery model intentionally permits authorized key reconstruction. |
| Real-time proof of possession | Gap | Signed Nostr and OpenETR events demonstrate key use, but credential presentation does not yet execute a fresh verifier challenge with a unique proof for every transaction. |
| Consent and disclosure | Partial | Accepting, presenting, sharing, deleting, disconnecting, enabling record protection, and consequential fund operations use explicit forms, CSRF protection, review screens, and confirmations. Full, selective, and derived disclosure are not all implemented. Safebox's graduated-disclosure model is a useful interaction policy but is not itself cryptographic selective disclosure. |
| Cloud and hybrid safeguards | Partial | Strong cookie authentication, session binding, tenant-specific key authority, TLS, encrypted state, process separation, leases, conflict handling, and visible degraded outcomes are present. Multi-factor authentication, device binding, privileged-administrator controls, regional data policy, and complete recovery controls remain. |
| Verifier requirements | Partial | OpenETR checking validates event IDs, signatures, graph structure, issuer information, and separates evidence from recognition. Complete credential status, holder binding, explicit verifier policy, and authenticated result binding are still developing. |
| Multi-wallet environments | Partial | Sessions are scoped to one Acorn and wallet data is selected by its key. Safebox Web does not offer a managed multi-wallet selector, per-device authorization, revocation, or full cross-wallet duplicate controls. |

### Clause 9 Accessibility and user experience

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Accessibility | Partial | Semantic server-rendered HTML, accessible labels, keyboard-compatible forms, responsive layouts, text-based alternatives, non-colour status text, and light/dark themes provide a foundation. No WCAG 2.2 Level AA audit or representative assistive-technology and user testing has been completed. |
| English and French | Gap | French localization infrastructure and catalogs exist, but only a bounded portion of the interface is translated. Material recovery, security, transaction, help, consent, and error content is not yet available with equivalent English and French quality. |
| Human readability | Implemented with qualifications | Records, attachments, issuer information, control history, balances, pending transfers, fees, transaction outcomes, and errors are rendered in human-readable pages. Some protocol and dependency errors still require normalization and contextual explanation. |
| Consistent and non-manipulative controls | Partial | The hypermedia design uses consistent forms, buttons, confirmations, and explicit consequences. A formal deceptive-design review and usability study have not been performed. |
| Notifications | Gap | In-application pending and completed transaction states exist, but there is no general holder-notification service for issuance, presentation, status, new-device binding, recovery, delegation, migration, or suspected compromise. |
| Error safety | Partial | User-facing errors are normalized and localized at presentation time, inputs are validated server-side, indeterminate outcomes are preserved, and durable jobs avoid blind replay. Coverage is not yet systematic across every external dependency and interrupted flow. |
| Assisted and inclusive access | Not implemented | Quick onboarding and Onboard a Friend improve convenience but do not define protected assistance, delegation separation, private review, or alternative channels. |

### Clause 10 Audit monitoring and operational resilience

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Holder transaction history | Implemented with qualifications | Cash and Clear histories show credits, debits, pending transfers, outcomes, tender, fees, balances, comments, and errors. Coverage of credential presentation, recovery, key use, permission changes, and administrative activity is incomplete. |
| Audit records | Gap | Application and worker logs support troubleshooting and avoid deliberate secret logging, but they are not an integrity-protected, access-controlled, retention-governed audit trail designed to reconstruct every security, privacy, and trust event. |
| Monitoring | Partial | Health endpoints, worker heartbeats, job phases, retry limits, leases, timeouts, status pages, and structured operational logs exist. Anomaly rules, alert thresholds, incident priority, and holder impact monitoring are not formalized. |
| Security-event recovery | Partial | Recovery mnemonics, explicit proof checking, conservative indeterminate states, worker recovery, and service-wallet migration provide useful mechanisms. There is no complete compromise playbook for suspension, key rotation, trusted-state restoration, impact communication, and credential reissuance. |
| Business continuity | Partial | Relay portability, replaceable infrastructure, local deployment, Mainstay integration, reciprocal resilience, and asynchronous reconciliation strongly support continuity. Formal recovery-time and recovery-point objectives, dependency outage plans, exercises, and tracked corrective actions are still required. |

### Clause 11 Security privacy and trust

| Area | Status | Safebox Web evidence and remaining work |
| --- | --- | --- |
| Fraud prevention | Partial | Cryptographic signatures, authenticated encryption, CSRF and origin checks, replay-aware descriptors, exact hashes, mint proof verification, recipient binding, confirmation, and fail-closed states provide layered controls. Coercion, social engineering, synthetic identity, fraudulent recovery, recourse, and disparate-impact controls are not comprehensively addressed. |
| Multi-device security | Gap | There is no device registry, individual device binding, trusted-channel notification, remote revocation, or secure synchronization policy for multiple devices. Relay-backed portability is not equivalent to multi-device authorization. |
| Privacy by design | Partial | The server stores no ordinary user session, user secrets stay in an encrypted browser cookie at rest, records and attachments are encrypted before external storage, telemetry is minimized, and OpenETR documentation warns about public digest correlation. Pairwise identifiers, complete selective disclosure, consent withdrawal, and a maintained privacy-impact assessment remain outstanding. |
| Cryptographic key management | Gap | Current mechanisms use AES-256-GCM, HKDF-SHA-256, Nostr secp256k1 signatures, NIP-44, random nonces, BIP39 recovery, attachment encryption, and optional independent record-protection entropy. Formal approved-algorithm policy, cryptographic-module validation, key rotation, revocation, destruction, HSM integration, and algorithm-transition plans are absent. |
| Continuous trust | Partial | Safebox performs live relay, mint, proof, event-signature, and graph checks at relevant operations and surfaces unavailable or indeterminate states. It does not yet implement governed continuous evaluation across credential, issuer, key, device, wallet-version, assurance, delegation, and compromise signals. |

## Strong alignment themes

### User control is attached to the component key

Safebox does not equate a key with a person's complete identity. The Acorn key
provides continuity and authority over controlled balances and records. Any
claim that the key represents a person, organization, role, or entitlement
comes from signed records, issuer evidence, domain assertions, recognition
policy, and the relying party's decision. This is consistent with the draft's
separation of holder, subject, issuer, verifier, trust registry, and relying
party roles.

### Portability is architectural rather than an export feature

An Acorn is reconstructed from user-controlled recovery material and
relay-backed state. It is not permanently bound to Safebox Web, one browser,
one device, or one infrastructure provider. This is strong evidence toward the
draft's migration and portability objective, although formal migration
transactions and completeness guarantees still need development.

### Graduated disclosure separates checking presenting and sharing

Safebox distinguishes three increasingly revealing actions:

1. **Check** examines signed origin and control evidence associated with the
   exact Record File.
2. **Present** temporarily exposes a human-readable record and its evidence
   without offering import.
3. **Share** authorizes a recipient to acquire the record through a temporary
   encrypted transfer.

This is a practical data-minimization model and places authorization at the
point of action. It does not yet satisfy the draft's full selective-disclosure
or derived-proof requirements because the present implementation generally
handles a whole record rather than cryptographically selected claims.

### Cryptographic validity remains separate from recognition

The OpenETR interface separates artifact matching, event signatures, graph
structure, derived control state, policy findings, and external recognition.
Safebox does not claim that a valid signature alone makes a passport, permit,
health record, or other artifact legally authentic. This is directly aligned
with the draft's distinction between cryptographic verification and a relying
party's business or legal acceptance.

### Pending and indeterminate outcomes remain visible

Funds received on a relay are shown as pending before mint finalization.
Outgoing and incoming operations use durable jobs, bounded retries, leases,
explicit phases, and transaction-history errors. Where publication or payment
may have occurred but cannot be verified, Safebox avoids reporting a false
failure or blindly repeating a consequential operation. This supports the
draft's requirements for accurate completion results, retry safety,
idempotency, and visible service unavailability.

## Priority work before making a conformance claim

1. **Declare a narrow profile and release boundary.** Identify the exact
   Safebox Web version, Acorn version, deployment architecture, roles,
   protocols, formats, dependencies, assumptions, and exclusions.
2. **Create a normative traceability matrix.** Give every applicable `shall`
   requirement a design reference, implementation reference, test identifier,
   result, and retained evidence.
3. **Resolve the key-protection profile.** Decide whether the target relies on
   platform key storage, passkeys, an HSM, a hardware Acorn, remote signing, or
   another assessed cryptographic boundary. Document how authorized recovery
   coexists with protection against unauthorized export.
4. **Implement formal lifecycle states.** Define initialization, active,
   locked, suspended, recovery, and decommissioned transitions, including
   session and recovery-channel invalidation.
5. **Define assurance and holder binding.** Add risk-based authentication and
   fresh transaction authorization appropriate to supported credential and
   value use cases.
6. **Complete credential presentation semantics.** Add audience-bound requests,
   verifier challenge, unique real-time proof, requested-claim review,
   retention information, replay resistance, and supported selective or
   derived disclosure.
7. **Complete status and trust data handling.** Model credential and issuer
   status separately and attach source, freshness, validity, ecosystem, and
   policy context to cached trust material.
8. **Complete accessibility and official-language evidence.** Perform a WCAG
   2.2 Level AA audit, assistive-technology testing, critical-flow user testing,
   and complete equivalent English and French translations.
9. **Establish audit and operations controls.** Add integrity protection,
   access policy, retention, correlation identifiers, monitoring, incident
   response, RTO/RPO, continuity exercises, and corrective-action tracking.
10. **Test dependency and adversarial cases systematically.** Expand negative
    coverage for malformed data, unsupported algorithms, invalid signatures,
    stale or revoked status, replay, clock variance, unavailable dependencies,
    interrupted migration, and unauthorized delegation.

## Evidence index

The principal current evidence includes:

- `README.md` for the application boundary, session design, recovery,
  deployment, secrets, tests, and residual risks;
- `app/security.py` for authenticated session encryption, expiry, validation,
  CSRF tokens, and cookie controls;
- `app/dependencies.py` for request-scoped Acorn construction and explicit
  operation boundaries;
- `app/main.py` and `app/templates/` for authorization, confirmations,
  hypermedia interaction, security headers, records, presentations, transfers,
  and human-readable results;
- `app/*_finalization.py`, `app/outgoing_payment.py`,
  `app/provider_payments.py`, and `app/models.py` for durable jobs, leases,
  retries, correlation, and transaction outcomes;
- `docs/HYPERMEDIA-ARCHITECTURE.md` and
  `docs/PWA-HYPERMEDIA-BOUNDARY.md` for server-authoritative interaction;
- `docs/CONCURRENCY-AND-JOB-COORDINATION.md` for multi-worker coordination;
- `docs/OPENETR-ISSUE-VERIFY-DESIGN-NOTE.md` for evidence, presentation,
  recognition, and privacy boundaries;
- `docs/LOCALIZATION.md` for language handling and current limitations;
- `docs/DEPLOYMENT.md`, `docs/DOCKER-PROXY-FORWARDED-HEADER-TRUST.md`,
  `docs/TAILSCALE-REVERSE-PROXY-DEPLOYMENT.md`, and
  `docs/OPENBAO-INTEGRATION-NOTE.md` for deployment trust and secret handling;
  and
- `tests/` for automated functional, negative, security, localization,
  hypermedia, concurrency, payment, record, and integration-boundary tests. At
  the time of this assessment, the local non-live suite passes 401 tests.

## Maintenance

This assessment should be updated whenever the DGSI draft changes materially,
the Safebox Web deployment boundary changes, a new credential format or wallet
role is claimed, or a major security, recovery, presentation, delegation,
accessibility, or audit capability is introduced. A dated copy of the source
draft used for each assessment should remain outside version control unless
redistribution is authorized.
