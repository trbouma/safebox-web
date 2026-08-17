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
in its own pending Clear receipt storage.

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
Clear token, does not finalize payment, and does not bind the wallet to a
particular Clear mint unless `SAFEBOX_CLEAR_MINTS` or `SAFEBOX_CLEAR_UNITS` are
configured.

