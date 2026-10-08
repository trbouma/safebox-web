---
title: Policy Briefs
description: Safebox Web briefs on digital wallets, user-controlled continuity, records, balances, disclosure, and trust.
---

# Policy Briefs

Safebox Web policy briefs connect practical implementation work with the
larger choices facing wallet designers, public institutions, communities, and
standards bodies. They are written for first-time readers who need to
understand why the architecture matters before examining its protocols and
code.

## Identity assurance and continuity

### High Assurance Identity and User-Controlled Continuity

**NIST SP 1800-42A | October 2026**

What would Safebox need to participate in a high-assurance digital identity
workflow? This brief distinguishes mDL verification, holder authentication,
issuer trust, and institutional accountability from Safebox's existing
record previews, OpenETR checks, and portable state. It proposes a bounded
prototype rather than a claim of certification or regulatory compliance.

[Read the NIST policy brief](high-assurance-identity-and-user-controlled-continuity.md){ .md-button .md-button--primary }

[Detailed technical analysis](https://github.com/trbouma/safebox-web/blob/main/docs/NIST-SP-1800-42A-ANALYSIS.md)

### Beyond the Device-Bound Wallet

Android's high-assurance credential architecture provides a strong answer to
one essential wallet question: how can a remote issuer know that a protected
key was generated and used inside approved device hardware?

Safebox and Acorn address the adjacent continuity question: how do
user-controlled keys, balances, records, and recovery survive the loss of one
device, application, relay, or provider? This brief explains why mature wallet
policy needs both device-bound security and relay-backed continuity—and why
they should remain distinct, composable layers.

[Read the policy brief](beyond-the-device-bound-wallet.md){ .md-button .md-button--primary }

## Continue exploring

For product behavior and implementation boundaries, see
[Trust Boundary](../trust-boundary.md),
[Graduated Disclosure](../graduated-disclosure.md), and
[Project Status](../project-status.md).
