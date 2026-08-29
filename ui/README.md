# cinqflow/ui

The Wave-0 workspace: one persona-shaped UI merging the client blueprint's
navigation-simplicity principle with ValueBridge's persona lanes and governance
surfaces (ADR-0020).

## The four commitments this code keeps

1. **Depth is a drawer, never an IA branch.** Every list row opens a right-hand
   drawer whose URL *is* the row's `citation_id`. There is exactly one depth
   level in Wave 0.
2. **Every number is a citation.** Figures render through `<Cited>`, which
   carries a `citation_id` and is clickable. An uncited number is a lint
   failure, mirroring the platform's "uncited claims are a defect class".
3. **Cost, grounding and refusals are on a screen.** LLM Observability ships in
   Wave 0 because the gateway produces budgets, prompt hashes and refusal
   events from day one. If it is not on a screen, it is not being governed.
4. **The seven status words, everywhere.** `<Status>` accepts nothing else, and
   `tests/lexicon.spec.ts` asserts no eighth word reaches a rendered surface.

## The merge rule

> **Persona shapes the home and the ranking. It never shapes the vocabulary or
> the depth.**

Everyone gets the same words, the same objects, the same drawer. What changes
by role is what is ranked first and which buttons exist — and a button that does
not exist is also refused at the server.

## Running it

```bash
npm install
CINQFLOW_API=http://127.0.0.1:8000 npm run dev
```

The API's OpenAPI document is the contract. `lib/api.ts` is the only module
that talks to it.
