# Relay-Backed Invoice Finalization Milestone

Date: 2026-08-27

## What prompted the change

Live testing across old and new mints showed that a Lightning invoice can be
paid at the node while the browser request, process, or mint lookup does not
finish. The earlier direct-deposit path kept the quote primarily in a browser
form token and finalized only when the user pressed a check button. Closing the
page or checking with an Acorn whose configured mint had changed made recovery
unnecessarily fragile.

## What changed

- Acorn now persists every outstanding deposit quote in encrypted relay-backed
  state before Safebox shows the invoice.
- The journal binds each quote to its exact issuing mint; old and new mint
  deployments cannot be confused during recovery.
- Safebox starts a bounded background finalizer and returns the invoice page
  without tying completion to one HTTP request.
- The Receive Funds page lists outstanding invoices and provides explicit
  resume actions after navigation, reconnection, or interruption.
- Multiple outstanding quotes have independent coordination jobs.
- The application database stores only a quote hash and non-secret job status;
  it stores neither raw quotes nor Acorn private state.
- Acorn uses a stable transaction marker and removes an outstanding quote only
  after proof and history persistence.

## Boundary learned

Durability does not require moving wallet custody into the web application.
The relay can retain encrypted recovery intent while the web database retains
only scheduling information. The user-controlled key returns through the
encrypted session when work needs to resume. This preserves the separation of
key, code, data, and execution while still providing an ordinary asynchronous
web experience.

## Residual boundary

Safebox cannot continue mutating a user Acorn after every valid user session is
gone, because it deliberately does not persist the nsec. Reconnecting the Acorn
is the authority needed to resume. Mint issuance and relay publication also
remain separate systems; interruption testing at each boundary remains part of
release hardening.
