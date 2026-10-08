---
title: High Assurance Identity and User-Controlled Continuity
description: What NIST’s mDL practice guide means for Safebox, credential verification, portable records, and institutional accountability.
---

# High Assurance Identity and User-Controlled Continuity

**Policy brief | 8 October 2026**

**For:** Safebox product leaders, public-sector and financial-institution partners, and digital-wallet policymakers.

**Decision requested:** Support a bounded, synthetic-data prototype that connects standards-based digital-credential verification to Safebox's portable record architecture. Do not describe the current product as a high-assurance mDL wallet, a complete identity-proofing service, or a NIST-certified implementation.

## The policy issue

Digital identity systems must answer both an assurance question and a continuity question. Can a service trust this credential and its presentation for this purpose? Can the person retain access to their records when a device, application, or provider changes?

The March 2026 draft of NIST SP 1800-42A addresses the first question through an example use of mobile driver's licenses for financial-account opening, enrollment, and high-risk transactions. Safebox Web and Safebox Acorn offer a foundation for the second through recoverable cryptographic authority and relay-backed records. The opportunity is to connect them without confusing their guarantees.

NIST's guide is an initial public draft and a voluntary practice guide, not a regulation or a certification of participating products. Its official comment period has closed. [Official publication record](https://csrc.nist.gov/pubs/sp/1800/42/ipd).

## What the NIST work establishes

The NIST demonstration treats an mDL as signed information presented through a protected protocol, not as a photograph or an uploaded credential file. It combines issuer trust, credential validation, device/holder checks, consent, and a relying party's decision process. It also separates identity proofing from routine authentication: after enrollment, a passkey provides everyday access without repeatedly disclosing the driver's license. High-risk re-verification is an additional signal, not automatic permission to transact. [NIST draft, §§6.1–6.4, printed pp.14–37](https://www.nccoe.nist.gov/sites/default/files/2026-03/nist-sp-1800-42a-ipd_0.pdf).

The demonstration does not complete the full KYC process, establish every production control, or prove that every wallet and browser combination is equally safe. Its design choices are instructive, not a mandate to reproduce its centralized architecture or selected vendors.

## What Safebox contributes today

Safebox separates the interface from the Acorn component that manages user-controlled authority and relay-backed state. It provides encrypted record handling, explicit Check, Present, and Share actions, and a ruleset-based OpenETR check that distinguishes artifact anchoring from later publisher statements.

These capabilities can support portable records, transparent evidence inspection, and recovery across compatible environments. They reduce dependence on one application's database as the sole repository of a person's records. They do not eliminate dependence on available infrastructure, intact recovery material, or a trustworthy execution environment.

The current boundaries matter:

- Safebox previews mdoc content but does not verify a live mDL presentation.
- An OpenETR anchor proves a signed statement about an artifact, not the publisher's government authority or the holder's identity.
- Temporary record presentation uses a bearer capability, not a fresh verifier-bound identity proof.
- Hosted Safebox processes can access operational Acorn keys and plaintext during authorized use.
- Passkey vault and hardware-backed credential integrations remain proposals, not deployed assurance features.

The detailed [technical analysis](https://github.com/trbouma/safebox-web/blob/main/docs/NIST-SP-1800-42A-ANALYSIS.md) grounds these conclusions in the current source and identifies the implementation gaps.

## Recommended policy position

### Require evidence for each assurance claim

Products should distinguish file inspection, signature verification, issuer recognition, holder authentication, credential status, and authorization. A single green “verified” label should not imply that every layer has been checked. Unknown or unavailable evidence must remain visibly different from a positive result.

This is especially important where identity and funds share an interface. A completed identity check must not become unrestricted authority to move money or disclose unrelated records.

### Separate portable authority from device bound keys

Acorn recovery is designed to reconstruct portable authority. A high-assurance credential key may be deliberately non-exportable. Those properties should belong to different keys.

On device replacement, Acorn could recover records, receipts, and reissuance context, while the issuer rebinds or reissues the credential to a newly protected key. Policy should reward both continuity and resistance to key cloning rather than demand that one key provide contradictory guarantees.

### Minimize disclosure and correlation

The institution should request only the attributes needed for the stated purpose. Safebox should not expose an Acorn's stable public key as a universal legal-identity identifier or automatically join identity results to payment history.

Prefer local user verification when its assurance is appropriate. Do not make centralized biometric collection the default response to uncertainty. A privacy assessment should cover legitimate processing, service metadata, replicas, and backups as well as unauthorized access. Encryption alone does not prevent surveillance by an authorized processor. [NIST draft, §6.2.8 and §8, pp.18–19 and 51–57](https://www.nccoe.nist.gov/sites/default/files/2026-03/nist-sp-1800-42a-ipd_0.pdf).

### Keep institutional obligations with the institution

User-controlled records, temporary verifier processing, and mandatory institutional evidence should have separate custodians and retention rules. Portability does not remove an institution's accountability; institutional retention does not justify indefinite copies at every vendor or relay.

The NIST draft contains inconsistent retention wording. An implementation must use a legally reviewed record-class schedule rather than copy a blanket period from the guide. The detailed analysis explains the discrepancy and the different retention triggers in the underlying U.S. bank CIP rule. Applicability to a particular Safebox deployment must be assessed separately.

### Preserve accessible alternatives and honest recovery

An mDL-only pathway can exclude people without a compatible device, credential, or accessible interface. Pilots should retain alternative service routes and measure actual comprehension, completion, and support needs.

Recovery messages must also be precise. Recovering Acorn state is not the same as recovering a non-exportable credential key. Ending a presentation cannot recall plaintext already copied by a recipient. Local service availability does not prove that issuer or status evidence is current.

## A bounded next step

Begin with one defined use case, test issuer credentials, synthetic identities, and no real funds. Integrate a supported standards-based verifier rather than extending Safebox's preview parser into an improvised identity protocol. Keep institutional recognition and payment decisions server-side, with only a narrow browser interface for supported credential presentation.

Approval to advance beyond the prototype should require evidence that:

1. Invalid, replayed, expired, wrong-issuer, and wrong-audience presentations are rejected.
2. Credential and trust-service failures cannot silently downgrade the assurance claim.
3. Users can understand and refuse the exact disclosure requested.
4. Results are bound to the intended session, purpose, and consequential action.
5. Device loss, Acorn recovery, and credential reissuance remain separate, tested processes.
6. Data collection, retention, operator intervention, and audit responsibilities are agreed in advance.
7. Supported devices, accessibility needs, and alternative paths have been tested with representative users.

Product and security leaders should own the capability boundary; credential engineers should own interoperability; institutional partners should own recognition and applicable compliance; operators should own runtime controls; and privacy and UX specialists should own disclosure and inclusion evidence.

## Bottom line

Safebox's defensible proposition is not that a recoverable private key replaces high-assurance identity. It is that a person should be able to benefit from high-assurance identity without making one device, wallet application, or provider the permanent custodian of all their records and authority.

A standards-based verification interface, combined with user-controlled continuity and explicit institutional accountability, is a worthwhile research and prototyping direction. Production claims must wait for the missing controls and supporting evidence.
