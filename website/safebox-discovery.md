# Safebox wallet discovery

The additive `GET /.well-known/safebox.json?name=alice` endpoint publishes a
version-1 wallet directory entry for a claimed handle. It identifies the Acorn
key, public wallet relay hints and enabled cash/Clear receipt capabilities.
The response contains no private keys, balances or recovery data. There is no
recipient login requirement and no database migration.

NIP-05 at `/.well-known/nostr.json` is unchanged, including its existing Clear
extensions. Current payment readers, social lookups and messaging attribution
also remain unchanged. Separate social keys are not enabled by this release.

Set `SAFEBOX_NIP05_EXTERNAL_RELAYS` to the publicly reachable wallet relays for
this initial dual-publication phase. The new endpoint intentionally returns 404
when none are configured; it never substitutes the internal home relay. Clear
advertising follows `SAFEBOX_CLEAR_RECEIVE_ENABLED` and `SAFEBOX_CLEAR_UNITS`.
The directory introduces no mint allowlist. Wallet messaging is not advertised.

Responses use public CORS, no-store and nosniff headers. Missing, duplicate or
invalid name parameters return 400; absent/unadvertised handles return 404;
invalid configuration or unavailable directory dependencies return 503. Errors
contain a stable code rather than private implementation details.

First publish the Acorn commit containing `acorn.safebox_discovery`, then run
`poetry update safebox-acorn`, review/commit the lockfile and test the installed
revision before deploying. An older dependency leaves existing functionality
running but the new endpoint reports 503 `discovery_dependency_unavailable`.
Do not claim discovery readiness until a real handle resolves successfully.

Verify both endpoint responses for the same handle before moving senders. Keep
NIP-05 mapping to the existing Acorn key throughout this release. The next phase
will move wallet payment and message readers to the new resolver, with explicit
legacy-domain compatibility. A generic social NIP-05 key is not proof of wallet
support. The older `safebox.json/<name>` transport is not reintroduced.

See the [family contract and migration plan](https://github.com/trbouma/oakgate/blob/main/docs/018-safebox-address-discovery.md).
