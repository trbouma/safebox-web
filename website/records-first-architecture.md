---
title: Records-First Architecture
description: A records-first approach that separates artifact identity, native claims, signed evidence, consequential state, recognition, and verifier policy.
---

# Records-First Architecture

Most digital systems start with an application, an account, or a platform
database. Safebox starts with records.

That starting point is one contribution to a broader conversation. Timothy
Bouma's essay
[*The Niels Bohr Moment for Digital Architecture*](https://trbouma.substack.com/p/the-niels-bohr-moment-for-digital):
suggests that the next generation of digital systems may benefit from a better
vocabulary before it needs another diagram. The familiar language of users, applications,
databases, APIs, identity, authentication, and authorization still matters,
but it no longer explains everything our systems are being asked to do.

Digital records now replace paper documents. Software acts for organizations.
AI agents exercise delegated authority. Digital assets behave like property.
Legal rights can be represented entirely electronically. Once systems start
making decisions, exercising authority, and creating legal consequences, it is
reasonable to ask whether a database row and an audit log are enough of an
explanation.

The useful questions become:

```text
identity     -> who is participating?
intent       -> what are they trying to accomplish?
control      -> who can act on this object now?
recognition  -> what effect does a community or institution give the event?
evidence     -> why should anyone else believe it?
```

Safebox does not claim to finish that theory. It is an early working product
direction that takes those questions seriously and leaves room for many other
contributions.

There is also a historical prompt for the records-first turn. In
[*The Medieval Innovation We've Misunderstood*](https://trbouma.substack.com/p/the-medieval-innovation-weve-misunderstood),
Bouma suggests that credentials were not originally just portable identity
tokens. Medieval letters, charters, writs, seals, and bills of exchange can be
understood as
portable records: they carried authority, event history, permission, status,
and proof across distance.

The opportunity is to widen the question again. Alongside "who are you?", a
records-first system can ask:

```text
what happened?
who authorized it?
what changed hands?
what powers were granted?
what evidence travels with the record?
who can verify it away from the original issuer?
```

Safebox starts there: with portable records that can carry private control,
public evidence, and viewpoint-dependent recognition. Identity remains
important; it just does not have to carry every architectural responsibility
by itself.

That shift is more than a storage choice. It changes what the app is allowed to
be. Safebox Web is not the authority for the user's records, identity, funds,
or evidence. It is an app that helps a person use portable records controlled
by their own key, preserved through relays and blob stores, and connected to
signed public evidence when needed.

In plain language:

```text
wallet app     -> the thing a person uses
keys           -> what gives the person control
funds          -> value the person can hold or transfer
records        -> information and evidence the person needs to preserve
credentials    -> records carrying claims secured by native schemes
attestations   -> signed statements about exact artifacts or events
OpenETR        -> portable evidence from which consequential state is derived
```

[OpenETR](https://trbouma.github.io/openetr/) is an open protocol for deriving
consequential state from end-verifiable evidence concerning durable electronic
records. A cryptographic digest identifies the exact **Digital Artifact**.
Signed Anchor, control, and linked-evidence records form a candidate **Digital
Controllable Record (DCR)**. Defined rules determine what consequential state
follows from valid evidence, while recognition remains with the community,
institution, or relying party. This model can operate across PKPASS, W3C
Verifiable Credentials, EUDI PID, ISO mobile driving licences, PDFs, and record
schemes not yet anticipated without replacing their native proofs.

The governing discipline is simple: **each proof proves only what it proves.**
A digest identifies content. A signature attributes a statement to a key.
Protocol rules derive state from the available evidence. Recognition determines
what effect that state receives in a particular context.

The rewrite is not that credentials disappear. It is that credentials can be
understood as a specialized kind of record, while leaving space for other
record types with different lifecycles.

It is also not a claim that the world needs one new master architecture. The
point is to experiment with separating concerns that are often collapsed into
one product, one identity system, one credential format, or one platform
database.

## Records first

A records-first architecture treats a record as the object that must remain
usable through application changes, infrastructure transitions, migrations, and
changes in who is asked to recognize it.

In Safebox, a record can have several layers:

- a private record controlled by the user's Acorn key;
- an encrypted Record File stored through Grove or another Blossom server;
- relay-backed metadata and recovery state;
- native signatures, bindings, status, and presentations defined by the
  artifact's own scheme;
- transferable ecash proofs associated with the Acorn; and
- OpenETR DCR evidence and consequential state concerning an exact Digital
  Artifact.

The app becomes replaceable. The record remains portable.

## Beyond conventional verifiable credentials

Verifiable credentials are often presented as issuer-to-holder-to-verifier
messages. That model is useful, but many real-world records are not just static
claims about a subject. They move. They are amended. They are controlled,
transferred, encumbered, redeemed, revoked, replaced, or recognized differently
by different parties.

OpenETR generalizes this by identifying the exact Digital Artifact and
assembling signed evidence concerning it into a candidate DCR:

```text
Digital Artifact -> signed Anchor record -> DCR evidence -> consequential state
```

That turns verification into more than checking whether one issuer signed one
credential. A verifier can ask:

- Do these exact bytes match the object being evaluated?
- Which key anchored the object?
- What signed events followed?
- Who appears to control it now?
- Are there competing histories or unresolved branches?
- Which signers and roles does this verifier recognize for this purpose?

This is one way to extend the credential idea into transferable records and
evidence graphs.

## One evidence layer across record schemes

Safebox can preserve and render very different verifiable records without
forcing them into one universal credential format. Each scheme keeps the
verification rules that give its claims meaning. The exact Record File
also receives a **Uniform Digest Anchor (UDA)** that other protocols can use
without needing to understand every field inside it.

| Record scheme | Native verification remains responsible for | OpenETR can add around the exact artifact |
| --- | --- | --- |
| Apple Wallet PKPASS | Manifest integrity, pass signature, certificate chain, and Wallet behavior | Anchor, presentation, custody, or control evidence |
| W3C Verifiable Credential | Issuer proof, credential status, holder presentation, and scheme-specific policy | Digest-bound attestations, presentation events, or control evidence |
| EUDI PID and ISO mDL | COSE signatures, MSO digest bindings, device authentication, status, and trust lists | Independent inspection, custody, presentation, and lifecycle evidence |
| Signed PDF or other artifact | Embedded signatures, timestamps, revocation evidence, or format-specific rules | Cross-organization attestations and control events bound to the same bytes |

This creates interoperability without flattening. A verifier can first confirm
that the presented bytes match the UDA, then apply the artifact's native
verification, then evaluate any OpenETR evidence relevant to its own purpose.

```text
exact Record File
    -> Uniform Digest Anchor
       +-> native claim verification
       +-> OpenETR Anchor and linked evidence
       +-> OpenETR control events and consequential state
       +-> community recognition and verifier policy
```

## Native claims and OpenETR evidence

A native issuer signature and an OpenETR attestation may use the same
cryptographic primitive, but they do not make the same statement.

**Signing claims** means that an issuer, holder, or device key makes an
assertion inside a defined record scheme. A university may sign a degree claim.
A licensing authority may sign mDL identity and driving-privilege data. A
PKPASS signer may sign the package manifest. The signature authenticates the
signer's assertion and protects the scheme-defined content and bindings.

**Attesting to an artifact** means that a signer makes a separate statement
about an exact artifact or an event involving it. An attestation might state
that:

- these exact bytes existed at a stated time;
- the artifact matched a record inspected through another process;
- a recognized party presented, received, or held the artifact;
- a custody or transfer event occurred under a stated procedure; or
- an organization recognized the artifact for a specific purpose.

An attestation does not silently become a native issuer signature, establish
the signer's authority, or prove every claim embedded in the artifact. It adds
attributable evidence that a verifier may evaluate using its own recognition
inputs and rule book.

The distinction is semantic, not merely cryptographic:

```text
native signature -> "this key makes these scheme-defined claims"
attestation      -> "this key makes this statement about this exact artifact"
control record   -> "this key states that this consequential action occurred"
protocol rules   -> "this state follows from the validated DCR evidence"
recognition      -> "this relying party accepts this actor, state, and effect"
```

OpenETR can carry attestation and control records across otherwise incompatible
record schemes. The UDA supplies the common object reference; the signed event states
what is being attested; recognition and verifier policy determine whether that
attestation should be trusted for the decision at hand.

## What a wallet needs to preserve

A wallet is the app or environment a person uses. It is not the whole thing
that must remain portable.

People and communities need continuity for the resources inside and around the
wallet:

- keys that can recover authority;
- funds that can be spent or reconciled;
- records that can be read, moved, presented, and verified;
- evidence about where a record came from and what happened to it; and
- recognition rules that say who gives that evidence effect.

Safebox Web is a wallet app for those resources. It should be useful and
trustworthy, but it should not be the only place those resources can live.

## OpenETR in Safebox

Safebox Web can connect a private Acorn record to OpenETR without making the
private record public.

Acorn protects the user's key and private record. Grove can store the encrypted
Record File. Spurline or other Nostr relays can preserve events. OpenETR
identifies the exact Digital Artifact and preserves Anchor, control, and linked
evidence records in a candidate DCR. The native verifier remains responsible
for the artifact's own signatures, bindings, and status.

The boundary matters:

```text
Acorn preserves private control.
Grove preserves encrypted bytes.
Spurline preserves local relay events.
Native schemes preserve signed claims and format-specific proofs.
OpenETR validates DCR evidence and derives consequential state under defined rules.
Safebox Web helps the user operate the workflow.
```

## Evidence is not recognition

A signature proves that a key signed an event. It does not automatically prove
that a government, school, employer, community, or counterparty recognizes the
event.

Verification should report artifact integrity, event authenticity, graph
continuity, transition validity, consequential state, evidence sufficiency, and
recognition separately where applicable. It should not compress them into one
universal `valid` result.

Safebox keeps those questions separate:

- Native schemes preserve signed claims and their internal bindings.
- OpenETR preserves signed DCR evidence about an exact Digital Artifact and
  derives consequential state under defined rules.
- Acorn controls private records and value.
- Nostr and other social or institutional inputs can help identify recognized
  actors.
- A verifier policy decides what effect to give the evidence.

A notary signature therefore adds evidence, not automatic truth. Its trust
comes from what was attested, whether the verifier recognizes the attestor,
whether the procedure was fit for purpose, and whether the statement is current
and relevant. That is different from trusting a native issuer's claims, though
a verifier may require both.

That distinction is the heart of the records-first model. The system does not
need one central database to decide what every record means. Different
verifiers can recognize the same evidence under different rules.

## Why this matters

A records-first architecture supports continuity across apps, devices,
organizations, and communities. It can work with hosted services in ordinary
use and local services when local operation matters most.

It also makes room for use cases that are awkward in ordinary app-centric
systems:

- private records with public proof of origin;
- transferable records with signed control history;
- community recognition during limited connectivity;
- Continuity Payments: in-kind local payment clearing using transferable
  proofs, with finality deferred until mint reconciliation;
- evidence that remains useful after the original application changes or is
  replaced; and
- verifier policies that can explain why evidence is or is not recognized.

Safebox Web is one working app in that architecture and the practical
foundation for Mainstay, the future unified application. Lockbox is the
hardware-first appliance intended to run Mainstay and its supporting services
locally:

> Lockbox preserves local stewardship, continuity, and evidence.

[Understand Deep Verification](deep-verification.md){ .md-button .md-button--primary }
[Explore Verifiable Records](records-and-wallet-passes.md){ .md-button }
