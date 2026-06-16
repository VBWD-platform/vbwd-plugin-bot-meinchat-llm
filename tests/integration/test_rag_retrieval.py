"""S98.1 — RAG ingest + FTS retrieval against real PostgreSQL.

Ingests a fixture corpus into ``bot_llm_rag_chunk`` and asserts the generated
``content_tsv`` ranks a topical chunk above an unrelated one. Also proves
hash-incremental skip and the empty-corpus / empty-query degradation.
"""
import pytest

from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
    RagChunkRepository,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.rag_ingest_service import (
    RagIngestService,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.retrieval_service import (
    RetrievalService,
)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _ingest(tmp_path):
    from vbwd.extensions import db

    _write(
        str(tmp_path / "plans.md"),
        "Our tariff plans suit small teams and growing businesses. "
        "Choose a monthly plan and add seats as you grow.",
    )
    _write(
        str(tmp_path / "shipping.md"),
        "Parcels are dispatched within two business days via a courier. "
        "Track your delivery from the order page.",
    )
    repository = RagChunkRepository(db.session)
    service = RagIngestService(
        repository, str(tmp_path), chunk_size_tokens=200, overlap_tokens=10
    )
    result = service.ingest()
    db.session.flush()
    return repository, result


@pytest.mark.integration
def test_ingest_then_retrieve_ranks_topical_chunk_first(app, tmp_path):
    repository, result = _ingest(tmp_path)
    assert result.files_indexed == 2
    assert result.chunks_written >= 2

    retrieval = RetrievalService(repository)
    hits = retrieval.retrieve("tariff plans for a team", top_k=2)
    assert hits, "expected at least one FTS hit"
    # The plan doc must rank above the shipping doc.
    assert "plans.md" in hits[0].source_file


@pytest.mark.integration
def test_reingest_unchanged_is_idempotent(app, tmp_path):
    repository, first = _ingest(tmp_path)
    total_after_first = repository.count()

    service = RagIngestService(repository, str(tmp_path), chunk_size_tokens=200)
    second = service.ingest()
    from vbwd.extensions import db

    db.session.flush()

    assert second.files_indexed == 0
    assert second.files_skipped == 2
    assert repository.count() == total_after_first


@pytest.mark.integration
def test_empty_corpus_and_blank_query_degrade(app, tmp_path):
    from vbwd.extensions import db

    repository = RagChunkRepository(db.session)
    # Empty dir → no corpus, retrieval returns nothing (no crash).
    service = RagIngestService(repository, str(tmp_path))
    result = service.ingest()
    assert result.files_seen == 0

    retrieval = RetrievalService(repository)
    assert retrieval.retrieve("anything", top_k=3) == []
    assert retrieval.retrieve("   ", top_k=3) == []
