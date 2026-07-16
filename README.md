> **v1.3 note:** Long-running collection now runs in a separate Render background worker. See `WORKER_FIX.md`.

# Cross-Asset Research Collector — No-Code Edition

This edition is designed for a non-technical user.

You do **not** run commands, edit Python, execute SQL, create database tables or change Render start commands.

After the repository is deployed, a password-protected browser dashboard performs the workflow:

1. Creates and upgrades the Supabase tables.
2. Freezes a 90-day dataset with the final 30 days quarantined.
3. Checks Alpaca, Coinbase, FRED, Supabase and every configured instrument.
4. Runs a two-day SPY test.
5. Collects the complete dataset.
6. Runs discovery-period quality checks only.
7. Generates and uploads the discovery, encrypted untouched-test and restricted archival packages.
8. Provides a download button only for the discovery package.

## Your only tasks

1. Create free Alpaca and FRED keys.
2. Create a Supabase project and copy two values.
3. Upload this folder to a private GitHub repository.
4. Create a Render Blueprint and paste six values when prompted.
5. Open the Render URL and click **Run complete setup**.

Open `NO_CODE_SETUP.md` for the exact click-by-click instructions.

## Active free-data universe

- 18 US-listed ETF proxies using Alpaca's free IEX feed at native five-minute frequency.
- Coinbase BTC-USD spot five-minute OHLCV.
- FRED/ALFRED US 2Y, 5Y, 10Y and 30Y official daily yields.
- Optional Bank of England and Bundesbank daily yield snapshots.

Every ETF remains explicitly labelled as a proxy. The system never relabels VIXY as VIX, Treasury ETF prices as yields, or US-listed international ETFs as native Asian/European sessions.

## Security

- The GitHub repository should be private.
- Credentials are entered only into Render during Blueprint creation.
- The dashboard uses HTTP Basic authentication with username `admin` and your chosen password.
- The Supabase Storage bucket is created private.
- The untouched-test and full archives are encrypted using your dashboard password and are not linked in the dashboard.
- The discovery package is served through a temporary signed URL.

## Recovery

Historical collection is checkpointed and duplicate-safe. If Render restarts or a provider temporarily fails, open the dashboard and click **Resume safely**. Previously completed rows are not duplicated.
