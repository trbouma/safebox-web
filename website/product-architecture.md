---
title: Product Architecture
description: How Safebox Web fits with Acorn, Grove, Spurline, and Lockbox.
---

# Product Architecture

Safebox Web is designed as a sibling product in a broader local-first family.
Each component has a narrow responsibility and can be developed, tested, and
operated independently.

The user-facing promise is simpler than the component diagram: Safebox Web is
the wallet app; the other pieces help preserve funds, records, and evidence
without making one hosted service permanent.

```text
              Safebox Web
                   |
                   v
                Acorn
          /    |     \
         v     v      v
    Spurline  Grove  OpenETR
   local relay blobs  evidence
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

</div>

## Toward Lockbox

Lockbox is the future appliance-like product profile for running this family
locally. The initial target is FreeBSD on a Raspberry Pi 4, with
hardware-backed trust boundaries such as a keypad and TROPIC01 HSM.

In that model:

- Safebox Web is the local user interface;
- Acorn is the controlled protocol state;
- Spurline is the local evidence relay;
- Grove is the local encrypted blob store;
- OpenETR provides signed transferable-record evidence; and
- external services remain useful, but not always required for local
  continuity.

Safebox Web should be able to describe this in plain continuity modes:
**Connected Mode**, **Local Mode**, **Mobile Mode**, and **Community Mode**.
The current app can start with Connected Mode and later determine the active
mode from service reachability, local pairing, bridge state, and community
mesh participation.

Continuity Payments are the payment expression of those modes. In Connected
Mode, Safebox Web can prefer direct ecash delivery when a Lightning address
resolves to another Safebox recipient. In Local or Community Mode, the future
Lockbox behavior should support provisional in-kind proof transfers with clear
user approval, explicit non-final status, and mint reconciliation when external
infrastructure returns.

## Related repositories

- [Safebox Acorn](https://github.com/trbouma/safebox-acorn)
- [Grove](https://github.com/trbouma/grove)
- [Spurline](https://github.com/trbouma/spurline)
- [Safebox Web](https://github.com/trbouma/safebox-web)
- [OpenETR](https://github.com/trbouma/openetr)
