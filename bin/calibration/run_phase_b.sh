#!/usr/bin/env bash
# Phase B driver: for each usable repo (small->large), establish run 1 (defects),
# then verify_repro does run 2 (fresh coverage) and confirms byte-identical output.
# Resumable: skips repos already marked VERIFIED in repro-verification.json.
set -u
cd "$(dirname "$0")/../.."
RESULT=data/calibration/repro-verification.json
LOG=/tmp/phase_b.log
: > "$LOG"
for r in $(cat /tmp/verify_order.txt); do
  if [ -f "$RESULT" ] && python3 -c "import json,sys;d=json.load(open('$RESULT'));sys.exit(0 if d.get('$r',{}).get('status')=='VERIFIED' else 1)" 2>/dev/null; then
    echo "SKIP (already VERIFIED): $r" | tee -a "$LOG"; continue
  fi
  echo "=== run1 (ensure cache): $r ===" | tee -a "$LOG"
  uv run python -m bin.calibration.harness defects --repos "$r" --snapshot-days 365 --max-fixes 60 >> "$LOG" 2>&1
  echo "=== run2 (verify): $r ===" | tee -a "$LOG"
  uv run python -m bin.calibration.verify_repro "$r" >> "$LOG" 2>&1
  python3 -c "import json;d=json.load(open('$RESULT'));print('  ->', d['$r']['status'], 'labels='+str(d['$r'].get('labels_match')), 'scores='+str(d['$r'].get('scores_match')), 'drift='+str(d['$r'].get('n_score_drift')))" 2>/dev/null | tee -a "$LOG"
done
echo "PHASE_B_DONE" | tee -a "$LOG"
