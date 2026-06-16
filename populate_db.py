"""Idempotent demo-data seeding for bot-meinchat-llm.

Writes a tiny demo sales-corpus markdown file into the configured ``rag_dir``
(only if it is absent) and runs a hash-incremental re-index through
``RagIngestService`` — so re-running this seeder is a no-op (the unchanged file
hash is skipped). Demo data goes through the service, never raw SQL
([[feedback_no_direct_db_for_test_data]]).
"""
import logging
import os

logger = logging.getLogger(__name__)

DEMO_CORPUS_FILENAME = "demo-sales-overview.md"
DEMO_CORPUS_BODY = """# Our plans and what they include

We offer tariff plans for small teams and growing businesses, optional add-ons,
shop products, and bookable resources. Pick the plan that fits your team size,
then layer add-ons as you grow. Ask the consultant for a recommendation and a
discount on checkout.
"""


def populate(app) -> None:
    """Seed the demo corpus and re-index it (idempotent)."""
    with app.app_context():
        plugin = _resolve_plugin(app)
        if plugin is None:
            logger.info(
                "[bot-meinchat-llm] plugin not registered — skipping demo seed"
            )
            return

        rag_dir = plugin.resolved_rag_dir()
        os.makedirs(rag_dir, exist_ok=True)
        demo_path = os.path.join(rag_dir, DEMO_CORPUS_FILENAME)
        if not os.path.exists(demo_path):
            with open(demo_path, "w", encoding="utf-8") as handle:
                handle.write(DEMO_CORPUS_BODY)
            logger.info("[bot-meinchat-llm] wrote demo corpus file %s", demo_path)

        result = plugin.reindex_corpus()
        logger.info(
            "[bot-meinchat-llm] demo corpus indexed: %s files, %s chunks "
            "(%s skipped)",
            result.files_indexed,
            result.chunks_written,
            result.files_skipped,
        )


def _resolve_plugin(app):
    manager = getattr(app, "plugin_manager", None)
    if manager is None:
        return None
    return manager.get_plugin("bot-meinchat-llm")
