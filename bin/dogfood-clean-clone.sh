#!/usr/bin/env sh
set -eu

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/riskratchet-dogfood.XXXXXX")"

git clone "$root" "$tmp/repo"
cd "$tmp/repo"

uv run pytest --cov --cov-report=json:coverage.json -q
uv run riskratchet baseline src --coverage coverage.json --output .riskratchet.json
uv run riskratchet scan src --coverage coverage.json --format json >/dev/null
uv run riskratchet check src --coverage coverage.json --baseline .riskratchet.json

printf '%s\n' "dogfood clean clone passed in $tmp/repo"
