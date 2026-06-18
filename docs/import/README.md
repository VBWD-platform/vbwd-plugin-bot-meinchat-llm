# bot-meinchat-llm — importable artifacts

This directory holds the data-exchange **envelopes** this plugin ships and
imports idempotently from `populate_db.py` (the normal import path — no raw
SQL). Each file is a VBWD data-exchange envelope:

```json
{ "vbwd_export": "<entity_key>", "version": 1, "<entity_key>": [ ...rows ] }
```

They are imported through the registered exchangers in upsert mode, so a re-run
is a no-op and the result is identical on dev-install, CI and prod.

## Artifacts

| File | Entity | Purpose |
| --- | --- | --- |
| `cms/layouts/consultant-layout.json` | `cms_layouts` | The `consultant-layout` layout with a single `chat` area (type `vue`) that hosts the chat widget. |
| `cms/posts/consultant-page.json` | `cms_posts` | The published `consultant` page (slug `/consultant`) using `consultant-layout`, with a per-page widget assignment placing `meinchat-bot-widget` in the `chat` area. |

## Import order and dependencies

`_seed_consultant_page` imports the **layout first**, then the **page**, so the
page's `layout_slug` resolves. The page references the hosted widget only by the
portable slug `meinchat-bot-widget` — that widget **record** is owned and seeded
by the `meinchat` plugin (its `docs/import/cms/widgets/meinchat-bot-widget.json`).
If the widget record is absent, the page still imports and the assignment is
silently skipped (safe degrade). cms is a soft dependency: if its `cms_layouts`
/ `cms_posts` exchangers are not registered, the seed is skipped cleanly.
