"""A PDF whose every string sits at a point the caller chose.

The statement fixtures are described as rows and drawn at real coordinates, so
the text and the geometry a parser reads are what a real generator's file
would give. One text object per page holding positioned runs, as such a
generator emits, and a WinAnsi font so that a pound sign's byte is a pound
sign.
"""

from __future__ import annotations

Placement = tuple[float, float, str]


def escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_placed_pdf(pages: list[list[Placement]], *, font_size: float) -> bytes:
    count = len(pages)
    font = 3 + 2 * count
    kids = " ".join(f"{3 + 2 * number} 0 R" for number in range(count))
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        f"<</Type/Pages/Kids[{kids}]/Count {count}>>".encode(),
    ]
    for number, placements in enumerate(pages):
        drawn = (
            f"BT /F1 {font_size:g} Tf\n"
            + "\n".join(
                f"1 0 0 1 {x:.2f} {y:.2f} Tm ({escaped(text)}) Tj"
                for x, y, text in placements
            )
            + "\nET"
        ).encode("latin-1")
        objects.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents {4 + 2 * number} 0 R"
            f"/Resources<</Font<</F1 {font} 0 R>>>>>>".encode()
        )
        objects.append(b"<</Length %d>>stream\n%s\nendstream" % (len(drawn), drawn))
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)
