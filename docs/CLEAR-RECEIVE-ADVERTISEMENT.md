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

Leaving `SAFEBOX_CLEAR_MINTS` and `SAFEBOX_CLEAR_UNITS` empty advertises
general Clear receive support. Wallets still validate the mint, unit, keyset
ids, and token payload after decrypting the transfer.

## Wallet display metadata

For each pending Clear balance, Safebox Web reads the mint's `/v1/info`
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
and CMU, followed by independent Clear transfer history. Cash payment
finalization does not process Clear transfers.

This terminology is deliberate: cash is used for payments, while CMUs are
transferred as credits for the products, services, or purposes defined by their
issuing program. A Clear transfer may support an exchange, allocation, gift, or
disbursement without being represented as cash payment.

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

If mint or unit restrictions are configured, the descriptor also includes:

```json
{
  "mints": ["http://127.0.0.1:3338"],
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

## Trust boundary

This is an application-level setting. It says that this Safebox Web deployment
currently advertises Clear receive support for claimed handles.

The advertisement does not prove that a particular user will accept every
Clear token, does not finalize a transfer, and does not bind the wallet to a
particular Clear mint unless `SAFEBOX_CLEAR_MINTS` or `SAFEBOX_CLEAR_UNITS` are
configured.

Pending Clear transfers can be deleted individually before finalization.
Deletion erases the stored bearer token and leaves only a minimal tombstone in
the relay-backed receipt journal so a later relay scan does not restore the
transfer.
Finalized Clear proof state and transaction history cannot be deleted through
this pending-transfer action.
