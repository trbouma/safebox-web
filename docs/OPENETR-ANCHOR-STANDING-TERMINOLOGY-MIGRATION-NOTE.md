# OpenETR Anchor, Recognition, and Standing Migration Note

## Status

Migration in progress for Safebox Web. Anchor/signer projection changes and
the Record File terminology migration have been implemented; recognition
adapters remain future work.

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
- a valid Anchor Event establishes initial consequential state for a candidate
  control graph;
- the signer is cryptographically identifiable, but is not necessarily a
  recognized issuer or authority;
- one object digest may have multiple candidate Anchor Events;
- a **Digital Artifact** is persistent digital content whose unique content
  identity is established by a cryptographic digest;
- a **Digital Original** is a Digital Artifact for which consequential state can
  be derived from valid end-verifiable events under OpenETR protocol rules;
- recognition determines whether a relying party accepts that state for a
  purpose, and applicable rules determine its effect.

The central migration principle is:

> Content makes an artifact identifiable. Consequential state makes it an
> original. Recognition and applicable rules determine accepted meaning and
> effect.

## Safebox Web architectural directive

Safebox Web is a producer, consumer, verifier, and projector of OpenETR
end-verifiable events. It is not the authoritative owner of consequential
state.

The implementation boundary is:

```text
ARTIFACT
canonical content + protocol-defined digest

EVENTS
portable signed OpenETR evidence

PROJECTION
consequential state derived under identified versioned rules
```

Storage location does not determine artifact identity. If two canonical byte
sequences have the same protocol-defined digest, they represent the same
Digital Artifact. Copying the content does not copy consequential state.

Safebox Web may cache a projection for performance, but the projection must be
reconstructable from the relevant valid events. Every displayed consequential
state should eventually identify the event or event chain from which it was
derived.

## The five questions Safebox must keep separate

Safebox Web should present five distinct layers:

| Layer | Question | What Safebox can currently establish |
| --- | --- | --- |
| Integrity | Do the displayed bytes match the expected digest? | Yes, through the exact file digest and fingerprint. |
| Protocol validity | Are the OpenETR events structurally and cryptographically valid? | Yes, within the implemented event and chain checks. |
| Consequential state | What state follows when OpenETR rules are applied to the valid event set? | Candidate graphs are available; versioned state derivation is not yet implemented. |
| Recognition | Does the relying party recognize the signer, assertion, graph, or authority for this purpose? | Not yet evaluated by a formal recognition adapter. |
| Standing and effect | What status or consequence follows from recognition? | Not yet evaluated by Safebox Web. |

A valid signature proves that a key signed exact event bytes. Valid linked
events can establish consequential state under OpenETR rules. They do not prove
that the signer is an externally authorized issuer, that every assertion is
true, or that a relying party must recognize the graph or give it effect.

## Terminology migration

### OpenETR event and actor terminology

| Current Safebox language | Target language | Reason |
| --- | --- | --- |
| Origin Event | Anchor Event | A valid anchor starts candidate consequential state without establishing universal recognition or effect. |
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

Safebox previously used **Original Record** as the general name for an exact
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

Use **Record File Fingerprint** where the label appears without an already
obvious Record File context. The shorter **Fingerprint** label may remain only
inside a section where the object being fingerprinted cannot be mistaken for
record metadata, a signer key, or a control graph.

More precise terms can be used where the distinction matters:

```text
canonical bytes identified by digest
    -> Digital Artifact

artifact represented in a valid control graph with derived consequential state
    -> Digital Original

Digital Original accepted as operative for a purpose
    -> Recognized Digital Original
```

Safebox should use **Digital Original** only when a Record File participates in
a valid OpenETR graph from which consequential state can be derived. Where a
recognition policy has evaluated that state, Safebox should qualify the result
as a **recognized Digital Original** and identify the applicable purpose or
context.

## Required projection change

### Current behavior

The previous `build_openetr_history()` implementation gathered matching kind
`1415` events, sorted them, selected the earliest event, and followed only the
control chain rooted in that event. The candidate-graph migration removed that
selection behavior and now preserves every valid candidate anchor.

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
            "consequential_state": {
                "status": "not_derived",
                "protocol_version": None,
                "controller": None,
                "lifecycle": None,
                "standing": None,
                "active_guards": [],
                "basis_event_ids": [],
            },
            "recognition": {
                "status": "not_evaluated",
                "basis": None,
            },
            "effect": {
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

Candidate graph construction is implemented. Full consequential-state
derivation remains a separate migration step. Until a versioned derivation
engine has applied OpenETR rules, Safebox should not infer controller,
lifecycle, guards, or Digital Original status merely from the presence of
events.

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

1. Record File Fingerprint and durable verification QR code;
2. concise integrity and protocol-validity status;
3. candidate Anchor Event and signer information;
4. the human-readable anchor statement;
5. related Control Events;
6. consequential-state result and protocol version;
7. recognition status;
8. effect status; and
9. collapsible protocol details.

For the initial implementation, Safebox should say:

> The Record File Fingerprint identifies these exact bytes. The displayed
> events are cryptographically valid. Consequential state has not yet been
> derived, and recognition and effect have not been evaluated.

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

None of these interface actions independently changes consequential state.
Digital Original status comes from the valid OpenETR event graph, not from
viewing, presenting, or sharing the Record File. Recognition and applicable
rules determine accepted standing and effect.

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

The terminology and claims review covered the following Safebox Web materials:

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
Digital Artifact + end-verifiable events + protocol rules = Digital Original
Digital Original + recognition + applicable rules = recognized effect
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
- Add explicit `not_evaluated` recognition and effect results.

### Phase 2: User-interface migration

- Update the Check pane to render candidate graphs.
- Change Origin and Issuer to Anchor and Signer.
- Update protocol-detail labels.
- Add plain-language integrity, consequential-state, recognition, and effect boundaries.

### Phase 3: Record terminology — implemented

- Replace generic Original Record language with Record File.
- Reserve Digital Original for a Record File with derived OpenETR consequential
  state, and qualify recognized standing separately.
- Update upload, view, presentation, sharing, deletion, and advisory text.

### Phase 4: Consequential-state derivation

- Route all consequential-state projection through the single
  `derive_consequential_state(artifact_id, events)` boundary. This seam is
  implemented and deliberately returns `not_derived` until the versioned rules
  below exist.
- Apply versioned OpenETR rules to each candidate graph.
- Derive controller, lifecycle, active guards, and basis event IDs.
- Distinguish `derived`, `incomplete`, `ambiguous`, `invalid`, and
  `not_derived` results.
- Ensure the same relevant event set and rules produce the same state across
  conforming implementations.
- Treat databases, caches, and rendered pages as projections rather than the
  authoritative source of consequential state.

### Phase 5: Documentation and public positioning

- Update the Safebox Web design notes and MkDocs pages.
- Explain Digital Originality as consequential state rather than byte
  uniqueness, while keeping recognition and effect separate.
- Preserve Check, Present, and Share as the graduated-disclosure model.

### Phase 6: Recognition adapters

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
- deterministic consequential-state derivation from the same event set;
- incomplete and conflicting event sets produce explicit non-derived or
  ambiguous results;
- explicit recognition and effect `not_evaluated` output;
- absence of claims that anchoring compels recognition or external effect; and
- updated Record File terminology in upload, view, presentation, share, and
  deletion workflows.

For every consequential feature, the implementation review should also ask:

1. **What is the artifact?** Identify its canonical content cryptographically.
2. **What happened?** Identify the end-verifiable event.
3. **What state follows?** Derive it under the identified OpenETR rules.
4. **Why should a verifier believe it?** Expose evidence that can be checked
   independently of Safebox Web.

If the final answer is only “because the Safebox Web database says so,” the
feature has not implemented Consequential State Architecture.

## Acceptance criteria

The migration is complete when:

1. Safebox displays kind `1415` as an Anchor Event everywhere.
2. Every valid candidate anchor and its related chain can be inspected.
3. Safebox never treats chronology alone as recognition.
4. A kind `0` profile is presented as signer metadata, not proof of issuer
   authority.
5. Integrity, protocol validity, consequential state, recognition, and effect
   are visibly separate.
6. Generic stored attachments are called Record Files rather than Original
   Records.
7. Digital Original is used only when consequential state can be derived;
   recognized standing and effect are displayed as separate contextual results.
8. Existing OpenETR event and durable-link interoperability remains intact.

## Non-goals

This migration does not:

- decide which candidate anchor is legally or institutionally authoritative;
- create a universal recognition registry;
- make Safebox Web a system of record;
- assert that a signed statement is true;
- treat storage, hashing, or an arbitrary anchor as sufficient consequential
  state without validating the OpenETR event graph; or
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
