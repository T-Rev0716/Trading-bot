import inspect

import pytest

import paper
import scenarios
from broker import Broker
from journal import JournaledBroker, NullJournal
from ledger import Ledger, LedgerError, connect
from tests.helpers import dossier, engines, quote
from venue import PaperVenue


def test_entry_with_expected_slippage_over_max_is_rejected_before_sending():
    eng, market, _ = engines(names=("strategy",))
    e, d = eng["strategy"], dossier()
    e.paper_cfg = dict(e.paper_cfg, max_slippage_bps=400)
    market.update(d["token_key"], quote(liq=15_000))      # $300 is 2% of it: 450 bps
    r = e.enter(d, 1.0, "c1")
    assert r["status"] == "SKIPPED" and r["reason"] == "slippage_over_max"
    assert r["expected_slippage_bps"] == 450
    assert e.ledger.orders() == []                         # nothing reached the venue


def test_starting_cash_is_configurable_and_never_silently_changed(tmp_path):
    db = connect(str(tmp_path / "p.db"))
    assert Ledger(db, "strategy", starting_cash=1_500).cash() == 1_500
    assert Ledger(db, "strategy").cash() == 1_500                  # reopen: kept
    with pytest.raises(LedgerError, match="opened with"):
        Ledger(db, "strategy", starting_cash=10_000)
    eng, _, _ = engines(starting_cash=2_500.0)
    assert {e.ledger.starting_cash() for e in eng.values()} == {2_500.0}


def test_a_1500_bank_never_clears_the_fee_floor():
    r = scenarios.run("trap", 1_500.0)
    assert all(m["closed_trades"] == 0 and m["orders_by_state"] == {}
               for m in r["metrics"].values())
    entries = [e for e in r["events"] if e["kind"] == "entries"][0]["results"]
    assert entries["strategy"]["entry"]["reason"] == "fee_floor"
    assert entries["baseline"]["entry"]["reason"] == "fee_floor"


def test_blind_closes_are_reported_as_assumptions_with_a_zero_recovery_stress():
    m = scenarios.run("unsellable", 10_000.0)["metrics"]["strategy"]
    assert m["blind_closes"] == 1 and m["realized_pnl_observed"] == 0
    assert m["realized_pnl_assumed"] == m["realized_pnl"] < 0
    assert m["blind_close_assumed_proceeds"] > 0
    # zero recovery: the whole $300 ticket and its $1.35 entry fee are gone
    assert m["stress_zero_recovery_equity"] == pytest.approx(10_000 - 301.35, abs=0.01)
    assert m["stress_zero_recovery_return_pct"] == pytest.approx(-3.0135, abs=0.001)


def test_paper_venue_is_the_only_broker():
    assert isinstance(PaperVenue(connect(":memory:")), Broker)
    with pytest.raises(RuntimeError, match="live execution is disabled"):
        paper.PaperEngine(Ledger(connect(":memory:"), "x"), object(), None)

    class Other:
        def submit(self, *a): ...
        def lookup(self, *a): ...
    with pytest.raises(RuntimeError, match="live execution is disabled"):
        paper.PaperEngine(Ledger(connect(":memory:"), "x"),
                          JournaledBroker(Other(), NullJournal()), None)


def test_no_other_broker_implementation_exists():
    import glob
    import os
    from tests.conftest import ROOT
    impls = []
    for path in glob.glob(os.path.join(ROOT, "*.py")):
        src = open(path).read()
        for block in src.split("\nclass ")[1:]:
            name = block.split("(")[0].split(":")[0].strip()
            body = block.split("\nclass ")[0]
            if "def submit(" in body and "def lookup(" in body:
                impls.append(name)
    assert sorted(impls) == ["Broker", "JournaledBroker", "PaperVenue"]
    assert "Broker" in inspect.getsource(__import__("broker"))
