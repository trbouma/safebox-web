# PKPASS Preview Feature

Status: Implemented  
Scope: Safebox Web Original Record rendering

## Summary

Safebox Web can render Apple Wallet `.pkpass` Original Records as a
server-rendered preview instead of treating them only as opaque downloads.

The feature is driven by Acorn's effective MIME metadata. A `.pkpass` file is a
ZIP package at the byte level, but its user-facing artifact type is:

```text
application/vnd.apple.pkpass
```

When Acorn returns that effective MIME, Safebox Web can select a Wallet-pass
representation while preserving the exact original bytes for download and
installation.

## User Experience

On a record detail page, a PKPASS Original Record may show:

- the pass organization, description, and serial number;
- logo, icon, strip, or thumbnail artwork embedded in the pass package;
- primary, secondary, auxiliary, header, and back fields from `pass.json`;
- a generated barcode symbol when the pass declares one; and
- an `Open/Add Wallet Pass` link that downloads the original `.pkpass` bytes.

This gives the user a useful visual check before opening the pass in Apple
Wallet, Google Wallet, or another compatible wallet application.

## Barcode Rendering

Wallet passes do not have to store barcode images. They commonly store barcode
instructions in `pass.json`:

```json
{
  "format": "PKBarcodeFormatAztec",
  "message": "...machine readable payload...",
  "messageEncoding": "ISO-8859-1",
  "altText": "Conf. ABC123"
}
```

Apple Wallet renders the barcode from this metadata at display time. Safebox Web
does the same for preview purposes.

Supported preview renderers:

| PKPASS format | Safebox Web behavior |
| --- | --- |
| `PKBarcodeFormatQR` | Render a server-generated QR SVG. |
| `PKBarcodeFormatAztec` | Render a server-generated Aztec SVG. |
| Other formats | Show the human-readable barcode metadata without a symbol. |

Boarding passes often use Aztec rather than QR. The visual difference matters:
QR codes have finder squares in the corners, while Aztec codes have a central
bullseye. A boarding-pass scanner expecting Aztec should receive an Aztec
symbol, not a QR code containing the same payload.

Safebox Web displays `altText` when available and keeps the full barcode message
available only to the renderer. This avoids filling the page with long machine
payloads while preserving a human-readable confirmation value.

## Hypermedia Boundary

The PKPASS preview remains a server-rendered hypermedia representation:

- the browser requests `/record`;
- Safebox Web reconstructs the request-scoped Acorn;
- Acorn retrieves and decrypts the Original Record;
- Safebox Web interprets the effective MIME and renders a complete HTML page;
- the pass-open action remains an ordinary authenticated link to `/record/blob`.

No browser-side PKPASS parser, ZIP reader, or barcode generator is required.
JavaScript is not required for the PKPASS preview. The server emits complete
HTML, image data URLs for package artwork, and SVG for generated barcode
symbols.

## Storage Boundary

Safebox Web does not rewrite, normalize, or re-sign the PKPASS. It reads the
package only to build the preview representation. The download route returns the
original bytes retrieved through Acorn.

The storage responsibilities remain split:

| Layer | Responsibility |
| --- | --- |
| Blossom | Store opaque encrypted bytes. |
| Acorn | Encrypt, retrieve, decrypt, verify, and expose effective MIME metadata. |
| Safebox Web | Choose the HTML representation and download headers from effective MIME. |

Blossom is intentionally unopinionated. It does not need to know whether the
encrypted blob is a PDF, JPEG, ZIP, PKPASS, or any future artifact type.

## Control and Verification

PKPASS preview creates a useful bridge to OpenETR-style control and
verification, but the preview itself is not the verification anchor.

The anchor is the digest of the exact Original Record bytes retrieved through
Acorn. For a Wallet pass:

```text
sha256(original_pkpass_bytes)
```

Safebox Web may render pass fields, artwork, and barcode symbols so a user can
understand what they are looking at. OpenETR or another control layer can bind
origin, transfer, presentation, revocation, or verifier-policy evidence to the
unchanged package digest.

That keeps the concerns separate:

| Concern | Example |
| --- | --- |
| Representation | Render the Wallet pass preview. |
| Integrity | Hash the exact PKPASS bytes. |
| Control | Attach signed origin or control events to the digest. |
| Verification | Apply policy to the digest, events, and recognized actors. |
| Wallet install | Serve the original package to a wallet application. |

Parsed pass fields such as passenger name, event title, barcode text, and
serial number are display and policy inputs. They must not replace the full
Original Record digest when identifying the artifact under verification.

## Security Notes

PKPASS preview is an allowlisted renderer. Safebox Web should not inline
arbitrary MIME types just because a record declares them.

The preview reader is bounded:

- archives above the configured size limit are not previewed;
- oversized members are skipped or rejected;
- path traversal entries reject the preview;
- only expected image types are embedded as artwork; and
- the signed PKPASS bytes are not modified.

Signature validation remains outside this feature. Safebox Web can preview a
Wallet pass, but the wallet application remains responsible for install-time
trust and signature behavior.
