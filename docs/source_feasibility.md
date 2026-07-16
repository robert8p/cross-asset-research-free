# Free-source feasibility assessment

## Decision

Use a mixed free-source architecture:

1. Alpaca IEX historical bars for US-listed ETF proxies.
2. Coinbase Exchange for BTC-USD spot.
3. FRED/ALFRED for point-in-time US Treasury yields.
4. Bank of England and Bundesbank for prospective-safe official UK/German yield snapshots.

This cannot reproduce the original native-futures universe. The system therefore changes the instrument catalogue explicitly rather than hiding the substitutions.

## Current providers

| Provider | Cost used by this edition | Data used | Frequency | Authentication | Important limitation |
|---|---:|---|---:|---|---|
| Alpaca | Free Paper Only account | US-listed ETF proxies | Native 5-minute bars | API key + secret | Free Paper Only accounts use IEX. IEX is one exchange; price discovery and especially volume are incomplete versus the consolidated US market. |
| Coinbase Exchange | Free public market-data endpoint | BTC-USD spot | Native 5-minute OHLCV | None for the selected Exchange candles endpoint | Single venue; not a composite crypto price. Empty no-trade intervals can be absent. |
| FRED/ALFRED | Free | US 2Y, 5Y, 10Y and 30Y Treasury yields | Daily | Free API key | Not intraday. Availability timestamps and vintages must be respected. |
| Bank of England | Free official download | UK 2Y and 10Y fitted nominal yields | Daily | None | No API and no reliable full vintage history. Historical values are assigned retrieval-time availability and cannot enter the past discovery information set. |
| Deutsche Bundesbank | Free official service | German 2Y and 10Y current-security yields | Daily | None | Current historical snapshot lacks full vintage history; retrieval-time availability is used. |

## Why Alpaca is useful but not equivalent

Alpaca's historical bars endpoint supports five-minute aggregates. The free Paper Only entitlement is IEX market data. That is suitable for a low-cost exploratory study of **US-traded proxy instruments**.

It is not a replacement for:

- CME, CBOT, NYMEX, COMEX, ICE or Eurex futures;
- cash VIX;
- native FTSE, DAX, Nikkei or Hang Seng market hours;
- futures contract rolls, open interest or full-market futures volume.

## Explicit substitutions

| Native exposure requested | Free proxy | Substitution risk |
|---|---|---|
| WTI futures | USO | Fund strategy and futures rolls |
| Brent futures | BNO | Fund strategy and futures rolls |
| Gold futures | GLD | Bullion trust; no COMEX futures volume |
| Silver futures | SLV | Bullion trust; no COMEX futures volume |
| S&P 500 | SPY | ETF basis and distributions |
| Nasdaq 100 | QQQ | ETF basis and distributions |
| Dow | DIA | ETF basis and distributions |
| Russell 2000 | IWM | ETF basis, sampling and distributions |
| VIX | VIXY | Short-term VIX futures ETF; roll and decay dominate |
| FTSE 100 | EWU | MSCI UK, not FTSE 100 |
| DAX 40 | EWG | MSCI Germany, not DAX 40 |
| Euro Stoxx 50 | FEZ | Close economic proxy, but USD ETF |
| Nikkei 225 | EWJ | MSCI Japan, not Nikkei 225 |
| Hang Seng | EWH | MSCI Hong Kong, not Hang Seng |
| 2Y Treasury rate | SHY | 1-3Y bond ETF price, not yield |
| 5Y Treasury rate | IEI | 3-7Y bond ETF price, not yield |
| 10Y Treasury rate | IEF | 7-10Y bond ETF price, not yield |
| 30Y Treasury rate | TLT | 20+Y bond ETF price, not constant 30Y yield |

## Acceptance rule

The free dataset may be used for discovery only when:

- every required symbol returns real five-minute bars;
- the manifest identifies Alpaca feed `iex` and adjustment `raw`;
- analysis treats IEX volume as partial venue activity;
- all proxy claims are phrased as proxy results;
- no yield curve is calculated from ETF prices;
- no claim about Asian or European native-session leadership is made from US-hours ETF data;
- the untouched split remains unopened.
