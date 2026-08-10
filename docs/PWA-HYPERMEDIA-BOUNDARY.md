# Progressive Web App and Hypermedia Boundary

## Purpose

Safebox Web may progressively adopt selected Progressive Web App (PWA)
capabilities without becoming a client-side wallet application. PWA features
can improve installation, device integration, resilience, and convenience;
they do not need to move Acorn state or workflow authority into the browser.

The governing rule is:

> The server defines available actions through links, forms, and complete
> representations. PWA capabilities may enhance how those actions are
> presented, captured, cached, or resumed.

This preserves the project's hypermedia constraint and the practical meaning
of HATEOAS: a browser follows controls supplied by the current server
representation instead of embedding its own independent map of application
states and transitions.

## HATEOAS does not prohibit browser capabilities

HATEOAS does not require a browser to be featureless. A page may use the
camera, NFC, the clipboard, installation metadata, notifications, or a service
worker while remaining hypermedia-driven. The architectural question is not
whether JavaScript exists. It is whether JavaScript becomes authoritative for
application workflow.

A browser enhancement stays within the boundary when it:

- begins with a control present in the server-rendered representation;
- gathers input or improves presentation without deciding the business
  outcome;
- submits through a server-provided URL and HTTP method;
- allows the server to validate, authorize, execute, and represent the result;
  and
- fails safely without creating a conflicting client-side state machine.

The browser must not infer undocumented routes, reproduce Acorn operations,
declare a mutation successful before server confirmation, or treat cached
representations as current authority.

## Appropriate PWA capabilities

### Installation and presentation

A web app manifest, application icons, launch metadata, theme colors, and an
installed home-screen experience are presentation features. They do not alter
the request model and are compatible with the hypermedia architecture.

The installed application should still open a server representation. It must
not assume that an earlier wallet session remains valid merely because the app
shell can launch.

### Camera, QR, and NFC input

Camera, QR, and future NFC support are input adapters. They may acquire a
Lightning invoice, address, record-sharing descriptor, or other value and
place it into an ordinary form supplied by the server.

The browser may perform basic format recognition needed to select an existing
form, but the server remains responsible for authoritative classification,
validation, authorization, and execution. Device input must not directly call
Acorn or initiate a payment, record import, or key operation without the
corresponding server review and confirmation step.

Manual entry should remain available wherever practical so lack of browser
permission or device support does not make the core workflow inaccessible.

### Clipboard support

Copy controls may place a public address, invoice, sharing descriptor, or
explicitly displayed recovery message on the clipboard. Clipboard access is a
presentation convenience, not persistence or application state.

Sensitive copy operations must be deliberate, clearly described, and paired
with appropriate warnings. The browser must never read the encrypted session
cookie or silently copy secret material.

### Notifications

Future push notifications may tell a user that attention is required and may
deep-link to a server-provided resource. Notification payloads must not contain
an `nsec`, record-protection key, recovery phrase, private record, proof, bearer
sharing secret, or other sensitive content.

Opening a notification must retrieve a current representation and re-establish
authorization normally. A notification is not proof that an operation
succeeded and must not serve as an independent workflow instruction.

## Service-worker caching policy

A service worker creates the greatest risk of accidentally crossing the
boundary. Its initial scope should therefore be deliberately narrow.

It may cache versioned, non-sensitive static assets such as:

- stylesheets;
- same-origin JavaScript used for progressive enhancement;
- public icons and application artwork;
- the web app manifest; and
- a minimal generic offline explanation.

It must not cache:

- authenticated HTML representations;
- responses containing wallet balances or transaction history;
- private record labels, contents, attachments, or decrypted blobs;
- deposit or payment invoices and result pages;
- session, CSRF, or encrypted workflow tokens;
- LNURL callback results associated with a payment;
- recovery or protected-record material; or
- authenticated API responses.

Sensitive and authenticated responses must retain explicit `Cache-Control:
no-store` behavior. A service worker must not override that policy.

An offline page must state that current wallet information and mutations are
unavailable. It must not show a stale balance as current, reconstruct an Acorn,
or imply that funds and records are safely synchronized merely because the
application shell is cached.

## Offline mutations and background synchronization

Safebox should not initially queue financial, key-management, record-sharing,
record-deletion, or recovery mutations for automatic replay. Those operations
can depend on proof state, relay state, mint state, CSRF tokens, expiring
invoices, transient sharing authority, and explicit user confirmation.

If background synchronization is considered later, each operation requires a
separate design and idempotency analysis. At minimum it must define:

- the server-issued action that authorizes the retry;
- the exact durable data placed in browser storage;
- expiry and cancellation behavior;
- duplicate-submission and ambiguous-outcome handling;
- how fresh authorization and user intent are established; and
- how the eventual server representation communicates the outcome.

Until those questions are answered, an interrupted mutation remains
interrupted. The user returns to a fresh server representation and chooses the
next available action.

## Local vault boundary

A future passkey-protected local Acorn vault would be a deliberate exception to
the current rule against browser-held recovery material. Its purpose would be
reconnection convenience after the HTTP-only session expires, not a local
system of record or an offline Acorn implementation.

The vault must be reviewed under its own design note. It must not become a
general cache for balances, proofs, records, application representations, or
pending mutations. Data recovered from it would establish a new server-side
session, after which Safebox would load current Acorn state through the normal
request boundary.

See the [Local Acorn Vault Design Note](./LOCAL-ACORN-VAULT-DESIGN-NOTE.md).

## Session and secret boundary

The attached Acorn session remains represented by an encrypted,
authenticated, `HttpOnly` cookie. PWA JavaScript and service workers must not
be given access to its plaintext credentials.

The running Safebox process necessarily decrypts the cookie and temporarily
holds operational key material while servicing an authenticated request. PWA
features do not change that trust boundary and must not create a parallel
browser-readable credential format merely to support installation or offline
launch.

Browser storage must be classified explicitly:

- presentation preferences may be stored locally;
- public, versioned application assets may be cached;
- transient device input should remain in the current representation; and
- secrets or application state require a dedicated threat model and design
  approval before storage.

## Versioning and update behavior

Cached assets must use an explicit version and predictable invalidation rule.
A new deployment must not combine an old enhancement script with incompatible
server representations indefinitely.

The service worker should prefer simple cache replacement over complex
client-side migrations. If an update cannot safely interpret previously stored
non-sensitive data, it should discard that data and retrieve a fresh server
representation.

## Review test

For each proposed PWA feature, ask:

1. Which server-provided link or form begins the interaction?
2. Is the browser gathering input or has it started deciding workflow?
3. Can the server independently validate all consequential input?
4. Does the server remain authoritative for success, failure, and the next
   available actions?
5. What is stored in the browser, for how long, and can it contain secrets or
   stale wallet state?
6. Does the service worker honor `no-store` responses?
7. What happens when the browser is offline or closes halfway through?
8. Does the feature still have a safe HTTP fallback where technically
   practical?
9. Does it preserve the boundary between Safebox Web and the Acorn component?

If a feature cannot answer these questions cleanly, it is not a routine PWA
enhancement. It requires an explicit architecture and security review.

## North-star constraint

PWA capabilities enhance the Safebox interface. Hypermedia continues to
control the application, and Acorn continues to control keys, funds, records,
and protocol state.
