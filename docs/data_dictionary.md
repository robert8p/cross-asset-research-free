# Data dictionary

## `instruments.csv` / `instruments`

One row per canonical instrument. In the free-data edition, Alpaca rows are US-listed ETF proxies and no raw futures contracts are expected. The most important distinction fields are `instrument_type`, `provider_symbol`, `contract_code`, `is_continuous`, `continuous_method`, `volume_type`, `data_frequency` and `methodological_limitations`.

## `bars_raw.parquet` / `market_bars`

| Field | Meaning |
|---|---|
| `instrument_id` | Stable UUID foreign key |
| `canonical_symbol` | Human-readable canonical identifier |
| `interval` | Stored bar interval; normally `5m` |
| `provider_timestamp` | Original provider timestamp representation |
| `bar_open_timestamp_utc` | Inclusive UTC bar start |
| `bar_close_timestamp_utc` | Exclusive UTC bar end |
| `open/high/low/close` | Unadjusted observed prices |
| `volume` | Provider-defined volume. For Alpaca free bars this is genuine IEX venue volume only, not consolidated US-market volume |
| `vwap`, `trade_count`, `bid`, `ask`, `mid`, `open_interest` | Nullable source fields; never fabricated |
| `source`, `source_symbol` | Provider and requested symbol |
| `contract_code` | Actual raw futures contract when known |
| `expiry_date` | Provider definition-schema expiry date when available; never inferred from the symbol |
| `is_continuous` | True for provider continuous mapping; false for raw contract or spot/index rows |
| `is_roll_affected` | Roll-boundary flag |
| `exchange_trading_date` | Calendar session label; overnight futures can map to the following trading date |
| `session_type` | `regular_session`, `extended_session`, `scheduled_break_observation`, `outside_scheduled_market` or an explicit unclassified state |
| `is_regular_session`, `is_extended_session` | Calendar-derived flags; no market closure is converted into a bar |
| `minutes_since_session_open`, `minutes_until_session_close` | Calendar-relative minutes for bars inside the configured normal session; null for unclassified/extended/break bars |
| `is_holiday`, `is_shortened_session` | Version-locked calendar flags |
| `is_partial_bar` | Fewer source observations than expected inside the five-minute bucket |
| `is_stale` | Quality flag for repeated inactive-looking values |
| `quality_status` | Pre-insert quality disposition |

## `bars_research_aligned.parquet`

Contains explicit five-minute alignment slots. A row with `is_observed=false` is a missing alignment state, not a fabricated bar. Price and volume remain null. `return_5m` exists only for exactly adjacent observed slots.

## `yield_observations` / `yields.parquet`

| Field | Meaning |
|---|---|
| `observation_date` | Date represented by the official statistic |
| `observation_timestamp_utc` | Conservative timestamp from which research may use the value |
| `maturity` | 2Y, 5Y, 10Y or 30Y |
| `yield_value` | Yield in percentage points, not decimal return |
| `published_at` | Known or conservative publication availability |
| `vintage_date` | Source vintage |
| `is_revised` | Whether the observation belongs to a later vintage |

## `yield_curve_features.parquet`

Curve spreads and changes in basis points. Positive spread change means steepening; negative means flattening.

## `market_sessions.csv`

One row per configured instrument/trading date, with UTC open/close, optional break metadata, shortened-session status, calendar source/version context and limitations. The schedule is a reproducible research input, not a live exchange-status feed.

## `futures_rolls.csv`

The schema is retained for compatibility with a later native-futures edition. The active free-data catalogue contains no futures contracts, so this file should be empty. Any row requires investigation.

## `data_quality_results.csv`

Every issue has an instrument, optional timestamp, type, severity, observed value, expected condition, resolution and disposition. `retained` means the unusual source value remains available for research with a warning.
