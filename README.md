# CINQFLOW

A CINQCARE-owned, metadata-driven healthcare data platform with an AI layer.

A Business Analyst onboards a feed as six fields of governed metadata. The
platform then runs it `Landing → Bronze → Silver Raw`, balanced and
replay-refused, while operations monitor, reconcile and troubleshoot it — and
the platform explains, with openable citations, what it is configured to do and
what it just did.

**Wave 0 is built.** Silver ODS sits behind gate G4 (identity resolution) and is
Wave 3; its schema is provisioned and empty.

New to the codebase? Read [`docs/DOMAIN.md`](docs/DOMAIN.md) first — the
business domain (VBC contracts, feeds, claims lineage, identity resolution,
DQ/PHI rules), the governance and AI-agent decisions, and what's actually
built versus designed. This README covers running the code; DOMAIN.md covers
why it's shaped the way it is.

## Five rules govern every change

1. **`core/` imports no vendor SDK, URL, path or credential** — and performs no
   I/O. Vendor code lives only in `adapters/`.
2. **Every external touch crosses a port** — real / dev stand-in / mock, sharing
   **one** contract suite.
3. **All environment difference lives in the connection profile.** Nowhere else.
4. **Agents propose; humans dispose.** R4 is human-always, not configurable.
5. **Acceptance criteria are the tests, written first** — including a negative
   test for every "don't", which *makes the attempt*.

They are not aspirations. Each one is a CI gate: `import-linter`,
`conformance/lint_core_purity.py`, the contract suites, and the negative tests.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements/dev.txt && pip install -e . --no-deps

./scripts/wave0-demo.sh            # the whole wave, proving itself
python conformance/kit.py          # 21 pins + 3 platform laws
cinqflow ask "why did batch 8842 lose rows?"

cd ui && npm install && npm run dev # http://localhost:3000/signin
cd ui && npm test                   # 69 Playwright assertions
```

Nothing above needs a database, a container or a credential — that is rung 0.
For the real Postgres plane (rung 0.5, the default development socket):

```bash
cp .env.example .env               # then fill in CINQFLOW_SECRET_PG_DSN
cinqflow install --profile profiles/local.yaml
```

## Getting a file in

Files arrive through the **`connector` pin** — the only pin with a write verb
into the landing zone. `storage` deliberately has none, so nothing else in the
platform can put a file there: *there is no second door* (ADR-0011).

```bash
# a file, from the shell — the same path an SFTP poller takes
cinqflow ingest --business-date 2026-10-01 --file ./_CINQDOWNSTATE_Member_Roster_20261001.xlsx

# or from the workspace
open http://127.0.0.1:3000/data/intake/deliver
```

Either way you get a **landing decision**, not "upload succeeded" — the bytes
almost always arrive, and what matters is what the platform decided:

| | | |
|---|---|---|
| `ACCEPTED` | → `processed/` | registered, then profiled by computation |
| `UNEXPECTED` | → `parked/` | matched no pattern. Registered anyway, never discarded |
| `REJECTED` | → `rejected/` | a named pre-flight check declined it |
| `SKIPPED` | → `archive/` | the fingerprint is already in the input registry |

An accepted file is profiled straight away — row counts, type readings, null
counts, key candidates. **No model is called**, which is what makes every fact
on the next screen citable and what the schema-inference agent grounds on.

## Layout

```
src/cinqflow/
  core/          the logic. No I/O, no vendor, no environment difference.
    model/         vocabulary · governed objects · identity · profile · llm · phi
    citations/     THE ADDRESS SPACE — a citation parses to a UI route
    schema_spec/   11 control tables + 6 data schemas, declared once
    registry/      feed · contract · dq rules · execution-plane register
    compiler/      metadata -> IR -> execution
    landing/       what happens to an arriving file: the four outcomes
    delivery/      how one gets IN: the key layout, the name rule, the checksum
    recon/ parsers/ security/ navigation.py
    prompts/       the fixed assembly order, owned by one function
    intelligence/  the six call stages, budgets, routing, schema subset
    tools/         16 certified tools, declared as data
    retrieval/     lexical index + a glossary generated from the vocabulary
    agents/        graphs as data — no runtime imported, ever
  ports/         21 pins: a Protocol each, and ONE contract suite each
  adapters/      mock · local (Postgres, dotenv) · openai_compatible · replay
  intelligence/  the gateway, the tool executor, the agent, the eval gates
  api/           the BFF. Its OpenAPI document is the UI's contract.
  workers/ installer/ simulator/
ui/              Next.js App Router — nine destinations, one drawer
conformance/     the Law-1 lint and the pin-by-pin kit
profiles/        mock · local · ci — every pin addressed, secrets by reference
compose/         the rung-1 twin
docs/adr/        decisions the implementation originated
```

## The three ideas worth knowing before reading the code

**`citation_id` is the platform's address space.** `recon:8842#DQ-002` is a
citation an agent emits, a route the UI opens, and a link you can paste into
Slack. One resolver serves all three, so "clicking a citation opens that
registry row" needed no plumbing.

**The compiled plan does three jobs.** The engine runs it, the agent explains
it, and the eval harness grades against it — so the Lane-3 golden set is
*generated* at zero annotation cost and grows with every feed added.

**The refusals ship before the capabilities.** Bronze `UPDATE` is refused at the
database layer before Bronze holds a row; the Read-Only server-side denial was
written before any edit route existed; the agent's write-refusal before the
agent. A guardrail nobody tries is a comment, not a control.

## Testing

Three lanes, and they are not interchangeable:

| Lane | What it proves | Holds a credential |
|---|---|---|
| **1 · mock** | machinery — the graph runs, the budget refuses, the schema rejects | no, by construction |
| **2 · replay** | wiring — recorded exchanges at the **port** boundary | no |
| **3 · real API** | **the only source of a quality claim** | yes |

`tests/conftest.py` deletes credential variables from the environment for
lanes 1 and 2, so a misclassified test fails loudly rather than quietly reaching
a real endpoint. **No evaluation threshold may be claimed from Lane 1 or 2** —
the Lane-3 evals skip with a message naming each unset variable rather than
passing against a stand-in.

## Zero PHI

Development holds **no PHI**, by constraint (ADR-0016). Every file is synthetic,
generated by the simulator from real layouts: *real layouts, never real
members.* No tool in the catalogue can emit a member-level row in any
environment — including the synthetic ones, because a tool that is safe only
because the data is fake is not a safe tool.
