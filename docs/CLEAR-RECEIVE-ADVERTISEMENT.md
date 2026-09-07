# Clear Receive Advertisement

## Status

Implemented as an application-level NIP-05 capability advertisement.

## Purpose

Safebox Web can advertise that claimed NIP-05 handles are willing to receive
Clear token transfers. The advertised capability is discovery metadata only:
Safebox Web does not receive, custody, unwrap, refresh, or store Clear tokens
in this flow.

The receiver wallet, currently Acorn, later queries its relay, unwraps the
NIP-59 gift wrap, validates the encrypted payload, and stores the Clear token
in its own pending Clear transfer receipt storage.

Safebox Web keeps ordinary page reads non-mutating. Wallet and `/clear` page
loads invoke `sweep_clear_transfers(preview_only=True)` so relay-visible kind
`1059` gift wraps with inner kind `7379` appear immediately as pending Clear
transfers. Previewing does not store receipts or advance the receive cursor.

**Check for Clear Transfers** performs the explicit mutating receive operation:
it stores discovered transfers as pending receipts and advances the cursor.
Accepting a previewed transfer stores that exact event before running the normal
acceptance workflow. These actions remain separate from cash payment
finalization and do not process kind `7378` ecash transfers.

## Settings

Enable the advertisement with:

```env
SAFEBOX_CLEAR_RECEIVE_ENABLED=true
```

Optional restrictions:

```env
SAFEBOX_CLEAR_MINTS=http://127.0.0.1:3338,https://clear.example
SAFEBOX_CLEAR_UNITS=cmu-00ce29eeaf094301
```

Separately list only mint routes that another Safebox can reach:

```env
SAFEBOX_CLEAR_EXTERNAL_MINTS=https://clear.example
```

`SAFEBOX_CLEAR_MINTS` controls configured acceptance and metadata access;
`SAFEBOX_CLEAR_EXTERNAL_MINTS` advertises public routes already known to the
receiver. That advertisement is not exhaustive: a receiver may learn about a
new public HTTPS mint from its first Clear transfer.
Never put an internal Docker route such as `http://clear:3339` in the external
list.

## Wallet display metadata

For each pending or spendable Clear balance, Safebox Web reads the mint's `/v1/info`
metadata and prefers `currency.friendly_alias` and
`currency.friendly_unit_alias` for display. The canonical mint URL and complete
CMU remain visible and continue to identify the balance. Metadata is accepted
only when the response advertises the same mint URL and CMU as the receipt.

Balances are grouped by exact `(mint, CMU)` identity. Safebox Web may total
receipts within one such balance, but it never adds amounts across different
mints or CMUs. The summary reports receipt and balance counts instead of a
cross-currency amount.

The wallet presents Cash Balance and Clear Balances as separate links. Cash
Balance opens `/transactions`, which presents incoming value as cash payments
with cash transaction history and finalization controls. Clear Balances opens
`/clear`, which presents CMU movement as Clear transfers grouped by exact mint
and CMU. The page orders spendable **Clear Balances**, actionable **Pending
Clear Transfers**, and completed **Clear Transaction History** as separate
sections. Cash payment finalization does not process Clear transfers.

Each pending transfer offers an explicit **Accept Clear Transfer** action.
Acorn refreshes the bearer proofs with the exact issuing mint and CMU, verifies
their separate kind `7380` storage, appends kind `7381` history, marks the
receipt accepted, and erases its bearer token. The resulting amount is shown as
spendable in that exact Clear Balance. Pending and spendable amounts are never
combined in the display.

Acceptance runs as a session-bound background job because mint refresh and
exact relay readback may outlast an HTTP gateway request. The browser returns
immediately and the Clear page reports running, completed, interrupted, or
failed status. A database lease prevents two web workers from accepting Clear
receipts for the same Acorn concurrently. The lease contains only the npub,
event identifier, progress, and result metadata—never an nsec or bearer token.
The receipt and proof state remain relay-backed and Acorn acceptance remains
idempotent, so an interrupted job can be started again safely.

This terminology is deliberate: cash is used for payments, while CMUs are
transferable units moved as credits for the products, services, or purposes
defined by their issuing program. Transferability does not make those units
cash. A Clear transfer may support an exchange, allocation, gift, or
disbursement without being represented as a cash payment.

Alias lookup follows a narrow network policy: public HTTPS mint URLs may be
queried without redirects, while an HTTP mint is queried only when its exact
URL is configured in `SAFEBOX_CLEAR_MINTS`. Failed, oversized, mismatched, or
untrusted responses fall back to the canonical CMU and mint URL.

In Docker Compose, pass these environment values into the `safebox-web`
service:

```yaml
environment:
  SAFEBOX_CLEAR_RECEIVE_ENABLED: "${SAFEBOX_CLEAR_RECEIVE_ENABLED:-false}"
  SAFEBOX_CLEAR_MINTS: "${SAFEBOX_CLEAR_MINTS:-}"
  SAFEBOX_CLEAR_EXTERNAL_MINTS: "${SAFEBOX_CLEAR_EXTERNAL_MINTS:-}"
  SAFEBOX_CLEAR_UNITS: "${SAFEBOX_CLEAR_UNITS:-}"
```

After changing environment values, recreate the container:

```sh
docker compose up -d --force-recreate safebox-web
```

Check the value inside the container:

```sh
docker compose exec safebox-web printenv SAFEBOX_CLEAR_RECEIVE_ENABLED
```

## NIP-05 response shape

When enabled, `/.well-known/nostr.json?name=alice` includes a `clear` section:

```json
{
  "names": {
    "alice": "<recipient-pubkey>"
  },
  "relays": {
    "<recipient-pubkey>": [
      "wss://relay.getsafebox.app"
    ]
  },
  "clear": {
    "alice": {
      "protocols": ["clear-token-transfer"],
      "transports": ["nip59"],
      "kinds": [7379]
    }
  }
}
```

If externally reachable mint routes or unit restrictions are configured, the
descriptor also includes:

```json
{
  "mints": ["https://clear.example"],
  "units": ["cmu-00ce29eeaf094301"]
}
```

## Transfer format advertised

The advertised format is:

```text
outer relay-visible event: kind 1059
inner Clear transfer: kind 7379
protocol: clear-token-transfer
transport: nip59
```

The sender should publish the gift wrap to the relay hints returned in the
same NIP-05 response.

## Current routing profiles

Safebox resolves recipient delivery and mint reachability independently:

| Recipient | Mint route | Result |
| --- | --- | --- |
| Same Safebox instance | Internal HTTP or public HTTPS | Explicit internal relay |
| External Safebox | Public HTTPS | Signed external inbox route |
| External Safebox | Internal HTTP only | Rejected before proof export |

A same-instance handle is resolved from the local claimed-handle directory and
does not require HTTPS discovery. An external recipient must advertise Clear
transport support and an externally reachable relay. A public HTTPS mint may
be new to the receiver and need not appear in its NIP-05 descriptor beforehand.

This interim profile was manually validated on September 6, 2026, for an
internal-mint transfer between two Mainstay wallets and a public-mint transfer
from an independent Safebox into Mainstay.

## Trust boundary

This is an application-level setting. It says that this Safebox Web deployment
currently advertises Clear receive support for claimed handles.

The advertisement does not prove that a particular user will accept every
Clear token or finalize a transfer. For this interim profile, a sender treats a
well-formed HTTPS mint URL as publicly reachable even when it is not yet in the
receiver's advertised mint list. Internal HTTP mint routes remain restricted
to same-instance recipients. This is a URL-based assumption, not a network
probe or cryptographic service-identity proof.

Pending Clear transfers can be deleted individually before finalization.
Deletion erases the stored bearer token and leaves only a minimal tombstone in
the relay-backed receipt journal so a later relay scan does not restore the
transfer.
Finalized Clear proof state and transaction history cannot be deleted through
this pending-transfer action.
