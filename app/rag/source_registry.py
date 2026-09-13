"""Secure, configuration-scoped knowledge source discovery."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Iterable

from app.rag.config import RAGConfig, get_rag_config
from app.rag.models import KnowledgeSource


_SOURCE_TYPES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "txt",
    ".pdf": "pdf",
    ".docx": "docx",
    ".html": "html",
    ".htm": "html",
    ".csv": "csv",
}

_NON_PRODUCTION_AREAS = frozenset({"staging", "rejected"})
_SCAN_AREAS = frozenset({"active", "staging", "rejected"})


class SourceRegistryError(ValueError):
    """An input rejected by the source boundary with a safe error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class KnowledgeSourceRegistry:
    def __init__(self, config: RAGConfig | None = None, roots: Iterable[Path | str] | None = None) -> None:
        self.config = config or get_rag_config()
        self.roots = [Path(item).resolve() for item in (roots or self.config.knowledge_roots)]

    def scan(self, area: str = "active") -> list[KnowledgeSource]:
        """Discover sources in a controlled knowledge area.

        ``active`` keeps backward compatibility with the existing configured
        root: files directly below that root remain active legacy content,
        while ``staging`` and ``rejected`` subtrees are excluded.  New
        operational content should live below ``active``.  The non-active
        areas are available only for operator validation and are never used by
        the production index manager's default scan.
        """
        area = str(area or "active").strip().lower()
        if area not in _SCAN_AREAS:
            raise SourceRegistryError("RAG_INVALID_KNOWLEDGE_AREA")
        sources: list[KnowledgeSource] = []
        for root in self.roots:
            if not root.exists() or not root.is_dir():
                continue
            scan_root = root / area if area == "staging" else root
            if area == "rejected" and not scan_root.exists():
                continue
            if not scan_root.exists() or not scan_root.is_dir() or scan_root.is_symlink():
                continue
            for path in sorted(scan_root.rglob("*")):
                try:
                    resolved_path = path.resolve()
                except OSError:
                    continue
                if not _within(resolved_path, root):
                    continue
                relative_parts = path.relative_to(root).parts
                if area == "active" and any(part.lower() in _NON_PRODUCTION_AREAS for part in relative_parts):
                    continue
                if area == "staging" and (not relative_parts or relative_parts[0].lower() != "staging"):
                    continue
                if area == "rejected" and (not relative_parts or relative_parts[0].lower() != "rejected"):
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                if path.suffix.lower() not in self.config.allowed_file_types:
                    continue
                try:
                    sources.append(self._describe(root, path))
                except SourceRegistryError as exc:
                    sources.append(self._failed_source(root, path, exc.code))
        return _mark_duplicates(sources)

    def register(self, relative_location: str, root_index: int = 0) -> KnowledgeSource:
        if root_index < 0 or root_index >= len(self.roots):
            raise SourceRegistryError("RAG_ROOT_NOT_CONFIGURED")
        root = self.roots[root_index]
        candidate = Path(relative_location)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SourceRegistryError("RAG_PATH_TRAVERSAL")
        if any(part.lower() in _NON_PRODUCTION_AREAS for part in candidate.parts):
            raise SourceRegistryError("RAG_NON_ACTIVE_SOURCE")
        path = (root / candidate).resolve()
        if not _within(path, root) or not path.is_file() or path.is_symlink():
            raise SourceRegistryError("RAG_SOURCE_NOT_FOUND")
        if path.suffix.lower() not in self.config.allowed_file_types:
            raise SourceRegistryError("RAG_UNSUPPORTED_FILE_TYPE")
        return self._describe(root, path)

    def _describe(self, root: Path, path: Path) -> KnowledgeSource:
        relative = path.relative_to(root).as_posix()
        source_id = _source_id(root, relative)
        size = path.stat().st_size
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        if size > self.config.max_file_size:
            raise SourceRegistryError("RAG_SOURCE_TOO_LARGE")
        digest = _sha256(path)
        suffix = path.suffix.lower()
        return KnowledgeSource(
            source_id=source_id,
            source_type=_SOURCE_TYPES[suffix],
            display_name=path.name,
            relative_location=relative,
            content_hash=digest,
            file_size=size,
            modified_at=modified,
            ingestion_status="registered",
            source_status=_source_area(relative),
            parser_status="pending",
            index_status="not_indexed",
            provenance="configured_local_root",
            language="unknown",
        )

    def _failed_source(self, root: Path, path: Path, error_code: str) -> KnowledgeSource:
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        return KnowledgeSource(
            source_id=_source_id(root, relative),
            source_type=_SOURCE_TYPES.get(path.suffix.lower(), "txt"),
            display_name=path.name,
            relative_location=relative,
            content_hash="",
            file_size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            ingestion_status="failed",
            source_status=_source_area(relative),
            parser_status="failed",
            index_status="not_indexed",
            provenance="configured_local_root",
            error_code=error_code,
        )

    def path_for(self, source: KnowledgeSource) -> Path:
        for root in self.roots:
            candidate = (root / source.relative_location).resolve()
            if _within(candidate, root) and candidate.is_file() and not candidate.is_symlink():
                return candidate
        raise SourceRegistryError("RAG_SOURCE_NOT_FOUND")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _source_id(root: Path, relative: str) -> str:
    # The configured root name is a logical namespace; the absolute path is
    # never returned or used as a public identifier.
    value = f"{root.name}:{_logical_relative(relative)}".encode("utf-8")
    return f"src_{hashlib.sha256(value).hexdigest()[:20]}"


def _logical_relative(relative: str) -> str:
    """Keep a source id stable when staging content is activated."""
    parts = Path(relative).parts
    if parts and parts[0].lower() in {"active", "staging", "rejected"}:
        parts = parts[1:]
    return "/".join(parts)


def _source_area(relative: str) -> str:
    first = Path(relative).parts[0].lower() if Path(relative).parts else ""
    if first == "staging":
        return "staging"
    if first == "rejected":
        return "rejected"
    return "active"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mark_duplicates(sources: list[KnowledgeSource]) -> list[KnowledgeSource]:
    by_hash: dict[str, str] = {}
    result: list[KnowledgeSource] = []
    for source in sources:
        if source.content_hash and source.content_hash in by_hash:
            source = source.model_copy(update={"duplicate_of_source_id": by_hash[source.content_hash]})
        elif source.content_hash:
            by_hash[source.content_hash] = source.source_id
        result.append(source)
    return result
