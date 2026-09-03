"""Versioned prompts. The version is recorded in every artifact's provenance."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent

#: name -> version. Bumping a prompt means adding a file and updating this map.
REGISTRY = {"interpret_file": 1, "recommend_mapping": 3}


def load(name: str) -> tuple[str, str]:
    """Returns (text, citation) e.g. ("You interpret...", "interpret_file@1")."""
    version = REGISTRY[name]
    text = (_DIR / f"{name}_v{version}.md").read_text()
    return text, f"{name}@{version}"
