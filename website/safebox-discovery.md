# Safebox wallet discovery

The additive `GET /.well-known/safebox.json?name=alice` endpoint publishes a
version-1 wallet directory entry for a claimed handle. It identifies the Acorn
key, public wallet relay hints and enabled cash/Clear receipt capabilities.
The response contains no private keys, balances or recovery data. There is no
recipient login requirement. Publishing the wallet directory itself needs no
database migration; the optional social-npub preference has its own nullable-column migration.

NIP-05 at `/.well-known/nostr.json` keeps its existing default mapping and Clear
extensions when no social npub is chosen. A social override publishes only the social key. Cash and Clear payment readers now prefer `safebox.json`. Wallet messaging and sender attribution also use this directory. Social
NIP-05 lookups remain unchanged. Preferences now accepts an optional social npub, separately from the Acorn binding.

## Which lookup serves which purpose?

**NIP-05 identifies a social identity. Safebox discovery identifies the Acorn for
wallet operations. Record transfer descriptors carry their own transfer information.**

| Purpose | Resolution | Meaning |
| --- | --- | --- |
| Social identity | `/.well-known/nostr.json?name=handle` | Chosen social key; defaults to the Acorn public key when no social preference is set |
| Direct cash payment | `/.well-known/safebox.json?name=handle` with `acorn-ecash-transfer` | Acorn destination key and wallet relay hints |
| Clear payment | The same wallet directory with `clear-token-transfer` and a compatible unit | Acorn destination, subject to the existing Clear routing restrictions |
| Ordinary Lightning payment | `/.well-known/lnurlp/handle` and its invoice callback | Lightning invoice; no social-key resolution is needed |
| Wallet message addressed by name | Wallet directory with `safebox-wallet-message` | Acorn key and message relays; sender-address claims are checked against the wallet directory too |
| Current record sharing or presentation | Acorn-generated transfer descriptor, carried in a QR code or link | Transfer-specific retrieval information; no recipient-address directory lookup |

Safebox Web implements wallet-message delivery and attribution. OakGate does not
yet have a messaging interface and does not advertise that capability. Explicit
npub/public-key message destinations retain their meaning. Local wallet recipients
resolve from the instance's Acorn registration, preserving private community relay
routes without consulting social NIP-05.

### Cash routing and fallback

The migrated application readers look for the explicit `acorn-ecash-transfer`
capability in `safebox.json`. They **do not use the older LNURL `safebox=true`
flag** to select this direct-transfer path. The older Acorn transport associated
with that flag is a separate legacy flow, not this version-1 directory contract.

If the directory advertises a usable direct route and local routing policy permits
it, cash is delivered as ecash through Acorn. When no usable direct route is
advertised, an ordinary cash payment may use LNURL and Lightning instead. An
ordinary external Lightning address does not need Safebox or NIP-05 support.
Clear payments, continuity-only transfers and wallet messages do not fall back
to Lightning.

A 404 or missing cash capability may permit ordinary Lightning for cash.
NIP-05 is never a wallet fallback, including for previously configured legacy
domains. Clear and wallet messages require their advertised wallet capability. Malformed data, unsupported versions, timeouts and
non-404 HTTP errors stop preparation rather than silently switching to Lightning
or a social key. Once a send has begun, an uncertain outcome never triggers an
automatic second payment by another route.

### Record sharing is not address discovery

Reading one's own records uses the connected Acorn. Current sharing and
presentation create an Acorn transfer descriptor and display its QR code or link;
the recipient imports or presents that descriptor through the corresponding Acorn
methods. These flows do not look up a social address or call `safebox.json` to
choose a recipient. OpenETR checks and record-signature verification are separate
from wallet address resolution.

Future record delivery addressed to `handle@domain` should resolve the wallet
through `safebox.json`, with an explicitly defined supported delivery capability.
It must not infer record-delivery support from a cash capability or use the social
npub as the Acorn destination. This is a design rule, not an implemented
address-based record delivery feature.

Set `SAFEBOX_NIP05_EXTERNAL_RELAYS` to the publicly reachable wallet relays for
dual publication. The new endpoint intentionally returns 404
when none are configured; it never substitutes the internal home relay. Clear
advertising follows `SAFEBOX_CLEAR_RECEIVE_ENABLED` and `SAFEBOX_CLEAR_UNITS`.
The directory introduces no mint allowlist. Wallet messaging advertises `safebox-wallet-message` (`nip17`, kind 14).

Responses use public CORS, no-store and nosniff headers. Missing, duplicate or
invalid name parameters return 400; absent/unadvertised handles return 404;
invalid configuration or unavailable directory dependencies return 503. Errors
contain a stable code rather than private implementation details.

First publish the Acorn commit containing `acorn.safebox_discovery`, then run
`poetry update safebox-acorn`, review/commit the lockfile and test the installed
revision before deploying. The payment reader requires this published Acorn API; deploy the committed
lockfile rather than an older Acorn installation.
Do not claim discovery readiness until a real handle resolves successfully.

Verify both endpoint responses for the same handle. Keep NIP-05 mapping to the
existing Acorn key until all supported legacy clients have migrated.
The older `safebox.json/<name>` transport is not reintroduced.

## Payment compatibility policy

Legacy NIP-05 wallet fallback has been retired. `SAFEBOX_LEGACY_DOMAINS` is
ignored, including values left in existing deployments. A 404 or absent cash
capability can use ordinary Lightning for cash; Clear, continuity transfers and
wallet messages require wallet discovery. Malformed directories, unknown versions,
network failures, redirects and other HTTP errors stop preparation.

Lightning fallback obtains and validates an LNURL invoice and calls Acorn's
`pay_multi_invoice` only. It does not call `pay_multi`, follow `safebox=true`, or
enter the older `safebox.json/<name>` transport. Invoice requests omit unsupported
comments and the legacy `safebox` query parameter. Public HTTPS requests validate
DNS, pin the connection, reject redirects and bound response size; the invoice
must match the requested amount, Bitcoin network and expiry requirements.

Local recipients use the instance's wallet registration directly, preserving
private relay support. Clear scope restrictions and Acorn's mint acceptance
policy are unchanged. External wallet discovery uses Acorn's bounded resolver.

See the [family contract and migration plan](https://github.com/trbouma/oakgate/blob/main/docs/018-safebox-address-discovery.md).

## Wallet messages

Wallet-address delivery and claimed-sender verification use the messaging
capability, independently of social NIP-05, with no legacy fallback.
Missing capability, a 404, malformed discovery or network
failure never becomes a social DM. Explicit npub/public-key destinations retain
their meaning. Local messages use the registered wallet and home relay.

See [private messaging](https://github.com/trbouma/safebox-web/blob/main/docs/PRIVATE-MESSAGING.md)
for verification limits, compatibility and deployment details. OakGate does not
yet have a messaging interface and continues to omit this capability.

## Social npub preference

Claim a handle, then set its public social npub in Preferences. No proof-of-control
step is required; Nostr clients compare the signed profile's `nip05` value with
this directory. Clearing the field restores the Acorn-based default. Public social
preferences persist with the handle; browser display settings remain in cookies.
Wallet discovery and messaging never substitute the social key. See the
[handle directory guide](https://github.com/trbouma/safebox-web/blob/main/docs/NIP-05-HANDLE-DIRECTORY.md#optional-social-identity)
for the database migration, backups and legacy-client limitations.
