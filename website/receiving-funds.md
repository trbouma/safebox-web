---
title: Receiving Funds
description: Safebox receiving flows for Lightning cash payments and future Clear CMU transfers.
---

# Receiving Funds

Safebox Web provides one **Receive Funds** page. Lightning requests receive
cash payments. A future Clear method will request transfers of a particular
CMU without presenting those credits as cash.

## Lightning today

The current method asks the Acorn home mint for a Lightning invoice. Safebox
presents the invoice as QR and text, then waits for the user to indicate that
the payment has been completed before asking Acorn to finalize it.

The browser does not poll the mint or hold application wallet state.

## Clear transfers next

The same page is designed to request a transfer from a particular Clear Mint
Unit. A compatible request will use Cashu NUT-18 with the exact
`cmu-<keyset-id>` and a strict accepted-mint list.

The transfer remains pending until the received proofs have been validated and
refreshed. Lightning cash payments and Clear transfers can share an entry point
without implying that they are the same kind of value or transaction.

[Read the implementation design](https://github.com/trbouma/safebox-web/blob/main/docs/RECEIVE-FUNDS-PAYMENT-REQUEST-DESIGN.md){ .md-button .md-button--primary }
