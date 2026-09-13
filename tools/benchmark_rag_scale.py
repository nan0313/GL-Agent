"""Measure SQLite/FTS5 with Python cosine and HNSW at bounded sizes.

The generated corpus is explicitly synthetic and is created in a temporary
directory.  It is not a semantic-quality benchmark and never calls Qwen.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.config import RAGConfig
from app.rag.embeddings import DisabledEmbeddingProvider
from app.rag.index_manager import RAGIndexManager
from app.rag.retrieval import HybridRetriever


def _rss_mb() -> float | None:
    """Return Windows working-set size without adding a process library."""
    try:
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        process = ctypes.c_void_p(-1)
        api = ctypes.windll.psapi.GetProcessMemoryInfo
        api.restype = ctypes.c_bool
        ok = api(process, ctypes.byref(counters), counters.cb)
        return round(counters.WorkingSetSize / (1024 * 1024), 3) if ok else None
    except (AttributeError, OSError, TypeError):
        return None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def _write_corpus(root: Path, chunk_count: int) -> None:
    document_count = 100
    # The Markdown title itself is a structured section, so account for that
    # section when targeting the requested chunk scale.
    sections_per_document = max(0, math.ceil(chunk_count / document_count) - 1)
    for document_index in range(document_count):
        sections: list[str] = [f"# Synthetic scale document {document_index:03d}"]
        for section_index in range(sections_per_document):
            value = document_index * 1000 + section_index
            sections.extend(
                [
                    f"## Entity scale-{document_index:03d}-{section_index:03d}",
                    (
                        f"Synthetic scale fixture record {value} describes sector {document_index % 10}, "
                        f"rated value {value + 100} kV and operating distance {value % 900 + 100} m. "
                        "This paragraph is generated only to measure ingestion, indexing, and retrieval "
                        "costs; it is not a real business document and must not be used as domain evidence."
                    ),
                ]
            )
        (root / f"scale_{document_index:03d}.md").write_text("\n\n".join(sections), encoding="utf-8")


def _config(root: Path, work: Path, vector_backend: str) -> RAGConfig:
    return RAGConfig(
        knowledge_roots=[str(root)],
        database_path=str(work / "rag.sqlite3"),
        index_path=str(work / "index"),
        dense_enabled=True,
        embedding_provider="fixture",
        vector_backend=vector_backend,
        chunk_target_chars=800,
        chunk_max_chars=1600,
        chunk_overlap_chars=100,
        chunk_min_chars=120,
        candidate_limit=24,
        top_k=3,
    )


def _query_latencies(manager: RAGIndexManager, count: int) -> dict[str, dict[str, float | None]]:
    queries = [f"scale entity {index:03d} rated value {index + 100} kV" for index in range(min(10, count))]
    lexical_config = manager.config.model_copy(update={"dense_enabled": False, "embedding_provider": "disabled"})
    dense_config = manager.config.model_copy(update={"lexical_enabled": False})
    retrievers = {
        "lexical": HybridRetriever(manager.store, DisabledEmbeddingProvider(), manager.vector_index, lexical_config),
        "dense": HybridRetriever(manager.store, manager.embedding_provider, manager.vector_index, dense_config),
        "hybrid": HybridRetriever(manager.store, manager.embedding_provider, manager.vector_index, manager.config),
    }
    result: dict[str, dict[str, float | None]] = {}
    for name, retriever in retrievers.items():
        timings: list[float] = []
        for query in queries:
            started = perf_counter()
            retriever.retrieve(query, top_k=3, candidate_limit=24)
            timings.append((perf_counter() - started) * 1000)
        result[name] = {"p50_ms": _percentile(timings, 0.50), "p95_ms": _percentile(timings, 0.95)}
    return result


def _file_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.parent.glob(f"{path.name}*") if item.is_file())


def measure(chunk_count: int, vector_backend: str = "python_cosine_fallback") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rag-scale-") as temporary:
        work = Path(temporary)
        root = work / "knowledge"
        root.mkdir()
        _write_corpus(root, chunk_count)
        memory_before = _rss_mb()
        manager = RAGIndexManager(_config(root, work, vector_backend))
        started = perf_counter()
        full = manager.rebuild("full")
        full_duration = round((perf_counter() - started) * 1000, 3)
        stats = manager.store.stats()
        actual_chunks = int(stats["chunk_count"])

        started = perf_counter()
        unchanged = manager.rebuild("incremental")
        no_change_duration = round((perf_counter() - started) * 1000, 3)

        changed_path = root / "scale_000.md"
        changed_path.write_text(changed_path.read_text(encoding="utf-8") + "\n\n## Incremental update\n\nA single synthetic record changed.", encoding="utf-8")
        started = perf_counter()
        modified = manager.rebuild("incremental")
        modified_duration = round((perf_counter() - started) * 1000, 3)

        query_latency = _query_latencies(manager, actual_chunks)
        index_size = sum(item.stat().st_size for item in Path(manager.config.index_path).glob("*") if item.is_file() and item.suffix in {".ragindex", ".hnsw", ".json"})
        sqlite_size = _file_size(Path(manager.config.database_path))
        memory_after = _rss_mb()
        load_manager = RAGIndexManager(_config(root, work, vector_backend))
        started = perf_counter()
        validation = load_manager.validate()
        load_duration = round((perf_counter() - started) * 1000, 3)
        load_manager.close()
        manager.close()
        cache_total = int(modified.get("cache_hits", 0)) + int(modified.get("embedding_count", 0))
        return {
            "fixture_type": "synthetic_scale_fixture",
            "vector_backend": vector_backend,
            "requested_chunks": chunk_count,
            "document_count": int(stats["document_count"]),
            "chunk_count": actual_chunks,
            "full_rebuild_ms": full_duration,
            "incremental_no_change_ms": no_change_duration,
            "incremental_single_document_ms": modified_duration,
            "embedding_cache_hit_rate_after_change": round(int(modified.get("cache_hits", 0)) / cache_total, 4) if cache_total else None,
            "full_embedding_count": full.get("embedding_count", 0),
            "full_cache_hits": full.get("cache_hits", 0),
            "no_change_embedding_count": unchanged.get("embedding_count", 0),
            "no_change_cache_hits": unchanged.get("cache_hits", 0),
            "modified_embedding_count": modified.get("embedding_count", 0),
            "modified_cache_hits": modified.get("cache_hits", 0),
            "index_load_ms": load_duration,
            "index_validation": validation,
            "retrieval_latency": query_latency,
            "rss_before_mb": memory_before,
            "rss_after_mb": memory_after,
            "index_size_bytes": index_size,
            "sqlite_size_bytes": sqlite_size,
            "embedding_provider": "fixture",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure bounded synthetic RAG scale")
    parser.add_argument("--sizes", default="100,1000,5000", help="requested chunk sizes")
    parser.add_argument("--backends", default="python_cosine_fallback,hnsw", help="comma-separated vector backends")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    backends = [item.strip() for item in args.backends.split(",") if item.strip()]
    report = {"benchmark": "rag-scale-v2", "fixture_type": "synthetic_scale_fixture", "runs": [measure(int(size.strip()), backend) for backend in backends for size in args.sizes.split(",") if size.strip()]}
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
