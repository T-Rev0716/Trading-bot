import pytest

import replay
from report import compare, metrics
from tests import synthetic
from tests.helpers import dossier, engines, quote
from venue import FaultPlan


def by_name(result):
    return {m["ledger"]: m for m in result["metrics"]}


def test_report_numbers_for_one_round_trip():
    eng, market, clock = engines(names=("strategy",))
    e, d = eng["strategy"], dossier()
    market.update(d["token_key"], quote(price=1.0))
    e.enter(d, 1.0, "c1")                       # $300 at 1.0 * (1 + 110bps)
    e.ledger.record_equity()
    clock.advance(300)
    market.update(d["token_key"], quote(price=1.5, v6=10_000))
    e.tick()
    m = metrics(e.ledger)
    p = e.ledger.positions("CLOSED")[0]
    assert m["closed_trades"] == 1 and m["open_positions"] == 0
    assert m["realized_pnl"] == pytest.approx(p["realized_pnl"], abs=0.01)
    assert m["cash"] == pytest.approx(10_000 + p["realized_pnl"], abs=0.01)
    assert m["equity"] == m["cash"]
    assert m["fees_paid"] == pytest.approx(p["entry_fee"] + p["exit_fee"], abs=0.01)
    assert m["win_rate"] == 1.0 and m["exit_reasons"] == {"volume_ratio": 1}
    assert m["unknown_orders"] == 0 and m["orders_by_state"] == {"FILLED": 2}
    assert "strategy" in compare([m])


def test_synthetic_replay_runs_both_ledgers_on_the_same_tape():
    r = replay.run(synthetic.tape())
    m = by_name(r)
    s, b = m["strategy"], m["baseline"]
    assert s["closed_trades"] == 1 and b["closed_trades"] == 1
    assert r["log"][0]["strategy"]["choice"].startswith("GOOD (solana:")
    assert r["log"][0]["baseline"]["choice"].startswith("TRAP (solana:")
    assert s["realized_pnl"] > 0 > b["realized_pnl"]
    assert s["exit_reasons"] == b["exit_reasons"] == {"volume_ratio": 1}


def test_replay_is_deterministic():
    assert replay.run(synthetic.tape())["metrics"] == replay.run(synthetic.tape())["metrics"]


def test_replay_with_a_lost_ack_reconciles_on_the_next_poll():
    r = replay.run(synthetic.tape(), faults=FaultPlan(timeout_after_fill={1}))
    m = by_name(r)
    assert r["log"][0]["strategy"]["entry"]["status"] == "UNKNOWN"
    assert m["strategy"]["closed_trades"] == 1 and m["strategy"]["unknown_orders"] == 0
    clean = by_name(replay.run(synthetic.tape()))
    assert m["strategy"]["realized_pnl"] == clean["strategy"]["realized_pnl"]


def test_replay_without_a_recorded_pick_stands_the_strategy_down():
    ev = synthetic.tape()
    ev[-1]["pick"]["labels"] = ["something else"]
    r = replay.run(ev)
    assert "pick unavailable" in r["log"][0]["strategy"]["note"]
    assert by_name(r)["strategy"]["closed_trades"] == 0


def test_absolute_gate_holds_the_strategy_back():
    m = by_name(replay.run(synthetic.tape(pick_worth=0.3)))
    assert m["strategy"]["closed_trades"] == 0 and m["baseline"]["closed_trades"] == 1
