---
title: Deep Verification
description: How Safebox separates bytes, renderings, control evidence, recognition, and policy.
---

# Deep Verification

Deep verification is Safebox's layered model for deciding whether a digital
record should be trusted.

It is a play on two familiar ideas:

- **Deep links** take you to a precise thing, not just a homepage.
- **Defense in depth** uses multiple independent layers instead of one fragile
  gate.

Deep verification applies the same instinct to records. It does not ask one
database, one file type, one preview screen, or one signature to answer every
question. It separates the interests and lets each layer do the job it is good
at.

```text
exact bytes
    -> digest
    -> signed control evidence
    -> recognized actors
    -> verifier policy
    -> human-readable result
```

## Why this matters

A digital record can be many things at once.

A boarding pass, ticket, credential, invoice, bill of lading, PDF, or Wallet
pass has bytes. It may have a useful preview. It may be signed by an issuer. It
may have a control history. It may be recognized by one institution and ignored
by another. It may be valid today and expired tomorrow.

Those are different questions:

| Question | Layer |
| --- | --- |
| Are these the exact bytes? | Digest and integrity |
| What should the user see? | Representation |
| Who originated or controlled it? | Signed control evidence |
| Who recognizes those keys or organizations? | Recognition |
| What conclusion should this verifier reach? | Policy |

Safebox is designed so those questions stay legible.

## The Original Record anchor

When Acorn stores an Original Record, it preserves the exact artifact bytes and
encrypts them before blob storage. The plaintext digest becomes the stable
anchor for verification.

For example, an Apple Wallet `.pkpass` file is a ZIP-shaped package, but the
user-facing artifact is a Wallet pass. Safebox Web can render the pass fields,
logo, and barcode, including boarding-pass Aztec codes. That preview helps a
person understand the record.

The verification anchor is still the digest of the exact `.pkpass` bytes.

```text
preview is for understanding
digest is for evidence
```

This means Safebox can show a useful representation without rewriting the
artifact or making the preview itself the object of verification.

## Where OpenETR fits

OpenETR adds a signed control layer around an artifact digest.

It can answer questions like:

- Who originated this artifact?
- Which key controlled it at a given point?
- Was it transferred, presented, encumbered, redeemed, or terminated?
- What evidence should a verifier consider?

Safebox Web can retrieve the Original Record through Acorn, hash the exact
bytes, and pass that digest into OpenETR verification. The verifier can then
combine the digest with signed control events and recognition inputs.

The application does not have to become the registry. The blob store does not
have to understand the file. The preview does not have to prove the whole
truth. Each layer contributes one part of the answer.

## Effective MIME and representation

Acorn's effective MIME metadata helps Safebox Web choose a representation.

Examples:

| Effective MIME | Safebox Web can show |
| --- | --- |
| `image/png` | Image preview |
| `application/pdf` | PDF preview |
| `application/vnd.apple.pkpass` | Wallet pass preview |
| unsupported type | Download-only attachment |

Effective MIME is not the verification proof. It is a rendering hint preserved
with the record. The digest remains the precise anchor.

That separation is what lets Safebox safely say:

```text
show it as a Wallet pass
verify it as these exact bytes
reason about it through signed control evidence
```

## A stronger verification model

Deep verification gives Safebox room to support richer records without stuffing
every concern into one component.

Acorn safeguards keys, encrypted records, exact bytes, and digests. Grove stores
opaque encrypted blobs. Spurline and other relays preserve signed evidence.
OpenETR can model control history. Safebox Web renders the workflow and explains
the result.

That is powerful because failure in one layer does not automatically collapse
the whole model. A pretty preview is not enough. A matching hash is not enough.
A signature from an unknown key is not enough. A recognized issuer still needs
policy. The confidence comes from the layers fitting together.

Deep verification is the product name for that fit.
