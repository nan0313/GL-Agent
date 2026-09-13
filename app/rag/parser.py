from dataclasses import dataclass
from pathlib import Path

from app.rag.document_loader import load_document
from app.rag.schema import RAGDocument


@dataclass(frozen=True)
class MarkdownSection:
    title: str
    content: str
    level: int


class MarkdownParser:
    def parse_path(self, path: Path) -> RAGDocument:
        return load_document(path)

    def parse_sections(self, document: RAGDocument) -> list[MarkdownSection]:
        if document.status != "parsed" or not document.content.strip():
            return []

        sections: list[MarkdownSection] = []
        current_title = document.title
        current_level = 1
        current_lines: list[str] = []

        for line in document.content.splitlines():
            heading = _parse_heading(line)
            if heading and current_lines:
                sections.append(
                    MarkdownSection(
                        title=current_title,
                        content="\n".join(current_lines).strip(),
                        level=current_level,
                    )
                )
                current_lines = []
            if heading:
                current_level, current_title = heading
            current_lines.append(line)

        if current_lines:
            sections.append(
                MarkdownSection(
                    title=current_title,
                    content="\n".join(current_lines).strip(),
                    level=current_level,
                )
            )
        return [section for section in sections if section.content.strip()]


def _parse_heading(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    hashes = len(stripped) - len(stripped.lstrip("#"))
    if hashes < 1 or hashes > 6:
        return None
    title = stripped.lstrip("#").strip()
    return hashes, title or "Untitled"
