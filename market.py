"""Price and volume quotes for marking paper positions. Data in, never orders out.

Every quote reaches the engine through journal.JournaledMarket, which records it."""
from dataclasses import dataclass

from ids import split_key


class QuoteUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Quote:
    price_usd: float | None
    liquidity_usd: float | None
    volume_h6: float | None
    volume_h24: float | None
    ts: float


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def quote_from_pair(p: dict, ts: float) -> Quote:
    """One DexScreener pair -> a Quote. priceUsd arrives as a decimal string."""
    v = p.get("volume") or {}
    return Quote(price_usd=_f(p.get("priceUsd")),
                 liquidity_usd=_f((p.get("liquidity") or {}).get("usd")),
                 volume_h6=_f(v.get("h6")), volume_h24=_f(v.get("h24")), ts=ts)


class DexScreenerMarket:
    """Live quotes off DexScreener's public API, filtered to the token's own chain."""

    def __init__(self, clock):
        self.clock = clock

    def quote(self, key: str) -> Quote:
        from collect import best_pair
        chain, addr = split_key(key)
        try:
            p = best_pair(chain, addr)
        except Exception as e:
            raise QuoteUnavailable(f"{key}: {e}") from e
        if p is None:
            raise QuoteUnavailable(f"{key}: no pair on {chain}")
        return quote_from_pair(p, self.clock())


class StaticMarket:
    """A quote per token, set by the caller: for tests and synthetic scenarios only.
       Keys in `fail` raise QuoteUnavailable, to simulate an outage."""

    def __init__(self):
        self._q: dict[str, Quote] = {}
        self.fail: set[str] = set()

    def update(self, key: str, q: Quote):
        self._q[key] = q

    def quote(self, key: str) -> Quote:
        if key in self.fail or key not in self._q:
            raise QuoteUnavailable(key)
        return self._q[key]
