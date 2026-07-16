#!/usr/bin/env bash
set -euo pipefail
python -m app incremental --provider fred --provider boe_yield_curve --provider bundesbank --history-days 14
