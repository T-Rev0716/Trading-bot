"""Order lifecycle regressions: duplicates, timeouts, UNKNOWN, reconciliation, release."""
import pytest

from ledger import DuplicateOrder, LedgerError, OrderBlocked, ReleaseMismatch
from tests.helpers import dossier, engines, quote
from venue import FaultPlan

D = dossier()
KEY = D["token_key"]


def setup(faults=None):
    eng, market, clock = engines(faults=faults, names=("strategy", "other"))
    market.update(KEY, quote())
    return eng["strategy"], eng["other"], market, clock


def test_entry_fills_and_the_position_references_its_order():
    e, _, _, _ = setup()
    r = e.enter(D, 1.0, "c1")
    assert r["status"] == "FILLED"
    p = e.ledger.positions()[0]
    assert p["entry_order_id"] == r["order_id"] and p["token_key"] == KEY
    assert e.ledger.cash() == pytest.approx(10_000 - 300 - 1.35)


def test_duplicate_idempotency_key_is_refused():
    e, _, _, _ = setup()
    kw = dict(side="buy", token_key=KEY, ticker="AAA", notional_usd=100.0, reserve_usd=101.0,
              idem_key="strategy:buy:x:c1")
    o = e.ledger.create_order(**kw)
    e.ledger.rejected(o["order_id"], "test")
    with pytest.raises(DuplicateOrder):
        e.ledger.create_order(**kw)


def test_the_same_cycle_cannot_enter_twice():
    e, _, _, _ = setup()
    assert e.enter(D, 1.0, "c1")["status"] == "FILLED"
    assert e.enter(D, 1.0, "c1") == {"status": "SKIPPED", "reason": "position_open",
                                     "token_key": KEY}


def test_a_rejected_entry_cannot_be_resent_in_the_same_cycle():
    e, _, market, _ = setup()
    market.update(KEY, quote(liq=1.0))            # a $1 pool: 2% of it never pays the fee
    assert e.enter(D, 1.0, "c1")["reason"] == "fee_floor"
    o = e.ledger.create_order(side="buy", token_key=KEY, ticker="AAA", notional_usd=100.0,
                              reserve_usd=101.0, idem_key=f"strategy:buy:{KEY}:c2")
    e.ledger.rejected(o["order_id"], "test")
    market.update(KEY, quote())
    assert e.enter(D, 1.0, "c2") == {"status": "SKIPPED", "reason": "duplicate",
                                     "token_key": KEY}
    assert e.enter(D, 1.0, "c3")["status"] == "FILLED"


def test_no_second_order_while_one_is_in_flight():
    e, _, _, _ = setup()
    e.ledger.create_order(side="buy", token_key=KEY, ticker="AAA", notional_usd=100.0,
                          reserve_usd=101.0, idem_key="k1")
    with pytest.raises(OrderBlocked, match="order_in_flight"):
        e.ledger.create_order(side="buy", token_key="solana:Other", ticker="B",
                              notional_usd=100.0, reserve_usd=101.0, idem_key="k2")


def test_timeout_after_fill_is_unknown_not_failed():
    e, other, _, clock = setup(FaultPlan(timeout_after_fill={1}))
    r = e.enter(D, 1.0, "c1")
    assert r["status"] == "UNKNOWN"
    o = e.ledger.order(r["order_id"])
    assert o["state"] == "UNKNOWN"
    # never assumed failed: the cash stays reserved, nothing is refunded or released
    assert e.ledger.cash() == 10_000 and e.ledger.reserved() == pytest.approx(301.35)
    assert e.ledger.free_cash() == pytest.approx(10_000 - 301.35)
    # and nothing new goes out until it is reconciled
    assert e.can_enter() == "unknown_order"
    assert e.enter(dossier(addr="Other111"), 1.0, "c2")["reason"] == "unknown_order"
    with pytest.raises(OrderBlocked, match="unknown_order"):
        e.ledger.create_order(side="buy", token_key="solana:Other111", ticker="B",
                              notional_usd=100.0, reserve_usd=101.0, idem_key="k9")
    # a different ledger is not blocked by it
    assert other.can_enter() is None
    # reconciliation finds the fill: the position appears, tied to that order
    assert e.reconcile() == [(r["order_id"], "FILLED")]
    p = e.ledger.positions()[0]
    assert p["entry_order_id"] == r["order_id"]
    assert e.ledger.cash() == pytest.approx(10_000 - 301.35) and e.ledger.reserved() == 0
    assert e.can_enter() == "position_open"


def test_timeout_before_receipt_waits_out_the_grace_period():
    e, _, _, clock = setup(FaultPlan(timeout_before_receipt={1}))
    r = e.enter(D, 1.0, "c1")
    assert r["status"] == "UNKNOWN"
    clock.advance(60)
    assert e.reconcile() == [(r["order_id"], "UNKNOWN")]         # silence is not an answer
    assert e.can_enter() == "unknown_order" and e.ledger.reserved() > 0
    clock.advance(61)
    assert e.reconcile() == [(r["order_id"], "REJECTED")]
    assert e.ledger.order(r["order_id"])["reason"] == "never_received"
    assert e.ledger.reserved() == 0 and e.ledger.cash() == 10_000 and e.can_enter() is None


def test_unreachable_venue_keeps_the_order_unknown():
    e, _, _, clock = setup(FaultPlan(timeout_after_fill={1}, lookup_unavailable={1, 2}))
    r = e.enter(D, 1.0, "c1")
    clock.advance(10_000)                                        # long past the grace period
    assert e.reconcile() == [(r["order_id"], "UNKNOWN")]
    assert e.reconcile() == [(r["order_id"], "UNKNOWN")]
    assert e.can_enter() == "unknown_order"
    assert e.reconcile() == [(r["order_id"], "FILLED")]


def test_order_left_submitted_by_a_crash_becomes_unknown():
    e, _, _, clock = setup()
    o = e.ledger.create_order(side="buy", token_key=KEY, ticker="AAA", notional_usd=100.0,
                              reserve_usd=101.0, idem_key="k1")
    e.ledger.submitted(o["order_id"])                            # then the process died
    assert e.reconcile() == [(o["order_id"], "UNKNOWN")]
    assert e.ledger.order(o["order_id"])["state"] == "UNKNOWN"
    clock.advance(200)
    assert e.reconcile() == [(o["order_id"], "REJECTED")]


def test_order_never_handed_over_is_rejected_as_never_sent():
    e, _, _, _ = setup()
    o = e.ledger.create_order(side="buy", token_key=KEY, ticker="AAA", notional_usd=100.0,
                              reserve_usd=101.0, idem_key="k1")
    assert e.reconcile() == [(o["order_id"], "REJECTED")]
    assert e.ledger.order(o["order_id"])["reason"] == "never_sent"


def test_sell_timeout_keeps_the_position_until_reconciled():
    e, _, market, _ = setup(FaultPlan(timeout_after_fill={2}))
    e.enter(D, 1.0, "c1")
    p = e.ledger.positions()[0]
    cash = e.ledger.cash()
    market.update(KEY, quote(v6=10_000))                          # ratio 0.13: close
    res = e.manage()
    assert res[0]["status"] == "UNKNOWN" and res[0]["reason"] == "volume_ratio"
    assert e.ledger.position(p["position_id"])["status"] == "CLOSING"
    assert e.ledger.cash() == cash                                # no proceeds assumed
    assert e.can_enter() == "unknown_order"
    e.reconcile()
    closed = e.ledger.position(p["position_id"])
    assert closed["status"] == "CLOSED" and closed["exit_order_id"] == res[0]["order_id"]
    assert closed["exit_reason"] == "volume_ratio"
    assert e.ledger.order(res[0]["order_id"])["position_id"] == p["position_id"]


def test_release_must_reference_the_matching_order():
    e, other, market, _ = setup()
    entry = e.enter(D, 1.0, "c1")
    p = e.ledger.positions()[0]
    with pytest.raises(ReleaseMismatch):
        e.ledger.release(p["position_id"], entry["order_id"])     # a buy is not a close
    with pytest.raises(ReleaseMismatch):
        e.ledger.release(p["position_id"], "no-such-order")
    # another ledger's close cannot release this position
    other.enter(D, 1.0, "c1")
    op = other.ledger.positions()[0]
    oc = other.close(op, "test", quote())
    with pytest.raises(ReleaseMismatch):
        e.ledger.release(p["position_id"], oc["order_id"])
    assert e.ledger.position(p["position_id"])["status"] == "OPEN"
    # the right order releases it, and releasing again with it is a no-op
    c = e.close(p, "test", quote())
    closed = e.ledger.release(p["position_id"], c["order_id"])
    assert closed["status"] == "CLOSED" and closed["exit_order_id"] == c["order_id"]


def test_a_rejected_close_leaves_the_position_open():
    e, _, market, _ = setup()
    e.enter(D, 1.0, "c1")
    p = e.ledger.positions()[0]
    e.ledger.db.execute("UPDATE positions SET last_liquidity=NULL")  # nothing to fall back to
    p = e.ledger.positions()[0]
    r = e.close(p, "test", quote(liq=None))                        # venue: no_liquidity
    assert r["status"] == "REJECTED"
    assert e.ledger.position(p["position_id"])["status"] == "OPEN"
    assert e.close(p, "test", quote())["status"] == "FILLED"       # next attempt, new key


def test_illegal_transitions_are_refused():
    e, _, _, _ = setup()
    r = e.enter(D, 1.0, "c1")
    with pytest.raises(LedgerError):
        e.ledger.unknown(r["order_id"], "late timeout")            # FILLED is final
