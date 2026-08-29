"""Agent graphs, declared as DATA. No runtime is imported here — ever.

    "agent graphs are declared in core, never bound to a runtime"
    — .importlinter, the `graphs-are-data` contract

A graph is nodes plus an edge spec. The node functions are pure over state and
call the platform's own services; the runtime merely walks the edges. That is
what makes LangGraph a Wave-2 ADAPTER SWAP (ADR-0018) rather than a rewrite,
and it is enforced mechanically: this package may not import
`cinqflow.adapters` or `langgraph`.
"""
