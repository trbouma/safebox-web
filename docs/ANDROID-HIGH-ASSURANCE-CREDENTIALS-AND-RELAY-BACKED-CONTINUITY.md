# Android High-Assurance Credentials and Relay-Backed Continuity

## Purpose

This note analyzes Google's August 2026 white paper *High Assurance Digital
Credentials on Android: A Framework for Hardware-Backed Security* against the
relay-backed architecture used by Safebox Web and Safebox Acorn.

It is an architectural analysis, not a security certification, implementation
claim, or statement that Safebox Web currently integrates Android StrongBox,
KeyMint, Remote Key Provisioning, or the Android digital-credential APIs.

The central finding is that the two architectures solve different parts of a
larger wallet problem:

> **Android provides a strong boundary for proving that a protected key was
> used on an authorized device. Acorn provides a portable continuity layer for
> user-controlled keys, balances, records, and encrypted state across devices,
> applications, relays, and service providers.**

They are complementary. Android is strongest at protected execution and
transaction authorization. Acorn is strongest at continuity, recovery,
replaceable infrastructure, and protocol-level portability. A high-assurance
Safebox deployment should compose these properties without collapsing their
keys, trust claims, or failure modes into one object.

## The Android paper's architectural claim

The paper reduces three high-assurance credential problems to distinct layers:

1. **Document authenticity** is established by issuer signatures in formats
   such as ISO mdoc or SD-JWT VC. Once issued, this property is logically
   separate from the device's local hardware security (page 8).
2. **Impersonation resistance** depends on keeping a credential key from being
   cloned and requiring legitimate user authorization before it is used
   (pages 8–9).
3. **Privacy** requires data minimization, unlinkable presentation protocols,
   and attestation that does not become a device-tracking mechanism
   (pages 10 and 26–27).

Android Keystore supplies the common platform boundary. StrongBox KeyMint is
the highest-assurance backend described in the paper: a dedicated,
tamper-resistant Secure Element generates and uses non-exportable keys,
enforces key policies, and produces attestations about key provenance. Android
Verified Boot, application binding, lock-screen knowledge factors, biometrics,
Hardware Authentication Tokens, and Remote Key Provisioning provide additional
layers around that core.

The paper is careful not to place every responsibility inside the strongest
hardware boundary. It proposes a small **WSCA Core** covering secure key
generation, isolation, cryptographic execution, and attestation. User
authentication remains a separate boundary because PIN entry, biometrics, the
operating-system interface, and transaction intent have different threat
models (pages 21–22). This is an important design lesson for Safebox: assurance
comes from explicit boundaries and verifiable claims, not from describing the
whole wallet as one uniformly trusted component.

## The Safebox and Acorn architectural claim

Safebox Web is an application interface to an Acorn component. Acorn provides
cryptographic authority and relay-backed state for keys, balances, records,
recovery, and transfer workflows. A configured relay provides signed-event
availability and transport; it does not become the owner of the records or the
source of their meaning. Encrypted attachments can be stored as opaque blobs
on Grove or another Blossom-compatible server.

The current hosted Safebox Web profile places an encrypted session capability
in the browser cookie. The web process decrypts the attached Acorn secret while
performing an authorized operation. The application database coordinates
handles and durable jobs but is not the user's wallet. This is a portable,
protocol-first model, but it is not a hardware-backed key boundary: the Acorn
key is available to application process memory while it is being used.

Acorn's recovery model intentionally permits the same component authority and
relay-backed state to be reconstructed in another compatible environment. That
is almost the inverse of a non-exportable credential key permanently confined
to one Secure Element. The difference is not a defect in either design; it
reflects two different objectives:

- **device binding** limits where a high-assurance credential operation may
  occur; and
- **component continuity** prevents a device, application, relay, or provider
  from becoming the permanent point of control.

## Two forms of openness

The Android paper describes openness primarily at the platform API and OEM
ecosystem level. A wallet application can use common Android APIs across
compatible hardware, and an issuer can consume a common attestation format
instead of integrating with every secure-element vendor.

Acorn describes openness at the state and authority level. The controlled
component is reconstructable from recovery material and relay-backed state,
and compatible applications or infrastructure can replace the original
interface or provider.

These forms of openness should not be confused:

| Question | Android high-assurance layer | Safebox and Acorn layer |
| --- | --- | --- |
| Can different wallet apps use a common protected-key API? | Yes, on compatible Android implementations | Not the primary claim |
| Can an issuer verify hardware key provenance? | Yes, through attestation and RKP | Not currently |
| Can the credential key be exported or cloned? | Intentionally no for the high-assurance profile | The Acorn continuity key is intentionally recoverable |
| Can the component survive loss of an app, device, relay, or provider? | Credential reissuance or platform-specific migration is required | This is a primary Acorn design goal |
| Can presentation occur locally without contacting an issuer? | Yes, when the credential protocol permits it | Safebox can present records locally in a future local deployment; the hosted app still depends on its execution path |
| Does the storage or transport provider decide whether a record is trusted? | No | No |

## The key architectural tension

A single key should not be asked to provide both perfect portability and
strict non-exportability.

If an Acorn recovery phrase reconstructs a key on a new device, that key cannot
also be truthfully attested as never having existed outside one certified
Secure Element. Conversely, if a credential key is permanently non-exportable,
loss or reset of the device must be handled through reissuance, rebinding, or a
separate recovery protocol—not by reconstructing the same private key from an
Acorn mnemonic.

The appropriate response is **key separation**:

| Key or authority | Purpose | Expected custody |
| --- | --- | --- |
| Acorn continuity key | Addressing, signing relay events, decrypting Acorn state, and continuity across infrastructure | Recoverable and user-controlled |
| Record Protection Key | Optional independent protection for especially sensitive record envelopes | Recoverable through a separate protected ceremony |
| Credential presentation key | Proof of possession for one credential or credential family | Device-bound and non-exportable when high assurance is required |
| Device attestation key | Evidence that a credential key was generated and remains in an approved hardware and application boundary | Managed by the Android attestation architecture |
| Issuer key | Signs credential claims or authoritative records | Controlled by the issuer |
| OpenETR event key or recognized authority | Makes signed statements about an exact artifact and its lifecycle | Controlled according to the applicable governance policy |

The Acorn key may authorize installation, retrieval, or use of a credential
envelope without pretending to be the high-assurance credential key itself.
Likewise, a device-bound key can authorize a presentation without becoming the
long-term root of all Acorn records and balances.

## A composable architecture

A future Android-enabled Safebox profile could use the following arrangement:

```text
issuer-signed credential or exact Record File
                    |
                    v
      encrypted Acorn record or reference
        replicated through selected relays
                    |
        +-----------+-----------+
        |                       |
        v                       v
Acorn continuity key     Android presentation key
portable authority       non-exportable authority
and recovery             in StrongBox or TEE
        |                       |
        +-----------+-----------+
                    v
       consented presentation or action
                    |
                    v
 issuer proof + device proof + applicable status,
 OpenETR evidence, recognition policy, and result
```

The relay-backed record can contain the credential, an encrypted Record File,
a reference to opaque blob content, presentation metadata, issuer material,
status references, OpenETR evidence, and information needed to request
reissuance. It should not contain an export of the non-exportable credential
private key.

On a replacement device, Acorn can recover the component's durable state. The
high-assurance credential may then be reissued or rebound to a newly attested
device key. This gives the user continuity without falsely claiming that the
same StrongBox key moved between devices.

## Lifecycle comparison

### Creation and provisioning

Android generates the credential key inside an approved hardware backend and
provides attestation evidence to the issuer. RKP translates manufacturer and
secure-element trust into a common certificate chain while attempting to limit
cross-application correlation.

Acorn creates or recovers a component key, discovers encrypted state from its
bootstrap relay, and loads only the resources required for the operation. It
does not presently give an issuer a hardware provenance statement.

A composed flow should let Acorn coordinate provisioning while the Android
hardware generates the credential key and proves its properties directly.

### Use and presentation

Android can require fresh PIN or biometric authorization and bind that
authorization to a specific cryptographic operation. Safebox currently
provides explicit, server-rendered **Check**, **Present**, and **Share** actions
for records. These actions implement graduated disclosure, but they do not by
themselves prove hardware-backed holder binding or selective disclosure at the
claim level.

The strongest combined flow would keep Safebox's understandable disclosure
steps while using a native credential protocol for device-bound proof of
possession, selective disclosure, session binding, and verifier-origin binding.

### Loss, reset, and replacement

The Android paper treats factory reset as a cryptographic wipe: destroying the
Secure Element master key renders offloaded credential key blobs unusable
(page 14). This is strong retirement behavior, but it also means those keys
cannot be recovered after reset.

Acorn treats device replacement as a normal continuity event. Recovery
material plus surviving relay state can reconstruct the component in another
compatible environment. A combined lifecycle therefore needs two distinct
messages:

- **Your Safebox state and portable authority can be recovered.**
- **A high-assurance credential may need to be reissued or rebound on the new
  device.**

### Revocation and compromise

Android RKP can stop replenishing attestation keys for compromised devices, but
credential status remains an issuer and credential-protocol responsibility.
Acorn can preserve signed status and control events, but relay availability
does not establish issuer recognition or device integrity.

A complete system needs explicit revocation for the credential, the device,
the application instance, the Acorn authority, and any delegated relationship.
Those states should not be represented by one generic “revoked” flag.

## Threat and resilience analysis

| Event or attack | Android high-assurance contribution | Acorn contribution | Remaining issue |
| --- | --- | --- | --- |
| Phone is lost or stolen | Non-exportable keys and user authentication resist use | Relay-backed state and mnemonic support continuity | Credential reissuance or rebinding; recovery ceremony security |
| Wallet app is removed | Application-bound keys become unavailable to other apps | Another compatible Acorn interface can recover portable state | High-assurance app migration is not automatic |
| Device is rooted or OS is compromised | Verified Boot and attestation expose some posture; Secure Element resists extraction | No equivalent device-attestation claim today | Compromised UI can misrepresent intent or capture secrets |
| Relay is unavailable | Not addressed | Another suitable relay or local replica can provide availability | Replica discovery, freshness, and tested recovery |
| Relay data is harvested | Not addressed | Record and blob encryption limit disclosure | Public metadata and future cryptographic attacks remain relevant |
| Safebox provider is unavailable | Local Android credentials may still present | Acorn state can be used through another compatible execution environment | Discoverability and deployment portability must be tested |
| Issuer or status service is unavailable | Offline presentations may remain possible | Cached signed evidence and local relays preserve material | Relying policy must define acceptable staleness and offline status |
| Malicious verifier tracks presentations | Ephemeral/session keys and selective disclosure can reduce correlation | Graduated disclosure reduces unnecessary record release | Stable Acorn public identifiers must not be exposed when unnecessary |
| User loses recovery material | Device key may continue working until device loss | Acorn continuity is at risk | Assisted recovery without creating custodial concentration remains unsolved |
| Secure hardware fails | Device-bound key is unavailable | Acorn preserves surrounding records and recovery context | Credential must be reissued or rebound |

## Privacy implications

The paper makes a valuable distinction between local cryptographic execution
and remote vaults. A remote signer necessarily learns timing and network
metadata about presentations; local execution can avoid that disclosure. It
also emphasizes that attestation itself can become a correlation mechanism if
certificate chains or device identifiers are stable.

Safebox improves privacy in different ways: private records are encrypted
before relay storage, attachments can be opaque to the blob server, and
graduated disclosure lets a holder check, present, or share according to need.
However, a stable Acorn `npub`, relay access patterns, public event metadata,
and a hosted web execution environment can still create correlation surfaces.

A high-assurance mobile profile should therefore:

- avoid exposing the Acorn public key as a universal credential subject ID;
- use credential-specific, pairwise, or session-specific identifiers where
  the presentation protocol supports them;
- keep routine presentation cryptography local to the device;
- request only the minimum record state needed for a transaction;
- separate hardware attestation from ordinary relying-party presentations;
- disclose when a hosted Safebox process sees plaintext session material; and
- retain Safebox's distinction between cryptographic validity, issuer
  authority, recognition policy, and legal or operational effect.

## Availability is not authenticity

Relay replication can make an encrypted credential or Record File available
after an app, device, provider, or local site is lost. It cannot prove that the
issuer's claims are true, that a device is uncompromised, that the presenter is
the intended holder, or that a relying party must recognize the result.

Conversely, a StrongBox attestation can provide strong evidence about a key's
origin and execution boundary. It does not preserve the user's records after a
device is destroyed, keep a community operating when outside services fail,
or decide the current meaning of a transferable or consequential record.

The combined assurance statement is therefore layered:

```text
issuer signature              -> who asserted the credential data
device-bound presentation key -> where protected possession was exercised
user authentication           -> how local use was authorized
Acorn continuity              -> how state survives replaceable infrastructure
relay and Grove replication   -> where encrypted material remains available
OpenETR evidence              -> what happened to an exact artifact over time
recognition policy            -> what a relying party accepts for this purpose
```

No single layer should claim the properties of the others.

## Implications for standards and policy

### Treat “wallet” as a system of bounded components

Policy should specify which component holds keys, performs cryptography,
captures user intent, stores records, synchronizes state, supports recovery,
checks status, and presents evidence. Certifying an undifferentiated “wallet”
encourages either an impossibly large evaluation boundary or ambiguous claims.

### Distinguish device portability from authority portability

A common API across many Android devices is valuable ecosystem portability.
It is not the same as allowing a user-controlled component and its records to
survive the loss of one device or application. Requirements should address
both without demanding that non-exportable keys become exportable.

### Permit recovery by reconstitution and reissuance

Recovery does not always mean restoring the same key. Acorn can reconstitute
portable state and authority; a high-assurance credential can be reissued to a
new attested key. Standards should support this composed lifecycle and make
the change visible in signed evidence.

### Evaluate the smallest meaningful security boundary

The paper's proposed WSCA Core is a useful model: certify the stable primitive
that actually provides hardware key protection, then evaluate the surrounding
orchestration according to its own threats. Safebox should follow the same
principle when introducing hardware adapters: the hardware should expose
narrow signing, decryption, attestation, and authorization operations rather
than absorbing the whole application.

### Make continuity an assurance property

High assurance is incomplete if legitimate users lose access whenever a
device, app, cloud service, or distant registry fails. Security profiles should
include encrypted portability, recovery testing, replaceable infrastructure,
offline or local presentation, and bounded stale-state policies alongside key
extraction resistance.

## Recommended Safebox roadmap

### Near term: specify the boundary

1. Define an **Android high-assurance profile** separately from the current
   hosted Safebox Web profile.
2. Publish a key-role registry covering Acorn, record protection, credential
   presentation, attestation, issuer, and service keys.
3. Define which state is relay-portable and which state must be reissued after
   device loss.
4. Add a capability model so the interface never implies hardware assurance
   when only software key custody is active.
5. Extend threat modeling to compromised Android UI, malicious applications,
   verifier-origin confusion, stable-identifier correlation, and recovery
   substitution.

### Prototype: compose rather than replace

1. Build a narrow Android bridge or companion proof-of-concept that can create
   a KeyMint-backed credential key and return its public key and attestation.
2. Store only the credential envelope, public material, status references, and
   recovery/reissuance metadata through Acorn.
3. Require a transaction-bound device signature for a presentation while
   retaining Safebox's Check, Present, and Share interaction model.
4. Test loss and replacement: recover Acorn state on a second device, then
   rebind or reissue the credential to a new hardware-backed key.
5. Test offline presentation independently from relay and issuer
   availability, with explicit policy for status freshness.

### Assurance and governance

1. Validate attestation chains, application identity, boot state, patch level,
   security level, authorization policy, and freshness according to an explicit
   issuer policy.
2. Keep attestation evidence out of routine presentations unless required.
3. Record device replacement and credential reissuance as explicit lifecycle
   events without publishing sensitive correlation data.
4. Define operational responses for lost devices, compromised Acorn recovery
   material, revoked credentials, unavailable issuers, and unavailable relays.
5. Align any formal claims with a deployment-specific object of conformity,
   building on the existing draft DGSI 103 Part 4 assessment.

## What Safebox should not claim yet

Safebox Web should not presently claim that:

- its Acorn keys are non-exportable or hardware-backed;
- its web session proves biometric user presence;
- relay-backed recovery preserves a StrongBox credential private key;
- an OpenETR history replaces issuer signature or credential-status checking;
- graduated disclosure is equivalent to cryptographic selective disclosure;
- an Android device is trustworthy merely because it runs the app; or
- the current deployment meets EUDIW LoA High, WSCA/WSCD, QSCD,
  AVA_VAN.5, or another certification profile.

The accurate claim is narrower and more useful: Safebox and Acorn provide a
promising continuity and portability layer that can be composed with certified
device security and established credential protocols.

## Conclusion

The Android paper shows how a large, open mobile ecosystem can expose a narrow,
verifiable hardware security boundary to many wallet applications. Safebox and
Acorn ask the adjacent question: how do the component, records, balances, and
recovery path remain user-controlled when the device, app, relay, or provider
changes?

The mature architecture needs both answers. The mobile device can be the best
place to authorize and perform a high-assurance presentation without becoming
the only place the user's durable state can survive. Acorn can preserve that
state across replaceable infrastructure without pretending that recoverable
software keys have the provenance of non-exportable secure hardware.

This is the practical boundary:

> **Hardware-backed when a consequential action is performed; relay-backed
> when continuity and recovery are required. Good boundaries, not barriers.**

## Source

Google, *High Assurance Digital Credentials on Android: A Framework for
Hardware-Backed Security*, August 2026, 29 pages.
