"""Read-only integration diagnostic. Solana only, for now.

    python diagnose.py                       check every service, print a report
    python diagnose.py --mint <MINT>         use this token instead of GT's newest
    python diagnose.py --save-fixtures tests/fixtures/solana
                                             also save sanitized responses + parsed output
    python diagnose.py --fomo                also check FOMO (needs a logged-in Chrome)
    python diagnose.py --jev-call            also make one paid Jev call (a few cents)
    python diagnose.py --json report.json    machine-readable report

Each service is checked on its own; one failing never hides another. Nothing here sends
an order, and nothing is written except the report and the fixtures you ask for.
Credentials are never printed, logged or saved: every string goes through sanitize.py.

Status per service:
  OK               reached, schema matches, parsers ran
  SCHEMA_MISMATCH  reached, but fields are missing or the wrong type, or a parser failed
  ERROR            reached, but answered with an HTTP error, a rate limit, or not JSON
  BLOCKED          could not be reached from here (proxy denial, DNS, timeout)
  NOT_CONFIGURED   needs a credential or a local setup that is not present

Exit code: 0 all OK; 1 any SCHEMA_MISMATCH or ERROR; 2 otherwise some BLOCKED or
NOT_CONFIGURED.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

import requests

import sanitize

GT = "https://api.geckoterminal.com/api/v2"
DEX = "https://api.dexscreener.com/latest/dex/tokens"
WSOL = "So11111111111111111111111111111111111111112"   # reference mint, not a launch
SOLANA_NET = 1399811149
UA = {"Accept": "application/json", "User-Agent": "desk-diagnose/1 (read-only)"}

SETUP = {
    "network": [
        "This environment's network policy refused the connection. Run the diagnostic "
        "on your own machine, or allow the host in your sandbox's network settings.",
        "Locally: python3 -m venv .venv && . .venv/bin/activate && "
        "pip install -r requirements.txt && python diagnose.py",
    ],
    "jev": [
        "Create a key at console.typesafe.ai -> Keys.",
        "export TYPESAFE_API_KEY=...   (never commit it, never paste it into an issue)",
        "python diagnose.py            (free: lists models only)",
        "python diagnose.py --jev-call (one paid call to check the answer schema)",
    ],
    "fomo": [
        'google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.fomo-chrome"',
        "Log into https://fomo.family in that window and leave it open.",
        "export CHROME_CDP=http://127.0.0.1:9222",
        "python diagnose.py --fomo",
    ],
}


class Blocked(Exception):
    pass


class HttpError(Exception):
    pass


@dataclass
class Endpoint:
    name: str
    status: str = "OK"
    http: int | None = None
    missing: list = field(default_factory=list)
    wrong_type: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)
    note: str = ""


@dataclass
class Result:
    service: str
    status: str = "OK"
    detail: str = ""
    endpoints: list = field(default_factory=list)
    verifications: list = field(default_factory=list)
    setup: list = field(default_factory=list)

    def endpoint(self, ep: Endpoint):
        self.endpoints.append(ep)
        return ep

    def verify(self, claim: str, result: str, observed):
        """result: PASS, FAIL or UNVERIFIED. Observed values are what came back."""
        self.verifications.append({"claim": claim, "result": result,
                                   "observed": sanitize.data(observed)})

    def settle(self):
        """The service's status is the worst of its endpoints'."""
        order = ["OK", "NOT_CONFIGURED", "BLOCKED", "ERROR", "SCHEMA_MISMATCH"]
        for ep in self.endpoints:
            if order.index(ep.status) > order.index(self.status):
                self.status = ep.status
        return self


# ---- HTTP, with the failure classified --------------------------------------------------
class Fetcher:
    def __init__(self, save_dir: str | None = None, timeout=20):
        self.save_dir, self.timeout = save_dir, timeout
        self.saved = []

    def _call(self, method, url, **kw):
        host = urlparse(url).hostname
        try:
            r = requests.request(method, url, timeout=self.timeout, headers=UA, **kw)
        except requests.exceptions.ProxyError as e:
            why = "403" if "403" in str(e) else type(e.__cause__ or e).__name__
            raise Blocked(f"egress proxy refused CONNECT to {host} ({why})")
        except requests.exceptions.SSLError:
            raise Blocked(f"TLS failure talking to {host}")
        except requests.exceptions.Timeout:
            raise Blocked(f"{host} timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise Blocked(f"{host} unreachable: {sanitize.text(str(e))[-120:]}")
        if r.status_code == 429:
            raise HttpError("429 rate limited")
        if r.status_code >= 400:
            raise HttpError(f"HTTP {r.status_code}: {sanitize.text(r.text)[:200]}")
        try:
            return r.status_code, r.json()
        except ValueError:
            raise HttpError(f"HTTP {r.status_code} but not JSON: {sanitize.text(r.text)[:120]}")

    def get(self, url, **params):
        return self._call("GET", url, params=params or None)

    def post(self, url, body):
        return self._call("POST", url, json=body)

    def save(self, service: str, name: str, request: dict, http: int, body, parsed):
        if not self.save_dir:
            return
        os.makedirs(self.save_dir, exist_ok=True)
        path = os.path.join(self.save_dir, f"{service}__{name}.json")
        doc = {"service": service, "name": name, "request": sanitize.data(request),
               "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "http_status": http, "sanitized": True,
               "body": sanitize.data(body), "parsed": sanitize.data(parsed)}
        with open(path, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        self.saved.append(path)


def run_endpoint(res: Result, name: str, fn):
    """Run one endpoint check; classify any failure onto the endpoint, never raise."""
    ep = res.endpoint(Endpoint(name))
    try:
        fn(ep)
    except Blocked as e:
        ep.status, ep.note = "BLOCKED", str(e)
    except HttpError as e:
        ep.status, ep.note = "ERROR", str(e)
    except Exception as e:                       # a parser or schema surprise
        ep.status = "SCHEMA_MISMATCH"
        ep.parse_errors.append(sanitize.text(f"{type(e).__name__}: {e}")[:300])
    return ep


# ---- schema checking ------------------------------------------------------------------
NUM = (int, float)
STRNUM = (str, int, float)


def check(obj, spec: dict, ep: Endpoint, sample=5):
    """spec: {"a.b[].c": types or (types, required)}. '[]' checks the first `sample`
       elements of a list. A required path that is absent is 'missing'; a present value
       of another type is 'wrong_type'. None counts as present but typeless (nullable)."""
    for path, rule in spec.items():
        types, required = rule if isinstance(rule, tuple) and isinstance(rule[-1], bool) \
            else (rule, True)
        for where, val, present in _walk(obj, path.split("."), "", sample):
            if not present:
                if required:
                    ep.missing.append(where)
            elif val is not None and types is not None and not isinstance(val, types):
                ep.wrong_type.append(f"{where}: {type(val).__name__}")
    if (ep.missing or ep.wrong_type) and ep.status == "OK":
        ep.status = "SCHEMA_MISMATCH"


def _walk(obj, parts, prefix, sample):
    if not parts:
        yield prefix, obj, True
        return
    head, rest = parts[0], parts[1:]
    is_list = head.endswith("[]")
    key = head[:-2] if is_list else head
    here = f"{prefix}.{key}" if prefix else key
    if not isinstance(obj, dict) or key not in obj:
        yield here, None, False
        return
    val = obj[key]
    if is_list:
        if not isinstance(val, list) or not val:
            yield here + "[]", val, val is not None and isinstance(val, list)
            return
        for i, item in enumerate(val[:sample]):
            yield from _walk(item, rest, f"{here}[{i}]", sample)
    else:
        yield from _walk(val, rest, here, sample)


# ---- services ---------------------------------------------------------------------------
def check_geckoterminal(f: Fetcher, mint_hint: str | None) -> tuple[Result, str | None, dict]:
    from collect import parse_new_pools, parse_token_info
    res, ctx = Result("geckoterminal"), {"mint": mint_hint}

    def new_pools(ep):
        ep.http, body = f.get(f"{GT}/networks/solana/new_pools", page=1)
        check(body, {"data[]": dict, "data[].attributes.address": str,
                     "data[].attributes.pool_created_at": str,
                     "data[].attributes.reserve_in_usd": STRNUM,
                     "data[].relationships.base_token.data.id": str}, ep)
        tids = parse_new_pools(body, "solana")
        f.save("geckoterminal", "new_pools", {"GET": f"{GT}/networks/solana/new_pools",
                                              "params": {"page": 1}}, ep.http, body, tids)
        ids = [((p.get("relationships") or {}).get("base_token") or {}).get("data", {})
               .get("id", "") for p in body.get("data", [])]
        res.verify("base_token id is 'solana_<mint>'",
                   "PASS" if ids and all(i.startswith("solana_") for i in ids) else "FAIL",
                   ids[:3])
        stamps = [(p.get("attributes") or {}).get("pool_created_at") for p in body["data"]]
        ages = [_age_hours(s) for s in stamps[:5]]
        res.verify("pool_created_at is ISO-8601 with an offset, and recent (< 7 days)",
                   "PASS" if ages and all(a is not None and 0 <= a < 168 for a in ages)
                   else "FAIL", {"sample": stamps[:3], "age_hours": ages[:3]})
        if not ctx["mint"] and tids:
            ctx["mint"] = tids[0].split(":")[0]

    def token_info(ep):
        mint = ctx["mint"] or WSOL
        ep.note = f"mint {mint}" + ("" if ctx["mint"] else " (reference, not a launch)")
        ep.http, body = f.get(f"{GT}/networks/solana/tokens/{mint}/info")
        check(body, {"data.attributes.address": str, "data.attributes.symbol": str,
                     "data.attributes.holders": (dict, True),
                     "data.attributes.mint_authority": (None, True),
                     "data.attributes.freeze_authority": (None, True),
                     "data.attributes.twitter_handle": (str, True),
                     "data.attributes.gt_score_details": (dict, False),
                     "data.attributes.is_honeypot": (None, False)}, ep)
        parsed = parse_token_info(body)
        ctx["gt_parsed"] = parsed
        f.save("geckoterminal", "token_info",
               {"GET": f"{GT}/networks/solana/tokens/{mint}/info"}, ep.http, body, parsed)
        a = body["data"]["attributes"]
        for k in ("mint_authority", "freeze_authority"):
            raw = a.get(k)
            res.verify(f"{k} parses to a definite flag (raw type: {type(raw).__name__})",
                       "PASS" if parsed[f"{k}_open"] is not None else "UNVERIFIED",
                       {"raw": raw, "parsed": parsed[f"{k}_open"]})
        dist = ((a.get("holders") or {}).get("distribution_percentage") or {})
        nums = {k: _num(v) for k, v in dist.items()}
        total = sum(v for v in nums.values() if v is not None)
        res.verify("holders.distribution_percentage is in percent (buckets sum to ~100)",
                   "PASS" if 95 <= total <= 105 else ("UNVERIFIED" if not dist else "FAIL"),
                   {"buckets": dist, "sum": round(total, 3)})
        cnt = (a.get("holders") or {}).get("count")
        res.verify("holders.count is an integer", "PASS" if isinstance(cnt, int) else
                   ("UNVERIFIED" if cnt is None else "FAIL"), cnt)

    run_endpoint(res, "new_pools", new_pools)
    run_endpoint(res, "token_info", token_info)
    return res.settle(), ctx["mint"], ctx


def check_dexscreener(f: Fetcher, mint: str) -> Result:
    from collect import parse_pair, pick_pair
    from market import quote_from_pair
    res = Result("dexscreener")
    spec = {"pairs[].chainId": str, "pairs[].pairAddress": str, "pairs[].priceUsd": str,
            "pairs[].liquidity.usd": NUM, "pairs[].volume.h1": NUM,
            "pairs[].volume.h6": NUM, "pairs[].volume.h24": NUM,
            "pairs[].txns.h1.buys": int, "pairs[].txns.h1.sells": int,
            "pairs[].txns.h6.buys": int, "pairs[].txns.h24.buys": int,
            "pairs[].pairCreatedAt": int}

    def tokens(name, m):
        def fn(ep):
            ep.note = f"mint {m}"
            ep.http, body = f.get(f"{DEX}/{m}")
            if not body.get("pairs"):
                ep.note += ": no pairs listed yet"
                f.save("dexscreener", name, {"GET": f"{DEX}/{m}"}, ep.http, body, None)
                return
            check(body, spec, ep)
            p = pick_pair(body, "solana", m)
            parsed = {"flow": parse_pair(p),
                      "quote": asdict(quote_from_pair(p, 0.0)) if p else None}
            f.save("dexscreener", name, {"GET": f"{DEX}/{m}"}, ep.http, body, parsed)
            if p:
                created = p.get("pairCreatedAt")
                res.verify(f"[{name}] pairCreatedAt is epoch milliseconds",
                           "PASS" if isinstance(created, int) and 1e12 < created < 1e13
                           else "FAIL", created)
                res.verify(f"[{name}] priceUsd is a decimal string that parses",
                           "PASS" if parsed["quote"]["price_usd"] is not None else "FAIL",
                           p.get("priceUsd"))
                v = p.get("volume") or {}
                ok = _num(v.get("h6")) is not None and _num(v.get("h24")) is not None and \
                    _num(v.get("h6")) <= _num(v.get("h24")) + 1e-9
                res.verify(f"[{name}] volume.h6 <= volume.h24 (windows nest)",
                           "PASS" if ok else "FAIL", {"h6": v.get("h6"), "h24": v.get("h24")})
        return fn

    run_endpoint(res, "tokens_sample", tokens("tokens_sample", mint))
    if mint != WSOL:
        run_endpoint(res, "tokens_reference", tokens("tokens_reference", WSOL))
    return res.settle()


def check_solana_rpc(f: Fetcher, mint: str, gt_top_10=None) -> Result:
    import collect
    res = Result("solana_rpc")
    url = collect.SOL_RPC
    calls = []

    def rpc(method, params):
        _, body = f.post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        calls.append({"method": method, "params": params, "response": body})
        if "error" in body:
            raise HttpError(f"RPC error {sanitize.text(json.dumps(body['error']))[:200]}")
        return body["result"]

    def holders(ep):
        ep.note = f"mint {mint} via {sanitize.text(url)}"
        h = collect.sol_holders(mint, rpc=rpc)
        sup = calls[0]["response"]["result"]["value"]
        check(calls[0]["response"], {"result.value.amount": str, "result.value.decimals": int,
                                     "result.value.uiAmountString": str}, ep)
        if len(calls) > 1:
            check(calls[1]["response"], {"result.value[].address": str,
                                         "result.value[].amount": str,
                                         "result.value[].decimals": int}, ep)
        if len(calls) > 2:
            check(calls[2]["response"], {"result.value[].data.parsed.info.owner": str,
                                         "result.value[].data.parsed.info.tokenAmount.amount":
                                             str}, ep)
        f.save("solana_rpc", "holders", {"POST": sanitize.text(url), "mint": mint,
                                         "calls": [{"method": c["method"],
                                                    "params": c["params"]} for c in calls]},
               200, {"calls": calls}, h)
        raw, ui, dec = sup["amount"], sup.get("uiAmountString"), sup.get("decimals")
        try:
            same = int(raw) == round(float(ui) * 10 ** dec)
        except (TypeError, ValueError):
            same = None
        res.verify("getTokenSupply amount is raw base units (amount == uiAmount * 10^decimals)",
                   "PASS" if same else ("UNVERIFIED" if same is None else "FAIL"),
                   {"amount": raw, "uiAmountString": ui, "decimals": dec})
        excl = [a["excluded"] for a in h["accounts"]]
        if h["top_wallet_share"] is None:
            verdict = "UNVERIFIED"                  # no account counted as a wallet
        else:
            verdict = "PASS" if all(e is not None for e in excl[:excl.index(None)]) \
                else "FAIL"
        res.verify("top_wallet_share is the first account not excluded as pool/curve/AMM",
                   verdict,
                   {"accounts": [{k: a[k] for k in ("share", "excluded", "owner_program")}
                                 for a in h["accounts"][:5]],
                    "top_wallet_share": h["top_wallet_share"]})
        if gt_top_10 is not None and h["top_wallet_share"] is not None:
            res.verify("top_wallet_share <= GT top_10_share (one wallet within the top 10)",
                       "PASS" if h["top_wallet_share"] <= gt_top_10 + 0.01 else "FAIL",
                       {"top_wallet_share": h["top_wallet_share"], "gt_top_10": gt_top_10})
        else:
            res.verify("top_wallet_share <= GT top_10_share", "UNVERIFIED",
                       "GT top_10 or RPC share unavailable")

    run_endpoint(res, "holders", holders)
    return res.settle()


def check_jev(f: Fetcher, paid_call: bool) -> Result:
    res = Result("jev")
    if not os.environ.get("TYPESAFE_API_KEY"):
        res.status, res.detail, res.setup = "NOT_CONFIGURED", "TYPESAFE_API_KEY is not set", \
            SETUP["jev"]
        return res
    from typesafe_sdk import TypeSafeAPIConnectionError, TypeSafeAPIError, TypeSafeClient

    def models(ep):
        try:
            with TypeSafeClient() as c:
                r = c.models.list()
        except TypeSafeAPIConnectionError as e:
            raise Blocked(sanitize.text(str(e))[:200])
        except TypeSafeAPIError as e:
            raise HttpError(sanitize.text(str(e))[:200])
        names = [m.name for m in r.models]
        ep.note = f"{len(names)} models"
        f.save("jev", "models", {"GET": "https://api.typesafe.ai/v1/models"}, 200,
               {"models": [m.model_dump() for m in r.models]}, names)
        res.verify("jev-latest or a jev model is listed",
                   "PASS" if any(n.startswith("jev") for n in names) else "FAIL", names)

    def system_one(ep):
        from questions import SETS
        state = {"ticker": "DIAG", "age_minutes": 42, "holder_count": 310,
                 "change": {"5m": 0.04, "1h": 0.22, "24h": 0.61}, "buys_h1": 540,
                 "sells_h1": 120, "liquidity_usd": 48000, "mcap_usd": 310000,
                 "volume_h24": 610000, "intended_ticket_usd": 900}
        try:
            with TypeSafeClient() as c:
                r = c.system_one(state=state, questions=SETS["market"])
        except TypeSafeAPIConnectionError as e:
            raise Blocked(sanitize.text(str(e))[:200])
        except TypeSafeAPIError as e:
            raise HttpError(sanitize.text(str(e))[:200])
        missing = sorted(set(SETS["market"]) - set(r.answers))
        ep.missing += missing
        if missing:
            ep.status = "SCHEMA_MISMATCH"
        res.verify("model id is a version, not an alias",
                   "PASS" if r.model != "jev-latest" else "FAIL", r.model)
        p = r.answers["shape"].probabilities if "shape" in r.answers else {}
        res.verify("shape probabilities sum to ~1",
                   "PASS" if p and abs(sum(p.values()) - 1) < 0.02 else "FAIL", p)
        f.save("jev", "system_one_market", {"POST": "https://api.typesafe.ai/v1/systemone",
                                            "state": state}, 200,
               {"model": r.model, "answers": {k: v.model_dump() for k, v in r.answers.items()},
                "usage": r.usage.model_dump()}, None)

    run_endpoint(res, "models", models)
    if paid_call:
        run_endpoint(res, "system_one", system_one)
    return res.settle()


def check_fomo(f: Fetcher, enabled: bool, mint: str) -> Result:
    res = Result("fomo")
    if not enabled:
        res.status, res.detail, res.setup = "NOT_CONFIGURED", \
            "optional; not checked without --fomo", SETUP["fomo"]
        return res
    import fomo_api
    try:
        requests.get(f"{fomo_api.CDP}/json/version", timeout=3)
    except requests.RequestException:
        res.status, res.setup = "NOT_CONFIGURED", SETUP["fomo"]
        res.detail = f"no Chrome DevTools endpoint at {fomo_api.CDP}"
        return res
    fo = fomo_api.Fomo()

    def bearer(ep):
        try:
            tok = fo.token()
        except fomo_api.FomoError as e:
            ep.status, ep.note = "NOT_CONFIGURED", str(e)
            return
        mins = (fomo_api._jwt_exp(tok) - time.time()) / 60
        ep.note = f"bearer present, expires in {mins:.0f} min"        # never the bearer

    def filter_tokens(ep):
        tid = f"{mint}:{SOLANA_NET}"
        try:
            raw = fo.filter_tokens([tid])
        except requests.exceptions.ProxyError as e:
            raise Blocked(sanitize.text(str(e))[:200])
        except requests.RequestException as e:
            raise HttpError(sanitize.text(str(e))[:200])
        rows = fomo_api._results(raw)
        if not rows:
            ep.status, ep.note = "SCHEMA_MISMATCH", "no result rows found in the response"
        for r in rows[:3]:
            check({"r": r}, {"r.token.address": str, "r.token.networkId": int,
                             "r.token.symbol": str, "r.marketCap": STRNUM,
                             "r.liquidity": STRNUM, "r.volume24": STRNUM,
                             "r.holders": (int, True), "r.priceUSD": STRNUM,
                             "r.change5m": (STRNUM, True), "r.change1": (STRNUM, True),
                             "r.change24": (STRNUM, True), "r.createdAt": (int, True)}, ep)
        parsed = [fomo_api._row(r) for r in rows]
        f.save("fomo", "filter_tokens", {"POST": f"{fomo_api.API}/proxy/filterTokens",
                                         "body": [tid]}, 200, raw, parsed)
        if parsed:
            c = parsed[0]["change"].get(3600)
            res.verify("change fields are fractions (0.22 = +22%), not percents",
                       "UNVERIFIED", {"change1": c, "note": "compare with the site by eye"})
            res.verify("createdAt is epoch seconds or milliseconds",
                       "PASS" if isinstance(parsed[0]["created"], (int, float)) and
                       parsed[0]["created"] > 1e9 else "FAIL", parsed[0]["created"])

    run_endpoint(res, "bearer", bearer)
    if res.endpoints[-1].status == "OK":
        run_endpoint(res, "filter_tokens", filter_tokens)
    return res.settle()


# ---- helpers, report ----------------------------------------------------------------------
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _age_hours(stamp):
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    return round((dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600, 2)


def diagnose(mint=None, save_dir=None, fomo=False, jev_call=False, fetcher=None) -> list:
    f = fetcher or Fetcher(save_dir)
    gt, sample, ctx = check_geckoterminal(f, mint)
    mint = sample or WSOL
    results = [gt, check_dexscreener(f, mint),
               check_solana_rpc(f, mint, (ctx.get("gt_parsed") or {}).get("top_10_share")),
               check_jev(f, jev_call), check_fomo(f, fomo, mint)]
    for r in results:
        if r.status == "BLOCKED" and not r.setup:
            r.setup = SETUP["network"]
        if not r.detail:
            notes = [f"{e.name}: {e.note}" for e in r.endpoints if e.note]
            r.detail = "; ".join(notes)
    return results


def render(results) -> str:
    out = [f"integration diagnostic (Solana), {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%MZ}"
           " - read-only, paper-only desk", ""]
    for r in results:
        out.append(f"{r.service:<14} {r.status:<16} {sanitize.text(r.detail)[:150]}")
        for e in r.endpoints:
            bits = [f"  - {e.name}: {e.status}"]
            if e.http:
                bits.append(f"http {e.http}")
            if e.missing:
                bits.append(f"missing {e.missing[:6]}")
            if e.wrong_type:
                bits.append(f"wrong type {e.wrong_type[:6]}")
            if e.parse_errors:
                bits.append(f"parse errors {e.parse_errors}")
            out.append(", ".join(bits))
        for v in r.verifications:
            obs = json.dumps(v["observed"], default=str)
            out.append(f"  [{v['result']}] {v['claim']}  observed: {obs[:160]}")
        for s in r.setup:
            out.append(f"  setup: {s}")
    return "\n".join(sanitize.text(line) for line in out)


def exit_code(results) -> int:
    st = {r.status for r in results}
    if st & {"ERROR", "SCHEMA_MISMATCH"}:
        return 1
    return 0 if st == {"OK"} else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mint", help="Solana mint to inspect (default: GT's newest pool)")
    ap.add_argument("--save-fixtures", metavar="DIR")
    ap.add_argument("--fomo", action="store_true", help="also check FOMO via Chrome CDP")
    ap.add_argument("--jev-call", action="store_true", help="also make one paid Jev call")
    ap.add_argument("--json", metavar="PATH")
    a = ap.parse_args()
    res = diagnose(a.mint, a.save_fixtures, a.fomo, a.jev_call)
    print(render(res))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(sanitize.data([asdict(r) for r in res]), fh, indent=1)
    sys.exit(exit_code(res))
