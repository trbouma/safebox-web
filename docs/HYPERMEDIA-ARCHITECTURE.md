# Hypermedia Web Architecture

Safebox Web is a server-rendered hypermedia application. The browser follows
links, submits HTML forms, receives complete HTML representations, and follows
HTTP redirects. It is not a JavaScript application that duplicates Acorn or
wallet workflow logic in the browser.

Browser representations are rendered with Jinja2 templates under
`app/templates/`. `base.html` owns the shared document structure and includes
small reusable partials, while page templates own their own headings, forms,
links, and conditional presentation. Visual rules live in the same-origin
`app/static/styles.css` file. FastAPI route functions supply plain values and
perform all workflow decisions; templates format those values but do not call
Acorn, mutate state, or decide application outcomes.

The same representations are responsive rather than split into separate
desktop and mobile applications. Narrow-screen styles improve spacing, touch
targets, QR sizing, long-value wrapping, and transaction-card layout while the
links, forms, routes, and server-side behavior remain identical.

Dark mode is the default presentation. A light-mode toggle stores only the
presentation choice in a non-sensitive browser cookie. The theme script does
not read or manage Acorn credentials, wallet state, records, payments, or
workflow decisions; without JavaScript, the complete application continues to
work in its default dark theme.

An optional passkey-protected browser vault is being evaluated as a deliberate,
narrow exception for reconnecting an Acorn after session expiry. It is not
implemented. The tradeoffs, alternatives, compatibility gate, and go/no-go
criteria are documented in the
[Local Acorn Vault Design Note](./LOCAL-ACORN-VAULT-DESIGN-NOTE.md).

Future installability, service-worker caching, notifications, offline behavior,
camera and NFC input, and local-vault work must also follow the
[Progressive Web App and Hypermedia Boundary](./PWA-HYPERMEDIA-BOUNDARY.md).
PWA capabilities may enhance how a server-provided action is presented,
captured, cached, or resumed; they do not move workflow authority into the
browser.

## Upstream component boundary

This document is the Safebox Web implementation of the authoritative
[Safebox App Boundary](https://github.com/trbouma/safebox-acorn/blob/main/docs/SAFEBOX-APP-BOUNDARY.md)
maintained with the Acorn component. The two documents have different jobs:

- the Acorn document defines what belongs to the browser, application,
  component, and infrastructure layers; and
- this document states how Safebox Web conforms to that allocation.

The working division is:

```text
browser       -> links, forms, and presentation
Safebox Web   -> HTTP, sessions, CSRF, validation, representations, and services
Acorn         -> keys, funds, records, mints, relays, recovery, and protocol state
infrastructure -> TLS, proxying, execution, persistence, availability, and monitoring
```

Safebox-specific services remain in this repository even when they call Acorn.
Examples include NIP-05 registration, LNURL endpoints, the provider-payment
queue, and the standalone service Acorn worker. Their wallet and protocol
operations must still pass through Acorn's public API rather than being
reimplemented here.

## Browser contract

The browser layer is intentionally limited to:

- `GET` links for navigation and retrieval;
- ordinary HTML forms for state-changing `POST` requests;
- complete server-rendered success and error pages;
- `303 See Other` redirects after successful mutations where the resulting
  resource has a stable URL;
- an encrypted, authenticated, HTTP-only session cookie; and
- optional JavaScript for presentation feedback and narrowly scoped browser
  device input.

All application decisions remain on the server. FastAPI routes validate input,
verify CSRF tokens, reconstruct the request-scoped Acorn, invoke component
operations, interpret results, and render the next representation.

## Resource-scoped loading

Hypermedia navigation also defines the cost boundary. The connected-wallet
landing page reconstructs Acorn from the encrypted browser session and loads
only lightweight, relay-visible Cash and Clear balance snapshots. It makes no
mint proof check, loads no transaction journal, and scans no incoming transfer
events. Friendly Clear names use the same bounded, short-lived mint metadata
cache as the Clear detail page. Its job is orientation, current approximate
balance visibility, and navigation.

Live status work is performed only when the user invokes the corresponding
control:

- `/transactions` initially loads Cash state and its stored journal; **Check
  Balance and Incoming Transfers** then performs mint proof verification and a
  read-only incoming-transfer scan; and
- `/clear` initially loads stored Clear balances, receipts, history, and
  metadata; **Check for Clear Transfers** performs the incoming relay scan.

This keeps the common landing request responsive, avoids querying unrelated
protocol services, and makes potentially slow network work correspond to an
explicit user request. Cash status checks remain read-only. The Clear check
stages discovered receipts, while proof finalization and Clear acceptance
remain separate POST actions.

### Why the loading boundary changed

Early Safebox Web routes commonly depended on a fully loaded Acorn because
`load_data()` was the convenient general initialization path. That assumption
became costly as Acorn added canonical relay readback, safer proof-state
classification, fee-aware mint operations, incoming-transfer journals, Clear
state, and wallet-scoped concurrency controls. Those checks are appropriate for
funds verification and mutation, but ordinary representations inherited their
latency when they requested more state than they used.

The clearest incident involved deferred recovery. The mnemonic was already
inside the encrypted HTTP-only session cookie, yet opening the recovery page
loaded the complete proof set first. A slow relay or occupied wallet path could
therefore hold a web worker until the reverse proxy returned a timeout. Recovery
display now uses the cookie-held mnemonic and locally reconstructed component
identity without loading proofs or contacting a mint. New sessions also carry
the selected home mint so the safekeeping message remains accurate. Relay-backed
cleanup still occurs only after the user confirms that backup is complete.

The application rule is now explicit:

> Each route requests the narrowest Acorn state domain needed to render or
> perform that resource's action.

In practice:

- key and cookie-held recovery representations use a constructed, unloaded
  Acorn;
- the wallet landing page uses balance snapshots;
- record navigation uses the record catalog and exact-record lookups;
- status pages read non-secret job coordination without reconstructing a
  wallet;
- explicit verification may load proofs and contact a mint; and
- funds mutations retain full loading, locking, outcome classification, and
  canonical persistence.

This is failure isolation as well as optimization. A slow mint must not block
record navigation, and a large proof history must not prevent a user from
viewing recovery material already under session control. Safebox Web does not
solve the problem by creating a local wallet journal: snapshots and catalogs
remain encrypted relay-backed read models, while the application database
contains only service and coordination state.

The kernel rationale and incremental API direction are documented in
[Acorn Load Boundaries and Relay-Backed Read Models](https://github.com/trbouma/safebox-acorn/blob/main/docs/LOAD-BOUNDARIES-AND-READ-MODELS.md).

## Template boundary

The template directory is deliberately a presentation layer rather than a
second application layer:

- `base.html` provides the common document shell, assets, theme control, and
  Acorn-to-Safebox relationship visual;
- named page templates make forms and representations independently readable;
- `partials/` contains reusable markup that has no workflow authority;
- Jinja autoescaping applies to route-supplied values by default; and
- only server-generated HTML fragments such as QR SVGs and existing balance
  summaries are explicitly marked safe at narrow template boundaries.

New browser pages should be added as templates instead of assembling large HTML
strings inside `app/main.py`. Small exceptional result and error pages may use
the generic `page.html` representation until their content is substantial
enough to justify a named template.

## Progressive enhancement only

Secondary representations receive their navigation from the shared server-side
template boundary. A full-width **Home** action always targets `/`, which
redirects an attached session to its wallet, and a second full-width action
returns to the meaningful parent representation such as Wallet, Records,
Record, Receive Funds, or Scanner. Pages without a meaningful parent show only Home.
The landing and connected-wallet main representations omit this navigation.
Individual templates do not reproduce top and bottom return links.

Server-rendered view pages use a consistent reading order: page title, primary
navigation, page content, and then any advisory material. Long list and detail
representations repeat the same Home and parent navigation at the bottom, after
the advisories, so a user can leave the page without scrolling back to the top.
The repeated controls remain ordinary links generated by the shared template;
they introduce no browser-side routing or application state.

`app/static/forms.js` does not call `fetch()`, open WebSockets, intercept form
submission, store application state, or calculate wallet outcomes. It only:

1. marks a submitted form as busy;
2. displays a wait message;
3. disables the submit button to reduce accidental duplicate submission; and
4. restores the controls if browser history returns to the page.

The script never calls `preventDefault()`. With JavaScript disabled or the
script unavailable, links and forms continue to work through normal browser
and HTTP behavior. Progress feedback is an enhancement, not a dependency.

The Lightning-payment scanner is a second bounded enhancement. Its same-origin
script controls the camera and copies decoded QR text into an ordinary HTML
form. It does not classify recipients, initiate payments, access the session
cookie, or call Acorn. The browser submits the acquired value to a
CSRF-protected route, where Safebox classifies and validates a Lightning
address, a fixed-amount mainnet BOLT11 invoice, or a NUT-18 Clear payment
request. Addresses enter the existing payment-review form. Invoices receive a
separate review representation backed by a short-lived encrypted state token.
NUT-18 requests receive a server-rendered review of their amount, CMU, mint,
and fee requirement, followed by a second CSRF-protected confirmation. Acorn
revalidates the request against current Clear proof state before spending.
Manual entry remains available when JavaScript, camera access, or QR decoding
is unavailable.

## State and trust boundary

Record sharing follows the same server-directed model. The sender confirms a
share form, Acorn creates an encrypted temporary transfer, and Safebox renders
its compact Base64URL descriptor as a QR code. The existing scanner recognizes
`acorn:record-transfer:` before attempting Lightning parsing and returns an
import-review representation. Import requires a second CSRF-protected form
submission. Acorn stores the received record before requesting deletion of the
temporary transfer blob; JavaScript performs camera acquisition only and does
not implement transfer cryptography or persistence.

The scanner may also acquire an absolute HTTPS website address. It does not
navigate immediately. Safebox returns a review representation showing the
destination host and complete address; an explicit CSRF-protected **Open
Website** form revalidates the destination and then redirects the browser.
Plain HTTP, embedded credentials, malformed URLs, and non-web schemes are
rejected. The QR decoder acquires the string, while the server owns
classification, validation, confirmation, and navigation.

The sender's QR representation also contains a CSRF-protected **Stop Sharing**
form. After explicit confirmation, Safebox delegates deletion to Acorn using
the transfer-scoped authority contained in the descriptor and returns a new
result representation. This cannot revoke a record that was already imported,
and deletion remains subject to the Blossom operator's retention behavior. No
client-side revocation state or deletion API is used.

The active QR representation uses one additional bounded progressive
enhancement: a `beforeunload` listener warns if the sender navigates away while
sharing is still active. Submitting **Stop Sharing** disarms the warning before
the normal form navigation. The listener does not delete anything, retain the
descriptor, or replace the confirmed server operation. Browser lifecycle
events are not reliable enough to guarantee cleanup, particularly on mobile,
so the visible warning, explicit deletion form, recipient cleanup, descriptor
expiry, and storage-operator retention remain separate safeguards.

Record presentation is a separate capability, not a visual variation of
sharing. The presenter confirms a server-side form, Acorn creates an
authenticated presentation-only envelope, and Safebox renders an
`acorn:record-presentation:` QR descriptor. After scanning, the server retrieves
and validates the package and returns a read-only representation containing the
record, its Record File, and available Control History. The response has no
import form or import affordance. Acorn also rejects import if a presentation
descriptor is relabelled as a transfer, because the capability is authenticated
inside the encrypted envelope.

The recipient's **Done** form and the presenter's **Stop Presenting** form both
request best-effort deletion using the presentation-scoped authority. Either
party may complete cleanup first, and the other receives a graceful closed
representation. The presenter page reuses the bounded navigation warning used
for sharing. These controls reduce accidental persistence; they cannot prevent
screenshots or other out-of-band copying by a recipient who was allowed to view
the presentation.

Safebox Web does not put an Acorn object, proof state, private records, or
workflow state into JavaScript, `localStorage`, or `sessionStorage`. The
encrypted session cookie contains the minimum material required to reconstruct
the attached Acorn. Because it is HTTP-only, page scripts cannot read it.

The **Manage Records** representation uses Acorn's encrypted relay-backed
record catalog. A normal page request reads one non-authoritative catalog event
without calling `load_data()` or loading proof state. Pagination and folder
navigation operate on that server-rendered catalog representation. Opening a
record still performs a direct authoritative lookup for that record. Viewing,
editing, sharing, presenting, downloading, saving, importing, and deleting
records all use the records-only Acorn dependency and do not load funds state.

The record's **Check** control is a bounded progressive enhancement. The
server-rendered record supplies both the verification-fragment URL and an
ordinary full-page fallback link. Opening the disclosure asks that URL for a
complete server-rendered HTML fragment and places the result in the pane.
JavaScript does not construct evidence, select relays, interpret control
events, authorize access, or retain record state. This keeps the potentially
slow OpenETR relay query out of the initial record response while preserving a
usable no-JavaScript path.

For a new wallet with no catalog, the ordinary page presents an empty initial
state and explains that this is expected. For an older wallet with uncatalogued
records, it does not automatically scan and decrypt the complete record history
during page rendering. The user explicitly chooses **Refresh Record List** to
build or refresh the catalog from authoritative record events. A successful
first record write asks Acorn to build the initial catalog on a bounded,
best-effort basis; the encrypted record event remains authoritative if that
derived-index update is unavailable.
Safebox Web does not persist a copy in its database, cookie, or browser storage.
Catalog reads have a separate bounded timeout (10 seconds by default) so an unavailable relay does not
turn record navigation into an unbounded wallet load.

The current `v2` cookie envelope uses AES-256-GCM with a fresh random nonce and
a purpose-specific key derived from the server-held application key using
HKDF-SHA256. Its issuance time, purpose, and credentials are authenticated
together. The server temporarily accepts unprefixed legacy Fernet sessions only
for their remaining original lifetime; all newly issued sessions use `v2`.

The server necessarily decrypts the session and holds the operational `nsec`
in process memory while handling an authenticated request. Acorn loads and
mutates encrypted relay-backed state from that server-side request boundary.
Private-record form contents pass through the server in plaintext for the
duration of the request and are then encrypted by Acorn; they are not stored in
the Safebox Web database.

This application boundary is also the designated location for any optional KEM
experiment. Such an experiment is not currently part of Acorn's stable API or
record format and would not remove the need to trust TLS termination, the
reverse proxy, or the running Safebox Web process. KEM-derived material passed
to Acorn must look like an ordinary validated secret or payload. Introducing a
KEM does not, by itself, justify moving cryptographic state or application logic
into browser JavaScript; any browser participation would require a separate,
explicitly reviewed design.

Optional encrypted file attachments follow the same boundary and remain part
of the private-record workflow. The browser submits one ordinary multipart
record form; Safebox validates the request and passes bounded bytes to Acorn;
Acorn encrypts them before the configured Blossom upload. Download is a normal
authenticated link whose response is produced only after Acorn has retrieved,
authenticated, and decrypted the attachment. No browser-side upload API, key
handling, or decryption logic is introduced. The multipart implementation may
use transient system temporary storage and therefore does not claim that
plaintext exists only in RAM, only that Safebox Web retains no application or
database copy of the uploaded bytes.

PKPASS Record File preview is a specialized server-rendered representation
of the same attachment boundary. Safebox Web uses Acorn's effective MIME result
to recognize `application/vnd.apple.pkpass`, reads the Wallet pass package only
to render a bounded HTML preview, and keeps the install/open action as an
ordinary `/record/blob` link. Barcode symbols declared in `pass.json`, including
boarding-pass Aztec codes, are generated server-side for display. The original
PKPASS bytes are not rewritten or re-signed. See
[PKPASS Preview Feature](./PKPASS-PREVIEW-FEATURE.md).

Safe raster images and PDFs use ordinary authenticated GET resources. Images
are rendered with a native `img` element. PDFs are progressively enhanced with
a pinned, locally served PDF.js renderer because Android Chrome does not
reliably provide an embedded native PDF viewer. PDF.js receives the same-origin
authenticated PDF response and renders one page at a time into a canvas; it
does not receive Acorn keys or implement application workflow. Previous/next
controls are local presentation state only.

Every PDF retains normal same-origin open and download links. Those links are
the no-JavaScript and rendering-failure fallback and allow a browser to open its
full-screen viewer or hand the document to another reader. The application
does not use JavaScript for authorization, decryption, record retrieval, or
mutation. Inline rendering remains restricted to an explicit media-type
allowlist, and decrypted responses remain non-cacheable.

Record deletion is also a normal HTML form mutation. The record representation
contains a CSRF-protected confirmation form, and the POST returns a complete
result representation describing relay visibility and Blossom cleanup. No
client-side deletion API or optimistic UI state is involved.

## Mutation pattern

Where the result has a stable resource URL, use POST/Redirect/GET:

```text
browser submits HTML form
        -> POST validates and performs the operation
        -> 303 redirects to the resource
        -> GET renders the current server-authoritative representation
```

Connection, disconnection, handle changes, payment requests, and private-record
saves follow this pattern where practical. Operations with an outcome that must
be shown exactly once may return a complete HTML result page, but they must
still work without JavaScript and must warn against unsafe resubmission when
the outcome is ambiguous.

## Connection, not account login

Safebox Web connects a browser session to an Acorn component; it does not log a
real-world person into a server-side user account. `POST /connect` validates the
supplied Acorn recovery material and creates the encrypted session cookie.
`POST /disconnect` clears that cookie. Neither action creates or deletes a user
account, and the application does not retain an attached user's Acorn state.

The older `/login` and `/logout` paths are compatibility redirects only. New
pages, documentation, and integrations should use **connect** and
**disconnect** so that the interface describes the actual component-session
boundary.

## JSON endpoints

JSON is reserved for protocol and diagnostic interfaces, not for rendering the
interactive application:

- `/.well-known/nostr.json` implements NIP-05 resolution;
- LNURL-pay routes implement the external payment protocol;
- `/health` supports deployment health checks; and
- `/api/session` provides a small authenticated diagnostic representation.

Browser pages do not fetch these endpoints to assemble their UI. If a future
feature requires browser-side API calls, it should be treated as an explicit
architectural change and justified against this document.

## Review checklist

For every browser-facing feature:

- Can it be completed with JavaScript disabled?
- Does navigation use a link and mutation use a form?
- Does the server remain authoritative for validation and workflow decisions?
- Is success represented by HTML or a redirect to HTML?
- Is sensitive or wallet state absent from browser storage and scripts?
- Is any JavaScript limited to optional presentation enhancement?
- Does a failed or timed-out mutation explain whether retrying is safe?
- Does the implementation preserve the upstream Acorn application boundary?
- If it uses a PWA capability, does it preserve the service-worker, offline,
  notification, and browser-storage rules in the PWA boundary document?

If the answer to any of these questions is no, the change requires an explicit
architecture review.
