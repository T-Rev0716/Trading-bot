"""Price and volume quotes for marking paper positions. Data in, never orders out."""
import json
from dataclasses import dataclass, asdict

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
        v = p.get("volume") or {}
        return Quote(price_usd=_f(p.get("priceUsd")),
                     liquidity_usd=_f((p.get("liquidity") or {}).get("usd")),
                     volume_h6=_f(v.get("h6")), volume_h24=_f(v.get("h24")),
                     ts=self.clock())


class ReplayMarket:
    """Quotes from a recorded tape. A token with no quote at or before now is unavailable."""

    def __init__(self):
        self._last: dict[str, Quote] = {}
        self.fail: set[str] = set()          # keys whose quotes should fail (tests)

    def update(self, key: str, q: Quote):
        self._last[key] = q

    def quote(self, key: str) -> Quote:
        if key in self.fail or key not in self._last:
            raise QuoteUnavailable(key)
        return self._last[key]


class RecordingMarket:
    """Wraps a market and appends every successful quote to the tape, for replay."""

    def __init__(self, inner, tape_path: str):
        self.inner, self.tape_path = inner, tape_path

    def quote(self, key: str) -> Quote:
        q = self.inner.quote(key)
        with open(self.tape_path, "a") as f:
            f.write(json.dumps({"type": "quote", "ts": q.ts, "token_key": key,
                                "quote": asdict(q)}) + "\n")
        return q
