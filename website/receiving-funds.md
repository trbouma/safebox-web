---
title: Receiving Funds
description: Safebox receiving flows for Lightning cash payments and Clear CMU transfers.
---

# Receiving Funds

Safebox Web provides one **Receive Funds** page. Lightning requests receive
cash payments. The Clear method requests transfers of a particular
CMU without presenting those credits as cash.

## Lightning today

A registered Lightning address also provides an
[asynchronous receipt path](asynchronous-receipt.md). The provider receives and
delivers using its own service Acorn while the recipient is offline. This is
separate from creating an invoice directly for a connected Acorn below.

The current method asks the Acorn home mint for a Lightning invoice. Safebox
presents the invoice as QR and text, then waits for the user to indicate that
the payment has been completed before asking Acorn to finalize it.

The browser does not poll the mint or hold application wallet state.

## Clear transfers

The same page can request a transfer from a supported Clear Mint
Unit. The request uses Cashu NUT-18 with the exact
`cmu-<keyset-id>` and a strict accepted-mint list.

The transfer remains pending until the received proofs have been validated and
refreshed. Lightning cash payments and Clear transfers can share an entry point
without implying that they are the same kind of value or transaction.

The sender delivers an encrypted transfer to the recipient relay. The recipient
can be offline, then retrieve and accept it later through a background job.
Acceptance errors retain a review state, and retries use Acorn's recovery
logic. The web application does not independently retry mint swaps.

[Read the implementation design](https://github.com/trbouma/safebox-web/blob/main/docs/RECEIVE-FUNDS-PAYMENT-REQUEST-DESIGN.md){ .md-button .md-button--primary }
