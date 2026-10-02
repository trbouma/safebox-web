# Transfer status and recovery

The authoritative shared principles are in
[Acorn: Transfer resilience](https://github.com/trbouma/safebox-acorn/blob/main/docs/TRANSFER-RESILIENCE.md).
This guide describes Safebox Web's presentation and orchestration, not a second
implementation of wallet or mint logic.

## Sending and receiving Clear requests

Create a request under **Receive Funds** for a selected Clear balance. The
`creqA…` text identifies the requested amount, mint/unit, recipient, request ID,
and delivery relays. Senders can scan it or paste it into **Transfer a Balance →
Pay a Clear Request**. Both paths show a review and require explicit confirmation.

Sender **Transfer completed** means the send operation completed with relay
delivery acknowledged. It does not mean the recipient has accepted the credits.
The displayed event ID is the reference to use for delivery diagnostics.

**Pending Clear Transfer** means the receiver has discovered the transfer or
has a pending receipt. A relay preview can be visible before journal storage.
Spendable balance requires mint acceptance and durable proof state. A request's
checkmark additionally requires the matching accepted receipt and incoming
history with the requested mint/unit and sufficient net amount.

Direct NIP-05 transfers and request payments have different inner message
types and route selection. Success with one is useful evidence, not proof that
the other has completed.

## Monitoring and navigation

- Clear requests have a **five-minute** server monitoring window. The worker
  checks for matching transfers about every three seconds, subject to operation
  duration, and attempts acceptance through the existing acceptance worker.
- Clear-request browser status polling lasts up to **five and a half minutes**,
  allowing a grace period. It fetches server-rendered HTML, not mint data.
- Navigating away stops browser polling, not the already-running server worker.
  A worker can still stop because its window expires or its process exits.
- **Refresh Transfer Status** on a valid request page restarts monitoring when
  the request is still pending. Accepted/review states are not blindly retried.
- A pending transfer can also be accepted from **Clear → Incoming Transfers**.
  Do not make a second payment to trigger acceptance of the first.
- Lightning invoice monitoring remains **two minutes**, with a separate
  two-and-a-half-minute browser polling window and relay-backed quote recovery.

The Clear monitor uses a fresh Acorn under the requesting user's wallet
credentials, not the provider/service Acorn. It is an in-memory, bounded job,
not a permanent watcher. Its encrypted request ticket is user-bound and expires
after the shorter of the session lifetime and one hour. The ticket is not a
bearer payment token, but should still be kept out of shared logs.

## Explain the failure that is known

| Evidence | User-facing meaning |
| --- | --- |
| Mint connection fails | The mint is not accessible from this wallet right now. Internal mints require the appropriate network. |
| No matching balance for an internal mint | Explain the internal-network requirement without claiming a connectivity test failed. |
| No matching mint/unit balance | The wallet lacks credits accepted by this request; unrelated credits cannot simply be substituted. |
| No sufficient single keyset | The amount and fees must fit in one supported keyset even when the displayed aggregate balance is higher. |
| Ambiguous acceptance or delivery | Review the existing event/receipt; do not blindly send or swap again. |

Use **accessible** for reachability and **matching/compatible** for mint, unit,
or keyset requirements. An HTTP `200` on the status endpoint only means the
status representation was returned; it does not establish payment completion.

For missing incoming payments, compare the exact request and event recipient,
query the advertised relay from the correct network, then inspect discovery and
acceptance separately. See Acorn's guide for the known scan-stopping limitation.
For job failure details and safe retries, see
[Clear acceptance reliability](CLEAR-ACCEPTANCE-RELIABILITY.md).

## Deployment

Standalone Web defaults to `SAFEBOX_CLEAR_REQUEST_RELAY_POLICY=public`.
`SAFEBOX_NIP05_EXTERNAL_RELAYS` supplies public inbox defaults; initialization
does not overwrite an existing wallet-owned signed inbox record. Explicit
`mint-route` deployments use `SAFEBOX_CLEAR_REQUEST_INTERNAL_RELAY` for internal
mint routes. Follow [Mainstay's policy guide](https://github.com/trbouma/mainstay/blob/main/docs/CLEAR-REQUEST-RELAY-POLICY.md)
when Web runs under Mainstay.

Publish required Acorn changes, update Web's dependency lock, rebuild, and
recreate services to apply configuration. Generate a new request after route
changes: a copied old request still advertises its old routes.
# Address-based Clear sends

Clear transfers entered as a NIP-05 address now use the same background outgoing
payment worker as Lightning and scanned/pasted Clear requests. After validation
and address resolution, the POST redirects to `/pay/status`; token export and
relay delivery no longer run under the HTTP request's payment timeout.

The worker receives the resolved recipient, selected mint and unit, memo, and
explicit internal relay or external relay hints. Its authenticated Acorn runs
independently of the browser request. The existing per-wallet running-job guard
prevents a second submission from starting another send while that job is active.
Clear fees and status links remain in Clear units and point to Clear history.

This is an in-memory execution job with persisted status, not a durable restart
queue. Leaving the page does not cancel it, but process interruption still
requires review. Relay acknowledgement failures or timeouts are uncertain
outcomes: no automatic retry is made, and Clear failures are not written into
the Cash transaction journal. Recipient acceptance remains a separate step.
