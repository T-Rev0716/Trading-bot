"""Parser regression checks over a directory of fixtures saved by diagnose.py.

Run on tests/fixtures/solana (real responses) by test_fixtures_solana.py, and on a temp
directory by test_diagnose.py to prove the save/check round trip works."""
import glob
import json
import os

import fomo_api
import sanitize
from collect import parse_new_pools, parse_pair, parse_token_info, pick_pair, sol_holders
from journal import plain
from market import quote_from_pair
from dataclasses import asdict


def load_all(d: str) -> dict:
    out = {}
    for path in sorted(glob.glob(os.path.join(d, "*.json"))):
        with open(path) as f:
            doc = json.load(f)
        out[(doc["service"], doc["name"])] = doc
    return out


def check_sanitized(docs: dict):
    for key, doc in docs.items():
        assert doc.get("sanitized") is True, key
        blob = json.dumps(doc)
        assert not sanitize.JWT.search(blob), f"{key} holds a JWT"
        assert sanitize.data(doc) == doc, f"{key} is not fully sanitized"


def check_geckoterminal(docs: dict):
    if (doc := docs.get(("geckoterminal", "new_pools"))):
        tids = parse_new_pools(doc["body"], "solana")
        assert plain(tids) == doc["parsed"]
        assert tids and all(t.endswith(":1399811149") for t in tids)
    if (doc := docs.get(("geckoterminal", "token_info"))):
        g = parse_token_info(doc["body"])
        assert plain(g) == doc["parsed"]
        for k in ("mint_authority_open", "freeze_authority_open", "is_honeypot"):
            assert g[k] in (True, False, None), k
        assert g["top_10_share"] is None or 0 <= g["top_10_share"] <= 1
        assert g["holder_count"] is None or isinstance(g["holder_count"], int)


def check_dexscreener(docs: dict):
    for name in ("tokens_sample", "tokens_reference"):
        doc = docs.get(("dexscreener", name))
        if not doc or doc["parsed"] is None:
            continue
        p = pick_pair(doc["body"], "solana")
        assert plain({"flow": parse_pair(p),
                      "quote": asdict(quote_from_pair(p, 0.0)) if p else None}) == doc["parsed"]
        flow = doc["parsed"]["flow"]
        for k in ("buys_h1", "sells_h1", "buys_h6", "sells_h6", "trades_h24"):
            assert flow[k] is None or (isinstance(flow[k], int) and flow[k] >= 0), k


def check_solana_rpc(docs: dict):
    doc = docs.get(("solana_rpc", "holders"))
    if not doc:
        return
    calls = list(doc["body"]["calls"])

    def recorded(method, params):
        c = calls.pop(0)
        assert (c["method"], c["params"]) == (method, plain(params)), "RPC call order changed"
        return c["response"]["result"]
    h = sol_holders(doc["request"]["mint"], rpc=recorded)
    assert plain(h) == doc["parsed"]
    assert not calls, "parser made fewer RPC calls than were recorded"
    assert h["top_wallet_share"] is None or 0 <= h["top_wallet_share"] <= 1
    assert all(a["excluded"] in (None, "no_owner", "known_amm_or_burn", "program_owned")
               for a in h["accounts"])


def check_fomo(docs: dict):
    if (doc := docs.get(("fomo", "filter_tokens"))):
        rows = [fomo_api._row(r) for r in fomo_api._results(doc["body"])]
        assert plain(rows) == doc["parsed"]


ALL = (check_sanitized, check_geckoterminal, check_dexscreener, check_solana_rpc, check_fomo)
