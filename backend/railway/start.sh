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
# IPv6-only in legacy environments and dual-stack (A + AAAA) in environments
# created after 2025-10-16, so the api process must accept both IPv4 and
# IPv6 to stay reachable regardless of which DNS records a caller resolves.
# uvicorn's CLI cannot dual-stack bind at all - `--host ::` makes it
# IPv6-only and breaks the public domain instead of fixing private
# networking. A single hypercorn `[::]` bind was assumed to be dual-stack
# (accepting IPv4-mapped connections too), but that depends on the
# container's IPV6_V6ONLY socket default and was NOT reliable here: once
# private DNS started also returning an IPv4 address, IPv4 connection
# attempts got ECONNREFUSED even though the process was up and the IPv6
# bind worked fine. Passing two explicit `--bind` flags removes the
# ambiguity - one socket per family. Local dev and Docker Compose are
# unaffected - they still run plain uvicorn (see Makefile,
# backend/Dockerfile), since neither needs Railway's private network.
poetry run hypercorn cinqflow.api.app:app --bind "0.0.0.0:${PORT:-8000}" --bind "[::]:${PORT:-8000}" &
poetry run cinqflow work &

wait -n
code=$?
echo "one of api/worker exited (code $code); stopping the other so Railway restarts the whole service"
kill $(jobs -p) 2>/dev/null || true
exit "$code"
