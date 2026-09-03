"""One intelligence runtime. Capabilities are graphs, not separate agents."""

from __future__ import annotations

from typing import Any

from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.graphs.interpret_file import InterpretFileGraph
from cinqflow.intelligence.graphs.recommend_mapping import RecommendMappingGraph
from cinqflow.intelligence.llm import LlmClient, build_client
from cinqflow.knowledge.provider import KnowledgeProvider
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings, get_settings


class AgentRuntime:
    def __init__(
        self,
        *,
        knowledge: KnowledgeProvider | None = None,
        llm: LlmClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        s = settings or get_settings()
        self.knowledge = knowledge or YamlKnowledgeProvider(s)
        self.llm = llm or build_client(s)
        self._graphs: dict[str, Any] = {}

    def graph(self, name: str) -> Any:
        """Graphs are built lazily and reused across jobs."""
        if name not in self._graphs:
            builders = {
                "interpret_file": InterpretFileGraph,
                "recommend_mapping": RecommendMappingGraph,
            }
            if name not in builders:
                raise KeyError(f"unknown graph: {name}")
            self._graphs[name] = builders[name](
                context_builder=ContextBuilder(self.knowledge), llm=self.llm
            )
        return self._graphs[name]

    def run(self, name: str, **inputs: Any) -> dict[str, Any]:
        return self.graph(name).run(**inputs)
