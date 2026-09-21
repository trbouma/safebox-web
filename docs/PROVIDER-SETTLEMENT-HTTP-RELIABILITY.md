# Provider settlement polling: HTTP reliability milestone

## What prompted the change

After deploying the read-only Acorn `get_quote_state()` dependency, incoming
payments began progressing again. However, discovery logs reported only
`HTTPStatusError`. Individual diagnostic requests returned HTTP 200, so the
earlier failures could not be attributed conclusively to rate limiting, a mint
outage, or a proxy. A healthy worker heartbeat did not establish successful
settlement polling.

## Behavior

- Settlement discovery logs the payment ID, mint URL, HTTP status, retry delay,
  and a restricted response summary. Known error phrases and numeric codes are
  retained; arbitrary response content is redacted to avoid exposing invoices
  or bearer material.
- Discovery requests run one at a time per mint, with up to eight independent
  mints checked concurrently. Each read has a 15-second deadline.
- HTTP 429 starts a mint discovery cooldown of at least 60 seconds. Numeric
  and HTTP-date `Retry-After` values are honored. HTTP 5xx and transport failures
  use exponential backoff from five seconds, with positive jitter and a
  five-minute cap (a longer Retry-After takes precedence).
- A successful read resets that mint's consecutive failure counter. Requests
  skipped during cooldown do not increment payment check attempts. Other HTTP
  errors are retried after five minutes for the affected quote without blocking
  the mint's other quotes.
- Existing pending rows receive durable `next_check_at` deadlines, including
  rows outside the current batch. Those deadlines survive worker restart;
  the in-memory consecutive-failure counter resets on restart. A newly created
  row after restart can still make an initial discovery request.
- Unpaid invoices are checked every five seconds for their first five minutes,
  every 30 seconds up to one hour, every five minutes up to one day, and every
  30 minutes thereafter. These are eligibility intervals, not delivery SLAs;
  worker load and cooldowns may delay a check.

HTTP discovery failures preserve the quote, payment status and delivery state.
They never trigger issuance or delivery directly. Paid discovery still persists
`PAID_RECONCILIATION_PENDING`, and the existing serialized wallet owner performs
issuance and delivery. These retries apply only to read-only quote discovery;
they do not introduce blind retries of ambiguous mint or relay mutations.

Diagnostics report `SETTLEMENT_DISCOVERY_DEFERRED` independently of the worker
heartbeat. A subsequent successful read clears the discovery error. An unpaid
quote is still not evidence of an incoming payment.

## Verification

Regression tests cover rate limits, server errors, timeouts, increasing backoff,
Retry-After dates, mint isolation, response redaction, restart deadlines, recovery
to a single delivery, progressive unpaid polling, and diagnostic warnings.

Deploy by rebuilding and recreating the web and service-worker containers. No
database migration or queue reset is required. The deployment must include an
Acorn version exposing `get_quote_state()`.
