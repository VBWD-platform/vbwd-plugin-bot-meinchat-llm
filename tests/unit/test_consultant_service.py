"""S98.3 — ConsultantService unit tests (fake LLM adapter, no network, no DB).

Verifies the answer engine contract:

* the built system prompt carries the persona + the live catalog block (with the
  exact PriceFactory price) + the retrieved doc-chunk text — so the model is
  grounded;
* a structured LLM reply maps to a ``BotReply`` whose ``text`` is the model's
  ``reply_text`` and whose choices are one priced ``BotChoice`` per VALID
  recommendation;
* a hallucinated slug (not in the live catalog) is dropped — no buy choice for
  an item that does not exist;
* an LLM failure (the client raising) degrades to a friendly canned reply that
  leaks no key / secret substring (Liskov: a substitutable provider never
  crashes the bot);
* a widget guest (``identity`` = the provisioned guest user) is answered the
  same way an authenticated user is.
"""
from dataclasses import dataclass
from typing import List, Optional

from plugins.bot_meinchat_llm.bot_meinchat_llm.services.catalog_snapshot_service import (  # noqa: E501
    CatalogBlock,
    CatalogItem,
    SELLABLE_PLAN,
)
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.consultant_service import (
    ConsultantService,
)


# ── lightweight test doubles ────────────────────────────────────────────────
@dataclass
class _FakeChatRef:
    provider_id: str = "meinchat"
    chat_id: str = "room-1"


@dataclass
class _FakeIdentity:
    provider_id: str = "meinchat"
    external_user_id: str = "guest-1"
    vbwd_user_id: str = "guest-uuid"


@dataclass
class _FakeInbound:
    text: Optional[str] = "which plan fits a small team?"
    command: Optional[str] = None
    args: List[str] = None
    action_data: Optional[str] = None
    identity: Optional[_FakeIdentity] = None
    chat_ref: _FakeChatRef = None

    def __post_init__(self):
        if self.args is None:
            self.args = []
        if self.chat_ref is None:
            self.chat_ref = _FakeChatRef()


@dataclass
class _FakeChunk:
    content: str


class _FakeCatalogService:
    def __init__(self, block: CatalogBlock):
        self._block = block

    def snapshot(self, currency=None) -> CatalogBlock:
        return self._block


class _FakeRetrievalService:
    def __init__(self, chunks: List[_FakeChunk]):
        self._chunks = chunks

    def retrieve(self, query: str, top_k: int = 5):
        return self._chunks


class _RecordingLlmClient:
    """Captures the system/user prompt and returns a scripted structured dict."""

    def __init__(self, response):
        self._response = response
        self.captured_system = None
        self.captured_user = None
        self.captured_schema = None

    def generate(self, system, user, *, json_schema=None, **kwargs):
        self.captured_system = system
        self.captured_user = user
        self.captured_schema = json_schema
        return self._response


class _RaisingLlmClient:
    def generate(self, system, user, *, json_schema=None, **kwargs):
        # The real LlmError carries an already-scrubbed message; simulate a
        # failure whose message would still never contain a key.
        raise RuntimeError("LLM call failed")


_SECRET_KEY = "sk-supersecretkey-1234567890"


class _LeakingLlmClient:
    def generate(self, system, user, *, json_schema=None, **kwargs):
        raise RuntimeError(f"boom from provider using {_SECRET_KEY}")


def _catalog_with_team_plan() -> CatalogBlock:
    return CatalogBlock(
        currency="EUR",
        items=[
            CatalogItem(
                sellable_type=SELLABLE_PLAN,
                name="Team Plan",
                slug="team",
                description="For small teams.",
                brutto=120.0,
                netto=100.0,
                currency="EUR",
                billing_period="monthly",
            )
        ],
    )


def _build_service(llm_client, *, persona="PERSONA-MARKER", retrieval_chunks=None):
    chunks = retrieval_chunks if retrieval_chunks is not None else [
        _FakeChunk("Our tariff plans suit teams of any size.")
    ]
    return ConsultantService(
        catalog_snapshot_service=_FakeCatalogService(_catalog_with_team_plan()),
        retrieval_service=_FakeRetrievalService(chunks),
        llm_client_provider=lambda: llm_client,
        persona=persona,
        debug_mode=False,
    )


def test_prompt_contains_persona_catalog_and_chunks():
    client = _RecordingLlmClient(
        {"reply_text": "Try the Team Plan.", "recommendations": [], "intent": "browse"}
    )
    service = _build_service(client, persona="PERSONA-MARKER")

    service.respond(_FakeInbound())

    system = client.captured_system
    assert "PERSONA-MARKER" in system
    # The catalog block carries the exact PriceFactory brutto + slug + name.
    assert "Team Plan" in system
    assert "team" in system
    assert "120.0" in system or "120" in system
    assert "EUR" in system
    # The retrieved doc chunk text is grounded into the prompt.
    assert "Our tariff plans suit teams of any size." in system
    # A JSON schema is requested (structured output).
    assert client.captured_schema is not None


def test_valid_recommendation_becomes_priced_choice():
    client = _RecordingLlmClient(
        {
            "reply_text": "The Team Plan is perfect for a small team.",
            "recommendations": [
                {"sellable_type": "plan", "slug": "team", "reason": "fits teams"}
            ],
            "intent": "recommend",
        }
    )
    service = _build_service(client)

    reply = service.respond(_FakeInbound())

    assert reply.text == "The Team Plan is perfect for a small team."
    assert len(reply.choices) == 1
    choice = reply.choices[0]
    assert choice.action_data == "consultant:buy:plan:team"
    # The label carries the item name and the exact PriceFactory price.
    assert "Team Plan" in choice.label
    assert "120" in choice.label
    assert "EUR" in choice.label


def test_hallucinated_slug_is_dropped():
    client = _RecordingLlmClient(
        {
            "reply_text": "Consider the Galaxy Plan.",
            "recommendations": [
                {"sellable_type": "plan", "slug": "galaxy", "reason": "invented"},
                {"sellable_type": "plan", "slug": "team", "reason": "real"},
            ],
            "intent": "recommend",
        }
    )
    service = _build_service(client)

    reply = service.respond(_FakeInbound())

    actions = {choice.action_data for choice in reply.choices}
    assert "consultant:buy:plan:team" in actions
    assert "consultant:buy:plan:galaxy" not in actions
    assert len(reply.choices) == 1


def test_llm_failure_degrades_gracefully():
    service = _build_service(_RaisingLlmClient())

    reply = service.respond(_FakeInbound())

    assert reply.text  # a friendly canned message, not empty
    assert reply.choices == []


def test_llm_failure_never_leaks_key():
    service = _build_service(_LeakingLlmClient())

    reply = service.respond(_FakeInbound())

    assert _SECRET_KEY not in reply.text
    assert "sk-" not in reply.text


def test_works_for_widget_guest_identity():
    client = _RecordingLlmClient(
        {
            "reply_text": "Welcome! The Team Plan suits you.",
            "recommendations": [
                {"sellable_type": "plan", "slug": "team", "reason": "fits"}
            ],
            "intent": "recommend",
        }
    )
    service = _build_service(client)
    guest_inbound = _FakeInbound(identity=_FakeIdentity())

    reply = service.respond(guest_inbound)

    assert reply.text == "Welcome! The Team Plan suits you."
    assert len(reply.choices) == 1


def test_string_json_response_is_parsed():
    # The core client may return a raw JSON string for some providers; the
    # service must parse it rather than crash.
    client = _RecordingLlmClient(
        '{"reply_text": "Hi there.", "recommendations": [], "intent": "browse"}'
    )
    service = _build_service(client)

    reply = service.respond(_FakeInbound())

    assert reply.text == "Hi there."
