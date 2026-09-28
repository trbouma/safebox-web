# Private messaging

The wallet's Private Messages link opens `/messages`. Authenticated GET reads
recent NIP-17 messages from the Acorn's home and discovered signed inbox relays.
An explicit refresh checks again; there is no background listener or automatic
reply. POST validates CSRF, sends using recipient inbox discovery, and redirects
after relay acknowledgement. That acknowledgement is not a read receipt.

The receiving Acorn validates the gift-wrap and seal signatures, checks that the
rumour author matches the seal signer, and requires the recipient tag. Invalid
envelopes are skipped individually. Duplicate rumour IDs are collapsed.
Messages are escaped text, including payment payloads: viewing them cannot
redeem tokens, make payments, or execute instructions.

Messages are decrypted in the authenticated server request. The Web server is
therefore trusted with session keys and plaintext, although transport to relays
is encrypted. This feature does not write decrypted messages to the database or
logs; responses are marked no-store. This does not guarantee absence from host
memory, browser memory, or operator-configured infrastructure logs.

## First-version limits

- Bounded recent inbox (50 outer events per relay), not an archive. Other
  encrypted transfer events can occupy that retrieval window.
- No sent history, unread state, read receipts, pagination, attachments, or
  automatic retries. Relay retention determines offline availability.
- An uncertain send must be checked with the recipient before resending.
  Repeated manual submissions can send duplicate messages.
- Recipients are shown by authenticated public key, not an unverified profile.

## Deployment

Deploy the companion Acorn changes (`get_private_messages` and discovery-aware
`secure_dm`) first. After committing/publishing Acorn, update Web's Poetry lock
to that revision and rebuild the image. The current lock cannot reference an
uncommitted local Acorn change. Older components are rejected by the messaging
page rather than silently using the legacy sender/listener.

Local tests against the sibling Acorn checkout:

```sh
PYTHONPATH=.:../safebox-acorn .venv/bin/pytest -q --import-mode=importlib tests/test_messages.py
```
