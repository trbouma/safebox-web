---
title: Graduated Disclosure
description: How Safebox reveals only the evidence and record access appropriate to each decision.
---

# Graduated Disclosure

Safebox is designed around a practical idea:

> Publish the evidence necessary for verification, disclose the record only
> when the decision requires it, and retain it only when deeper verification
> is justified.

This is **graduated disclosure**. The holder and verifier can begin with minimal
evidence and move to deeper access only when the consequence of the decision
warrants it.

```text
evidence only
    -> public or temporary view
    -> presentation verification
    -> deep verification
```

Graduated disclosure complements selective disclosure. Selective disclosure
asks which attributes should be revealed. Graduated disclosure asks how far
the complete verification interaction needs to proceed.

## The Safebox Disclosure Path

| Stage | What happens |
| --- | --- |
| **Evidence only** | OpenETR carries a digest and signed statement while Acorn keeps the record private. |
| **Public record** | A less-sensitive record can be published so anyone can compare its exact bytes with the signed evidence. |
| **Present** | The holder displays the record and a QR code. The verifier temporarily inspects the record and its Control History without importing it. |
| **Deep verify** | The verifier receives the exact record and applies native-format, cryptographic, recognition, and policy checks in its own environment. |

Most interactions should stop at presentation verification. Deep verification
is available for secondary inspection, disputes, regulated decisions, or other
higher-consequence cases.

Receiving a copy does not transfer control or ownership. OpenETR control
changes require their own separately authorized and signed events.

## Sensitive And Public Records

A passport, birth certificate, health record, or sensitive legal document can
remain private while a recognized authority or notary publishes a signed event
for its digest. The holder can later present the exact artifact, allowing a
verifier to compare its bytes with the public evidence without making the
document permanently public.

A vendor permit, public licence, inspection certificate, or product record may
be intended for public display. In that case, both the artifact and signed
evidence can be available. Customers and inspectors can independently verify
the issuer and check for later replacement, suspension, or revocation events.

The disclosure policy belongs to the record's domain and participants—not to a
universal assumption that every record should be public or private.

## Present For Routine Verification

The Safebox **Present** action creates a temporary QR-mediated capability. A
verifier can:

1. view the human-readable record;
2. inspect its Original Record where available;
3. review its Control History;
4. follow the durable OpenETR verifier link; and
5. decide whether the signer is recognized for the claimed role.

The verifier does not need to import the record. The presenter can select
**Stop Presenting**, and the recipient can select **Done**, allowing the
temporary transfer object to be removed.

Presentation verification is not merely looking at a similar image. An exact
digest comparison requires the exact artifact bytes. Cropping, editing,
resaving, scanning, or recompressing a file normally changes its digest.

## Escalate To Deep Verification

When secondary inspection is justified, Safebox can share the exact record for
the verifier's own process. The verifier may then:

- retain the artifact where authorized;
- calculate its digest independently;
- validate native signatures, schemas, or package structure;
- inspect metadata or perform forensic analysis;
- query OpenETR evidence independently;
- consult registries or other recognition sources; and
- apply its own documented policy.

This makes verification proportional to risk instead of forcing every
interaction into either blind trust or a heavyweight record exchange.

[Understand Deep Verification](deep-verification.md){ .md-button .md-button--primary }

## Four Concrete Examples

### Passport Or Birth Certificate

A recognized authority or notary publishes only a signed digest. The holder
presents the document and QR evidence for routine inspection. A border,
benefits, legal, or investigative process can request the exact record when
secondary inspection is required.

The authority's signature establishes a statement about an exact artifact. It
does not alone establish that the presenter is the rightful holder. A
photograph comparison, component-key challenge, control event, or another
domain method may provide holder binding.

### Vendor Permit

The permit and its signed evidence can be public. Anyone can verify that the
displayed permit matches what a recognized authority issued and determine
whether later events changed its status.

### Insurance Damage Photograph

An adjuster or authorized agent attests to the digest of an exact photograph.
Any byte-level alteration breaks the match. Routine claims may use temporary
presentation; disputed or high-value claims can proceed to deep verification,
metadata review, and forensic analysis.

The digest proves integrity since attestation. The agent's signed statement and
the insurer's policy determine what is claimed about the scene and whether it
is accepted.

### Delivery Or Service Photograph

A provider or authorized worker attests to a delivery or service photograph
and gives portable evidence to the customer. The customer can verify that the
image is exactly what the provider attested instead of relying on a picture
available only inside the provider's application.

A valid image of the wrong doorstep remains evidence of what was photographed,
not automatic proof of correct delivery. Context and recognition still matter.

## What Verification Actually Establishes

Safebox keeps several questions visible:

| Question | Answered by |
| --- | --- |
| Are these the exact bytes? | Digest comparison |
| Which key made the statement? | Signature verification |
| What did the signer claim? | Event content and tags |
| Is the signer recognized? | Authority, registry, community, contractual, or legal policy |
| Is the record current? | Control and lifecycle events |
| Is the presenter the rightful holder? | Domain-appropriate holder binding |
| Should this verifier rely on it? | The verifier's own policy and surrounding facts |

Cryptography preserves the evidence. It does not silently decide authority,
truth, ownership, or legal effect.

## Privacy Still Requires Care

Hash-only publication minimizes content disclosure, but it is not anonymous.
A digest can become a stable correlation identifier, and event metadata may
reveal the signer, timing, or relationships. Predictable artifacts may also be
susceptible to candidate-guessing attacks.

Good implementations publish minimal metadata, restrict temporary record
access, define retention rules, and use stronger commitment or
selective-disclosure methods where the risk justifies their complexity.

## The Product Boundary

```text
Acorn       -> safeguards the key and exact record
Safebox Web -> presents, shares, and explains the workflow
OpenETR     -> supplies digest-bound evidence and Control History
Verifier    -> recognizes actors and decides effect
```

Safebox makes graduated disclosure usable. OpenETR makes the evidence portable.
Neither component needs to become the verifier's system of record.

[Read the canonical OpenETR Graduated Disclosure brief](https://trbouma.github.io/openetr/policy-briefs/graduated-disclosure/){ .md-button }
