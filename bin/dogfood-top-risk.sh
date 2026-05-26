#!/usr/bin/env sh
# Refresh `docs/top-risk.md` with the project's current top-risk functions.
#
# This is the test-improvement queue: each row is a candidate to add tests
# for next. The script reports — it does not gate. Run before tagging a
# release; it's also wired into CI as an artifact upload.

set -eu

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

uv run pytest --cov=src/riskratchet --cov-branch --cov-report=json:coverage.json -q

uv run riskratchet scan src \
  --coverage coverage.json \
  --top 25 --min-score 30 \
  --format markdown > docs/top-risk.md

uv run riskratchet scan src \
  --coverage coverage.json \
  --top 25 --min-score 30 \
  --format json > docs/top-risk.json

generated="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
printf '\n_Generated %s by bin/dogfood-top-risk.sh_\n' "$generated" >> docs/top-risk.md

printf '%s\n' "top-risk refreshed: docs/top-risk.md (top-25, min-score 30)"
