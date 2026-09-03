from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from services.rag.models import SparseRepresentation

TOKEN_PATTERN = re.compile(r"[\w-]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.casefold())


def sparse_vector(text: str) -> SparseRepresentation:
    counts = Counter(tokenize(text))
    weighted: dict[int, float] = {}
    for token, count in counts.items():
        index = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
        weighted[index] = weighted.get(index, 0.0) + 1.0 + math.log(count)
    indices = sorted(weighted)
    return SparseRepresentation(indices=indices, values=[weighted[index] for index in indices])


def lexical_overlap(query: str, content: str) -> float:
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    content_tokens = set(tokenize(content))
    return len(query_tokens & content_tokens) / len(query_tokens)
