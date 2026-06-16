"""S98.5 — admin reindex route + reindex_sales_docs command (integration).

Drives the FULL runtime path through the Flask client against real PG:

* ``POST /api/v1/admin/bot-meinchat-llm/reindex`` requires the manage
  permission — 401 unauthenticated, 403 for a non-admin user, 200 for an admin;
* the 200 body carries ``{files, chunks, skipped}`` counts;
* the ``reindex_sales_docs`` bot command refuses a non-admin caller;
* no response / reply contains an API-key-like secret substring.

Users are created through the auth/user service (no raw SQL).
"""
from __future__ import annotations

from uuid import uuid4

from vbwd.models.enums import UserRole

from plugins.bot_meinchat_llm import (
    BotMeinchatLlmPlugin,
    REINDEX_COMMAND,
)

REINDEX_PATH = "/api/v1/admin/bot-meinchat-llm/reindex"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(app, email):
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    user_repo = UserRepository(db.session)
    auth_service = app.container.auth_service()
    existing = user_repo.find_by_email(email)
    if existing is None:
        auth_service.register(email=email, password="BotLlm123@")
        db.session.commit()
        existing = user_repo.find_by_email(email)
    result = auth_service.login(email=email, password="BotLlm123@")
    return existing.id, result.token


def _make_admin(app, email):
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    user_id, token = _register(app, email)
    user = UserRepository(db.session).find_by_id(user_id)
    user.role = UserRole.ADMIN  # legacy fallback: ADMIN ⇒ all permissions
    db.session.commit()
    return user_id, token


def test_reindex_requires_authentication(client):
    response = client.post(REINDEX_PATH)
    assert response.status_code == 401


def test_reindex_forbidden_for_non_admin(app, client):
    email = f"plain-{uuid4().hex[:8]}@example.com"
    _user_id, token = _register(app, email)
    response = client.post(REINDEX_PATH, headers=_auth(token))
    assert response.status_code == 403


def test_reindex_returns_counts_for_admin(app, client):
    email = f"admin-{uuid4().hex[:8]}@example.com"
    _user_id, token = _make_admin(app, email)

    response = client.post(REINDEX_PATH, headers=_auth(token))

    assert response.status_code == 200
    body = response.get_json()
    assert set(body.keys()) == {"files", "chunks", "skipped"}
    assert isinstance(body["files"], int)
    assert isinstance(body["chunks"], int)
    assert isinstance(body["skipped"], int)


def test_reindex_response_has_no_secret_substring(app, client):
    email = f"admin-{uuid4().hex[:8]}@example.com"
    _user_id, token = _make_admin(app, email)
    response = client.post(REINDEX_PATH, headers=_auth(token))
    raw = response.get_data(as_text=True)
    assert "sk-" not in raw
    assert "api_key" not in raw.lower()


# ── reindex_sales_docs bot command (admin identity only) ────────────────────
def _initialized_plugin() -> BotMeinchatLlmPlugin:
    plugin = BotMeinchatLlmPlugin()
    plugin.initialize()
    return plugin


def _make_inbound(*, command=None, identity=None):
    from plugins.bot_base.bot_base.types import BotInbound, ChatRef

    return BotInbound(
        provider_id="meinchat",
        chat_ref=ChatRef(provider_id="meinchat", chat_id="room-admin"),
        sender_ref="sender-1",
        command=command,
        identity=identity,
    )


def test_reindex_command_refuses_non_admin(app):
    from plugins.bot_base.bot_base.types import BotIdentity
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    email = f"plain-{uuid4().hex[:8]}@example.com"
    _register(app, email)
    user = UserRepository(db.session).find_by_email(email)

    plugin = _initialized_plugin()
    identity = BotIdentity(
        provider_id="meinchat", external_user_id="ext-1", vbwd_user_id=user.id
    )
    inbound = _make_inbound(command=REINDEX_COMMAND, identity=identity)

    with app.app_context():
        reply = plugin.handle_action(inbound)

    assert "administrator" in reply.text.lower()


def test_reindex_command_refuses_unidentified(app):
    plugin = _initialized_plugin()
    inbound = _make_inbound(command=REINDEX_COMMAND, identity=None)
    with app.app_context():
        reply = plugin.handle_action(inbound)
    assert "administrator" in reply.text.lower()


def test_reindex_command_runs_for_admin(app):
    from plugins.bot_base.bot_base.types import BotIdentity

    email = f"admin-{uuid4().hex[:8]}@example.com"
    admin_id, _token = _make_admin(app, email)

    plugin = _initialized_plugin()
    identity = BotIdentity(
        provider_id="meinchat", external_user_id="ext-a", vbwd_user_id=admin_id
    )
    inbound = _make_inbound(command=REINDEX_COMMAND, identity=identity)

    with app.app_context():
        reply = plugin.handle_action(inbound)

    assert "reindex" in reply.text.lower()
    assert "sk-" not in reply.text
