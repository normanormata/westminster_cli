"""Merge the 2025 Modern English Study Version into the bundled corpus.

Parses the OPC MESV PDFs and attaches the modern English text to each entry
in src/westminster_cli/data/standards.json:

    curl -L https://opc.org/documents/2025_Modern_English_Study_Version_of_The_Confession_of_Faith.pdf -o /tmp/mesv_cf.pdf
    curl -L https://opc.org/documents/2025_Modern_English_Study_Version_of_The_Larger_Catechism.pdf -o /tmp/mesv_lc.pdf
    curl -L https://opc.org/documents/2025_Modern_English_Study_Version_of_The_Shorter_Catechism.pdf -o /tmp/mesv_sc.pdf
    uv run python scripts/add_mesv.py /tmp/mesv_cf.pdf /tmp/mesv_lc.pdf /tmp/mesv_sc.pdf

The PDFs are plain text: a preface, then `CHAPTER <n>` headings with numbered
sections (confession) or `Q. <n>. ... A. ...` blocks (catechisms). Numbering
mirrors the constitutional text exactly, which the merge validates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz

DATA_PATH = Path(__file__).resolve().parent.parent / "src" / "westminster_cli" / "data" / "standards.json"

TITLE_RE = re.compile(r"^2025 Modern English Study Version of The ")
PAGE_NUMBER_RE = re.compile(r"^\d+\s*$")
CHAPTER_RE = re.compile(r"^CHAPTER (\d+)\s*$")
SECTION_RE = re.compile(r"^(\d+)\.\s+(.*)$")
# SC Q106 is printed without the period after the number.
QUESTION_RE = re.compile(r"^Q\.\s*(\d+)\.?\s+(.*)$")
ANSWER_RE = re.compile(r"^A\.\s+(.*)$")


def normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def content_lines(pdf_path: str) -> list[str]:
    pdf = fitz.open(pdf_path)
    lines: list[str] = []
    started = False
    for page in pdf:
        for raw in page.get_text().splitlines():
            line = raw.rstrip()
            if not started:
                if TITLE_RE.match(line.strip()):
                    started = True
                continue
            if PAGE_NUMBER_RE.match(line) or not line.strip():
                continue
            lines.append(line)
    if not started:
        raise SystemExit(f"{pdf_path}: MESV title line not found")
    return lines


def parse_confession(pdf_path: str) -> dict[str, dict[str, str]]:
    """Return {ref: {"text": ..., "heading": ...}} for chapter.section refs."""
    entries: dict[str, dict[str, str]] = {}
    chapter = 0
    expected_section = 1
    heading: str | None = None
    awaiting_heading = False
    current_ref: str | None = None
    parts: list[str] = []

    def flush():
        nonlocal parts
        if current_ref is not None and parts:
            entry = {"text": normalize(" ".join(parts))}
            if heading and current_ref.endswith(".1"):
                entry["heading"] = heading
            entries[current_ref] = entry
        parts = []

    for line in content_lines(pdf_path):
        stripped = line.strip()
        match = CHAPTER_RE.match(stripped)
        if match:
            flush()
            chapter = int(match.group(1))
            expected_section = 1
            current_ref = None
            awaiting_heading = True
            continue
        if awaiting_heading:
            heading = normalize(stripped)
            awaiting_heading = False
            continue
        match = SECTION_RE.match(stripped)
        if match and int(match.group(1)) == expected_section and chapter:
            flush()
            current_ref = f"{chapter}.{expected_section}"
            expected_section += 1
            parts = [match.group(2)]
            continue
        if current_ref is not None:
            parts.append(stripped)
    flush()
    return entries


def parse_catechism(pdf_path: str) -> dict[str, dict[str, str]]:
    """Return {ref: {"question": ..., "answer": ...}} keyed by question number."""
    entries: dict[str, dict[str, str]] = {}
    current_ref: str | None = None
    expected_question = 1
    question_parts: list[str] = []
    answer_parts: list[str] = []
    in_answer = False

    def flush():
        nonlocal question_parts, answer_parts
        if current_ref is not None:
            entries[current_ref] = {
                "question": normalize(" ".join(question_parts)),
                "answer": normalize(" ".join(answer_parts)),
            }
        question_parts = []
        answer_parts = []

    for line in content_lines(pdf_path):
        stripped = line.strip()
        match = QUESTION_RE.match(stripped)
        if match and int(match.group(1)) == expected_question:
            flush()
            current_ref = str(expected_question)
            expected_question += 1
            question_parts = [match.group(2)]
            in_answer = False
            continue
        match = ANSWER_RE.match(stripped)
        if match and current_ref is not None and not in_answer:
            in_answer = True
            answer_parts = [match.group(1)]
            continue
        if current_ref is None:
            continue
        if in_answer:
            answer_parts.append(stripped)
        else:
            question_parts.append(stripped)
    flush()
    return entries


def merge(data: dict, doc_id: str, parsed: dict[str, dict[str, str]]) -> None:
    document = next(doc for doc in data["documents"] if doc["id"] == doc_id)
    known_refs = {entry["ref"] for entry in document["entries"]}
    parsed_refs = set(parsed)

    unknown = sorted(parsed_refs - known_refs)
    missing = sorted(known_refs - parsed_refs)
    if unknown:
        raise SystemExit(f"{doc_id}: MESV refs not in corpus: {unknown}")
    if missing:
        raise SystemExit(f"{doc_id}: corpus refs missing from MESV: {missing}")

    for entry in document["entries"]:
        mesv = parsed[entry["ref"]]
        for key in ("question_mesv", "answer_mesv", "heading_mesv", "text_mesv"):
            entry.pop(key, None)
        if entry["kind"] == "qa":
            if not mesv.get("question") or not mesv.get("answer"):
                raise SystemExit(f"{doc_id} {entry['ref']}: empty MESV question or answer")
            entry["question_mesv"] = mesv["question"]
            entry["answer_mesv"] = mesv["answer"]
        else:
            if not mesv.get("text"):
                raise SystemExit(f"{doc_id} {entry['ref']}: empty MESV text")
            entry["text_mesv"] = mesv["text"]
            if mesv.get("heading"):
                entry["heading_mesv"] = mesv["heading"]

    print(f"{doc_id}: {len(parsed)} entries merged")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wcf_pdf", help="Path to the MESV Confession of Faith PDF")
    parser.add_argument("wlc_pdf", help="Path to the MESV Larger Catechism PDF")
    parser.add_argument("wsc_pdf", help="Path to the MESV Shorter Catechism PDF")
    parser.add_argument("--data", default=str(DATA_PATH), help="Path to standards.json")
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    merge(data, "wcf", parse_confession(args.wcf_pdf))
    merge(data, "wlc", parse_catechism(args.wlc_pdf))
    merge(data, "wsc", parse_catechism(args.wsc_pdf))

    Path(args.data).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
