# CINQFLOW Intelligence Runtime — Complete Reference

## Architecture Overview

The intelligence capability uses **one runtime, multiple graphs**. Each graph is a LangGraph state machine built from deterministic observations, selected context, LLM reasoning, and validated structured output.

```
job (worker) → runtime.run(name, **inputs)
              ↓
              graph.run(**inputs)
              ↓
              [ground] → [model] → [assemble] → structured artifact
              ↓
              return {content, prompt, model, knowledge}
              ↓
              persist (worker's responsibility)
```

### Non-Negotiable Design Rules

From `docs/blueprints/checklist.md §0`:

- **LangGraph reasons and proposes only; it never moves bulk data.** Graphs consume deterministic facts and governed knowledge; they output structured proposals. Data movement is the responsibility of deterministic code.
- **No AI output is the system of record without analyst approval.** Interpretations and proposals carry status flags and are persisted separately from the source.
- **No LLM-generated code is ever executed.** Mappings are validated data (MappingField), never executable templates.
- **Graphs do not import knowledge files.** The KnowledgeProvider mediates all knowledge access, enabling provider swaps (YAML → database) without touching graph code.
- **Deterministic nodes validate AI output.** Every graph has at least one no-model node that enforces constraints the prompt alone cannot guarantee.

---

## Implementation

### Entry Point: AgentRuntime

Location: `src/cinqflow/intelligence/runtime.py`

**Responsibility:** Compose the runtime from knowledge, LLM, and settings. Build graphs lazily and reuse them.

```python
from cinqflow.intelligence.runtime import AgentRuntime

runtime = AgentRuntime(settings=s)  # or use defaults
result = runtime.run("interpret_file", facts=profile.facts, source_system="...", feed="...")
```

**Inputs to run():**
- `name` (str): graph name from the registry
- `**inputs` (dict): graph-specific inputs, passed directly to the first node

**Returns:**
- `dict` with keys: `content` (persisted artifact), `prompt`, `model`, `knowledge` (citations)

**Key design:**
- Graphs are built once and cached in `_graphs` dict
- Builder registry in `_graphs` is the authoritative list of available graphs
- Runtime does not decide what a graph does; graphs are self-contained

### Graph Pattern

Every graph follows this structure:

```
START → [_ground] → [_infer] → [_assemble] → END
```

Where:
- **Ground (no model)**: Selects knowledge, validates inputs, builds the payload
- **Infer (LLM call)**: Single model.complete_json() call
- **Assemble (no model)**: Validates, drops unsuitable items, formats output

**State object (TypedDict):**
- Input keys (facts, source_system, feed, domain, etc.)
- Intermediate keys (payload, citations, system, etc.)
- Output keys (content, raw, status, etc.)

**Contract:** `run()` method returns a dict with these keys:
- `content` (BaseModel): persisted artifact (InterpretationContent or ProposalContent)
- `prompt` (str): prompt citation e.g. "interpret_file@1"
- `model` (str): model id from LLM client
- `knowledge` (list[str]): citations of all knowledge sources used
- `status` (str, optional): for propose-then-validate graphs

### Graph 1: interpret_file

Location: `src/cinqflow/intelligence/graphs/interpret_file.py`

**Purpose:** Reason over upload profile facts + source knowledge → interpretation

**Inputs:**
- `facts` (ProfileFacts): deterministic profile from profiler
- `source_system` (str): e.g. "fidelis_ny_upstate"
- `feed` (str): e.g. "member_roster"
- `on_step` (Callable, optional): invoked with node name after each node finishes

**Output Schema:** `InterpretationResponse`
- `claims` (list[LlmClaim]): kind, field, value, confidence, evidence
- `risks` (list[str]): data quality warnings
- `unknowns` (list[str]): unresolved questions + discarded items

**Nodes:**
1. **_ground**: Loads source definition + glossary terms (selective). Removes sample_rows (PHI).
2. **_infer**: Calls LLM with context + payload as JSON.
3. **_assemble**: Drops claims without evidence, validates kind/field/confidence ranges, formats output.

**Run method:**
- Uses `stream()` mode to support `on_step` callbacks
- Worker uses `on_step` to record progress for polling UI

**Example usage in worker:**
```python
def on_step(node: str) -> None:
    store.record_interpretation_step(upload_id=upload_id, node=node)
    conn.commit()

result = runtime.run(
    "interpret_file",
    facts=profile.facts,
    source_system=upload.source_system,
    feed=upload.feed,
    on_step=on_step,
)
```

### Graph 2: recommend_mapping

Location: `src/cinqflow/intelligence/graphs/recommend_mapping.py`

**Purpose:** Reason over Bronze profile facts + canonical model + history → mapping proposal

**Inputs:**
- `facts` (ProfileFacts): deterministic Bronze profile
- `source_system` (str): e.g. "fidelis_ny_upstate"
- `feed` (str): e.g. "member_roster"
- `domain` (str): singular e.g. "enrollment" (not "enrollments")

**Output Schema:** `MappingProposalResponse`
- `fields` (list[LlmFieldCandidate]): source, target, concept, transform, confidence, evidence, status
- `notes` (list[str]): human-readable explanations

**Nodes:**
1. **_ground**: Loads canonical model (legal targets), source def, glossary, approved decision history, domain rules.
2. **_recommend**: Calls LLM with context + payload as JSON.
3. **_validate**: Enforces that:
   - Every observed column appears exactly once (even if the model ignored it → status=unknown)
   - Every target exists in canonical model (fabricated targets → status=invalid, reason set, rejected_target recorded)
   - No unsupported transforms (dropped from the field, noted in content.notes)
   - No two columns both claim one target (both become ambiguous)
   - No hallucinated source columns (dropped, noted)

**Run method:**
- Uses `invoke()` (whole-graph mode), no streaming
- Deterministic validation is the safety net

**Example usage in worker:**
```python
outcome = runtime.run(
    "recommend_mapping",
    facts=result.facts,
    source_system=upload.source_system,
    feed=upload.feed,
    domain=_domain_for(upload.domain),
)
# outcome["status"] is "proposed" (valid) or "invalid" (contains fabricated targets)
# outcome["content"] is ProposalContent, suitable for persistence
```

### Context Assembly: ContextBuilder

Location: `src/cinqflow/intelligence/context.py`

**Responsibility:** Select knowledge for THIS job, not the whole base. Return observations + selected context.

**Key methods:**

- **for_interpretation()**: Returns source definition + glossary terms matching observed columns
- **for_mapping()**: Returns canonical model + source definition + glossary + approved mappings + domain rules + decision history
- **legal_targets()**: All field names in the canonical model (used by _validate node)

**No sample rows:** Both remove `sample_rows` from observations (they are PHI-bearing and redundant with bounded example values).

**Selective glossary:** Only terms matching observed column names, to keep context bounded.

---

## LLM Clients

Location: `src/cinqflow/intelligence/llm.py`

### Protocol: LlmClient

All clients implement this protocol:

```python
class LlmClient(Protocol):
    model_id: str
    def complete_json(self, *, system: str, user: str, response_model: type[BaseModel] | None = None) -> dict[str, Any]: ...
```

### Anthropic Client

- Uses Messages API (no structured output support at the wire level)
- Parses JSON from free text
- Per-item validation by graph nodes is the safety net
- `response_model` accepted for interface parity but not enforced at wire level

### OpenAI Client

- Uses `chat.completions.parse()` with structured outputs
- `response_model` is converted to OpenAI JSON Schema and enforced at generation time
- Catches `LengthFinishReasonError` and `ContentFilterFinishReasonError`
- Per-item validation still runs (defense in depth)

### Stub Client

Deterministic offline reasoner. Same input yields same output, enabling replay tests without a provider.

**For interpret_file:**
- Infers domain from column names (member_id → enrollments) or from governed knowledge
- Makes claims about grain, row count, PHI candidates, risks based on profile

**For recommend_mapping:**
- Uses three strategies in order:
  1. Governed glossary term → canonical field
  2. Prior approved decision for same column name
  3. Exact name match on canonical field
- Falls back to unknown, never guesses

---

## Prompts

Location: `src/cinqflow/intelligence/prompts/`

**Pattern:**
- Separate markdown files, versioned
- Prompt loader reads the file and returns (text, citation)
- Citation is recorded in provenance for every artifact

**Registry:** `REGISTRY` dict in `__init__.py` maps name → version number

**Adding a new prompt:**
1. Create `<name>_v1.md`
2. Add to `REGISTRY: {"<name>": 1}`
3. Graphs load via `prompts.load("<name>")`

---

## Structured Output Schemas

Location: `src/cinqflow/intelligence/schemas.py`

**Important:** Schemas here are for **LLM output**, not persisted artifacts.

Two key differences:
1. **No numeric range constraints** (OpenAI's converter doesn't support JSON Schema `ge`/`le`): Enforced by _assemble/_validate nodes
2. **No fields the model invents** (e.g. `rejected_target`, `reason`): Set by deterministic code

**Classes:**
- `LlmClaim`: kind, field, value, confidence, evidence
- `InterpretationResponse`: claims, risks, unknowns
- `LlmFieldCandidate`: source, target, concept, transform, confidence, evidence, status
- `MappingProposalResponse`: fields, notes

---

## Worker Integration

### interpret_upload

Location: `src/cinqflow/workers/interpret_upload.py`

**Flow:**
1. Get upload + profile from store
2. Set status to INTERPRETING, record run start (committed before graph runs)
3. Call runtime.run() with on_step callback
4. Catch exceptions → persist error, mark failed
5. Success → persist interpretation with provenance, mark interpreted

**Key:** Status and run start are durably persisted before the graph runs, so a mid-run poll sees real progress.

### analyze_bronze

Location: `src/cinqflow/workers/analyze_bronze.py`

**Flow:**
1. Get run + upload
2. Profile the batch (deterministic, code, not AI)
3. Persist bronze profile
4. Call runtime.run() for mapping proposal
5. Catch exceptions → persist error, mark profiled (no proposal)
6. Success → persist proposal with provenance

**Key:** Deterministic profiling happens first; AI reasons only over facts established by code.

---

## Testing Strategy

### Unit Tests

**Location:** `tests/unit/test_intelligence.py` and `tests/unit/test_mapping_intelligence.py`

**Coverage:**
- Context selection (knowledge filtering, PHI scrubbing)
- Governed knowledge wins (domain from source definition, not inference)
- Provenance recording
- Structured output validation (drop malformed, drop without evidence, drop invalid targets)
- Determinism (stub reasoner gives same output twice)
- Stub reasoner logic (glossary → name match → unknown)
- Adversarial inputs:
  - Model invents targets not in canonical
  - Model invents source columns not in Bronze
  - Model proposes unsupported transforms
  - Two columns claim one target
  - Candidate without evidence
- Concept handling (survives rejection, None for ignored columns)
- Architectural constraints (graphs don't import YAML, don't know provider)

**Fixture:** `small_csv_bytes` (from conftest), roster sample

### Integration Tests

**Location:** `tests/integration/test_bronze_intelligence.py`

**Coverage:**
- Full worker flow: land Bronze → profile → analyze → persisted proposal
- Profile immutability (hash of facts)
- Sampling recorded
- Analysis refused for incomplete batch

### Replayed LLM

By default, tests use StubClient. For live provider tests:
```
pytest -m live_llm
```

---

## Adding a New Graph

### Checklist

1. **Create the graph class**
   - File: `src/cinqflow/intelligence/graphs/<graph_name>.py`
   - Inherit structure from existing graphs
   - Define `State` TypedDict
   - Implement `_ground()`, `_infer()` (or equivalent reasoning), `_assemble()` (validation)
   - Implement `run()` method with proper error handling

2. **Add schema**
   - File: `src/cinqflow/intelligence/schemas.py`
   - Define LlmXxxResponse with fields the model outputs
   - Use Literal for enums, no numeric constraints

3. **Register the graph**
   - Add to `AgentRuntime._graphs` builders dict
   - Graph.run() must return dict with: content, prompt, model, knowledge

4. **Add prompt**
   - File: `src/cinqflow/intelligence/prompts/<name>_v1.md`
   - Update REGISTRY with name and version
   - Prompt loads via `prompts.load()`

5. **Update ContextBuilder**
   - Add `for_<graph_name>()` method if needed
   - Return `JobContext(observations=..., context=..., citations=[...])`

6. **Create worker**
   - File: `src/cinqflow/workers/<topic_name>.py`
   - Call `runtime.run(graph_name, ...)`
   - Persist result with provenance
   - Catch exceptions, mark failed with error

7. **Register worker**
   - Add to `handlers()` dict in `queue/worker.py`
   - Topic format: `<domain>.<action>` e.g. `upload.interpret`, `bronze.analyze`

8. **Add tests**
   - Context selection + filtering
   - Structured output validation
   - Adversarial inputs (model invents things)
   - Determinism
   - Provenance
   - Full worker flow (integration)

---

## Constraints Verified

✓ **One runtime**: `AgentRuntime` composes all graphs
✓ **LangGraph only**: No Celery, no other agents, no polling loops in graph code
✓ **Graphs don't move data**: No file I/O, no db writes (observations only)
✓ **Deterministic grounding**: _ground nodes are pure functions
✓ **One LLM call per graph**: Each graph calls model exactly once (or zero if the graph has no inference)
✓ **Validation is deterministic**: _assemble/_validate nodes enforce contracts without AI
✓ **Provenance recorded**: Every artifact carries prompt version, model id, knowledge citations
✓ **No knowledge file imports**: Only `KnowledgeProvider` reads YAML
✓ **Knowledge is governance**: Graphs propose only what the canonical model allows
✓ **Failures are visible**: Invalid targets persisted as such, not silently corrected

---

## Performance Notes

- Graphs are built once and cached (lazy initialization)
- interpret_file uses `stream()` for on_step callback; recommend_mapping uses `invoke()`
- Context selection is O(N columns) for glossary filtering
- Validation in _assemble/_validate is O(N items) with early exits
- No external API calls except the model

---

## Debugging

### Logs

Enable DEBUG logging to see graph execution:
```bash
LOGLEVEL=DEBUG python -m cinqflow.queue.worker
```

Worker logs include:
- Topic, attempt count
- Graph node execution (via on_step callback)
- Errors with full traceback, persisted as `error` on the artifact

### Replay

Stub client gives deterministic output; run same inputs twice for comparison:
```python
r1 = runtime.run("interpret_file", facts=..., ...)
r2 = runtime.run("interpret_file", facts=..., ...)
assert r1["content"].model_dump() == r2["content"].model_dump()
```

### Inspect Intermediate State

Graph state is available in run() at the final node. Modify graph.run() to log intermediate state:
```python
for update in self._compiled.stream(state, stream_mode="updates"):
    print(f"After {list(update.keys())[0]}: {update}")
```

---

## References

- **Templates:** `docs/blueprints/templates.md` — artifact and knowledge shapes
- **Checklist:** `docs/blueprints/checklist.md` — non-negotiables and stage gates
- **Features:** `docs/blueprints/features.md` — acceptance criteria per stage
- **LangGraph docs:** https://langchain-ai.github.io/langgraph/

