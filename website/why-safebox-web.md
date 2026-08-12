---
title: Why Safebox Web?
description: Why Safebox Web exists as a sibling product to Acorn, Grove, and Spurline.
---

# Why Safebox Web?

People need a usable place to work with portable keys, records, funds, and
evidence. Most people do not want to operate directly through scripts, raw
relay events, or wallet internals.

Safebox Web exists to make a portable wallet practical without making the app
the authority.

That means the app starts from a simple user promise: funds, records, and
evidence should remain usable when a device, provider, relay, or application
changes.

## The app is not the authority

Safebox Web provides the human workflows:

- creating and connecting a wallet;
- reviewing balances and proof health;
- receiving and sending value;
- storing and reading private records;
- attaching protected original records;
- sharing or presenting records by QR; and
- claiming handles and using Lightning address flows.

The authority remains in the user's Acorn. The web app should be replaceable.

## Local-first, not isolated

Local-first does not mean disconnected by default. It means the person or
community has a workable local continuity path when hosted services, global
connectivity, or a particular operator are unavailable.

Safebox Web can use public infrastructure, private infrastructure, or local
services. In the Lockbox direction, it can sit beside Acorn, Spurline, and
Grove on the same small appliance.

## Practical continuity

For most people, the starting point is ordinary: they open Safebox Web through
a web-connected service, check a balance, receive value, store a record, or
present evidence. The experience should feel familiar and convenient.

The important difference is underneath. The app should not be the only place
the wallet can live. If a hosted service is unavailable, a provider changes
terms, or the wider internet is down, the same Acorn-controlled state should
have a local continuity path.

In that situation, Safebox Web can fall back to local services:

- a local Acorn execution environment for keys, funds, records, and recovery;
- a local Spurline relay for signed events and wallet state;
- a local Grove service for protected original records and larger blobs; and
- later, nearby community infrastructure or mesh transport when wider
  connectivity is unavailable.

Payment continuity follows the same pattern. In ordinary connected use,
Safebox Web can use mints, Lightning, and direct Safebox-to-Safebox ecash
transfers. It now also demonstrates Continuity Payments between Safebox
addresses using previously issued ecash. The recipient sees the same simple
result whether the sender had full connectivity or not: received value is
pending until it can be finalized with the mint.

This creates a useful distinction on the wallet screen:

- **Confirmed** is mint-confirmed and spendable.
- **Pending** has been received and preserved but still awaits finalization.

The user can finalize pending transactions from the transaction page. When the
mint is unavailable, those transactions remain pending and the confirmed
balance stays unchanged. Connectivity can return later without hiding what was
received or overstating what is final.

Safebox Web proves this experience today. Mainstay is the future unified
application that will carry it across records, identity, and payments.
Lockbox is the hardware-first form of the local fallback: a small local home
for Mainstay, Acorn, Spurline, Grove, and related services people may normally
reach through a web-connected service.

<figure class="safebox-continuity-figure" markdown>
![Concept illustration of a phone running Safebox Web beside a compact Lockbox-style Safebox appliance with an integrated keypad, NFC tap point, short LoRa-style antenna, and physical Wi-Fi control, ready beside a small emergency bag, passports, keys, and an emergency folder](assets/images/lockbox-appliance-concept.jpg)
<figcaption>Safebox Web proves the user experience today. Mainstay will unify that experience across continuity modes, while Lockbox provides a portable local home for the application and its services.</figcaption>
</figure>

The long-term goal remains simple:

> Lockbox preserves local authority, continuity, and evidence.

Safebox Web is the app a person can use now. Acorn is the portable wallet and
record state underneath it. Spurline preserves local relay events. Grove
preserves encrypted blob availability. Mainstay will bring those pieces into
one unified application, and Lockbox will provide the appliance that keeps
them available locally when continuity matters.

[Read the records-first architecture](radical-rewrite-of-architecture.md){ .md-button .md-button--primary }
