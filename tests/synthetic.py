"""A small synthetic tape. It exists to exercise the replay and report machinery, and it
is rigged by construction: the trap token looks better to the rules than the good one.
Its results say nothing about whether the strategy has an edge in a real market."""
from ids import label
from tests.helpers import T0, answers, dossier

GOOD = dossier(addr="GoodMint11111111111111111111111111111111111", ticker="GOOD",
               buys_h1=60, sells_h1=40, buys_h6=300, sells_h6=240)
TRAP = dossier(addr="TrapMint11111111111111111111111111111111111", ticker="TRAP",
               buys_h1=85, sells_h1=15, buys_h6=400, sells_h6=100)
GOOD_PATH = [1.00, 1.05, 1.12, 1.20, 1.30, 1.38, 1.40, 1.36]
TRAP_PATH = [1.00, 1.02, 0.90, 0.75, 0.60, 0.50, 0.42, 0.40]


def _q(key, ts, price, v6, v24=300_000.0):
    return {"type": "quote", "ts": ts, "token_key": key,
            "quote": {"price_usd": price, "liquidity_usd": 100_000.0, "volume_h6": v6,
                      "volume_h24": v24, "ts": ts}}


def tape(pick_worth=0.8) -> list[dict]:
    ev = []
    for i, (pg, pt) in enumerate(zip(GOOD_PATH, TRAP_PATH)):
        ts = T0 + 300 * i
        last = i == len(GOOD_PATH) - 1
        ev.append(_q(GOOD["token_key"], ts, pg, 10_000.0 if last else 80_000.0))
        ev.append(_q(TRAP["token_key"], ts, pt, 10_000.0 if last else 80_000.0))
    trap_answers = answers(TRAP, shape={"type": "choice", "choice": "one_buyer",
                                        "confidence": 0.7,
                                        "probabilities": {"crowd": 0.1, "one_buyer": 0.7,
                                                          "fading": 0.1, "too_early": 0.1}})
    ev.append({"type": "cycle", "ts": T0, "cycle_id": "c1",
               "candidates": [{"d": GOOD, "answers": answers(GOOD)},
                              {"d": TRAP, "answers": trap_answers}],
               "pick": {"labels": [label(GOOD)],
                        "response": {"model": "jev-synthetic", "answers": {
                            "worth_trading_at_all": {"type": "noul", "noul": pick_worth}}}}})
    return ev
