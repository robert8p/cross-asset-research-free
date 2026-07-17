# Build report — v1.4 Round 2

## Added

- One-click dashboard action for a deeper Round 2 package.
- One-year disjoint historical backfill ending at the Round 1 start boundary.
- Frozen ten-instrument higher-quality bar universe plus four US yield series.
- Observed-only 15-minute aggregation with completeness flags and no forward fill.
- Europe/London daylight-saving-aware decision-time reference file.
- Round 2 analysis prompt and predeclared research plan.
- Private Supabase upload and secure dashboard download.
- Checkpoint-safe restart behaviour for the exact Round 2 historical request.

## Integrity

- Existing untouched-test period is not queried by the Round 2 backfill or exporter.
- Round 2 is labelled retrospective historical corroboration, not external validation.
- No current test archive is opened or repackaged.

## Validation

- Python compilation: pass.
- Automated tests: 38 passed.
