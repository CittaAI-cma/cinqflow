"""`CanonicalModel.required_targets`: the minimum a spec touching an entity must
map. Derived from `primary_key`, already-governed data - no new knowledge shape."""

from __future__ import annotations

from pathlib import Path

from cinqflow.knowledge.canonical import EMPTY, load_canonical
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"


def _enrollment(tmp_path) -> object:
    s = Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")
    return load_canonical(YamlKnowledgeProvider(s), "enrollment")


def test_single_column_identity_is_required(tmp_path):
    canonical = _enrollment(tmp_path)
    assert canonical.required_targets(["members"]) == ("members.source_system_id",)


def test_system_populated_key_columns_never_count(tmp_path):
    """`members.source_system` is part of the declared primary key but is
    platform-populated - a mapping never fills it in, so it must never be
    demanded of one."""
    canonical = _enrollment(tmp_path)
    assert "members.source_system" not in canonical.required_targets(["members"])


def test_composite_keys_are_not_enforced_component_by_component(tmp_path):
    """`members_enrollment_segments`' key has more than one mappable column -
    which of those a given feed can supply is feed-dependent judgment, not a
    blanket rule, so none of them are reported as required."""
    canonical = _enrollment(tmp_path)
    assert canonical.required_targets(["members_enrollment_segments"]) == ()


def test_untouched_entities_contribute_nothing(tmp_path):
    """A feed that never maps into an entity is never asked to satisfy it."""
    canonical = _enrollment(tmp_path)
    assert canonical.required_targets([]) == ()
    assert canonical.required_targets(["no_such_table"]) == ()


def test_multiple_touched_entities_each_contribute_their_own_identity(tmp_path):
    canonical = _enrollment(tmp_path)
    required = canonical.required_targets(["members", "members_addresses"])
    assert "members.source_system_id" in required
    # members_addresses' key is composite too - contributes nothing of its own
    assert not any(t.startswith("members_addresses.") for t in required)


def test_empty_canonical_model_has_no_required_targets():
    assert EMPTY.required_targets(["members"]) == ()
