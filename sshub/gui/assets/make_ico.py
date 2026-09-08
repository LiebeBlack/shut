"""Generate sshub.ico (used by the PyInstaller build and the tray).

Primary renderer is cairosvg (optional — needs native cairo); when it
is unavailable the power-glyph icon is drawn with PIL directly, so the
CI build never depends on native libraries.

Run:      python -m sshub.gui.assets.make_ico
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

HERE = Path(__file__).parent
SVG = HERE / "icon.svg"
ICO = HERE / "sshub.ico"


def svg_to_png_bytes(size: int) -> bytes:
    """Render the icon as a size x size PNG (cairosvg -> PIL fallback)."""
    try:
        import cairosvg

        return cairosvg.svg2png(
            url=str(SVG), output_width=size, output_height=size
        )
    except ImportError:  # pragma: no cover - exercised only on CI without cairo
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Power glyph: ring + vertical bar, emerald on dark roundel.
        margin = max(2, size // 8)
        d.ellipse((margin, margin, size - margin, size - margin),
                  fill=(20, 23, 31, 255))
        ring = max(1, size // 12)
        d.ellipse((margin + ring * 2, margin + ring * 2,
                   size - margin - ring * 2, size - margin - ring * 2),
                  outline=(0, 229, 160, 255), width=ring)
        bar = (size // 2, margin + ring, size // 2, size // 2)
        d.line(bar, fill=(0, 229, 160, 255), width=max(1, size // 10))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def png_to_bmp(png: bytes) -> bytes:
    """Convert PNG bytes to uncompressed BMP payload for ICO embedding."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = img.size
    raw = img.tobytes()  # BGRA conversion below
    bgra = bytearray(w * h * 4)
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i:i + 4]
        bgra[i] = b
        bgra[i + 1] = g
        bgra[i + 2] = r
        bgra[i + 3] = a
    # BITMAPINFOHEADER (40 bytes) + pixel data + AND mask (all zeros = opaque via alpha)
    header = struct.pack(
        "<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0
    )
    mask = b"\x00" * (((w + 31) // 32) * 4 * h)
    return header + bytes(bgra) + mask


def build_ico() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images: list[tuple[int, bytes]] = []
    for s in sizes:
        png = svg_to_png_bytes(s)
        images.append((s, png_to_bmp(png)))

    # ICO container: 6-byte header + 16 bytes per entry
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    body = b""
    for s, bmp in images:
        entries += struct.pack(
            "<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(bmp), offset
        )
        body += bmp
        offset += len(bmp)
    ICO.write_bytes(header + entries + body)
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes)")


if __name__ == "__main__":
    build_ico()
