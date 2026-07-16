# Acceptance matrix — free-data edition

| Requirement | Status | Acceptance evidence |
|---|---|---|
| Explicit instrument catalogue | Implemented | 27 enabled entries; 18 Alpaca instruments are explicitly `etf_proxy` |
| Free five-minute cross-asset coverage | Implemented with substitutions | Alpaca preflight returns real rows for each required ETF proxy; Coinbase returns BTC bars |
| Native futures and cash indices | Not achieved with free sources | No claim of equivalence; original catalogue retained only as reference |
| Genuine full-market volume | Not achieved | Alpaca volume is labelled `genuine_single_venue_partial` |
| Official US yields | Implemented | FRED/ALFRED point-in-time observations for 2Y/5Y/10Y/30Y |
| UK/German yields | Prospective-safe only | Retrieval-time availability; excluded from historical information set |
| Supabase schema | Implemented | Migration creates all tables and indexes |
| Idempotent loading | Implemented and tested | Unique keys plus bulk conflict-safe upserts |
| UTC and session labels | Implemented and tested | XNYS and 24/7 calendar tests |
| No closure forward-fill | Implemented and tested | Alignment preserves null missing states |
| Futures rolls | Not applicable to active free catalogue | `futures_rolls` expected empty |
| Discovery/test quarantine | Implemented and tested | Export isolation tests and explicit cutoff |
| Export hashes and manifests | Implemented | SHA-256 for every generated file |
| Deployment without coding | Implemented | `docs/setup_guide.md` and Render Blueprint |
| Real data collected | Pending user credentials/deployment | Must be proven by `preflight`, backfill logs and Supabase rows |
| Discovery package ready | Pending | Requires completed backfill and discovery-only quality run |
