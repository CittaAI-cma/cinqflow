"""All environment difference lives here. Nowhere else."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CINQFLOW_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://localhost/cinqflow"

    # Workflow state and the job queue get their own schemas. The pre-existing
    # `queue` schema in this database belongs to the previous implementation and is
    # left untouched, hence `jobq`.
    workflow_schema: str = "workflow"
    queue_schema: str = "jobq"

    # The physical namespace the SILVER_RAW layer renders into. `silver_raw` in this
    # database holds the previous implementation's `members` table, whose shape this
    # build does not populate (NOT NULL columns it has no mapping for), so the layer
    # renders next to it rather than into it - the same reason the queue is `jobq`.
    # The logical layer name in the contract is unchanged.
    silver_schema: str = "silver"

    # A message claimed longer than this is assumed to belong to a dead worker
    # and returns to `pending` on the next claim.
    queue_claim_timeout_seconds: int = 300

    landing_root: Path = REPO_ROOT / ".cinqflow" / "landing"
    knowledge_root: Path = REPO_ROOT / "knowledge"

    # Intelligence. `stub` keeps the flow deterministic and offline; `anthropic`
    # and `openai` call a real provider and require llm_api_key. `openai` uses
    # Structured Outputs (schema-constrained decoding), so llm_model may be a
    # fine-tune id (e.g. "ft:gpt-4o-2024-08-06:org::id") of a base model that
    # supports it.
    llm_provider: str = "stub"  # stub | anthropic | openai
    llm_model: str = "claude-sonnet-5"
    llm_api_key: str | None = None
    llm_max_tokens: int = 2048

    # Bounded evidence: how many sample values per column the profiler keeps.
    profile_sample_values: int = 5
    profile_sample_rows: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
