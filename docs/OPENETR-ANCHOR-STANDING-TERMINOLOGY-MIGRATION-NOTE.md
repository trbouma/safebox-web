# OpenETR Anchor, Recognition, and Standing Migration Note

## Status

Proposed migration for Safebox Web.

This note records the changes needed to align Safebox Web with the revised
OpenETR distinction between Anchor Events, control, recognition, standing, and
Digital Originals. It is a migration plan rather than a claim that every item
has already been implemented.

## Why this migration is needed

The current Safebox Web OpenETR projection was built around an earlier model:

- kind `1415` was presented as an **Origin Event**;
- the signer of that event was presented as the **Issuer**;
- the earliest matching kind `1415` event was selected as the origin;
- later events were displayed as one Control History rooted in that event; and
- attached files were generally called **Original Records**.

The revised OpenETR model makes narrower and more defensible claims:

- kind `1415` is an **Anchor Event**;
- an Anchor Event establishes an initial signed control state, not original
  standing;
- the signer is cryptographically identifiable, but is not necessarily a
  recognized issuer or authority;
- one object digest may have multiple candidate Anchor Events;
- recognition determines what standing a relying party gives an object or
  graph; and
- a **Digital Original** is a controlled digital object with recognized
  standing as an original.

The central migration principle is:

> Protocol validity is evidence. Recognition and standing determine what that
> evidence means for a particular purpose.

## The four questions Safebox must keep separate

Safebox Web should present four distinct layers:

| Layer | Question | What Safebox can currently establish |
| --- | --- | --- |
| Integrity | Do the displayed bytes match the expected digest? | Yes, through the exact file digest and fingerprint. |
| Protocol validity | Are the OpenETR events structurally and cryptographically valid? | Yes, within the implemented event and chain checks. |
| Recognition | Does the relying party recognize the signer, assertion, graph, or authority for this purpose? | Not yet evaluated by a formal recognition adapter. |
| Standing and effect | What status or consequence follows from recognition? | Not yet evaluated by Safebox Web. |

A valid signature proves that a key signed exact event bytes. It does not prove
that the signer is an authorized issuer, that the assertion is true, or that
the controlled object has standing as an original.

## Terminology migration

### OpenETR event and actor terminology

| Current Safebox language | Target language | Reason |
| --- | --- | --- |
| Origin Event | Anchor Event | Anchoring starts a candidate control graph but does not establish original standing. |
| Origin and Issuer | Anchor and Signer | Signing is demonstrable; issuer status requires recognition or domain policy. |
| Issuer profile | Signer profile | A Nostr kind `0` profile is self-published metadata about the signing key. |
| Issuer Public Key | Anchor Signer Public Key | Avoids inferring authority from event authorship. |
| Issued | Anchored | Describes what the wire event proves without overstating effect. |
| Origin issued | Anchor recorded | Keeps the user-facing action consistent with kind `1415` semantics. |
| Earliest signed origin | Candidate anchor | Time ordering is not a recognition rule. |

The wire action `action=issue` remains valid under the current OpenETR Nostr
binding. Safebox should interpret it as the compatibility action label used by
an Anchor Event, not as proof that the signer has recognized issuer authority.

### Record-file terminology

Safebox currently uses **Original Record** as the general name for an exact
attachment protected by Acorn. Under the revised model, storing, hashing,
signing, or anchoring bytes does not independently make them an original.

The recommended ordinary user-facing term is **Record File**:

| Current language | Target language |
| --- | --- |
| Original Record | Record File |
| Store Original Record | Store Record File |
| Original Record attachment | Record File |
| Delete record and Original Record | Delete record and Record File |
| No Original Record is available | No Record File is available |

The short **Fingerprint** label can remain unchanged.

More precise terms can be used where the distinction matters:

```text
bytes or file before anchoring
    -> Digital Object / exact artifact

object represented in a valid control graph
    -> Controlled Digital Object

controlled object recognized as the operative original for a purpose
    -> Digital Original
```

Safebox should use **Digital Original** only when a recognition policy actually
returns that standing and identifies the applicable purpose or context.

## Required projection change

### Current behavior

The current `build_openetr_history()` implementation gathers matching kind
`1415` events, sorts them, selects the earliest event, and follows only the
control chain rooted in that event. Additional kind `1415` events are reduced
to a warning.

This behavior is no longer sufficient. The earliest event is a chronology
fact, not an authority or recognition decision.

### Target behavior

Safebox should preserve every cryptographically valid candidate Anchor Event
and construct an independent candidate graph for each one:

```python
{
    "digest": "...",
    "candidate_graphs": [
        {
            "anchor": {...},
            "signer_profile": {...},
            "controls": [...],
            "warnings": [...],
            "recognition": {
                "status": "not_evaluated",
                "basis": None,
            },
            "standing": {
                "status": "not_evaluated",
                "value": None,
                "purpose": None,
            },
        }
    ],
    "unlinked_events": [...],
    "invalid_event_count": 0,
}
```

Safebox must not silently choose an authoritative graph. A future recognition
adapter may select or rank a candidate, but its result must include the
recognition context and selection basis.

### Control-event grouping

For each candidate Anchor Event, Safebox should:

1. begin with the Anchor Event ID as the graph root;
2. follow kind `1416` events through exact `e` references;
3. honour a compatible explicit root reference where older events include one;
4. avoid attaching one event to multiple candidate graphs without evidence;
5. report broken or unresolved links; and
6. preserve unlinked but otherwise valid events for protocol inspection.

Multiple candidate anchors and their chains should remain visible. They are not
duplicates merely because they share an object digest.

## Signer-profile handling

The current kind `0` lookup is useful recognition context but should no longer
be named or presented as proof of an issuer.

Recommended internal changes:

- `build_issuer_profile()` to `build_signer_profile()`;
- `issuer_profile` to `signer_profile`; and
- `issuer_profile_error` to `signer_profile_error`.

When several candidate anchors have different authors, Safebox should resolve
the latest valid kind `0` profile for every unique signer. These profiles can be
queried together by author list to avoid one relay round trip per anchor.

The interface should explain:

> Signer information is self-published Nostr profile metadata signed by the
> same key as the Anchor Event. It may help identify or recognize the signer,
> but it does not independently establish the signer's authority.

The word **issuer** remains appropriate when a domain-specific record,
credential scheme, or recognition policy establishes that role. It should not
be inferred solely from authorship of kind `1415`.

## Check-pane migration

The **Check** interaction remains the correct first level in Safebox's
graduated-disclosure model. Its contents should change from one presumed origin
to one or more candidate graphs.

Recommended presentation order:

1. fingerprint and durable verification QR code;
2. concise integrity and protocol-validity status;
3. candidate Anchor Event and signer information;
4. the human-readable anchor statement;
5. related Control Events;
6. recognition status;
7. standing status; and
8. collapsible protocol details.

For the initial implementation, Safebox should say:

> The fingerprint identifies this exact Record File, and the displayed events
> are cryptographically valid. Recognition and standing have not been
> evaluated.

If multiple anchors exist, the interface should say how many candidate graphs
were found and render each separately. It should not say that the earliest one
is authoritative.

Protocol details should use:

- Anchor Event;
- Anchor Event ID;
- Anchor Event Kind;
- Anchor Signer Public Key;
- Prior Event ID; and
- queried relays.

## Check, Present, and Share

The existing graduated-disclosure interaction vocabulary remains aligned with
the revised OpenETR model:

| Action | Meaning after migration |
| --- | --- |
| Check | Inspect digest-bound signed evidence without receiving the private Record File. |
| Present | Temporarily inspect the exact Record File and compare it with candidate graph evidence. |
| Share | Receive and retain the exact Record File for deeper native-format, cryptographic, recognition, or policy review. |

None of these actions independently assigns Digital Original standing. A
recognition context decides whether the controlled object has that standing and
what effect follows.

## Compatibility rules

The migration should preserve wire and bookmark compatibility:

- continue querying regular event kinds `1415` and `1416`;
- continue accepting `action=issue` on kind `1415`;
- continue reading compatible legacy root-reference fields where required;
- retain existing durable OpenETR links and QR-code format;
- keep old internal `origin` fields temporarily only where needed during a
  staged application migration; and
- do not expose the old Origin/Issuer interpretation in new user-facing text.

This migration does not require importing the full OpenETR package into
Safebox Web. The lightweight read-only adapter can continue to implement the
minimum wire projection, provided it follows the current specifications.

## Documentation migration

The following Safebox Web materials require a terminology and claims review:

- `docs/OPENETR-ISSUE-VERIFY-DESIGN-NOTE.md`;
- `website/graduated-disclosure.md`;
- `website/deep-verification.md`;
- `website/records-and-wallet-passes.md`;
- `website/product-architecture.md`;
- the public home-page record language;
- record upload, viewing, presentation, sharing, and deletion templates; and
- tests and screenshots that contain **Original Record**, **Origin Event**, or
  **Issuer** as an inferred role.

Documentation should explicitly include:

```text
Digital object + control + recognition = possible Digital Original
```

It should also explain that recognition is contextual. Different institutions,
communities, contractual frameworks, or legal regimes may recognize different
candidate anchors for different purposes.

## Implementation phases

### Phase 1: Semantic correctness

- Rename kind `1415` presentation to Anchor Event.
- Build and retain every candidate anchor graph.
- Stop selecting the earliest anchor as authoritative.
- Rename issuer projection fields to signer fields.
- Add explicit `not_evaluated` recognition and standing results.

### Phase 2: User-interface migration

- Update the Check pane to render candidate graphs.
- Change Origin and Issuer to Anchor and Signer.
- Update protocol-detail labels.
- Add plain-language integrity, recognition, and standing boundaries.

### Phase 3: Record terminology

- Replace generic Original Record language with Record File.
- Reserve Digital Original for a controlled object with recognized standing.
- Update upload, view, presentation, sharing, deletion, and advisory text.

### Phase 4: Documentation and public positioning

- Update the Safebox Web design notes and MkDocs pages.
- Explain Digital Originality as standing rather than byte uniqueness.
- Preserve Check, Present, and Share as the graduated-disclosure model.

### Phase 5: Recognition adapters

- Define a recognition-result interface.
- Allow domain, community, institutional, contractual, or legal profiles to
  evaluate candidate anchors.
- Require each result to state its policy, purpose, basis, and time of
  evaluation.
- Display recognized standing without presenting it as globally universal.

## Required tests

The migration should include tests for:

- one Anchor Event with no recognition policy;
- two Anchor Events for the same digest;
- independent control chains beneath each anchor;
- no automatic earliest-anchor authority;
- signer-profile resolution for each unique anchor signer;
- unlinked control events that are not attached to the wrong graph;
- invalid events excluded from candidate graphs;
- compatibility with `action=issue`;
- explicit recognition and standing `not_evaluated` output;
- absence of claims that anchoring alone creates a Digital Original; and
- updated Record File terminology in upload, view, presentation, share, and
  deletion workflows.

## Acceptance criteria

The migration is complete when:

1. Safebox displays kind `1415` as an Anchor Event everywhere.
2. Every valid candidate anchor and its related chain can be inspected.
3. Safebox never treats chronology alone as recognition.
4. A kind `0` profile is presented as signer metadata, not proof of issuer
   authority.
5. Integrity, protocol validity, recognition, and standing are visibly
   separate.
6. Generic stored attachments are called Record Files rather than Original
   Records.
7. Digital Original is used only with an explicit recognition context.
8. Existing OpenETR event and durable-link interoperability remains intact.

## Non-goals

This migration does not:

- decide which candidate anchor is legally or institutionally authoritative;
- create a universal recognition registry;
- make Safebox Web a system of record;
- assert that a signed statement is true;
- turn a stored or anchored file into a Digital Original automatically; or
- require the OpenETR implementation library as an application dependency.

## Source material

This migration is based on the current OpenETR specifications and policy
material, particularly:

- `DIGITAL_ORIGINALITY_CONTROL_AND_STANDING_DESIGN_NOTE.md`;
- `OPENETR_NOSTR_WIRE_FORMAT_SPEC.md`;
- `EVENT_KIND_REGISTRY.md`;
- `CONTROL_EVENT_MINIMUM_SHAPES.md`; and
- the Digital Originality and Graduated Disclosure policy briefs.

The OpenETR specifications should remain authoritative if terminology in an
older reference implementation or application screen has not yet migrated.
