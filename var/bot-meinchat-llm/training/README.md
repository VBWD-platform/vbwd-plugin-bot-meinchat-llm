# Consultant Training — how to coach your sales bot before going live

Put your **"how to sell" lessons** in this folder (`*.md` or `*.txt`). Every
lesson here is **always applied** to steer how the consultant talks — its style,
how it qualifies a customer, how it recommends, how it handles objections, and
how it closes.

This is **different** from the `../rag/` folder:

| Folder | Holds | Used how |
|---|---|---|
| `rag/` | Product / price **KNOWLEDGE** (what you sell, features, pricing) | Retrieved per question (top-k) |
| `training/` (this folder) | **HOW to sell** — sales method, example dialogues, objection scripts, do's & don'ts | Always injected into every reply |

## How a merchant trains their consultant

1. Edit or add lesson files here (start from the examples in this folder).
2. Write them as plain guidance and example conversations — the consultant
   reads them like a sales playbook and a set of few-shot examples.
3. Keep the **product facts** (plans, prices, features) in `rag/`, not here.
4. Re-index isn't required for training — lessons are read fresh on each reply.
   (Restart the backend if you changed config, e.g. `training_dir`.)
5. Test in the chat widget before going live; iterate on the lessons until the
   consultant sells the way you want.

## Tips

- Be specific: "Always qualify team size and must-have features before
  recommending" beats "be helpful".
- Show, don't just tell: include 2–3 example exchanges (customer says X →
  consultant replies Y). The model copies the pattern.
- State your guardrails: what the consultant must NEVER do (invent prices,
  discuss competitors, answer off-topic).
- Keep the total under a few thousand words (a cost guard caps how much is
  injected — see `training_max_chars`).

The files shipped here (`01-sales-method.md`, `02-example-conversations.md`,
`03-objection-handling.md`) are a working template — adapt them to your brand.
