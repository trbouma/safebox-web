---
title: Receiving Funds
description: One Safebox flow for Lightning and future Clear CMU payment requests.
---

# Receiving Funds

Safebox Web provides one **Receive Funds** page for receiver-created payment
requests. The user chooses a method, enters an amount, and presents the request
to the payer.

## Lightning today

The current method asks the Acorn home mint for a Lightning invoice. Safebox
presents the invoice as QR and text, then waits for the user to indicate that
the payment has been completed before asking Acorn to finalize it.

The browser does not poll the mint or hold application wallet state.

## Clear Mint Notes next

The same page is designed to request Mint Notes from a particular Clear Mint
Unit. A compatible request will use Cashu NUT-18 with the exact
`cmu-<keyset-id>` and a strict accepted-mint list.

The payment remains pending until the received proofs have been validated and
refreshed. This gives Lightning and direct Mint Note transfer one coherent
user-facing flow without pretending that they use the same settlement method.

[Read the implementation design](https://github.com/trbouma/safebox-web/blob/main/docs/RECEIVE-FUNDS-PAYMENT-REQUEST-DESIGN.md){ .md-button .md-button--primary }
