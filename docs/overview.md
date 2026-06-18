# bot-meinchat-llm — overview

`bot-meinchat-llm` is the LLM sales-consultant layer of the bot stack. It turns
a guest chat into a grounded, catalog-aware sales conversation.

## Answer pipeline (high level)

1. A guest message arrives over a bot adapter (typically the meinchat bot
   widget). bot-base routes it to this plugin's `handle_action`.
2. The consultant service retrieves the most relevant corpus chunks (Postgres
   full-text, `retrieval_mode = "fts"`) from the indexed `rag_dir`.
3. The live catalog snapshot (subscription / shop / booking, priced through the
   core `PriceFactory`) is attached so the model recommends only real,
   authoritatively-priced items.
4. The training lessons (`training_dir`) and the merchant-editable prompt
   templates (`prompt_dir`) steer the voice. The model / endpoint / key come
   from the core LLM Connection (S97), selected by `llm_connection_slug`.
5. On a buy intent the sales-attribution path can mint a per-room referral
   coupon (S92) + checkout deep link, so a redeemed sale credits the bot.

## Surfaces

- **Command seam** (`bot_namespace = "consultant"`): `consultant` (talk to the
  consultant) and `reindex_sales_docs` (admin-only corpus reindex).
- **Ambient answerer**: `bot_ambient_answerer = True` — answers unclaimed free
  text so no explicit command is needed in the widget.
- **Admin route**: `POST /api/v1/admin/bot-meinchat-llm/reindex` gated by
  `bot_meinchat_llm.corpus.manage`.

## Corpus vs training

- `rag_dir` — product/price **knowledge** retrieved per question.
- `training_dir` — "how to sell" **lessons** that are always applied.

## The `/consultant` page

The hosting CMS page is reproduced from shipped data-exchange envelopes via the
normal import path — see [`import/README.md`](import/README.md). It is identical
on dev-install, CI and prod and is fully idempotent.
