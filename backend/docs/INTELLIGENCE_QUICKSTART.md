# Intelligence Runtime — Quick Start

## Five-Minute Overview

The intelligence runtime is a **single LangGraph-based reasoner** that handles all AI tasks. Two graphs exist:

| Graph | Purpose | When | Input | Output |
|-------|---------|------|-------|--------|
| `interpret_file` | Understand an upload | After profiling | Upload profile facts | Claims, risks, unknowns |
| `recommend_mapping` | Map columns to targets | After Bronze lands | Bronze profile facts | Field candidates + mapping confidence |

Each graph has **three nodes: ground (no model) → infer (model) → assemble (no model)**.

---

## Using the Runtime

### Simplest Case

```python
from cinqflow.intelligence.runtime import AgentRuntime
from cinqflow.settings import get_settings

runtime = AgentRuntime()  # Uses default settings
result = runtime.run(
    "interpret_file",
    facts=profile.facts,
    source_system="fidelis_ny_upstate",
    feed="member_roster",
)

# result is dict:
# {
#   "content": InterpretationContent(...),
#   "prompt": "interpret_file@1",
#   "model": "claude-3-5-sonnet",
#   "knowledge": ["sources/fidelis_ny_upstate__member_roster.yaml@2", ...]
# }
```

### With Progress Callback

```python
def on_step(node: str) -> None:
    print(f"Completed node: {node}")

result = runtime.run(
    "interpret_file",
    facts=profile.facts,
    source_system="fidelis_ny_upstate",
    feed="member_roster",
    on_step=on_step,
)
# Output:
# Completed node: ground
# Completed node: infer
# Completed node: assemble
```

### Custom LLM Client

```python
from cinqflow.intelligence.llm import AnthropicClient, build_client

# Build from settings (auto-detects provider from CINQFLOW_LLM_PROVIDER env var)
client = build_client()

# Or instantiate directly
from cinqflow.settings import get_settings
client = AnthropicClient(get_settings())

# Pass to runtime
runtime = AgentRuntime(llm=client)
```

### Custom Knowledge Provider

```python
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import get_settings

provider = YamlKnowledgeProvider(get_settings())
runtime = AgentRuntime(knowledge=provider)
```

---

## Reading the Output

### InterpretationContent

```python
{
  "claims": [
    {
      "kind": "observed_fact",           # or inference, governed_knowledge, recommendation
      "field": "row_count",
      "value": "51230",
      "confidence": 1.0,                  # 0.0 to 1.0
      "evidence": ["profile:row_count"],  # why this claim
    },
    ...
  ],
  "risks": [
    "DOB is null in 0.2% of rows — quarantine or default?",
    ...
  ],
  "unknowns": [
    "Column 'LOB' code set not confirmed against glossary",
    "claim without evidence discarded: likely_grain",  # dropped item
    ...
  ]
}
```

**Key:** Claims without evidence are **discarded and noted in unknowns**.

### ProposalContent

```python
{
  "fields": [
    {
      "source": "DOB",
      "target": "member.date_of_birth",    # or None
      "concept": "Member's date of birth",
      "transform": { "op": "parse_date", "args": {"format": "MM/DD/YYYY"} },
      "confidence": 0.95,                  # 0.0 to 1.0
      "evidence": ["glossary:DOB", "approved:molina_ny_roster.DOB→date_of_birth"],
      "status": "candidate",               # or ambiguous, unknown, invalid
      "rejected_target": None,             # if status is invalid
      "reason": None,                      # if status is not candidate
    },
    ...
  ],
  "notes": [
    "12 of 47 columns have no canonical target: plan_cd, elig_cd, …",
    "2 columns propose member.source_system_id: member_id, medicaid_id. One must win.",
    ...
  ]
}
```

**Key:** Every observed column appears exactly once. Fabricated targets are marked `invalid`, not silently corrected.

---

## Persisting Results

Both worker examples show the pattern:

```python
# After runtime.run() succeeds
artifact = store.put_interpretation(
    upload_id=upload_id,
    profile_id=profile.profile_id,
    provenance=Provenance(
        prompt=result["prompt"],        # e.g. "interpret_file@1"
        model=result["model"],          # e.g. "claude-3-5-sonnet"
        knowledge=result["knowledge"],  # list of citations with versions
    ),
    content=result["content"],  # InterpretationContent or ProposalContent
)
```

**Key:** Provenance records exact versions. Replaying with new models is always possible.

---

## Testing

### Without LLM

Use `StubClient` (deterministic offline reasoner):

```python
from cinqflow.intelligence.llm import StubClient
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.graphs.interpret_file import InterpretFileGraph
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings

s = Settings(llm_provider="stub")
graph = InterpretFileGraph(
    context_builder=ContextBuilder(YamlKnowledgeProvider(s)),
    llm=StubClient(),
)
result = graph.run(facts=..., source_system=..., feed=...)
```

Stub gives **identical output twice** for identical input (no randomness).

### With Mock LLM

For tests that need specific model behavior:

```python
class MockClient:
    model_id = "mock"
    
    def complete_json(self, *, system, user, response_model=None):
        return {
            "claims": [
                {
                    "kind": "inference",
                    "field": "likely_domain",
                    "value": "enrollment",
                    "confidence": 0.9,
                    "evidence": ["test"],
                }
            ],
            "risks": [],
            "unknowns": [],
        }

graph = InterpretFileGraph(
    context_builder=ContextBuilder(YamlKnowledgeProvider(s)),
    llm=MockClient(),
)
result = graph.run(facts=..., source_system=..., feed=...)
assert len(result["content"].claims) == 1
```

---

## Debugging

### Enable Logging

```bash
LOGLEVEL=DEBUG python -m cinqflow.queue.worker
```

Worker logs include:
- Topic and attempt count
- Graph nodes as they complete (via on_step callback)
- Exceptions with full traceback

### Inspect Graph State

Modify the `run()` method to log intermediate state:

```python
def run(self, **inputs):
    state = {...initial state...}
    final: dict[str, Any] = dict(state)
    for update in self._compiled.stream(state, stream_mode="updates"):
        for node, partial in update.items():
            print(f"After {node}:")
            print(f"  payload keys: {partial.get('payload', {}).keys()}")
            print(f"  citations: {partial.get('citations', [])}")
            final.update(partial)
    return {...}
```

### Compare Stub vs Live

Run the same job twice:

```python
# Stub (deterministic, no API calls)
r1 = graph_with(StubClient(), s).run(facts=facts, ...)

# Stub again (must match)
r2 = graph_with(StubClient(), s).run(facts=facts, ...)

assert r1["content"].model_dump() == r2["content"].model_dump()  # Always passes
```

---

## Common Errors

### "unknown graph: my_task"
The graph name is not registered in `AgentRuntime._graphs` builders dict.
- Add the graph class to the builders dict in `runtime.py`

### "model did not return JSON"
The LLM returned non-JSON (or unparseable JSON).
- Check the prompt — it may not be instructing JSON output
- For AnthropicClient: add `{"format": "json"}` instruction to prompt
- For OpenAIClient: Structured Outputs is enforced at generation time (rare)

### Claims without evidence are discarded
This is intentional. The _assemble node drops claims with empty evidence lists.
- Check the model's output in logs
- Adjust the prompt to encourage evidence

### Fabricated target is marked invalid but I expected it to be a candidate
Also intentional. The _validate node checks every target against the canonical model. If it's not there, status becomes `invalid` and `rejected_target` is set.
- This makes AI hallucinations visible, not silent
- The analyst sees what the model invented

### "no candidate returned for this column"
The model ignored a column. The _validate node adds it as `status=unknown` with reason "no candidate returned for this column".
- This ensures completeness (every column is accounted for)

---

## How to Add a Graph

See `docs/INTELLIGENCE_RUNTIME.md` for the full checklist. Quick version:

1. Create `src/cinqflow/intelligence/graphs/my_task.py` with three nodes: _ground, _infer, _assemble
2. Define `State` TypedDict with input and intermediate keys
3. Define schema in `src/cinqflow/intelligence/schemas.py`
4. Add prompt to `src/cinqflow/intelligence/prompts/my_task_v1.md`
5. Update `REGISTRY` in `prompts/__init__.py`
6. Register graph in `AgentRuntime._graphs` builders dict
7. Add tests for context, output validation, adversarial inputs, determinism, provenance

---

## Files to Know

```
src/cinqflow/intelligence/
├── runtime.py                 # Entry point (AgentRuntime)
├── llm.py                     # LLM clients (Anthropic, OpenAI, Stub)
├── context.py                 # ContextBuilder (selective knowledge assembly)
├── schemas.py                 # LLM output contracts (InterpretationResponse, etc.)
├── graphs/
│   ├── interpret_file.py      # Stage 1 graph
│   └── recommend_mapping.py   # Stage 3 graph
└── prompts/
    ├── __init__.py            # Prompt registry
    ├── interpret_file_v1.md   # Interpretation prompt
    └── recommend_mapping_v2.md # Mapping prompt

tests/unit/
├── test_intelligence.py                      # interpret_file tests
├── test_mapping_intelligence.py              # recommend_mapping tests
└── test_intelligence_state_and_provenance.py # State machine & edge cases

tests/integration/
└── test_bronze_intelligence.py               # Full worker flow
```

---

## Next Steps

- **Read the runtime:** `docs/INTELLIGENCE_RUNTIME.md`
- **Read the examples:** `src/cinqflow/workers/interpret_upload.py` and `analyze_bronze.py`
- **Run the tests:** `pytest tests/ -k intelligence -v`
- **Try a graph:** `python -c "from cinqflow.intelligence.runtime import AgentRuntime; print(AgentRuntime().graph('interpret_file').name)"`

