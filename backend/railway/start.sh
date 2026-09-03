#!/bin/bash
# Railway-only combined process: api + worker in one container, one Railway
# service, so only this one service needs the shared /data volume (Railway
# does not support a volume attached to more than one service). Everywhere
# else - native dev, Docker Compose - api and worker stay two independent
# processes; this script exists only for this deployment target.
#
# If either process exits, this exits too (via `wait -n`), so Railway's
# restart policy restarts BOTH together. That is the actual behavior change
# from running them as separate services: they now share fate on crash and
# scale as one unit. See docs/blueprints/checklist.md's Railway deployment
# note for the full rationale.
set -e

poetry run uvicorn cinqflow.api.app:app --host 0.0.0.0 --port "${PORT:-8000}" &
poetry run cinqflow work &

wait -n
code=$?
echo "one of api/worker exited (code $code); stopping the other so Railway restarts the whole service"
kill $(jobs -p) 2>/dev/null || true
exit "$code"
