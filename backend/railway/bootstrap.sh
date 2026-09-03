#!/bin/bash
# Railway pre-deploy command. Runs once per deploy, before start.sh, in its
# own container with the volume already mounted.
#
# Two idempotent steps:
# 1. Seed CINQFLOW_KNOWLEDGE_ROOT from the image's baked-in copy, but only if
#    the volume path doesn't exist yet - a fresh volume on first deploy has no
#    governed knowledge on it at all. Never overwrites on later deploys, so
#    approved-mapping exports written by G2 promotions (`knowledge/mappings/
#    approved/*.yaml`) accumulate on the volume instead of being clobbered by
#    the next release's baked-in copy.
# 2. `cinqflow install` - already idempotent (CREATE SCHEMA/TABLE IF NOT
#    EXISTS), same command `make install-db` runs locally.
set -e

if [ -n "$CINQFLOW_KNOWLEDGE_ROOT" ] && [ ! -d "$CINQFLOW_KNOWLEDGE_ROOT" ]; then
    echo "empty knowledge volume at $CINQFLOW_KNOWLEDGE_ROOT - seeding from the image"
    mkdir -p "$(dirname "$CINQFLOW_KNOWLEDGE_ROOT")"
    cp -r /app/knowledge-seed "$CINQFLOW_KNOWLEDGE_ROOT"
fi

poetry run cinqflow install
