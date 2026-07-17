# Round 2 research design

## Decision

Do not open the existing untouched-test archive. Conduct a disjoint historical corroboration round first.

## Why

Round 1 evaluated 11,432 variants but had only 13 validation sessions. Its strongest effects were generally too small after next-bar execution and costs, and the free IEX feed was too sparse for several proxies. Round 2 therefore changes the research question rather than tuning failed thresholds.

## Scope

- Historical window: the 365 calendar days immediately before Round 1 began.
- Primary instruments: BTC, DIA, EWJ, GLD, IEF, IWM, QQQ, SLV, SPY and TLT.
- Slow regime information: official US 2Y, 5Y, 10Y and 30Y yields.
- Decision times: 14:00, 17:00 and 19:00 Europe/London, converted using actual daylight-saving rules.
- Horizons: 30, 60 and 120 minutes, plus session close.
- Baseline transaction cost: 5 basis points round trip; stress: 10 basis points.

## Distinct hypothesis families

1. Overnight BTC state predicting the US opening hour.
2. Opening-range trend or reversal at 17:00 London.
3. Cross-sectional equity breadth and large-cap/small-cap divergence.
4. Equity-duration and equity-metals divergence.
5. Afternoon trend persistence or reversal at 19:00 London.
6. Official-yield and curve changes as trailing regime conditioners.

## Restrictions

- No unrestricted technical-indicator sweep.
- No volume or missingness candidate based on IEX venue artefacts.
- No forward fill.
- No same-bar execution.
- No candidate with fewer than 60 independent days or 80 events.
- No graduation unless gross mean return is at least 10 bp and net expectancy remains positive after 5 bp costs.
- Maximum 500 effective variants.
- Any survivor remains pending the still-sealed external test.
