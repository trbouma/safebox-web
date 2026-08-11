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
    <img src="assets/images/safebox-phone-app.png" alt="Safebox Web wallet screen shown as a mobile app">
  </div>
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
history, deposits, payments, and record sharing flows.

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

## Part of the local-first family

Safebox Web is a sibling product in the Acorn family:

```text
Safebox Web -> Acorn -> Spurline
                      -> Grove
```

It is useful as a hosted or self-hosted web app today, and it is shaped to
become one of the local applications inside the future Lockbox appliance.

<div class="safebox-family" markdown>

[**Acorn**  
Protocol authority for keys, records, recovery, and value.](https://github.com/trbouma/safebox-acorn)

[**Grove**  
Blossom storage for opaque, content-addressed blobs.](https://github.com/trbouma/grove)

[**Spurline**  
A local-first relay for individuals and communities.](https://github.com/trbouma/spurline)

[**Lockbox**  
The future appliance profile for local authority and continuity.](product-architecture.md)

</div>

## Built for continuity

Safebox Web is deliberately server-rendered, dependency-aware, and modest in
what it asks the browser to do. The browser provides the session and
progressive interactions. Acorn performs wallet and record operations. Relays,
mints, Grove servers, and later local Lockbox services remain explicit
replaceable dependencies.

[Read the radical rewrite](radical-rewrite-of-architecture.md){ .md-button .md-button--primary }
[Read the product architecture](product-architecture.md){ .md-button .md-button--primary }
[Get started](getting-started.md){ .md-button }
