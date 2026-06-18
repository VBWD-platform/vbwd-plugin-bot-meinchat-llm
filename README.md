# bot-meinchat-llm

A RAG-grounded LLM **sales consultant** chatbot. It answers a guest's questions
grounded in a static sales corpus plus the live, authoritatively-priced catalog,
recommends a plan/product, and (optionally) offers a referral discount that
credits the bot's token balance on a redeemed sale.

The plugin is a provider-neutral **bot-base consumer**: it structurally
implements `BotCommandProvider` (`bot_namespace="consultant"`), so its commands
light up over every bot adapter (meinchat, Telegram) with no consumer change.
The bridge is optional — the neutral DTOs are imported lazily inside the seam
methods, so the module loads even when `bot-base` is absent.

## What it does

- **`/consultant` command** — greets the guest and claims the conversation, then
  answers grounded turns. The plugin also opts into the bot-base ambient-answerer
  seam (`bot_ambient_answerer = True`), so a widget guest can simply type a
  question and the consultant replies without the explicit command.
- **RAG corpus** — markdown / PDF documents under the configured `rag_dir` are
  chunked and indexed (Postgres full-text baseline) for grounded retrieval. The
  index is (re)built on enable (best-effort) and via the admin reindex surface.
- **Training lessons** — "how to sell" lessons under `training_dir` (sales
  method, example dialogues, objection handling) are always injected to steer the
  consultant's behaviour (distinct from the product/price knowledge corpus).
- **Live catalog** — recommendations and prices come from the live catalog
  (subscription / shop / booking) through the core `PriceFactory`, so the bot
  never invents a price.
- **Reward path** — on a buy intent the consultant can mint a per-room referral
  coupon (S92) and a checkout deep link, so a redeemed sale credits the bot.
- **Admin reindex** — `POST /api/v1/admin/bot-meinchat-llm/reindex` (gated by
  `bot_meinchat_llm.corpus.manage`) and the `reindex_sales_docs` bot command
  (admin identity only) rebuild the corpus index.

## The core LLM Connection (no API key here)

The model / endpoint / API key live in a **core "LLM Connection"** (S97). This
plugin stores **no** API key — only the optional `llm_connection_slug` of the
connection to use. An empty slug selects the active default connection.

## Config keys (`config.json` / `admin-config.json`)

| Key | Default | Purpose |
| --- | --- | --- |
| `debug_mode` | `false` | Verbose server-side logging of prompts / usage (never shown to the guest). Disable in production. |
| `llm_connection_slug` | `""` | Slug of the core LLM Connection to use; empty = active default. No API key stored here. |
| `persona` | sales-consultant text | Persona steering the consultant + sales-agent voice. |
| `reward_enabled` | `true` | Offer a referral discount on a buy intent so a redeemed sale credits the bot's balance. |
| `retrieval_mode` | `"fts"` | Corpus retrieval backend. `fts` = Postgres full-text (no external vector DB). |
| `public_base_url` | `http://localhost:8080` | Absolute site origin used to build full checkout links; empty = relative paths. |
| `rag_dir` | `${VBWD_VAR_DIR}/bot-meinchat-llm/rag` | Static sales corpus (`.md` / `.pdf`) directory. |
| `training_dir` | `${VBWD_VAR_DIR}/bot-meinchat-llm/training` | "How to sell" lessons directory (always applied). |
| `training_max_chars` | `8000` | Max characters of training lessons injected into the prompt (cost guard). |
| `prompt_dir` | `${VBWD_VAR_DIR}/bot-meinchat-llm/prompts` | Editable prompt-template directory (`system.md` + `user.md`), seeded on enable. |

`${VBWD_VAR_DIR}` is resolved at runtime so a relocated var dir is honoured per
environment.

## How it fits the bot stack

```
bot-base          transport-neutral bot core (DTOs, registries, dispatcher, links)
  └─ bot-meinchat       meinchat ↔ bot bridge + bot user identity + conversation style
        └─ bot-meinchat-llm   the LLM sales consultant (this plugin)
```

Declared plugin dependencies: `bot-base`, `meinchat`, `referral`, `discount`,
`subscription`, `shop`, `booking`.

## Demo data / the `/consultant` page

`populate_db.py` is the idempotent demo seed. It:

1. writes a small demo sales corpus and runs a hash-incremental reindex, and
2. reproduces the public `/consultant` CMS page from the SHIPPED data-exchange
   envelopes under `docs/import/cms/` (a `consultant-layout` with a `chat` area,
   and a published `consultant` page hosting the meinchat `meinchat-bot-widget`).

The page seed imports through the registered `cms_layouts` / `cms_posts`
exchangers (the normal import path — no raw SQL) and soft-degrades to a no-op
when cms is disabled or the widget record is absent. See
[`docs/import/README.md`](docs/import/README.md) for the artifacts.

## Quality gate

```
cd vbwd-backend && bin/pre-commit-check.sh --plugin bot_meinchat_llm --full
```
