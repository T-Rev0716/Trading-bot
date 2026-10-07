"""SIZE, in Python. The four steps of the SIZE prompt, enforced here instead of by a bot."""
import math

from thresholds import PAPER, SIZING


def entry_fee(notional: float, cfg=PAPER) -> float:
    return max(cfg["fee_rate"] * notional, cfg["min_fee_usd"])


def ticket(free_cash: float, size_factor: float, liquidity_usd, *, cfg=SIZING,
           paper=PAPER) -> tuple[float, str | None]:
    """(dollars, None) or (0.0, reason). Dollars are spent on tokens; the fee comes on top.

    1. kelly_fraction * free cash, clamped at max_ticket_fraction. Free cash only.
    2. times size_factor (the missing-data cuts, stacked).
    3. at most max_pool_share of the pool. Bigger and you are the exit.
    4. zero if one side's fee is over max_fee_rate of the ticket, or cash cannot cover it.
    """
    if free_cash is None or free_cash <= 0:
        return 0.0, "no_free_cash"
    if not liquidity_usd or liquidity_usd <= 0:
        return 0.0, "no_liquidity"
    if not 0 < size_factor <= 1:
        return 0.0, "size_factor_zero"
    t = min(cfg["kelly_fraction"], cfg["max_ticket_fraction"]) * free_cash
    t *= size_factor
    t = min(t, liquidity_usd * cfg["max_pool_share"])
    t = math.floor(t * 100) / 100                       # whole cents, rounded down
    if t <= 0:
        return 0.0, "zero_ticket"
    if entry_fee(t, paper) / t > cfg["max_fee_rate"]:
        return 0.0, "fee_floor"
    if t + entry_fee(t, paper) > free_cash:
        return 0.0, "insufficient_cash"
    return t, None


def intended_ticket(free_cash: float, cfg=SIZING) -> float:
    """The most SIZE could ever allow right now. What the judge is asked about."""
    return round(max(0.0, free_cash) * cfg["max_ticket_fraction"], 2)
