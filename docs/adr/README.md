# ADRs written by the build

The programme's decision record lives in
[`../../../memory/02-decisions/`](../../../memory/02-decisions/). This folder
holds ADRs that the *implementation* originated — decisions taken while writing
the code that change what the product is, not merely how it is built.

| ADR | Decision |
|---|---|
| [ADR-0020](ADR-0020-merged-persona-ui-and-the-citation-address-space.md) | One merged persona-shaped UI; `citation_id` is the platform's address space |
| [ADR-0021](ADR-0021-persona-home-slots.md) | The persona home is a **slot manifest in `core/persona.py`**, ranked per role and served on `/api/me` — not a branch in a component. Wave 0 has **three** roles. |
| [ADR-0022](ADR-0022-wave1-roles-and-approval-routing.md) | Wave 1 ships **seven** roles and approval routing is a table in `core`. The `engineer` does not become an approver. |
| [ADR-0023](ADR-0023-the-connector-pin-is-the-only-way-in.md) | Delivery is the **21st pin**, not a route. `storage` keeps no write verb, so ADR-0011's "no second door" stays a property rather than a convention. |
| [ADR-0024](ADR-0024-populating-the-plane-is-a-pipeline-run.md) | The plane is populated by **running the pipeline**, never by `COPY` — so lineage, drift, quarantine and reconciliation are real. The populator lives in `scripts/`; `src/cinqflow/` is unchanged. |

An ADR here is not a lesser ADR. It follows RB-07 the same way: if it changes
the architecture, `atlas.html` is edited and `tools/build_knowledge.py` is
re-run **in the same commit**, never a generated file by hand.
