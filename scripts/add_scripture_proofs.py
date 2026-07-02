"""Merge OPC scripture proof references into the bundled standards corpus.

Parses the OPC "with Scripture proofs" PDFs and attaches the proof citations
to each entry in src/westminster_cli/data/standards.json:

    curl -L https://opc.org/documents/CFLayout.pdf -o /tmp/CFLayout.pdf
    curl -L https://opc.org/documents/LCLayout.pdf -o /tmp/LCLayout.pdf
    curl -L https://opc.org/documents/SCLayout.pdf -o /tmp/SCLayout.pdf
    uv run python scripts/add_scripture_proofs.py \
        /tmp/CFLayout.pdf /tmp/LCLayout.pdf /tmp/SCLayout.pdf

The PDFs mark proofs with superscript letters in the body text (10pt body,
7pt superscript) and print the proofs as lettered footnotes (8pt) in which
each verse citation is set in bold. Letters cycle a-z (skipping j and v)
continuously through each document, so body letters and footnote letters are
paired positionally and validated against each other.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz

DATA_PATH = Path(__file__).resolve().parent.parent / "src" / "westminster_cli" / "data" / "standards.json"

QUESTION_RE = re.compile(r"^Q\.\s*(\d+)\.")
CHAPTER_RE = re.compile(r"^Chapter (\d+)\s*$")
SECTION_RE = re.compile(r"^(\d+)\.\s")
# Proof letters cycle a-z (skipping j and v); once exhausted within one
# entry the PDFs continue with primed letters (a', b', ...).
MARKER_RE = re.compile(r"^([a-z]'?)$")
FOOTNOTE_LETTER_RE = re.compile(r"^([a-z]'?)\.\s*$")
CITATION_SPLIT_RE = re.compile(r"(?<=\d\.)\s+|(?<=\)\.)\s+")
CITATION_SHAPE_RE = re.compile(r"^[1-3]?\s?[A-Z][A-Za-z.,\s]*\d")
# Running heads, page numbers, and "Q. n" continuation markers interleave
# with the 8pt footnote stream and must not interrupt a wrapped citation.
PAGE_FURNITURE_RE = re.compile(
    r"^(THE (LARGER|SHORTER) CATECHISM|THE CONFESSION OF FAITH|CHAPTER \d+|\d+|Q\.\s*\d+)\s*$"
)


def normalize_letter(text: str) -> str:
    # A marker at a line break can absorb the hyphen of a hyphenated word.
    return re.sub(r"[´ʹ′’]", "'", text.strip()).rstrip("-")


def iter_spans(pdf):
    for page in pdf:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        yield span


def normalize_citation(text: str) -> str:
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(".")


def add_citations(bold_text: str, citations: list[str], dropped: list[str]) -> None:
    """Split a contiguous bold run into citations, appending to `citations`.

    Parenthesized or digit-less fragments are annotations such as
    "(see entire chapter)" that belong to the preceding citation; a
    digit-less fragment with no preceding citation is bold emphasis inside
    a quotation, not a citation.
    """
    for part in CITATION_SPLIT_RE.split(bold_text):
        part = normalize_citation(part)
        if not part:
            continue
        if not part.startswith("(") and any(ch.isdigit() for ch in part):
            citations.append(part)
            continue
        # "(see verses 13-19)" / "See entire chapter" belong to the
        # preceding citation; other digit-less fragments are bold emphasis
        # inside a quotation, not citations.
        if (part.startswith("(") or part.lower().startswith("see ")) and citations:
            citations[-1] = f"{citations[-1]} {part}"
        else:
            dropped.append(part)


def parse_document(pdf_path: str, kind: str) -> list[tuple[str, str, list[str]]]:
    """Return [(entry_ref, letter, citations), ...] in document order.

    kind is "catechism" (refs are question numbers) or "confession"
    (refs are chapter.section).
    """
    pdf = fitz.open(pdf_path)

    body_markers: list[tuple[str, str]] = []  # (entry_ref, letter)
    footnotes: list[tuple[str, list[str]]] = []  # (letter, citations)

    current_ref = None
    chapter = 0
    expected_section = 1
    expected_question = 1
    bold_buffer: list[str] = []
    dropped: list[str] = []

    def flush_bold():
        if not bold_buffer:
            return
        text = " ".join(bold_buffer)
        bold_buffer.clear()
        if not footnotes:
            # Decorative title lettering (e.g. drop-cap "The") precedes any
            # footnote; real citations always contain digits.
            if any(ch.isdigit() for ch in text):
                raise SystemExit(f"{pdf_path}: citation text before any footnote letter")
            dropped.append(text)
            return
        add_citations(text, footnotes[-1][1], dropped)

    for span in iter_spans(pdf):
        text = span["text"]
        size = round(span["size"])
        bold = "Bold" in span["font"]
        superscript = bool(span["flags"] & 1)

        if superscript and size == 7:
            letter = normalize_letter(text)
            if not MARKER_RE.match(letter):
                raise SystemExit(f"{pdf_path}: unexpected superscript {text!r}")
            if current_ref is None:
                raise SystemExit(f"{pdf_path}: proof marker {letter!r} before any entry")
            body_markers.append((current_ref, letter))
            continue

        if size == 8 and not bold and PAGE_FURNITURE_RE.match(text.strip()):
            continue

        if size >= 10:
            stripped = text.strip()
            if kind == "confession":
                if size >= 12 and bold:
                    match = CHAPTER_RE.match(stripped)
                    if match:
                        chapter = int(match.group(1))
                        expected_section = 1
                    continue
                match = SECTION_RE.match(re.sub(r"\s+", " ", text.lstrip()))
                if match and int(match.group(1)) == expected_section and chapter:
                    current_ref = f"{chapter}.{expected_section}"
                    expected_section += 1
            else:
                match = QUESTION_RE.match(re.sub(r"\s+", " ", text.lstrip()))
                if match and int(match.group(1)) == expected_question:
                    current_ref = str(expected_question)
                    expected_question += 1
            continue

        if size == 8:
            if bold:
                bold_buffer.append(text)
                continue
            flush_bold()
            match = FOOTNOTE_LETTER_RE.match(normalize_letter(text))
            if match:
                footnotes.append((match.group(1), []))
    flush_bold()

    if dropped:
        print(f"  note: {pdf_path} skipped bold non-citations: {dropped}", file=sys.stderr)

    if len(body_markers) != len(footnotes):
        raise SystemExit(
            f"{pdf_path}: {len(body_markers)} body markers vs {len(footnotes)} footnotes"
        )

    results = []
    mismatches = 0
    for (ref, body_letter), (foot_letter, citations) in zip(body_markers, footnotes):
        if body_letter != foot_letter:
            # The printed edition has rare label typos (e.g. WLC 129 marks
            # the text "i" but labels the footnote "j"). The two streams
            # stay position-aligned, so warn and keep the body letter —
            # but repeated mismatches mean the streams have desynced.
            mismatches += 1
            print(
                f"  note: {pdf_path} letter mismatch at {ref}: "
                f"text {body_letter!r} vs footnote {foot_letter!r}",
                file=sys.stderr,
            )
            if mismatches > 3:
                raise SystemExit(f"{pdf_path}: too many letter mismatches, streams desynced")
        if not citations:
            raise SystemExit(f"{pdf_path}: footnote {foot_letter!r} for {ref} has no citations")
        for citation in citations:
            if not CITATION_SHAPE_RE.match(citation):
                raise SystemExit(
                    f"{pdf_path}: suspicious citation {citation!r} in footnote "
                    f"{foot_letter!r} for {ref}"
                )
        results.append((ref, body_letter, citations))
    return results


def merge(data: dict, doc_id: str, parsed: list[tuple[str, str, list[str]]]) -> tuple[int, int, int]:
    document = next(doc for doc in data["documents"] if doc["id"] == doc_id)
    proofs_by_ref: dict[str, list[dict]] = {}
    for ref, letter, citations in parsed:
        proofs_by_ref.setdefault(ref, []).append(
            {"letter": letter, "references": citations}
        )

    known_refs = {entry["ref"] for entry in document["entries"]}
    unknown = sorted(set(proofs_by_ref) - known_refs)
    if unknown:
        raise SystemExit(f"{doc_id}: proofs found for unknown refs: {unknown}")

    covered = 0
    for entry in document["entries"]:
        proofs = proofs_by_ref.get(entry["ref"])
        entry.pop("proofs", None)
        if proofs:
            entry["proofs"] = proofs
            covered += 1

    missing = [entry["ref"] for entry in document["entries"] if "proofs" not in entry]
    if missing:
        print(f"  note: {doc_id} entries without proofs: {missing}", file=sys.stderr)

    letters = sum(len(v) for v in proofs_by_ref.values())
    citations = sum(len(p["references"]) for v in proofs_by_ref.values() for p in v)
    print(
        f"{doc_id}: {covered}/{len(document['entries'])} entries with proofs, "
        f"{letters} footnotes, {citations} citations"
    )
    return covered, letters, citations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wcf_pdf", help="Path to CFLayout.pdf")
    parser.add_argument("wlc_pdf", help="Path to LCLayout.pdf")
    parser.add_argument("wsc_pdf", help="Path to SCLayout.pdf")
    parser.add_argument("--data", default=str(DATA_PATH), help="Path to standards.json")
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    merge(data, "wcf", parse_document(args.wcf_pdf, "confession"))
    merge(data, "wlc", parse_document(args.wlc_pdf, "catechism"))
    merge(data, "wsc", parse_document(args.wsc_pdf, "catechism"))

    Path(args.data).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
