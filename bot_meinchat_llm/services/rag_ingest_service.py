"""RagIngestService — read the sales corpus and index it (S98.1).

Walks ``rag_dir`` for ``*.md`` (read as UTF-8 text) and ``*.pdf`` (text via
``pypdf``), chunks each file, and upserts rows into ``bot_llm_rag_chunk``.

Ingest is **hash-incremental**: each file's SHA-256 is compared against the
stored ``file_hash`` and an unchanged file is skipped without re-chunking. A
changed file's old chunks are deleted and replaced.

Degrades gracefully: a missing/empty ``rag_dir`` indexes nothing and reports
zero counts (never raises); ``pypdf`` being absent skips ``.pdf`` files with a
warning so the plugin still enables and the gate stays green where the optional
dependency is not installed.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import List

from plugins.bot_meinchat_llm.bot_meinchat_llm.models.rag_chunk import RagChunk
from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
    RagChunkRepository,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.text_chunker import (
    DEFAULT_CHUNK_SIZE_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    chunk_text,
)

logger = logging.getLogger(__name__)

MARKDOWN_EXTENSIONS = (".md", ".markdown")
PDF_EXTENSION = ".pdf"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an ingest run: how many files seen / (re)indexed / skipped."""

    files_seen: int
    files_indexed: int
    files_skipped: int
    chunks_written: int


class RagIngestService:
    """Index the static sales corpus into ``bot_llm_rag_chunk``."""

    def __init__(
        self,
        repository: RagChunkRepository,
        rag_dir: str,
        *,
        chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        self._repository = repository
        self._rag_dir = rag_dir
        self._chunk_size_tokens = chunk_size_tokens
        self._overlap_tokens = overlap_tokens

    def ingest(self) -> IngestResult:
        """Re-index the corpus, skipping files whose hash is unchanged."""
        corpus_files = self._list_corpus_files()
        files_indexed = 0
        files_skipped = 0
        chunks_written = 0

        for file_path in corpus_files:
            file_hash = self._hash_file(file_path)
            if self._repository.get_hash_for_file(file_path) == file_hash:
                files_skipped += 1
                continue

            text = self._extract_text(file_path)
            if text is None:
                # Unsupported / unreadable file (e.g. pdf with pypdf absent).
                files_skipped += 1
                continue

            self._repository.delete_for_file(file_path)
            chunks = self._build_chunks(file_path, text, file_hash)
            self._repository.add_chunks(chunks)
            files_indexed += 1
            chunks_written += len(chunks)

        return IngestResult(
            files_seen=len(corpus_files),
            files_indexed=files_indexed,
            files_skipped=files_skipped,
            chunks_written=chunks_written,
        )

    def _list_corpus_files(self) -> List[str]:
        """Sorted corpus file paths under ``rag_dir``; empty if it is missing."""
        if not self._rag_dir or not os.path.isdir(self._rag_dir):
            logger.info(
                "[bot-meinchat-llm] corpus dir '%s' missing — no corpus indexed",
                self._rag_dir,
            )
            return []
        found: List[str] = []
        for root, _dirs, names in os.walk(self._rag_dir):
            for name in sorted(names):
                lowered = name.lower()
                if lowered.endswith(MARKDOWN_EXTENSIONS) or lowered.endswith(
                    PDF_EXTENSION
                ):
                    found.append(os.path.join(root, name))
        return sorted(found)

    def _build_chunks(
        self, source_file: str, text: str, file_hash: str
    ) -> List[RagChunk]:
        pieces = chunk_text(
            text,
            chunk_size_tokens=self._chunk_size_tokens,
            overlap_tokens=self._overlap_tokens,
        )
        return [
            RagChunk(
                source_file=source_file,
                chunk_index=index,
                content=piece,
                file_hash=file_hash,
            )
            for index, piece in enumerate(pieces)
        ]

    @staticmethod
    def _hash_file(file_path: str) -> str:
        digest = hashlib.sha256()
        with open(file_path, "rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    def _extract_text(self, file_path: str) -> str | None:
        """Plain text of a corpus file, or ``None`` if it cannot be read.

        ``.md`` is read as UTF-8; ``.pdf`` goes through ``pypdf`` (a soft import
        — a missing dependency logs a warning and skips the file rather than
        crashing ingest).
        """
        lowered = file_path.lower()
        if lowered.endswith(MARKDOWN_EXTENSIONS):
            return self._read_markdown(file_path)
        if lowered.endswith(PDF_EXTENSION):
            return self._read_pdf(file_path)
        return None

    @staticmethod
    def _read_markdown(file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    @staticmethod
    def _read_pdf(file_path: str) -> str | None:
        try:
            from pypdf import PdfReader
        except ImportError:
            logger.warning(
                "[bot-meinchat-llm] pypdf not installed — skipping pdf '%s' "
                "(add pypdf to enable pdf corpus files)",
                file_path,
            )
            return None
        reader = PdfReader(file_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
