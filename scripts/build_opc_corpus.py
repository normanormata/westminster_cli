from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "westminster_cli" / "data" / "standards.json"


@dataclass
class Block:
    kind: str
    text: str


class MainBlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.main_depth = 0
        self.current_kind: Optional[str] = None
        self.current_parts: list[str] = []
        self.blocks: list[Block] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())
        if tag == "div" and "mainBlock" in classes and not self.in_main:
            self.in_main = True
            self.main_depth = 1
            return

        if not self.in_main:
            return

        if tag == "div":
            self.main_depth += 1
        elif tag in {"p", "h1", "h3", "td"}:
            self._flush()
            self.current_kind = tag
            self.current_parts = []
        elif tag == "br" and self.current_kind:
            self.current_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_main:
            return

        if tag in {"p", "h1", "h3", "td"} and self.current_kind == tag:
            self._flush()
        elif tag == "div":
            self.main_depth -= 1
            if self.main_depth <= 0:
                self._flush()
                self.in_main = False

    def handle_data(self, data: str) -> None:
        if self.in_main and self.current_kind:
            self.current_parts.append(data)

    def _flush(self) -> None:
        if not self.current_kind:
            return
        text = normalize(" ".join("".join(self.current_parts).split()))
        if text:
            self.blocks.append(Block(self.current_kind, text))
        self.current_kind = None
        self.current_parts = []


def normalize(value: str) -> str:
    return (
        value.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00a0", " ")
        .strip()
    )


def parse_blocks(path: Path) -> list[Block]:
    parser = MainBlockParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.blocks


def parse_catechism(path: Path, doc_id: str, title: str, short_title: str, source_url: str) -> dict:
    entries = []
    for block in parse_blocks(path):
        if block.kind != "p":
            continue
        match = re.match(r"^Q\. (\d+)\. (.*?) A\. (.*)$", block.text)
        if not match:
            continue
        ref, question, answer = match.groups()
        entries.append(
            {
                "ref": ref,
                "kind": "qa",
                "question": question.strip(),
                "answer": answer.strip(),
            }
        )

    return {
        "id": doc_id,
        "title": title,
        "short_title": short_title,
        "source": "Orthodox Presbyterian Church constitutional text",
        "source_url": source_url,
        "entries": entries,
    }


def parse_wcf(path: Path) -> dict:
    entries = []
    current_chapter: Optional[str] = None
    current_heading: Optional[str] = None
    current_entry: Optional[dict] = None

    def flush_current() -> None:
        nonlocal current_entry
        if current_entry is not None:
            current_entry["text"] = " ".join(current_entry["text_parts"]).strip()
            del current_entry["text_parts"]
            entries.append(current_entry)
            current_entry = None

    for block in parse_blocks(path):
        if block.kind == "h3":
            flush_current()
            match = re.match(r"^CHAPTER (\d+) (.+)$", block.text)
            if match:
                current_chapter, current_heading = match.groups()
            continue

        if current_chapter is None or block.kind not in {"p", "td"}:
            continue

        numbered = re.match(r"^(\d+)\. (.*)$", block.text)
        if block.kind == "p" and numbered:
            flush_current()
            paragraph, text = numbered.groups()
            current_entry = {
                "ref": f"{int(current_chapter)}.{paragraph}",
                "kind": "section",
                "heading": current_heading,
                "text_parts": [text],
            }
        elif current_entry is not None:
            current_entry["text_parts"].append(block.text)

    flush_current()
    return {
        "id": "wcf",
        "title": "Westminster Confession of Faith",
        "short_title": "Confession of Faith",
        "source": "Orthodox Presbyterian Church constitutional text",
        "source_url": "https://opc.org/wcf.html",
        "entries": entries,
    }


def build(wcf: Path, lc: Path, sc: Path) -> dict:
    return {
        "source": "Orthodox Presbyterian Church Confession and Catechisms",
        "source_url": "https://opc.org/confessions.html",
        "documents": [
            parse_wcf(wcf),
            parse_catechism(
                lc,
                "wlc",
                "Westminster Larger Catechism",
                "Larger Catechism",
                "https://opc.org/lc.html",
            ),
            parse_catechism(
                sc,
                "wsc",
                "Westminster Shorter Catechism",
                "Shorter Catechism",
                "https://opc.org/sc.html",
            ),
        ],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: build_opc_corpus.py WCF_HTML LC_HTML SC_HTML", file=sys.stderr)
        return 2

    corpus = build(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    OUTPUT.write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for document in corpus["documents"]:
        print(f"{document['id']}: {len(document['entries'])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
