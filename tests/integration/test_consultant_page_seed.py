"""Integration: the bot-meinchat-llm seeder reproduces the /consultant page.

``_seed_consultant_page`` no longer relies on raw INSERTs — it loads the shipped
``docs/import/cms/layouts/consultant-layout.json`` +
``docs/import/cms/posts/consultant-page.json`` envelopes, validates them, and
imports them through the registered ``cms_layouts`` / ``cms_posts``
data-exchange exchangers in ``mode="upsert"`` (idempotent by slug). This proves
the live path against a real Postgres: cms present + its exchangers registered +
the ``meinchat-bot-widget`` record seeded → one published ``consultant`` page
whose ``chat`` area hosts the ``meinchat-bot-widget`` per-page assignment, and a
second run upserts (no duplicate, no error).

cms (+ its post-type registry) and the meinchat widget record are SOFT
dependencies of this convenience page. This suite imports the cms models /
exchangers, registers them explicitly (the same wiring ``CmsPlugin.on_enable``
does) and seeds the widget through the shared ``cms_widgets`` exchanger from the
meinchat plugin's shipped envelope — so it skips cleanly (importorskip) when the
peer packages are absent in a bare per-plugin CI clone
([[project_ci_plugin_isolation_and_vbwd_sdk_plugin_set]]). Data is seeded
through the exchanger layer only (no raw SQL — feedback_no_direct_db_for_test_data).

Engineering requirements (binding, restated): TDD-first; DevOps-first (cold
local + CI); SOLID/DI/DRY (one JSON source per entity, shared exchangers);
Liskov (skip path never breaks callers); no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin bot_meinchat_llm --full``.
"""
import json
from pathlib import Path

import pytest

from vbwd.services.data_exchange.registry import data_exchange_registry

from plugins.bot_meinchat_llm import populate_db

# Peer packages that this convenience page needs. Absent in a bare per-plugin
# clone → skip the whole module (the seed itself soft-degrades there too).
cms_layout_module = pytest.importorskip("plugins.cms.src.models.cms_layout")
cms_post_module = pytest.importorskip("plugins.cms.src.models.cms_post")
cms_widget_module = pytest.importorskip("plugins.cms.src.models.cms_widget")
pytest.importorskip("plugins.cms.src.services.data_exchange.cms_exchangers")

CmsLayout = cms_layout_module.CmsLayout
CmsPost = cms_post_module.CmsPost
CmsWidget = cms_widget_module.CmsWidget

_PAGE_SLUG = "consultant"
_LAYOUT_SLUG = "consultant-layout"
_WIDGET_SLUG = "meinchat-bot-widget"

# This suite creates + truncates the cms tables on its own committed engine
# connection, so it must run WITHOUT the autouse rolled-back-session isolation
# (which swaps ``db.engine`` for a Connection). See conftest ``no_db_isolation``.
pytestmark = pytest.mark.no_db_isolation

# The cms tables the consultant layout + page import touches.
_CMS_MODELS = (
    "cms_widget",
    "cms_layout",
    "cms_post",
    "cms_post_widget",
)


def _meinchat_widget_envelope_path() -> Path:
    """The meinchat plugin's shipped widget envelope (the widget RECORD source).

    The page references the widget only by slug, so the assignment resolves only
    when the widget record exists. We seed it from meinchat's single-source
    envelope (DRY) rather than re-hardcoding a widget dict here."""
    import plugins.meinchat as meinchat_pkg

    return (
        Path(meinchat_pkg.__file__).resolve().parent
        / "docs/import/cms/widgets/meinchat-bot-widget.json"
    )


@pytest.fixture
def db(app):
    """Function-scoped DB with the cms tables created, the cms post types +
    exchangers registered, and the ``meinchat-bot-widget`` record seeded —
    mirroring ``CmsPlugin.on_enable`` so the seeder's unified-import path is live.

    The session-scoped ``app`` fixture already ran ``create_all()`` for core +
    plugin models, so we create ONLY the cms tables here (``checkfirst`` makes a
    repeat run a no-op) and truncate them per test for isolation."""
    from sqlalchemy import text

    from vbwd.extensions import db as _db
    from vbwd.interfaces.file_storage import InMemoryFileStorage

    meinchat_widgets_envelope = (
        pytest.importorskip("plugins.meinchat") and _meinchat_widget_envelope_path()
    )
    if not meinchat_widgets_envelope.exists():
        pytest.skip("meinchat-bot-widget envelope absent")

    from plugins.cms.src.services.data_exchange.cms_exchangers import (
        register_cms_exchangers,
    )
    from plugins.cms.src.services.post_type_registry import (
        PostType,
        is_registered,
        register_post_type,
    )

    # cms_post FKs onto cms_style, so create the style table too (the page
    # carries no style, but the FK constraint must resolve at table-create).
    from plugins.cms.src.models.cms_post_widget import CmsPostWidget
    from plugins.cms.src.models.cms_style import CmsStyle

    cms_tables = [
        model.__table__
        for model in (CmsWidget, CmsLayout, CmsStyle, CmsPost, CmsPostWidget)
    ]

    with app.app_context():
        # Ensure the cms tables exist (no-op when cms tests already created
        # them), then start from empty on their own committed connection.
        # ``create_all`` with an explicit table subset resolves FK ordering and
        # is a no-op for already-present tables (checkfirst); creating only the
        # page-import tables avoids clashing on shared enums.
        with _db.engine.begin() as connection:
            _db.metadata.create_all(bind=connection, tables=cms_tables, checkfirst=True)
            for table_name in reversed(_CMS_MODELS):
                connection.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))

        # The post upsert validates ``type`` against the cms post-type registry
        # (cms registers ``page``/``post`` on enable); register them if absent.
        for key, label in (("page", "Page"), ("post", "Post")):
            if not is_registered(key):
                register_post_type(PostType(key=key, label=label, routable=True))

        register_cms_exchangers(_db.session, file_storage=InMemoryFileStorage())

        # Seed the widget RECORD (the page references it by slug) through the
        # shared cms_widgets exchanger from meinchat's single-source envelope.
        _seed_widget_record(_db, meinchat_widgets_envelope)
        try:
            yield _db
        finally:
            for entity_key in (
                "cms_posts",
                "cms_terms",
                "cms_layouts",
                "cms_styles",
                "cms_widgets",
                "cms_images",
            ):
                data_exchange_registry.unregister(entity_key)
            _db.session.remove()
            with _db.engine.begin() as connection:
                for table_name in reversed(_CMS_MODELS):
                    connection.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))


def _seed_widget_record(_db, envelope_path: Path) -> None:
    from vbwd.services.data_exchange.port import MODE_UPSERT

    with open(envelope_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    exchanger = data_exchange_registry.get("cms_widgets")
    exchanger.import_(payload, mode=MODE_UPSERT, dry_run=False)
    _db.session.commit()


def _consultant_pages(db):
    return db.session.query(CmsPost).filter_by(slug=_PAGE_SLUG).all()


def test_seed_creates_published_consultant_page_hosting_widget(db):
    populate_db._seed_consultant_page()

    pages = _consultant_pages(db)
    assert len(pages) == 1
    page = pages[0]
    assert page.type == "page"
    assert page.status == "published"
    assert page.title == "Talk to our consultant"

    layout = db.session.query(CmsLayout).filter_by(slug=_LAYOUT_SLUG).one()
    assert page.layout_id == layout.id

    from plugins.cms.src.models.cms_post_widget import CmsPostWidget

    widget = db.session.query(CmsWidget).filter_by(slug=_WIDGET_SLUG).one()
    assignments = db.session.query(CmsPostWidget).filter_by(post_id=page.id).all()
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.area_name == "chat"
    assert assignment.widget_id == widget.id


def test_seed_is_idempotent_upsert(db):
    populate_db._seed_consultant_page()
    populate_db._seed_consultant_page()

    assert len(_consultant_pages(db)) == 1
    assert db.session.query(CmsLayout).filter_by(slug=_LAYOUT_SLUG).count() == 1
