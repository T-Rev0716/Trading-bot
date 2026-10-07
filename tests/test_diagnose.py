"""Tests of the diagnostic itself: classification, schema checks, sanitizing, exit codes.

The bodies below are SHAPE PROBES written for these tests. They are not recorded API
responses, they are never saved under tests/fixtures, and passing these tests does not
verify any integration. Only real fixtures (test_fixtures_solana.py) can do that."""
import json

import pytest
import requests

import diagnose
import sanitize
from tests import fixture_checks

MINT = "ProbeMint1111111111111111111111111111111111"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcm9iZSJ9.c2lnbmF0dXJlc2ln"
SECRET = "ts-probe-secret-value-123456"

NEW_POOLS = {"data": [{"attributes": {"address": "PoolA", "pool_created_at":
                                      "2026-10-07T18:00:00Z", "reserve_in_usd": "12345.6"},
                       "relationships": {"base_token": {"data": {"id": f"solana_{MINT}"}}}}]}
TOKEN_INFO = {"data": {"attributes": {
    "address": MINT, "symbol": "PRB", "mint_authority": "no", "freeze_authority": "no",
    "twitter_handle": "probe", "gt_score_details": {"pool": 50},
    "holders": {"count": 812, "distribution_percentage":
                {"top_10": "41.5", "11_30": "20.5", "31_50": "8", "rest": "30"}}}}}
PAIR = {"chainId": "solana", "pairAddress": "PairA", "priceUsd": "0.00123",
        "liquidity": {"usd": 50000.0}, "volume": {"h1": 1000.0, "h6": 9000.0, "h24": 30000.0},
        "txns": {"h1": {"buys": 10, "sells": 5}, "h6": {"buys": 60, "sells": 40},
                 "h24": {"buys": 200, "sells": 150}}, "pairCreatedAt": 1791400000000}
RPC = {"getTokenSupply": {"value": {"amount": "1000000000", "decimals": 6,
                                    "uiAmountString": "1000"}},
       "getTokenLargestAccounts": {"value": [
           {"address": "AcctPool", "amount": "300000000", "decimals": 6},
           {"address": "AcctWallet", "amount": "20000000", "decimals": 6}]},
       "jsonParsed": {"value": [
           {"data": {"parsed": {"info": {"owner": "PoolState",
                                         "tokenAmount": {"amount": "300000000"}}}}},
           {"data": {"parsed": {"info": {"owner": "Wallet1",
                                         "tokenAmount": {"amount": "20000000"}}}}}]},
       "base64": {"value": [{"owner": "AmmProgram"}, {"owner": "11111111111111111111111111111111"}]}}


class FakeFetcher(diagnose.Fetcher):
    """Routes by URL to a shape probe, or to a failure."""

    def __init__(self, save_dir=None, fail=None, override=None):
        super().__init__(save_dir)
        self.fail, self.override = fail or {}, override or {}

    def _call(self, method, url, **kw):
        for frag, exc in self.fail.items():
            if frag in url:
                raise exc
        for frag, body in self.override.items():
            if frag in url:
                return 200, body
        if "new_pools" in url:
            return 200, NEW_POOLS
        if "/info" in url:
            return 200, TOKEN_INFO
        if "dexscreener" in url:
            return 200, {"pairs": [PAIR]}
        body = kw["json"]
        m = body["method"]
        key = m if m != "getMultipleAccounts" else body["params"][1]["encoding"]
        return 200, {"jsonrpc": "2.0", "id": 1, "result": RPC[key]}


@pytest.fixture(autouse=True)
def no_network_jev(monkeypatch):
    """conftest sets a dummy key; without this the Jev check would try the real API."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


def by(results):
    return {r.service: r for r in results}


def test_unreachable_hosts_are_blocked_not_failed(monkeypatch):

    def refuse(*a, **k):
        raise requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden")
    monkeypatch.setattr(requests, "request", refuse)
    r = by(diagnose.diagnose())
    assert [r[s].status for s in ("geckoterminal", "dexscreener", "solana_rpc")] == \
        ["BLOCKED"] * 3
    assert "egress proxy refused CONNECT to api.geckoterminal.com (403)" in \
        r["geckoterminal"].detail
    assert r["jev"].status == r["fomo"].status == "NOT_CONFIGURED"
    assert all(x.setup for x in r.values())                # every gap says how to fix it
    assert not any(v["result"] == "PASS" for x in r.values() for v in x.verifications)
    assert diagnose.exit_code(list(r.values())) == 2


def test_services_are_checked_independently():
    r = by(diagnose.diagnose(fetcher=FakeFetcher(fail={
        "geckoterminal": diagnose.Blocked("probe: gt down")})))
    assert r["geckoterminal"].status == "BLOCKED"
    assert r["dexscreener"].status == "OK" and r["solana_rpc"].status == "OK"


def test_a_clean_probe_passes_schema_and_verifications():
    r = by(diagnose.diagnose(fetcher=FakeFetcher()))
    assert r["geckoterminal"].status == r["dexscreener"].status == "OK"
    assert r["solana_rpc"].status == "OK"
    v = {x["claim"]: x["result"] for s in r.values() for x in s.verifications}
    assert v["holders.distribution_percentage is in percent (buckets sum to ~100)"] == "PASS"
    assert v["getTokenSupply amount is raw base units (amount == uiAmount * 10^decimals)"] \
        == "PASS"
    assert v["top_wallet_share is the first account not excluded as pool/curve/AMM"] == "PASS"


def test_missing_and_mistyped_fields_are_a_schema_mismatch():
    bad = json.loads(json.dumps(PAIR))
    del bad["txns"]["h1"]
    bad["priceUsd"] = 0.00123                             # a number, not a string
    r = by(diagnose.diagnose(fetcher=FakeFetcher(override={"dexscreener": {"pairs": [bad]}})))
    ep = r["dexscreener"].endpoints[0]
    assert r["dexscreener"].status == "SCHEMA_MISMATCH"
    assert "pairs[0].txns.h1" in ep.missing
    assert any(w.startswith("pairs[0].priceUsd") for w in ep.wrong_type)


def test_a_parser_crash_is_reported_not_raised():
    r = by(diagnose.diagnose(fetcher=FakeFetcher(override={"/info": {"data": {}}})))
    ep = next(e for e in r["geckoterminal"].endpoints if e.name == "token_info")
    assert ep.status == "SCHEMA_MISMATCH" and ep.parse_errors


def test_rate_limit_is_an_error():
    r = by(diagnose.diagnose(fetcher=FakeFetcher(fail={
        "dexscreener": diagnose.HttpError("429 rate limited")})))
    assert r["dexscreener"].status == "ERROR"
    assert diagnose.exit_code(list(r.values())) == 1


def test_credentials_never_reach_output_or_fixtures(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", SECRET)
    leaky = json.loads(json.dumps(TOKEN_INFO))
    leaky["data"]["attributes"]["description"] = f"key {SECRET} jwt {JWT}"
    leaky["authorization"] = f"Bearer {JWT}"
    leaky["cookie"] = "privy-token=abc"
    f = FakeFetcher(str(tmp_path), override={"/info": leaky})
    monkeypatch.setattr(diagnose, "check_jev", lambda f, paid: diagnose.Result(
        "jev", "ERROR", f"failed with key {SECRET} and Authorization: Bearer {JWT}"))
    res = diagnose.diagnose(fetcher=f)
    out = diagnose.render(res)
    files = "".join(p.read_text() for p in tmp_path.iterdir())
    for blob in (out, files):
        assert SECRET not in blob and JWT not in blob and "privy-token=abc" not in blob
    assert "[REDACTED]" in files


def test_sanitize_keeps_token_metadata_and_strips_credentials():
    row = {"token": {"address": MINT, "symbol": "PRB"}, "Authorization": "Bearer x",
           "url": "https://rpc.example/?api-key=abcdef123456&x=1"}
    s = sanitize.data(row)
    assert s["token"] == row["token"]
    assert s["Authorization"] == sanitize.REDACTED
    assert s["url"] == "https://rpc.example/?api-key=[REDACTED]&x=1"


def test_saved_fixtures_round_trip_through_the_regression_checks(tmp_path):
    diagnose.diagnose(fetcher=FakeFetcher(str(tmp_path)))
    docs = fixture_checks.load_all(str(tmp_path))
    assert {("geckoterminal", "new_pools"), ("geckoterminal", "token_info"),
            ("dexscreener", "tokens_sample"), ("solana_rpc", "holders")} <= set(docs)
    for check in fixture_checks.ALL:
        check(docs)


def test_the_regression_checks_catch_a_parser_change(tmp_path):
    diagnose.diagnose(fetcher=FakeFetcher(str(tmp_path)))
    docs = fixture_checks.load_all(str(tmp_path))
    docs[("geckoterminal", "token_info")]["parsed"]["top_10_share"] = 41.5   # percent
    with pytest.raises(AssertionError):
        fixture_checks.check_geckoterminal(docs)


def test_the_real_fixture_directory_holds_no_probes():
    import glob
    import os
    from tests.conftest import ROOT
    for path in glob.glob(os.path.join(ROOT, "tests", "fixtures", "solana", "*.json")):
        assert "Probe" not in open(path).read(), f"{path} is a probe, not a recording"


def test_jev_without_a_key_is_not_configured_and_makes_no_call(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: pytest.fail("network used"))
    r = diagnose.check_jev(FakeFetcher(), paid_call=True)
    assert r.status == "NOT_CONFIGURED" and "TYPESAFE_API_KEY" in r.detail
