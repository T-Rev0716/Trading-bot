"""Read-only diagnostics over a recorded run. Never part of the trading loop: nothing here
writes to a ledger or a journal, and no value is ever substituted for another.

    python crosscheck.py evidence  runs/<run_id>/journal.jsonl
        The rejection evidence each cycle recorded: reason counts and up to five examples
        per reason (raw value, parsed value, threshold, source field, fetch time).

    python crosscheck.py liquidity runs/<run_id>/journal.jsonl [--fomo] [--json out.json]
    python crosscheck.py liquidity --token solana:<mint> [--token ...] [--fomo]
        For at most five liquidity-rejected tokens, FOMO's liquidity next to DexScreener's
        for the same chain and full address, with every DexScreener pair listed.

Token selection (from a journal): the most recent cycle first; within a cycle the reasons
liquidity_below_min, liquidity_missing, liquidity_invalid in that order; examples in the
order they were recorded; each chain:address once; stop at five.

DexScreener pairs: only pairs whose chainId is the token's chain are compared; pairs on
other chains (an EVM address can exist on several) are counted, never used. Each pair's
own liquidity.usd is shown. They are NOT added up: a token's pools are separate markets,
and the desk itself uses one pool, the deepest by liquidity.usd on the token's chain
(collect.pick_pair). That pair is marked `selected`.

Outcomes are kept apart: `no_pairs` (DexScreener lists nothing for the address),
`no_pairs_on_chain` (pairs exist, none on this chain), `liquidity_missing` (pairs on the
chain, none with a usable liquidity.usd), `ok`, and `BLOCKED` / `ERROR` for the request.

FOMO's value is the one recorded in the journal (with its local fetch time) unless
--fomo is given, which re-fetches it now from a logged-in Chrome session. The two
providers are fetched at different moments; the report says when each was fetched.
"""
import argparse
import json
import sys
import time

import sanitize
from collect import DEX, DEX_CHAIN, FOMO_NET, pick_pair
from diagnose import Blocked, Fetcher, HttpError
from ids import split_key
from values import parse_metric, raw_for_record

LIMIT = 5
LIQUIDITY_REASONS = ("liquidity_below_min", "liquidity_missing", "liquidity_invalid")
POLICY = ("same chain and full address only; each pair's own liquidity.usd; not summed; "
          "selected = deepest liquidity.usd on the token's chain (collect.pick_pair)")


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def scan_results(events: list[dict]) -> list[dict]:
    """Every journaled scan result, oldest first, with the cycle it belongs to."""
    out = []
    for e in sorted(events, key=lambda e: e["seq"]):
        if e.get("kind") == "candidates" and "value" in e:
            out.append({"seq": e["seq"], "cycle_id": e["key"], "stats": e["value"]["stats"]})
    return out


def select_liquidity_rejections(events: list[dict], limit: int = LIMIT) -> list[dict]:
    picked, seen = [], set()
    for scan in reversed(scan_results(events)):
        ev = scan["stats"].get("evidence") or {}
        for reason in LIQUIDITY_REASONS:
            for ex in (ev.get(reason) or {}).get("examples", []):
                if ex["token_key"] in seen:
                    continue
                seen.add(ex["token_key"])
                picked.append({"token_key": ex["token_key"], "tid": ex.get("tid"),
                               "reason": reason, "cycle_id": scan["cycle_id"],
                               "recorded_fomo": ex})
                if len(picked) >= limit:
                    return picked
    return picked


def dexscreener_pairs(fetcher: Fetcher, key: str) -> dict:
    chain, addr = split_key(key)
    out = {"source": "dexscreener", "request": f"{DEX}/{addr}", "pairs": [],
           "other_chain_pairs": 0, "selected_pair": None, "selected_liquidity_usd": None,
           "selection_policy": POLICY}
    try:
        _, body = fetcher.get(f"{DEX}/{addr}")
    except Blocked as e:
        return out | {"status": "BLOCKED", "detail": str(e)}
    except HttpError as e:
        return out | {"status": "ERROR", "detail": str(e)}
    out["fetched_at_local"] = time.time()
    pairs = body.get("pairs") or []
    want = DEX_CHAIN.get(chain)
    same = [p for p in pairs if p.get("chainId") == want]
    out["other_chain_pairs"] = len(pairs) - len(same)
    sel = pick_pair(body, chain, addr) if same else None
    lower = addr.lower()
    for p in same:
        raw = (p.get("liquidity") or {}).get("usd")
        m = parse_metric(raw)
        base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
        role = "base" if (base.get("address") or "").lower() == lower else \
            "quote" if (quote.get("address") or "").lower() == lower else "neither"
        out["pairs"].append({
            "pair_address": p.get("pairAddress"), "dex_id": p.get("dexId"),
            "base": {"address": base.get("address"), "symbol": base.get("symbol")},
            "quote": {"address": quote.get("address"), "symbol": quote.get("symbol")},
            "token_role": role, "liquidity_usd_raw": raw_for_record(raw),
            "liquidity_status": m.status, "invalid_kind": m.kind,
            "liquidity_usd": m.value, "selected": p is sel})
    if not pairs:
        out["status"] = "no_pairs"
    elif not same:
        out["status"] = "no_pairs_on_chain"
    elif not any(x["liquidity_status"] == "ok" for x in out["pairs"]):
        out["status"] = "liquidity_missing"
    else:
        out["status"] = "ok"
    if sel is not None:
        s = next(x for x in out["pairs"] if x["selected"])
        out["selected_pair"] = s["pair_address"]
        out["selected_liquidity_usd"] = s["liquidity_usd"]
    return out


def fomo_now(tid: str) -> dict:
    """Current FOMO liquidity, read-only, from a logged-in Chrome session."""
    import requests
    import fomo_api
    try:
        requests.get(f"{fomo_api.CDP}/json/version", timeout=3)
    except requests.RequestException:
        return {"status": "NOT_CONFIGURED", "detail": f"no Chrome DevTools at {fomo_api.CDP}"}
    try:
        payload = fomo_api.Fomo().filter_tokens([tid])
    except fomo_api.FomoError as e:
        return {"status": "NOT_CONFIGURED", "detail": str(e)}
    except requests.exceptions.ProxyError as e:
        return {"status": "BLOCKED", "detail": sanitize.text(str(e))[:200]}
    except requests.RequestException as e:
        return {"status": "ERROR", "detail": sanitize.text(str(e))[:200]}
    fetched = time.time()
    rows = [fomo_api._row(r) for r in fomo_api._results(payload)]
    want = tid.split(":")[0]
    row = next((r for r in rows if r and fomo_api._key(r["address"], r["net"]) ==
                fomo_api._key(want, r["net"])), None)
    if row is None:
        return {"status": "no_row", "fetched_at_local": fetched}
    liq = row["metrics"]["liq"]
    return {"status": "ok", "source_field": liq["source_field"], "raw": liq["raw"],
            "parse_status": liq["status"], "invalid_kind": liq["invalid_kind"],
            "liquidity_usd": liq["value"], "fetched_at_local": fetched,
            "provider_timestamp": row["provider_timestamp"]}


def crosscheck(tokens: list[dict], fetcher: Fetcher, refetch_fomo: bool) -> list[dict]:
    out = []
    for tok in tokens[:LIMIT]:
        rec = tok.get("recorded_fomo") or {}
        fomo = {"recorded": {k: rec.get(k) for k in (
            "source_field", "raw", "parse_status", "invalid_kind", "parsed",
            "fetched_at_local", "provider_timestamp")} if rec else None}
        if refetch_fomo:
            tid = tok.get("tid") or _tid(tok["token_key"])
            fomo["current"] = fomo_now(tid)
        dex = dexscreener_pairs(fetcher, tok["token_key"])
        f_val = ((fomo.get("current") or {}).get("liquidity_usd")
                 if refetch_fomo else rec.get("parsed"))
        d_val = dex["selected_liquidity_usd"]
        out.append({"token_key": tok["token_key"], "rejected_for": tok.get("reason"),
                    "cycle_id": tok.get("cycle_id"), "fomo": fomo, "dexscreener": dex,
                    "comparison": {
                        "fomo_value_used": "current" if refetch_fomo else "recorded",
                        "fomo_liquidity_usd": f_val,
                        "dexscreener_selected_pair_liquidity_usd": d_val,
                        "ratio_dex_over_fomo": (round(d_val / f_val, 4)
                                                if f_val and d_val is not None else None),
                        "note": "fetched at different times; neither value replaces the "
                                "other anywhere in the desk"}})
    return sanitize.data(out)


def _tid(key: str) -> str:
    chain, addr = split_key(key)
    return f"{addr}:{FOMO_NET[chain]}"


def render_evidence(events: list[dict]) -> str:
    lines = []
    for s in scan_results(events):
        ev = s["stats"].get("evidence")
        if ev is None:
            lines.append(f"{s['cycle_id']}: no evidence recorded (journal predates it)")
            continue
        lines.append(f"{s['cycle_id']}: " + ", ".join(f"{r} x{v['count']}"
                                                      for r, v in ev.items()))
        for r, v in ev.items():
            for x in v["examples"]:
                lines.append(f"    {r:<22} {x['token_key']}  {x['source']}.{x['source_field']}"
                             f" raw={x['raw']!r} parsed={x['parsed']} "
                             f"{x['parse_status']}{'/' + x['invalid_kind'] if x['invalid_kind'] else ''}"
                             f" threshold={x['threshold']} fetched_at_local={x['fetched_at_local']}"
                             f" provider_ts={x['provider_timestamp']}")
    return "\n".join(sanitize.text(x) for x in lines)


def render_crosscheck(rows: list[dict]) -> str:
    out = []
    for r in rows:
        c, d = r["comparison"], r["dexscreener"]
        out.append(f"{r['token_key']}  rejected_for={r['rejected_for']}  "
                   f"FOMO({c['fomo_value_used']})={c['fomo_liquidity_usd']}  "
                   f"DexScreener={d['status']} selected={c['dexscreener_selected_pair_liquidity_usd']}"
                   f"  ratio={c['ratio_dex_over_fomo']}")
        if d.get("detail"):
            out.append(f"    {d['detail']}")
        for p in d["pairs"]:
            out.append(f"    {'*' if p['selected'] else ' '} {p['dex_id']} {p['pair_address']} "
                       f"{p['base']['symbol']}/{p['quote']['symbol']} role={p['token_role']} "
                       f"liquidity.usd={p['liquidity_usd_raw']!r} ({p['liquidity_status']})")
        if d["other_chain_pairs"]:
            out.append(f"    {d['other_chain_pairs']} pair(s) on other chains, not used")
    out.append(f"policy: {POLICY}")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["evidence", "liquidity"])
    ap.add_argument("journal", nargs="?")
    ap.add_argument("--token", action="append", default=[], help="chain:address")
    ap.add_argument("--fomo", action="store_true", help="re-fetch FOMO now (needs Chrome)")
    ap.add_argument("--json", metavar="PATH")
    a = ap.parse_args()
    events = load(a.journal) if a.journal else []
    if a.mode == "evidence":
        if not a.journal:
            sys.exit("evidence needs a journal")
        print(render_evidence(events))
        sys.exit(0)
    toks = [{"token_key": k} for k in a.token] or select_liquidity_rejections(events)
    if not toks:
        sys.exit("no liquidity-rejected tokens with recorded evidence in that journal")
    rows = crosscheck(toks, Fetcher(), a.fomo)
    print(render_crosscheck(rows))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=1)
    sys.exit(2 if any(r["dexscreener"]["status"] in ("BLOCKED", "ERROR") for r in rows)
             else 0)
