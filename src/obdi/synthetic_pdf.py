"""Writing a PDF statement, for generated corpora and for fixtures.

Lived in the test tree until 2026-08-13, where it had grown six callers. The
synthetic world generator needs it too, and a module under `src` cannot import
from `tests` - so it moved here rather than being written a second time. Two
implementations of a PDF writer would drift, and the comment below is exactly
the kind of hard-won detail that would not survive being copied.

EVERY FIGURE PASSED TO THIS IS INVENTED. The repository holds code only, and
that claim has to survive a module that can produce statements: nothing here
reads a real one, and the generator feeds it a world built from a seed.
"""

from __future__ import annotations


def build_pdf(lines: list[str]) -> bytes:
    """A valid single-page PDF containing `lines`.

    Offsets are computed rather than guessed, because a PDF without a correct
    xref is not a PDF.

    Objects are delimited by newlines. Without them `endstream` runs straight
    into `endobj`, a strict reader lexes the pair as one token, never closes
    the stream, and reports a page with no content at all. That is a malformed
    file rather than a quirk to work around - and a lenient reader accepting it
    is what let this fixture pass for valid. A fixture only one reader accepts
    tests that reader's tolerance.

    One text run per line at a fixed x, which is what makes this usable for
    line-oriented statement formats and NOT for column-positional ones: a
    parser that decides debit from credit by which column a number sits in
    needs words placed at distinct x offsets, and would read everything here as
    one column. That is a real constraint on which issuer a generated statement
    can imitate, not an oversight.
    """
    drawn = "\n".join(
        f"BT /F1 12 Tf 50 {700 - index * 20} Td ({line}) Tj ET"
        for index, line in enumerate(lines)
    )
    stream = drawn.encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)
