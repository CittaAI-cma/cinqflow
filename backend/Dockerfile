# ─────────────────────────────────────────────────────────────────────────────
# cinqflow / backend
#
# The image is built from the requirements LAYERING, not from one flat install,
# because that layering is the architecture (see requirements.txt): a package
# absent from the image cannot be imported by accident, which is what makes
# "no vendor SDK in core" true by construction rather than by lint.
#
#   --build-arg EXTRA_REQUIREMENTS=requirements/agents.txt      Wave 2 LangGraph
#   --build-arg EXTRA_REQUIREMENTS=requirements/azure.txt       rungs 3-4
#   --build-arg EXTRA_REQUIREMENTS=requirements/databricks.txt  rung 2+
#
# The default image is rung 0.5/1: what runs today, with no Azure subscription
# and no Databricks workspace.
# ─────────────────────────────────────────────────────────────────────────────
ARG PYTHON_VERSION=3.12

# ── builder ──────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is for the few sdists that still compile; it stays in the
# BUILDER and never reaches the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# THE SAME PATH THE RUNTIME USES, and that is the whole reason it is set here.
# `pip install -e .` writes the ABSOLUTE source path into the venv, so a
# builder that installs from `/` produces a venv that looks for the package at
# `/src` — which exists in the builder and not in the runtime image. The
# failure is `ModuleNotFoundError: No module named 'cinqflow'` at start, from
# an image whose build succeeded.
WORKDIR /app

# Requirements first, source second — the dependency layer is then cached
# across every source edit, which is most of them.
COPY requirements.txt ./
COPY requirements/ ./requirements/
RUN pip install -r requirements.txt

# The PHI scrubber is a REAL pin, not a stand-in, so its model is part of the
# image. `en_core_web_lg` is ~600 MB; a build that wants a smaller image passes
# `--build-arg SPACY_MODEL=en_core_web_sm` and accepts the lower recall, or
# `--build-arg SPACY_MODEL=` to skip it and leave `phi_scrub` unenergized.
ARG SPACY_MODEL=en_core_web_lg
RUN if [ -n "$SPACY_MODEL" ]; then python -m spacy download "$SPACY_MODEL"; fi

# Rung-specific layers, absent unless asked for.
ARG EXTRA_REQUIREMENTS=""
RUN if [ -n "$EXTRA_REQUIREMENTS" ]; then pip install -r "$EXTRA_REQUIREMENTS"; fi

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-deps -e .

# ── runtime ──────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

# libpq for psycopg, curl for the healthcheck. Nothing else.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 cinqflow

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=cinqflow:cinqflow pyproject.toml ./
COPY --chown=cinqflow:cinqflow src/ ./src/
COPY --chown=cinqflow:cinqflow profiles/ ./profiles/
COPY --chown=cinqflow:cinqflow scripts/ ./scripts/
COPY --chown=cinqflow:cinqflow conformance/ ./conformance/

# The landing zone is a VOLUME in every compose file. Created here so the
# directory exists and is writable before anything mounts over it.
RUN mkdir -p /app/.cinqflow/landing /app/.cinqflow/drop \
 && chown -R cinqflow:cinqflow /app/.cinqflow

USER cinqflow

# Which environment this container is: the profile is the ONLY thing that
# differs between local, dev and prod. Compose overrides it; nothing else does.
ENV CINQFLOW_PROFILE=profiles/local.yaml \
    CINQFLOW_HOST=0.0.0.0 \
    CINQFLOW_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${CINQFLOW_PORT}/healthz" || exit 1

# `api.local` is the REAL server — Postgres metadata, Postgres control, a real
# landing zone. `api.dev` (the in-memory picture) is selected in the local
# overlay by overriding this command, never by an env flag here.
CMD ["sh", "-c", "python -m cinqflow.api.local --host \"$CINQFLOW_HOST\" --port \"$CINQFLOW_PORT\" --profile \"$CINQFLOW_PROFILE\""]
