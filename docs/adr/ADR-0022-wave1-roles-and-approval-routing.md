# ADR-0022 · Wave 1 ships seven roles, and approval routing is a table in core

**Status:** Accepted · **Date:** 2026-08-30 · **Governs:** identity, governance, CF-V1-E11-01

## Context

Wave 0 deliberately shipped three roles — Engineer, Read-Only, Administrator — the smallest set
that makes the Read-Only server-side denial testable (ADR-0021 rejected building personas the
platform cannot authenticate). Wave 1's exit criterion is *"a BA — not an engineer — onboards the
Centene clone end to end … both approvals recorded."* E11-01 routes approvals per object type:
mapping/dq_rule → data steward; config/contract → platform engineer; publication → business +
technical approver (plate 14). A router with one approver role routes nothing, and a demo where an
engineer plays every part proves nothing.

## Decision

Add exactly the four roles the routing names and no more: **`business_analyst`**,
**`data_steward`**, **`platform_engineer`**, **`business_approver`**.

**The `engineer` does NOT become an approver.** The first draft of this decision overloaded
`engineer` onto plate 14's `platform_engineer` to save a role — and `test_an_engineer_may_run_and_
retry_but_not_approve` failed, correctly. *"Separate create, approve, publish and operate rights"*
is a Wave-0 guarantee with a test behind it, and plate 14 names `platform_engineer` as a routing
target distinct from the person who authors and operates. The engineer builds and runs; the
platform engineer signs off. Overloading them would have dissolved the segregation quietly, in the
one table nobody re-reads.

The full matrix with source/feed/domain/environment scoping remains `CF-V4-E2-02` — that story
widens the `_PERMITTED` table; it does not revisit this decision.

Two tables, two questions, deliberately separate:

- **`core/security._PERMITTED`** — what a role may *attempt* (`APPROVE`, `PUBLISH`, the new
  `RETIRE`). An administrator still cannot approve anything.
- **`core/lifecycle.APPROVAL_ROUTING`** — *where* a holder of APPROVE may exercise it, per object
  type, as data: stewarded types (mapping, dq_rule, glossary_term, runbook) and engineered types
  (source, feed, contract, prompt, execution_plane_contract), with publication additionally
  admitting the business approver. A completeness test asserts every `ObjectType` is routed, so a
  new type cannot ship without the guardrails the first ten have.

The two universal negatives stay where they were — raised by `core/model/governed.py`, never
re-checked in the router, because a second copy of a guarantee is where the first goes to drift.

One port change rides along: `metadata_db` gains **`record_transition(obj, entry)`** — a lifecycle
state change and its audit row persist together or not at all, matching `transition_to`'s pair
return. E4-03's dual-signature publication (business AND technical, recorded on the evidence pack)
arrives with the release packet; this ADR only names who may hold the pen.

## Consequences

- The Wave-1 exit demo is honest: five humans (BA, steward, engineer, platform engineer, business
  approver), each refused everywhere except their own lane, all refusals logged.
- `dev-users.yaml` gains four identities; Keycloak/Entra group mappings gain four claims at
  rungs 1/3 — a profile concern, as always.
- **Cost:** seven roles to keep straight. That is the same count as the MVP's seven-persona
  requirement, arrived at from the routing table rather than from the org chart — CF-V4-E2-02 still
  owes the scoping half.

## Sources

Plate 14 (approval_routing) · ADR-0006 · ADR-0021 · `CF-V1-E11-01` acceptance criteria ·
`memory/06-product/00-epics-and-stories.md` Wave-1 exit criterion
