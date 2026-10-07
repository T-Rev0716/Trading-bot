"""SCAN and VET. Jev fetches nothing, so this is the code that goes and gets everything.

SCAN runs universe and shortlist, VET runs trade_counts and dossier.
Every field name the rest of the desk reads is set here, once.
"""
import collections
import logging
import os
import time

import requests

from fomo_api import Fomo
from ids import CHAIN_NAME, token_key
from values import raw_for_record

GT  = "https://api.geckoterminal.com/api/v2"
DEX = "https://api.dexscreener.com/latest/dex/tokens"
SOL_RPC = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
log = logging.getLogger("desk.collect")

# the three chains the desk trades, plus Base which shares the BSC question set
GT_NET   = dict(CHAIN_NAME)
FOMO_NET = {v: k for k, v in GT_NET.items()}
# DexScreener chainId per desk chain. An EVM address can exist on several chains, so
# pairs are filtered to the token's own chain. If Robinhood pairs never match, check
# the chainId DexScreener uses for it and fix it here.
DEX_CHAIN = {"solana": "solana", "robinhood": "robinhood", "bsc": "bsc", "base": "base"}

GT_PER_MINUTE = 10                  # free tier
_gt_calls = collections.deque()


def gt_get(path: str, **params) -> dict:
    """Every GeckoTerminal call goes through here, so the desk can never pass 10/min.
       It waits for a slot rather than earning a 429."""
    while len(_gt_calls) >= GT_PER_MINUTE:
        wait = 60.5 - (time.monotonic() - _gt_calls[0])
        if wait > 0:
            time.sleep(wait)
        _gt_calls.popleft()
    _gt_calls.append(time.monotonic())
    r = requests.get(f"{GT}{path}", params=params, timeout=20,
                     headers={"Accept": "application/json"})
    r.raise_for_status()                    # a 429 here means another process shares the IP
    return r.json()


def created_epoch(created) -> float | None:
    """createdAt comes back as epoch seconds or milliseconds depending on the row.
       Returns epoch seconds, or None. Unknown stays unknown."""
    if not created:
        return None
    try:
        c = float(created)
    except (TypeError, ValueError):
        return None
    return c / 1000 if c > 1e11 else c          # milliseconds -> seconds


def age_minutes(created, now: float | None = None) -> float | None:
    """A missing launch time is not a fresh launch: None stays None."""
    c = created_epoch(created)
    if c is None:
        return None
    return max(0.0, ((time.time() if now is None else now) - c) / 60)


def universe(nets=("solana", "bsc", "robinhood"), pages=2) -> list[str]:
    """Where the whole thing starts. Fresh pools per chain -> ['<addr>:<netId>', ...].
       Costs one GeckoTerminal slot per chain per page, so keep pages small."""
    ids, seen = [], set()
    for net in nets:
        for page in range(1, pages + 1):
            try:
                r = gt_get(f"/networks/{net}/new_pools", page=page)
            except Exception as e:
                log.warning("new_pools %s p%d failed: %s", net, page, e)
                break
            for tid in parse_new_pools(r, net):
                if tid not in seen:
                    seen.add(tid)
                    ids.append(tid)
    return ids


def parse_new_pools(body: dict, net: str) -> list[str]:
    """GT new_pools body -> ['<base token addr>:<netId>', ...], in response order."""
    out = []
    for pool in body.get("data", []):
        base = ((pool.get("relationships") or {}).get("base_token") or {})
        gid = (base.get("data") or {}).get("id")            # 'solana_<addr>'
        if not gid or "_" not in gid:
            continue
        out.append(f"{gid.split('_', 1)[1]}:{FOMO_NET[net]}")
    return out


def normalise(tid: str, m: dict, now: float | None = None) -> dict:
    """FOMO's field names become the desk's field names, once, here.
       Every file downstream reads these names and only these."""
    addr, net = tid.split(":")
    chain = GT_NET.get(int(net))
    return {"addr": addr, "net": int(net), "tid": tid, "ticker": m["symbol"], "chain": chain,
            "token_key": token_key(chain, addr) if chain else None,
            "mcap_usd": m["mcap"], "liquidity_usd": m["liq"],
            "volume_h24": m["vol24"], "price_usd": m["price"],
            "holder_count": m["holders"] or None,
            "change": {"5m": m["change"].get(300), "1h": m["change"].get(3600),
                       "4h": m["change"].get(14400), "12h": m["change"].get(43200),
                       "24h": m["change"].get(86400)},
            "created_at": created_epoch(m["created"]),
            "age_minutes": age_minutes(m["created"], now),
            # where each screened number came from, for rejection evidence
            "data_quality": _quality(m),
            "fetched_at_local": m.get("fetched_at"),
            "provider_timestamp": m.get("provider_timestamp")}


DESK_METRIC = {"liq": "liquidity_usd", "vol24": "volume_h24", "mcap": "mcap_usd"}


def _quality(m: dict) -> dict:
    """Per screened field: source, source field, raw value and parse status. Rows built
       without metrics (older callers, tests) get a status derived from the value."""
    q = {}
    for k, field in DESK_METRIC.items():
        info = (m.get("metrics") or {}).get(k)
        if info is None:
            v = m.get(k)
            info = {"source": "fomo", "source_field": None, "raw": v,
                    "status": "missing" if v is None else "ok", "invalid_kind": None}
        q[field] = {x: info.get(x) for x in ("source", "source_field", "raw", "status",
                                             "invalid_kind")}
    q["age_minutes"] = {"source": "fomo", "source_field": m.get("created_field"),
                        "raw": raw_for_record(m.get("created")),
                        "status": "missing" if created_epoch(m.get("created")) is None
                        else "ok", "invalid_kind": None}
    return q


def shortlist(fomo: Fomo, ids: list[str], now: float | None = None) -> list[dict]:
    """Pass one over everything FOMO knows. No network beyond FOMO itself:
       one call per twenty tokens, and not a single request per token.
       Every row is current: watched tokens are re-fetched here, never reused."""
    out = []
    for tid, m in fomo.tokens(ids).items():             # 20 per call
        t = normalise(tid, m, now)
        if t["net"] in GT_NET:
            out.append(t)
    # turnover ranks the queue. It orders work, it does not decide anything
    out.sort(key=lambda t: (t["volume_h24"] or 0) / max(t["mcap_usd"] or 0, 1),
             reverse=True)
    return out


_NO_TRADES = {"buys_h1": None, "sells_h1": None, "buys_h6": None, "sells_h6": None,
              "trades_h24": None, "volume_h1": None, "volume_h6": None}


def best_pair(chain: str, addr: str) -> dict | None:
    """The deepest DexScreener pair for this token on its own chain, or None.
       Raises on a network failure: the caller decides what missing means."""
    r = requests.get(f"{DEX}/{addr}", timeout=20)
    r.raise_for_status()
    return pick_pair(r.json(), chain, addr)


def pick_pair(body: dict, chain: str, addr: str = "") -> dict | None:
    """The deepest pair on the token's own chain from a DexScreener tokens body."""
    pairs = body.get("pairs") or []
    want = DEX_CHAIN.get(chain)
    mine = [p for p in pairs if p.get("chainId") == want]
    if pairs and not mine:
        log.warning("dexscreener has %s only on %s, not %s", addr,
                    sorted({p.get("chainId") for p in pairs}), want)
    if not mine:
        return None
    return max(mine, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


def trade_counts(t: dict) -> dict:
    """buys and sells per window. FOMO does not return them, DexScreener does.
       Called ONLY for tokens that already cleared the free checks. One per token,
       so this runs on tens, never on the whole universe."""
    try:
        p = best_pair(t["chain"], t["addr"])
    except Exception as e:
        log.warning("dexscreener %s failed: %s", t["ticker"], e)
        return dict(_NO_TRADES)
    return parse_pair(p)


def parse_pair(p: dict | None) -> dict:
    """One DexScreener pair -> the desk's flow fields. None -> every field null."""
    if p is None:
        return dict(_NO_TRADES)
    x, v = p.get("txns") or {}, p.get("volume") or {}
    win = lambda w, side: (x.get(w) or {}).get(side)
    b24, s24 = win("h24", "buys"), win("h24", "sells")
    return {"buys_h1": win("h1", "buys"), "sells_h1": win("h1", "sells"),
            "buys_h6": win("h6", "buys"), "sells_h6": win("h6", "sells"),
            "trades_h24": b24 + s24 if b24 is not None and s24 is not None else None,
            "volume_h1": v.get("h1"), "volume_h6": v.get("h6")}


def flag(v) -> bool | None:
    """GeckoTerminal answers flags as true/false, 'yes'/'no', an address, or 'unknown'.
       One meaning out: True, False, or None for not known."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("yes", "true", "1"):
            return True
        if s in ("no", "false", "0"):
            return False
        if len(s) >= 32 and s.isalnum():           # an authority address is set
            return True
    return None


def share(pct) -> float | None:
    """GeckoTerminal distribution is a percent string. The desk uses fractions."""
    try:
        return float(pct) / 100 if pct is not None else None
    except (TypeError, ValueError):
        return None


def dossier(t: dict) -> dict:
    """One GT call per token. Fills what the chain actually has, null where it does not."""
    net = t["chain"]
    g = parse_token_info(gt_get(f"/networks/{net}/tokens/{t['addr']}/info"))
    # GT first, FOMO as the fallback. On Robinhood GT is null and FOMO is all you get.
    d = {**t, **g, "holder_count": g["holder_count"] or t["holder_count"],
         "top_wallet_share": None}

    # Solana only: exact top wallet share, free, off the public RPC
    if t["net"] == 1399811149:
        try:
            d["top_wallet_share"] = sol_top_wallet(t["addr"])
        except Exception as e:
            log.warning("solana rpc %s failed: %s", t["ticker"], e)   # stays null
    return d


def parse_token_info(body: dict) -> dict:
    """GT token info body -> the desk's GT-derived fields. Units: shares are fractions."""
    a = body["data"]["attributes"]
    holders = a.get("holders") or {}
    return {"holder_count": holders.get("count"),
            "top_10_share": share((holders.get("distribution_percentage") or {})
                                  .get("top_10")),
            "developer_holding_percentage": a.get("developer_holding_percentage"),
            "gt_score_details": a.get("gt_score_details"),
            "is_honeypot": flag(a.get("is_honeypot")),
            "mint_authority_open": flag(a.get("mint_authority")),
            "freeze_authority_open": flag(a.get("freeze_authority")),
            "description": a.get("description"),
            "x_handle": clean_handle(a.get("twitter_handle"))}


def clean_handle(h):
    """GT returned 'LuffyX100X/status/2102659581109272876' on a Robinhood token.
       Take the handle segment, or treat the account as missing."""
    if not h:
        return None
    h = h.strip()
    for host in ("x.com/", "twitter.com/"):
        if host in h:
            h = h.split(host, 1)[1]
    h = h.lstrip("@").split("?")[0].split("/")[0]
    return h if h and h.replace("_", "").isalnum() and len(h) <= 15 else None


# token accounts owned by these are pools or burns, not holders
SYSTEM_PROGRAM = "11111111111111111111111111111111"
NOT_A_HOLDER = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",   # Raydium AMM v4 authority
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",   # Raydium CPMM authority
    "1nc1nerator11111111111111111111111111111111",    # incinerator
}


def _rpc(method: str, params: list):
    r = requests.post(SOL_RPC, timeout=20, json={"jsonrpc": "2.0", "id": 1,
                                                 "method": method, "params": params})
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def sol_holders(mint: str, rpc=None) -> dict:
    """The largest token accounts with their owners, and why each one does or does not
       count as a holder. rpc(method, params) defaults to the public endpoint; the
       diagnostic and the fixture tests pass a recorder or a recording.

       Amounts are raw integer strings in base units; supply is in the same units, so
       their ratio needs no decimals."""
    rpc = rpc or _rpc
    sup = rpc("getTokenSupply", [mint])["value"]
    supply = float(sup["amount"])
    top = rpc("getTokenLargestAccounts", [mint])["value"]
    out = {"supply_raw": sup["amount"], "decimals": sup.get("decimals"), "accounts": [],
           "top_wallet_share": None}
    if not supply or not top:
        return out
    accts = rpc("getMultipleAccounts", [[x["address"] for x in top],
                                        {"encoding": "jsonParsed"}])["value"]
    owners = [((((a or {}).get("data") or {}).get("parsed") or {}).get("info") or {})
              .get("owner") for a in accts]
    known = sorted({o for o in owners if o})
    kinds = rpc("getMultipleAccounts", [known, {"encoding": "base64",
                                                "dataSlice": {"offset": 0, "length": 0}}]
                )["value"] if known else []
    program_of = {o: (k or {}).get("owner") for o, k in zip(known, kinds)}

    for x, owner in zip(top, owners):
        prog = program_of.get(owner)
        if not owner:
            why = "no_owner"
        elif owner in NOT_A_HOLDER:
            why = "known_amm_or_burn"
        elif prog is not None and prog != SYSTEM_PROGRAM:
            why = "program_owned"                   # pool state, bonding curve
        else:
            why = None
        share_ = float(x["amount"]) / supply
        out["accounts"].append({"address": x["address"], "amount": x["amount"],
                                "owner": owner, "owner_program": prog,
                                "share": share_, "excluded": why})
        if why is None and out["top_wallet_share"] is None:
            out["top_wallet_share"] = share_
    return out


def sol_top_wallet(mint: str, rpc=None) -> float | None:
    """Share of supply in the largest account that is a wallet.

       The largest token account on a fresh launch is almost always the pool or the
       bonding curve. Counting it would fail every token, so accounts whose owner is a
       program account (pool state, bonding curve) or a known AMM authority are skipped."""
    return sol_holders(mint, rpc)["top_wallet_share"]


def social_state(d: dict) -> dict:
    """What SOCIAL hands the judge. The X block is filled by the bot's X plugin."""
    return {"x_account": d["x_account"],                 # collected by SOCIAL, not here
            "published_handle": d["x_handle"],
            "token": {"ticker": d["ticker"], "narrative": d.get("description"),
                      "age_minutes": d.get("age_minutes")}}
