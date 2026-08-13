# Informational Currency Rate Cache

## Summary

Safebox Web can show an estimated fiat value beneath an Acorn's satoshi
balance. Rates are informational display data: they never determine proof
ownership, mint balances, payment authorization, settlement, or transaction
history.

The implementation carries forward the useful part of Safebox 2's exchange
rate support while keeping it outside the Acorn kernel and outside each user's
wallet state.

## Process boundary

The singleton `service-acorn-worker` refreshes rates because it already provides
one operator-owned background process. The rate module does not receive the
service Acorn object or its key. This is an operator background responsibility,
not a wallet operation.

The worker fetches configured fiat-per-BTC values and writes them to the shared
`currency_rate` database table. One or more web workers read that table while
rendering the wallet. Web requests never contact the external rate provider.

```text
rate provider
    |
    | periodic HTTPS fetch
    v
singleton worker --> currency_rate table <-- web worker(s)
                                             |
                                             v
                                  informational estimate
```

## Failure and freshness behavior

- A failed fetch does not block worker payment processing.
- A failed fetch does not replace previously valid rows.
- A missing cached rate causes Safebox to show sats only.
- Values older than the configured freshness threshold remain visible but are
  explicitly identified as potentially stale.
- A response value must be numeric, finite, and greater than zero before it is
  cached.
- Only configured currencies are considered.

The displayed calculation is:

```text
estimated fiat = spendable sats * fiat per BTC / 100,000,000
```

The estimate uses the wallet's mint-confirmed spendable balance when available.

## Public rates page

`GET /rates` presents the cached fiat-per-BTC values without requiring an
Acorn session. The page reads only the shared database: it does not load a
wallet, inspect a balance, set a session cookie, or cause an outbound provider
request. It identifies cached rows older than the configured stale threshold.

Public means unauthenticated, not plaintext. The application-wide transport
boundary still requires HTTPS outside direct loopback development.

## Configuration

```dotenv
SAFEBOX_CURRENCY_RATES_ENABLED=true
SAFEBOX_CURRENCY_RATE_SOURCE_URL=https://blockchain.info/ticker
SAFEBOX_CURRENCY_RATE_INTERVAL_SECONDS=3600
SAFEBOX_CURRENCY_RATE_CURRENCIES=CAD,USD,EUR,GBP,JPY,INR
SAFEBOX_DEFAULT_DISPLAY_CURRENCY=CAD
SAFEBOX_CURRENCY_RATE_STALE_SECONDS=86400
```

The source currently follows Safebox 2's ticker response convention, where
each currency has a `15m` field containing its fiat value for one BTC. Changing
to a provider with another schema requires an adapter rather than only changing
the URL.

The refresh interval defaults to one hour. The stale threshold defaults to one
day. Compose enables the feature, while the Python settings default to disabled
unless the operator explicitly enables the external dependency.

## Privacy and trust

The provider observes periodic requests from the Safebox operator, not requests
from individual users. It does not receive Acorn keys, balances, handles, relay
addresses, or payment activity.

Safebox stores the public provider URL without its query string or fragment, so
an operator-supplied API token in a URL is not copied into the rate table.

The provider can supply an inaccurate market price. Safebox therefore labels
the value as an estimate and must not use it for financial settlement. TLS and
input validation protect transport and structure, but they do not establish
the economic correctness of the quoted rate.

## User preference boundary

The first implementation uses the operator-configured default display currency.
A future user-selected currency may be stored in the encrypted session cookie.
That preference should not be added to the public handle directory or treated
as provider-owned wallet state.
