# Local Acorn Vault Design Note

## Status

**Proposed for evaluation. Not implemented.**

This note evaluates whether Safebox Web should offer optional local storage for
the recovery material shown when an Acorn is created or first connected. The
primary purpose is immediate, convenient safekeeping in the browser. Reconnecting
an Acorn after its session cookie expires is an important secondary benefit. It
is a decision document, not an implementation specification or release
commitment.

## Recommendation

A local vault is feasible and directly addresses a risky first-run behavior.
Users want to finish setup quickly, so they often screenshot the mnemonics or
paste them into an unprotected document or text file. Safebox should offer a
better one-step default: save the material locally, courtesy of the browser,
before the user leaves the creation flow.

If pursued, this should be a **passkey-protected Local Acorn vault** using
WebAuthn PRF, Web Crypto, and IndexedDB. Enrollment should feel like one local
device-confirmation action, not like creating and remembering another password.

Do not implement a vault encrypted directly by a short PIN. Once browser
storage is copied, an attacker can test PINs offline without application rate
limits. If the interface says that a device PIN can unlock the vault, the PIN
must be enforced inside the platform authenticator; Safebox must never receive
the PIN or use it directly as key material.

The feature should remain optional. The local copy provides immediate
safekeeping and may remain useful for routine reconnection, but it is not the
only durable backup. Safebox should continue encouraging an offline copy after
the immediate setup pressure has passed.

## Problem

When Safebox creates a new Acorn, it must show the Safebox Acorn mnemonic and
protected record mnemonic while they are available. This creates a vulnerable
human moment: the user understands that the material is important, but usually
wants to complete setup immediately. The fastest available actions—screenshot,
copy, clipboard, notes application, or plaintext file—are often the least safe.

The design should improve that moment before optimizing later recovery. A
single **Save locally on this device** action would ask the browser and platform
authenticator to protect the complete safekeeping bundle. The user could then
make a more deliberate offline backup later without leaving the phrases in an
obviously exposed temporary location.

Safebox Web also carries attachment material in a protected, authenticated,
HTTP-only session cookie. When that cookie expires or is cleared, the same
local vault can provide a controlled reconnection path:

```text
session expires
      ↓
user unlocks a local vault with a passkey
      ↓
browser briefly reconstructs the attachment bundle
      ↓
existing login boundary issues a fresh HTTP-only cookie
```

## Goals

- Give a newly created Acorn an immediate safer alternative to screenshots,
  clipboard history, and plaintext files.
- Make first-run local safekeeping a single, fast, comprehensible action.
- Store both the Safebox Acorn mnemonic and protected record mnemonic together
  when both are generated.
- Reconnect a previously attached Acorn without re-entering recovery material.
- Keep the application server stateless with respect to the attached Acorn.
- Persist only authenticated ciphertext in the browser.
- Require explicit local user verification before unlocking.
- Preserve the existing server-side login, validation, CSRF, and cookie model.
- Fail closed when authentication, decryption, or validation fails.
- Provide an obvious **Forget this Acorn on this device** operation.
- Fall back cleanly to manual attachment on unsupported devices.

## First-run product principle

The vault is a **convenience mechanism first**. It should meet the user where
they are during setup rather than requiring perfect backup discipline before
they can continue.

The interface should distinguish three states:

1. **Not saved:** recovery material exists only in the current page/session.
2. **Saved locally on this device:** the passkey-protected vault was written and
   verified, but device/browser loss remains a recovery risk.
3. **Offline backup confirmed:** the user reports that a separate durable copy
   has also been made.

Local saving should be available before offline-backup confirmation. Requiring
the offline copy first would recreate the very screenshot-and-paste pressure
the vault is intended to reduce. Safebox should still explain that clearing
browser data or losing the device can destroy the local copy.

## Non-goals

- Replacing offline mnemonic backup.
- Recovering from a lost device, lost passkey, or cleared browser profile.
- Storing proof state, records, transaction history, or wallet caches.
- Synchronizing recovery material through the Safebox server.
- Moving Acorn, relay, mint, funds, or record logic into JavaScript.
- Protecting an unlocked session from a compromised operating system, browser,
  extension, or same-origin script.

## Proposed stored data

The plaintext bootstrap bundle should contain only what the existing login
boundary needs:

```json
{
  "version": 1,
  "acorn_secret_type": "mnemonic-or-nsec",
  "acorn_secret": "<secret>",
  "bootstrap_relay": "wss://relay.example",
  "record_protection_secret": "<optional mnemonic or key>",
  "component_npub": "npub1..."
}
```

For an Acorn created from a mnemonic, the bundle may retain that mnemonic. For
an externally generated `nsec`, it must retain the `nsec`; Safebox must not
invent a mnemonic that cannot reproduce the imported key.

The IndexedDB envelope would contain only ciphertext and non-secret metadata:

```json
{
  "format": "safebox-local-acorn-v1",
  "component_npub": "npub1...",
  "credential_id": "<WebAuthn credential id>",
  "prf_input": "<random 32 bytes>",
  "kdf_salt": "<random 32 bytes>",
  "iv": "<random 12 bytes>",
  "ciphertext": "<authenticated ciphertext>"
}
```

The displayed `npub` is only a recognition hint. After decryption, Safebox must
recalculate the public key from the secret and verify that it matches.

## Cryptographic design

1. Create or select a WebAuthn credential with user verification required.
2. Evaluate the WebAuthn PRF extension with a random vault-specific input.
3. Feed the 32-byte PRF result into HKDF-SHA-256 with a random salt and the
   context `safebox-local-acorn-v1`.
4. Derive a 256-bit AES-GCM key.
5. Encrypt canonical UTF-8 JSON using a fresh 96-bit nonce.
6. Authenticate stable envelope fields as AES-GCM additional authenticated
   data.
7. Release plaintext and working-key references after use as far as the browser
   runtime permits.

The PRF extension is defined by
[WebAuthn Level 3](https://www.w3.org/TR/webauthn-3/#prf-extension). Browser
cryptography should use the standard
[Web Crypto API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API);
Safebox must not implement cryptographic primitives itself.

The passkey private key and PRF secret remain with the authenticator. Face ID,
fingerprint, device PIN, or security-key touch authorizes the authenticator; the
verification factor is not sent to Safebox.

## Enrollment flow

Enrollment occurs only with explicit consent. For a newly created Acorn, it
should be offered directly on the safekeeping page before the user dismisses the
mnemonics:

1. Present **Save locally on this device** as the fastest recommended action.
2. Explain briefly that it is safer than a screenshot or plaintext file, but
   does not replace a separate backup.
3. Feature-detect WebAuthn PRF support.
4. Create the passkey and require local device verification.
5. Encrypt the complete bundle, including both generated mnemonics and the
   bootstrap relay.
6. Store only the envelope in IndexedDB.
7. Perform a decrypt-and-validate readback before reporting success.
8. Show **Saved locally on this device** and continue to offer the existing
   copy/display controls for a later offline backup.

For an existing Acorn connection, offer the same local-save action only after
the supplied secret, relay, and optional protected record material have been
validated successfully.

Enrollment must never happen automatically merely because a user logs in.

## Unlock flow

When no valid session exists and a local envelope is present:

1. Offer **Unlock local Acorn** alongside manual attachment.
2. Display a public-key fingerprint for recognition.
3. Request the stored credential and PRF evaluation.
4. Require platform user verification.
5. Derive the key and authenticate/decrypt the envelope.
6. Strictly validate its schema, secret, relay, and calculated public key.
7. Submit it to the existing login route or a narrowly scoped one-time unlock
   endpoint.
8. Let the server issue the ordinary HTTP-only session cookie.
9. Remove plaintext values from the DOM and release references.

The server must still validate the Acorn secret and relay. A successful passkey
operation is not a substitute for Acorn validation.

## Locking, deletion, and rotation

The least surprising behavior is:

- **Disconnect** ends the session but retains an explicitly enrolled vault.
- **Forget this Acorn on this device** deletes the IndexedDB envelope and local
  credential association where supported.

Changing the Acorn secret, bootstrap relay, or protected record key requires a
verified vault rewrite. A failed update must leave the last valid envelope
intact.

Version one should support one local Acorn per origin. Multiple vaults add
credential selection, naming, deletion, and misidentification risks.

## Security benefits

- Copying IndexedDB does not reveal plaintext recovery material.
- A low-entropy device PIN is not used as the encryption key.
- The authenticator mediates PRF use and can require local verification.
- Safebox does not acquire a persistent server-side recovery database.
- The resulting application session remains HTTP-only.

## Residual risks

### Same-origin script compromise

Code executing under the Safebox origin can read IndexedDB and request an
authenticator operation. User verification may prevent silent unlock, but a
user could approve a malicious prompt. CSP, dependency control, template
escaping, and XSS prevention become even more important.

### Device compromise

The vault cannot protect plaintext during legitimate use from a compromised
browser, operating system, privileged extension, accessibility service, or
screen capture.

### Storage loss

Site-data deletion, private browsing, profile reset, or storage eviction can
destroy the envelope. Offline recovery material remains mandatory.

### Passkey portability

Passkeys may be synchronized or device-bound depending on the authenticator.
PRF support and cross-device behavior must be tested rather than assumed.

### Origin and operator trust

The credential is scoped to a relying-party domain. Domain migration can make
it unavailable. A malicious domain, reverse-proxy, or application operator able
to serve modified JavaScript could attempt to capture material during unlock.
The vault does not remove the execution-environment trust boundary.

## Why a PIN-only vault is unsuitable

A four-digit PIN has 10,000 possibilities and a six-digit PIN has 1,000,000.
An attacker with a copied envelope can try candidates offline. Browser code
cannot reliably impose retry limits on an attacker using separate software.

A strong passphrase with Argon2id could be a fallback, but it adds another
recovery secret and a reviewed WASM dependency because Web Crypto does not
provide Argon2id. PBKDF2 is available but should not be presented as making a
short PIN safe.

## Alternatives

### Keep the current design

Smallest attack surface, no sensitive browser JavaScript, and full consistency
with the hypermedia model. The cost is repeated secret entry after expiration.

### Recommend a password manager

Mature password managers already provide local verification, synchronization,
and recovery. This remains the recommended near-term convenience option even
if a Local Acorn vault is later implemented.

### Store an encrypted bundle on the server

This improves cross-device availability but violates the stateless boundary,
creates durable sensitive server data, and introduces account, recovery,
operator, and offline-attack concerns. It is not recommended.

### Store a non-extractable Web Crypto key in IndexedDB

This is simpler, but possession of the browser profile may be sufficient to use
the key and there is no comparable user-verification ceremony. It is not an
adequate replacement for passkey-mediated unlocking.

### Use WebAuthn `largeBlob`

Authenticator storage support, capacity, and synchronization vary. It does not
remove the need for an encryption and recovery design and should not be the
version-one storage mechanism.

## Hypermedia boundary

The vault would be a deliberate exception to
[Hypermedia Web Architecture](./HYPERMEDIA-ARCHITECTURE.md). It can remain
narrowly bounded:

- JavaScript performs only enrollment, unlock, and deletion;
- no client-side Acorn instance is created;
- no wallet, proof, funds, relay, mint, or record state is cached;
- decrypted material crosses the existing server login boundary; and
- manual attachment continues to work without JavaScript.

The vault should be a small, independently reviewable, same-origin module with
no third-party runtime dependencies where practical.

## Compatibility gate

Before implementation, build a synthetic-data prototype covering:

- WebAuthn registration and authentication;
- PRF availability during both operations;
- stable PRF results after browser restart;
- platform, synchronized, and roaming authenticators;
- Chrome, Safari, Firefox, Android, iOS, macOS, and Windows targets;
- IndexedDB persistence, eviction, and deletion; and
- the production RP-ID and reverse-proxy domain arrangement.

Unsupported combinations must fall back cleanly to manual attachment. The
prototype must never contain a real `nsec` or mnemonic.

## Proposed stages

1. **Compatibility prototype:** synthetic data and browser matrix only.
2. **Envelope tests:** deterministic derivation, tampering, wrong credential,
   schema version, update, and deletion tests.
3. **Feature-flagged integration:** one optional vault, creation-page local
   safekeeping, existing-Acorn enrollment, unlock, and forget controls.
4. **Pilot:** measure successful unlocks, fallbacks, user comprehension,
   accidental deletion, and support burden.

## Go/no-go criteria

Proceed only if:

- PRF works reliably on the intended browser and device matrix;
- the production RP-ID can remain stable;
- manual attachment remains obvious and fully functional;
- a security review accepts the new JavaScript boundary;
- tampering, unsupported PRF, wrong credentials, and storage loss fail closed;
- the interface clearly distinguishes convenience from durable recovery;
- users can inspect and delete the vault; and
- deployment controls prohibit unreviewed third-party scripts.

Do not proceed if implementation requires a short PIN-derived key, silent
enrollment, server-side recovery storage, or claims that the vault replaces
offline backup.

## Open questions

1. Which browser and device combinations must be supported for the pilot?
2. Should both platform and roaming hardware passkeys be supported initially?
3. Should synchronized and device-bound passkeys be distinguished in the UI?
4. What stable production RP-ID will own the credential?
5. Should Disconnect retain the enrolled vault by default?
6. Is one local Acorn per origin sufficient for the pilot?
7. What support path applies when browser data exists but its passkey is gone?
8. How prominently should Safebox continue prompting for an offline backup
   after local safekeeping succeeds?

## Decision

No implementation decision has been made. The observed first-run behavior is
enough to justify the next decision step: a synthetic WebAuthn PRF compatibility
prototype. Results from that prototype should determine whether to implement a
feature-flagged, creation-first pilot or retain the current session-cookie and
offline-recovery model.
