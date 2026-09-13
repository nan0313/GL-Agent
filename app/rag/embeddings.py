"""Optional local embedding providers with explicit real/fixture status."""

from abc import ABC, abstractmethod
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

from app.rag.config import RAGConfig
from app.rag.query import tokenize_query


class EmbeddingProviderUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EmbeddingProvider(ABC):
    provider_type = "unknown"
    model_name = "unknown"
    model_version = "unknown"
    dimension = 0
    normalize = True

    @property
    def provider_key(self) -> str:
        return f"{self.provider_type}:{self.model_name}:{self.model_version}:{self.dimension}"

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...

    def health_check(self, *, load: bool = False) -> dict[str, Any]:
        return {"available": True, "provider_type": self.provider_type, "model_name": self.model_name, "model_version": self.model_version, "dimension": self.dimension, "normalize": self.normalize}


class DisabledEmbeddingProvider(EmbeddingProvider):
    provider_type = "disabled"
    model_name = "none"
    model_version = "none"
    dimension = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingProviderUnavailable("RAG_DENSE_DISABLED")

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingProviderUnavailable("RAG_DENSE_DISABLED")

    def health_check(self, *, load: bool = False) -> dict[str, Any]:
        return {"available": False, "enabled": False, "provider_type": self.provider_type, "model_name": self.model_name, "error_code": "RAG_DENSE_DISABLED"}


class DeterministicTestEmbeddingProvider(EmbeddingProvider):
    """Hash vectors for tests only; never claim semantic model quality."""

    provider_type = "fixture"
    model_name = "deterministic-hash"
    model_version = "fixture_v1"
    dimension = 64

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def health_check(self, *, load: bool = False) -> dict[str, Any]:
        return {"available": True, "enabled": True, "provider_type": "fixture", "model_name": self.model_name, "model_version": self.model_version, "dimension": self.dimension, "semantic_status": "fixture_not_real_semantic"}

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = tokenize_query(text) or [text.strip().lower()]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset in range(0, 16, 2):
                index = int.from_bytes(digest[offset : offset + 2], "big") % self.dimension
                sign = 1.0 if digest[offset] & 1 else -1.0
                vector[index] += sign
        return _normalize(vector)


class LocalSentenceTransformerEmbeddingProvider(EmbeddingProvider):
    provider_type = "local_sentence_transformer"

    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 16) -> None:
        self.model_name = model_name
        self.model_id = Path(model_name).name if Path(model_name).exists() else model_name
        self.model_version = "unloaded"
        self.device = device
        self.batch_size = max(1, batch_size)
        self.normalize = True
        self._model = None
        self.dimension = 0
        self._error_code: str | None = None

    @property
    def provider_key(self) -> str:
        return f"{self.provider_type}:{self.model_id}:{self.model_version}:{self.dimension}"

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.model_name:
            self._error_code = "RAG_EMBEDDING_MODEL_NOT_LOCAL"
            raise EmbeddingProviderUnavailable(self._error_code)
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError:
            self._error_code = "RAG_EMBEDDING_DEPENDENCY_MISSING"
            raise EmbeddingProviderUnavailable(self._error_code)
        try:
            self._model = SentenceTransformer(self.model_name, device=self.device, local_files_only=True)
            dimension_method = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
            self.dimension = int(dimension_method())
            self.model_version = "local"
            return self._model
        except Exception as exc:
            self._error_code = "RAG_EMBEDDING_MODEL_LOAD_FAILED"
            raise EmbeddingProviderUnavailable(self._error_code) from exc

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        try:
            values = model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            return [[float(item) for item in row] for row in values]
        except Exception as exc:
            raise EmbeddingProviderUnavailable("RAG_EMBEDDING_FAILED") from exc

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def health_check(self, *, load: bool = False) -> dict[str, Any]:
        if load and self._model is None:
            try:
                self._load()
            except EmbeddingProviderUnavailable:
                pass
        if self._model is None:
            probe_dimension = self._probe_dimension()
            if probe_dimension:
                return {"available": True, "enabled": True, "provider_type": self.provider_type, "model_name": self.model_id, "model_version": "local_probe", "dimension": probe_dimension, "normalize": True, "semantic_status": "real_local_available"}
            return {"available": False, "enabled": True, "provider_type": self.provider_type, "model_name": self.model_id or "configured", "model_version": self.model_version, "dimension": self.dimension, "error_code": self._error_code or "RAG_EMBEDDING_NOT_LOADED"}
        return {"available": True, "enabled": True, "provider_type": self.provider_type, "model_name": self.model_id, "model_version": self.model_version, "dimension": self.dimension, "normalize": True, "semantic_status": "real_local"}

    def _probe_dimension(self) -> int:
        """Read local model metadata without loading weights or using network."""
        candidates: list[Path] = []
        configured = Path(self.model_name) if self.model_name else None
        if configured is not None and configured.is_dir():
            candidates.append(configured)
        if not candidates and self.model_name:
            try:
                from huggingface_hub import try_to_load_from_cache  # type: ignore

                config_path = try_to_load_from_cache(self.model_name, "config.json")
                weights_path = try_to_load_from_cache(self.model_name, "model.safetensors") or try_to_load_from_cache(self.model_name, "pytorch_model.bin")
                if config_path and weights_path:
                    candidates.append(Path(config_path).parent)
            except Exception:
                return 0
        for directory in candidates:
            config_path = directory / "config.json"
            if not config_path.is_file() or not ((directory / "model.safetensors").is_file() or (directory / "pytorch_model.bin").is_file()):
                continue
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                dimension = int(config.get("hidden_size") or config.get("projection_dim") or 0)
                if dimension > 0:
                    return dimension
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return 0


def build_embedding_provider(config: RAGConfig) -> EmbeddingProvider:
    if not config.dense_enabled or config.embedding_provider in {"", "disabled", "none"}:
        return DisabledEmbeddingProvider()
    if config.embedding_provider == "fixture":
        return DeterministicTestEmbeddingProvider()
    if config.embedding_provider in {"sentence_transformers", "local_sentence_transformer"}:
        return LocalSentenceTransformerEmbeddingProvider(config.embedding_model, config.embedding_device, config.embedding_batch_size)
    return DisabledEmbeddingProvider()


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector
