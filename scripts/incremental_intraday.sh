#!/usr/bin/env bash
set -euo pipefail
python -m app incremental --provider alpaca --provider coinbase --history-days 2
