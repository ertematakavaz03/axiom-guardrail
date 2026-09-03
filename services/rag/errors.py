from apps.api.app.errors import AgentArenaError


class EmbeddingError(AgentArenaError):
    reason_code = "EMBEDDING_FAILURE"


class RetrievalError(AgentArenaError):
    reason_code = "RETRIEVAL_FAILURE"


class QdrantUnavailableError(RetrievalError):
    reason_code = "QDRANT_UNAVAILABLE"


class QdrantTimeoutError(RetrievalError):
    reason_code = "QDRANT_TIMEOUT"


class RerankerError(AgentArenaError):
    reason_code = "RERANKER_FAILURE"


class DocumentParsingError(AgentArenaError):
    reason_code = "DOCUMENT_PARSING_ERROR"


class DocumentIngestionError(AgentArenaError):
    reason_code = "DOCUMENT_INGESTION_ERROR"
