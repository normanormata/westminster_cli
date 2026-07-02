from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class Proof:
    letter: str
    references: Tuple[str, ...]


@dataclass(frozen=True)
class Entry:
    doc_id: str
    doc_title: str
    ref: str
    kind: str
    question: Optional[str] = None
    answer: Optional[str] = None
    heading: Optional[str] = None
    text: Optional[str] = None
    proofs: Tuple[Proof, ...] = ()
    question_mesv: Optional[str] = None
    answer_mesv: Optional[str] = None
    heading_mesv: Optional[str] = None
    text_mesv: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.doc_id.upper()} {self.ref}"

    @property
    def searchable_text(self) -> str:
        parts = [self.doc_id, self.doc_title, self.ref, self.kind]
        parts.extend([self.question or "", self.answer or "", self.heading or "", self.text or ""])
        return " ".join(parts).casefold()


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    short_title: str
    source: str
    source_url: str
    entries: Tuple[Entry, ...]


def load_documents() -> Tuple[Document, ...]:
    with resources.files("westminster_cli.data").joinpath("standards.json").open(
        encoding="utf-8"
    ) as data_file:
        raw = json.load(data_file)

    documents: list[Document] = []
    for doc in raw["documents"]:
        entries = tuple(
            Entry(
                doc_id=doc["id"],
                doc_title=doc["title"],
                ref=str(item["ref"]),
                kind=item["kind"],
                question=item.get("question"),
                answer=item.get("answer"),
                heading=item.get("heading"),
                text=item.get("text"),
                proofs=tuple(
                    Proof(letter=proof["letter"], references=tuple(proof["references"]))
                    for proof in item.get("proofs", [])
                ),
                question_mesv=item.get("question_mesv"),
                answer_mesv=item.get("answer_mesv"),
                heading_mesv=item.get("heading_mesv"),
                text_mesv=item.get("text_mesv"),
            )
            for item in doc["entries"]
        )
        documents.append(
            Document(
                id=doc["id"],
                title=doc["title"],
                short_title=doc["short_title"],
                source=doc["source"],
                source_url=doc["source_url"],
                entries=entries,
            )
        )
    return tuple(documents)


def find_document(documents: Iterable[Document], doc_id: str) -> Optional[Document]:
    wanted = doc_id.casefold()
    for document in documents:
        if document.id.casefold() == wanted:
            return document
    return None


def find_entry(document: Document, ref: str) -> Optional[Entry]:
    wanted = ref.casefold()
    for entry in document.entries:
        if entry.ref.casefold() == wanted:
            return entry
    return None


def find_chapter_entries(document: Document, chapter: str) -> list[Entry]:
    prefix = f"{chapter}.".casefold()
    return [entry for entry in document.entries if entry.ref.casefold().startswith(prefix)]


def search_entries(documents: Iterable[Document], query: str) -> list[Entry]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []

    matches: list[Entry] = []
    for document in documents:
        for entry in document.entries:
            haystack = entry.searchable_text
            if all(term in haystack for term in terms):
                matches.append(entry)
    return matches
