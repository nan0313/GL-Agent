import hashlib
from pathlib import Path

from app.rag.schema import RAGDocument

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "docs" / "knowledge"


KnowledgeDocument = RAGDocument


def load_knowledge_documents(
    knowledge_dir: Path | None = None,
) -> list[RAGDocument]:
    root = knowledge_dir or DEFAULT_KNOWLEDGE_DIR
    if not root.exists():
        return []

    documents: list[RAGDocument] = []
    for path in sorted(root.glob("*.md")):
        documents.append(load_document(path))
    return documents


def load_document(path: Path) -> RAGDocument:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        doc_id = _stable_doc_id(path, "")
        return RAGDocument(
            doc_id=doc_id,
            source_path=str(path),
            title=path.stem,
            content="",
            status="failed",
            metadata={
                "loader": "local_markdown",
                "error": str(exc)[:300],
                "suffix": path.suffix.lower(),
            },
        )

    doc_id = _stable_doc_id(path, content)
    stripped = content.strip()
    status = "parsed" if stripped else "empty"
    return RAGDocument(
        doc_id=doc_id,
        source_path=str(path),
        title=_extract_title(content, path.stem),
        content=content,
        status=status,
        metadata={
            "loader": "local_markdown",
            "suffix": path.suffix.lower(),
            "char_count": len(content),
        },
    )


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def _stable_doc_id(path: Path, content: str) -> str:
    try:
        relative = path.resolve().relative_to(Path(__file__).resolve().parents[2])
        raw = relative.as_posix()
        return "doc_" + raw.replace("/", "_").replace("\\", "_").replace(":", "").replace(" ", "_")
    except ValueError:
        # External test fixtures must not leak or encode an absolute path in ids.
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
        stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)
        return f"doc_{stem or 'document'}_{digest}"
