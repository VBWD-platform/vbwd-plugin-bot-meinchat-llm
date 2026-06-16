"""bot-meinchat-llm — a RAG-grounded LLM consultant + sales-agent chatbot.

The plugin is a provider-neutral **bot-base consumer**: it structurally
implements ``BotCommandProvider`` (``bot_namespace="consultant"``) so its
commands light up over every bot adapter (meinchat, Telegram) with no consumer
change. The bridge is optional — the neutral DTOs are imported lazily inside the
seam methods so this module loads even when ``bot-base`` is absent
(chat/cms-ai lazy-import precedent).

S98.0 lands the foundation: the command seam (gated on enabled — empty list when
disabled, Liskov), baseline config (the LLM client is CORE, so the plugin holds
**no** API key — only an optional ``llm_connection_slug``), the manage
permission, and DI provider registration. The answer engine (S98.3), the
attribution/reward path (S98.4) and the admin reindex surface (S98.5) fill the
``handle_action`` stub in later slices; S98.1 (corpus ingest) runs on enable.
"""
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from flask import current_app

from vbwd.plugins.base import BasePlugin, PluginMetadata

if TYPE_CHECKING:  # pragma: no cover - typing only
    from flask import Blueprint

    from plugins.bot_base.bot_base.types import BotCommand, BotInbound, BotReply


BOT_NAMESPACE = "consultant"
CONSULTANT_COMMAND = "consultant"
REINDEX_COMMAND = "reindex_sales_docs"

# The buy-choice action namespace bot-base routes back to ``handle_action``
# (matches ConsultantService's choice ``action_data``). Kept here so the seam
# router and the choice builder share one source of truth.
BUY_ACTION_PREFIX = "consultant:buy"

# Admin permission gating the corpus re-index (the route lands in S98.5; the key
# is declared now so it is present in the permission catalog from foundation).
MANAGE_PERMISSION_KEY = "bot_meinchat_llm.corpus.manage"

DEFAULT_CONFIG: Dict[str, Any] = {
    # The model / endpoint / API key live in a CORE "LLM Connection" (S97). This
    # plugin stores ONLY the optional slug of the connection to use; empty ⇒ the
    # active default connection. There is deliberately NO api key here.
    "llm_connection_slug": "",
    # Persona steering the consultant + sales-agent voice (S98.3 uses it).
    "persona": (
        "You are a friendly, concise sales consultant for our platform. "
        "Recommend only from the provided catalog and never invent a price."
    ),
    # Whether the consultant offers a referral discount on a buy intent (S98.4).
    "reward_enabled": True,
    # Absolute site origin used to build FULL checkout links (e.g.
    # http://localhost:8080). Empty ⇒ relative paths. Set to your public URL.
    "public_base_url": "http://localhost:8080",
    # Retrieval backend. "fts" = Postgres full-text baseline (D-Retrieval).
    "retrieval_mode": "fts",
    # Corpus directory. ``${VBWD_VAR_DIR}`` is resolved at runtime by
    # ``resolved_rag_dir`` so a relocated var dir is honoured per environment.
    "rag_dir": "${VBWD_VAR_DIR}/bot-meinchat-llm/rag",
    # TRAINING directory — "how to sell" LESSONS a merchant writes to coach the
    # consultant before going live (sales method, example dialogues, objection
    # handling). Distinct from ``rag_dir`` (product/price KNOWLEDGE): every
    # lesson here is ALWAYS applied to steer the consultant's behaviour.
    "training_dir": "${VBWD_VAR_DIR}/bot-meinchat-llm/training",
    # Max characters of training lessons injected into the prompt (cost guard).
    "training_max_chars": 8000,
    "debug_mode": False,
}

_DEFAULT_VAR_DIR = "/app/var"


class BotMeinchatLlmPlugin(BasePlugin):
    """RAG consultant + sales-agent bot (a bot-base consumer)."""

    #: The namespace bot-base routes commands / free text to (D1 / D7).
    bot_namespace = BOT_NAMESPACE

    #: Opt into answering UNCLAIMED free text (bot-base ambient-answerer seam) so
    #: a widget guest can just type a question and the consultant replies — no
    #: ``/consultant`` command needed. A command still claims the chat as usual.
    bot_ambient_answerer = True

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="bot-meinchat-llm",
            version="1.0.0",
            author="VBWD Team",
            description=(
                "RAG-grounded LLM consultant and sales agent: answers grounded "
                "in the sales corpus + the live, authoritatively-priced catalog."
            ),
            # Declared plugin→plugin deps. bot-base is the command seam; meinchat
            # is the widget host + bot identity; referral/discount are the reward
            # substrate (S92); subscription/shop/booking are the catalog readers.
            dependencies=[
                "bot-base",
                "meinchat",
                "referral",
                "discount",
                "subscription",
                "shop",
                "booking",
            ],
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        merged = {**DEFAULT_CONFIG}
        if config:
            merged.update(config)
        super().initialize(merged)

    def get_blueprint(self) -> Optional["Blueprint"]:
        # The admin corpus re-index route (S98.5). Absolute /api/v1/admin/...
        # paths on the blueprint, so get_url_prefix stays "".
        from plugins.bot_meinchat_llm.bot_meinchat_llm.routes import (
            bot_meinchat_llm_bp,
        )

        return bot_meinchat_llm_bp

    def get_url_prefix(self) -> Optional[str]:
        return ""

    @property
    def admin_permissions(self) -> List[Dict[str, str]]:
        return [
            {
                "key": MANAGE_PERMISSION_KEY,
                "label": "Reindex the consultant's sales corpus",
                "group": "Consultant Bot",
            }
        ]

    def resolved_rag_dir(self) -> str:
        """The corpus directory with ``${VBWD_VAR_DIR}`` expanded at runtime."""
        import os

        configured = self.get_config("rag_dir", DEFAULT_CONFIG["rag_dir"])
        var_dir = os.environ.get("VBWD_VAR_DIR", _DEFAULT_VAR_DIR)
        return configured.replace("${VBWD_VAR_DIR}", var_dir)

    def resolved_training_dir(self) -> str:
        """The training-lessons directory with ``${VBWD_VAR_DIR}`` expanded."""
        import os

        configured = self.get_config("training_dir", DEFAULT_CONFIG["training_dir"])
        var_dir = os.environ.get("VBWD_VAR_DIR", _DEFAULT_VAR_DIR)
        return configured.replace("${VBWD_VAR_DIR}", var_dir)

    def on_enable(self) -> None:
        # Register the plugin's repository as a container provider so routes /
        # services / other plugins resolve it via the container (the DI gotcha
        # that previously cost a prod outage — [[project_plugin_di_provider_registration]]).
        from vbwd.plugins.di_helpers import register_repositories
        from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
            RagChunkRepository,
        )

        from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.room_coupon_repository import (  # noqa: E501
            RoomCouponRepository,
        )

        container = getattr(current_app, "container", None)
        if container is not None:
            register_repositories(
                container,
                {
                    "bot_meinchat_llm_rag_chunk_repository": RagChunkRepository,
                    "bot_meinchat_llm_room_coupon_repository": RoomCouponRepository,
                },
            )

        # S98.1 — best-effort corpus index on enable. Never break plugin enable
        # if the corpus dir is missing / unreadable (degrades to "no corpus").
        self._ingest_corpus_best_effort()

    def on_disable(self) -> None:
        from vbwd.plugins.di_helpers import unregister_repositories

        container = getattr(current_app, "container", None)
        if container is not None:
            unregister_repositories(
                container,
                [
                    "bot_meinchat_llm_rag_chunk_repository",
                    "bot_meinchat_llm_room_coupon_repository",
                ],
            )

    def _ingest_corpus_best_effort(self) -> None:
        """Index the sales corpus on enable, swallowing any failure.

        The index is convenience-on-enable, not a precondition for enabling; a
        missing dir or a transient error must not block ``on_enable``. The
        explicit re-index route/command (S98.5) is the authoritative entry point.
        """
        import logging

        try:
            self.reindex_corpus()
        except Exception as error:  # noqa: BLE001 — enable must never fail here
            logging.getLogger(__name__).warning(
                "[bot-meinchat-llm] corpus ingest on enable failed: %s",
                error,
                exc_info=True,
            )

    def reindex_corpus(self):
        """Run a full corpus (re)index and commit, returning the ``IngestResult``.

        Builds the session-bound service from the request/app DB session (the
        single source of truth) so it commits on the live transaction.
        """
        from vbwd.extensions import db
        from plugins.bot_meinchat_llm.bot_meinchat_llm.repositories.rag_chunk_repository import (  # noqa: E501
            RagChunkRepository,
        )
        from plugins.bot_meinchat_llm.bot_meinchat_llm.services.rag_ingest_service import (  # noqa: E501
            RagIngestService,
        )

        repository = RagChunkRepository(db.session)
        service = RagIngestService(repository, self.resolved_rag_dir())
        result = service.ingest()
        db.session.commit()
        return result

    # ── bot-base consumer seam (S98.0) ───────────────────────────────────────
    def get_bot_commands(self) -> List["BotCommand"]:
        """The consultant command(s) — only while enabled (Liskov: [] disabled).

        ``CommandRegistry`` collects these from the *enabled* plugin set, so a
        disabled plugin contributes nothing. The neutral ``BotCommand`` DTO is
        imported lazily so this module loads even when bot-base is absent.
        """
        from plugins.bot_base.bot_base.types import BotCommand

        return [
            BotCommand(
                name=CONSULTANT_COMMAND,
                description="Talk to our consultant",
                namespace=BOT_NAMESPACE,
            ),
            BotCommand(
                name=REINDEX_COMMAND,
                description="Reindex the consultant's sales documents (admin)",
                namespace=BOT_NAMESPACE,
            ),
        ]

    def handle_action(self, context: "BotInbound") -> "BotReply":
        """Route a consultant turn (S98.3-S98.5).

        Discriminates, in order: a tapped ``consultant:buy:*`` choice (S98.4
        coupon offer), the ``consultant`` command (greet — the dispatcher has
        already claimed the conversation, D-Autoclaim via command-claim), the
        admin-only ``reindex_sales_docs`` command (S98.5), and otherwise free
        text → the grounded consultant answer turn (S98.3).
        """
        from plugins.bot_base.bot_base.types import BotReply

        action_data = getattr(context, "action_data", None)
        command = getattr(context, "command", None)

        if action_data and action_data.startswith(BUY_ACTION_PREFIX):
            return self._handle_buy_choice(context, action_data)
        if command == CONSULTANT_COMMAND:
            return self._greet()
        if command == REINDEX_COMMAND:
            return self._handle_reindex_command(context)

        try:
            return self._consultant_respond(context)
        except Exception as error:  # noqa: BLE001 — degrade, never crash the bot
            self._log_handle_action_failure(error)
            return BotReply(
                text=(
                    "Sorry, I could not answer right now. Please try again in a "
                    "moment."
                )
            )

    # ── handle_action helpers (S98.3-S98.5) ──────────────────────────────────
    def _greet(self) -> "BotReply":
        from plugins.bot_base.bot_base.types import BotReply

        return BotReply(
            text=(
                "Hi! I'm your sales consultant. Tell me what you need and I'll "
                "recommend the right plan or product."
            )
        )

    def _consultant_respond(self, context: "BotInbound") -> "BotReply":
        from plugins.bot_meinchat_llm.bot_meinchat_llm.service_factory import (
            build_consultant_service,
        )

        service = build_consultant_service(
            persona=self.get_config("persona", DEFAULT_CONFIG["persona"]),
            debug_mode=bool(self.get_config("debug_mode", False)),
        )
        return service.respond(context)

    def _handle_buy_choice(
        self, context: "BotInbound", action_data: str
    ) -> "BotReply":
        """A tapped ``consultant:buy:<type>:<slug>`` choice → a coupon offer.

        Validates the slug against the live catalog, then offers a per-room
        referral coupon + checkout deep link (S98.4). Degrades to a plain link
        if the slug is unknown or no coupon can be minted.
        """
        from plugins.bot_base.bot_base.types import BotReply
        from plugins.bot_meinchat_llm.bot_meinchat_llm.service_factory import (
            build_catalog_snapshot_service,
            build_sales_attribution_service,
        )

        parts = action_data.split(":")
        if len(parts) < 4:
            return BotReply(text="Sorry, I could not process that selection.")
        sellable_type, slug = parts[2], parts[3]

        catalog_block = build_catalog_snapshot_service().snapshot()
        item = next(
            (
                candidate
                for candidate in catalog_block.items
                if candidate.slug == slug
                and candidate.sellable_type == sellable_type
            ),
            None,
        )
        if item is None:
            return BotReply(
                text="That item is no longer available. Ask me for another option."
            )

        room_id = self._room_id(context)
        attribution = build_sales_attribution_service(
            reward_enabled=bool(self.get_config("reward_enabled", True))
        )
        offer = attribution.offer_for_buy(room_id=room_id, item=item)
        if offer is None:
            return BotReply(
                text=(
                    f"Great choice — {item.name} costs {item.brutto} "
                    f"{item.currency}. You can complete the purchase on our "
                    "checkout page."
                )
            )
        return BotReply(text=offer.to_reply_text())

    def _handle_reindex_command(self, context: "BotInbound") -> "BotReply":
        """``reindex_sales_docs`` — admin identity only (S98.5).

        A non-admin / unidentified caller gets a friendly refusal; an admin runs
        the heavy ingest and gets the counts. No secret ever reaches the chat.
        """
        from plugins.bot_base.bot_base.types import BotReply

        if not self._is_admin_identity(context):
            return BotReply(
                text=(
                    "Only an administrator can reindex the sales documents."
                )
            )
        try:
            from plugins.bot_meinchat_llm.bot_meinchat_llm.service_factory import (
                run_reindex,
            )

            counts = run_reindex()
        except Exception as error:  # noqa: BLE001 — never leak internals to chat
            self._log_handle_action_failure(error)
            return BotReply(text="Reindex failed. Please check the server logs.")
        return BotReply(
            text=(
                f"Reindexed the sales corpus: {counts.files} files, "
                f"{counts.chunks} chunks, {counts.skipped} skipped."
            )
        )

    @staticmethod
    def _room_id(context: "BotInbound") -> str:
        chat_ref = getattr(context, "chat_ref", None)
        return getattr(chat_ref, "chat_id", "") if chat_ref is not None else ""

    @staticmethod
    def _is_admin_identity(context: "BotInbound") -> bool:
        """True only when the inbound identity resolves to an ADMIN vbwd user."""
        identity = getattr(context, "identity", None)
        if identity is None:
            return False
        user_id = getattr(identity, "vbwd_user_id", None)
        if user_id is None:
            return False
        container = getattr(current_app, "container", None)
        if container is None:
            return False
        try:
            user = container.user_repository().find_by_id(user_id)
        except Exception:  # noqa: BLE001 — treat resolution failure as non-admin
            return False
        if user is None:
            return False
        from vbwd.models.enums import UserRole

        return getattr(user, "role", None) == UserRole.ADMIN

    @staticmethod
    def _log_handle_action_failure(error: Exception) -> None:
        import logging

        logging.getLogger(__name__).warning(
            "[bot-meinchat-llm] handle_action failed: %s", error, exc_info=True
        )
