#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# The Wave-0 exit. THE DEMO IS THE TEST RUN.
#
# Every line below is asserted in CI (`twin-e2e`) and runnable by hand. That is
# the point: a demo that is not a test rots between showings, and a test that is
# not a demo never gets watched.
#
#   ./scripts/wave0-demo.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."
PY=".venv/bin/python"
export PYTHONPATH=src
[ -f .env ] && set -a && . ./.env && set +a

say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }

say "1 · The laws, mechanically"
.venv/bin/ruff check src tests conformance
.venv/bin/mypy src
$PY conformance/lint_core_purity.py
.venv/bin/lint-imports

say "2 · The whole suite — unit, contract, pipeline, invariants"
.venv/bin/python -m pytest -q

say "3 · Certify the socket, pin by pin"
$PY conformance/kit.py

say "4 · Stand up the rung-0.5 plane, then take it back"
$PY -m cinqflow.installer.cli doctor --profile profiles/local.yaml

say "5 · Ask what a run did — every claim cited"
$PY -m cinqflow.installer.cli ask "why did batch 8842 lose rows?"

say "6 · Ask what the feed is configured to do"
$PY -m cinqflow.installer.cli ask "what does the fidelis-downstate-roster feed do?"

say "7 · Ask it to retry a batch — REFUSED, explained, logged"
$PY -m cinqflow.installer.cli ask "retry batch 8842"

say "8 · Ask for a member — DECLINED BY NAME (CF-V4-E14-04)"
$PY -m cinqflow.installer.cli ask "what is a member's date of birth?"

say "9 · The workspace, in a browser"
echo "   cd ui && npm run dev     # then sign in at http://localhost:3000/signin"
echo "   cd ui && npm test        # 24 Playwright assertions of the same script"

printf '\n\033[1;32mWave 0 · green\033[0m\n'
