"""The only path to a model call.

Two clients: `anthropic` for the real provider, `stub` for a deterministic offline
reasoner. The stub is not a mock of a mock - it is a documented, rule-based
fallback so the flow is complete and testable without a key, and it labels its own
output as such through the model id in provenance.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel

from cinqflow.settings import Settings, get_settings


class LlmError(Exception):
    pass


class LlmClient(Protocol):
    model_id: str

    def complete_json(
        self, *, system: str, user: str, response_model: type[BaseModel] | None = None
    ) -> dict[str, Any]: ...


class AnthropicClient:
    """Real provider. Asks for a single JSON object and refuses anything else.

    `response_model` is accepted for interface parity but not enforced at the
    wire level here - the Messages API this build calls has no schema-
    constrained decoding, so this path relies on the prompt's own instructions
    plus the deterministic per-item validation every graph already does after
    the call (`_assemble` / `_validate`), same as before this parameter existed.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise LlmError("llm_provider=anthropic requires CINQFLOW_LLM_API_KEY")
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise LlmError("anthropic package unavailable") from exc
        self._client = Anthropic(api_key=settings.llm_api_key)
        self.model_id = settings.llm_model
        self._max_tokens = settings.llm_max_tokens

    def complete_json(
        self, *, system: str, user: str, response_model: type[BaseModel] | None = None
    ) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model_id,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LlmError(f"model did not return JSON: {text[:200]}") from exc


class OpenAIClient:
    """Real provider via OpenAI Structured Outputs.

    Unlike `AnthropicClient`, this path gets the model to guarantee its shape at
    generation time: `response_model` is passed straight through as
    `response_format`, so the API itself refuses to emit anything that doesn't
    match - not a post-hoc check on free text. The deterministic per-item
    validation downstream (`_assemble` / `_validate`) still runs regardless; this
    just means it should rarely find anything to reject on this path.

    Works with a fine-tuned model id (e.g. `ft:gpt-4o-2024-08-06:org::id`) the
    same way as a base model - Structured Outputs is a property of the base
    model a fine-tune was built on, not a separate capability to configure.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise LlmError("llm_provider=openai requires CINQFLOW_LLM_API_KEY")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LlmError("openai package unavailable") from exc
        self._client = OpenAI(api_key=settings.llm_api_key)
        self.model_id = settings.llm_model
        self._max_tokens = settings.llm_max_tokens

    def complete_json(
        self, *, system: str, user: str, response_model: type[BaseModel] | None = None
    ) -> dict[str, Any]:
        if response_model is None:
            # Every real call site passes one (see the two graphs); a client
            # calling in without it would silently get unconstrained JSON mode,
            # which defeats the point of choosing this provider.
            raise LlmError("llm_provider=openai requires a response_model per call")
        try:
            from openai import ContentFilterFinishReasonError, LengthFinishReasonError
        except ImportError as exc:  # pragma: no cover
            raise LlmError("openai package unavailable") from exc

        try:
            completion = self._client.chat.completions.parse(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=response_model,
                # Not `max_tokens`: deprecated, and rejected outright by o-series
                # reasoning models - this name works whether the fine-tune's base
                # is a standard or a reasoning model.
                max_completion_tokens=self._max_tokens,
            )
        except LengthFinishReasonError as exc:
            raise LlmError(
                f"response truncated before completing {response_model.__name__}; "
                "raise CINQFLOW_LLM_MAX_TOKENS or shorten the payload"
            ) from exc
        except ContentFilterFinishReasonError as exc:
            raise LlmError("response blocked by the provider's content filter") from exc

        message = completion.choices[0].message
        if message.parsed is None:
            raise LlmError(f"model refused: {message.refusal or 'no reason given'}")
        return message.parsed.model_dump()


class StubClient:
    """Deterministic offline reasoner over the same context payload.

    It reads the profile facts and governed knowledge it is given and derives the
    same claim shapes the real model must produce. Identical input yields identical
    output, which is what makes replay-style tests possible without a provider.
    """

    model_id = "stub-reasoner-1"

    def complete_json(
        self, *, system: str, user: str, response_model: type[BaseModel] | None = None
    ) -> dict[str, Any]:
        payload = json.loads(user)
        # The two graphs are told apart by what their context carries, not by a
        # flag, so neither has to know a stub exists.
        if "canonical" in (payload.get("context") or {}):
            return self._recommend_mapping(payload)
        return self._interpret_file(payload)

    # ------------------------------------------------------- recommend_mapping
    def _recommend_mapping(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Rule-based mapping proposal over the same context a model would see.

        It only ever names targets present in the supplied canonical model, and
        leaves a column unknown when the governed knowledge gives it no home -
        which is exactly the behaviour the prompt demands of a real model.
        """
        facts = payload["observations"]
        context = payload.get("context") or {}
        canonical = context.get("canonical") or {}

        legal: dict[str, dict[str, Any]] = {}
        for entity in canonical.get("entities", []):
            for field in entity.get("fields", []):
                legal[field["name"]] = field
        by_leaf: dict[str, list[str]] = {}
        for qualified in legal:
            by_leaf.setdefault(qualified.split(".", 1)[1], []).append(qualified)

        glossary = {
            str(term.get("term", "")).lower(): term
            for term in (context.get("glossary") or {}).get("terms", [])
        }
        glossary_citation = (context.get("glossary") or {}).get("citation", "glossary")

        history_targets: dict[str, list[str]] = {}
        history_citation = (context.get("history") or {}).get("citation", "history")
        for decision_set in (context.get("history") or {}).get("decision_sets", []):
            for decision in decision_set.get("decisions", []):
                leaf = str(decision.get("source_field", "")).split(".")[-1].strip().lower()
                target = str(decision.get("target", "")).strip()
                if leaf and target in legal:
                    history_targets.setdefault(leaf, []).append(target)

        # An approved decision (not a mechanical decision set - a governed
        # ruling, e.g. "member_id is the member key, not medicaid_id") outranks
        # a mechanical exemplar. `recommend_mapping._validate` enforces this
        # regardless of what the stub does, but a faithful offline reasoner
        # should already agree, the same way a real model is asked to.
        precedent_targets: dict[str, str] = {}
        precedent_citation = (context.get("precedents") or {}).get("citation", "precedents")
        for decision in (context.get("precedents") or {}).get("decisions", []):
            column = str(decision.get("applies_to", "")).strip().lower()
            target = str(decision.get("target", "")).strip()
            if column and target in legal:
                precedent_targets[column] = target

        fields: list[dict[str, Any]] = []
        notes: list[str] = []

        for column in facts["columns"]:
            name = column["name"]
            lowered = name.lower()
            evidence: list[str] = []
            target: str | None = None
            confidence = 0.0

            # 0. An approved decision already routes this exact column.
            if lowered in precedent_targets:
                target = precedent_targets[lowered]
                confidence = 0.95
                evidence.append(f"precedent:{precedent_citation}:{lowered}->{target}")

            # 1. A governed glossary term that points at a canonical field.
            term = glossary.get(lowered)
            maps_toward = str((term or {}).get("maps_toward", "")).strip()
            if target is None and maps_toward and maps_toward in legal:
                target = maps_toward
                confidence = 0.92
                evidence.append(f"glossary:{glossary_citation}:{name}")

            # 2. A prior approved decision for the same source column name.
            if target is None and lowered in history_targets:
                choices = sorted(set(history_targets[lowered]))
                target = choices[0]
                confidence = 0.85
                evidence.append(f"history:{history_citation}:{lowered}->{target}")

            # 3. The column name matches a canonical field name outright.
            if target is None and lowered in by_leaf:
                choices = sorted(by_leaf[lowered])
                if len(choices) == 1:
                    target = choices[0]
                    confidence = 0.7
                    evidence.append(f"name match:{target}")
                else:
                    evidence.append("ambiguous name match: " + ", ".join(choices))

            status = "candidate" if target else "unknown"
            if target is None and evidence:
                status = "ambiguous"
            if target is None and not evidence:
                where = canonical.get("citation", "the canonical model")
                evidence.append(f"no canonical field for '{name}' in {where}")

            transform: dict[str, Any] | None = None
            if target:
                declared = legal[target].get("type")
                if declared == "timestamp" and column["inferred_type"] == "date":
                    transform = {"op": "parse_date", "args": {"format": "%Y-%m-%d"}}
                    evidence.append("observed ISO dates into a timestamp column")
                elif declared == "bool" and column["inferred_type"] != "bool":
                    transform = {"op": "cast", "args": {"to": "bool"}}
                    evidence.append(f"observed {column['inferred_type']} into a boolean column")

            # A real model states this regardless of whether a target exists; the
            # stub's best equivalent is the target's own governed meaning, or -
            # absent one - the column name read as prose.
            if target and legal[target].get("means"):
                concept = legal[target]["means"]
            else:
                concept = name.replace("_", " ").strip() or None

            fields.append(
                {
                    "source": name,
                    "target": target,
                    "concept": concept,
                    "transform": transform,
                    "confidence": confidence,
                    "evidence": evidence,
                    "status": status,
                }
            )

        unknown = [f["source"] for f in fields if f["target"] is None]
        if unknown:
            notes.append(
                f"{len(unknown)} of {len(fields)} columns have no canonical target: "
                + ", ".join(unknown[:8])
                + ("…" if len(unknown) > 8 else "")
            )
        contested = [c for c in (canonical.get("contested_fields") or [])]
        if contested and unknown:
            notes.append(
                "The canonical model records these as contested/absent, which may explain "
                "some unknowns: " + ", ".join(str(c) for c in contested[:6])
            )
        return {"fields": fields, "notes": notes}

    # ----------------------------------------------------------- interpret_file
    def _interpret_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        facts = payload["observations"]
        knowledge = payload.get("context", {})
        columns = [c["name"] for c in facts["columns"]]
        lowered = {c.lower() for c in columns}

        source = (knowledge.get("source") or {}).get("content", {})
        domain = source.get("domain")
        cadence = source.get("cadence")

        claims: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []

        def risk(claim: str, *, basis: str, check: str, consequence: str) -> None:
            signals.append(
                {
                    "kind": "risk",
                    "claim": claim,
                    "basis": basis,
                    "check": check,
                    "consequence": consequence,
                }
            )

        def unknown(claim: str, *, basis: str, check: str, consequence: str) -> None:
            signals.append(
                {
                    "kind": "unknown",
                    "claim": claim,
                    "basis": basis,
                    "check": check,
                    "consequence": consequence,
                }
            )

        # Domain: governed knowledge when the feed is registered, else inferred.
        if domain:
            claims.append(
                {
                    "kind": "governed_knowledge",
                    "field": "likely_domain",
                    "value": domain,
                    "confidence": 0.99,
                    "evidence": [f"source:{knowledge['source']['citation']}"],
                }
            )
        else:
            member_like = {"memberid", "member_id"} & lowered
            claims.append(
                {
                    "kind": "inference",
                    "field": "likely_domain",
                    "value": "enrollment" if member_like else "unknown",
                    "confidence": 0.6 if member_like else 0.2,
                    "evidence": [f"column:{c}" for c in sorted(member_like)]
                    or ["no domain signal"],
                }
            )
            if not member_like:
                unknown(
                    "Domain could not be established from columns or knowledge.",
                    basis="No member-id-shaped column, and no registered source knowledge "
                    "for this feed to fall back on.",
                    check="Open the Forensic column table and confirm no domain-identifying "
                    "column was missed.",
                    consequence="This upload lands in Bronze with domain left as a guess; "
                    "nothing downstream depends on it yet.",
                )

        dataset = source.get("feed") or "unregistered feed"
        claims.append(
            {
                "kind": "inference",
                "field": "likely_dataset",
                "value": dataset,
                "confidence": 0.85 if source else 0.3,
                "evidence": [f"columns:{','.join(columns[:5])}"],
            }
        )

        keys = facts.get("candidate_keys") or []
        if keys:
            grain = "one row per " + " + ".join(keys[0])
            if cadence == "monthly":
                grain += " per monthly delivery"
            claims.append(
                {
                    "kind": "inference",
                    "field": "likely_grain",
                    "value": grain,
                    "confidence": 0.8 if cadence else 0.65,
                    "evidence": [f"candidate_key:{keys[0]}", f"row_count:{facts['row_count']}"],
                }
            )
        else:
            unknown(
                "No candidate key found; grain is unresolved.",
                basis="The profiler found no column, or combination of columns, with full "
                "row cardinality.",
                check="Open the candidate-key panel — the profiler lists every column it checked.",
                consequence="Bronze accepts the rows regardless; grain is simply not asserted.",
            )

        claims.append(
            {
                "kind": "observed_fact",
                "field": "row_count",
                "value": str(facts["row_count"]),
                "confidence": 1.0,
                "evidence": ["profile:row_count"],
            }
        )

        if facts.get("phi_candidates"):
            claims.append(
                {
                    "kind": "recommendation",
                    "field": "phi_handling",
                    "value": "Treat as PHI: " + ", ".join(facts["phi_candidates"]),
                    "confidence": 0.9,
                    "evidence": [f"column:{c}" for c in facts["phi_candidates"]],
                }
            )

        for column in facts["columns"]:
            if column["null_count"] and facts["row_count"]:
                pct = 100 * column["null_count"] / facts["row_count"]
                if pct >= 1:
                    risk(
                        f"{column['name']} is null in {pct:.1f}% of rows "
                        f"({column['null_count']}/{facts['row_count']}).",
                        basis=f"Computed directly from the profiled column ({column['name']}).",
                        check=f"Open Forensic mode and check {column['name']}'s null count "
                        "against the sample rows.",
                        consequence="The rows still land in Bronze unchanged; a mapping rule "
                        "can enforce this later if needed.",
                    )
        if facts.get("duplicate_rows"):
            risk(
                f"{facts['duplicate_rows']} fully duplicated rows present.",
                basis="Computed by the profiler by comparing every column across rows.",
                check="Open Forensic mode to see the duplicate-row count restated against the "
                "sample.",
                consequence="Duplicates are not removed at this stage; Bronze is a verbatim "
                "copy of the file.",
            )

        expected = set(source.get("expected_columns") or [])
        if expected:
            missing = sorted(expected - set(columns))
            extra = sorted(set(columns) - expected)
            if missing:
                risk(
                    "Expected columns absent: " + ", ".join(missing),
                    basis=f"The registered source knowledge for this feed lists these columns "
                    f"as expected: {', '.join(sorted(expected))}.",
                    check="Compare the column list above with the source definition in the "
                    "knowledge base.",
                    consequence="Any claim or mapping depending on a missing column is marked "
                    "unknown, not guessed.",
                )
            if extra:
                unknown(
                    "Columns not described by source knowledge: " + ", ".join(extra),
                    basis="These columns are present in the file but not listed in the "
                    "registered source for this feed.",
                    check="Check the source knowledge YAML to see if it needs updating for "
                    "this delivery.",
                    consequence="These columns still land in Bronze; they are simply not yet "
                    "interpreted against governed knowledge.",
                )

        return {"claims": claims, "signals": signals}


def build_client(settings: Settings | None = None) -> LlmClient:
    s = settings or get_settings()
    if s.llm_provider == "openai":
        return OpenAIClient(s)
    if s.llm_provider == "anthropic":
        return AnthropicClient(s)
    if s.llm_provider == "stub":
        return StubClient()
    raise LlmError(f"unknown llm_provider: {s.llm_provider}")
