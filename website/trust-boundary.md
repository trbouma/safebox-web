---
title: Trust Boundary
description: What Safebox Web does and does not hold.
---

# Trust Boundary

Safebox Web is intentionally a wallet app, not a server-side wallet database
or identity provider. It has to handle sensitive material while serving a
connected session, but it should not become the permanent authority for the
user's funds, records, or recovery path.

## Public state is allowed; user-key custody is not

Safebox Web may persist public information without taking custody of a user's
Acorn. “Stateless” describes the user-secret boundary, not an absence of a database.

| Information | Storage and purpose |
| --- | --- |
| Public handle and Acorn npub | Server-side directory registration and wallet routing |
| Optional social npub | Server-side NIP-05 association; separate from the Acorn key |
| Public relay addresses | Server-side routing hints |
| Job status, leases and operational metadata | Server-side coordination; not a durable user signing credential |
| User Acorn nsec and session-held recovery material | Authenticated, encrypted browser session; transient server memory during authorized use |
| Currency and language preferences | Browser-held encrypted session |

The public social npub does not establish wallet ownership. Changing it affects
NIP-05, while `safebox.json`, payments and wallet messages keep the Acorn binding.
Public preferences can remain available while the user is signed out. They do not
let the server reconstruct the private key or act as the user's Acorn.

## Private-key use is session-authorized, not persistent

The browser holds the encrypted session containing the user's Acorn secret and
bootstrap information. The server authenticates and decrypts that session to
construct an Acorn for an authorized operation. User private keys, recovery words
and copies of secret-bearing session cookies must never be persisted server-side:
not in the application database, files, Redis, durable queues, logs, analytics,
traces or backups. Encrypting a server-side copy would still cross this boundary.

Session-authorized background work may retain credentials in process memory
until that bounded operation finishes. Ending a request, closing the browser or
signing out is not a guarantee of immediate memory erasure or cancellation of an
already-started job. Durable job records must not contain credentials that let a
new process reconstruct the user's Acorn without a new authorized session.
Creation and connection likewise handle key material transiently to establish the
session; they do not create a persistent server-side recovery copy.

For example, incoming-funds finalization stores a public-key-scoped job lease,
phase, counts, amounts, timestamps and a bounded error. It does not store the
recipient nsec, recovery words, Cashu proofs, incoming bearer token or private
records. Other provider jobs have their own documented settlement state; this is
not a claim that all application data is public or non-sensitive.

## The running server is still trusted

Safebox Web is non-custodial in this storage model, but it is not a browser-only
signer. The user's key is available in plaintext to the trusted web process while
Acorn performs authorized work. The operator's cookie-encryption key plus a
captured session cookie can also expose the user's key. Users therefore trust
the running application, host and immediate proxy boundary during use.

The no-persistence rule is an application and deployment requirement, not proof
that a malicious operator cannot capture secrets. Proxy logging, request-body
capture, crash dumps and host diagnostics must not turn transient credentials
into retained data. Keep secret-bearing requests and cookies out of those systems.

Operator-owned service keys are a separate category. The application service
identity, cookie-encryption key and provider service Acorn may require persistent
operator storage. They must never be substituted for or confused with a user's
Acorn private key. Public directory backups preserve address associations, not a
user's private-key recovery path.

OakGate deliberately uses a different model: it stores encrypted user recovery
material and can operate the user's Acorn while the user is offline. Safebox Web
must not acquire that capability by persisting user credentials. Both use the
same underlying Acorn architecture.

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
wallet. That final step belongs to an authorized recipient Acorn, through a
connected web session or another compatible client. This is an
[asynchronous receipt capability](asynchronous-receipt.md): the npub identifies
the Acorn and the Lightning address provides a service path to it. Gateway,
mint, and relay trust obligations remain.

## What stays replaceable

The surrounding infrastructure can change:

- relays can be public, private, or local Spurline relays;
- blob servers can be hosted Grove instances or local Grove instances;
- mints remain external issuers of spendable proofs; and
- the web app itself can be replaced by another compatible Acorn interface.

That replaceability is the point. Safebox Web should be pleasant to use, but
the user's continuity should not depend on one web deployment.
