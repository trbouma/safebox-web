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
- optional presentation-only JavaScript for submission progress feedback.

All application decisions remain on the server. FastAPI routes validate input,
verify CSRF tokens, reconstruct the request-scoped Acorn, invoke component
operations, interpret results, and render the next representation.

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

`app/static/forms.js` does not call `fetch()`, open WebSockets, intercept form
submission, store application state, or calculate wallet outcomes. It only:

1. marks a submitted form as busy;
2. displays a wait message;
3. disables the submit button to reduce accidental duplicate submission; and
4. restores the controls if browser history returns to the page.

The script never calls `preventDefault()`. With JavaScript disabled or the
script unavailable, links and forms continue to work through normal browser
and HTTP behavior. Progress feedback is an enhancement, not a dependency.

## State and trust boundary

Safebox Web does not put an Acorn object, proof state, private records, or
workflow state into JavaScript, `localStorage`, or `sessionStorage`. The
encrypted session cookie contains the minimum material required to reconstruct
the attached Acorn. Because it is HTTP-only, page scripts cannot read it.

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

Encrypted blob uploads follow the same boundary. The browser submits an
ordinary multipart form; Safebox validates the request and passes bounded bytes
to Acorn; Acorn encrypts them before the configured Blossom upload. Download is
a normal authenticated link whose response is produced only after Acorn has
retrieved, authenticated, and decrypted the blob. No browser-side upload API,
key handling, or decryption logic is introduced. The multipart implementation
may use transient system temporary storage and therefore does not claim that
plaintext exists only in RAM, only that Safebox Web retains no application or
database copy.

Safe raster images and PDFs use ordinary authenticated GET resources rendered
with native HTML elements (`img` and `object`). PDF records also provide a
normal same-origin link to the decrypted `application/pdf` response because
embedded-object support varies across mobile browsers. That link lets a browser
open its full-screen PDF viewer or hand the document to a reader app; the
separate attachment response remains available for download. There is no Fetch
API, object-URL lifecycle, PDF.js dependency, or client-side viewer state.
Inline rendering is restricted to an explicit media-type allowlist.

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

Login, logout, handle changes, deposits, and private-record saves follow this
pattern where practical. Operations with an outcome that must be shown exactly
once may return a complete HTML result page, but they must still work without
JavaScript and must warn against unsafe resubmission when the outcome is
ambiguous.

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

If the answer to any of these questions is no, the change requires an explicit
architecture review.
