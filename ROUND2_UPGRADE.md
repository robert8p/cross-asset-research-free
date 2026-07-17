# One-click Round 2 upgrade

This upgrade adds one dashboard button: **Build deeper Round 2 package**.

Round 2 backfills the 365 calendar days immediately before Round 1 began, using only the ten higher-quality bar instruments and four official US yield series. It does not query the existing untouched-test period.

## Apply it

1. Download and extract `cross_asset_research_v1.4_round2_patch.zip`.
2. Open the existing GitHub repository.
3. Select **Add file → Upload files**.
4. Drag everything inside the extracted patch folder into GitHub.
5. Allow GitHub to replace the existing files.
6. Commit with: `Add fixed-time Round 2 research`.
7. Wait for Render to redeploy the dashboard and worker.
8. Keep the worker on Pro for the backfill and export.
9. Open the dashboard and click **Build deeper Round 2 package** once.
10. When complete, click **Download Round 2 package**.

No new account, key, database, SQL or environment variable is required.

## What the button does

- Backfills a disjoint year ending exactly when Round 1 started.
- Uses BTC, DIA, EWJ, GLD, IEF, IWM, QQQ, SLV, SPY and TLT.
- Adds US 2Y, 5Y, 10Y and 30Y official yields.
- Recalculates coverage and quality for that historical window.
- Creates observed-only 15-minute bars without forward-filling missing five-minute bars.
- Generates daylight-saving-aware references for 14:00, 17:00 and 19:00 Europe/London.
- Uploads a private Round 2 research package to Supabase Storage.
- Leaves the existing untouched-test archive sealed.

## Expected duration

The backfill is materially larger than Round 1. Leave the worker on Pro and allow it to finish. A worker restart is checkpoint-safe for the exact Round 2 range.
