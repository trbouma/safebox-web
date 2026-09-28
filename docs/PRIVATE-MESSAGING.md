# Private messaging

The wallet's Private Messages link opens `/messages`. Authenticated GET reads
recent NIP-17 messages from the Acorn's home and discovered signed inbox relays.
An explicit refresh checks again; there is no background listener or automatic
reply. POST validates CSRF, sends using recipient inbox discovery, and redirects
after relay acknowledgement. That acknowledgement is not a read receipt.

Web sends an encrypted JSON message envelope:

```json
{"type":"safebox.dm","version":1,"sender":"alice@example.com","message":"Hello!"}
```

The sender address comes from the authenticated user's registered handle and
the same public host as the wallet address. Without a handle, `sender` is null.
Acorn still transports kind-14 content unchanged; Web parses this envelope for
display. Plain-text messages (including older `From:` prefixes), unrelated JSON,
and unsupported envelope versions remain visible as literal text.

Web resolves the claimed NIP-05 address and compares its key against Acorn's
authenticated event author. The UI labels it verified, mismatch, or unverified;
the actual public key remains visible and messages are never hidden merely
because address verification fails. Verification describes the current address
association, not necessarily its historical ownership at send time.

Lookups use HTTPS, public IPs only, DNS-pinned connections with hostname TLS
verification, no redirects or environment proxies, a 64-KiB response limit, and
a three-second deadline. At most ten distinct claims are checked per page.
The bounded process-local cache lasts five minutes for successful resolutions
and thirty seconds for failures. Private/local-only address hosts remain
unverified. External address servers can observe that a lookup occurred.

The receiving Acorn validates the gift-wrap and seal signatures, checks that the
rumour author matches the seal signer, and requires the recipient tag. Invalid
envelopes are skipped individually. Duplicate rumour IDs are collapsed.
Acorn distinguishes NUT-18 payments from chat after decryption: JSON objects
containing `mint`, `unit`, and `proofs` are omitted from the chat inbox, for both
Bitcoin ecash and Clear units. This also excludes malformed payment-shaped
objects; validation and acceptance remain the responsibility of the existing
transfer flow. Ordinary text and unrelated JSON are still displayed as escaped
text. No event kinds or advertised transports change. Viewing the inbox cannot
redeem tokens, make payments, or execute instructions. Bare Cashu tokens pasted
into a chat message are not NUT-18 payloads and remain text messages.

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
