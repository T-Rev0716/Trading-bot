import json

import httpx2
import pytest
from fastapi.testclient import TestClient
from typesafe_sdk import AsyncTypeSafeClient

import book
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
    monkeypatch.setattr(judge, "AsyncTypeSafeClient", lambda: AsyncTypeSafeClient(
        api_key="ts-test", transport=httpx2.MockTransport(handler)))
    with TestClient(judge.app) as c:
        yield c
    judge._client = None


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
    state = {"candidates": [{"label": "A [solana:aaa]", "summary": "s"},
                            {"label": "A [solana:bbb]", "summary": "t"}]}
    r = api.post("/judge", json={"question_set": "pick", "state": state}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["answers"]["best"]["choice"] in ("A [solana:aaa]", "A [solana:bbb]")
    bad = api.post("/judge", json={"question_set": "pick", "state": {}}, headers=AUTH)
    assert bad.status_code == 422


def test_upstream_422_is_passed_on(api):
    r = api.post("/judge", json={"question_set": "market", "state": {"make_422": True}},
                 headers=AUTH)
    assert r.status_code == 422


def test_book_routes(api):
    book.take({"token": {"ticker": "T", "address": "a", "network_id": 56}})
    assert api.get("/book/held", headers=AUTH).json()["held"]["ticker"] == "T"
    assert api.post("/book/release").status_code == 401
    assert api.post("/book/release", headers=AUTH).json()["released"]["ticker"] == "T"
    assert book.held() is None
