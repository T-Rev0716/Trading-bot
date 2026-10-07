import json

import httpx2
import pytest
from fastapi.testclient import TestClient
from typesafe_sdk import AsyncTypeSafeClient

import judge

AUTH = {"Authorization": "Bearer test-secret"}
seen = []


def answer(q):
    if q["type"] == "noul":
        return {"type": "noul", "noul": 0.7}
    if q["type"] == "choice":
        labels = list(q["criteria"])
        p = {l: 1 / len(labels) for l in labels}
        return {"type": "choice", "choice": labels[0], "confidence": 0.6, "probabilities": p}
    levels = {str(i): c for i, c in enumerate(q["criteria"])}
    return {"type": "score", "score": 1.5, "confidence": 0.5, "legend": levels,
            "probabilities": {k: 1 / len(levels) for k in levels}}


def handler(request):
    body = json.loads(request.content)
    seen.append(body)
    if body["state"].get("make_422"):
        return httpx2.Response(422, json={"detail": [{"loc": ["body"], "msg": "bad",
                                                      "type": "x"}]})
    return httpx2.Response(200, json={
        "model": "jev-1.13.0",
        "answers": {k: answer(q) for k, q in body["questions"].items()},
        "usage": {"input_tokens": 100, "output_tokens": 5}})


@pytest.fixture
def api(monkeypatch):
    seen.clear()
    judge._client = None
    # judge.DESK_SECRET was read from the environment at import. Pin it, so the tests do
    # not depend on whatever DESK_SECRET the shell running pytest happens to export.
    monkeypatch.setattr(judge, "DESK_SECRET", "test-secret")
    monkeypatch.setattr(judge, "AsyncTypeSafeClient", lambda: AsyncTypeSafeClient(
        api_key="ts-test", transport=httpx2.MockTransport(handler)))
    with TestClient(judge.app) as c:
        yield c
    judge._client = None


def test_judge_tests_pass_with_a_different_shell_secret(tmp_path):
    """regression: with DESK_SECRET exported to anything else, every authorised request
       here used to get 401"""
    import os
    import subprocess
    import sys
    from tests.conftest import ROOT
    env = {**os.environ, "DESK_SECRET": "a-different-shell-secret",
           "DESK_DB": str(tmp_path / "d.db")}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_judge.py", "-k", "not different_shell_secret"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:]


def test_bad_secret(api):
    r = api.post("/judge", json={"question_set": "market", "state": {}},
                 headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_unknown_set(api):
    r = api.post("/judge", json={"question_set": "vibes", "state": {}}, headers=AUTH)
    assert r.status_code == 422


@pytest.mark.parametrize("qs", ["market", "solana", "bsc", "robinhood", "social"])
def test_every_set_round_trips_through_the_sdk(api, qs):
    r = api.post("/judge", json={"question_set": qs, "state": {"ticker": "T"}}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "jev-1.13.0"
    assert set(body["answers"]) == set(judge.SETS[qs])
    assert len(seen) == 1                        # one call per set, never one per question


def test_pick_builds_options_from_state(api):
    state = {"candidates": [{"label": "A (solana:aaa)", "summary": "s"},
                            {"label": "A (solana:bbb)", "summary": "t"}]}
    r = api.post("/judge", json={"question_set": "pick", "state": state}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["answers"]["best"]["choice"] in ("A (solana:aaa)", "A (solana:bbb)")
    one = {"candidates": state["candidates"][:1]}
    r = api.post("/judge", json={"question_set": "pick", "state": one}, headers=AUTH)
    assert set(r.json()["answers"]) == {"worth_trading_at_all"}
    bad = api.post("/judge", json={"question_set": "pick", "state": {}}, headers=AUTH)
    assert bad.status_code == 422


def test_upstream_422_is_passed_on(api):
    r = api.post("/judge", json={"question_set": "market", "state": {"make_422": True}},
                 headers=AUTH)
    assert r.status_code == 422


def test_no_book_or_order_routes(api):
    paths = {r.path for r in judge.app.routes}
    assert not any(p.startswith("/book") for p in paths)
