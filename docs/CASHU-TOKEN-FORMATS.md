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

These changes require the accompanying `safebox-acorn` token codec and
receipt-path changes. Publish Acorn first, then run `poetry update safebox-acorn`
in Safebox Web, review and commit the updated lockfile, and rebuild the web
image. The current lockfile is not updated to an unpublished local commit.

Local verification can use the sibling Acorn checkout without changing the
installed dependency:

```sh
PYTHONPATH=.:../safebox-acorn .venv/bin/pytest -q -p no:cacheprovider --import-mode=importlib
```
