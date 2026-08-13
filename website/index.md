---
title: Safebox Web
description: A local-first wallet app for individuals and communities.
---

<section class="safebox-hero" markdown>

# Safebox Web

<p class="safebox-tagline">A local-first wallet app for individuals and communities.</p>

<p class="safebox-intro">Safebox Web helps people hold funds, manage private records, receive value, and present portable evidence while keeping control portable across apps and infrastructure.</p>

<figure class="safebox-phone" markdown>
  <div class="safebox-phone-frame">
    <span class="safebox-phone-speaker" aria-hidden="true"></span>
    <img src="assets/images/safebox-phone-app.png" alt="Safebox Web mobile app in Connected Mode showing a mint-confirmed balance and pending transactions">
  </div>
  <figcaption>The large balance is mint-confirmed and spendable. Incoming value stays visible as pending until it can be finalized.</figcaption>
</figure>

[Why Safebox Web?](why-safebox-web.md){ .md-button .md-button--primary }
[Radical Rewrite of Architecture](radical-rewrite-of-architecture.md){ .md-button .md-button--primary }
[View the source](https://github.com/trbouma/safebox-web){ .md-button }

</section>

## A wallet app for funds, records, and evidence

Safebox Web gives people a practical place to create or connect a wallet,
hold private records, receive value, share evidence, and recover continuity
when surrounding services change.

Underneath the app is Acorn: the portable protocol state for keys, funds,
records, and recovery. The design keeps application convenience separate from
user authority, so Safebox Web can be replaced without replacing the wallet.

<section class="safebox-brand" markdown>

<img class="safebox-brand-mark" src="assets/images/safebox-logo.png" alt="Safebox logo">

<p>Safebox Web is one interface for user-controlled funds, records, handles, and evidence.</p>

</section>

<div class="safebox-grid" markdown>

<article class="safebox-card" markdown>

### Connect

Create a new wallet or connect one you already control using recovery words or
a compatible private key.

</article>

<article class="safebox-card" markdown>

### Use

Work with relay-backed records, protected originals, handles, transaction
history, deposits, direct Safebox payments, Lightning payments, and record
sharing flows.

</article>

<article class="safebox-card" markdown>

### Verify

Connect private records to OpenETR evidence so exact artifacts can be checked
against signed origin and control history.

</article>

<article class="safebox-card" markdown>

### Move

The wallet state remains portable. The app can be replaced without replacing
the controlled key, relay-backed records, or recovery path.

</article>

</div>

## Web-enabled but local-first under the hood

Safebox Web can feel like an ordinary web app: open it in a browser, use clear
screens, scan QR codes, and share records. Underneath, the important state is
not meant to belong to the web deployment.

Acorn publishes encrypted records through Nostr relays. A relay can store and
serve a signed event, but it does not become the authority over what the record
means. The authority that travels with the record is the cryptographic evidence
of who published it, who can decrypt it, and which later signed events refer to
it.

That lets records live anywhere suitable: a public relay, a community relay, a
private relay, or a local Spurline relay inside a Lockbox. The web app is a
convenient way to use the record. The record's continuity does not depend on
that one web app remaining online.

## Show arrival now, confirm spendability honestly

A payment does not need to disappear behind a spinner while every relay and
mint operation finishes. Safebox shows incoming transfers as pending as soon
as they are visible for the Acorn. Each arrival remains separate from the
confirmed balance until Acorn establishes that its proofs are compatible,
accepted by the mint, and safely persisted.

<div class="safebox-grid safebox-grid--two" markdown>

<article class="safebox-card" markdown>

### Arrival is visible

Relay evidence gives the user immediate assurance that a payment was sent to
their Acorn.

</article>

<article class="safebox-card" markdown>

### Spendability is earned

The large balance changes only after mint confirmation and Acorn's proof
compatibility checks.

</article>

<article class="safebox-card" markdown>

### Finalization can continue

Longer relay and mint work runs in the background. The user can return later
without losing sight of what arrived.

</article>

<article class="safebox-card" markdown>

### Coordination is not custody

Safebox stores non-secret job progress, not the recipient's key, proofs, or
private wallet state.

</article>

</div>

[See how Safebox treats funds](how-safebox-treats-funds.md){ .md-button .md-button--primary }

## Part of the local-first family

Safebox Web is a sibling product in the Acorn family:

```text
Safebox Web -> Acorn -> Spurline
                      -> Grove

        practical foundation for
                 Mainstay
                    |
             Lockbox appliance
```

It is useful as a hosted or self-hosted web app today, and it is the practical
foundation for Mainstay, the future unified application. Lockbox is the
hardware-first appliance intended to run Mainstay and its supporting services
locally.

<div class="safebox-family" markdown>

[**Acorn**  
Protocol authority for keys, records, recovery, and value.](https://github.com/trbouma/safebox-acorn)

[**Grove**  
Blossom storage for opaque, content-addressed blobs.](https://github.com/trbouma/grove)

[**Spurline**  
A local-first relay for individuals and communities.](https://github.com/trbouma/spurline)

[**Mainstay + Lockbox**<br>
The future unified app and its hardware-first local appliance.](product-architecture.md)

</div>

## Built for continuity

Safebox Web is deliberately server-rendered, dependency-aware, and modest in
what it asks the browser to do. The browser provides the session and
progressive interactions. Acorn performs wallet and record operations. Relays,
mints, Grove servers, and later local Lockbox services remain explicit
replaceable dependencies.

Safebox Web uses two simple payment signals. The large balance is
**mint-confirmed and spendable**. Incoming ecash from connected payment flows
and Continuity Payments can remain **pending** until the user finalizes them.
If a mint cannot be reached, the value stays pending rather than being
presented as final.

Continuity Payments extend that model into local commerce. Safebox can transfer
previously issued ecash directly to another Safebox address, including when
mint access is interrupted. The recipient does not need a different receiving
workflow: the payment appears as pending and can be finalized when the mint is
available again. This working experiment is an important foundation for
Mainstay across Connected, Local, Mobile, and Community modes.

[Read the radical rewrite](radical-rewrite-of-architecture.md){ .md-button .md-button--primary }
[See how Safebox treats funds](how-safebox-treats-funds.md){ .md-button .md-button--primary }
[Read the product architecture](product-architecture.md){ .md-button .md-button--primary }
[Get started](getting-started.md){ .md-button }
