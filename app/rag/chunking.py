"""Versioned structure-aware chunking."""

import hashlib
import math
import re

from app.rag.models import ChunkRecord, ParsedDocument, ParsedSection


CHUNKER_VERSION = "structured_v2"


class StructuredChunker:
    chunker_version = CHUNKER_VERSION

    def __init__(self, target_chars: int = 1000, max_chars: int = 1600, overlap_chars: int = 140, min_chars: int = 120) -> None:
        self.target_chars = max(1, int(target_chars))
        self.max_chars = max(self.target_chars, int(max_chars))
        self.overlap_chars = max(0, min(int(overlap_chars), self.max_chars // 3))
        self.min_chars = max(0, min(int(min_chars), self.max_chars))

    def chunk(self, document: ParsedDocument) -> list[ChunkRecord]:
        if document.status != "parsed":
            return []
        raw: list[tuple[ParsedSection, str]] = []
        for section in document.sections:
            text = section.text.strip()
            if not text:
                continue
            parts = _split_section(text, self.target_chars, self.max_chars, self.overlap_chars)
            for part in parts:
                if part.strip():
                    raw.append((section, part.strip()))

        # Tiny heading-only pieces are merged with the preceding section when
        # that does not exceed the configured max. This avoids evidence made
        # entirely of a repeated heading.
        merged: list[tuple[ParsedSection, str]] = []
        for section, content in raw:
            if merged and len(content) < self.min_chars and len(merged[-1][1]) + len(content) + 2 <= self.max_chars:
                previous_section, previous = merged[-1]
                merged[-1] = (previous_section, f"{previous}\n\n{content}")
            else:
                merged.append((section, content))

        chunks: list[ChunkRecord] = []
        for ordinal, (section, content) in enumerate(merged):
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunk_id = "chk_" + hashlib.sha1(f"{document.document_id}:{section.section_id}:{ordinal}:{digest}".encode("utf-8")).hexdigest()[:20]
            chunks.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    source_id=document.source_id,
                    section_id=section.section_id,
                    title=document.title,
                    section_path=section.section_path or [section.heading],
                    content=content,
                    content_hash=digest,
                    char_count=len(content),
                    estimated_tokens=max(1, math.ceil(len(content) / 2.2)),
                    page_start=section.page_number,
                    page_end=section.page_number,
                    ordinal=ordinal,
                    metadata={
                        "section": section.heading,
                        "section_level": section.heading_level,
                        "paragraph_start": section.paragraph_start,
                        "paragraph_end": section.paragraph_end,
                        **section.metadata,
                    },
                    chunker_version=CHUNKER_VERSION,
                )
            )
        for index, chunk in enumerate(chunks):
            chunk.previous_chunk_id = chunks[index - 1].chunk_id if index else None
            chunk.next_chunk_id = chunks[index + 1].chunk_id if index + 1 < len(chunks) else None
        return chunks


def _split_section(text: str, target: int, maximum: int, overlap: int) -> list[str]:
    if len(text) <= maximum:
        return [text]
    # PDF/DOCX extraction often has single newlines rather than blank-line
    # paragraphs. Pack complete lines so numbered clauses, list entries and
    # table rows are not cut at arbitrary character offsets.
    paragraphs = _structural_blocks(text)
    result: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > maximum:
            if current:
                result.append(current)
                current = ""
            result.extend(_split_long(paragraph, maximum, overlap))
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= target or (current and len(candidate) <= maximum):
            current = candidate
        else:
            result.append(current)
            carry = _whole_line_tail(current, overlap) if overlap else ""
            current = f"{carry}\n\n{paragraph}".strip() if carry else paragraph
    if current:
        result.append(current)
    return result


def _structural_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            blocks.append(lines[0])
            continue
        # Preserve each extracted paragraph/list/table row as an atomic block.
        blocks.extend(lines)
    return blocks or [text]


def _whole_line_tail(text: str, target_chars: int) -> str:
    if not text or target_chars <= 0:
        return ""
    lines = text.splitlines()
    selected: list[str] = []
    used = 0
    for line in reversed(lines):
        if selected and used + len(line) + 1 > target_chars:
            break
        selected.append(line)
        used += len(line) + 1
    return "\n".join(reversed(selected))


def _split_long(text: str, maximum: int, overlap: int) -> list[str]:
    result: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + maximum)
        if end < len(text):
            boundary = max(text.rfind(mark, start + maximum // 2, end) for mark in ("\u3002", "\uff01", "\uff1f", ". ", "\n", " "))
            if boundary > start:
                end = boundary + (1 if text[boundary] in "\u3002\uff01\uff1f" else 0)
        part = text[start:end].strip()
        if part:
            result.append(part)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return result
