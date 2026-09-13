"""Safe local document parsers behind one registry."""

import csv
import hashlib
from html.parser import HTMLParser
import io
from pathlib import Path
import re
from typing import Protocol
import zipfile
import xml.etree.ElementTree as ET

from app.rag.models import KnowledgeSource, ParsedDocument, ParsedSection


PARSER_VERSION = "parsers_v2"


class Parser(Protocol):
    name: str
    version: str

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument: ...


class DocumentParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {
            "markdown": MarkdownDocumentParser(),
            "txt": TextDocumentParser(),
            "pdf": PDFDocumentParser(),
            "docx": DocxDocumentParser(),
            "html": HtmlDocumentParser(),
            "csv": CsvDocumentParser(),
        }

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        parser = self._parsers.get(source.source_type)
        if parser is None:
            return _failed(source, "RAG_UNSUPPORTED_FILE_TYPE", "unknown")
        try:
            return parser.parse(source, path)
        except Exception as exc:
            code = str(exc) if str(exc).startswith("RAG_") else "RAG_PARSE_FAILED"
            return _failed(source, code, getattr(parser, "name", "unknown"))

    def parser_version(self, source_type: str) -> str:
        parser = self._parsers.get(source_type)
        return getattr(parser, "version", PARSER_VERSION)


class MarkdownDocumentParser:
    name = "markdown"
    version = "markdown_v2"

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        text = _read_text(path)
        if not text.strip():
            return _empty(source, self.name, self.version)
        sections = _markdown_sections(text, source.source_id)
        return _parsed(source, _title(text, path.stem), sections, self.name, self.version)


class TextDocumentParser:
    name = "text"
    version = "text_v1"

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        text = _read_text(path)
        if not text.strip():
            return _empty(source, self.name, self.version)
        section_id = _section_id(source.source_id, "body", 0)
        section = ParsedSection(section_id=section_id, heading=path.stem, text=text.strip(), paragraph_start=1, paragraph_end=len(text.splitlines()), section_path=[path.stem])
        return _parsed(source, path.stem, [section], self.name, self.version)


class PDFDocumentParser:
    name = "pypdf"
    version = "pdf_v2"

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return _failed(source, "RAG_PARSER_DEPENDENCY_MISSING", self.name, {"ocr_required": False})
        reader = PdfReader(str(path))
        sections: list[ParsedSection] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                heading = _pdf_page_heading(text, page_number)
                sections.append(
                    ParsedSection(
                        section_id=_section_id(source.source_id, f"page_{page_number}", page_number),
                        heading=heading,
                        heading_level=1,
                        text=text,
                        page_number=page_number,
                        section_path=[heading, f"Page {page_number}"] if heading != f"Page {page_number}" else [heading],
                        metadata={"page_number": page_number, "detected_heading": heading if heading != f"Page {page_number}" else None},
                    )
                )
        if not sections:
            return _failed(source, "RAG_PDF_OCR_REQUIRED", self.name, {"ocr_required": True})
        return _parsed(source, path.stem, sections, self.name, self.version, {"page_count": len(reader.pages)})


class DocxDocumentParser:
    name = "docx_xml"
    version = "docx_v2"

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        # Reading the document XML directly avoids executing VBA, macros,
        # embedded objects, relationships or external resources.
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                return _failed(source, "RAG_DOCX_INVALID", self.name)
            root = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        sections: list[ParsedSection] = []
        current_heading = path.stem
        current_level = 1
        buffer: list[str] = []
        ordinal = 0
        for node in root.findall(".//w:body/*", ns):
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "p":
                text = "".join(item.text or "" for item in node.findall(".//w:t", ns)).strip()
                if not text:
                    continue
                style = node.find("./w:pPr/w:pStyle", ns)
                style_name = style.attrib.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "") if style is not None else ""
                match = re.search(r"heading\s*([1-6])", style_name, re.IGNORECASE)
                if match:
                    if buffer:
                        sections.append(_make_section(source.source_id, current_heading, current_level, buffer, ordinal))
                        ordinal += 1
                        buffer = []
                    current_level = int(match.group(1))
                    current_heading = text
                else:
                    buffer.append(text)
            elif tag == "tbl":
                rows: list[str] = []
                for row in node.findall("./w:tr", ns):
                    cells = [" ".join(item.text or "" for item in cell.findall(".//w:t", ns)).strip() for cell in row.findall("./w:tc", ns)]
                    rows.append(" | ".join(cells))
                if rows:
                    buffer.append("\n".join(rows))
        if buffer:
            sections.append(_make_section(source.source_id, current_heading, current_level, buffer, ordinal))
        if not sections:
            return _empty(source, self.name, self.version)
        # ``current_heading`` is the final heading in the file, not the
        # document title. Using it as the title polluted every chunk's title
        # retrieval signal (for example with an appendix heading).
        return _parsed(source, path.stem, sections, self.name, self.version)


class _SafeHTMLParser(HTMLParser):
    _ignored = {"script", "style", "nav", "noscript", "template", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.lines: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._ignored or any(key in {"hidden", "aria-hidden"} or (key == "style" and value == "display:none") for key, value in attrs):
            self.depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.lines.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored and self.depth:
            self.depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.lines.append("\n")

    def handle_data(self, data: str) -> None:
        if self.depth == 0:
            value = data.strip()
            if value:
                self.lines.append(value)
                if self._in_title:
                    self.title += value


class HtmlDocumentParser:
    name = "html_stdlib"
    version = "html_v1"

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        parser = _SafeHTMLParser()
        parser.feed(_read_text(path))
        text = re.sub(r"\n{3,}", "\n\n", " ".join(parser.lines)).strip()
        if not text:
            return _empty(source, self.name, self.version)
        title = parser.title.strip() or path.stem
        section = ParsedSection(section_id=_section_id(source.source_id, "body", 0), heading=title, text=text, section_path=[title], paragraph_start=1)
        return _parsed(source, title, [section], self.name, self.version)


class CsvDocumentParser:
    name = "csv_stdlib"
    version = "csv_v1"

    def parse(self, source: KnowledgeSource, path: Path) -> ParsedDocument:
        text = _read_text(path)
        rows = list(csv.reader(io.StringIO(text)))
        if not rows or not any(cell.strip() for cell in rows[0]):
            return _empty(source, self.name, self.version)
        headers = [cell.strip()[:200] for cell in rows[0][:100]]
        records: list[str] = []
        for row_id, row in enumerate(rows[1:10001], start=1):
            cells = [cell.strip()[:2000] for cell in row[:100]]
            records.append(f"row_id={row_id}; " + "; ".join(f"{header}={value}" for header, value in zip(headers, cells) if header))
        if not records:
            return _empty(source, self.name, self.version)
        text_body = "\n".join(records)
        section = ParsedSection(section_id=_section_id(source.source_id, "rows", 0), heading=path.stem, text=text_body, section_path=[path.stem], metadata={"headers": headers, "row_count": len(records)})
        return _parsed(source, path.stem, [section], self.name, self.version, {"row_count": len(records), "column_count": len(headers)})


def _parsed(source: KnowledgeSource, title: str, sections: list[ParsedSection], parser_name: str, parser_version: str, metadata: dict | None = None) -> ParsedDocument:
    content_hash = source.content_hash or hashlib.sha256("\n".join(item.text for item in sections).encode("utf-8")).hexdigest()
    return ParsedDocument(document_id=_document_id(source.source_id, content_hash), source_id=source.source_id, title=title.strip() or source.display_name, language=_language("\n".join(item.text for item in sections)), sections=sections, metadata=metadata or {}, content_hash=content_hash, parser_name=parser_name, parser_version=parser_version)


def _empty(source: KnowledgeSource, parser_name: str, parser_version: str) -> ParsedDocument:
    return ParsedDocument(document_id=_document_id(source.source_id, source.content_hash), source_id=source.source_id, title=source.display_name, content_hash=source.content_hash, parser_name=parser_name, parser_version=parser_version, status="empty")


def _failed(source: KnowledgeSource, code: str, parser_name: str, metadata: dict | None = None) -> ParsedDocument:
    return ParsedDocument(document_id=_document_id(source.source_id, source.content_hash or code), source_id=source.source_id, title=source.display_name, content_hash=source.content_hash, parser_name=parser_name, parser_version=PARSER_VERSION, metadata=metadata or {}, status="failed", error_code=code)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValueError("RAG_BINARY_CONTENT")
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = data.decode(encoding)
            if _control_ratio(text) <= 0.02:
                return text
        except UnicodeDecodeError:
            continue
    raise ValueError("RAG_TEXT_DECODE_FAILED")


def _pdf_page_heading(text: str, page_number: int) -> str:
    """Recover a conservative numbered section title from an extracted page."""
    candidates: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        match = re.match(r"^(\d{1,2})(?:\.0)?\s+([\u4e00-\u9fffA-Za-z][^\u3002\uff1b:\uff1a]{1,60})$", line)
        if not match:
            continue
        number = int(match.group(1))
        label = match.group(2).strip()
        if 1 <= number <= 20 and not re.search(r"(?:GB|DL|Q/GDW|ISBN|ICS)\s*$", label, re.IGNORECASE):
            candidates.append((number, f"{number} {label}"))
    if candidates:
        # The first short numbered heading on a page is normally the section
        # anchor; later numbered lines are often list items belonging to it.
        return candidates[0][1]
    return f"Page {page_number}"


def _control_ratio(text: str) -> float:
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t")
    return controls / max(1, len(text))


def _markdown_sections(text: str, source_id: str) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    stack: list[tuple[int, str]] = []
    current_heading = "Document"
    current_level = 0
    current_path: list[str] = []
    lines: list[str] = []
    in_code = False
    ordinal = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
        if match and not in_code:
            if lines and "\n".join(lines).strip():
                sections.append(_make_section(source_id, current_heading, current_level, lines, ordinal, current_path, line_number - len(lines), line_number - 1))
                ordinal += 1
            current_level = len(match.group(1))
            current_heading = match.group(2).strip()
            while stack and stack[-1][0] >= current_level:
                stack.pop()
            stack.append((current_level, current_heading))
            current_path = [item[1] for item in stack]
            lines = [line]
        else:
            lines.append(line)
    if lines and "\n".join(lines).strip():
        sections.append(_make_section(source_id, current_heading, current_level, lines, ordinal, current_path, max(1, len(text.splitlines()) - len(lines) + 1), len(text.splitlines())))
    return sections


def _make_section(source_id: str, heading: str, level: int, lines: list[str], ordinal: int, path: list[str] | None = None, start: int | None = None, end: int | None = None) -> ParsedSection:
    return ParsedSection(section_id=_section_id(source_id, heading, ordinal), heading=heading, heading_level=level, text="\n".join(lines).strip(), section_path=path or [heading], paragraph_start=start, paragraph_end=end, metadata={"ordinal": ordinal})


def _section_id(source_id: str, value: str, ordinal: int) -> str:
    return "sec_" + hashlib.sha1(f"{source_id}:{ordinal}:{value}".encode("utf-8")).hexdigest()[:16]


def _document_id(source_id: str, content_hash: str) -> str:
    return "doc_" + hashlib.sha1(f"{source_id}:{content_hash}".encode("utf-8")).hexdigest()[:20]


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return fallback


def _language(text: str) -> str:
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    letters = sum(1 for char in text if char.isalpha())
    return "zh" if chinese >= max(2, letters * 0.1) else "en"
