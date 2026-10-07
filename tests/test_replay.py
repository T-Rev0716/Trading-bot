"""Record, then replay: the replayed run must reproduce orders, balances, positions and
equity row for row, through outages and lost acknowledgements."""
import copy

import pytest

import replay
import scenarios
import session
from journal import RecordJournal, ReplayDivergence
from market import Quote, StaticMarket
from paper import build
from tests.helpers import T0, answers, dossier
from venue import FaultPlan


def snapshot(engines) -> dict:
    db = next(iter(engines.values())).ledger.db
    dump = lambda q: [tuple(r) for r in db.execute(q)]
    return {"accounts": dump("SELECT * FROM accounts ORDER BY ledger"),
            "orders": dump("SELECT * FROM orders ORDER BY ledger, created_at, order_id"),
            "order_events": dump("SELECT * FROM order_events ORDER BY rowid"),
            "positions": dump("SELECT * FROM positions ORDER BY ledger, position_id"),
            "marks": dump("SELECT * FROM marks ORDER BY rowid")}


def assert_same(recorded, replayed):
    a, b = snapshot(recorded), snapshot(replayed)
    for table in a:
        assert a[table] == b[table], table


@pytest.mark.parametrize("name", list(scenarios.SCENARIOS))
@pytest.mark.parametrize("bank", scenarios.BANKS)
def test_every_scenario_replays_exactly(name, bank):
    rec = scenarios.run(name, bank)
    rep = replay.run(rec["events"])
    assert_same(rec["engines"], rep["engines"])
    assert rec["metrics"] == {m["ledger"]: m for m in rep["metrics"]}


# a harder run: every kind of outage, on both the entry and the exit side
A = dossier(addr="MintAAAA1111111111111111111111111111111111", ticker="AAA")
B = dossier(addr="MintBBBB1111111111111111111111111111111111", ticker="BBB",
            buys_h1=90, sells_h1=10)


def hostile_run():
    clock = session.SessionClock(T0)
    journal = RecordJournal()
    market = StaticMarket()
    faults = FaultPlan(timeout_before_receipt={1},       # strategy's first entry: lost
                       timeout_after_fill={2, 5},        # baseline entry, a later exit
                       lookup_unavailable={2, 3})
    engines = build(":memory:", market, clock=clock, journal=journal, faults=faults,
                    starting_cash=10_000.0)
    session.start(engines, journal, clock)
    cands = [(A, answers(A)), (B, answers(B))]

    def judge(qs, state):
        labels = [c["label"] for c in state["candidates"]]
        a = {"worth_trading_at_all": {"type": "noul", "noul": 0.9}}
        if len(labels) > 1:
            a["best"] = {"type": "choice", "choice": labels[0], "confidence": 0.8,
                         "probabilities": {lb: 0.5 for lb in labels}}
        return {"model": "jev-test", "answers": a}

    prices = {A["token_key"]: [1.0, 1.1, None, 1.2, 1.3, 1.25, 1.2, 1.1, 1.0, 0.9],
              B["token_key"]: [1.0, 0.9, 0.8, None, None, None, 0.7, 0.7, 0.6, 0.6]}
    for i in range(10):
        ts = T0 + 300 * i
        for k, p in prices.items():
            if p[i] is None:
                market.fail.add(k)
            else:
                market.fail.discard(k)
                market.update(k, Quote(p[i], 100_000.0, 10_000.0 if i == 7 else 80_000.0,
                                       300_000.0, ts))
        session.poll(engines, journal, clock, ts)
        if i % 2 == 0:
            session.cycle(engines, journal, clock, ts + 1,
                          lambda ask, free: (cands, {"n": 2}), judge)
    return engines, journal.events


def test_a_reconciled_position_can_still_be_closed_blind():
    """regression: a position created by reconciliation had no liquidity on record, so
       its blind close was rejected (no_liquidity) every poll while it went unmeasured"""
    engines, events = hostile_run()
    b = engines["baseline"].ledger
    pid = b.db.execute("SELECT position_id FROM positions WHERE ledger='baseline' "
                       "ORDER BY opened_at LIMIT 1").fetchone()[0]
    closes = b.db.execute("SELECT state, flags FROM orders WHERE position_id=? "
                          "ORDER BY created_at", (pid,)).fetchall()
    assert tuple(closes[0]) == ("FILLED", '["blind_close"]')


def test_hostile_run_exercises_every_failure_and_replays_exactly():
    engines, events = hostile_run()
    io = lambda kind, **kw: [e for e in events if e["kind"] == kind and
                             all(e.get(k) == v for k, v in kw.items())]
    assert io("quote", error="QuoteUnavailable"), "no quote outage recorded"
    assert io("submit", error="DeliveryTimeout"), "no lost acknowledgement recorded"
    assert io("lookup", error="LookupUnavailable"), "no venue outage recorded"
    reasons = {r[0] for e in engines.values() for r in e.ledger.db.execute(
        "SELECT reason FROM orders WHERE reason IS NOT NULL")}
    assert "never_received" in reasons
    flags = "".join(r[0] or "" for r in next(iter(engines.values())).ledger.db.execute(
        "SELECT flags FROM orders"))
    assert "blind_close" in flags

    rep = replay.run(events)
    assert_same(engines, rep["engines"])


def test_replay_never_sees_a_quote_from_after_its_decision():
    _, events = hostile_run()
    now = None
    for e in events:
        if e["kind"] in ("poll", "cycle"):
            now = e["ts"]
        if e["kind"] == "quote" and "value" in e:
            assert e["value"]["ts"] <= now, f"seq {e['seq']} quote from the future"


def test_tampered_quote_diverges():
    _, events = hostile_run()
    ev = copy.deepcopy(events)
    q = next(e for e in ev if e["kind"] == "quote" and "value" in e)
    q["value"]["price_usd"] *= 1.5
    with pytest.raises(ReplayDivergence):
        replay.run(ev)


def test_reordered_events_diverge_instead_of_being_looked_up():
    _, events = hostile_run()
    ev = copy.deepcopy(events)
    quotes = [e for e in ev if e["kind"] == "quote"]
    a = quotes[0]
    b = next(q for q in quotes if q["key"] != a["key"])
    a["seq"], b["seq"] = b["seq"], a["seq"]       # same events, different order
    with pytest.raises(ReplayDivergence, match="recorded key"):
        replay.run(ev)


def test_missing_or_truncated_events_diverge():
    _, events = hostile_run()
    with pytest.raises(ReplayDivergence, match="gaps"):
        replay.run(events[:5] + events[6:])
    cut = next(i for i, e in enumerate(events) if e["kind"] == "submit")
    with pytest.raises(ReplayDivergence, match="after the last recorded event"):
        replay.run(events[:cut])


def test_changed_thresholds_are_refused(monkeypatch):
    _, events = hostile_run()
    monkeypatch.setitem(session.EXITS, "min_volume_ratio", 0.3)
    with pytest.raises(ReplayDivergence, match="EXITS"):
        replay.run(events)


def test_resume_continues_the_same_journal(tmp_path):
    path, db = str(tmp_path / "j.jsonl"), str(tmp_path / "p.db")
    market = StaticMarket()
    market.update(A["token_key"], Quote(1.0, 1e5, 8e4, 3e5, T0))
    clock = session.SessionClock(T0)
    j = RecordJournal(path)
    eng = build(db, market, clock=clock, journal=j)
    session.start(eng, j, clock)
    session.poll(eng, j, clock, T0)
    session.cycle(eng, j, clock, T0 + 1, lambda a, f: ([(A, answers(A))], {}),
                  lambda qs, st: {"model": "m", "answers": {
                      "worth_trading_at_all": {"type": "noul", "noul": 0.9}}})
    # the process restarts on the same run directory
    clock2 = session.SessionClock(T0 + 600)
    j2 = RecordJournal(path)
    eng2 = build(db, market, clock=clock2, journal=j2)
    session.start(eng2, j2, clock2, resumed=True)
    session.poll(eng2, j2, clock2, T0 + 600)
    rep = replay.run(replay.load(path))
    assert_same(eng2, rep["engines"])
