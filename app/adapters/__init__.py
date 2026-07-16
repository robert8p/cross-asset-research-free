from .alpaca_adapter import AlpacaAdapter
from .boe_adapter import BoEYieldCurveAdapter
from .bundesbank_adapter import BundesbankAdapter
from .coinbase_adapter import CoinbaseAdapter
from .fred_adapter import FredAdapter

ADAPTERS = {
    "alpaca": AlpacaAdapter,
    "boe_yield_curve": BoEYieldCurveAdapter,
    "bundesbank": BundesbankAdapter,
    "coinbase": CoinbaseAdapter,
    "fred": FredAdapter,
}


def create_adapter(provider: str, settings: dict, dry_run: bool = False):
    try:
        return ADAPTERS[provider](settings, dry_run=dry_run)
    except KeyError as exc:
        raise ValueError(f"No adapter registered for provider {provider!r}") from exc
