# Blank-canvas cross-asset leading-indicator research prompt

You are analysing a discovery package produced by the Cross-Asset Intraday Leading-Indicator Research System. Use only the discovery package. Do not request, open, inspect or infer the contents of any archive labelled `UNTOUCHED_TEST` or `FULL_RESTRICTED`.

Use Python for every quantitative calculation. Begin by verifying hashes, schemas, timestamp conventions, source limitations, roll flags, missingness and the exact discovery boundary. Stop and report an integrity failure if the package includes timestamps at or after the declared untouched start.

## Mandatory free-data proxy restrictions

This package is the free-data edition. Apply all of these restrictions:

* Analyse the actual ticker and canonical proxy name. Never rewrite a result as if it were observed in the native future, cash index, commodity, yield or foreign market.
* Alpaca bars use the IEX feed. Treat volume, trade count and VWAP as single-venue measures, not consolidated US-market activity.
* USO and BNO include fund and futures-roll mechanics; GLD and SLV are trusts; these are not native commodity futures.
* VIXY is a short-term VIX-futures ETF and is not cash VIX. A VIXY finding is not a VIX finding.
* EWU, EWG, FEZ, EWJ and EWH are US-listed ETFs. Do not infer native London, Frankfurt, Tokyo or Hong Kong session leadership from their timestamps.
* SHY, IEI, IEF and TLT are bond ETF prices, not yields. Never derive 2s10s, 5s30s or another yield curve from ETF prices. Yield-curve features may use only timestamp-available official yield observations.
* Because most proxies share US trading hours, explicitly test whether apparent lead-lag results are merely asynchronous IEX trading, ETF liquidity differences, opening effects or stale-price effects.
* A surviving result is at most a proxy-market candidate requiring validation with native-market data before it can support a claim about the underlying exposure.

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
* official yield-curve changes; ETF duration-price differentials must be separately named and must not be called a yield curve
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
