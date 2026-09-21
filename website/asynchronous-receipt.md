---
title: Asynchronous Receipt
description: An online receiving presence for an Acorn whose owner can be offline.
---

# Asynchronous Receipt

You should not have to keep a wallet open to receive a transfer. Safebox Web
provides an online receiving presence for your Acorn, so someone can send a
Lightning payment while you are offline. Your Acorn can retrieve and finalize
the resulting transfer later.

## An identity and a path to it

**The npub identifies the Acorn; the Lightning address provides a path to it.**
A familiar address such as `alice@example.org` lets a payer interact with a
provider that knows where to deliver the transfer. It does not replace the
Acorn's public-key identity.

An address, provider, or relay can change while the Acorn keeps its key.
Those changes require updating the delivery arrangements and preserving access
to earlier transfers. They do not change who controls the Acorn.

## What happens while you are offline

1. The payer requests an invoice through your Lightning address and pays it.
2. The provider confirms settlement and uses its own service Acorn to obtain ecash.
3. It delivers an encrypted transfer to your Acorn's registered relay.
4. Your Acorn retrieves and finalizes the transfer when you return.

These stages can take different amounts of time. Safebox Web distinguishes a
payment confirmed by the mint, a transfer awaiting delivery, a pending receipt,
and your finalized balance. An incoming amount can therefore be visible before
it becomes spendable. Applicable fees can reduce the final credit.

## Clear transfers can arrive while you are offline

Clear takes a direct path: the sender delivers an encrypted transfer to your
Acorn's relay, including when responding to a Clear payment request. There is
no Lightning invoice or provider settlement queue in this path.

Your Acorn retrieves the transfer and accepts it against the issuing mint when
you return. Until that succeeds, the credits remain pending. Safebox Web can
run acceptance in the background, while Acorn handles proof validation and
recovery. If the outcome is uncertain, the app retains a review state rather
than automatically repeating the transfer.

## Receiving does not require opening your wallet

The receiving worker uses its own Acorn and your public key. It does not need
your recovery words or private key, and it cannot finalize your wallet on your
behalf. The recipient Acorn performs that step under your authority.

Safebox Web coordinates the receiving service, schedules work, and explains
progress. The safebox-acorn kernel handles keys, proofs, encryption, wallet
recovery, and transfer acceptance. This keeps the receiving service separate
from control of the recipient's wallet.

## A clear trust boundary

Asynchronous receipt still depends on the gateway honoring its obligations,
the mint honoring its notes, and the relay retaining the transfer. It is not
an unqualified trustless-payment claim or a promise of indefinite offline
storage. Operators must choose retention appropriate to their users.

A connected browser session has a separate trust boundary: Safebox Web handles
the session's secret material while carrying out authorized wallet operations.
The provider's offline receiving path does not require that session.

[Read the trust boundary](trust-boundary.md){ .md-button }
[Read the architecture note](https://github.com/trbouma/safebox-web/blob/main/docs/ASYNCHRONOUS-RECEIPT-ARCHITECTURE.md){ .md-button }
