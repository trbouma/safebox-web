---
title: Product Architecture
description: How Safebox Web fits with Acorn, Grove, Spurline, Mainstay, and Lockbox.
---

# Product Architecture

Safebox Web is designed as a sibling product in a broader local-first family.
Each component has a narrow responsibility and can be developed, tested, and
operated independently.

The user-facing promise is simpler than the component diagram: Safebox Web is
the wallet app available today. It is also the practical foundation for
Mainstay, the future unified application for records, identity, and payments.
The other pieces preserve state and evidence without making one hosted service
permanent.

```text
              Safebox Web
                   |
                   v
                Acorn
          /    |     \
         v     v      v
    Spurline  Grove  OpenETR
   local relay blobs  evidence

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

A records-first evidence layer for exact artifact digests, signed origin
events, control history, and verifier policy.

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

## Separate responsibilities, composable confidence

Safebox does not ask one database or provider to establish every property of a
payment or record. Confidence is assembled from narrow responsibilities:

| Layer | Responsibility |
| --- | --- |
| Acorn key | authorization, signing, and decryption |
| Relay | signed-event availability and transport |
| Mint | Cashu proof spend state |
| Acorn runtime | proof compatibility, wallet mutation, and verified persistence |
| Safebox Web | user intent, workflow coordination, and understandable status |

That is why an incoming payment can be visibly present but not yet part of the
spendable balance. It is also why the application database can coordinate a
background task without becoming the wallet.

[Read the funds model](how-safebox-treats-funds.md){ .md-button }

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
