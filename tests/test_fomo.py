import base64
import json
import time

from fomo_api import Fomo, _results, _row, _jwt_exp


def jwt(exp):
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_jwt_exp():
    assert _jwt_exp(jwt(123)) == 123 and _jwt_exp("garbage") == 0.0


CODEX_ROW = {"token": {"address": "0xAbC", "networkId": 56, "symbol": "T",
                       "createdAt": 1700000000},
             "marketCap": "310000", "liquidity": "48000.5", "volume24": "610000",
             "priceUSD": "0.0031", "holders": 310, "change5m": "0.04", "change1": "0.22",
             "change4": None, "change12": "0.5", "change24": "0.61"}


def test_results_and_row():
    for wrapped in ([CODEX_ROW], {"data": {"filterTokens": {"results": [CODEX_ROW]}}},
                    {"results": [CODEX_ROW]}):
        rows = _results(wrapped)
        assert len(rows) == 1
    r = _row(CODEX_ROW)
    assert r["mcap"] == 310000 and r["liq"] == 48000.5 and r["holders"] == 310
    assert r["change"][3600] == 0.22 and r["change"][14400] is None


def test_tokens_maps_back_to_requested_ids(monkeypatch):
    f = Fomo()
    sol = dict(CODEX_ROW, token={"address": "MiNt", "networkId": 1399811149, "symbol": "S"})
    monkeypatch.setattr(f, "filter_tokens", lambda ids: {"results": [CODEX_ROW, sol]})
    out = f.tokens(["0xabc:56", "MiNt:1399811149", "mint:1399811149"])
    assert set(out) == {"0xabc:56", "MiNt:1399811149"}


def test_token_is_cached_until_near_expiry(monkeypatch):
    f = Fomo()
    reads = []
    monkeypatch.setattr(f, "_tab", lambda: {"webSocketDebuggerUrl": "ws://x"})
    monkeypatch.setattr(f, "_read_bearer", lambda tab: reads.append(1) or jwt(time.time() + 3600))
    t1, t2 = f.token(), f.token()
    assert t1 == t2 and len(reads) == 1
