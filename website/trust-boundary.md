---
title: Trust Boundary
description: What Safebox Web does and does not hold.
---

# Trust Boundary

Safebox Web is intentionally a wallet app, not a server-side wallet database
or identity provider. It has to handle sensitive material while serving an
connected session, but it should not become the permanent authority for the
user's funds, records, or recovery path.

## What the web app holds

After connection, Safebox Web keeps a small encrypted browser session containing the
connected Acorn secret and bootstrap relay. The server decrypts that session
only when handling a request that needs an Acorn instance.

The application database is for operational application state, such as handle
directory entries and provider jobs. It is not the user's wallet.

Incoming-funds finalization makes this distinction concrete. The database can
store a public-key-scoped job lease, phase, counts, amounts, timestamps, and a
bounded error. It does not store the recipient nsec, recovery words, Cashu
proofs, incoming bearer token, or private records.

The recipient key is nevertheless present in clear form inside the trusted web
process while Acorn performs a session-authorized operation. Stateless does
not mean the execution environment never handles secrets; it means those
secrets do not become durable application state. Whoever controls the running
code and its immediate proxy boundary must therefore still be trusted during
the session.

## Web-enabled, local-first

Safebox Web can be reached through a browser, but the browser-facing app is not
the authority context for the user's records. Acorn publishes encrypted records
as signed Nostr events. Relays provide availability and routing; they do not
decide what a record means.

The authority that can be checked is attached to the signed event itself: who
published it, which key can decrypt it, and which later signed events refer to
it. That is why the same record can be preserved on a public relay, community
relay, private relay, or local Spurline relay without becoming owned by that
relay.

## What Acorn handles

Acorn handles wallet loading, proof operations, record encryption, relay
publishing, recovery behavior, and payment workflows. Safebox Web delegates
those operations rather than duplicating them.

The provider's singleton service Acorn is a separate authority. It can receive
Lightning settlement and deliver a private transfer to a registered recipient,
but it does not receive the recipient key and cannot finalize the recipient's
wallet. That final step belongs to the authenticated recipient session.

## What stays replaceable

The surrounding infrastructure can change:

- relays can be public, private, or local Spurline relays;
- blob servers can be hosted Grove instances or local Grove instances;
- mints remain external issuers of spendable proofs; and
- the web app itself can be replaced by another compatible Acorn interface.

That replaceability is the point. Safebox Web should be pleasant to use, but
the user's continuity should not depend on one web deployment.
