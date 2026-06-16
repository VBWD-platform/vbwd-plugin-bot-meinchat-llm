"""S98.1 — RagIngestService unit tests (fake repo, real temp corpus dir).

Exercises ingest bookkeeping (chunk counts, hash-incremental skip, missing dir)
without a database by faking the repository's hash/delete/add surface.
"""
import os

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.rag_ingest_service import (
    RagIngestService,
)


class FakeRagChunkRepository:
    """In-memory stand-in honouring the repository contract the service uses."""

    def __init__(self):
        # source_file -> (file_hash, [chunk, ...])
        self._by_file = {}

    def get_hash_for_file(self, source_file):
        entry = self._by_file.get(source_file)
        return entry[0] if entry else None

    def delete_for_file(self, source_file):
        self._by_file.pop(source_file, None)
        return 0

    def add_chunks(self, chunks):
        for chunk in chunks:
            entry = self._by_file.setdefault(chunk.source_file, [chunk.file_hash, []])
            entry[0] = chunk.file_hash
            entry[1].append(chunk)

    def total_chunks(self):
        return sum(len(entry[1]) for entry in self._by_file.values())


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def test_missing_dir_degrades_to_no_corpus():
    repository = FakeRagChunkRepository()
    service = RagIngestService(repository, "/nonexistent/corpus/dir")
    result = service.ingest()
    assert result.files_seen == 0
    assert result.files_indexed == 0
    assert result.chunks_written == 0


def test_ingest_markdown_produces_chunks(tmp_path):
    _write(str(tmp_path / "plans.md"), "tariff plans " * 50)
    repository = FakeRagChunkRepository()
    service = RagIngestService(
        repository, str(tmp_path), chunk_size_tokens=40, overlap_tokens=5
    )
    result = service.ingest()
    assert result.files_seen == 1
    assert result.files_indexed == 1
    assert result.chunks_written >= 1
    assert repository.total_chunks() == result.chunks_written


def test_reingest_unchanged_file_is_skipped(tmp_path):
    _write(str(tmp_path / "plans.md"), "tariff plans " * 50)
    repository = FakeRagChunkRepository()
    service = RagIngestService(repository, str(tmp_path), chunk_size_tokens=40)

    first = service.ingest()
    assert first.files_indexed == 1

    second = service.ingest()
    assert second.files_indexed == 0
    assert second.files_skipped == 1
    assert second.chunks_written == 0
    # The corpus index is unchanged (no duplicate chunks).
    assert repository.total_chunks() == first.chunks_written


def test_changed_file_is_reindexed(tmp_path):
    corpus_file = str(tmp_path / "plans.md")
    _write(corpus_file, "tariff plans overview")
    repository = FakeRagChunkRepository()
    service = RagIngestService(repository, str(tmp_path), chunk_size_tokens=40)
    service.ingest()

    _write(corpus_file, "completely different content about bookable resources")
    result = service.ingest()
    assert result.files_indexed == 1
    assert result.files_skipped == 0


def test_pdf_without_pypdf_is_skipped_not_crashed(tmp_path, monkeypatch):
    # Force the soft-import to fail so the service degrades rather than crashing.
    import builtins

    real_import = builtins.__import__

    def _no_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("pypdf not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pypdf)

    pdf_path = os.path.join(str(tmp_path), "guide.pdf")
    with open(pdf_path, "wb") as handle:
        handle.write(b"%PDF-1.4 not-a-real-pdf")
    repository = FakeRagChunkRepository()
    service = RagIngestService(repository, str(tmp_path))
    result = service.ingest()
    assert result.files_seen == 1
    assert result.files_indexed == 0
    assert result.files_skipped == 1
