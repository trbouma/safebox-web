# Private messaging

The wallet's Private Messages link opens `/messages`. Authenticated GET reads
recent NIP-17 messages from the Acorn's home and discovered signed inbox relays.
An explicit refresh checks again; there is no background listener or automatic
reply. POST validates CSRF, resolves wallet destinations, sends through Acorn, and redirects
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

## Wallet delivery and attribution

For `name@domain`, Web resolves `/.well-known/safebox.json?name=name` and requires
`safebox-wallet-message` with transport `nip17` and inner kind 14. It passes the
resolved wallet npub and explicit relay list to Acorn's existing `secure_dm`.
Acorn performs encryption, signing and delivery through Stroma; the address is
not resolved a second time through a social API. Explicit npub/public-key inputs
keep their existing meaning and use Acorn's signed inbox discovery.

The same wallet directory verifies a claimed sender address against Acorn's
authenticated event author. The UI labels it verified, mismatch, or unverified;
the actual public key remains visible and failed lookup never hides the message.
Verification describes only the current address association, not historical
ownership at send time. NIP-05 remains a separate social-identity lookup, with a
separate cache that is never used to authenticate wallet-message claims.

Compatibility uses `SAFEBOX_LEGACY_DOMAINS` (default `safebox.dev`). Only a v1
HTTP 404 permits NIP-05 lookup on one of those exact, configured domains. These
must be known older wallet instances whose NIP-05 keys still identify Acorns;
legacy messaging also requires public relay hints. Set the list empty to disable
fallback. Remove domains before social and wallet keys split. A successful v1
response without messaging capability means unsupported; it does not fall back.
Unknown versions, invalid data, timeouts and non-404 HTTP errors stop sending.
Discovery failure says no message was sent; failure after publication starts
retains the uncertain-delivery warning and never automatically retries.

Local destinations and sender claims use the registered wallet binding directly,
including private home relays. A missing local handle never triggers an external
lookup. Public external lookups use HTTPS, public DNS-pinned connections with
hostname TLS verification, no redirects/proxies, and a 64-KiB response limit.
At most ten distinct claims are checked concurrently per inbox page, each with
a five-second total deadline. Wallet associations are not cached. External
address servers can observe that a lookup occurred.

Web advertises this capability alongside cash/Clear in its public wallet
directory. OakGate currently has no messaging interface and does not advertise
it. The public NIP-05 response and version-1 message envelope remain unchanged;
older messages remain readable. No separate social-key registration UI is added.

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

Use the committed Poetry lockfile, which includes Acorn's published directory
parser/resolver, `get_private_messages`, and discovery-aware `secure_dm`. This
migration needs no new Acorn API or user-key operation in Web. Configure
`SAFEBOX_NIP05_EXTERNAL_RELAYS` with the public wallet inbox routes, verify the
public v1 entry, then deploy senders with the appropriate legacy-domain list.
An older v1 publisher without the message capability must upgrade before these
senders can address it as a wallet; no inference from its cash capability is made.
Keep public NIP-05 mappings compatible until uncontrolled legacy clients migrate.

Tests run against the installed locked dependency and fake wallets. Real delivery
between controlled instances remains an operator check; tests do not send DMs.

## Relationship to records and payments

Wallet messages addressed by name resolve the Acorn through `safebox.json`;
social NIP-05 is a different association. Record sharing/presentation currently
uses Acorn transfer descriptors and QR codes, with no recipient-address lookup.
See the [resolution table](../website/safebox-discovery.md#which-lookup-serves-which-purpose)
for cash capabilities, Lightning fallback, Clear and future address-based records.
