---
title: Project Status
description: Current status of Safebox Web.
---

# Project Status

Safebox Web is developer-stage software. It should be used with small test
balances and non-critical records while the surrounding Acorn, Grove,
Spurline, and Lockbox release gates continue to mature.

## Implemented direction

Safebox Web currently provides a FastAPI, server-rendered wallet app for
onboarding, login, wallet views, private records, record sharing and
presentation, handles, Lightning deposits, Lightning payments, incoming ecash,
and selected evidence-verification workflows.

The application is intentionally modest in browser-side authority. JavaScript
is used for progressive interaction and device input such as QR acquisition;
workflow authority remains server-side and Acorn-centered.

## Current focus

- clearer local development and deployment paths;
- better documentation for Safebox Web as a standalone product;
- local relay and blob-store integration through Spurline and Grove;
- hardening payment and proof maintenance workflows;
- preserving the hypermedia boundary as PWA features are explored; and
- preparing the family for a future Lockbox appliance profile.

## Related technical notes

Detailed design notes remain in the repository's `docs/` directory. Start
with:

- [Hypermedia Architecture](https://github.com/trbouma/safebox-web/blob/main/docs/HYPERMEDIA-ARCHITECTURE.md)
- [PWA Hypermedia Boundary](https://github.com/trbouma/safebox-web/blob/main/docs/PWA-HYPERMEDIA-BOUNDARY.md)
- [Deployment](https://github.com/trbouma/safebox-web/blob/main/docs/DEPLOYMENT.md)
- [Local Acorn Vault Design Note](https://github.com/trbouma/safebox-web/blob/main/docs/LOCAL-ACORN-VAULT-DESIGN-NOTE.md)
