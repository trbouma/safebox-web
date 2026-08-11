# FIDES Use Case Submission Draft: Safebox Web and Lockbox

This draft is structured for manual entry into the FIDES Community use case
submission form.

## Use Case Overview

### Use case title

Safebox Web: Local-first wallets, records, and evidence continuity

### Description

Safebox Web is a local-first wallet app for individuals and communities. It
helps people hold funds, manage private records, receive value, and present
portable evidence while keeping control portable across apps and
infrastructure. A user can create or connect a wallet, store encrypted private
records, attach protected original documents, claim a handle, receive or
transfer value, and later move the same wallet state to another compatible
environment. Underneath the app, Acorn provides portable key, record, recovery,
and ecash state. OpenETR can link exact artifacts to signed origin and control
history, extending verifiable credentials into a broader records-first model.
Safebox Web helps users operate these workflows without becoming the permanent
system of record.

Character count: 777 / 1,200

### Sector

Suggested sector: Digital trust / community continuity / public sector

If the FIDES form requires one predefined sector, choose the closest available
option. Likely candidates are:

- Public sector
- Financial services
- Education
- Health and social services
- Digital trust infrastructure
- Community services

### Production deployment

No

Suggested note if there is a comments field:

Developer-stage implementation with live local integration tests across
Safebox Web, Acorn, Spurline, and Grove. Suitable for demonstrations and pilot
planning, not yet production use.

### Involved organizations

- AOS

### Submitted by organization

AOS

### Contact email

trbouma@gmail.com

## How It Works

### Form-ready text

1. A person opens Safebox Web and creates a new wallet or connects one they
already control with recovery material.
2. Safebox Web starts an authenticated browser session and uses Acorn only for
the current request.
3. Acorn loads encrypted wallet and record state from the selected relay.
4. The user stores a private record or uploads an original document. Acorn
encrypts the content and stores metadata on a relay; large encrypted blobs can
be stored through Grove.
5. When public evidence is needed, OpenETR can bind the exact artifact to
signed origin and control-history events.
6. The user can share or present a record by QR code. The recipient can review
the record, protected original, and related evidence before deciding what to
recognize or import.
7. Payments can be received or transferred as ecash proofs. In a disconnected
community, proofs may support local in-kind clearing until global connectivity
returns.
8. In a Lockbox deployment, Safebox Web, Acorn, Spurline, Grove, and OpenETR
preserve local authority, continuity, and evidence while still synchronizing
with external infrastructure when available.

Character count: 1,126 / 1,200

## Tags

identity, wallet, verifiable credentials, transferable records, local-first,
continuity, records, payments, OpenETR, Nostr, ecash, community resilience

## More Info URL

Suggested:

https://github.com/trbouma/safebox-web

Additional related URLs:

- https://github.com/trbouma/safebox-acorn
- https://github.com/trbouma/grove
- https://github.com/trbouma/spurline
- https://github.com/trbouma/openetr

## Media

### Cover images

Optional. Suggested options:

- Safebox Web logo or site screenshot
- Lockbox family architecture diagram
- Safebox Web wallet or record workflow screenshot

### Demo videos

Optional. Suggested demo flow:

1. Create a new Acorn in Safebox Web.
2. Store a protected record.
3. Attach an original document through Grove.
4. Link the original document to OpenETR evidence.
5. Present the record by QR code.
6. Show the local stack with Spurline and Grove running locally.

## Publication Confirmation

I confirm this information may be published.

## Longer Background Notes

Safebox Web is the human-facing application in a family of local-first
components:

- Acorn provides the protocol runtime for keys, records, recovery, and value.
- Spurline provides a local Nostr relay for evidence and wallet continuity.
- Grove provides Blossom-compatible storage for opaque encrypted blobs.
- OpenETR provides signed evidence for exact artifacts and transferable-record
  control history.
- Lockbox is the future appliance profile that can run these components
  locally for an individual, organization, or community.

The central continuity claim is:

> Lockbox preserves local authority, continuity, and evidence.

This use case is especially relevant for communities that may remain locally
connected while temporarily disconnected from the wider Internet. A remote
community, for example, could continue to access locally preserved records,
recognize locally held evidence, and exchange transferable payment proofs until
global network connectivity returns.
