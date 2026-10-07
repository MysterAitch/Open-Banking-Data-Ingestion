"""A PDF is read once; every page afterwards reads what that gave.

Reading a statement's pages is the slow step in obdi: a page's words have no position until its
fonts have been parsed, and a store of kept statements paid that on the first view after every
restart, because the readings were held in memory only. The document cannot change, so what it
yields changes only when the extractor does. This keeps, per document, in `statement_extractions`:

  * its text lines and its table's cells with their positions and pages (the grid of a page is
    the cells' own alignment, so it is derived and not stored a second time);
  * the issuer names found in the text (`statement_names`);
  * for a document of several accounts, each section's key, label, reading, and the arithmetic
    gate's verdict, and for one whose sections cannot be told apart, why;
  * its masked shape, as the shape page shows it, without the file's name.

WHEN IT IS FILLED: when a document is kept (`keep_extraction`), and by the rebuild for any
document with no extraction made by the current `EXTRACTOR_VERSION` (`fill_missing`) - a document
made by another version is read again there and nowhere else. A document that cannot be read has
a row too, saying why, so it is not tried on every pass.

WHEN IT IS READ: by `serving`, which makes every parser read a stored extraction where it would
have read the bytes. A page serves strictly: a document with no current extraction raises
`NotExtracted` rather than being read, and the page says so ("not yet extracted; the next rebuild
extracts it"). The rebuild serves leniently, because it is the door that fills.

The table is derived: it can be dropped and refilled from the raw artefacts alone, which is why a
rebuild leaves it in place (extraction is the cost) and why `EXTRACTOR_VERSION` is the only thing
that makes it read again. It holds a statement's real text, like the raw artefact it came from, so
nothing here reaches a page except through the masking each page already does.
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..core.errors import DataError
from .parsers import pdf_statements
from .parsers.base import ParseError
from .parsers.pdf_statements import (
    NotExtracted,
    PdfStatementParser,
    RawExtraction,
    SectionReading,
    pdf_parser_for,
    read_raw,
    supplying,
)
from .parsers.statement_reading import reading_from_json, reading_to_json
from .statement_columns import Cell, Row
from .statement_names import names_found
from .statement_shape import shape_of_extraction
from .store import ExtractionRecord, Store

#: How many documents a serving block keeps decoded. A parser asks for one document several
#: times in a row (every parser sniffs it, then the one that claims it reads it), and a page
#: that walks the kept statements moves on to the next.
_SERVED_KEPT = 4


@dataclass(frozen=True)
class Extracted:
    """Everything stored for one document, decoded."""

    raw: RawExtraction
    names: list[tuple[str, int]]
    #: The document's accounts, or None for a document that is read whole.
    sections: list[SectionReading] | None
    #: Why a document's sections cannot be told apart, "" where they can or there are none.
    #: As raised, so digits in it are not yet masked.
    sections_error: str
    #: The masked shape as `ShapeReport.describe` says it with no file name before it.
    masked_shape: str


def _encode_cells(table: list[Row]) -> str:
    return json.dumps(
        [
            [row.y, row.page, [[cell.x, cell.text, cell.x_end] for cell in row.cells]]
            for row in table
        ],
        separators=(",", ":"),
    )


def _decode_cells(text: str) -> list[Row]:
    return [
        Row(
            y=float(y),
            page=int(page),
            cells=[Cell(x=float(x), text=str(word), x_end=float(end)) for x, word, end in cells],
        )
        for y, page, cells in json.loads(text)
    ]


def _encode_sections(sections: list[SectionReading] | None) -> str:
    if sections is None:
        return ""
    return json.dumps(
        [
            {
                "key": item.key,
                "label": item.label,
                "reading": reading_to_json(item.reading),
                "refusal": item.refusal,
            }
            for item in sections
        ],
        separators=(",", ":"),
    )


def _decode_sections(text: str) -> list[SectionReading] | None:
    if not text:
        return None
    return [
        SectionReading(
            key=str(item["key"]),
            label=str(item["label"]),
            reading=reading_from_json(str(item["reading"])),
            refusal=str(item["refusal"]),
        )
        for item in json.loads(text)
    ]


def raw_from_record(record: ExtractionRecord) -> RawExtraction:
    """The text and cells of a stored extraction; raises ValueError where the row is damaged."""
    try:
        if record.failure:
            return RawExtraction([], [], record.page_count, record.failure)
        lines = [str(line) for line in json.loads(record.lines)]
        return RawExtraction(lines, _decode_cells(record.cells), record.page_count)
    except (TypeError, KeyError) as exc:
        raise ValueError(f"stored extraction is damaged: {exc}") from exc


def extracted_from_record(record: ExtractionRecord) -> Extracted:
    """Everything a stored row holds; raises ValueError where it is damaged."""
    try:
        names = [(str(name), int(count)) for name, count in json.loads(record.names)]
        return Extracted(
            raw=raw_from_record(record),
            names=names,
            sections=_decode_sections(record.sections),
            sections_error=record.sections_error,
            masked_shape=record.masked_shape,
        )
    except (TypeError, KeyError) as exc:
        raise ValueError(f"stored extraction is damaged: {exc}") from exc


def _record_of(found: Extracted) -> ExtractionRecord:
    return ExtractionRecord(
        failure=found.raw.failure,
        page_count=found.raw.page_count,
        lines=json.dumps(found.raw.lines, separators=(",", ":")),
        cells=_encode_cells(found.raw.table),
        names=json.dumps(found.names, separators=(",", ":")),
        sections=_encode_sections(found.sections),
        sections_error=found.sections_error,
        masked_shape=found.masked_shape,
    )


def _sections_of_document(payload: bytes) -> tuple[list[SectionReading] | None, str]:
    """The sections a document divides into and why not, from the extraction being served."""
    try:
        parser = pdf_parser_for(payload)
    except ParseError:
        # Two readers claim it and disagree: nothing is divided, and the listing says no reader
        # reads it, as it did before extractions were kept.
        return None, ""
    if not isinstance(parser, PdfStatementParser):
        return None, ""
    try:
        return parser.sections(payload), ""
    except (DataError, ValueError) as exc:
        return None, str(exc)
    except Exception as exc:
        print(
            f"{parser.source} could not divide a statement into accounts - {exc}",
            file=sys.stderr,
        )
        return None, f"{type(exc).__name__}: {exc}"


def extract_document(payload: bytes) -> Extracted:
    """Read a PDF once and work out everything a page will want to say about it.

    The parsers that find the sections run over the text and cells just read and not over the
    bytes again, so a document is read from its file exactly once.
    """
    raw = read_raw(payload)
    if raw.failure:
        shape = shape_of_extraction([], [], 0)
        shape.readable = False
        return Extracted(raw, [], None, "", shape.describe())
    with supplying(lambda _digest: raw):
        names = names_found(raw.lines)
        sections, error = _sections_of_document(payload)
    shape = shape_of_extraction(raw.lines, raw.table, raw.page_count)
    return Extracted(raw, names, sections, error, shape.describe())


def keep_extraction(store: Store, digest: str, payload: bytes) -> Extracted:
    """Read a held PDF and keep what it yields, replacing any earlier extraction of it.

    The caller commits. Returns what was kept, so a caller that wants to say something about the
    document need not read it back.
    """
    found = extract_document(payload)
    store.keep_statement_extraction(
        digest, pdf_statements.EXTRACTOR_VERSION, _record_of(found)
    )
    return found


def fill_missing(store: Store) -> int:
    """Extract every held PDF that has no extraction made by the current version; commits.

    Returns how many documents were read. A document that cannot be read is kept as failed,
    so it is not read again until the version moves. A row of the current version that does not
    decode counts as missing (`stored` says so aloud), so a damaged row is repaired here and not
    left to every page to report.
    """
    current = {
        digest
        for digest in store.extracted_digests(pdf_statements.EXTRACTOR_VERSION)
        if stored(store, digest) is not None
    }
    wanted = [
        str(row["digest"])
        for row in store.connection.execute(
            "SELECT DISTINCT digest FROM raw_artefacts WHERE media_type = 'application/pdf'"
        )
        if str(row["digest"]) not in current
    ]
    for digest in wanted:
        row = store.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE digest = ? AND media_type = "
            "'application/pdf' LIMIT 1",
            (digest,),
        ).fetchone()
        keep_extraction(store, digest, bytes(row["payload"]))
        store.connection.commit()
    return len(wanted)


def is_kept(store: Store, digest: str) -> bool:
    """Whether a row made by the current version exists for the document, without decoding it."""
    return store.statement_extraction(digest, pdf_statements.EXTRACTOR_VERSION) is not None


def stored(store: Store, digest: str) -> Extracted | None:
    """What is stored for a document by the current version, or None where nothing is.

    A damaged row is said aloud and counts as nothing: the page says the document is not
    extracted, and `fill_missing` is what repairs it.
    """
    record = store.statement_extraction(digest, pdf_statements.EXTRACTOR_VERSION)
    if record is None:
        return None
    try:
        return extracted_from_record(record)
    except ValueError as exc:
        print(
            f"artefact {digest[:12]}: stored extraction is damaged ({exc}), "
            "treating it as not extracted",
            file=sys.stderr,
        )
        return None


def stored_sections(store: Store, digest: str) -> list[SectionReading] | None:
    """The sections stored for a document: its accounts, an empty list for a document that is
    not divided or whose sections cannot be told apart, and None where nothing is stored at the
    current version (or the row is damaged, said aloud).

    Only the sections' columns are read, because this is asked for every assigned section on
    every page that states a balance.
    """
    kept = store.statement_extraction_sections(digest, pdf_statements.EXTRACTOR_VERSION)
    if kept is None:
        return None
    try:
        return _decode_sections(kept[0]) or []
    except (ValueError, KeyError, TypeError) as exc:
        print(
            f"artefact {digest[:12]}: stored extraction is damaged ({exc}), "
            "treating it as not extracted",
            file=sys.stderr,
        )
        return None


@contextmanager
def serving(store: Store, *, strict: bool) -> Iterator[None]:
    """Inside the block, every parser reads a document's stored extraction, not its bytes.

    With `strict`, a document with no extraction at the current version raises `NotExtracted`
    where a parser would have read it: the door for a page, which never reads a PDF. Without
    it such a document is read as it always was, which is the door for the rebuild after it has
    filled what is missing.
    """
    kept: OrderedDict[str, RawExtraction] = OrderedDict()
    # Documents found to have no row, for this block only: every parser sniffs a document, and
    # asking the store again for each one was a statement per parser per document.
    missing: set[str] = set()

    def supplier(digest: str) -> RawExtraction | None:
        if digest in kept:
            kept.move_to_end(digest)
            return kept[digest]
        if digest in missing:
            return None
        record = store.statement_extraction(digest, pdf_statements.EXTRACTOR_VERSION)
        raw: RawExtraction | None = None
        if record is not None:
            try:
                raw = raw_from_record(record)
            except ValueError as exc:
                print(
                    f"artefact {digest[:12]}: stored extraction is damaged ({exc})",
                    file=sys.stderr,
                )
        if raw is None:
            if strict:
                raise NotExtracted(digest)
            missing.add(digest)
            return None
        kept[digest] = raw
        while len(kept) > _SERVED_KEPT:
            kept.popitem(last=False)
        return raw

    with supplying(supplier):
        yield


@contextmanager
def serving_extractions(
    store: Store, media_type: str, digest: str, payload: bytes
) -> Iterator[None]:
    """For a door that has just landed a file: extract it first if it is a PDF with no
    extraction at the current version (and commit), then serve the parsers from the store.

    A file that is not a PDF is left alone. This is the import door's own read of a document it
    is about to parse anyway, so the read is shared with the parse and with every page after it.
    """
    if media_type != "application/pdf":
        yield
        return
    if not is_kept(store, digest):
        keep_extraction(store, digest, payload)
        store.connection.commit()
    with serving(store, strict=False):
        yield


def not_yet_extracted_words() -> str:
    """What a page says of a document that has no extraction yet. Said here once."""
    return "not yet extracted; the next rebuild extracts it"
