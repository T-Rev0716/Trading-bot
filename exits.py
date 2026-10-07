"""RISK, in Python. One decision per open position per poll. Nobody overrules it."""
from thresholds import EXITS


def decide(position: dict, quote, now: float, cfg=EXITS) -> str | None:
    """Exit reason, or None to hold. `quote` is None when every retry failed.

    A position you cannot measure is a position you do not hold: no quote, or no volume
    data, closes it."""
    if quote is None:
        return "no_quote"
    if quote.price_usd is None or quote.price_usd <= 0:
        return "no_price"
    v6, v24 = quote.volume_h6, quote.volume_h24
    if v6 is None or v24 is None:
        return "volume_missing"
    if v24 <= 0:
        return "volume_dead"
    if v6 / (v24 / 4) < cfg["min_volume_ratio"]:
        return "volume_ratio"
    change = quote.price_usd / position["entry_price"] - 1
    if cfg["stop_loss"] is not None and change <= -cfg["stop_loss"]:
        return "stop_loss"
    if cfg["take_profit"] is not None and change >= cfg["take_profit"]:
        return "take_profit"
    if cfg["max_hold_minutes"] is not None and \
       (now - position["opened_at"]) / 60 >= cfg["max_hold_minutes"]:
        return "max_hold"
    return None


def blind_close_price(position: dict, cfg=EXITS) -> float:
    """The reference price for closing without a quote: the last price, cut by the
       haircut. Paper results are not allowed to benefit from missing data."""
    return (position["last_price"] or position["entry_price"]) * (1 - cfg["stale_quote_haircut"])
