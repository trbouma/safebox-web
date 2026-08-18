---
title: Records and Wallet Passes
description: How Safebox Web safeguards, previews, and preserves original records, including PKPASS Wallet passes.
---

# Records and Wallet Passes

Safebox Web can safeguard a private record together with its **Original
Record**: the exact PDF, image, Wallet pass, or other artifact attached to it.
The record supplies context. The Original Record preserves the thing itself.

Supported representations currently include:

| Original Record | Safebox Web behavior |
| --- | --- |
| Image | Show an inline image preview. |
| PDF | Show a browser-compatible PDF viewer and original download. |
| Apple Wallet `.pkpass` | Show a Wallet-pass preview and an **Open/Add Wallet Pass** action. |
| Other artifact | Preserve the original and provide a download when inline rendering is not appropriate. |

## PKPASS is now a first-class Original Record

A `.pkpass` file is technically a signed ZIP package, but to a person it is a
boarding pass, ticket, membership card, coupon, or other Wallet pass. Safebox
Web preserves that distinction instead of presenting every pass as an
unhelpful ZIP download.

When Acorn identifies the effective media type as
`application/vnd.apple.pkpass`, Safebox Web can display:

- the issuing organization, description, and serial number;
- primary, secondary, auxiliary, header, and back fields;
- package artwork such as the logo, icon, strip, or thumbnail;
- a QR or Aztec symbol when the pass declares a supported barcode; and
- a link that opens or downloads the original pass for a compatible wallet.

The preview is server-rendered. The browser does not need a PKPASS parser or a
special client-side application merely to inspect the pass.

## The original remains the original

Safebox Web does not rewrite, normalize, or re-sign a Wallet pass. Acorn
encrypts the Original Record before it is sent to blob storage and returns the
exact decrypted bytes when the user requests it.

```text
Original PKPASS bytes
        -> encrypted by Acorn
        -> stored as an opaque blob
        -> retrieved and authenticated
        -> decrypted for the connected session
        -> previewed or opened as a Wallet pass
```

The preview helps a person understand the artifact. The digest of the exact
Original Record remains the stable anchor for integrity and deeper
verification.

## Preview is not issuer validation

Showing a pass does not, by itself, establish that its issuer should be
trusted. A compatible Wallet application remains responsible for its
install-time signature and trust behavior. Safebox Web preserves the pass and
makes it understandable; OpenETR or another control layer can associate the
unchanged artifact digest with signed origin, transfer, presentation,
revocation, or verifier-policy evidence.

That separation is deliberate:

| Question | Responsible layer |
| --- | --- |
| Where are the encrypted bytes held? | Grove or another Blossom server |
| Who encrypts, retrieves, and authenticates them? | Acorn |
| How are they presented to the user? | Safebox Web |
| Is this the exact original artifact? | Original Record digest |
| Who issued or controlled it? | Signed evidence and verifier policy |

## A path to W3C Verifiable Credentials

The same architecture provides a clear path for holding and verifying
[W3C Verifiable Credentials](https://www.w3.org/TR/vc-data-model-2.0/).
A credential can be safeguarded as an Original Record while Safebox preserves
its exact secured representation and the surrounding private record supplies
human context.

Supporting a credential involves several distinct questions:

| Question | Verification concern |
| --- | --- |
| Does it conform to the expected credential model? | Credential structure and schema |
| Is its securing mechanism valid? | Issuer proof verification |
| Is it suspended, revoked, or otherwise constrained? | Credential status and policy |
| Is the presenter entitled to present it? | Holder binding and presentation challenge |
| Does this verifier recognize the issuer and claims? | Local recognition and verifier policy |

Safebox should not collapse these questions into a single “verified” badge.
The W3C credential layer can establish that a secured claim was made by a
particular issuer and presented under the applicable mechanism. The verifier
still decides what that claim means in context.

### Deeper verification through OpenETR

OpenETR can add another dimension when the credential refers to an object,
record, entitlement, or instrument whose history matters. The exact credential
or underlying Original Record digest can be connected to signed control events
that describe origin, transfer, presentation, encumbrance, redemption, or
termination.

```text
W3C credential verification
        -> who made this secured claim?
        -> is the credential and presentation valid?

OpenETR control verification
        -> what object does the claim refer to?
        -> where did it originate?
        -> how has control changed over time?
        -> is the presented state still current?
```

These models are complementary. A Verifiable Credential can carry a portable,
machine-verifiable assertion. OpenETR can subject the referenced artifact and
its control history to deeper verification. Safebox provides the private
safekeeping and presentation environment in which both forms of evidence can
be used without turning the application itself into the system of record.

[Understand deep verification](deep-verification.md){ .md-button .md-button--primary }
[Read the implementation note](https://github.com/trbouma/safebox-web/blob/main/docs/PKPASS-PREVIEW-FEATURE.md){ .md-button }
