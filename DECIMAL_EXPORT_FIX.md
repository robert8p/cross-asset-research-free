# v1.3.4 Decimal export fix

This patch fixes a deterministic export failure caused by PostgreSQL `NUMERIC`
values being returned by psycopg as `decimal.Decimal` objects. The compact
aligned export now converts only its calculation fields to `float64` before
computing five-minute returns. Official yield values are normalised similarly
before curve-feature arithmetic.

The source-faithful raw bars and raw yields exports are unchanged.
