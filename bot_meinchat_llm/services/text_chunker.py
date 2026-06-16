"""Whitespace-token chunker for the RAG corpus (S98.1).

A small, dependency-free splitter: it breaks a document's text into overlapping
chunks of roughly ``chunk_size`` whitespace tokens with ``overlap`` tokens
carried into the next chunk. "Token" here means a whitespace-delimited word — a
deliberately simple proxy for LLM tokens (NO OVERENGINEERING: the corpus is
small and retrieval is lexical, so an exact tokenizer buys nothing).
"""
from typing import List

# Defaults sit inside the sprint's ~600-900 token target with a small overlap so
# a fact spanning a chunk boundary still appears whole in one chunk.
DEFAULT_CHUNK_SIZE_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 80


def chunk_text(
    text: str,
    *,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> List[str]:
    """Split ``text`` into overlapping whitespace-token chunks.

    Returns an empty list for blank input. ``overlap_tokens`` is clamped below
    ``chunk_size_tokens`` so the window always advances (no infinite loop).
    """
    if not text or not text.strip():
        return []
    if chunk_size_tokens <= 0:
        raise ValueError("chunk_size_tokens must be positive")

    safe_overlap = max(0, min(overlap_tokens, chunk_size_tokens - 1))
    step = chunk_size_tokens - safe_overlap

    words = text.split()
    chunks: List[str] = []
    start = 0
    while start < len(words):
        window = words[start : start + chunk_size_tokens]
        chunks.append(" ".join(window))
        start += step
    return chunks
