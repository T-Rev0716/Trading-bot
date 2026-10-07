import subprocess
import sys

import pytest

import book
import main
import session
from journal import RecordJournal
from tests.conftest import ROOT
from tests.helpers import answers, dossier, engines, quote
from venue import FaultPlan

A = dossier(addr="MintA1111111111111111111111111111111111111", ticker="AAA")
B = dossier(addr="MintB1111111111111111111111111111111111111", ticker="AAA",
            buys_h1=90, sells_h1=10)


class Fomo:
    def token(self):
        return "t"


class Desk:
    def __init__(self):
        self.reports = []

    def read_x(self, handle):
        return {"handle": handle}

    def report(self, what):
        self.reports.append(what)


@pytest.fixture
def wired(monkeypatch):
    toks = [A, B]
    monkeypatch.setattr(main, "universe", lambda nets, pages: [t["tid"] for t in toks])
    monkeypatch.setattr(main, "shortlist", lambda fomo, ids, now=None: [
        {k: t[k] for k in ("addr", "net", "tid", "ticker", "chain", "token_key",
                           "mcap_usd", "liquidity_usd", "volume_h24", "price_usd",
                           "holder_count", "change", "age_minutes")} for t in toks])
    monkeypatch.setattr(main, "trade_counts", lambda t: {
        k: next(x for x in toks if x["tid"] == t["tid"])[k]
        for k in ("buys_h1", "sells_h1", "buys_h6", "sells_h6", "trades_h24",
                  "volume_h1", "volume_h6")})
    monkeypatch.setattr(main, "dossier", lambda t: {
        **next(x for x in toks if x["tid"] == t["tid"]), **t})


def judge_for(calls):
    def judge(qs, state):
        calls.append(qs)
        if qs == "pick":
            labels = [c["label"] for c in state["candidates"]]
            ans = {"worth_trading_at_all": {"type": "noul", "noul": 0.9}}
            if len(labels) > 1:
                ans["best"] = {"type": "choice", "choice": labels[0], "confidence": 0.8,
                               "probabilities": {l: 0.8 if i == 0 else 0.2
                                                 for i, l in enumerate(labels)}}
            return {"model": "jev-test", "answers": ans}
        full = answers(A)
        from questions import SETS
        return {"model": "jev-test", "answers": {k: full[k] for k in SETS[qs]}}
    return judge


def run_cycle(eng, clock, judge, journal=None, ts=None):
    journal = journal or RecordJournal()
    scan_fn = lambda ask, free: main.scan(Fomo(), judge, Desk(), free, ask)
    return session.cycle(eng, journal, session.SessionClock(clock()), ts or clock(),
                         scan_fn, judge), journal


def test_cycle_enters_both_ledgers_and_journals_it(wired):
    eng, market, clock = engines()
    for d in (A, B):
        market.update(d["token_key"], quote())
    calls = []
    out, j = run_cycle(eng, clock, judge_for(calls))
    assert out["strategy"]["entry"]["status"] == "FILLED"
    assert out["baseline"]["entry"]["status"] == "FILLED"
    assert out["baseline"]["choice"] == f"AAA ({B['token_key']})"     # higher buy share
    assert calls.count("market") == 2 and calls.count("pick") == 1
    kinds = [e["kind"] for e in j.events]
    assert kinds[:2] == ["cycle", "candidates"] and kinds[-1] == "entries"
    pick = next(e for e in j.events if e["kind"] == "judge")
    assert pick["request"]["labels"] == [f"AAA ({A['token_key']})", f"AAA ({B['token_key']})"]
    # both ledgers hold: the next cycle does not scan at all
    calls.clear()
    out, _ = run_cycle(eng, clock, judge_for(calls))
    assert out["skipped"] == {"strategy": "position_open", "baseline": "position_open"}
    assert calls == []


def test_unknown_order_blocks_the_strategy_but_not_the_baseline(wired):
    eng, market, clock = engines(faults=FaultPlan(timeout_after_fill={1}))
    for d in (A, B):
        market.update(d["token_key"], quote())
    calls = []
    out, _ = run_cycle(eng, clock, judge_for(calls))
    assert out["strategy"]["entry"]["status"] == "UNKNOWN"
    assert out["baseline"]["entry"]["status"] == "FILLED"
    eng["baseline"].ledger.db.execute("DELETE FROM positions WHERE ledger='baseline'")
    book.DB.execute("DELETE FROM bench_v2")
    calls.clear()
    out, _ = run_cycle(eng, clock, judge_for(calls), ts=clock() + 900)
    assert out["blocked"]["strategy"] == "unknown_order"
    assert calls == []                                    # no judge spend while blocked
    assert "strategy" not in out                          # no strategy entry attempted


def test_judge_rejection_benches_for_the_strategy_only(wired):
    eng, market, clock = engines()

    def judge(qs, state):
        r = judge_for([])(qs, state)
        if qs == "market":
            r["answers"]["momentum_already_spent"] = {"type": "noul", "noul": 0.99}
        return r
    market.update(A["token_key"], quote())
    market.update(B["token_key"], quote())
    out, _ = run_cycle(eng, clock, judge)
    assert out["stats"]["soft"] == {"momentum_already_spent": 2}
    assert book.benched(A["tid"]) == "strategy"
    assert out["baseline"]["entry"]["status"] == "FILLED"


def test_a_failed_scan_stands_the_cycle_down_and_is_journaled(wired):
    eng, market, clock = engines()
    j = RecordJournal()

    def boom(ask, free):
        raise ConnectionError("fomo down")
    out = session.cycle(eng, j, session.SessionClock(clock()), clock(), boom, judge_for([]))
    assert out["error"] == "RecordedError: ConnectionError: fomo down"
    assert [e["kind"] for e in j.events] == ["cycle", "candidates", "cycle_error"]


def test_live_flag_is_refused(tmp_path):
    r = subprocess.run([sys.executable, "main.py", "--live"], cwd=ROOT,
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "DESK_DB": str(tmp_path / "d.db")})
    assert r.returncode == 1 and "Live execution is disabled" in r.stderr


def test_there_is_no_order_delivery_code():
    import desk
    assert not hasattr(desk.Desk, "send_to_seats")
    src = "".join(open(f"{ROOT}/{f}").read() for f in ("main.py", "desk.py", "paper.py"))
    assert "SEATS_WEBHOOK_URL" not in src
