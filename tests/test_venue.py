import pytest

from ledger import connect
from venue import FaultPlan, PaperVenue, VenueReject, simulate_fill

CFG = {"fee_rate": 0.0045, "min_fee_usd": 0.95, "base_slippage_bps": 50,
       "impact_bps_per_pct_of_pool": 200, "max_slippage_bps": 500}


def test_buy_fill_is_exact():
    # $1,000 into a $100k pool: 1% of pool -> 50 + 200 = 250 bps
    f = simulate_fill("buy", 2.0, 100_000, notional_usd=1_000, cfg=CFG)
    assert f.slippage_bps == 250
    assert f.price == pytest.approx(2.05)
    assert f.qty == pytest.approx(1_000 / 2.05)
    assert f.fee_usd == pytest.approx(4.5) and f.notional_usd == 1_000 and f.flags == ()


def test_sell_fill_is_exact_and_fee_floor_applies():
    f = simulate_fill("sell", 1.0, 100_000, qty=100, cfg=CFG)     # $100 -> 0.1% of pool
    assert f.slippage_bps == pytest.approx(70)
    assert f.price == pytest.approx(0.993) and f.notional_usd == pytest.approx(99.3)
    assert f.fee_usd == 0.95


def test_slippage_over_max_completes_and_is_flagged():
    f = simulate_fill("buy", 1.0, 10_000, notional_usd=1_000, cfg=CFG)   # 10% of pool
    assert f.slippage_bps == 2_050 and f.flags == ("slippage_over_max",)


def test_fills_are_deterministic():
    a = simulate_fill("buy", 1.234, 55_555, notional_usd=321.0, cfg=CFG)
    assert a == simulate_fill("buy", 1.234, 55_555, notional_usd=321.0, cfg=CFG)


def test_rejects_without_price_or_liquidity():
    for args in ((None, 1e5), (0, 1e5), (1.0, None)):
        with pytest.raises(VenueReject):
            simulate_fill("buy", *args, notional_usd=100, cfg=CFG)


def test_resubmitting_an_order_id_never_fills_twice():
    v = PaperVenue(connect(":memory:"), cfg=CFG, faults=FaultPlan())
    o = {"order_id": "x1", "side": "buy", "notional_usd": 100.0, "qty": None}
    f1 = v.submit(o, 1.0, 1e5)
    f2 = v.submit(o, 5.0, 1e3)                   # different market, same id
    assert f1 == f2
    assert v.db.execute("SELECT COUNT(*) FROM venue_orders").fetchone()[0] == 1
