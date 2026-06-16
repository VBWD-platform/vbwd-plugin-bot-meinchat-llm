"""Assemble the consultant + attribution services from the running app (DRY).

Both the ``handle_action`` seam (free-text turn, buy choice) and the admin
re-index route need the same collaborator graph wired through
``current_app.container`` + the live ``db.session``. This is the one place that
assembles it, so the DI wiring lives in exactly one home.

Every peer-plugin import is lazy / soft-guarded so this module loads even when a
peer is disabled — a disabled referral simply means no coupon offer (degrade),
not an import crash ([[feedback_core_never_depends_on_plugins]]).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from flask import current_app

from vbwd.extensions import db

logger = logging.getLogger(__name__)

#: The meinchat bot identity the consultant issues referral coupons as. Mirrors
#: the bot_meinchat sender so the reward lands on the same BOT user.
BOT_SENDER_EMAIL = "consultant-bot@vbwd.local"
BOT_SENDER_NICKNAME = "consultant"


@dataclass(frozen=True)
class ReindexCounts:
    """The admin reindex response shape ``{files, chunks, skipped}``."""

    files: int
    chunks: int
    skipped: int


def build_catalog_snapshot_service() -> Any:
    """The live, authoritatively-priced catalog snapshot service."""
    from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
        CatalogSnapshotService,
    )

    price_factory = current_app.container.price_factory()
    return CatalogSnapshotService(lambda: db.session, price_factory)


def build_retrieval_service() -> Any:
    """The FTS retrieval service over the indexed corpus."""
    from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
        RagChunkRepository,
    )
    from plugins.bot_meinchat_llm.bot_meinchat_llm.services.retrieval_service import (
        RetrievalService,
    )

    return RetrievalService(RagChunkRepository(db.session))


def build_consultant_service(*, persona: str, debug_mode: bool) -> Any:
    """The grounded answer engine, wired to the CORE LLM client provider."""
    from plugins.bot_meinchat_llm.bot_meinchat_llm.services.consultant_service import (
        ConsultantService,
    )

    return ConsultantService(
        catalog_snapshot_service=build_catalog_snapshot_service(),
        retrieval_service=build_retrieval_service(),
        llm_client_provider=lambda: _resolve_llm_client(),
        persona=persona,
        debug_mode=debug_mode,
        conversation_history_provider=_recent_room_conversation,
        base_url=_plugin_config().get("public_base_url", ""),
    )


def _recent_room_conversation(inbound: Any) -> list:
    """The last few messages of the inbound's room (oldest→newest) as
    ``[{"role": "Customer"|"Consultant", "text": str}]`` so the consultant keeps
    conversational context. Soft-guarded: meinchat absent / bad chat_id → ``[]``.
    The room id is the inbound chat_id minus the provider ``room:`` prefix; the
    Customer is the inbound identity, everyone else (the bot) is the Consultant.
    """
    from uuid import UUID

    chat_ref = getattr(inbound, "chat_ref", None)
    chat_id = getattr(chat_ref, "chat_id", "") if chat_ref is not None else ""
    if not chat_id:
        return []
    room_id_text = chat_id[len("room:"):] if chat_id.startswith("room:") else chat_id
    try:
        room_id = UUID(room_id_text)
    except (ValueError, AttributeError):
        return []
    identity = getattr(inbound, "identity", None)
    guest_user_id = getattr(identity, "vbwd_user_id", None) if identity else None
    try:
        from vbwd.extensions import db
        from plugins.meinchat.meinchat.repositories.message_repository import (
            MessageRepository,
        )

        rows = MessageRepository(db.session).page_room(room_id, limit=8)
    except Exception:  # noqa: BLE001 — meinchat soft dep / read failure
        return []
    history = []
    for row in reversed(rows):  # page_room is newest-first
        body = (getattr(row, "body", None) or "").strip()
        if not body:
            continue
        is_customer = str(getattr(row, "sender_id", "")) == str(guest_user_id)
        history.append(
            {"role": "Customer" if is_customer else "Consultant", "text": body}
        )
    return history


def build_sales_attribution_service(*, reward_enabled: bool) -> Any:
    """The bot-issued referral coupon offerer (S98.4)."""
    from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.room_coupon_repository import (  # noqa: E501
        RoomCouponRepository,
    )
    from plugins.bot_meinchat_llm.bot_meinchat_llm.services.sales_attribution_service import (  # noqa: E501
        SalesAttributionService,
    )

    return SalesAttributionService(
        referral_service_provider=_resolve_referral_service,
        bot_user_id_provider=resolve_bot_user_id,
        bot_nickname=BOT_SENDER_NICKNAME,
        room_coupon_cache=RoomCouponRepository(db.session),
        reward_enabled=reward_enabled,
        base_url=_plugin_config().get("public_base_url", ""),
    )


def run_reindex() -> ReindexCounts:
    """Re-run the corpus ingest and return ``{files, chunks, skipped}`` counts."""
    from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
        RagChunkRepository,
    )
    from plugins.bot_meinchat_llm.bot_meinchat_llm.services.rag_ingest_service import (
        RagIngestService,
    )

    rag_dir = _resolved_rag_dir()
    service = RagIngestService(RagChunkRepository(db.session), rag_dir)
    result = service.ingest()
    db.session.commit()
    return ReindexCounts(
        files=result.files_seen,
        chunks=result.chunks_written,
        skipped=result.files_skipped,
    )


# ── collaborator resolution (lazy / soft-guarded) ───────────────────────────
def _resolve_llm_client() -> Any:
    """Resolve the CORE connection-bound LLM client (S97).

    The plugin's optional ``llm_connection_slug`` selects the connection; empty
    ⇒ the active default. Raised errors propagate to the ConsultantService which
    degrades gracefully (no key ever leaves core).
    """
    slug = _plugin_config().get("llm_connection_slug") or None
    return current_app.container.llm_client(slug=slug)


def _resolve_referral_service() -> Any:
    """Build the S92 referral service bound to the live session (declared dep)."""
    from plugins.referral.referral.service_factory import build_referral_service

    return build_referral_service(db.session)


def resolve_bot_user_id() -> Optional[Any]:
    """Idempotently resolve the consultant BOT user id (the reward target).

    Reuses meinchat's ``BotSenderProvisioner`` so the reward lands on a real
    ``UserRole.BOT`` vbwd user. Returns ``None`` (degrade — no coupon) if the
    meinchat peer / user service is unavailable.
    """
    container = getattr(current_app, "container", None)
    if container is None:
        return None
    try:
        from plugins.meinchat.meinchat.repositories.nickname_repository import (
            NicknameRepository,
        )
        from plugins.meinchat.meinchat.services.bot_sender_provisioner import (
            BotSenderProvisioner,
        )
        from plugins.meinchat.meinchat.services.nickname_service import NicknameService
        from vbwd.repositories.user_repository import UserRepository

        provisioner = BotSenderProvisioner(
            user_service=container.user_service(),
            user_repository=UserRepository(db.session),
            nickname_service=NicknameService(NicknameRepository(db.session)),
            session=db.session,
        )
        return provisioner.ensure_bot_sender(BOT_SENDER_EMAIL, BOT_SENDER_NICKNAME)
    except Exception as error:  # noqa: BLE001 — degrade to no-coupon
        logger.info(
            "[bot-meinchat-llm] bot user could not be provisioned (%s) — "
            "no coupon will be offered",
            error,
        )
        return None


def _plugin_config() -> dict:
    plugin = _plugin()
    return getattr(plugin, "_config", {}) or {}


def _resolved_rag_dir() -> str:
    plugin = _plugin()
    if plugin is not None and hasattr(plugin, "resolved_rag_dir"):
        return plugin.resolved_rag_dir()
    import os

    var_dir = os.environ.get("VBWD_VAR_DIR", "/app/var")
    return f"{var_dir}/bot-meinchat-llm/rag"


def _plugin():
    manager = getattr(current_app, "plugin_manager", None)
    if manager is None:
        return None
    return manager.get_plugin("bot-meinchat-llm")
