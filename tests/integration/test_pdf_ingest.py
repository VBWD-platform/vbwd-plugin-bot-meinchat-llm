"""S98.1 — pdf corpus ingestion via pypdf (real PostgreSQL).

Copies a checked-in PDF fixture into a temp corpus dir and ingests it, asserting
chunks are produced from the extracted text. Skips when pypdf is not installed —
the plugin's requirements.txt declares it; the ingest service soft-imports it so
its absence only skips .pdf files (verified separately in the unit suite).
"""
import os
import shutil

import pytest

pytest.importorskip("pypdf")

from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E402,E501
    RagChunkRepository,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.rag_ingest_service import (  # noqa: E402,E501
    RagIngestService,
)

_FIXTURE_PDF = os.path.join(os.path.dirname(__file__), "..", "fixtures", "plans.pdf")


@pytest.mark.integration
def test_pdf_is_ingested_into_chunks(app, tmp_path):
    from vbwd.extensions import db

    shutil.copy(_FIXTURE_PDF, str(tmp_path / "plans.pdf"))
    repository = RagChunkRepository(db.session)
    service = RagIngestService(
        repository, str(tmp_path), chunk_size_tokens=120, overlap_tokens=10
    )

    result = service.ingest()
    db.session.flush()

    assert result.files_seen == 1
    assert result.files_indexed == 1
    assert result.chunks_written >= 1
    # The extracted PDF text is searchable via the same FTS path.
    hits = repository.search("tariff plans", top_k=3)
    assert hits, "expected the pdf-derived chunk to be FTS-retrievable"
