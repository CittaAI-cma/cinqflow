# ADR-0020 · One merged, persona-shaped UI, and the citation address space

**Status:** Accepted · **Date:** 2026-08-29 · **Governs:** product IA, Wave 0

## Context

`memory/06-product/01-screens-and-ux.md` carries **two CANON screen concepts** and
they have never been reconciled:

| | Owns |
|---|---|
| **Client blueprint** (10 destinations) | the navigation-simplicity principle, the seven status words, "technical depth stays one level below the main panel" |
| **ValueBridge** (11 screens) | the persona-lane thesis, and three governance surfaces the blueprint has no equivalent for — LLM Observability among them |

The memory is explicit that **ValueBridge may not influence the shipped IA
without an ADR**, and that plate 02's `screens: 10` changes via `atlas.html` and
a rebuild, never by hand.

Wave 0 has to ship *a* UI. Shipping both concepts is not an option; shipping
the blueprint alone loses the persona thesis and the governance surfaces;
shipping ValueBridge alone loses the simplicity principle that makes the
platform legible to a Business Analyst.

## Decision

**Wave 0 ships ONE UI that merges them**, under a single rule:

> **Persona shapes the home and the ranking. It never shapes the vocabulary or
> the depth.**

Everyone gets the same seven status words, the same objects, and the same
drawer. What changes by role is *what is ranked first on the home screen* and
*which buttons exist* — and a button that does not exist is also refused at the
server.

**Nine destinations are active in Wave 0** (the blueprint's structure, with
ValueBridge's naming where it is better):

| Group | Destination | Answers |
|---|---|---|
| Overview | Home *(persona-shaped)* | what needs me, ranked by downstream harm |
| Data | Data Intake | what feeds exist, what should arrive, what is Missing |
| Data | Data Explorer *(Landing · Bronze · Silver Raw)* | what data do we have, where is it |
| Operations | Monitor | expected vs actual, per feed, per cycle |
| Operations | Control Operations | the one drawer: stages · inputs · errors · quarantine · recon |
| AI | Ask CINQFLOW | explain this feed · this plan · this run, every claim cited |
| AI | LLM Observability | runs, cost against cap, grounding, **refusals** *(net-new from ValueBridge)* |
| Admin | Users & Roles | who can do what |
| Admin | Audit Trail | who changed what, and who was refused |

Wave-1+ destinations (Mapping & Rules, Data Quality, Work Queue, Data Lineage)
are **hidden until their wave activates** — never stubbed, never an empty
screen. Navigation is generated from a wave-activation manifest in
`core/navigation.py`, so this cannot drift.

### The second half: `citation_id` is the platform's ADDRESS SPACE

`CF-V0-E16-09` requires every tool result to carry a resolvable `citation_id`.
The obvious reading is "the agent needs citations". **This ADR adopts the
better reading: the citation vocabulary is the UI's routing primitive too.**

One resolver in `core/citations/` maps a citation ⇄ a route + drawer state, and
the agent, the breadcrumb, the deep link and the drawer all consume it.

## Consequences

- "Clicking a citation opens that registry row" (E16-10 happy path) needs **no
  agent-specific plumbing** — it is the same `resolve()` navigation already
  uses. A Playwright test clicks a chip inside an answer and lands on the
  drawer.
- The Lane-3 gate *citation resolvability = 100%* becomes a **test over the
  router**, computable with no model in the loop.
- Wave 1's R2 proposals arrive with a review surface already built: a proposal
  is a diff over addressable objects.
- **A URL is shareable.** "Look at `recon:8842#DQ-002`" replaces a screenshot in
  Slack — precisely the `daily_status.xlsx` behaviour the programme exists to
  retire.
- **Depth is a drawer, never an IA branch.** Exactly one depth level exists in
  Wave 0, and each of the five drawer panels is served by a *certified tool* —
  the same one the agent calls. There is no private query behind any screen, so
  a figure on a screen and a figure in an answer cannot disagree.
- **Every number is a `<Cited>`.** A value with no resolvable citation renders
  marked, mirroring "uncited claims are a defect class".

## What would overturn this

Evidence from real Business Analysts that persona-ranked home screens make the
platform *harder* to give directions in — i.e. that two people looking at the
same product cannot orient each other. The merge rule exists to prevent exactly
that, and the Playwright test asserting both personas see the same destinations
with the same labels is its tripwire. If that test has to be weakened, this
decision is what should be revisited.

## Sources

`memory/06-product/01-screens-and-ux.md` (both concepts, marked CANON) ·
`ours/CINQFLOW_User_Stories_Final.docx` `CF-V0-E16-09`, `CF-V0-E16-10` ·
`docs/architecture/INVARIANTS.md` (intelligence, governance) ·
[ADR-0017](../../../memory/02-decisions/ADR-0017-final-story-set-is-the-single-source.md) ·
[ADR-0019](../../../memory/02-decisions/ADR-0019-wave-0-ships-a-control-plane-explainer.md)
