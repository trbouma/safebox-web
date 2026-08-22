---
title: Product Architecture
description: How Safebox Web fits with Acorn, Grove, Spurline, Mainstay, and Lockbox.
---

# Product Architecture

Safebox Web is designed as a sibling product in the Mainstay product family.
Each component has a narrow responsibility and can be developed, tested, and
operated independently.

The user-facing promise is simpler than the component diagram: Safebox Web is
the wallet app available today. It is also the practical foundation for
Mainstay, the future unified application for keys, balances, and records.
The other pieces preserve state and evidence without making one hosted service
permanent.

## One record model, two practical views

Safebox's **Manage Balances** and **Manage Records** areas are not backed by
unrelated data models. Both arise from Acorn's uniform controlled-resource
model:

```text
fungible records     -> aggregated within an equivalence domain -> Balance
non-fungible records -> retained as individually meaningful     -> Record
```

Cash proofs and Clear mint notes are unique cryptographic records, but the
quantities they represent can be fungible within tightly defined domains.
Safebox projects those compatible quantities as balances. A passport image,
attestation, credential, or controlled original remains individually visible
because its identity and history cannot be replaced by a sum.

This also clarifies the product vocabulary. **Transfer** is the general
movement of controlled value or authority. **Payment** describes the value or
settlement leg when a transfer forms part of an economic transaction. A
transfer may instead be an allocation, gift, benefit, refund, or treasury
disbursement.

```text
              Safebox Web
                   |
                   v
                Acorn
          /    |     \
         v     v      v
    Spurline  Grove  control protocols
   local relay blobs  OpenETR and others

        proven workflows
              |
              v
           Mainstay  -> unified application
              |
              v
           Lockbox   -> hardware-first appliance
```

## Component roles

<div class="safebox-grid safebox-grid--two" markdown>

<article class="safebox-card" markdown>

### Safebox Web

The user app for onboarding, records, payments, handles, sharing, and
presentation workflows.

</article>

<article class="safebox-card" markdown>

### Acorn

The protocol runtime for keys, signing, encrypted records, recovery, ecash,
Lightning workflows, and relay-backed wallet state.

</article>

<article class="safebox-card" markdown>

### Spurline

A local Nostr relay that preserves relevant events for an individual,
organization, application, or community.

</article>

<article class="safebox-card" markdown>

### Grove

A Blossom-compatible blob store for opaque, content-addressed bytes. Acorn can
encrypt originals before Grove receives them.

</article>

<article class="safebox-card" markdown>

### OpenETR

A records-first control protocol that can bind signed origin and lifecycle
events to an exact artifact's Uniform Digest Anchor. It is one interoperable
control profile rather than a requirement for every record community.

</article>

<article class="safebox-card" markdown>

### Mainstay

The future unified application and primary entry point. Safebox Web supplies
practical wallet, record, and payment workflows that Mainstay can carry across
continuity modes.

</article>

<article class="safebox-card" markdown>

### Lockbox

The hardware-first appliance for running Mainstay, Acorn, Spurline, Grove, and
supporting services locally.

</article>

</div>

## Good boundaries, not barriers

Safebox does not ask one database or provider to establish every property of a
payment or record. Confidence is assembled from narrow responsibilities:

| Layer | Responsibility |
| --- | --- |
| Acorn key | authorization, signing, and decryption |
| Relay | signed-event availability and transport |
| Mint | Cashu proof spend state |
| Acorn runtime | proof compatibility, wallet mutation, and verified persistence |
| Effective MIME resolver | artifact classification for application handling |
| Safebox renderer | bounded human-readable previews for known formats |
| Native verifier | format-specific signatures, bindings, status, and trust policy |
| Attestation or control protocol | independent statements and lifecycle evidence bound to a Uniform Digest Anchor |
| Safebox Web | user intent, workflow coordination, and understandable status |

That is why an incoming payment can be visibly present but not yet part of the
spendable balance. It is also why the application database can coordinate a
background task without becoming the wallet.

The same pattern applies to records. Safebox's
[Deep Verification](deep-verification.md) model keeps exact bytes, Uniform
Digest Anchors, effective MIME, semantic presentation, native verification,
signed attestations, control evidence, recognition, and policy in separate
layers that can reinforce each other.

These boundaries contain failures and keep authority legible. Open protocols
prevent them from becoming barriers: each component can remain independently
deployable and replaceable while Mainstay and Safebox Web coordinate a
coherent experience across them.

[Read the balance model](how-safebox-treats-funds.md){ .md-button }

## Toward Mainstay and Lockbox

The product distinction is deliberate:

> **Mainstay is the application. Lockbox is the appliance. Continuity is the
> capability.**

Mainstay is intended to give people one calm experience across the product
family. Lockbox is the future appliance-like product profile for running that
experience and its services locally. The initial target is FreeBSD on a
Raspberry Pi 4, with hardware-backed trust boundaries such as a keypad and
TROPIC01 HSM.

In that model:

- Mainstay is the unified local user application;
- Safebox Web contributes the proven wallet, record, and payment workflows;
- Acorn is the controlled protocol state;
- Spurline is the local evidence relay;
- Grove is the local encrypted blob store;
- OpenETR provides signed transferable-record evidence; and
- external services remain useful, but not always required for local
  continuity.

Mainstay should describe changing conditions in plain continuity modes:
**Connected Mode**, **Local Mode**, **Mobile Mode**, and **Community Mode**.
Safebox Web already establishes Connected Mode and the confirmed-versus-pending
language. Later modes can be determined from service reachability, local
pairing, bridge state, and community mesh participation.

Continuity Payments are the payment expression of those modes. Safebox Web now
demonstrates direct ecash delivery to another Safebox address and keeps
unfinalized value visible as pending. If the mint is unreachable, finalization
can wait without changing the confirmed balance. Mainstay can carry that same
interaction into Local, Mobile, and Community modes while Lockbox supplies the
local services and storage.

## Related repositories

- [Safebox Acorn](https://github.com/trbouma/safebox-acorn)
- [Grove](https://github.com/trbouma/grove)
- [Spurline](https://github.com/trbouma/spurline)
- [Safebox Web](https://github.com/trbouma/safebox-web)
- [OpenETR](https://github.com/trbouma/openetr)
