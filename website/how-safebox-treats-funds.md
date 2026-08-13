---
title: How Safebox Treats Funds
description: Why Safebox separates payment arrival, mint confirmation, and spendable balance.
---

# How Safebox Treats Funds

A payment should feel immediate without pretending that every part of the
network has already finished.

Safebox therefore separates three useful facts:

```text
Arrived     -> a transfer addressed to this Acorn is visible on its relay
Pending     -> the transfer is preserved and awaiting mint finalization
Spendable   -> compatible proofs have been accepted and confirmed by the mint
```

The user sees an incoming payment as soon as Safebox can read the transfer.
The confirmed balance changes only after Acorn has completed the mint and
relay checks needed to make the value spendable.

## Immediate assurance, honest balance

Waiting for every network operation before showing anything creates needless
uncertainty. Adding an unverified payment directly to the balance creates a
different and more serious problem: the interface can promise value that is
not yet spendable.

Safebox uses two signals instead:

- **Pending transactions** show that funds have arrived for the Acorn. Each
  item can show its amount, arrival time, sender reference, and current stage.
- **Confirmed balance** shows the proofs Acorn can currently treat as
  mint-confirmed and spendable.

Pending does not mean lost or failed. It means Safebox is preserving the
difference between evidence of delivery and evidence of finality.

## Finalization can take time

Relays and mints do not always answer at the same speed. A relay may accept an
event before returning it in a query. A mint may need time to check or refresh
proofs. Transaction history also needs to become readable after publication.

Safebox can run this work in the background. The user starts finalization,
leaves the page, and returns later to see whether it completed or requires
another check. The slower workflow does not hide the original arrival.

This is intentionally different from a loading spinner that holds one browser
request open until everything succeeds.

## Different systems answer different questions

No single service is treated as the source of every truth.

| System | What it establishes |
| --- | --- |
| **Acorn key** | Which component can authorize and decrypt its controlled state |
| **Relay** | Which signed transfer and wallet events are currently available |
| **Mint** | Whether particular Cashu proofs are spent, pending, or unspent |
| **Acorn proof checks** | Whether stored proofs are structurally compatible and usable |
| **Safebox Web** | How the user starts, observes, and understands the workflow |

This separation became especially important during pre-release testing. A
mint could report a canonical proof identifier as unspent even though a proof
created by historical Acorn code did not conform to the mandatory Cashu
construction and could not be redeemed. Acorn now checks protocol
compatibility separately instead of treating `UNSPENT` as the whole answer.

The lesson is broader than that one defect:

> A trustworthy balance is a conclusion built from several kinds of evidence,
> not merely a number read from storage.

## Coordination is not wallet ownership

Safebox Web records enough non-secret information to coordinate background
work: the component public key, progress, amounts, timestamps, and a temporary
job lease. That prevents two browser tabs or web workers from trying to
finalize the same wallet simultaneously.

The application database does not become the wallet. It does not store the
recipient nsec, recovery words, Cashu proofs, incoming bearer tokens, or
private records. The recipient key is supplied by the encrypted browser
session and exists in the trusted web process only while Acorn performs the
requested work.

If that process stops, relay-backed transfers remain the recovery queue. The
user can reconnect and continue.

## Provider payments preserve the boundary

A registered Safebox handle can receive an ordinary Lightning payment through
the service Acorn operated by the provider:

```text
Lightning payer
  -> provider invoice
  -> service Acorn receives settlement
  -> private transfer to the recipient relay
  -> recipient sees pending funds
  -> recipient Acorn finalizes them
```

The service Acorn acts as the application's treasurer and delivery agent. It
does not receive the recipient's private key and cannot silently become the
recipient wallet.

The reverse direction also works. After correcting the historical proof
compatibility issue, a connected Safebox wallet completed a Lightning payment
to an independently operated Swiss Bitcoin Pay application. That demonstrates
that Safebox is not limited to paying itself or another Safebox deployment.

## What this makes possible

This model supports ordinary connected payments today and creates a foundation
for continuity under less reliable conditions:

- an incoming payment can remain visible while a mint is temporarily slow;
- direct Safebox transfers can use the same receiving experience;
- community or local relay infrastructure can preserve delivery evidence;
- the app can be replaced without redefining the wallet balance; and
- future continuity modes can defer finalization without disguising it.

Safebox aims for a calm result: show what arrived, state what is still pending,
and reserve the balance for what can actually be spent.

[Read the product architecture](product-architecture.md){ .md-button .md-button--primary }
[Read the trust boundary](trust-boundary.md){ .md-button }
[View the technical milestone](https://github.com/trbouma/safebox-web/blob/main/docs/FUNDS-ARRIVAL-AND-FINALIZATION-MILESTONE-2026-08-13.md){ .md-button }
