"""The paper engine: sends a ledger's orders to the simulated venue and works exits.

Live execution is disabled. There is no code path from here to a real venue.
"""
import logging
import time

import exits
import sizing
from ledger import DuplicateOrder, Ledger, OrderBlocked, connect
from market import QuoteUnavailable
from thresholds import EXITS, RECONCILE
from venue import DeliveryTimeout, LookupUnavailable, PaperVenue, VenueReject

log = logging.getLogger("desk.paper")


class PaperEngine:
    def __init__(self, ledger: Ledger, venue: PaperVenue, market, *, clock=time.time,
                 exit_cfg=EXITS, reconcile_cfg=RECONCILE):
        self.ledger, self.venue, self.market, self.clock = ledger, venue, market, clock
        self.exit_cfg, self.reconcile_cfg = exit_cfg, reconcile_cfg

    @property
    def name(self) -> str:
        return self.ledger.name

    def can_enter(self) -> str | None:
        """None when a new entry may be sent, else the reason it may not."""
        return self.ledger.blocked_reason("buy")

    def _quote(self, key: str):
        for _ in range(1 + self.exit_cfg["quote_retries"]):
            try:
                return self.market.quote(key)
            except QuoteUnavailable as e:
                log.info("%s quote failed: %s", self.name, e)
        return None

    # ---- sending ---------------------------------------------------------------------
    def _send(self, o: dict, ref_price, liquidity, flags=()) -> str:
        self.ledger.submitted(o["order_id"])
        try:
            fill = self.venue.submit(o, ref_price, liquidity, flags)
        except VenueReject as e:
            self.ledger.rejected(o["order_id"], str(e))
            return "REJECTED"
        except (DeliveryTimeout, Exception) as e:     # any lost answer is UNKNOWN
            self.ledger.unknown(o["order_id"], f"{type(e).__name__}: {e}")
            log.error("%s order %s UNKNOWN: %s. No new orders until reconciled.",
                      self.name, o["order_id"], e)
            return "UNKNOWN"
        self.ledger.filled(o["order_id"], fill)
        return "FILLED"

    # ---- reconciliation --------------------------------------------------------------
    def reconcile(self) -> list[tuple[str, str]]:
        """Settle every order that is not final. Only the venue's own record settles
           an UNKNOWN; silence before the grace period settles nothing."""
        out = []
        for o in self.ledger.orders("NEW", "SUBMITTED", "UNKNOWN"):
            oid = o["order_id"]
            if o["state"] == "NEW":                   # created, never handed to the venue
                self.ledger.rejected(oid, "never_sent")
                out.append((oid, "REJECTED"))
                continue
            if o["state"] == "SUBMITTED":             # the process stopped mid-send
                self.ledger.unknown(oid, "found SUBMITTED at reconcile")
            try:
                status, x = self.venue.lookup(oid)
            except LookupUnavailable as e:
                out.append((oid, "UNKNOWN"))
                log.warning("%s cannot reconcile %s: %s", self.name, oid, e)
                continue
            if status == "FILLED":
                self.ledger.filled(oid, x)
            elif status == "REJECTED":
                self.ledger.rejected(oid, x)
            elif self.clock() - o["created_at"] >= self.reconcile_cfg["unknown_grace_seconds"]:
                self.ledger.rejected(oid, "never_received")
                status = "REJECTED"
            else:
                status = "UNKNOWN"                    # too early to call it
            out.append((oid, status))
        return out

    # ---- entries and exits -------------------------------------------------------------
    def enter(self, d: dict, size_factor: float, cycle_id: str, meta=None) -> dict:
        key = d["token_key"]
        if (why := self.can_enter()):
            return {"status": "SKIPPED", "reason": why, "token_key": key}
        q = self._quote(key)
        if q is None or not q.price_usd:
            return {"status": "SKIPPED", "reason": "no_quote", "token_key": key}
        t, why = sizing.ticket(self.ledger.free_cash(), size_factor, q.liquidity_usd)
        if not t:
            return {"status": "SKIPPED", "reason": why, "token_key": key}
        try:
            o = self.ledger.create_order(
                side="buy", token_key=key, ticker=d["ticker"], notional_usd=t,
                reserve_usd=t + sizing.entry_fee(t),
                idem_key=f"{self.name}:buy:{key}:{cycle_id}",
                meta={**(meta or {}), "size_factor": size_factor, "ref_price": q.price_usd})
        except DuplicateOrder:
            return {"status": "SKIPPED", "reason": "duplicate", "token_key": key}
        status = self._send(o, q.price_usd, q.liquidity_usd)
        if status == "FILLED":
            p = self.ledger.positions("OPEN")[0]
            self.ledger.mark(p["position_id"], q.price_usd, q.liquidity_usd)
        return {"status": status, "order_id": o["order_id"], "token_key": key, "ticket": t}

    def manage(self) -> list[dict]:
        """Poll every open position once and close the ones RISK says close."""
        out = []
        for p in self.ledger.positions("OPEN"):
            q = self._quote(p["token_key"])
            if q is not None and q.price_usd:
                self.ledger.mark(p["position_id"], q.price_usd, q.liquidity_usd)
            else:
                self.ledger.mark(p["position_id"], failed=True)
            p = self.ledger.position(p["position_id"])
            reason = exits.decide(p, q, self.clock(), self.exit_cfg)
            if reason:
                out.append(self.close(p, reason, q))
        return out

    def close(self, p: dict, reason: str, q=None) -> dict:
        pid = p["position_id"]
        if (why := self.ledger.blocked_reason("sell")):
            return {"status": "BLOCKED", "reason": why, "position_id": pid}
        if q is not None and q.price_usd:
            ref, liq, flags = q.price_usd, q.liquidity_usd or p["last_liquidity"], ()
        else:
            ref, liq, flags = exits.blind_close_price(p, self.exit_cfg), p["last_liquidity"], \
                ("blind_close",)
        attempt = self.ledger.db.execute(
            "SELECT COUNT(*) FROM orders WHERE position_id=? AND side='sell'", (pid,)
        ).fetchone()[0]
        try:
            o = self.ledger.create_order(
                side="sell", token_key=p["token_key"], ticker=p["ticker"], position_id=pid,
                idem_key=f"{self.name}:sell:{pid}:{attempt}",
                meta={"exit_reason": reason, "ref_price": ref})
        except OrderBlocked as e:
            return {"status": "BLOCKED", "reason": str(e), "position_id": pid}
        return {"status": self._send(o, ref, liq, flags), "order_id": o["order_id"],
                "position_id": pid, "reason": reason}

    def tick(self) -> dict:
        """Reconcile, then exits, then an equity mark. Runs every poll."""
        rec = self.reconcile()
        closed = self.manage()
        self.ledger.record_equity()
        return {"reconciled": rec, "exits": closed}


def build(db_path: str, market, *, clock=time.time, names=("strategy", "baseline"),
          faults=None, new_id=None) -> dict[str, PaperEngine]:
    """One database, one simulated venue, one ledger and engine per name."""
    db = connect(db_path)
    venue = PaperVenue(db, faults=faults, clock=clock)
    kw = {"clock": clock} | ({"new_id": new_id} if new_id else {})
    return {n: PaperEngine(Ledger(db, n, **kw), venue, market, clock=clock) for n in names}
