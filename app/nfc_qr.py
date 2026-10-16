"""Text transport for the exact encrypted NFC bytes; no URLs or new authority."""
import base64
import re

import qrcode
import qrcode.image.svg

PREFIX = "safebox-card:v1:"
MAX_BYTES = 400


def encode(payload: bytes) -> str:
    if not 1 <= len(payload) <= MAX_BYTES:
        raise ValueError("Invalid login QR size")
    return PREFIX + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode(value: str) -> bytes:
    if not value.startswith(PREFIX) or len(value) > len(PREFIX) + 534:
        raise ValueError("Invalid login QR format")
    body = value[len(PREFIX):]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", body):
        raise ValueError("Invalid login QR encoding")
    try:
        payload = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
        if encode(payload) != value:
            raise ValueError()
        return payload
    except ValueError:
        raise ValueError("Invalid login QR encoding") from None


def svg(value: str) -> str:
    # Only locally generated geometry is rendered as trusted markup.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(value)
    qr.make(fit=True)
    result = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).to_string(encoding="unicode")
    result = result.replace('<svg ', '<svg class="qr-code" ', 1)
    return result.replace('><path', '><rect width="100%" height="100%" fill="#ffffff" />'
                          '<path shape-rendering="crispEdges"', 1)
