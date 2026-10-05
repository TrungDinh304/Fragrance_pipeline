"""Tầng embedding: cổng + adapter. Xem `ports.py` cho hợp đồng."""

from .document import document, documents
from .hashing import HashingEmbedder
from .ports import (Embedder, Embedding, EmbeddingSet, EmbeddingUnavailable,
                    ModelMismatch)

__all__ = ["Embedder", "Embedding", "EmbeddingSet", "EmbeddingUnavailable",
           "ModelMismatch", "HashingEmbedder", "document", "documents"]
