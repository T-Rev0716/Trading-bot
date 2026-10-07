"""The simulated venue. Deterministic fills, fees and slippage; nothing leaves the machine.

The venue keeps its own record of every order it received, separate from the ledger,
so a lost acknowledgement can be reconciled the way it would be against a real venue:
by asking what happened to that order id.
"""
import json
from dataclasses import dataclass, asdict, field

from thresholds import PAPER


class DeliveryTimeout(RuntimeError):
    """No acknowledgement. The order may or may not have executed."""


class LookupUnavailable(RuntimeError):
    """The venue could not be asked. Nothing is known, nothing is assumed."""


class VenueReject(RuntimeError):
    """The venue received the order and refused it. Nothing executed."""


@dataclass(frozen=True)
class Fill:
    price: float
    qty: float
    notional_usd: float       # tokens' value at the fill price
    fee_usd: float
    slippage_bps: float
    flags: tuple = ()


def simulate_fill(side: str, ref_price, liquidity_usd, *, notional_usd=None, qty=None,
                  cfg=PAPER) -> Fill:
    """Pure function: same inputs, same fill.

       buy:  spends exactly notional_usd on tokens, plus the fee on top.
       sell: sells exactly qty, the fee comes out of the proceeds."""
    if not ref_price or ref_price <= 0:
        raise VenueReject("no_price")
    if not liquidity_usd or liquidity_usd <= 0:
        raise VenueReject("no_liquidity")
    size = notional_usd if side == "buy" else (qty or 0) * ref_price
    if not size or size <= 0:
        raise VenueReject("bad_size")

    slip = cfg["base_slippage_bps"] + cfg["impact_bps_per_pct_of_pool"] * (
        size / liquidity_usd * 100)
    flags = ("slippage_over_max",) if slip > cfg["max_slippage_bps"] else ()
    if side == "buy":
        price = ref_price * (1 + slip / 10_000)
        q, notional = notional_usd / price, notional_usd
    else:
        price = ref_price * max(0.0, 1 - slip / 10_000)
        q, notional = qty, qty * price
    fee = max(cfg["fee_rate"] * notional, cfg["min_fee_usd"])
    if side == "sell":
        fee = min(fee, notional)            # a fee cannot take more than the proceeds
    return Fill(price=price, qty=q, notional_usd=notional, fee_usd=fee,
                slippage_bps=slip, flags=flags)


@dataclass
class FaultPlan:
    """Deterministic failure injection, by submission or lookup count (1-based)."""
    timeout_after_fill: set = field(default_factory=set)       # executed, ack lost
    timeout_before_receipt: set = field(default_factory=set)   # never arrived
    lookup_unavailable: set = field(default_factory=set)


class PaperVenue:
    def __init__(self, conn, cfg=PAPER, faults: FaultPlan | None = None, clock=None):
        self.db, self.cfg, self.faults, self.clock = conn, cfg, faults or FaultPlan(), clock
        self.submits = self.lookups = 0
        self.db.execute("""CREATE TABLE IF NOT EXISTS venue_orders(
            order_id TEXT PRIMARY KEY, status TEXT, fill TEXT, reason TEXT, received_at REAL)""")
        self.db.commit()

    def submit(self, order: dict, ref_price, liquidity_usd, flags=()) -> Fill:
        self.submits += 1
        n = self.submits
        if n in self.faults.timeout_before_receipt:
            raise DeliveryTimeout(f"order {order['order_id']} lost before the venue")

        row = self.db.execute("SELECT status, fill, reason FROM venue_orders WHERE order_id=?",
                              (order["order_id"],)).fetchone()
        if row:                              # same id twice: same answer, never a second fill
            fill, reason = self._decode(row)
            if fill is None:
                raise VenueReject(reason)
        else:
            try:
                fill = simulate_fill(order["side"], ref_price, liquidity_usd,
                                     notional_usd=order["notional_usd"], qty=order["qty"],
                                     cfg=self.cfg)
                fill = Fill(**{**asdict(fill), "flags": tuple(fill.flags) + tuple(flags)})
                self._store(order["order_id"], "FILLED", fill, None)
            except VenueReject as e:
                self._store(order["order_id"], "REJECTED", None, str(e))
                raise
        if n in self.faults.timeout_after_fill:
            raise DeliveryTimeout(f"order {order['order_id']} executed, ack lost")
        return fill

    def lookup(self, order_id: str):
        """('FILLED', Fill) | ('REJECTED', reason) | ('NOT_FOUND', None)"""
        self.lookups += 1
        if self.lookups in self.faults.lookup_unavailable:
            raise LookupUnavailable(order_id)
        row = self.db.execute("SELECT status, fill, reason FROM venue_orders WHERE order_id=?",
                              (order_id,)).fetchone()
        if not row:
            return "NOT_FOUND", None
        fill, reason = self._decode(row)
        return (row[0], fill) if fill else (row[0], reason)

    def _store(self, order_id, status, fill, reason):
        self.db.execute("INSERT INTO venue_orders VALUES (?,?,?,?,?)",
                        (order_id, status, json.dumps(asdict(fill)) if fill else None,
                         reason, self.clock() if self.clock else None))
        self.db.commit()

    @staticmethod
    def _decode(row):
        status, fill, reason = row
        if fill:
            f = json.loads(fill)
            return Fill(**{**f, "flags": tuple(f["flags"])}), None
        return None, reason
