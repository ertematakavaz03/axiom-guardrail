from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

import httpx

from apps.api.app.config import Settings
from services.rag.errors import EmbeddingError


class EmbeddingProvider(Protocol):
    model_name: str
    vector_size: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class DeterministicEmbeddingProvider:
    """Stable feature-hash embeddings for local demos and CI."""

    def __init__(self, vector_size: int = 64, model_name: str = "deterministic-hash-v1") -> None:
        if vector_size < 8:
            raise ValueError("vector_size must be at least 8")
        self.vector_size = vector_size
        self.model_name = model_name

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.vector_size
        tokens = re.findall(r"[\w-]+", text.casefold())
        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.vector_size
            sign = 1.0 if value & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(item * item for item in vector))
        return [item / norm for item in vector] if norm else vector


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        vector_size: int,
        timeout_seconds: float = 30,
    ) -> None:
        if not api_key:
            raise EmbeddingError("OpenAI-compatible embedding API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.vector_size = vector_size
        self.timeout_seconds = timeout_seconds

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self.model_name, "input": texts}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            vectors = [item["embedding"] for item in sorted(body["data"], key=lambda row: row["index"])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding provider request failed") from exc
        if len(vectors) != len(texts) or any(len(vector) != self.vector_size for vector in vectors):
            raise EmbeddingError("Embedding provider returned an invalid vector shape")
        return [[float(value) for value in vector] for vector in vectors]

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]


def embedding_provider_for(
    settings: Settings, provider_name: str | None = None, model_name: str | None = None
) -> EmbeddingProvider:
    provider = provider_name or settings.embedding_provider
    model = model_name or settings.embedding_model
    if provider == "deterministic":
        return DeterministicEmbeddingProvider(settings.embedding_vector_size, model)
    if provider in {"openai", "openai_compatible"}:
        api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        return OpenAICompatibleEmbeddingProvider(
            api_key=api_key,
            base_url=settings.openai_base_url,
            model_name=model,
            vector_size=settings.embedding_vector_size,
        )
    raise EmbeddingError("Unsupported embedding provider", details={"provider": provider})
