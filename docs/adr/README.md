# ADRs written by the build

The programme's decision record lives in
[`../../../memory/02-decisions/`](../../../memory/02-decisions/). This folder
holds ADRs that the *implementation* originated — decisions taken while writing
the code that change what the product is, not merely how it is built.

| ADR | Decision |
|---|---|
| [ADR-0020](ADR-0020-merged-persona-ui-and-the-citation-address-space.md) | One merged persona-shaped UI; `citation_id` is the platform's address space |

An ADR here is not a lesser ADR. It follows RB-07 the same way: if it changes
the architecture, `atlas.html` is edited and `tools/build_knowledge.py` is
re-run **in the same commit**, never a generated file by hand.
