"""ConsultantService — the RAG + catalog answer engine (S98.3).

A single consultant turn: build a grounded system prompt (persona + the live,
authoritatively-priced catalog snapshot + the top-k retrieved sales-doc chunks),
ask the CORE LLM client for a STRUCTURED reply, validate every recommended slug
against the live catalog (drop hallucinations), and map the result to a neutral
``BotReply`` whose choices are priced buy-choices.

Design notes honouring the binding requirements:

* **DIP / DI** — the LLM client is the *core* ``container.llm_client(...)`` passed
  in as a provider callable; the catalog + retrieval services are injected. The
  service imports no SDK and holds no API key.
* **Liskov / graceful degradation** — any LLM failure (the typed core
  ``LlmError`` or anything else) yields a friendly canned ``BotReply`` and never
  re-raises, so the bot stays a substitutable answerer. The error detail is
  logged server-side (gated on ``debug_mode``); nothing secret reaches the guest.
* **No invented prices** — the prompt instructs "recommend ONLY from the
  provided catalog" and every recommendation slug is validated against the live
  snapshot before a buy-choice is built; the choice label renders the exact
  PriceFactory price, never a model-stated number.
* **Concise replies (guest-economy)** — the per-word guest charge (meinchat D11)
  bills the guest for the bot's answer too, so the prompt caps the reply length
  and ``MAX_REPLY_TOKENS`` bounds the generation. This is a *flag*, not an
  exemption (the sprint's Risk-Economy note): the bot is told to stay short.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, List

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
    CatalogBlock,
    CatalogItem,
)

logger = logging.getLogger(__name__)

#: Default number of corpus chunks injected per turn. Kept small so the prompt
#: stays inside the latency budget (nginx 60s) and the guest-economy charge.
DEFAULT_RETRIEVAL_TOP_K = 5

#: A soft cap requested of the model so the per-word guest charge stays modest
#: (Risk-Economy — flagged, not exempted). The per-connection max_tokens is the
#: hard cap baked into the core adapter; this is the polite request.
MAX_REPLY_TOKENS = 350

#: The structured-output contract. The core client's ``generate`` forces JSON
#: across either provider; this declares the exact shape the engine parses.
RESPONSE_SCHEMA = {
    "reply_text": "string",
    "recommendations": "array",
    "intent": "string",
}

#: Shown when the LLM is unreachable / errors — friendly, leaks nothing.
FALLBACK_REPLY_TEXT = (
    "Sorry, I could not reach our consultant right now. "
    "Please try again in a moment, or browse our plans and products directly."
)

# The buy-choice action namespace bot-base routes back to ``handle_action``.
_BUY_ACTION_PREFIX = "consultant:buy"


class ConsultantService:
    """Builds a grounded prompt, calls the core LLM client, returns a BotReply."""

    def __init__(
        self,
        *,
        catalog_snapshot_service: Any,
        retrieval_service: Any,
        llm_client_provider: Callable[[], Any],
        persona: str,
        debug_mode: bool = False,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    ) -> None:
        self._catalog_snapshot_service = catalog_snapshot_service
        self._retrieval_service = retrieval_service
        self._llm_client_provider = llm_client_provider
        self._persona = persona
        self._debug_mode = debug_mode
        self._top_k = top_k

    def respond(self, inbound: Any) -> Any:
        """Answer one free-text turn, grounded in the corpus + the live catalog.

        Never raises: an LLM/parse failure degrades to a friendly canned reply
        (Liskov) with no secret leak.
        """
        from plugins.bot_base.bot_base.types import BotReply

        query = (getattr(inbound, "text", None) or "").strip()
        catalog_block = self._catalog_snapshot_service.snapshot()
        chunks = self._retrieve_chunks(query)

        system_prompt = self._build_system_prompt(catalog_block, chunks)
        user_prompt = query or "Hello"

        try:
            raw_result = self._llm_client_provider().generate(
                system_prompt,
                user_prompt,
                json_schema=RESPONSE_SCHEMA,
                max_tokens=MAX_REPLY_TOKENS,
            )
            parsed = self._parse_result(raw_result)
        except Exception as error:  # noqa: BLE001 — degrade, never crash the bot
            self._log_failure(error)
            return BotReply(text=FALLBACK_REPLY_TEXT)

        reply_text = str(parsed.get("reply_text") or "").strip() or FALLBACK_REPLY_TEXT
        choices = self._build_choices(parsed.get("recommendations") or [], catalog_block)
        return BotReply(text=reply_text, choices=choices)

    # ── prompt construction ─────────────────────────────────────────────────
    def _retrieve_chunks(self, query: str) -> List[Any]:
        if not query:
            return []
        try:
            return self._retrieval_service.retrieve(query, self._top_k)
        except Exception as error:  # noqa: BLE001 — retrieval is best-effort
            self._log_failure(error)
            return []

    def _build_system_prompt(
        self, catalog_block: CatalogBlock, chunks: List[Any]
    ) -> str:
        catalog_text = self._render_catalog(catalog_block)
        corpus_text = self._render_corpus(chunks)
        return (
            f"{self._persona}\n\n"
            "You are a concise sales consultant. Keep answers short — the guest "
            "is billed per word for both their question and your reply.\n\n"
            "RULES:\n"
            "- Recommend ONLY items from the CATALOG below. Never invent an item "
            "or a price. Always quote the exact price shown.\n"
            "- Use the SALES NOTES below for context; never contradict the "
            "catalog prices.\n\n"
            f"CATALOG:\n{catalog_text}\n\n"
            f"SALES NOTES:\n{corpus_text}\n"
        )

    @staticmethod
    def _render_catalog(catalog_block: CatalogBlock) -> str:
        if not catalog_block.items:
            return "(no catalog items available)"
        lines = []
        for item in catalog_block.items:
            period = f" / {item.billing_period}" if item.billing_period else ""
            description = f" — {item.description}" if item.description else ""
            lines.append(
                f"- [{item.sellable_type}] {item.name} (slug: {item.slug}): "
                f"{item.brutto} {item.currency}{period}{description}"
            )
        return "\n".join(lines)

    @staticmethod
    def _render_corpus(chunks: List[Any]) -> str:
        contents = [
            str(getattr(chunk, "content", "")).strip()
            for chunk in chunks
            if getattr(chunk, "content", None)
        ]
        if not contents:
            return "(no sales notes retrieved)"
        return "\n---\n".join(contents)

    # ── result parsing + choice building ────────────────────────────────────
    @staticmethod
    def _parse_result(raw_result: Any) -> dict:
        """Normalise the core client's result to a dict (it may return a str)."""
        if isinstance(raw_result, dict):
            return raw_result
        if isinstance(raw_result, str):
            return json.loads(raw_result)
        raise ValueError("LLM returned an unexpected result type")

    def _build_choices(
        self, recommendations: List[dict], catalog_block: CatalogBlock
    ) -> List[Any]:
        """One priced buy-choice per recommendation whose slug is in the catalog.

        Hallucinated slugs (not in the live snapshot) are dropped — the bot can
        never offer to buy an item that does not exist.
        """
        from plugins.bot_base.bot_base.types import BotChoice

        index = self._catalog_index(catalog_block)
        choices: List[Any] = []
        seen: set = set()
        for recommendation in recommendations:
            if not isinstance(recommendation, dict):
                continue
            sellable_type = str(recommendation.get("sellable_type") or "").strip()
            slug = str(recommendation.get("slug") or "").strip()
            key = (sellable_type, slug)
            if not slug or key in seen:
                continue
            item = index.get(key) or index.get(("", slug))
            if item is None:
                continue
            seen.add(key)
            choices.append(
                BotChoice(
                    label=self._choice_label(item),
                    action_data=(
                        f"{_BUY_ACTION_PREFIX}:{item.sellable_type}:{item.slug}"
                    ),
                    hint=f"{item.brutto} {item.currency}",
                )
            )
        return choices

    @staticmethod
    def _catalog_index(catalog_block: CatalogBlock) -> dict:
        index: dict = {}
        for item in catalog_block.items:
            index[(item.sellable_type, item.slug)] = item
            # Also index by slug alone so a recommendation that omits / mistypes
            # the type still resolves to a real item.
            index.setdefault(("", item.slug), item)
        return index

    @staticmethod
    def _choice_label(item: CatalogItem) -> str:
        return f"{item.name} — {item.brutto} {item.currency}"

    def _log_failure(self, error: Exception) -> None:
        if self._debug_mode:
            logger.warning(
                "[bot-meinchat-llm] consultant turn failed: %s", error, exc_info=True
            )
        else:
            logger.info("[bot-meinchat-llm] consultant turn failed (see debug_mode)")
