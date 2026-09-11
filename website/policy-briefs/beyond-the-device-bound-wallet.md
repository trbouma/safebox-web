---
title: Beyond the Device-Bound Wallet
description: Why high-assurance device security and relay-backed user continuity are complementary foundations for digital wallets.
---

# Beyond the Device-Bound Wallet

**Policy brief**

## Core proposition

A trustworthy digital wallet needs to answer two different questions:

1. **Was this sensitive action performed with a protected key on an authorized
   device?**
2. **Can the person or community recover its keys, balances, and records when
   that device, application, provider, or network is no longer available?**

Google's 2026 paper *High Assurance Digital Credentials on Android* offers a
careful answer to the first question. Safebox Web and Safebox Acorn are being
designed around the second.

Neither answer is sufficient alone. Hardware-backed security without
continuity can turn one protected device into a point of failure. Portable
relay-backed state without a protected execution boundary cannot prove that a
high-consequence action occurred inside certified hardware. The practical
policy objective is to compose both properties while keeping their keys,
claims, and responsibilities separate.

> **Hardware-backed for protected action. Relay-backed for continuity.**

## What Android gets right

The Android paper does not treat a digital credential as a picture of a card.
It treats it as issuer-signed information presented through cryptographic keys
and explicit authorization.

Its high-assurance model uses Android Keystore and, at the strongest level,
StrongBox secure hardware to:

- generate and retain non-exportable credential keys;
- prevent a credential from being silently cloned to another device;
- prove key provenance and device posture to an issuer;
- restrict keys to an authentic application instance;
- require a PIN or biometric authorization before use; and
- support local, privacy-preserving presentation without contacting a central
  credential vault for every transaction.

Just as important, the paper narrows the strongest certification boundary. It
does not pretend that secure silicon, the operating system, the screen, user
authentication, the wallet application, the issuer, and the presentation
protocol all provide the same assurance. Each layer has a specific job.

That is good architectural discipline: **good boundaries, not barriers**.

## The problem that secure hardware does not solve

A non-exportable key is deliberately difficult to move. That is its security
value—and its continuity limitation.

When a phone is lost, destroyed, reset, unsupported, or inaccessible, the
secure hardware cannot simply reveal its private key for installation
elsewhere. A high-assurance credential may need to be reissued or rebound to a
new protected key. The surrounding records, status references, evidence, and
recovery context still need somewhere durable and portable to live.

The same problem appears above the device:

- a wallet application can disappear;
- a cloud account can be suspended;
- an identity provider can go offline;
- a remote community can lose its connection to a national service;
- a local registry can be interrupted by fire, flood, or equipment failure;
- a commercial provider can change terms or stop operating.

High assurance should therefore include continuity, not only resistance to key
extraction.

## What Safebox and Acorn add

Safebox Web is an interface to an Acorn component. Acorn places portable
cryptographic authority and encrypted state on user-selected protocol
infrastructure rather than making one application database the permanent
wallet.

In this model:

- the Acorn key controls signed and encrypted component state;
- relays provide availability and transport without becoming the authority;
- encrypted Record Files can remain opaque to a Grove or Blossom server;
- recovery material can reconstruct the Acorn through another compatible
  environment;
- multiple relays or local infrastructure can provide reciprocal resilience;
  and
- OpenETR evidence can preserve what recognized actors have said or done about
  an exact record over time.

This is not a claim that the current Safebox Web key is hardware-backed. In the
hosted profile, the attached Acorn secret is protected in an encrypted browser
session at rest but becomes available to the trusted web process during an
authorized operation. That boundary must be disclosed honestly.

Acorn's strength is different: the wallet's durable authority and records are
not supposed to become inseparable from that one web deployment.

## Portability and non-exportability are not the same goal

It is tempting to ask one “wallet key” to do everything. That creates a
contradiction.

If a recovery phrase can reconstruct a key on a replacement device, the key
cannot also be truthfully described as never having existed outside one secure
element. If a credential key is genuinely non-exportable, it cannot be restored
from the Acorn recovery phrase.

The sound architecture uses separate keys for separate responsibilities:

| Authority | What it does |
| --- | --- |
| **Acorn continuity key** | Recovers portable component authority and encrypted relay-backed state |
| **Protected-record key** | Adds an independent protection boundary for especially sensitive records |
| **Credential presentation key** | Proves possession from approved device hardware when high assurance is required |
| **Issuer key** | Establishes who made the credential claim |
| **Attestation chain** | Provides evidence about the credential key's hardware and application boundary |
| **OpenETR authority** | Makes signed lifecycle statements about an exact artifact under a recognition policy |

On a new device, Acorn can recover the surrounding state. A high-assurance
credential can then be reissued or rebound to a new, locally generated hardware
key. Continuity is preserved without pretending that a non-exportable key was
copied.

## A wallet as a layered public capability

The combined model looks less like one container and more like a set of
cooperating public capabilities:

```text
issuer signature              -> who asserted the credential data
device-bound key              -> where protected possession was exercised
PIN or biometric authorization -> how local use was approved
Acorn                          -> how user-controlled state remains portable
relays and encrypted blobs     -> how records remain available
OpenETR evidence               -> what happened to the exact record over time
recognition policy             -> what this verifier accepts for this purpose
```

This layered view matters because a valid statement at one layer must not be
inflated into a claim about another:

- a relay copy provides availability, not authenticity;
- a hardware attestation provides key provenance, not legal identity;
- an issuer signature provides origin, not permanent validity;
- a biometric authorization provides local user presence, not universal
  recognition;
- a valid OpenETR graph provides evidence and consequential state under rules,
  not automatic legal effect everywhere; and
- recovery restores authority and state, not necessarily every device-bound
  credential key.

## Privacy requires both local use and restrained disclosure

The Android paper highlights a practical privacy advantage of local credential
presentation: a remote credential vault does not learn the time, network
location, and relying party for every use. It also warns that hardware
attestation can become a tracking mechanism if stable device evidence is shown
too broadly.

Safebox approaches disclosure through **Check**, **Present**, and **Share**:

- **Check** examines the available signed evidence without exposing the Record
  File.
- **Present** makes the exact file temporarily visible for ordinary human
  inspection.
- **Share** releases a copy when deeper verification or retention is justified.

This graduated-disclosure model complements—but does not replace—credential
protocols that can selectively disclose claims or prove predicates. A future
Android profile can combine both: a familiar Safebox decision flow with a
device-bound, minimally identifying credential presentation underneath.

The design should also avoid using a stable Acorn public key as a universal
person identifier. Keys can represent authority and continuity; a person's
identity and a relying party's trust judgement are assembled from context,
credentials, relationships, signed history, and policy outside the key itself.

## Continuity deserves a place in high-assurance policy

Security frameworks often devote great attention to preventing unauthorized
use while treating legitimate recovery and institutional continuity as
secondary operational concerns. For households, communities, and public
services, prolonged unavailability can be consequential even when no secret
has been stolen.

A mature wallet profile should therefore ask:

- Can the holder change applications without losing the record?
- What survives loss or reset of the device?
- Which keys are restored, and which credentials must be reissued?
- Can an authorized transaction proceed locally when an outside service is
  unavailable?
- How fresh must issuer or status information be for the decision at hand?
- Can encrypted state be recovered from another relay or local appliance?
- Can an institution prove what happened during an outage and reconcile later?
- Who controls each recovery path, and can that authority be revoked?

These are assurance questions, not merely backup features.

## Policy recommendations

### Define the wallet boundary precisely

Standards and procurement should identify the components that generate keys,
capture intent, authenticate the user, store records, synchronize state,
perform presentations, check status, and support recovery. “The wallet” is too
broad to serve as one security claim.

### Require explicit key roles

Portable continuity keys, credential keys, device attestation keys, issuer
keys, service keys, and record-protection keys should not be interchangeable.
Each needs a documented purpose, custodian, lifecycle, and compromise response.

### Recognize two kinds of portability

A common hardware API across manufacturers gives developers and issuers
ecosystem portability. Recoverable, protocol-controlled authority gives users
continuity across applications and infrastructure. Policy should require the
appropriate form—or both—without confusing them.

### Allow recovery through reissuance

Recovery should not require export of a key whose assurance depends on
non-exportability. A wallet should be able to recover its durable state and
then bind eligible credentials to newly attested hardware, preserving visible
lifecycle evidence.

### Keep attestation proportionate

Issuers may need hardware provenance during enrollment. Ordinary verifiers
should not automatically receive stable device evidence. Attestation,
credential presentation, and user identification require distinct privacy
rules.

### Treat local and relay-backed operation as complementary

Local device execution reduces network surveillance and supports offline use.
Relay-backed encrypted state supports recovery and continuity. A high-assurance
profile should be able to use both without turning the relay into an identity
provider or the phone into the only durable home of the wallet.

## What this means for Safebox

The near-term goal should not be to absorb Android Keystore into Acorn or move
all Acorn authority into one phone. It should be to define an optional Android
high-assurance profile with a narrow bridge between the layers:

1. Android hardware generates and retains a credential presentation key.
2. An issuer verifies its attestation before provisioning a credential.
3. Acorn preserves the encrypted credential envelope, public material,
   evidence, status references, and recovery or reissuance context.
4. Safebox presents a clear request and captures the user's intent.
5. Android authorizes and performs the credential proof locally.
6. A replacement device recovers Acorn state, then rebinds or reissues the
   credential to a newly attested key.

Safebox must continue to distinguish what exists today from that future
profile. The current implementation demonstrates relay-backed continuity,
encrypted records, recovery, balances, and graduated-disclosure workflows. It
does not yet demonstrate StrongBox custody, hardware attestation, or a
certified high-assurance credential presentation.

## Conclusion

Android's architecture shows how high-assurance credential use can be reduced
to a small, verifiable hardware boundary surrounded by explicit platform and
protocol responsibilities. Safebox and Acorn show how a wallet's durable state
can remain controlled outside any one application, device, relay, or service
provider.

The two ideas meet in a stronger and more practical wallet:

> **The device protects the moment of action. The protocol protects continuity
> over time.**

That is not a compromise between security and portability. It is a clearer
allocation of responsibility.

## Further reading

- Google, *High Assurance Digital Credentials on Android: A Framework for
  Hardware-Backed Security*, August 2026.
- [Detailed Safebox architectural analysis](https://github.com/trbouma/safebox-web/blob/main/docs/ANDROID-HIGH-ASSURANCE-CREDENTIALS-AND-RELAY-BACKED-CONTINUITY.md)
- [Safebox Trust Boundary](../trust-boundary.md)
- [Safebox Product Architecture](../product-architecture.md)
- [Graduated Disclosure](../graduated-disclosure.md)
- [Records and Wallet Passes](../records-and-wallet-passes.md)

