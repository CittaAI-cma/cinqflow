# ADR-0021 · Persona home slots, and the three roles Wave 0 actually has

**Status:** Accepted · **Date:** 2026-08-30 · **Governs:** product IA, Wave 0 · **Amends:** [ADR-0020](ADR-0020-merged-persona-ui-and-the-citation-address-space.md)

## Context

ADR-0020 fixed the merge rule — *persona shapes the home and the ranking; it
never shapes the vocabulary or the depth* — but did not say **where the ranking
lives**. In practice it lived in `ui/app/page.tsx` as:

```tsx
const engineer = me.roles.includes("engineer");
<h1>{engineer ? "What needs you" : "What arrived"}</h1>
ranked.sort(engineer ? byHarm : byRecency)
```

Three problems, in increasing order of seriousness:

1. It covered **two** of Wave 0's three roles. The Administrator got the
   else-branch by accident, not by design.
2. Nothing tested it, because there was nothing to test — a ternary in a
   component is not a fact anything can assert against.
3. It put a **product decision in the browser**. "What an Engineer sees first"
   is the same kind of fact as "which destinations exist in this wave", and
   that one already lives in `core/navigation.py` precisely so it cannot drift.

A design-system review also proposed a **seven-role** slot table and three new
dev principals (Data Steward, Operations, Approver). That proposal was wrong
and is recorded here because the reasoning matters: `core/model/identity.Role`
ships **three** roles in Wave 0, deliberately — *"the smallest set that makes
the Read-Only server-side denial testable, which is the actual Wave-0
guarantee"* — and the seven-role matrix is CF-V4-E2-02. Inventing the other
four in the UI would have produced personas the platform cannot authenticate,
authorize, or test, and a home screen demoing roles that do not exist.

## Decision

**The persona home is a slot manifest in `core/persona.py`, ranked per role,
and served on `/api/me`.**

- A `HomeSlot` is named for the **question** it answers, not the widget that
  draws it — `needs-you`, not `harm-ranked batch table`. The question survives
  a redesign.
- `_HOME` maps role → **ordered** tuple of slots. Order is the ranking, which
  is the whole of persona's permitted influence over the home.
- Each slot carries a `wave` and a `requires: Action`. A slot from an
  unactivated wave, or one whose action the server would refuse, is **absent** —
  never a stub, never an empty card. Same rule `navigation.py` applies to
  Wave-1 destinations.
- Wave-1 slots (`trust-today`, `analyst-cost`, `autonomy-now`,
  `awaiting-decision`) are **declared but inert**, so activating one is a wave
  bump rather than a home-screen rewrite.
- **Three roles, matching `Role`.** CF-V4-E2-02 adds rows to `_HOME`; it does
  not change the shape and does not touch the UI — the same widening path
  `core/security._PERMITTED` is built for.

The UI keeps a `TITLES` map (copy) and a `SlotBody` switch (drawing). It does
**not** know which role it is drawing for. That split is what makes the merge
rule enforceable rather than merely stated: a slot cannot quietly change a word
or a depth for one persona, because it cannot tell which persona it is.

## Consequences

- The ranking is testable without a browser. `tests/unit/test_persona.py` runs
  every role against every slot in milliseconds — *"a permission matrix tested
  by clicking is a permission matrix nobody tests"*, and the same is true of a
  ranking matrix.
- The page title **is** the first slot's title, and the lede is that slot's own
  `answers` string. A persona-ranked home whose headline does not match its
  first card reads as generic, and the two can no longer drift.
- The Administrator gets a designed home — refusals and access changes — rather
  than the analyst's screen by default.
- `tests/design-system.spec.ts` widens ADR-0020's tripwire from two roles to
  all three: same destinations, same labels, only the rank moves.

## What would overturn this

The same evidence ADR-0020 named: real users finding that persona-ranked homes
make the platform harder to give directions in. The parity test is the
tripwire; if it has to be weakened, revisit ADR-0020 first and this second.

Separately, if CF-V4-E2-02 lands and `_HOME` turns out to need per-scope rather
than per-role ranking, that is a widening this shape does not support and would
need its own ADR.

## Sources

[ADR-0020](ADR-0020-merged-persona-ui-and-the-citation-address-space.md) ·
`src/cinqflow/core/persona.py` · `src/cinqflow/core/navigation.py` ·
`src/cinqflow/core/security/__init__.py` (`_PERMITTED`, the widening pattern) ·
`src/cinqflow/core/model/identity.py` (`Role`, and why Wave 0 ships three) ·
`../../../implementation_docs/DESIGN_SYSTEM.md`
