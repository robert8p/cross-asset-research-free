#!/usr/bin/env bash
set -euo pipefail

if [[ "${COLLECTOR_ENABLED:-false}" != "true" ]]; then
  echo '{"status":"disabled","reason":"Set COLLECTOR_ENABLED=true after migration, preflight and historical backfill."}'
  exit 0
fi

# Refresh free five-minute sources with overlap; conflict-safe upserts make this idempotent.
python -m app incremental \
  --provider alpaca \
  --provider coinbase \
  --history-days 2

UTC_HOUR="$(date -u +%H)"
UTC_WEEKDAY="$(date -u +%u)" # 1=Monday, 7=Sunday

# Refresh free official daily observations once each weekday.
# UK/German current-history snapshots retain retrieval-time availability and are never backdated.
if [[ "$UTC_HOUR" == "14" && "$UTC_WEEKDAY" -le 5 ]]; then
  python -m app incremental \
    --provider fred \
    --provider boe_yield_curve \
    --provider bundesbank \
    --history-days 21
fi

# Re-run quality only against the frozen discovery cutoff.
if [[ "$UTC_HOUR" == "03" && "$UTC_WEEKDAY" == "7" ]]; then
  python -m app quality
fi
