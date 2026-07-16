# Cross-Asset Intraday Leading-Indicator Research System

Act as a senior market-data engineer, quantitative research architect, Python developer and research-integrity auditor.

I cannot code. Build the complete working system, explain every action in simple step-by-step language, and minimise anything I need to configure manually.

## 1. Objective

Create a production-quality but cost-conscious data pipeline that:

1. Downloads detailed historical market data for a defined cross-asset universe.
2. Collects five-minute OHLCV bars wherever genuine five-minute data is available.
3. Loads the data into Supabase.
4. Runs comprehensive data-quality and integrity checks.
5. Creates research-ready export packages that can be uploaded to ChatGPT.
6. Creates a separate analysis prompt instructing ChatGPT to perform genuinely blank-canvas research into possible leading indicators.
7. Preserves an untouched chronological test period that must not be inspected during discovery.
8. Does not build a trading bot, execute trades, connect to a brokerage account or provide live-trading functionality.

The ultimate research question is:

> Are there observable changes in one market that consistently and materially precede movements in another market, at a frequency sufficiently above chance to justify further prospective testing?

Do not assume that a relationship exists. A valid conclusion may be that no reliable leading indicator has been found.

---

# 2. Required market universe

Create a canonical instrument catalogue. For every instrument, record:

* canonical name
* asset class
* economic exposure
* source provider
* provider symbol
* instrument type
* currency
* exchange
* exchange timezone
* normal trading session
* extended trading session
* contract expiry, where relevant
* whether it is a spot price, cash index, ETF, future, continuous future, yield or proxy
* whether volume is genuine, unavailable or proxy-derived
* data frequency
* data licence or redistribution restrictions
* any known methodological limitations

## 2.1 Energy

Collect:

* WTI crude oil
* Brent crude oil

For intraday analysis, prefer the relevant liquid futures contracts.

Preserve both:

* individual contract data
* a clearly documented continuous series

Never silently splice futures contracts together. Record every roll date and the method used to create the continuous series.

## 2.2 Precious metals

Collect:

* Gold
* Silver

Prefer liquid futures for intraday price and volume information.

Where available, store both the raw contracts and a continuous series.

## 2.3 Cryptocurrency

Collect:

* Bitcoin quoted in US dollars

Prefer BTC-USD spot data from a major regulated venue with genuine five-minute OHLCV data.

Do not combine prices from multiple venues without explicitly recording and validating the methodology.

## 2.4 Major equity indices

Core universe:

* S&P 500
* Nasdaq 100
* Dow Jones Industrial Average
* Russell 2000
* VIX
* FTSE 100
* DAX 40
* Euro Stoxx 50
* Nikkei 225
* Hang Seng

For intraday analysis, prefer liquid index futures where they provide longer trading hours and genuine volume.

Where economically and technically useful, retain both:

* cash-index values
* corresponding futures-market data

Do not treat an ETF, CFD, future and cash index as interchangeable. Every substitution must be explicitly labelled.

## 2.5 Interest rates and yields

Required US curve points:

* 2-year
* 5-year
* 10-year
* 30-year

Preferred additional markets, subject to reliable availability:

* UK 2-year and 10-year
* German 2-year and 10-year

Store official yield observations at their genuine published frequency.

Because official government yields may not be available at five-minute resolution, use the corresponding liquid government-bond futures as separate intraday rate-market signals.

For the US, assess appropriate contracts representing approximately:

* 2-year Treasury exposure
* 5-year Treasury exposure
* 10-year Treasury exposure
* long-bond exposure

Do not label futures prices as “yields.”

Store them as rate-futures prices and derive changes or implied rate-direction signals separately.

Create derived curve features only from information available at that timestamp, including:

* 2s10s
* 5s30s
* 2s5s
* 5s10s
* curve steepening or flattening
* changes in curve slope

Document every formula and sign convention.

---

# 3. Historical period

Preferred design:

* Download at least 90 complete calendar days.
* Use the first 60 days for discovery and validation.
* Quarantine the final 30 days as an untouched test period.

Minimum acceptable fallback:

* Download 60 complete calendar days.
* Quarantine the final 20% chronologically as an untouched test period.

Do not inspect, summarise, profile, chart, count, analyse or use the untouched test observations during discovery.

The untouched test export must be a separate file.

If the market-data provider offers more history at little or no additional cost, collect 180 days, while still maintaining a separately quarantined test period.

Report:

* calendar start and end date
* number of trading days per instrument
* number of valid five-minute observations
* number of expected observations
* percentage coverage
* gaps by instrument and session

---

# 4. Data-source feasibility assessment

Before developing the pipeline, produce a source-feasibility table.

For each required instrument, compare suitable current providers on:

* five-minute historical availability
* maximum lookback
* raw versus adjusted data
* futures-contract coverage
* continuous-futures availability
* volume availability
* official yield availability
* authentication requirements
* API limits
* subscription cost
* exchange-data fees
* personal-use restrictions
* export and analysis rights
* reliability
* timestamp conventions
* licensing restrictions

Source selection principles:

1. Prefer direct, documented APIs over webpage scraping.
2. Prefer genuine traded instruments over indicative prices.
3. Prefer one consistent cross-asset provider where economically reasonable.
4. A mixed-source architecture is acceptable where it materially improves quality or reduces cost.
5. Verify current API documentation, pricing and historical-data limits before implementation.
6. Never claim that a free source supports the requirements unless this has been tested.
7. Never silently fall back to daily data when five-minute data was requested.
8. Never silently substitute ETFs or CFDs for indices, commodities or futures.
9. Never use synthetic or demonstration data in the final research export.
10. Do not violate provider terms or data-redistribution restrictions.

Where a paid service is unavoidable, state:

* the precise subscription or entitlement required
* its current cost
* why it is needed
* the lowest-cost viable alternative
* what quality would be lost under the cheaper alternative

Allow provider credentials to be supplied through environment variables. Do not hard-code credentials.

---

# 5. Data model

Build a Supabase/Postgres schema with at least the following tables.

## 5.1 `instruments`

Suggested fields:

* `instrument_id`
* `canonical_symbol`
* `canonical_name`
* `asset_class`
* `subcategory`
* `instrument_type`
* `economic_exposure`
* `provider`
* `provider_symbol`
* `exchange`
* `exchange_timezone`
* `currency`
* `price_unit`
* `contract_multiplier`
* `contract_code`
* `expiry_date`
* `is_continuous`
* `continuous_method`
* `volume_type`
* `data_frequency`
* `active_from`
* `active_to`
* `metadata_json`
* `created_at`
* `updated_at`

## 5.2 `market_bars`

Suggested fields:

* `instrument_id`
* `interval`
* `bar_open_timestamp_utc`
* `bar_close_timestamp_utc`
* `open`
* `high`
* `low`
* `close`
* `volume`
* `vwap`
* `trade_count`
* `bid`
* `ask`
* `mid`
* `open_interest`
* `source`
* `source_symbol`
* `contract_code`
* `is_continuous`
* `session_type`
* `exchange_trading_date`
* `is_regular_session`
* `is_extended_session`
* `is_partial_bar`
* `is_stale`
* `quality_status`
* `ingested_at`

Use an appropriate unique constraint that prevents duplicate instrument, interval, contract and timestamp records.

## 5.3 `yield_observations`

Suggested fields:

* `instrument_id`
* `observation_timestamp_utc`
* `observation_date`
* `maturity`
* `yield_value`
* `yield_type`
* `source`
* `published_at`
* `vintage_date`
* `is_revised`
* `ingested_at`

## 5.4 `futures_rolls`

Suggested fields:

* `continuous_instrument_id`
* `outgoing_contract`
* `incoming_contract`
* `decision_timestamp`
* `roll_timestamp`
* `roll_basis`
* `price_adjustment`
* `adjustment_method`
* `outgoing_volume`
* `incoming_volume`
* `outgoing_open_interest`
* `incoming_open_interest`

## 5.5 `ingestion_runs`

Record:

* run ID
* source
* start time
* end time
* requested range
* row counts
* inserts
* updates
* duplicates
* rejected rows
* API calls
* retries
* status
* error details
* software version
* configuration hash

## 5.6 `data_quality_issues`

Record every detected issue with:

* instrument
* timestamp
* issue type
* severity
* observed value
* expected condition
* resolution
* whether the row was retained, repaired or excluded

---

# 6. Timestamp and session requirements

All analysis timestamps must be stored in UTC.

Also preserve:

* original provider timestamp
* exchange timezone
* exchange trading date
* regular-session indicator
* extended-session indicator
* minutes since session open
* minutes until session close
* day of week
* holiday or shortened-session status

Important rules:

* Do not assume every market trades at the same time.
* Do not forward-fill prices across market closures.
* Do not interpret a closed market as a zero return.
* Do not create artificial zero-volume bars merely to make a rectangular dataset.
* Preserve missingness explicitly.
* Allow Bitcoin to retain its genuine 24/7 trading history.
* Handle daylight-saving-time changes correctly.
* Validate that each bar timestamp represents the same bar convention across providers.
* Explicitly state whether timestamps identify the start or end of a bar.
* All cross-market comparisons must use information that was actually available by the relevant timestamp.

Create both:

1. A raw event-time dataset.
2. A research-aligned dataset constructed under explicitly documented alignment rules.

The raw dataset must never be overwritten by the aligned version.

---

# 7. Futures requirements

For every futures market:

* Download individual contracts where available.
* Identify the active contract using a point-in-time rule.
* Do not use knowledge of future volume or open interest to determine an earlier roll.
* Record expiry dates and roll dates.
* Avoid mixing bars from two contracts without labelling them.
* Preserve unadjusted contract prices.
* Create continuous data as a separate derivative table or export.
* Document whether the series is unadjusted, difference-adjusted, ratio-adjusted or otherwise transformed.
* Ensure returns around contract rolls are not mistaken for economic market movements.
* Flag every bar affected by a roll.

For volume-based rolls, the decision may use only volume information known by the decision timestamp.

---

# 8. Collector engineering requirements

Build the collector in Python.

The pipeline must be:

* idempotent
* restartable
* modular
* logged
* configuration-driven
* rate-limit aware
* tolerant of temporary API failures
* capable of incremental updates
* capable of historical backfills
* capable of validating data before database insertion

Implement:

* paginated or chunked API retrieval
* exponential backoff
* retry limits
* timeout handling
* checkpointing
* batched database loading
* conflict-safe upserts
* transaction protection
* structured logs
* environment-variable validation
* dry-run mode
* source-specific adapters
* reproducible dependency locking

Do not rely on manually editing Python files to change instruments or dates. Use a human-readable configuration file.

For large database loads, use an efficient bulk-loading method rather than inserting every bar individually.

---

# 9. Data-quality requirements

Run all quality checks using Python.

At minimum, test for:

* duplicate instrument/timestamp records
* invalid OHLC relationships
* negative prices where economically impossible
* negative volume
* malformed timestamps
* timestamps outside expected sessions
* missing required fields
* unexpected interval lengths
* missing bars inside active sessions
* duplicated provider pages
* bars returned in reverse order
* zero prices
* excessive zero-volume runs
* stale repeated prices
* extreme returns
* probable decimal or unit errors
* inconsistent currencies
* contract-roll jumps
* incomplete first or final bars
* timezone errors
* daylight-saving anomalies
* unexpected weekend observations
* API truncation
* rate-limit-related missing ranges
* inconsistent bar boundaries between providers
* revised yield observations
* future information accidentally included in earlier rows

Do not automatically delete unusual observations merely because they are extreme.

For each extreme observation:

1. Check it against the source.
2. Check adjacent bars.
3. Determine whether it reflects a real market move, bad tick, unit error or contract roll.
4. Preserve both the original value and any corrected value.
5. Document the decision.

Produce coverage heatmaps and gap summaries, but do not inspect the untouched test period.

---

# 10. Export design

Create separate export packages.

## 10.1 Discovery package

Filename:

`cross_asset_discovery_export_<timestamp>.zip`

This package may contain only the discovery and validation periods.

Include:

* `bars_raw.parquet`
* `bars_research_aligned.parquet`
* `yields.parquet`
* `instruments.csv`
* `futures_rolls.csv`
* `market_sessions.csv`
* `data_dictionary.md`
* `methodology.md`
* `source_manifest.json`
* `data_quality_report.md`
* `data_quality_results.csv`
* `coverage_summary.csv`
* `export_manifest.json`
* `analysis_prompt.md`
* `README.md`

Also create CSV versions split into manageable files where necessary.

Avoid one enormous CSV. Create chunks small enough to upload and analyse reliably.

## 10.2 Untouched test package

Filename:

`cross_asset_UNTOUCHED_TEST_<timestamp>.zip`

This must contain only the quarantined test period.

Do not:

* profile it
* summarise its returns
* calculate descriptive statistics
* create charts
* compare instruments
* run quality checks that expose market behaviour to the researcher
* include it in the discovery package

Only structural checks necessary to confirm file readability and schema validity are permitted.

Encrypt or clearly label this archive where practical.

## 10.3 Full archival package

A third restricted archive may contain the complete raw collection for backup purposes, but it must not be used in discovery.

## 10.4 Export manifest

The manifest must include:

* extraction timestamp
* dataset start and end
* split dates
* row counts by instrument
* file sizes
* SHA-256 hash for every file
* source and API version
* collector Git commit
* Python version
* dependency versions
* configuration hash
* known limitations
* exclusions
* corrections
* roll methodology
* timestamp convention

---

# 11. Blank-canvas analysis prompt

Create `analysis_prompt.md` as a complete prompt that can be used in a separate ChatGPT conversation.

The prompt must instruct ChatGPT to analyse only the discovery package initially.

It must contain the following research requirements.

## Research posture

* Begin with no assumption that any known technical indicator works.
* Do not begin by testing popular indicators merely because they are familiar.
* Do not assume correlations are stable.
* Do not assume one asset class leads another.
* Do not assume relationships are linear.
* Do not assume relationships are symmetric.
* Do not assume a predictor works in every session or regime.
* Do not reinterpret noise as a finding.
* Explicitly permit a conclusion of “no useful relationship found.”

## Outcome definitions

Systematically examine forward outcomes at predeclared horizons such as:

* 5 minutes
* 10 minutes
* 15 minutes
* 30 minutes
* 60 minutes
* 120 minutes
* 240 minutes
* session close, where appropriate
* next relevant market open, where appropriate

For every target instrument, evaluate:

* signed forward return
* absolute forward move
* upward versus downward direction
* probability of exceeding economically meaningful movement thresholds
* maximum favourable excursion
* maximum adverse excursion
* realised forward volatility
* time to reach a movement threshold

Do not modify the outcome definitions after seeing results merely to rescue a weak relationship.

## Candidate information set

At each timestamp, use only information known at or before that timestamp.

Systematically investigate raw and derived information including:

* lagged returns
* price acceleration and deceleration
* intrabar range
* realised volatility
* volume
* relative volume
* volume acceleration
* VWAP deviation
* gap behaviour
* cross-asset return differentials
* cross-asset volatility differentials
* futures-curve or yield-curve changes
* session transitions
* market opens and closes
* stale versus active markets
* asynchronous reactions between regions
* changes in correlation
* joint states across multiple instruments
* nonlinear thresholds
* interactions
* sequences of states
* persistence
* reversals
* volatility regime
* time-of-day effects
* day-of-week effects
* market-specific session position
* missingness as an observable state where economically legitimate

Do not use future-derived normalisation values.

Rolling statistics must use trailing observations only.

## Discovery methods

Use a broad method set rather than relying on one model.

Suitable methods may include:

* lagged cross-correlation
* conditional probability tables
* event studies
* mutual information
* rank relationships
* change-point analysis
* regime clustering
* decision trees
* random forests or gradient-boosted trees
* regularised linear models
* interaction searches
* sequence-pattern analysis
* Granger-style predictive tests, with their assumptions and limitations stated
* transfer-entropy-style analysis where computationally feasible
* permutation tests
* block bootstrap
* symbolic or rule-based searches
* stability analysis across days and sessions

Machine-learning feature importance alone is not evidence of a tradable leading indicator.

## Independence and statistical integrity

Treat the following as non-independent:

* overlapping forward-return windows
* adjacent five-minute observations
* observations from the same trading day
* multiple contracts representing similar exposures
* highly correlated indices
* repeated tests of neighbouring thresholds
* repeated tests of neighbouring horizons

Use:

* day-level or session-level resampling
* block bootstrap
* clustered uncertainty estimates
* purged chronological validation
* embargoes between training and validation windows
* multiple-testing correction
* false-discovery-rate controls
* stability across neighbouring parameters
* effect-size thresholds
* minimum event counts
* confidence intervals

Do not present a raw p-value from thousands of overlapping rows as proof of significance.

## Chronological split

Within the discovery export, create a further chronological separation between:

* exploratory discovery
* internal validation

Do not randomly shuffle time-series observations.

Do not let later observations influence earlier feature construction, model selection, normalisation or threshold choice.

The separate untouched-test archive must not be opened until:

1. Candidate rules have been fully specified.
2. Thresholds have been frozen.
3. Directions have been frozen.
4. Instruments have been frozen.
5. Entry times and holding periods have been frozen.
6. Exclusion rules have been frozen.
7. Transaction-cost assumptions have been frozen.
8. The complete candidate specification has been written to a timestamped file.

## Candidate graduation standard

A relationship may graduate only when it:

* occurs materially more often than chance
* has an economically meaningful effect size
* survives chronological validation
* remains directionally consistent across neighbouring parameters
* is not driven by one day or one exceptional event
* has a sufficient independent sample size
* survives reasonable transaction-cost and slippage assumptions
* does not depend on future data
* has a plausible execution path
* remains observable at the user’s decision times where relevant
* is clearly distinguishable from a coincident correlation
* is not merely an artefact of different market opening hours
* is robust to reasonable alignment choices

Label all discoveries according to evidence level:

* descriptive observation
* exploratory relationship
* candidate pending validation
* validated candidate pending untouched test
* rejected
* inconclusive

Never call an exploratory relationship an “edge.”

## Practical relevance

For each surviving relationship, report:

* target instrument
* predictor instrument or state
* exact condition
* observation time
* prediction horizon
* direction
* event count
* independent-day count
* base rate
* conditional success rate
* uplift over base rate
* median forward return
* mean forward return
* confidence interval
* maximum adverse excursion
* stability by day
* stability by session
* stability by regime
* sensitivity to neighbouring parameters
* likely transaction costs
* likely slippage
* execution constraints
* possible causal explanation
* alternative non-causal explanations
* failure conditions
* evidence level

A success rate above 50% is not sufficient by itself. Compare it with the unconditional base rate and economic payoff.

## Required analysis outputs

Produce:

1. Dataset-integrity assessment
2. Market-session and alignment assessment
3. Search-space description
4. Methods used
5. Number of hypotheses and variants effectively evaluated
6. Multiple-testing controls
7. Strongest descriptive relationships
8. Relationships rejected during validation
9. Candidate rules, if any
10. Evidence against candidate rules
11. Regime dependence
12. Sensitivity analysis
13. Transaction-cost assessment
14. Research limitations
15. Exact frozen specifications for any candidates
16. A machine-readable `candidate_rules.json`
17. A complete `analysis_audit.json`
18. A reproducible Python analysis script
19. A plain-English research report
20. A clear verdict on whether any result justifies opening the untouched test set

Use Python for every quantitative calculation.

Do not open the untouched test archive unless explicitly instructed in a later, separate research stage.

---

# 12. Deployment package

Deliver a downloadable GitHub-ready ZIP containing:

* complete source code
* SQL migrations
* configuration templates
* environment-variable template
* dependency file
* tests
* data-quality scripts
* export scripts
* logging
* README
* troubleshooting guide
* Render deployment configuration
* scheduled-job configuration
* Supabase setup instructions
* data-provider setup instructions

The package must support:

* one-off historical backfill
* incremental scheduled collection
* quality-check job
* export job
* status output

Do not require me to write code.

Provide simple, numbered instructions covering:

1. Creating or selecting a market-data account
2. Obtaining API credentials
3. Creating the Supabase project
4. Running the supplied SQL
5. Uploading the ZIP contents to GitHub
6. Deploying the services or cron jobs on Render
7. Adding environment variables
8. Running a small test collection
9. Verifying Supabase rows
10. Running the complete historical backfill
11. Running quality checks
12. Creating the discovery export
13. Confirming the untouched archive is separate
14. Uploading the discovery package to ChatGPT
15. Using the generated analysis prompt

Each instruction must state:

* exactly where to click
* exactly what to paste
* what a successful result looks like
* what common error messages mean
* how to recover from failure

---

# 13. Testing and acceptance criteria

The system is complete only when:

* all selected instruments have been mapped explicitly
* the selected source is documented
* real data has been retrieved
* the database schema has been created
* historical bars have been inserted into Supabase
* rerunning the collector creates no duplicates
* timestamps are consistently stored in UTC
* futures rolls are documented
* market closures are not forward-filled
* data-quality checks have run
* coverage has been measured
* discovery and untouched-test periods are separated
* the discovery export has been generated
* every export file has a hash
* the generated analysis prompt is included
* the package can be deployed using non-technical instructions
* known limitations and unavailable data are disclosed
* no fabricated, test or demonstration rows appear in the final export

At completion, provide a concise integrity verdict stating:

* what was successfully collected
* what was not available
* any substitutions used
* actual data frequencies
* coverage percentages
* unresolved quality concerns
* whether the discovery package is ready for analysis
* whether the untouched test package remained uninspected

Do not hide incomplete requirements behind a general statement that the pipeline “works.”
