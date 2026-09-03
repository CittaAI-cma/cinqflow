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

# hypercorn, not uvicorn: Railway's private network (*.railway.internal) is
# IPv6-only, so the api process must bind dual-stack to stay reachable both
# publicly (IPv4) and over private networking (IPv6). uvicorn's CLI cannot
# dual-stack bind at all - `--host ::` makes it IPv6-only and breaks the
# public domain instead of fixing private networking. hypercorn's `[::]`
# bind is dual-stack, so one process serves both. Local dev and Docker
# Compose are unaffected - they still run plain uvicorn (see Makefile,
# backend/Dockerfile), since neither needs Railway's private network.
#
# NOTE: do NOT also add an explicit `--bind 0.0.0.0:...` alongside this -
# this container's IPv6 socket already has bindv6only=0 (confirmed by a
# 2026-09-03 incident: adding a second 0.0.0.0 bind crashed the process at
# startup with "OSError: [Errno 98] Address already in use", because the
# [::] bind already claims the IPv4 space too). The ECONNREFUSED private-
# networking failure that incident was chasing had a different cause -
# see git history around 2026-09-03 for the investigation.
poetry run hypercorn cinqflow.api.app:app --bind "[::]:${PORT:-8000}" &
poetry run cinqflow work &

wait -n
code=$?
echo "one of api/worker exited (code $code); stopping the other so Railway restarts the whole service"
kill $(jobs -p) 2>/dev/null || true
exit "$code"
