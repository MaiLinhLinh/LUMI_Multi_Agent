"""Dense music embeddings provided by a local Ollama BGE-M3 service."""

from __future__ import annotations

import math
from typing import Any, Sequence

import httpx


class MusicEmbeddingError(RuntimeError):
    """Raised when Ollama cannot return a trustworthy embedding batch."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class OllamaMusicEmbeddingService:
    """Call Ollama's batch embed endpoint and validate every returned vector."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "bge-m3",
        dimensions: int = 1024,
        timeout_seconds: float = 120,
        client: Any | None = None,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("Embedding dimensions must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def embed_documents(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 16,
    ) -> list[list[float]]:
        """Embed non-empty texts in bounded batches using ``POST /api/embed``."""

        if batch_size <= 0:
            raise ValueError("Embedding batch size must be positive")
        clean_texts = [str(text).strip() for text in texts]
        if any(not text for text in clean_texts):
            raise ValueError("Embedding inputs must not be empty")
        if not clean_texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(clean_texts), batch_size):
            batch = clean_texts[start : start + batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OllamaMusicEmbeddingService":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": texts,
                    "truncate": False,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise MusicEmbeddingError(
                "ollama_timeout",
                "Ollama embedding request timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = _response_detail(exc.response)
            raise MusicEmbeddingError(
                "ollama_http_error",
                f"Ollama returned HTTP {exc.response.status_code}: {detail}",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise MusicEmbeddingError(
                "ollama_unavailable",
                f"Could not connect to Ollama at {self.base_url}: {exc}",
                retryable=True,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MusicEmbeddingError(
                "invalid_ollama_response",
                "Ollama embedding response was not valid JSON.",
                retryable=False,
            ) from exc

        raw_vectors = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(texts):
            raise MusicEmbeddingError(
                "invalid_embedding_count",
                "Ollama returned a different number of embeddings than inputs.",
                retryable=False,
            )
        return [self._validate_vector(vector) for vector in raw_vectors]

    def _validate_vector(self, vector: Any) -> list[float]:
        if not isinstance(vector, list) or len(vector) != self.dimensions:
            observed = len(vector) if isinstance(vector, list) else "non-list"
            raise MusicEmbeddingError(
                "invalid_embedding_dimensions",
                (
                    f"Expected {self.dimensions} embedding dimensions from "
                    f"{self.model}, received {observed}."
                ),
                retryable=False,
            )
        normalized: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MusicEmbeddingError(
                    "invalid_embedding_value",
                    "Embedding contains a non-numeric value.",
                    retryable=False,
                )
            number = float(value)
            if not math.isfinite(number):
                raise MusicEmbeddingError(
                    "invalid_embedding_value",
                    "Embedding contains NaN or infinity.",
                    retryable=False,
                )
            normalized.append(number)
        return normalized


def _response_detail(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(getattr(response, "text", "")).strip()[:300] or "unknown error"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()[:300]
    return "unknown error"
