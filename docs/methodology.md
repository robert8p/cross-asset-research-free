# Methodology — free-data proxy edition

## 1. Research identity

This is a study of free, retail-accessible **proxy instruments**, not a native global futures study. Every export retains the original requested economic exposure and the actual traded instrument separately.

## 2. Alpaca bars

The collector requests `5Min`, `adjustment=raw`, `feed=iex` from Alpaca's historical stock-bars endpoint. Pagination is followed until no token remains. Bars are filtered to the requested half-open interval `[start, end)`, deduplicated by timestamp, and stored without price adjustment.

IEX data represents eligible activity on one US exchange. Volume, trade count and VWAP are therefore genuine for that feed but incomplete relative to the consolidated US market. Relative-volume analysis may be performed only within the same instrument and feed, with this limitation stated.

## 3. ETF proxy interpretation

ETF returns may contain the economic signal of the intended exposure, but also:

- fund fees and distributions;
- tracking error and portfolio sampling;
- futures roll effects for commodity and volatility funds;
- currency translation;
- creation/redemption and ETF microstructure;
- a US trading-hours filter over foreign exposures.

A result involving an ETF must be labelled with the ETF ticker. It cannot be restated as if observed in the native index, future, commodity or yield.

## 4. Bitcoin

Coinbase Exchange BTC-USD candles are native five-minute single-venue spot OHLCV. Bitcoin retains 24/7 history. Coinbase is not combined with other venues.

## 5. Official yields

US DGS2, DGS5, DGS10 and DGS30 observations are requested through FRED/ALFRED with vintages. The collector assigns a conservative timestamp for when a value may enter the research information set.

UK and German public historical snapshots do not expose a reliable full sequence of vintages. They are stored with retrieval-time availability and are therefore prospective-only for leakage-safe work. They must not be backdated into the 90-day discovery sample.

Daily yield-curve features may be calculated only from official yields available by the timestamp. Positive spread change means steepening; negative means flattening. Treasury ETF prices are not yields and do not form a yield curve.

## 6. Sessions and alignment

All timestamps are UTC. US ETFs use the XNYS regular-session calendar for labels. Observed bars outside regular hours are retained as extended-session observations; no missing bars are manufactured. Foreign-market ETFs remain US-traded instruments and are not assigned their underlying foreign exchange's session.

The research-aligned file creates explicit five-minute slots and missingness flags. It never forward-fills prices across gaps or closures. Returns require exactly adjacent observed slots.

## 7. Split isolation

The dataset dates are frozen before collection. Quality checks query only timestamps before `UNTOUCHED_START_UTC`. The untouched package is serialized separately without return calculations, profiling, charts, coverage summaries or descriptive statistics.

## 8. Futures tables

The database retains generic futures tables so a later paid native-data edition can use the same schema. In this free edition, no futures contracts or continuous futures are active, so `futures_rolls.csv` should be empty. An unexpected futures-roll row is an integrity warning.
