# Build and integrity report — No-Code Free-Data Edition

## Build verdict

**Pass for software packaging and automated tests. Live-data acceptance remains credential-dependent.**

## What changed

The prior command-driven deployment was replaced by one Render web service with a password-protected control dashboard.

The user no longer needs to:

- execute Python commands;
- edit a Render start command;
- run SQL;
- create Supabase tables or buckets;
- calculate and copy split dates;
- inspect Render logs during normal operation;
- locate the discovery archive manually in Supabase Storage.

The user enters six secret values once during Render Blueprint creation and then clicks **Run complete setup**.

## Automatic workflow

- Idempotent schema migration on service startup.
- One-time frozen 90-day window with final 30 days quarantined.
- Required-source and instrument preflight.
- Coinbase BTC smoke test.
- Two-day SPY insertion/idempotency test.
- Complete historical backfill with checkpoints.
- Discovery-only quality checks.
- Separate discovery, encrypted untouched-test and restricted full exports.
- Private Supabase Storage bucket creation and upload.
- Temporary signed download URL for the discovery package only.
- Safe resume after interruption.

## Security controls

- Private GitHub repository expected.
- Render prompts for secrets; no credentials are committed.
- One authenticated dashboard, fixed username `admin`.
- Password comparison uses constant-time comparison.
- Private Storage bucket enforced.
- Untouched and full archives encrypted.
- Untouched archive is never linked by the dashboard.
- Dashboard status excludes untouched-period market summaries and counts.

## Validation

- 24 automated tests pass.
- Python compilation passes.
- Blueprint test confirms one Render service and exactly six prompted values.
- Supabase URL derivation from the transaction-pooler URI is tested.
- Final CLI JSON extraction for archive metadata is tested.
- Existing split-isolation, calendar, quality, checkpoint, resampling and Alpaca tests continue to pass.

## Not completed without user credentials

- Live Alpaca/FRED authentication.
- Live Coinbase connectivity from the deployed environment.
- Supabase table creation in the user's project.
- Real 90-day collection and measured coverage.
- Final archive generation and upload.

No fabricated market rows are included.
