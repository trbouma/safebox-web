---
title: Project Status
description: Current status of Safebox Web.
---

# Project Status

Safebox Web is developer-stage software. It should be used with small test
balances and non-critical records while the surrounding Acorn, Grove,
Spurline, Mainstay, and Lockbox release gates continue to mature.

## Implemented direction

Safebox Web currently provides a FastAPI, server-rendered wallet app for
onboarding, Acorn connection, wallet views, private records, record sharing and
presentation, handles, receiver-created Lightning payment requests, Lightning
payments, incoming ecash,
Continuity Payments, confirmed-versus-pending balance signals, pending
transaction finalization, and selected evidence-verification workflows.

It also presents organization-issued Clear transfers through a separate wallet
path: NIP-05 receive advertisement, explicit kind `7379` relay checking,
multi-mint Clear Balances, mint alias resolution, independent Clear Transfer
History, and durable pending-transfer deletion.

The application is intentionally modest in browser-side authority. JavaScript
is used for progressive interaction and device input such as QR acquisition;
workflow authority remains server-side and Acorn-centered.

## August 2026 funds milestone

Safebox Web now distinguishes relay-visible arrival, pending mint finalization,
and confirmed spendability. A user sees individual pending transactions and
their aggregate amount immediately below the confirmed balance, while a
session-bound background task completes mint and relay verification. The
recipient key remains in web-process memory; only public coordination and
progress are stored in the application database.

After the corresponding Acorn proof-safety correction, a connected wallet also
completed an outgoing Lightning payment to an independently operated Swiss
Bitcoin Pay application. This demonstrates practical external
interoperability while small-value and release-hardening constraints remain in
force.

[Read the complete milestone](https://github.com/trbouma/safebox-web/blob/main/docs/FUNDS-ARRIVAL-AND-FINALIZATION-MILESTONE-2026-08-13.md){ .md-button .md-button--primary }

## August 2026 Clear transfer milestone

Safebox Web now demonstrates an approachable wallet model for organization-
issued value. A public Clear mint issued CMUs into its treasury, sent an exact
amount to a Safebox NIP-05 address, and published a private kind `7379`
transfer. The recipient used **Check for Clear Transfers** and saw the amount
under a distinct Clear Balance with mint-provided aliases.

The same workflow demonstrated durable deletion of an unwanted pending
transfer. Ordinary kind `7378` cash payment receipt continued to operate
through its existing path.

Recipient finalization and exact-balance onward Clear spending are now
implemented. Durable recovery from an unknown outgoing delivery outcome and
broader interoperability hardening remain future work.

[Read the Clear transfer product milestone](https://github.com/trbouma/safebox-web/blob/main/docs/CLEAR-TRANSFER-PRODUCT-MILESTONE-2026-08-17.md){ .md-button .md-button--primary }

## August 2026 verifiable records milestone

Safebox Web now resolves and preserves effective MIME metadata for Original
Records and provides specialized inline previews for PKPASS Wallet passes,
JSON W3C Verifiable Credentials, EUDI PID mdocs, and ISO mobile driving
licences.

PID and mDL previews expose semantic identity fields while keeping technical
CBOR available separately. The views are explicitly marked as previews rather
than signature verification. Exact original bytes and their Uniform Digest
Anchors remain available for native verifiers, OpenETR, or other independent
attestation and control protocols.

## Current focus

- clearer local development and deployment paths;
- better documentation for Safebox Web as a standalone product;
- local relay and blob-store integration through Spurline and Grove;
- hardening payment and proof maintenance workflows;
- expanding native verifier adapters and digest-bound attestation workflows
  without coupling them to generic blob storage;
- preserving the hypermedia boundary as PWA features are explored; and
- carrying the proven Safebox workflows into the future Mainstay application
  and Lockbox appliance profile.

## Related technical notes

Detailed design notes remain in the repository's `docs/` directory. Start
with:

- [Hypermedia Architecture](https://github.com/trbouma/safebox-web/blob/main/docs/HYPERMEDIA-ARCHITECTURE.md)
- [PWA Hypermedia Boundary](https://github.com/trbouma/safebox-web/blob/main/docs/PWA-HYPERMEDIA-BOUNDARY.md)
- [Deployment](https://github.com/trbouma/safebox-web/blob/main/docs/DEPLOYMENT.md)
- [Local Acorn Vault Design Note](https://github.com/trbouma/safebox-web/blob/main/docs/LOCAL-ACORN-VAULT-DESIGN-NOTE.md)
- [Funds Arrival and Finalization Milestone](https://github.com/trbouma/safebox-web/blob/main/docs/FUNDS-ARRIVAL-AND-FINALIZATION-MILESTONE-2026-08-13.md)
- [Clear Transfer Product Milestone](https://github.com/trbouma/safebox-web/blob/main/docs/CLEAR-TRANSFER-PRODUCT-MILESTONE-2026-08-17.md)
