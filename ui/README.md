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

**The API has to be up first.** Every screen is a server component that fetches
from the BFF, so with no API there is nothing to render and the root layout
crashes — you get "CINQFLOW could not load", which now names the URL it tried
and the command below.

```bash
# 1 · the API — rung 0, nothing running but Python
cd ..                       # cinqflow/
PYTHONPATH=src .venv/bin/python -m cinqflow.api.dev --port 8000

# 2 · the workspace, in another shell
cd ui
npm install
npm run dev                 # http://127.0.0.1:3000/signin
```

`CINQFLOW_API` defaults to `http://127.0.0.1:8000`; set it if the API is
somewhere else. `npm test` needs neither shell — Playwright starts both itself.

### Two stacks, and they must not touch

`npm test` starts its **own** pair beside the one you are browsing. Everything
about them is separate, and each separation is there because its absence broke
something real:

| | you browse | `npm test` |
|---|---|---|
| Next | `:3000` | `:3100` |
| API | `:8000` | `:8100` |
| build dir | `.next` | `.next-test` (`NEXT_DIST_DIR`) |
| landing zone | `.cinqflow/landing` | `.cinqflow/test-landing` |

Two dev servers rebuilding one `.next` corrupt each other's client-reference
manifest, and the symptom is not a build error — the running app starts serving
**500s for pages that are fine**, or sign-in silently stops setting its cookie.
If you ever see `Invariant: Expected clientReferenceManifest to be defined`,
that is what happened; the config now prevents it.

The other half of that confusion: a "could not reach the API" page names the
port **it actually tried**, so an error mentioning `:8100` is the *test* stack,
not yours.

The API's OpenAPI document is the contract. `lib/api.ts` is the only module
that talks to it, and the only place that distinguishes a **refusal** (a
decision the server made and recorded) from **unreachable** (a transport
failure that reached nobody). It exports two callers: `api()` for JSON and
`upload()` for the one multipart request in the product — separate rather than
a flag, because `upload()` must *omit* `content-type` so the runtime can set
the multipart boundary it generated.

## Getting a file in

Two doors, **one** server action, one route — a second upload path would be a
second place for the business date to be formatted differently or a refusal to
be swallowed.

| File | Its job |
|---|---|
| `app/data/intake/deliver/page.tsx` | intake's own door — here the feed is the *question* |
| `app/data/intake/feed/[feedId]/deliver/page.tsx` | one feed's step; the feed is the *address*, so it is locked |
| `app/data/intake/deliver/actions.ts` | the shared server action. `redirect()` is called **outside** the try/catch |
| `components/DeliverForm.tsx` | drop zone, name echo, live pattern reading, pending state |
| `components/DeliveryOutcome.tsx` | the landing decision — path, citation, link into the profile |

Two rules these keep, both with tests behind them:

- **The browser advises; it never gates.** The form reads the feed's pattern
  and says what will happen, then lets you send it anyway. A file stopped in
  the browser leaves no registry row, no parked copy and no reason anybody can
  read — worse than the second door ADR-0011 forbids.
- **`redirect()` throws.** Called inside the `catch` that renders refusals it
  is caught *as* one, and every delivery reports
  `REJECTED — Error: NEXT_REDIRECT` however well it actually went.

## The design system

Tokens, primitives and the rules that keep them enforced live in
[CONTRIBUTING.md](CONTRIBUTING.md); the analysis and phased plan they came from
is in [`implementation_docs/DESIGN_SYSTEM.md`](../../implementation_docs/DESIGN_SYSTEM.md).

Three things worth knowing before editing anything here:

- **`app/design` is the living reference** — every component in every state,
  reading no platform data. Check new states there before shipping them.
- **`npm run lint:tokens` fails on a colour literal or a raw spacing value**
  outside `app/globals.css`.
- **The persona home is ranked in `core/persona.py`, not in the UI**
  ([ADR-0021](../docs/adr/ADR-0021-persona-home-slots.md)). The UI knows how to
  draw a slot; it does not know which role it is drawing for.
