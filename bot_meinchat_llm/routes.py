"""bot-meinchat-llm admin routes — corpus re-index (S98.5).

A single permission-gated admin endpoint that re-runs the RAG ingest off the
chat request path (the reindex is heavy; running it here keeps the consultant
turn fast — Risk-Latency). Absolute ``/api/v1/admin/bot-meinchat-llm/*`` paths so
``get_url_prefix()`` stays ``""`` (room to add a public group later).

Each route is ``@require_auth @require_admin @require_permission(...)`` — the
manage permission (declared in the plugin's foundation) gates the re-index, so an
unauthenticated caller gets 401 and a non-permitted user gets 403.

Secrets: the response carries only counts; it never echoes a prompt or an API key
(the key lives in the core LLM connection, not in this plugin).
"""
import logging

from flask import Blueprint, jsonify
from vbwd.middleware.auth import require_admin, require_auth, require_permission

from plugins.bot_meinchat_llm import MANAGE_PERMISSION_KEY

logger = logging.getLogger(__name__)

bot_meinchat_llm_bp = Blueprint("bot_meinchat_llm", __name__)


@bot_meinchat_llm_bp.route("/api/v1/admin/bot-meinchat-llm/reindex", methods=["POST"])
@require_auth
@require_admin
@require_permission(MANAGE_PERMISSION_KEY)
def admin_reindex_corpus():
    """Re-run the sales-corpus ingest; return ``{files, chunks, skipped}``.

    The heavy ingest runs synchronously here (off the chat path). On a corpus /
    runtime error the response is a clean 500 with a generic message — never a
    stack trace and never a secret.
    """
    from plugins.bot_meinchat_llm.bot_meinchat_llm.service_factory import (
        run_reindex,
    )

    try:
        result = run_reindex()
    except Exception as error:  # noqa: BLE001 — never leak internals to the client
        logger.warning("[bot-meinchat-llm] admin reindex failed: %s", error)
        return jsonify({"error": "Reindex failed"}), 500

    return (
        jsonify(
            {
                "files": result.files,
                "chunks": result.chunks,
                "skipped": result.skipped,
            }
        ),
        200,
    )
