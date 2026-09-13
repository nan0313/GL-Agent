import hashlib

from app.rag.parser import MarkdownParser
from app.rag.config import RAG_CHUNK_MAX_CHARS, RAG_CHUNK_OVERLAP_CHARS
from app.rag.schema import RAGChunk, RAGDocument


KnowledgeChunk = RAGChunk


def split_documents(
    documents: list[RAGDocument],
    max_chars: int = RAG_CHUNK_MAX_CHARS,
    overlap_chars: int = RAG_CHUNK_OVERLAP_CHARS,
) -> list[RAGChunk]:
    chunks: list[RAGChunk] = []
    parser = MarkdownParser()
    for document in documents:
        sections = parser.parse_sections(document)
        chunk_index = 0
        for section in sections:
            for part in _split_with_overlap(section.content, max_chars, overlap_chars):
                content = part.strip()
                if not content:
                    continue
                chunk_id = _stable_chunk_id(document.doc_id, chunk_index, content)
                chunks.append(
                    RAGChunk(
                        chunk_id=chunk_id,
                        doc_id=document.doc_id,
                        source_path=document.source_path,
                        title=document.title,
                        section=section.title,
                        content=content,
                        char_count=len(content),
                        metadata={
                            "section_level": section.level,
                            "chunk_index": chunk_index,
                            "splitter": "markdown_heading_paragraph",
                            "max_chars": max_chars,
                            "overlap_chars": overlap_chars,
                        },
                    )
                )
                chunk_index += 1
    return chunks


def _split_markdown_sections(content: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.startswith("## ") and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _split_with_overlap(content: str, max_chars: int, overlap_chars: int) -> list[str]:
    max_chars = max(100, int(max_chars or 800))
    overlap_chars = max(0, min(int(overlap_chars or 0), max_chars // 2))
    paragraphs = [item.strip() for item in content.split("\n\n") if item.strip()]
    if not paragraphs:
        paragraphs = [content.strip()]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_split(paragraph, max_chars, overlap_chars))
            continue

        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current.strip():
            chunks.append(current.strip())
        overlap = current[-overlap_chars:] if overlap_chars and current else ""
        current = f"{overlap}\n\n{paragraph}".strip() if overlap else paragraph

    if current.strip():
        chunks.append(current.strip())
    return chunks


def _hard_split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return parts


def _stable_chunk_id(doc_id: str, index: int, content: str) -> str:
    digest = hashlib.sha1(f"{doc_id}:{index}:{content}".encode("utf-8")).hexdigest()[:12]
    return f"{doc_id}#chunk_{index}_{digest}"
