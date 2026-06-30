"""Shared fixtures for bot-meinchat-llm tests.

Unit specs use ``MagicMock`` repos / fakes and need no DB. Integration specs
request ``app`` / ``client`` and self-bootstrap a ``<dbname>_test`` database
with all core + plugin tables created via ``ensure_schema_and_baseline``
(mirrors the bot_base / meinchat harness).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("TESTING", "true")


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, dbname = base.rpartition("/")
    dbname = dbname.split("?")[0]
    return f"{prefix}/{dbname}_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine, text

    main_url = url.rsplit("/", 1)[0] + "/postgres"
    dbname = url.rsplit("/", 1)[1].split("?")[0]
    engine = create_engine(main_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


def _import_optional_catalog_models() -> None:
    """Import peer model packages when present (so create_all builds their
    tables). Absent packages are skipped — the integration tests that need a
    peer guard on it (importorskip) or are enabled per the plugin set below.

    Includes the catalog peers (subscription/shop/booking — S98.2) and the
    reward-substrate peers (discount/meinchat/referral — S98.4) so the bot-issued
    coupon mint→redeem→credit path has its tables."""
    for module_name in (
        "plugins.subscription.subscription.models",
        "plugins.shop.shop.models",
        "plugins.booking.booking.models",
        "plugins.discount.discount.models",
        "plugins.meinchat.meinchat.models",
        "plugins.referral.referral.models",
    ):
        try:
            __import__(module_name)
        except ImportError:
            continue


def _ensure_referral_commission_enum_value(database) -> None:
    """Add ``REFERRAL_COMMISSION`` to the native ``tokentransactiontype`` enum.

    The harness builds schema via ``create_all`` (not migrations), so the core
    migration that adds this enum value never runs against the persistent
    ``<dbname>_test`` DB. Replicates that migration's idempotent
    ``ADD VALUE IF NOT EXISTS`` (autocommit — cannot run in a transaction) so the
    bot's mint→redeem→commission path can credit the bot's balance. Test-infra
    only — no core file touched."""
    from sqlalchemy import text

    engine = database.engine
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(
            text(
                "ALTER TYPE tokentransactiontype "
                "ADD VALUE IF NOT EXISTS 'REFERRAL_COMMISSION'"
            )
        )


def _ensure_plugin_enabled(flask_app) -> None:
    """Enable bot-meinchat-llm (+ the peers its tests touch) so on_enable
    registrations fire. A fresh per-plugin CI clone has no plugins.json, so the
    plugin is discovered-but-not-enabled and its DI provider never registers.
    Idempotent — skips already-enabled plugins.
    """
    from vbwd.plugins.base import PluginStatus

    manager = getattr(flask_app, "plugin_manager", None)
    if manager is None:
        return
    with flask_app.app_context():
        # discount must enable before referral (referral clones discount
        # coupons); meinchat provides the bot sender + nickname. bot-meinchat-llm
        # is enabled last so its on_enable registrations fire over a ready graph.
        for plugin_name in (
            "bot-base",
            "subscription",
            "shop",
            "booking",
            "discount",
            "meinchat",
            "referral",
            "bot-meinchat-llm",
        ):
            plugin = manager.get_plugin(plugin_name)
            if plugin is None or plugin.status == PluginStatus.ENABLED:
                continue
            try:
                manager.enable_plugin(plugin_name)
            except ValueError:
                if plugin.status == PluginStatus.INITIALIZED:
                    plugin.enable()


@pytest.fixture(scope="session")
def app():
    from vbwd.app import create_app
    from vbwd.extensions import db as _db

    test_url = _test_db_url()
    _ensure_test_db(test_url)
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": test_url,
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "RATELIMIT_STORAGE_URL": "memory://",
        }
    )
    with application.app_context():
        # Import the model packages whose tables must exist: the plugin's own
        # models plus the catalog peers exercised by the snapshot integration
        # test. The peers are soft-imported so a bare per-plugin clone (no
        # subscription package) still builds the plugin's schema and runs every
        # non-catalog test.
        import plugins.bot_meinchat_llm.bot_meinchat_llm.models  # noqa: F401

        _import_optional_catalog_models()

        from vbwd.testing.integration_db import ensure_schema_and_baseline

        ensure_schema_and_baseline(_db)
        _ensure_referral_commission_enum_value(_db)

    _ensure_plugin_enabled(application)

    yield application

    with application.app_context():
        _db.engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_test(app, request):
    from vbwd.extensions import db as _db

    if request.node.get_closest_marker("no_db_isolation") is not None:
        with app.app_context():
            yield
            _db.session.remove()
        return

    with app.app_context():
        from vbwd.testing.integration_db import rollback_isolation

        with rollback_isolation(_db):
            yield


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(_isolate_test):
    """The rolled-back, app-context-bound db handle for integration tests."""
    from vbwd.extensions import db as _db

    yield _db
