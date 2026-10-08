# NIST SP 1800-42A and Safebox Web

## Executive assessment

**Assessment date: 8 October 2026.** This analysis reviews the March 2026 initial public draft of *NIST SP 1800-42A, Digital Identities—Mobile Driver’s License (mDL): Accelerating Development and Adoption of Digital Identity for Financial Institutions*, against the inspected Safebox Web and Safebox Acorn source. The accompanying [policy brief](NIST-SP-1800-42A-POLICY-BRIEF.md) presents the decisions for product leaders, institutional partners, and policymakers.

Safebox has a credible role in this ecosystem, but it is not currently the high-assurance mDL wallet or online mDL verifier demonstrated by NIST. Its strongest contribution is portable, user-controlled continuity for encrypted records and funds, coupled with explicit disclosure actions and inspectable OpenETR evidence. Its principal gaps are live credential presentation, authoritative issuer trust, holder binding, credential status, phishing-resistant authentication, and institution-grade accountability.

The appropriate direction is **composition rather than substitution**: retain Acorn's continuity model; integrate a separately bounded, standards-based credential-verification capability; keep high-assurance presentation keys distinct from recoverable Acorn keys; and leave institutional recognition, retention, and transaction approval with the responsible institution.

Three distinctions govern the assessment:

1. A signed artifact is not necessarily an authentic government credential.
2. Control of an Acorn key or browser session does not prove that the controller is the person named in a credential.
3. Recovering a credential file does not recover the authority to present it through a non-exportable device key.

No claim of NIST certification, a NIST assurance level, regulatory compliance, or deployment security follows from this review.

## Source and assessment boundaries

The supplied PDF contains 100 physical pages. References below use its **printed page numbers**; printed page 1 is PDF page 12. The main source is the supplied draft, including its architecture, threat model, privacy and usability discussions, and appendices. The [official publication record](https://csrc.nist.gov/pubs/sp/1800/42/ipd) identifies it as an initial public draft, published 18 March 2026, with the comment period closed on 8 May 2026. The [official PDF](https://www.nccoe.nist.gov/sites/default/files/2026-03/nist-sp-1800-42a-ipd_0.pdf) provides a shareable source.

This is a source-based architectural assessment, not a penetration test, deployment audit, complete dependency review, or formal standards-conformance evaluation. Statements marked implemented refer to inspected code, not independently observed production controls. Proposed designs are not counted as implemented functionality.

The inspected repository snapshots were:

- Safebox Web: `6c4250631e55a23dc4e7c9bebf80cbe38dd93974`.
- Safebox Acorn: `2b20845816615a71d6577aa130a0550972669dc7`.

The NIST guide concerns U.S. financial-institution identity proofing. It is not automatically a legal framework for every Safebox operator or deployment. Canadian, community, cross-border, and other uses require their own applicability analysis.

## What NIST demonstrates

### Three distinct journeys

NIST separates account application, digital enrollment, and high-risk transaction authorization (§6.1, p.14; §6.4, pp.23–37).

During application, the applicant presents issuer-signed identity information. The bank combines credential verification with additional information and checks. In the demonstration, the Social Security Number service is simulated; the mDL does not supply every required attribute.

After approval, digital enrollment links the customer to the approved application and provisions a passkey. Where enrollment takes place in a later session, a fresh mDL presentation re-establishes the link. Routine access subsequently uses the passkey, not repeated disclosure of the driver's license.

For high-risk transactions, mDL re-verification provides an additional signal. It does not replace the financial institution's risk assessment or independently authorize the transaction.

This separation is directly useful for Safebox. Connecting an Acorn, proving a person's identity for a specific service, and approving a funds transfer should not become one undifferentiated login action.

### A system of cooperating roles

The reference architecture includes a government issuer, approved mobile wallets, an issuer-key trust service, a verifier, an identity-management system, mediation services, and banking systems (Table 2, pp.20–21; Figure 4, p.22). Its verification assurance does not come from a file format alone. It depends on the relationship between issuance, keys, presentation, holder authentication, and the relying party's policies.

The centralized verifier and identity-management system are deployment choices made for the demonstration, not a universal instruction to centralize all wallet state. NIST discusses both SaaS and institution-controlled alternatives, including their different trust boundaries (§6.2.1–6.2.2, pp.14–16).

### Protocol and platform mediation

The demonstrated cross-device path uses OpenID4VP, Digital Credential Query Language (DCQL), the browser Digital Credentials API, and the FIDO CTAP proximity mechanism. The relying party supplies a fresh challenge; the wallet releases the requested attributes through the presentation protocol (§6.2.4, pp.16–17; §6.4.1, pp.25–28).

A QR code is only one visible step. NIST attributes phishing resistance to the surrounding origin, challenge, cryptographic, and proximity controls—not to QR encoding itself. Copying a file through a QR descriptor does not inherit those properties.

### Important limits of the demonstration

The draft is a voluntary practice guide, not a regulation (p.iii). It did not demonstrate mDL issuance, complete production banking controls, all KYC obligations, production retention periods, account recovery, or all presentation protocols. Same-device presentation was outside the initial build; ISO 18013-7 Annex C was excluded from its threat-model profile; HAIP was not implemented in its entirety (§2.1, p.3; p.5; §6.4.3, p.35; §6.5, p.37; p.41 footnotes 11–12).

Its usability study recruited 12 NIST staff not involved in the project (§9.1, p.58). That is useful formative research, not evidence that all populations or accessibility needs have been covered. Appendix D's comparative ratings are qualitative assessments, not measured fraud-reduction percentages or scores that can be transferred to Safebox.

## Current Safebox capabilities and gaps

| Area | Inspected Safebox position | Implication against the NIST example |
| --- | --- | --- |
| Encrypted record continuity | Implemented through Acorn records and encrypted attachment/transfer mechanisms | Useful custody and availability foundation; not identity proofing |
| Hosted key use | Session cookie contains encrypted bootstrap credentials; Web decrypts them to construct Acorn | The execution provider remains trusted with usable user authority |
| Web safeguards | Authenticated encryption, expiry, HTTP-only cookies, same-site policy, CSRF, origin checks, CSP, cache restrictions | Meaningful defenses; not device-bound holder authentication |
| mdoc handling | CBOR decoding and human-readable preview with an explicit non-verification warning | Not an mDL verifier or evidence of ISO presentation conformance |
| Record presentation | Temporary encrypted bearer descriptor, explicit initiation, expiry checks, stop/done cleanup | Not a fresh, recipient-bound OpenID4VP presentation |
| Attribute minimization | Separate Check, Present, and Share actions | Graduated disclosure exists; credential-level selective disclosure does not |
| OpenETR checking | Core Record Ruleset 1.0 anchor and publisher-notice evaluation | Proves narrowly defined signed statements about an artifact, not government issuer authority |
| Issuer trust | Signer profiles and other key/address metadata can be displayed | No inspected mDL issuer certificate trust-list and lifecycle integration |
| Credential status | Publisher notices and retrieval uncertainty are represented in OpenETR | No general mDL validity, device binding, or credential-status enforcement |
| Routine authentication | Acorn secret/mnemonic attachment followed by an encrypted session | No implemented WebAuthn account-authentication ceremony identified |
| Passkey convenience | Local vault is explicitly proposed, not implemented | Must not be counted as a deployed authentication control |
| Recovery | Recoverable Acorn authority and relay-backed state | Useful continuity, but not reissuance of a lost device-bound credential |
| Institutional records | Operational database and logs support app workflows | Not a CIP evidence repository or complete regulated audit trail |
| Accessibility and offline use | Responsive hypermedia and some alternative inputs; local infrastructure is a direction | Formal accessibility and independent offline credential assurance remain unproven |

### Key custody and sessions

`SessionCredentials` includes `nsec`, bootstrap relay, and optional recovery/protection material. `SessionCipher` encrypts and authenticates the payload using AES-256-GCM, with purpose separation and absolute expiry. The web dependency decrypts it and supplies `nsec` to Acorn. Some background jobs capture credentials in memory after initiation. These controls reduce persistent application-side storage but do not make the hosted operator unable to access keys. [S1, S2]

The default session lifetime is 30 days. This is an implementation default, not a NIST-recommended high-assurance lifetime. A deployment should choose inactivity, absolute lifetime, fresh authorization, and revocation controls according to risk. Disconnect removes the current browser cookie; it should not be represented as revoking every stolen copy or ending every previously authorized background operation. [S1, S2]

HTTPS is the default boundary, but the code also permits direct loopback development and an explicit insecure-HTTP override. A high-assurance deployment profile must prohibit that override and verify reverse-proxy trust, cookie attributes, and transport behavior in the deployed configuration. CSP and CSRF reduce web attack surfaces; neither proves the natural person's identity or prevents a malicious operator from changing the served application. [S1, S3]

The proposed OpenBao integration could improve management of operator secrets. It does not by itself make user keys non-exportable or remove plaintext from the process that uses them. Likewise, the proposed WebAuthn PRF vault would protect a local attachment bundle; unlocking that bundle is distinct from an RP-verified passkey authentication and from a hardware-attested credential presentation. [S8, S9]

### An mdoc preview is not credential verification

`_mdoc_preview` decodes CBOR and extracts document types, namespaces, and display fields. The template expressly warns that issuer signatures, device signatures, and digest bindings have not been verified. This is appropriate and should remain prominent. [S3, S4]

Before Safebox could claim to verify an mDL presentation, a defined verifier profile would need to check at least the issuer authentication structure, disclosed-element digest bindings, accepted issuer trust path, document type, validity constraints, fresh device/holder proof, and presentation/session binding. Status handling and the response to unavailable or stale trust material must also be defined. The exact checks should come from the selected standards profile and a tested implementation, not an improvised extension of the preview parser.

A previewed file may be useful for inspection or archival purposes. Even an authentic static credential container is not necessarily a usable presentation: it may lack a fresh proof bound to this verifier and this transaction.

### Temporary presentation is a bearer capability

Acorn's record descriptor carries a blob URL, ciphertext digest, secret, and expiry. The envelope can include the entire record payload and attachment. Its presentation variant changes the application capability to view-only; it does not bind the secret to a named recipient, verifier origin, or fresh verifier challenge. The Web flow requests a one-hour lifetime. [S3, S5]

Consequently, possession of the descriptor grants meaningful access. A screenshot, clipboard copy, compromised browser, or forwarded QR can disclose the capability. The legitimate viewer can still retain displayed plaintext. Removing an import button is not cryptographic prevention of copying.

Expiry is checked by the cooperating decoder; it does not make a previously obtained decryption secret stop working against copied ciphertext. Stop Presenting and Done attempt deletion of the temporary server copy and report uncertain outcomes. They cannot recall downloaded material or guarantee deletion by every recipient or replica. These are architectural properties, not evidence that the existing flow is unsuitable for its intended temporary-sharing purpose. They do mean it must not be described as the NIST mDL presentation flow.

### OpenETR is complementary evidence

The implemented `openetr:core-record:1.0` evaluator separates anchored state from publisher position, validates event IDs/signatures and required tags, follows exact notice links, and reports conflicting or incomplete evidence. Recognition and effect remain outside the evaluator. [S6]

That is a useful precedent for a future credential verifier: report which evidence supports which conclusion. It is not a replacement for mDL verification. An arbitrary signing key can anchor the digest of a forged license image. A valid anchor then establishes a signed anchoring statement, not the authenticity of the depicted license or the authority of its publisher.

Nor does an OpenETR withdrawal automatically equal revocation of a government-issued mDL. An institution could adopt an explicit policy assigning a recognized publisher's notice a consequence, but it would have to define publisher authorization, credential mapping, freshness, conflict handling, and applicable semantics. No such general mapping should be inferred from the current ruleset.

The UI message “No subsequent publisher notices found in the retrieved evidence” means absence of qualifying notices in the observed set. It is not a clean bill of health or proof that no adverse notice exists. Bounded relay retrieval, unknown completeness, and signer-declared timestamps must remain visible.

## Where the architectures reinforce each other

NIST's example favors direct presentation of signed claims over reliance on document images and repeated data-broker queries. Safebox's record model supports retaining exact artifacts and signed evidence outside a single application database. Both approaches can reduce dependence on an intermediary's unsupported assertion about a record, provided the verifier still establishes the relevant trust and cryptographic checks.

Safebox's explicit Check, Present, and Share actions also provide a useful interaction structure for consent. A future credential flow can preserve these understandable steps while adding the actual requested-attribute list, verifier identity, purpose, and retention disclosure. It should not claim that the present UI already delivers claim-level selective disclosure.

Relay-backed continuity can preserve recovery instructions, evidence receipts, policy references, and reissuance context when an app or device disappears. This supplements an area that NIST's first demonstration did not implement: account recovery and credential continuity. Portability nevertheless depends on available records, intact recovery material, compatible execution, and the issuer's reissuance process.

Finally, NIST's recognition of separate verifier, identity-management, and institutional roles is compatible with Acorn's component boundary. A specialized verifier can be integrated without moving funds or all user records into a central identity service. A centralized enterprise verification service and a user-controlled continuity layer can coexist.

## Threat and privacy implications

The following are assessment priorities, not findings of successfully exploited vulnerabilities. They adapt NIST's threat model (§7, pp.39–50) to Safebox's actual trust boundaries.

| Threat | Existing contribution | Remaining concern and response |
| --- | --- | --- |
| Forged identity document | Exact-byte storage and OpenETR signatures expose some evidence | Verify the native credential and authorized issuer; never promote preview or anchoring to identity approval |
| Copied credential or descriptor | Encrypted blobs, explicit initiation, application expiry | Bearer descriptors do not establish holder binding; use fresh audience-bound presentation for identity |
| Phishing and QR relay | HTTPS, same-origin controls, confirmation screens | A normal QR scan has no demonstrated CTAP proximity protection; adopt a supported standards-mediated identity path |
| Session theft | HTTP-only, same-site, authenticated and expiring cookie | A stolen usable cookie can exercise authority; review shorter risk-tiered sessions, revocation, and fresh authorization |
| Malicious or compromised Web host | Less central wallet-state persistence | Host sees operational keys/plaintext; minimize exposure and isolate high-assurance key use |
| Replayed verifier result | Existing jobs have correlation concepts | Identity results need their own one-time nonce, audience, session, account, purpose, expiry, and transaction binding |
| Unrecognized or compromised issuer | Signed keys and profiles provide attribution | Establish governed issuer trust, key rotation/revocation, and fail-safe policy for stale or missing trust data |
| Hidden adverse publisher notice | OpenETR exposes incomplete retrieval and conflicts | Define admissible evidence age and source policy; do not infer positive status from missing evidence |
| Malicious verifier or overcollection | User-confirmed presentation | Authenticate the verifier and display exact claims and purpose; provide a real refusal/alternative path |
| Cross-service correlation | Encrypted payloads limit content exposure | Stable npub, handles, artifact digests, network and service metadata may join identity and financial activity |
| Device loss and reattachment | Recoverable Acorn key and relay-backed records | Possession of recovery material is powerful authority; separate credential reissuance from continuity recovery |
| Operator intervention | Operational repair and review are possible | Production identity/funds interventions need scoped authorization, reason records, separation of duties, and audit |
| Local service outage | Infrastructure can potentially be replaced or localized | Reachability is not current issuer/status assurance; label stale/unknown state and prevent assurance downgrade |

### Privacy beyond encryption

NIST distinguishes privacy harms from security breaches and identifies predictability, manageability, and disassociability as engineering objectives (§8.1, pp.51–52). This matters particularly for Safebox because the same Acorn can hold both records and financial value.

Encryption does not prevent a legitimate host from correlating a verified legal identity with wallet activity it processes. A privacy review should map the browser, Web process, worker, relays, blob servers, mints, verifier, issuer-trust/status services, proxies, logs, and backups. It should state which party sees plaintext, identifiers, purposes, timing, IP addresses, and retention metadata.

Recommended defaults are purpose-specific disclosure, separation of identity and payment records, minimal and short-lived verifier processing, and avoidance of the public Acorn npub as a universal credential subject identifier. Pairwise identifiers or scoped linkages should be considered where the chosen protocol and institutional requirements permit them. A hash of a stable document number and issuer is still a stable, potentially guessable identifier—not anonymization.

Do not publish personal credential digests or verification results to public relays by default. Exact artifact digests can become correlation handles, and predictable documents may permit guessing attacks. Even encrypted disclosure receipts can reveal metadata; their replication and access policy require explicit design.

### Local verification and biometrics

NIST chose local holder authentication rather than default server-side selfie comparison, while acknowledging shared-device and other residual risks (§6.2.8, pp.18–19). Safebox should follow the design discipline, not overgeneralize the outcome. Local device unlock does not necessarily distinguish the credential subject from every person enrolled on a shared device, and a copied web session is weaker still.

Prefer verifiable local authorization where supported. Any proposed server-side biometric collection should require a documented necessity assessment, alternatives, retention controls, and privacy review. It should not be added merely to compensate for an undefined trust model.

## Policy and institutional accountability

### Keep ownership of obligations explicit

An ordinary Safebox installation is not automatically a financial institution because it handles Bitcoin or Clear credits. Conversely, using user-controlled storage does not exempt a deployment from obligations that do apply. Relevant questions are who provides the service, which jurisdiction and activities are involved, and who makes the identity or financial decision.

For an institutional integration, distinguish three record domains:

1. **Holder continuity:** encrypted user-controlled records and receipts maintained through Acorn.
2. **Verification processing:** minimized temporary inputs and results managed by the verifier under a defined purpose and deletion schedule.
3. **Institutional accountability:** required identifying information, decision evidence, discrepancy resolution, and retention records controlled by the responsible institution.

These domains can exchange authenticated results without sharing one database or one recovery key. A user's deletion action cannot promise deletion of records an institution is required to retain; equally, the institution's duty does not justify indefinite retention by every intermediary.

### Correct the retention ambiguity before implementation

The draft discusses five-year retention in §6.5, p.37, but Table 7, p.56, states seven years after account closure. Appendix C, p.74, gives the more specific five-year rules and marks retention as not demonstrated.

The [Federal Reserve's published text of 31 CFR 1020.220(a)(3)(ii)](https://www.federalreserve.gov/frrs/regulations/section-1020220-customer-identification-program-requirements-for-banks.htm), checked on the assessment date, distinguishes identifying information retained for five years after account closure—with a credit-card dormancy provision—from verification-description, methods/results, and discrepancy records retained for five years after creation. A deployment needs a record-class schedule, not a blanket seven-year setting copied from the draft. Other applicable obligations and legal holds require separate determination by counsel.

The draft also labels some table content inconsistently. For traceability, this analysis uses sections, printed pages, and substantive content rather than relying solely on table captions.

### Do not turn a demonstration into a compliance claim

NIST expressly stops short of an authoritative CIP-compliance determination (§10.3, pp.63–64; Appendix C, p.68). Broader KYC, sanctions, ongoing monitoring, and many institutional processes are outside the demonstration. An authenticated mDL result cannot be treated as completion of all those duties.

Its use of centralized identity management is not a mandate for a universal identity account, and its example account-linking identifier is not a recommendation to disclose a driver's license number on every login. Safebox should preserve these distinctions in partner and public communications.

## Recommended integration model

The first prototype should make Safebox an application consuming a well-defined credential-verification result, not attempt simultaneously to become a government credential issuer, a certified device wallet, and an institutional identity system.

| Component | Proposed responsibility | Explicit exclusion |
| --- | --- | --- |
| Native credential wallet or supported platform | Protect credential presentation key and obtain local user authorization | Exporting that key into the Acorn recovery bundle |
| Scoped browser adapter | Invoke supported Digital Credentials API interaction and convey the response | Deciding trust or transaction success in JavaScript |
| Standards-based verifier | Validate presentation, issuer trust, disclosed data, freshness, and required holder/device proof | Deciding institutional legal recognition on its own |
| Safebox Web | Create request context, enforce consent and correlation, display separate findings | Treating a reusable “verified” flag as perpetual identity authorization |
| Acorn | Preserve permitted receipts, recovery context, records, and user authority | Reconstructing a supposedly non-exportable credential key |
| Institutional policy service | Decide acceptable issuers, assurance, purposes, status freshness, and action | Delegating responsibility merely because signatures validated |
| Institutional evidence store | Retain the records required for its decision and obligations | Requiring every intermediate service to keep a full credential copy |

This can remain compatible with server-rendered hypermedia. A narrowly scoped browser API call is a device/protocol interface; the server can still own workflow, validation, and outcomes. It will require a deliberate exception to the current browser boundary, comparable in scope discipline—not in security function—to the proposed local vault. Unsupported browsers need an explicit alternative rather than silent fallback to an unbound document upload under the same assurance label. [S7]

### Keep four authorities separate

- The **Acorn continuity key** authorizes portable encrypted state and component operations.
- A **credential presentation key** proves possession in the credential protocol and may be non-exportable.
- A **passkey** authenticates access to a particular relying party; its synchronization and assurance properties need explicit policy.
- An **issuer or institutional key** signs credential claims, verification receipts, or decisions under that organization's governance.

The optional Record Protection Key is a further separation of recovery material, not evidence that the credential presentation key is hardware-protected. The inspected activation function publishes capability status and a fingerprint; it should not be cited as proof of a complete independent record-encryption boundary. [S5, S10]

### Bind results to the action

A proposed verification result should identify the request and one-time challenge, intended audience, protocol/profile version, accepted issuer, policy version, minimally disclosed claims, validity/status findings, holder-proof findings, observation time, and expiry. Bind an identity-sensitive payment approval to the actual account, recipient, amount, and operation—not simply to a session that was “recently verified.”

The receiving server must authenticate results, enforce audience and freshness, consume the request only once where appropriate, and distinguish failure from unavailable or unsupported verification. Persist only the evidence required by the role. A signed result is not trustworthy if its signing service is compromised; governance, least privilege, and operational monitoring remain necessary.

## Prioritized work and acceptance gates

These phases are a proposed sequence, not a delivery estimate or authorization to implement.

| Priority | Work | Owner role | Exit evidence |
| --- | --- | --- | --- |
| P0 | Publish a capability and claims register separating preview, anchoring, credential verification, identity proofing, and authorization | Product and security | Each UI/partner claim maps to an implemented check and named limitation |
| P0 | Define pilot role, jurisdiction, data inventory, trust boundaries, risk cases, and prohibited configurations | Product, operator, privacy/legal | Approved threat/privacy model; no real credential data before approval |
| P1 | Integrate one pinned presentation profile with test issuer credentials | Credential engineering | Valid presentation accepted; tampering, wrong issuer, replay, wrong audience, expired proof, and missing required checks rejected |
| P1 | Define issuer trust and freshness/status policy | Institutional partner and verifier owner | Versioned trust configuration, rotation/compromise exercises, explicit unavailable behavior |
| P1 | Design authentication and action-specific step-up separately from vault convenience | Security and Web engineering | Enrollment/recovery binding, fresh authorization, session-revocation tests, and no reliance on a generic verified boolean |
| P2 | Implement attribute-specific consent and minimized receipts | Product and privacy | Disclosed claims match approved request; canceled requests release nothing; no unnecessary identity/payment joins |
| P2 | Exercise loss, reissuance, provider failure, and incomplete relay recovery | Acorn, native-wallet partner, operations | Documented recovery limits; new device credential binding; old authority handled by policy |
| P2 | Test usability, accessibility, and multiple supported devices/wallets | UX and assurance | Measured completion/errors/help needs; assistive-technology and alternative-path results |
| P3 | Establish institutional audit, retention, incident response, and supplier assurance | Institutional/operator governance | Access-controlled evidence, retention tests, incident drills, scoped operator interventions, external review |

### A bounded first experiment

Use synthetic identities and a test issuer. A user connects a test Acorn, starts a clearly labeled identity experiment, and approves a minimal request from a known test verifier. A separate supported wallet performs the standards-based presentation. Safebox validates the bound result and offers an encrypted receipt in Acorn containing only the agreed evidence and context. Do not send real funds, use production government credentials, or label the result a completed CIP process.

Test: valid and expired credentials; modified attributes; unknown and rotated issuers; wrong device proof; replay; wrong origin/audience; copied or substituted QR; canceled consent; verifier timeout; stale trust material; changed payment details; interrupted browser sessions; and loss/replacement of the credential device. Where the selected profile does not support a required property, report unsupported and stop that assurance path.

Measure completion and abandonment, help required, consent comprehension, verifier latency, data released, data retained, failure-mode clarity, and whether users distinguish preview from verification. A recovered Acorn on a second device must not be able to impersonate the original non-exportable credential key merely because the stored file survived.

## Questions for standards and policy engagement

Safebox could contribute useful evidence on four underdeveloped boundaries:

- How can user-held encrypted receipts support continuity without becoming replayable identity assertions or tracking artifacts?
- How should a portable wallet preserve recovery context while a high-assurance credential must be reissued to a new device key?
- How should relying parties communicate incomplete or stale evidence when local infrastructure remains usable but external trust/status services are unavailable?
- What minimal interoperability contract allows a replaceable verifier to serve a user-controlled wallet without collecting the wallet's unrelated funds and records?

These are contributions to a pilot and standards discussion, not grounds to claim that a portable private key replaces issuer, wallet, or holder assurance.

## Evidence register and validation

The following local sources ground the implementation findings. Source symbols are included so references remain useful if line numbers move.

- **S1 — Session custody and defaults:** [app/security.py](../app/security.py), `SessionCredentials`, `SessionCipher`, `is_allowed_transport`, `set_session_cookie`; [app/config.py](../app/config.py), session defaults and insecure transport settings.
- **S2 — Hosted execution and jobs:** [app/dependencies.py](../app/dependencies.py), `get_session_credentials`, `build_acorn`, `get_background_acorn_factory`.
- **S3 — Web boundaries and record flows:** [app/main.py](../app/main.py), `security_boundary`, `_mdoc_preview`, `create_record_presentation`, `stop_record_presentation`, `finish_record_presentation`, and share/import routes.
- **S4 — Preview disclosure:** [app/templates/record.html](../app/templates/record.html), mdoc preview warning.
- **S5 — Acorn record mechanisms:** sibling repository `safebox-acorn/acorn/record_transfer.py`, `RecordTransferDescriptor`, envelope encryption/decryption and expiry checks; `safebox-acorn/acorn/acorn.py`, `create_record_transfer`, `create_record_presentation`, `inspect_record_presentation`, `delete_record_transfer`, `activate_record_protection`.
- **S6 — OpenETR:** [app/openetr_rules.py](../app/openetr_rules.py), `evaluate_core_record`; [app/openetr.py](../app/openetr.py), bounded retrieval and display adaptation; [ruleset implementation note](OPENETR-ANCHOR-STANDING-TERMINOLOGY-MIGRATION-NOTE.md).
- **S7 — Browser architecture:** [Hypermedia architecture](HYPERMEDIA-ARCHITECTURE.md) and [PWA boundary](PWA-HYPERMEDIA-BOUNDARY.md).
- **S8 — Proposed vault:** [Local Acorn Vault Design Note](LOCAL-ACORN-VAULT-DESIGN-NOTE.md), expressly not implemented.
- **S9 — Proposed operator-secret management:** [OpenBao Integration Note](OPENBAO-INTEGRATION-NOTE.md), expressly not a completed integration.
- **S10 — Related assessments:** [Android high-assurance and continuity analysis](ANDROID-HIGH-ASSURANCE-CREDENTIALS-AND-RELAY-BACKED-CONTINUITY.md) and [draft DGSI assessment](DRAFT-DGSI-103-4-CONFORMANCE-ASSESSMENT.md). These provide context, not independent certification; current source takes precedence over older descriptions.

Focused existing tests completed on the assessment date: **92 passed, 325 deselected**, using the sibling Acorn source. Selection covered OpenETR, mdoc, record-presentation/share, cookie, CSRF, origin, and session-related cases from `tests/test_openetr.py` and `tests/test_app.py`. These are regression results, not interoperability, penetration, production-configuration, or conformance evidence.

The current README retains older descriptions of OpenETR and receipt behavior in places. This assessment therefore relies on the inspected functions and tests rather than treating every README feature description as current.

## Overall conclusion

Safebox should not try to win this comparison by calling portable state equivalent to high-assurance identity. Its stronger position is that high-assurance identity and user-controlled continuity solve different problems and should interoperate.

NIST supplies a practical reference for trustworthy presentation and institutional use. Safebox can add a portable continuity and evidence layer, provided it implements or integrates the missing credential checks, preserves key and role separation, and is candid about hosted execution, metadata exposure, incomplete retrieval, and recovery limits. That is a defensible direction for research and a bounded prototype—not yet a production assurance claim.
