import exits
from tests.helpers import dossier, engines, quote

POS = {"entry_price": 1.0, "opened_at": 0.0, "last_price": 2.0}
CFG = {"min_volume_ratio": 0.2, "quote_retries": 2, "stale_quote_haircut": 0.25,
       "stop_loss": None, "take_profit": None, "max_hold_minutes": None}


def test_volume_ratio_rule():
    assert exits.decide(POS, quote(v6=14_999, v24=300_000), 0, CFG) == "volume_ratio"
    assert exits.decide(POS, quote(v6=15_000, v24=300_000), 0, CFG) is None    # exactly 0.20 holds


def test_unmeasurable_positions_close():
    assert exits.decide(POS, None, 0, CFG) == "no_quote"
    assert exits.decide(POS, quote(price=None), 0, CFG) == "no_price"
    assert exits.decide(POS, quote(v6=None), 0, CFG) == "volume_missing"
    assert exits.decide(POS, quote(v24=0), 0, CFG) == "volume_dead"


def test_optional_rules():
    cfg = CFG | {"stop_loss": 0.3, "take_profit": 1.0, "max_hold_minutes": 60}
    assert exits.decide(POS, quote(price=0.7), 0, cfg) == "stop_loss"
    assert exits.decide(POS, quote(price=2.0), 0, cfg) == "take_profit"
    assert exits.decide(POS, quote(price=1.1), 3600, cfg) == "max_hold"
    assert exits.decide(POS, quote(price=1.1), 3599, cfg) is None


def test_three_failed_quotes_close_blind_at_a_haircut():
    eng, market, clock = engines(names=("strategy",))
    e, d = eng["strategy"], dossier()
    market.update(d["token_key"], quote(price=2.0))
    e.enter(d, 1.0, "c1")
    market.fail.add(d["token_key"])
    res = e.manage()
    assert res[0]["status"] == "FILLED" and res[0]["reason"] == "no_quote"
    o = e.ledger.order(res[0]["order_id"])
    assert "blind_close" in o["flags"]
    # reference was 2.0 * 0.75 = 1.5, then sell slippage on top
    assert o["fill_price"] < 1.5
    assert e.ledger.position(res[0]["position_id"])["exit_reason"] == "no_quote"
