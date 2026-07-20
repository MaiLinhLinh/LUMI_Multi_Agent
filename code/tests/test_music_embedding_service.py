import json

import httpx
import pytest

from rag_manager.services.music_embedding_service import (
    MusicEmbeddingError,
    OllamaMusicEmbeddingService,
)


def test_ollama_embedding_service_batches_and_validates_vectors() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={"embeddings": [[float(index)] * 4 for index, _ in enumerate(payload["input"])]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = OllamaMusicEmbeddingService(
        model="bge-m3",
        dimensions=4,
        client=client,
    )

    vectors = service.embed_documents(["a", "b", "c"], batch_size=2)

    assert len(vectors) == 3
    assert all(len(vector) == 4 for vector in vectors)
    assert [request["input"] for request in requests] == [["a", "b"], ["c"]]
    assert all(request["truncate"] is False for request in requests)


def test_ollama_embedding_service_rejects_wrong_dimensions() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        )
    )
    service = OllamaMusicEmbeddingService(dimensions=4, client=client)

    with pytest.raises(MusicEmbeddingError) as exc_info:
        service.embed_documents(["music"])

    assert exc_info.value.code == "invalid_embedding_dimensions"
    assert exc_info.value.retryable is False


def test_ollama_embedding_service_reports_unavailable_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = OllamaMusicEmbeddingService(dimensions=4, client=client)

    with pytest.raises(MusicEmbeddingError) as exc_info:
        service.embed_documents(["music"])

    assert exc_info.value.code == "ollama_unavailable"
    assert exc_info.value.retryable is True
