# Safebox wallet discovery

The additive `GET /.well-known/safebox.json?name=alice` endpoint publishes a
version-1 wallet directory entry for a claimed handle. It identifies the Acorn
key, public wallet relay hints and enabled cash/Clear receipt capabilities.
The response contains no private keys, balances or recovery data. There is no
recipient login requirement and no database migration.

NIP-05 at `/.well-known/nostr.json` is unchanged, including its existing Clear
extensions. Cash and Clear payment readers now prefer `safebox.json`. Wallet messaging and sender attribution also use this directory. Social
NIP-05 lookups remain unchanged. Separate social keys are
not enabled by this release.

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

`SAFEBOX_LEGACY_DOMAINS=safebox.dev` is the default comma-separated list of exact,
known older wallet domains. Set it empty to disable legacy lookup. Add another
older instance only if its NIP-05 names still identify Acorn wallet keys. Remove
a domain before its social and wallet keys diverge; wildcards are not supported.

Only a 404 from `safebox.json` permits NIP-05 lookup on a listed domain. Cash
requires a wallet key and public relay hints; Clear also requires its legacy
capability descriptor and matching unit. An unlisted 404 or absent capability
can use ordinary Lightning for cash. Clear and continuity transfers never do.
Malformed directories, unknown versions, network failures, redirects and other
HTTP errors stop preparation without a payment or fallback.

Local recipients use the instance's wallet registration directly, preserving
private relay support. Clear mint scope restrictions and Acorn's mint acceptance
policy are unchanged. External discovery uses Acorn's bounded public HTTPS
resolver. Legacy lookups also validate public DNS, pin the connection, reject
redirects, and limit response size. No social-key lookup substitutes for a
successful wallet-directory response.

See the [family contract and migration plan](https://github.com/trbouma/oakgate/blob/main/docs/018-safebox-address-discovery.md).

## Wallet messages

Wallet-address delivery and claimed-sender verification use the messaging
capability, independently of social NIP-05. The same configured legacy-domain
policy applies only after 404. Missing capability, malformed discovery or network
failure never becomes a social DM. Explicit npub/public-key destinations retain
their meaning. Local messages use the registered wallet and home relay.

See [private messaging](https://github.com/trbouma/safebox-web/blob/main/docs/PRIVATE-MESSAGING.md)
for verification limits, compatibility and deployment details. OakGate does not
yet have a messaging interface and continues to omit this capability.
