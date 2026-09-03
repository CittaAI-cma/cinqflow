# Dev-parity image for the CINQFLOW backend (api + worker share this image;
# which process runs is decided by the `command:` in compose, not here).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir "poetry==2.4.1"

# Preserve the `backend/` path segment so cinqflow.settings.REPO_ROOT
# (parents[3] of settings.py) resolves to /app inside the container, matching
# the sibling knowledge/ and .cinqflow/ mounts compose provides at that level.
WORKDIR /app/backend

# Dependencies first so poetry install is cached while iterating on source.
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root

# Then the package itself, so `cinqflow` (console script) resolves even
# before compose overlays the live src/ bind mount.
COPY . .
RUN poetry install

EXPOSE 8000
