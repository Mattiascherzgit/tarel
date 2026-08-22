"""Explicit model download and lazy llama.cpp embedding adapter."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tarel.retrieval.contracts import RetrievalFailure

DEFAULT_MODEL_NAME = "qwen3-embedding-0.6b-q4-k-m"
_LLAMA_TOKEN_BATCH_SIZE = 256
_QUERY_INSTRUCTION = (
    "Instruct: Retrieve relevant DWH, BI, ERP database tables and fields for this "
    "analytics question.\nQuery: "
)


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    filename: str
    url: str
    sha256: str
    size: int
    source: str


@dataclass(frozen=True, slots=True)
class ModelDownloadResult:
    path: Path
    spec: ModelSpec
    reused: bool


MODEL_SPECS = {
    DEFAULT_MODEL_NAME: ModelSpec(
        name=DEFAULT_MODEL_NAME,
        filename="qwen3-embedding-0.6b-q4-k-m.gguf",
        url=(
            "https://huggingface.co/enacimie/"
            "Qwen3-Embedding-0.6B-Q4_K_M-GGUF/resolve/"
            "51fe2a65af23d8cfd3c9c1d89846cf9073f8902b/"
            "qwen3-embedding-0.6b-q4_k_m.gguf"
        ),
        sha256="17c3e3f2eaabc6e321702b4a13680d042e72afc5d602f359f27a670c3e54718c",
        size=396_474_560,
        source="https://huggingface.co/enacimie/Qwen3-Embedding-0.6B-Q4_K_M-GGUF",
    ),
}


def model_cache_root() -> Path:
    override = os.getenv("TAREL_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg_cache = os.getenv("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache).expanduser() / "tarel"
    return Path.home() / ".cache" / "tarel"


def default_model_path(name: str = DEFAULT_MODEL_NAME) -> Path:
    return model_cache_root() / "models" / model_spec(name).filename


def model_spec(name: str) -> ModelSpec:
    try:
        return MODEL_SPECS[name]
    except KeyError as exc:
        raise RetrievalFailure("unknown_model", f"Unknown local embedding model: {name}") from exc


def resolve_model_path(path: Path | None = None) -> Path:
    configured = path
    if configured is None:
        environment_path = os.getenv("TAREL_EMBEDDING_MODEL")
        configured = Path(environment_path) if environment_path else default_model_path()
    resolved = configured.expanduser().resolve()
    if not resolved.is_file():
        raise RetrievalFailure(
            "model_not_found",
            f"Embedding model not found: {resolved}. Run `tarel model download` or pass --model.",
        )
    if resolved.suffix.casefold() != ".gguf":
        raise RetrievalFailure("invalid_model", "Local embedding models must be GGUF files.")
    return resolved


def download_model(
    *,
    name: str = DEFAULT_MODEL_NAME,
    target: Path | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> ModelDownloadResult:
    spec = model_spec(name)
    _validate_model_source(spec.url)
    path = (target or default_model_path(name)).expanduser().resolve()
    if path.suffix.casefold() != ".gguf":
        raise RetrievalFailure("invalid_model_target", "Model target must end with .gguf.")
    if path.exists() and not force:
        if sha256_file(path) != spec.sha256:
            raise RetrievalFailure(
                "model_checksum_mismatch",
                f"Existing model has an unexpected checksum: {path}. Use --force to replace it.",
            )
        return ModelDownloadResult(path=path, spec=spec, reused=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=".model-",
        suffix=".download",
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    downloaded = 0
    try:
        request = urllib.request.Request(spec.url, headers={"User-Agent": "tarel/0.0.1"})
        # Model registry entries are immutable, checksum-pinned HTTPS URLs.
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(  # nosec B310
            request
        ) as response:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, spec.size)
        if downloaded != spec.size or digest.hexdigest() != spec.sha256:
            raise RetrievalFailure(
                "model_checksum_mismatch",
                "Downloaded model did not match the pinned size and SHA-256 checksum.",
            )
        os.replace(temporary_path, path)
    except RetrievalFailure:
        temporary_path.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as exc:
        temporary_path.unlink(missing_ok=True)
        raise RetrievalFailure(
            "model_download_failed",
            "Could not download embedding model.",
        ) from exc
    return ModelDownloadResult(path=path, spec=spec, reused=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_model_source(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RetrievalFailure(
            "invalid_model_source",
            "Registered embedding models require a credential-free HTTPS source URL.",
        )


class LlamaCppEmbedding:
    """CPU-only Qwen-compatible embeddings without LlamaIndex or a server."""

    def __init__(
        self,
        model_path: Path,
        *,
        n_ctx: int = 2048,
        n_threads: int | None = None,
    ) -> None:
        resolved = resolve_model_path(model_path)
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise RetrievalFailure(
                "missing_local_rag_dependency",
                "llama-cpp-python is not installed. Install `tarel[local-rag]`.",
            ) from exc
        threads = n_threads or max(1, (os.cpu_count() or 2) - 1)
        self._model_path = resolved
        self._model: Any = Llama(
            model_path=str(resolved),
            n_ctx=n_ctx,
            n_batch=_LLAMA_TOKEN_BATCH_SIZE,
            n_ubatch=_LLAMA_TOKEN_BATCH_SIZE,
            n_threads=threads,
            n_gpu_layers=0,
            embedding=True,
            verbose=False,
        )

    @property
    def model_id(self) -> str:
        return self._model_path.name

    def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        batch_size: int,
    ) -> tuple[tuple[float, ...], ...]:
        if not 1 <= batch_size <= 256:
            raise RetrievalFailure("invalid_batch_size", "Batch size must be between 1 and 256.")
        vectors: list[tuple[float, ...]] = []
        # llama.cpp's n_batch is token capacity, not a safe multi-sequence document count.
        # Keep caller batches for scheduling, but decode one document at a time.
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            for position, text in enumerate(batch, start=offset + 1):
                try:
                    embedded = self._model.embed(text, normalize=True, truncate=True)
                except Exception as exc:
                    raise RetrievalFailure(
                        "embedding_failed",
                        f"llama.cpp failed while embedding document {position} of {len(texts)}.",
                    ) from exc
                vectors.append(_normalized_vector(embedded))
        return tuple(vectors)

    def embed_query(self, text: str) -> tuple[float, ...]:
        embedded = self._model.embed(
            f"{_QUERY_INSTRUCTION}{text.strip()}",
            normalize=True,
            truncate=True,
        )
        return _normalized_vector(embedded)


def _normalized_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise RetrievalFailure("embedding_failed", "Embedding must be a non-empty number array.")
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise RetrievalFailure("embedding_failed", "Embedding contains a non-number.") from exc
    if not all(math.isfinite(item) for item in vector):
        raise RetrievalFailure("embedding_failed", "Embedding contains a non-finite number.")
    magnitude = math.sqrt(sum(item * item for item in vector))
    if magnitude == 0:
        raise RetrievalFailure("embedding_failed", "Embedding has zero magnitude.")
    return tuple(item / magnitude for item in vector)
