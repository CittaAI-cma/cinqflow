"""Environment-variable wiring: catches drift between "this setting exists
and is documented" and "it actually reaches the running container."

Every test here is a regression guard for a real incident hit while
deploying to Railway, not a hypothetical:

- `CINQFLOW_LLM_MAX_TOKENS` was never forwarded by docker-compose.yml's
  backend-env anchor, so setting it in compose/.env had zero effect - the
  container silently used settings.py's 2048 default and every mapping
  proposal truncated mid-response. Nothing failed loudly; it just produced
  a worse answer. `test_docker_compose_forwards_every_deploy_varying_setting`
  exists so a var falling out of that anchor fails a test instead of a demo.
- The same class of bug, one layer up: a var missing from .env.example means
  nobody knows to set it on a fresh clone or a new Railway service.
- Multiple CORS origins (http://localhost:3000 vs http://127.0.0.1:3000)
  broke silently in the browser with no server-side signal at all - the
  parsing itself needs direct coverage, independent of any HTTP round trip.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from cinqflow.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Settings that legitimately differ per deploy target (Docker Compose vs.
#: Railway vs. native dev) and therefore MUST be forwarded through compose's
#: backend-env anchor and documented in every .env.example - as opposed to
#: schema/timeout/sampling settings, which are fine left at their code
#: default everywhere and don't belong in this list.
DEPLOY_VARYING_SETTINGS = [
    "CINQFLOW_DATABASE_URL",
    "CINQFLOW_LLM_PROVIDER",
    "CINQFLOW_LLM_MODEL",
    "CINQFLOW_LLM_API_KEY",
    "CINQFLOW_LLM_MAX_TOKENS",
    "CINQFLOW_CORS_ALLOWED_ORIGINS",
]


def _backend_env_anchor_text() -> str:
    """The `&backend-env` block as raw text, not parsed YAML.

    `docker compose` never expands `${VAR}` bash-interpolation syntax when a
    tool merely loads the YAML - only `docker compose` itself does - so
    `yaml.safe_load` gives back the literal `${CINQFLOW_LLM_MAX_TOKENS:-8000}`
    strings. That's fine here: presence of the variable *name* in that text
    is exactly what's being checked, and staying at the text level (instead
    of re-implementing bash's `${VAR:-default}` expansion) is what keeps this
    test honest about what it can and can't verify.
    """
    compose_path = REPO_ROOT / "compose" / "docker-compose.yml"
    doc = yaml.safe_load(compose_path.read_text())
    migrate_env = doc["services"]["migrate"]["environment"]
    # The anchor is a mapping in the parsed YAML; re-serializing it back to
    # text is a simple, dependency-free way to search it for `${VAR...}`
    # references without hand-rolling bash-substitution parsing.
    return yaml.safe_dump(migrate_env)


def test_docker_compose_forwards_every_deploy_varying_setting():
    anchor_text = _backend_env_anchor_text()
    missing = [name for name in DEPLOY_VARYING_SETTINGS if name not in anchor_text]
    assert not missing, (
        f"compose/docker-compose.yml's backend-env anchor never forwards "
        f"{missing} - setting these in compose/.env would silently have no "
        f"effect (this is exactly how CINQFLOW_LLM_MAX_TOKENS went missing)"
    )


#: compose/.env.example never sets CINQFLOW_DATABASE_URL directly - unlike
#: native dev, docker-compose.yml synthesizes it itself from POSTGRES_USER/
#: PASSWORD/DB (see the &backend-env anchor), so there's nothing for a user
#: to set here for that one variable.
ENV_EXAMPLE_EXCEPTIONS = {
    "compose/.env.example": {"CINQFLOW_DATABASE_URL"},
}


def test_env_example_files_document_every_deploy_varying_setting():
    for rel_path in ("backend/.env.example", "compose/.env.example"):
        text = (REPO_ROOT / rel_path).read_text()
        exceptions = ENV_EXAMPLE_EXCEPTIONS.get(rel_path, set())
        missing = [
            name
            for name in DEPLOY_VARYING_SETTINGS
            if name not in text and name not in exceptions
        ]
        assert not missing, f"{rel_path} never mentions {missing}"


def test_cors_origins_splits_multiple_comma_separated_origins():
    s = Settings(cors_allowed_origins="http://localhost:3000,http://127.0.0.1:3000")
    assert s.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_cors_origins_strips_whitespace_around_entries():
    s = Settings(cors_allowed_origins=" http://localhost:3000 , http://127.0.0.1:3000 ")
    assert s.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_cors_origins_drops_empty_entries():
    # A trailing comma (easy to leave behind editing a Raw Editor value) must
    # not produce an empty-string origin - FastAPI's CORSMiddleware treats
    # "" as a literal, useless allowlist entry rather than ignoring it.
    s = Settings(cors_allowed_origins="http://localhost:3000,")
    assert s.cors_origins == ["http://localhost:3000"]


def test_cors_origins_single_value_still_a_list():
    s = Settings(cors_allowed_origins="http://localhost:3000")
    assert s.cors_origins == ["http://localhost:3000"]
