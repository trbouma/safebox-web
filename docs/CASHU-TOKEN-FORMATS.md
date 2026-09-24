# Cashu token format compatibility

Safebox Web accepts `cashuA` (V3) and `cashuB` (V4) in its Clear token paste
and QR-scanning flows, including the optional `cashu:` URI prefix. Acorn
handles decoding, receipt storage, acceptance, Cash/Clear relay transfers,
and payment-request exports for both formats.

Outgoing tokens prefer cashuB. Acorn falls back to cashuA when proofs cannot
be represented in V4, such as legacy non-hexadecimal keyset identifiers.
The Clear token creation form also offers cashuA for older recipient wallets.
There is no automatic way to detect a recipient wallet's supported format
from a standalone bearer token.

This fallback is local serialization of the same proofs, not a second mint
operation. A mint error must never trigger reissuance under another format.
Mint APIs exchange proofs rather than the cashuA/cashuB token wrapper.

## Deployment

Clear requests advertise public NIP-17 inbox relays, not internal home-relay
hostnames. The home relay remains a local receive route. If no signed inbox
list is available, a public home-relay URL can be advertised; an internal-only
wallet must configure a public inbox before creating requests. Generic
discovery relays are not assumed to be wallet inboxes.

The receiver retains the advertised routes for the request's monitoring and
targeted receipt discovery. The sender checks connectivity before exporting
credits and requires relay acknowledgement on delivery. Connectivity does
not guarantee later delivery, so failed sends must not be retried blindly.
Previously generated requests retain their old routes: create a new request
after deploying this update.

These changes require the accompanying `safebox-acorn` token codec and
receipt-path changes. Publish Acorn first, then run `poetry update safebox-acorn`
in Safebox Web, review and commit the updated lockfile, and rebuild the web
image. The current lockfile is not updated to an unpublished local commit.

Local verification can use the sibling Acorn checkout without changing the
installed dependency:

```sh
PYTHONPATH=.:../safebox-acorn .venv/bin/pytest -q -p no:cacheprovider --import-mode=importlib
```
