"""Persistent local vector backends with an explicit small-scale fallback."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Iterable


class VectorIndexError(RuntimeError):
    """Stable, safe vector backend error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VectorBackend(ABC):
    """Minimal backend contract shared by Python cosine and HNSW."""

    backend_name = "unknown"
    backend = "unknown"
    backend_version = "unknown"
    supports_incremental_update = False
    supports_delete = False

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    @abstractmethod
    def provider_key(self) -> str | None: ...

    @property
    @abstractmethod
    def index_version(self) -> str | None: ...

    @property
    @abstractmethod
    def item_count(self) -> int: ...

    @property
    @abstractmethod
    def capacity(self) -> int: ...

    @property
    @abstractmethod
    def deleted_count(self) -> int: ...

    @abstractmethod
    def build(self, vectors: dict[str, list[float]], *, version: str, provider_key: str, dimension: int, incremental: bool = False) -> Path: ...

    @abstractmethod
    def load(self, version: str, *, provider_key: str | None = None, dimension: int | None = None) -> None: ...

    @abstractmethod
    def search(self, query_vector: list[float], top_k: int = 10, allowed_ids: set[str] | None = None) -> list[tuple[str, float]]: ...

    @abstractmethod
    def add(self, items: dict[str, list[float]]) -> None: ...

    @abstractmethod
    def update(self, items: dict[str, list[float]]) -> None: ...

    @abstractmethod
    def delete(self, chunk_ids: Iterable[str]) -> None: ...

    @abstractmethod
    def persist(self) -> Path | None: ...

    @abstractmethod
    def validate(self) -> dict[str, Any]: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...


class PersistentVectorIndex(VectorBackend):
    """JSON-backed Python cosine index retained as the compatibility fallback."""

    backend_name = "python_cosine_fallback"
    backend = backend_name
    backend_version = "json_v1"
    supports_incremental_update = False
    supports_delete = True

    def __init__(self, index_path: str | Path) -> None:
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self._vectors: dict[str, list[float]] = {}
        self.version: str | None = None
        self._provider_key: str | None = None
        self._dimension: int = 0
        self._closed = False

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_key(self) -> str | None:
        return self._provider_key

    @property
    def index_version(self) -> str | None:
        return self.version

    @property
    def item_count(self) -> int:
        return len(self._vectors)

    @property
    def capacity(self) -> int:
        return len(self._vectors)

    @property
    def deleted_count(self) -> int:
        return 0

    def build(self, vectors: dict[str, list[float]], *, version: str, provider_key: str, dimension: int, incremental: bool = False) -> Path:
        self._validate_vectors(vectors, dimension)
        payload = {"format": 1, "backend": self.backend, "backend_version": self.backend_version, "version": version, "provider_key": provider_key, "dimension": dimension, "items": [{"chunk_id": key, "vector": value} for key, value in sorted(vectors.items())]}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["checksum"] = sha256(encoded.encode("utf-8")).hexdigest()
        target = self.index_path / f"{_safe_version(version)}.ragindex"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.index_path, prefix=".ragindex-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
            temporary.replace(target)
        except OSError as exc:
            raise VectorIndexError("RAG_VECTOR_INDEX_WRITE_FAILED") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
        self._vectors = dict(vectors)
        self.version = version
        self._provider_key = provider_key
        self._dimension = dimension
        self._closed = False
        return target

    def load(self, version: str, *, provider_key: str | None = None, dimension: int | None = None) -> None:
        path = self.index_path / f"{_safe_version(version)}.ragindex"
        if not path.exists():
            raise VectorIndexError("RAG_VECTOR_INDEX_NOT_FOUND")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            checksum = payload.pop("checksum")
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if sha256(encoded.encode("utf-8")).hexdigest() != checksum:
                raise VectorIndexError("RAG_VECTOR_INDEX_CORRUPT")
            if payload.get("backend") != self.backend:
                raise VectorIndexError("RAG_VECTOR_BACKEND_MISMATCH")
            actual_dimension = int(payload["dimension"])
            if dimension is not None and actual_dimension != dimension:
                raise VectorIndexError("RAG_VECTOR_DIMENSION_MISMATCH")
            if provider_key is not None and payload.get("provider_key") != provider_key:
                raise VectorIndexError("RAG_VECTOR_MODEL_MISMATCH")
            vectors = {str(item["chunk_id"]): [float(value) for value in item["vector"]] for item in payload["items"]}
            self._validate_vectors(vectors, actual_dimension)
        except VectorIndexError:
            raise
        except Exception as exc:
            raise VectorIndexError("RAG_VECTOR_INDEX_CORRUPT") from exc
        self._vectors = vectors
        self.version = str(payload["version"])
        self._provider_key = str(payload["provider_key"])
        self._dimension = actual_dimension
        self._closed = False

    def add(self, items: dict[str, list[float]]) -> None:
        if not self._dimension:
            self._dimension = len(next(iter(items.values()))) if items else 0
        self._validate_vectors(items, self._dimension)
        self._vectors.update(items)

    def update(self, items: dict[str, list[float]]) -> None:
        self.add(items)

    def delete(self, chunk_ids: Iterable[str]) -> None:
        for chunk_id in chunk_ids:
            self._vectors.pop(chunk_id, None)

    def persist(self) -> Path | None:
        if self.version is None or self._provider_key is None:
            return None
        return self.build(self._vectors, version=self.version, provider_key=self._provider_key, dimension=self._dimension)

    def validate(self) -> dict[str, Any]:
        return {"status": "success", "backend": self.backend, "version": self.version, "provider_key": self._provider_key, "dimension": self._dimension, "item_count": self.item_count, "capacity": self.capacity, "deleted_count": 0}

    def close(self) -> None:
        self._closed = True

    def health(self) -> dict[str, Any]:
        return {"available": True, "backend": self.backend, "backend_version": self.backend_version, "dimension": self._dimension, "provider_key": self._provider_key, "index_version": self.version, "item_count": self.item_count, "capacity": self.capacity, "deleted_count": 0, "supports_incremental_update": self.supports_incremental_update, "supports_delete": self.supports_delete}

    def search(self, query_vector: list[float], top_k: int = 10, allowed_ids: set[str] | None = None) -> list[tuple[str, float]]:
        if not self._dimension:
            return []
        self._validate_vectors({"query": query_vector}, self._dimension)
        result: list[tuple[str, float]] = []
        for chunk_id, vector in self._vectors.items():
            if allowed_ids is not None and chunk_id not in allowed_ids:
                continue
            result.append((chunk_id, round(sum(a * b for a, b in zip(query_vector, vector)), 8)))
        result.sort(key=lambda item: (-item[1], item[0]))
        return result[:max(0, int(top_k))]

    def _validate_vectors(self, vectors: dict[str, list[float]], dimension: int) -> None:
        if dimension < 1:
            raise VectorIndexError("RAG_VECTOR_INVALID_DIMENSION")
        for vector in vectors.values():
            if len(vector) != dimension or any(not math.isfinite(float(value)) for value in vector):
                raise VectorIndexError("RAG_VECTOR_DIMENSION_MISMATCH")


class HNSWVectorBackend(VectorBackend):
    """Persistent hnswlib backend with versioned manifest and label mapping."""

    backend_name = "hnsw"
    backend = backend_name
    supports_incremental_update = True
    supports_delete = True

    def __init__(self, index_path: str | Path, *, space: str = "cosine", m: int = 16, ef_construction: int = 200, ef_search: int = 64, max_elements: int = 0, resize_factor: float = 2.0) -> None:
        try:
            import hnswlib  # type: ignore
        except ImportError as exc:
            raise VectorIndexError("RAG_HNSW_DEPENDENCY_MISSING") from exc
        if space not in {"cosine", "l2", "ip"} or int(m) < 4 or int(ef_construction) < 1 or int(ef_search) < 1 or float(resize_factor) <= 1.0:
            raise VectorIndexError("RAG_HNSW_INVALID_CONFIG")
        self._hnswlib = hnswlib
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.space = space
        self.m = int(m)
        self.ef_construction = int(ef_construction)
        self.ef_search = int(ef_search)
        self.configured_max_elements = max(0, int(max_elements))
        self.resize_factor = float(resize_factor)
        self.backend_version = f"hnswlib_{getattr(hnswlib, '__version__', '0.8.0')}"
        self._index: Any | None = None
        self._dimension = 0
        self._provider_key: str | None = None
        self.version: str | None = None
        self._capacity = 0
        self._labels: dict[str, int] = {}
        self._label_to_chunk: dict[int, str] = {}
        self._vector_checksums: dict[str, str] = {}
        self._retired_labels: set[int] = set()
        self._closed = False

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_key(self) -> str | None:
        return self._provider_key

    @property
    def index_version(self) -> str | None:
        return self.version

    @property
    def item_count(self) -> int:
        return len(self._labels)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def deleted_count(self) -> int:
        return len(self._retired_labels)

    @property
    def label_map(self) -> dict[str, int]:
        return dict(self._labels)

    def build(self, vectors: dict[str, list[float]], *, version: str, provider_key: str, dimension: int, incremental: bool = False) -> Path:
        self._validate_vectors(vectors, dimension)
        if incremental and self._index is not None and self.version is not None:
            return self._incremental_build(vectors, version=version, provider_key=provider_key, dimension=dimension)
        return self._full_build(vectors, version=version, provider_key=provider_key, dimension=dimension)

    def _full_build(self, vectors: dict[str, list[float]], *, version: str, provider_key: str, dimension: int) -> Path:
        index = self._new_index(dimension, self._initial_capacity(len(vectors)))
        labels: dict[str, int] = {}
        reverse: dict[int, str] = {}
        retired: set[int] = set()
        for chunk_id in sorted(vectors):
            label = _allocate_label(chunk_id, reverse, retired)
            labels[chunk_id] = label
            reverse[label] = chunk_id
        if vectors:
            self._add_items(index, vectors, labels)
        checksums = {chunk_id: _vector_checksum(vector) for chunk_id, vector in vectors.items()}
        target = self._persist(index, vectors, labels, reverse, checksums, retired, version, provider_key, dimension, self._capacity_for(index))
        self._set_state(index, labels, reverse, checksums, retired, version, provider_key, dimension, self._capacity_for(index))
        return target

    def _incremental_build(self, vectors: dict[str, list[float]], *, version: str, provider_key: str, dimension: int) -> Path:
        if provider_key != self._provider_key:
            raise VectorIndexError("RAG_HNSW_PROVIDER_MISMATCH")
        if dimension != self._dimension:
            raise VectorIndexError("RAG_HNSW_DIMENSION_MISMATCH")
        index = self._clone_index()
        labels = dict(self._labels)
        reverse = dict(self._label_to_chunk)
        checksums = dict(self._vector_checksums)
        retired = set(self._retired_labels)
        desired = set(vectors)
        for chunk_id in sorted(set(labels) - desired):
            label = labels.pop(chunk_id)
            reverse.pop(label, None)
            retired.add(label)
            try:
                index.mark_deleted(label)
            except Exception as exc:
                raise VectorIndexError("RAG_HNSW_LABEL_MAP_INVALID") from exc
            checksums.pop(chunk_id, None)
        additions: dict[str, list[float]] = {}
        for chunk_id in sorted(desired):
            digest = _vector_checksum(vectors[chunk_id])
            if chunk_id in labels and checksums.get(chunk_id) == digest:
                continue
            if chunk_id in labels:
                old_label = labels.pop(chunk_id)
                reverse.pop(old_label, None)
                retired.add(old_label)
                try:
                    index.mark_deleted(old_label)
                except Exception as exc:
                    raise VectorIndexError("RAG_HNSW_LABEL_MAP_INVALID") from exc
            label = _allocate_label(chunk_id, reverse, retired)
            labels[chunk_id] = label
            reverse[label] = chunk_id
            checksums[chunk_id] = digest
            additions[chunk_id] = vectors[chunk_id]
        required = int(index.get_current_count()) + len(additions)
        capacity = self._capacity_for(index)
        if required > capacity:
            capacity = max(required, int(math.ceil(max(1, capacity) * self.resize_factor)))
            try:
                index.resize_index(capacity)
            except Exception as exc:
                raise VectorIndexError("RAG_HNSW_CAPACITY_EXCEEDED") from exc
        if additions:
            self._add_items(index, additions, labels)
        target = self._persist(index, vectors, labels, reverse, checksums, retired, version, provider_key, dimension, capacity)
        self._set_state(index, labels, reverse, checksums, retired, version, provider_key, dimension, capacity)
        return target

    def _new_index(self, dimension: int, capacity: int) -> Any:
        try:
            index = self._hnswlib.Index(space=self.space, dim=dimension)
            index.init_index(max_elements=max(1, capacity), ef_construction=self.ef_construction, M=self.m)
            index.set_ef(self.ef_search)
            return index
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_BUILD_FAILED") from exc

    def _clone_index(self) -> Any:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.index_path, prefix=".hnsw-clone-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
            self._index.save_index(str(temporary))
            clone = self._hnswlib.Index(space=self.space, dim=self._dimension)
            clone.load_index(str(temporary), max_elements=max(1, self._capacity))
            clone.set_ef(self.ef_search)
            return clone
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_CLONE_FAILED") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def _add_items(self, index: Any, vectors: dict[str, list[float]], labels: dict[str, int]) -> None:
        try:
            import numpy as np  # type: ignore
            ids = sorted(vectors)
            index.add_items(np.asarray([vectors[item] for item in ids], dtype=np.float32), np.asarray([labels[item] for item in ids], dtype=np.int64))
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_ADD_FAILED") from exc

    def _persist(self, index: Any, vectors: dict[str, list[float]], labels: dict[str, int], reverse: dict[int, str], checksums: dict[str, str], retired: set[int], version: str, provider_key: str, dimension: int, capacity: int) -> Path:
        safe = _safe_version(version)
        target = self.index_path / f"{safe}.hnsw"
        manifest_target = self.index_path / f"{safe}.hnsw.manifest.json"
        temporary_binary: Path | None = None
        temporary_manifest: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.index_path, prefix=".hnsw-", suffix=".tmp", delete=False) as stream:
                temporary_binary = Path(stream.name)
            index.save_index(str(temporary_binary))
            binary_checksum = _sha256_file(temporary_binary)
            payload = {
                "format": 1,
                "backend": self.backend,
                "backend_version": self.backend_version,
                "version": version,
                "provider_key": provider_key,
                "dimension": dimension,
                "item_count": len(labels),
                "capacity": capacity,
                "deleted_count": len(retired),
                "space": self.space,
                "m": self.m,
                "ef_construction": self.ef_construction,
                "ef_search": self.ef_search,
                "labels": {key: labels[key] for key in sorted(labels)},
                "vector_checksums": {key: checksums[key] for key in sorted(checksums)},
                "retired_labels": sorted(retired),
                "binary_sha256": binary_checksum,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            encoded = _canonical_json(payload)
            payload["checksum"] = sha256(encoded.encode("utf-8")).hexdigest()
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.index_path, prefix=".hnsw-manifest-", suffix=".tmp", delete=False) as stream:
                temporary_manifest = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
            temporary_binary.replace(target)
            temporary_manifest.replace(manifest_target)
            return target
        except OSError as exc:
            raise VectorIndexError("RAG_HNSW_INDEX_WRITE_FAILED") from exc
        except VectorIndexError:
            raise
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_INDEX_WRITE_FAILED") from exc
        finally:
            for temporary in (temporary_binary, temporary_manifest):
                if temporary is not None and temporary.exists():
                    temporary.unlink(missing_ok=True)

    def load(self, version: str, *, provider_key: str | None = None, dimension: int | None = None) -> None:
        safe = _safe_version(version)
        binary = self.index_path / f"{safe}.hnsw"
        manifest_path = self.index_path / f"{safe}.hnsw.manifest.json"
        if not binary.exists() or not manifest_path.exists():
            raise VectorIndexError("RAG_HNSW_INDEX_NOT_FOUND")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            checksum = payload.pop("checksum")
            if sha256(_canonical_json(payload).encode("utf-8")).hexdigest() != checksum:
                raise VectorIndexError("RAG_HNSW_INDEX_CORRUPT")
            if payload.get("backend") != self.backend:
                raise VectorIndexError("RAG_HNSW_BACKEND_MISMATCH")
            if str(payload.get("backend_version")) != self.backend_version:
                raise VectorIndexError("RAG_HNSW_VERSION_MISMATCH")
            if (str(payload.get("space")) != self.space or int(payload.get("m")) != self.m or int(payload.get("ef_construction")) != self.ef_construction):
                raise VectorIndexError("RAG_HNSW_CONFIG_MISMATCH")
            if str(payload.get("version")) != str(version):
                raise VectorIndexError("RAG_HNSW_VERSION_MISMATCH")
            if provider_key is not None and payload.get("provider_key") != provider_key:
                raise VectorIndexError("RAG_HNSW_PROVIDER_MISMATCH")
            actual_dimension = int(payload["dimension"])
            if dimension is not None and actual_dimension != dimension:
                raise VectorIndexError("RAG_HNSW_DIMENSION_MISMATCH")
            if _sha256_file(binary) != payload.get("binary_sha256"):
                raise VectorIndexError("RAG_HNSW_INDEX_CORRUPT")
            labels = {str(key): int(value) for key, value in dict(payload["labels"]).items()}
            reverse = {label: chunk_id for chunk_id, label in labels.items()}
            retired = {int(value) for value in payload.get("retired_labels", [])}
            if len(reverse) != len(labels) or any(label <= 0 for label in reverse) or set(reverse).intersection(retired):
                raise VectorIndexError("RAG_HNSW_LABEL_MAP_INVALID")
            if int(payload["item_count"]) != len(labels) or set(labels) != set(payload.get("vector_checksums", {})):
                raise VectorIndexError("RAG_HNSW_LABEL_MAP_INVALID")
            capacity = int(payload["capacity"])
            index = self._hnswlib.Index(space=str(payload["space"]), dim=actual_dimension)
            index.load_index(str(binary), max_elements=max(1, capacity))
            index.set_ef(int(payload["ef_search"]))
            current_count = int(index.get_current_count())
            if current_count < len(labels) or current_count > capacity:
                raise VectorIndexError("RAG_HNSW_ITEM_COUNT_MISMATCH")
        except VectorIndexError:
            raise
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_INDEX_CORRUPT") from exc
        self._set_state(index, labels, reverse, dict(payload.get("vector_checksums", {})), retired, version, str(payload["provider_key"]), actual_dimension, capacity)

    def add(self, items: dict[str, list[float]]) -> None:
        if self._index is None:
            raise VectorIndexError("RAG_HNSW_INDEX_NOT_LOADED")
        self._validate_vectors(items, self._dimension)
        additions: dict[str, list[float]] = {}
        for chunk_id in sorted(items):
            if chunk_id in self._labels:
                continue
            label = _allocate_label(chunk_id, self._label_to_chunk, self._retired_labels)
            self._labels[chunk_id] = label
            self._label_to_chunk[label] = chunk_id
            self._vector_checksums[chunk_id] = _vector_checksum(items[chunk_id])
            additions[chunk_id] = items[chunk_id]
        self._resize_for(self._index, len(additions))
        if additions:
            self._add_items(self._index, additions, self._labels)

    def update(self, items: dict[str, list[float]]) -> None:
        if self._index is None:
            raise VectorIndexError("RAG_HNSW_INDEX_NOT_LOADED")
        self._validate_vectors(items, self._dimension)
        changed: dict[str, list[float]] = {}
        for chunk_id, vector in items.items():
            digest = _vector_checksum(vector)
            if self._vector_checksums.get(chunk_id) == digest:
                continue
            if chunk_id in self._labels:
                old = self._labels.pop(chunk_id)
                self._label_to_chunk.pop(old, None)
                self._retired_labels.add(old)
                self._index.mark_deleted(old)
            label = _allocate_label(chunk_id, self._label_to_chunk, self._retired_labels)
            self._labels[chunk_id] = label
            self._label_to_chunk[label] = chunk_id
            self._vector_checksums[chunk_id] = digest
            changed[chunk_id] = vector
        self._resize_for(self._index, len(changed))
        if changed:
            self._add_items(self._index, changed, self._labels)

    def delete(self, chunk_ids: Iterable[str]) -> None:
        if self._index is None:
            return
        for chunk_id in chunk_ids:
            label = self._labels.pop(chunk_id, None)
            if label is None:
                continue
            self._label_to_chunk.pop(label, None)
            self._vector_checksums.pop(chunk_id, None)
            self._retired_labels.add(label)
            self._index.mark_deleted(label)

    def persist(self) -> Path | None:
        if self._index is None or self.version is None or self._provider_key is None:
            return None
        return self._persist(self._index, {}, self._labels, self._label_to_chunk, self._vector_checksums, self._retired_labels, self.version, self._provider_key, self._dimension, self._capacity)

    def validate(self) -> dict[str, Any]:
        if self._index is None:
            raise VectorIndexError("RAG_HNSW_INDEX_NOT_LOADED")
        current_count = int(self._index.get_current_count())
        if current_count < self.item_count or current_count > self.capacity:
            raise VectorIndexError("RAG_HNSW_ITEM_COUNT_MISMATCH")
        if len(self._labels) != len(self._vector_checksums):
            raise VectorIndexError("RAG_HNSW_LABEL_MAP_INVALID")
        return {"status": "success", "backend": self.backend, "version": self.version, "provider_key": self._provider_key, "dimension": self._dimension, "item_count": self.item_count, "capacity": self.capacity, "deleted_count": self.deleted_count}

    def close(self) -> None:
        self._index = None
        self._closed = True

    def health(self) -> dict[str, Any]:
        return {"available": True, "backend": self.backend, "backend_version": self.backend_version, "dimension": self._dimension, "provider_key": self._provider_key, "index_version": self.version, "item_count": self.item_count, "capacity": self.capacity, "deleted_count": self.deleted_count, "supports_incremental_update": self.supports_incremental_update, "supports_delete": self.supports_delete}

    def search(self, query_vector: list[float], top_k: int = 10, allowed_ids: set[str] | None = None) -> list[tuple[str, float]]:
        if self._index is None or not self._labels or top_k <= 0:
            return []
        self._validate_vectors({"query": query_vector}, self._dimension)
        try:
            import numpy as np  # type: ignore
            current_count = int(self._index.get_current_count())
            available_count = max(1, current_count - self.deleted_count)
            filtered = allowed_ids is not None and len(allowed_ids) < len(self._labels)
            query_k = available_count if filtered else min(available_count, max(int(top_k) * 4, int(top_k)))
            labels, distances = self._index.knn_query(np.asarray([query_vector], dtype=np.float32), k=max(1, query_k))
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_SEARCH_FAILED") from exc
        result: list[tuple[str, float]] = []
        for label, distance in zip(labels[0], distances[0]):
            chunk_id = self._label_to_chunk.get(int(label))
            if chunk_id is None or (allowed_ids is not None and chunk_id not in allowed_ids):
                continue
            score = 1.0 - float(distance) if self.space == "cosine" else -float(distance)
            result.append((chunk_id, round(score, 8)))
        result.sort(key=lambda item: (-item[1], item[0]))
        return result[: int(top_k)]

    def _resize_for(self, index: Any, additions: int) -> None:
        if additions <= 0:
            return
        required = int(index.get_current_count()) + additions
        capacity = self._capacity_for(index)
        if required <= capacity:
            return
        new_capacity = max(required, int(math.ceil(max(1, capacity) * self.resize_factor)))
        try:
            index.resize_index(new_capacity)
        except Exception as exc:
            raise VectorIndexError("RAG_HNSW_CAPACITY_EXCEEDED") from exc
        self._capacity = new_capacity

    def _capacity_for(self, index: Any) -> int:
        try:
            return int(index.get_max_elements())
        except Exception:
            return self._capacity

    def _initial_capacity(self, item_count: int) -> int:
        configured = self.configured_max_elements or max(1024, item_count)
        return max(1, configured, item_count)

    def _set_state(self, index: Any, labels: dict[str, int], reverse: dict[int, str], checksums: dict[str, str], retired: set[int], version: str, provider_key: str, dimension: int, capacity: int) -> None:
        self._index = index
        self._labels = dict(labels)
        self._label_to_chunk = dict(reverse)
        self._vector_checksums = dict(checksums)
        self._retired_labels = set(retired)
        self.version = version
        self._provider_key = provider_key
        self._dimension = dimension
        self._capacity = capacity
        self._closed = False

    def _validate_vectors(self, vectors: dict[str, list[float]], dimension: int) -> None:
        if dimension < 1:
            raise VectorIndexError("RAG_HNSW_INVALID_DIMENSION")
        for vector in vectors.values():
            if len(vector) != dimension or any(not math.isfinite(float(value)) for value in vector):
                raise VectorIndexError("RAG_HNSW_DIMENSION_MISMATCH")


class UnavailableVectorBackend(VectorBackend):
    """Safe dense-disabled backend used when the configured backend is missing."""

    backend_name = "unavailable"
    backend = backend_name
    backend_version = "none"

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        self._dimension = 0
        self.version = None

    @property
    def dimension(self) -> int: return self._dimension

    @property
    def provider_key(self) -> str | None: return None

    @property
    def index_version(self) -> str | None: return self.version

    @property
    def item_count(self) -> int: return 0

    @property
    def capacity(self) -> int: return 0

    @property
    def deleted_count(self) -> int: return 0

    def build(self, *args, **kwargs) -> Path:
        raise VectorIndexError(self.error_code)

    def load(self, *args, **kwargs) -> None:
        raise VectorIndexError(self.error_code)

    def search(self, *args, **kwargs) -> list[tuple[str, float]]:
        return []

    def add(self, *args, **kwargs) -> None: raise VectorIndexError(self.error_code)

    def update(self, *args, **kwargs) -> None: raise VectorIndexError(self.error_code)

    def delete(self, *args, **kwargs) -> None: return None

    def persist(self) -> Path | None: return None

    def validate(self) -> dict[str, Any]: raise VectorIndexError(self.error_code)

    def close(self) -> None: return None

    def health(self) -> dict[str, Any]:
        return {"available": False, "backend": self.backend, "backend_version": self.backend_version, "dimension": 0, "provider_key": None, "index_version": None, "item_count": 0, "capacity": 0, "deleted_count": 0, "supports_incremental_update": False, "supports_delete": False, "error_code": self.error_code}


def build_vector_backend(config: Any, index_path: str | Path) -> VectorBackend:
    backend = str(getattr(config, "vector_backend", "python_cosine_fallback") or "python_cosine_fallback").lower()
    if backend == "hnsw":
        return HNSWVectorBackend(index_path, space=config.hnsw_space, m=config.hnsw_m, ef_construction=config.hnsw_ef_construction, ef_search=config.hnsw_ef_search, max_elements=config.hnsw_max_elements, resize_factor=config.hnsw_resize_factor)
    if backend in {"python_cosine", "python_cosine_fallback"}:
        return PersistentVectorIndex(index_path)
    raise VectorIndexError("RAG_VECTOR_BACKEND_INVALID")


def _allocate_label(chunk_id: str, reverse: dict[int, str], retired: set[int]) -> int:
    candidate = _label_seed(chunk_id)
    candidate = candidate or 1
    start = candidate
    while candidate in reverse or candidate in retired:
        candidate = candidate + 1 if candidate < 0x7FFFFFFF else 1
        if candidate == start:
            raise VectorIndexError("RAG_HNSW_LABEL_COLLISION")
    return candidate


def _label_seed(chunk_id: str) -> int:
    """Return a deterministic positive int32-compatible label seed."""
    candidate = int(sha256(chunk_id.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
    return candidate or 1


def _vector_checksum(vector: list[float]) -> str:
    return sha256(json.dumps([float(value) for value in vector], separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_version(version: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in version)[:100]
