# Working on `cinqflow/ui`

The design system is not the stylesheet. It is the small set of rules below,
each of which has something that fails when it is broken.

## The four rules

### 1 · No colour literal, no raw spacing, outside `globals.css`

`npm run lint:tokens` fails the build on a hex, an `rgb()`, or a `24px` in any
file under `app/`, `components/` or `lib/`. Use a token.

The previous stylesheet had twelve distinct spacing values in 130 lines, none
of them from a scale, and nobody had done anything wrong — that is what a rule
with no gate looks like after six months.

*Exempt:* `0`, `1px` and `2px` (hairlines, not spacing decisions) and SVG
geometry (`viewBox`, `d`, `strokeWidth`).

### 2 · A closed set is a type, and the component refuses the rest

`<Status>` accepts one of seven words and renders anything else visibly wrong.
Do the same for every closed set you add — risk class, lifecycle state, wave.
Never widen the type to make a call site compile.

### 3 · Meaning is never carried by colour alone

Every status is **word + shape + colour**. `.uncited` carries a `⚠` as well as
a hue. `tests/a11y.spec.ts` runs axe over every destination at WCAG 2.1 AA and
must stay at zero violations.

If you add a signal, ask what it looks like in greyscale before you pick a hue.

### 4 · Depth is a drawer, and the drawer's URL is the citation

One depth level. A row opens `?panel=…`, which **is** its `citation_id`.
Clicking it overlays (`app/operations/control/@drawer/`); pasting it cold
renders the full page from the same component. Both paths render
`components/BatchPanels.tsx` — a shared link must not depend on how you arrived.

## The contract with the tests

`tests/workspace.spec.ts` is the **Wave-0 exit criterion**. It asserts against
these hooks. Renaming one is a product change, not a refactor:

| Hook | Where |
|---|---|
| `[data-destination]` | `Sidebar` |
| `[data-panel]` | `PanelTabs` |
| `[data-claim]` `[data-answer]` `[data-refusal]` | Ask |
| `[data-outcome]` | agent-action rows |
| `[data-signin]` | sign-in |
| `[data-action]` | audit rows |
| `.status` `.chip` | `Status`, `CitationChip` |

Literal strings it depends on: `What needs you` · `What arrived` ·
`How I got there` · `This drawer has no write buttons` · `rows_in == rows_out` ·
`segregation of duties`.

Run the full suite at every phase boundary. If a visual change breaks one of
these, the change is wrong until proven otherwise — that suite is the evidence
the redesign altered nothing the platform promised.

## Where things live

```
app/globals.css          tokens · reset · base · components · utilities  (@layer)
app/fonts.ts             Inter + JetBrains Mono, self-hosted woff2
app/design               the living reference — every component, every state
components/ui/           primitives: MetricTile · EmptyState · Skeleton · Drawer · PanelTabs
components/              platform vocabulary: Status · Cited · Refusal · RowsTable · BatchPanels
components/home/slots    the persona home's bodies (they render no headings)
lib/citations.ts         the address space, mirroring core/citations byte-for-byte
lib/queries.ts           concurrent fetch helpers and the harm ranking
scripts/lint-tokens.mjs  rule 1
```

## Things that are decided, not open

- **Persona may change rank, prominence and affordance. Nothing else.**
  Ranking lives in `core/persona.py`, not in a component — see
  [ADR-0021](../docs/adr/ADR-0021-persona-home-slots.md).
- **Wave 0 has three roles.** Engineer, Read-Only, Administrator. The
  seven-role matrix is CF-V4-E2-02. Do not add a fourth to make a screen look
  complete.
- **Wave-1 destinations and slots are absent, never stubbed.**
- **Fonts are committed `.woff2` via `next/font/local`**, not `next/font/google`,
  which needs network at build time.
- **One theme.** Light, done properly. Tokens are already the indirection a
  dark theme would need; adding `[data-theme]` later touches one file.

## Before you open a PR

```bash
npm run lint:tokens
npm run typecheck
npm test                 # workspace + gap-findings + design-system + a11y
```
