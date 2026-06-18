"""Idempotent demo-data seeding for bot-meinchat-llm.

Two seed steps, both idempotent and both through the proper service / import
path (never raw SQL — [[feedback_no_direct_db_for_test_data]]):

  1. Writes a tiny demo sales-corpus markdown file into the configured
     ``rag_dir`` (only if it is absent) and runs a hash-incremental re-index
     through ``RagIngestService`` — so re-running this seeder is a no-op (the
     unchanged file hash is skipped).
  2. Reproduces the public ``/consultant`` CMS page from the SHIPPED
     data-exchange envelopes (``docs/import/cms/layouts/consultant-layout.json``
     + ``docs/import/cms/posts/consultant-page.json``), imported through the
     registered ``cms_layouts`` / ``cms_posts`` exchangers in upsert mode. The
     page hosts the meinchat ``meinchat-bot-widget`` (seeded by the meinchat
     plugin) in its ``chat`` area. cms is a SOFT dependency — if its exchangers
     are not registered the page seed is skipped cleanly.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEMO_CORPUS_FILENAME = "demo-sales-overview.md"
DEMO_CORPUS_BODY = """# Our plans and what they include

We offer tariff plans for small teams and growing businesses, optional add-ons,
shop products, and bookable resources. Pick the plan that fits your team size,
then layer add-ons as you grow. Ask the consultant for a recommendation and a
discount on checkout.
"""

# The shipped envelopes are the SINGLE SOURCE of the consultant layout + page
# (DRY) — no hardcoded layout/page dict lives here. The hosted widget
# (``meinchat-bot-widget``) is seeded by the meinchat plugin; this page only
# references it by portable slug, so the assignment is silently skipped if the
# widget record is absent (safe degrade).
_LAYOUT_JSON_RELATIVE_PATH = "docs/import/cms/layouts/consultant-layout.json"
_POSTS_JSON_RELATIVE_PATH = "docs/import/cms/posts/consultant-page.json"
_CMS_LAYOUTS_ENTITY_KEY = "cms_layouts"
_CMS_POSTS_ENTITY_KEY = "cms_posts"


def populate(app) -> None:
    """Seed the demo corpus + the consultant page (both idempotent)."""
    with app.app_context():
        plugin = _resolve_plugin(app)
        if plugin is None:
            logger.info("[bot-meinchat-llm] plugin not registered — skipping demo seed")
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

        # Reproduce the /consultant page from the shipped envelopes (after the
        # corpus is indexed so the consultant can answer once the page is live).
        _seed_consultant_page()


def _resolve_plugin(app):
    manager = getattr(app, "plugin_manager", None)
    if manager is None:
        return None
    return manager.get_plugin("bot-meinchat-llm")


def _seed_consultant_page() -> None:
    """Idempotently reproduce the public ``/consultant`` CMS page by importing
    the shipped layout + page envelopes through the unified ``cms_layouts`` /
    ``cms_posts`` data-exchange mechanism (the JSON is the single source of
    truth — same path as ``flask data-exchange import cms_posts <file>``).

    cms is a SOFT dependency: if the ``cms_layouts`` / ``cms_posts`` exchangers
    are not registered (cms absent/disabled) or the cms import code is
    unavailable, skip cleanly — bot-meinchat-llm only soft-depends on cms for
    this convenience page (mirrors meinchat's ``_seed_demo_widget`` skip logic).
    """
    from vbwd.extensions import db
    from vbwd.services.data_exchange.registry import data_exchange_registry

    layout_exchanger = data_exchange_registry.get(_CMS_LAYOUTS_ENTITY_KEY)
    posts_exchanger = data_exchange_registry.get(_CMS_POSTS_ENTITY_KEY)
    if layout_exchanger is None or posts_exchanger is None:
        logger.info("[bot-meinchat-llm] cms not present — skipping consultant page")
        return

    try:
        from vbwd.services.data_exchange.envelope import validate_envelope
        from vbwd.services.data_exchange.port import MODE_UPSERT
    except ImportError:
        logger.info("[bot-meinchat-llm] cms not present — skipping consultant page")
        return

    # Import the layout FIRST so the page's ``layout_slug`` resolves, then the
    # page (which also carries the per-page widget assignment).
    layout_result = _import_envelope(
        layout_exchanger,
        _LAYOUT_JSON_RELATIVE_PATH,
        _CMS_LAYOUTS_ENTITY_KEY,
        validate_envelope,
        MODE_UPSERT,
    )
    if layout_result is None:
        return
    posts_result = _import_envelope(
        posts_exchanger,
        _POSTS_JSON_RELATIVE_PATH,
        _CMS_POSTS_ENTITY_KEY,
        validate_envelope,
        MODE_UPSERT,
    )
    if posts_result is None:
        return

    db.session.commit()
    logger.info(
        "[bot-meinchat-llm] seeded consultant page via cms import "
        "(layout created=%s/updated=%s, page created=%s/updated=%s)",
        layout_result.created,
        layout_result.updated,
        posts_result.created,
        posts_result.updated,
    )


def _import_envelope(
    exchanger,
    relative_path: str,
    entity_key: str,
    validate_envelope,
    mode_upsert: str,
):
    """Load + validate a shipped envelope and upsert it through ``exchanger``.

    Returns the ``ImportResult`` on success, or ``None`` when the cms import
    code path is unavailable mid-flight (soft degrade)."""
    envelope_path = Path(__file__).resolve().parent / relative_path
    with open(envelope_path, encoding="utf-8") as handle:
        payload = json.load(handle)

    # Fail fast on a malformed envelope (same guard the CLI applies).
    validate_envelope(payload, entity_key)

    try:
        return exchanger.import_(payload, mode=mode_upsert, dry_run=False)
    except ImportError:
        logger.info("[bot-meinchat-llm] cms not present — skipping consultant page")
        return None
