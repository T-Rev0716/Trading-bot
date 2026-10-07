"""FOMO client. FOMO has no public API, so this reads your own logged-in session.

The Privy bearer is read out of a Chrome tab on fomo.family over the DevTools protocol.
Start Chrome with a dedicated profile and a debugging port (Chrome 136+ refuses remote
debugging on the default profile):

    google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.fomo-chrome"

Log into fomo.family in that window once and leave it open.

The response shape of /proxy/filterTokens is not documented. Before trusting the desk,
run `python fomo_api.py probe <addr>:<netId>` and check the parsed row against the site.
"""
import base64
import json
import os
import sys
import time

import requests
import websocket                                # pip install websocket-client

API    = os.environ.get("FOMO_API", "https://prod-api.fomo.family")
ORIGIN = "https://fomo.family"
CDP    = os.environ.get("CHROME_CDP", "http://127.0.0.1:9222")
UA     = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
SOLANA = 1399811149
BATCH  = 20

# seconds -> FOMO field. The desk keys `change` by window length.
CHANGE_FIELDS = {300: "change5m", 3600: "change1", 14400: "change4",
                 43200: "change12", 86400: "change24"}


class FomoError(RuntimeError):
    pass


def _jwt_exp(tok: str) -> float:
    try:
        body = tok.split(".")[1]
        body += "=" * (-len(body) % 4)
        return float(json.loads(base64.urlsafe_b64decode(body))["exp"])
    except Exception:
        return 0.0


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _key(addr: str, net) -> str:
    """Solana addresses are case sensitive, EVM addresses are not."""
    return f"{addr if int(net) == SOLANA else addr.lower()}:{int(net)}"


class Fomo:
    def __init__(self, cdp: str = CDP):
        self.cdp = cdp
        self._tok, self._exp = None, 0.0

    # ---- bearer -------------------------------------------------------------------
    def _tab(self) -> dict:
        tabs = requests.get(f"{self.cdp}/json/list", timeout=5).json()
        for t in tabs:
            if t.get("type") == "page" and "fomo.family" in t.get("url", ""):
                return t
        # no tab open: open one. Chrome 111+ wants PUT here.
        t = requests.put(f"{self.cdp}/json/new?{ORIGIN}", timeout=10).json()
        time.sleep(8)                                       # let Privy boot and refresh
        return t

    def _eval(self, tab: dict, expr: str):
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15,
                                         suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    return ((msg.get("result") or {}).get("result") or {}).get("value")
        finally:
            ws.close()

    def _read_bearer(self, tab: dict):
        raw = self._eval(tab, "localStorage.getItem('privy:token')")
        if not raw:
            return None
        try:
            raw = json.loads(raw)                           # Privy stores it JSON-quoted
        except (TypeError, ValueError):
            pass
        return raw if isinstance(raw, str) and raw.count(".") == 2 else None

    def token(self, force: bool = False) -> str:
        """Bearer for the API. Lives about an hour. Refreshed out of Chrome when close
           to expiry by reloading the tab, which makes Privy mint a new one."""
        if not force and self._tok and self._exp - time.time() > 120:
            return self._tok
        tab = self._tab()
        tok = self._read_bearer(tab)
        if force or not tok or _jwt_exp(tok) - time.time() < 120:
            self._eval(tab, "location.reload()")
            for _ in range(20):
                time.sleep(1.5)
                try:
                    tok = self._read_bearer(tab)
                except Exception:
                    continue                                # page mid-reload
                if tok and _jwt_exp(tok) - time.time() > 120:
                    break
        if not tok or _jwt_exp(tok) - time.time() <= 0:
            raise FomoError("no live Privy token in Chrome. Log into fomo.family in the "
                            "debugging profile.")
        self._tok, self._exp = tok, _jwt_exp(tok)
        return tok

    # ---- data ---------------------------------------------------------------------
    def filter_tokens(self, ids: list[str]):
        """Raw /proxy/filterTokens for up to 20 '<address>:<netId>' ids."""
        for attempt in (0, 1):
            r = requests.post(f"{API}/proxy/filterTokens", json=ids, timeout=30,
                              headers={"Authorization": f"Bearer {self.token(attempt == 1)}",
                                       "Origin": ORIGIN, "Referer": ORIGIN + "/",
                                       "User-Agent": UA})
            if r.status_code == 401 and attempt == 0:
                continue                                    # bearer died early, refresh once
            r.raise_for_status()
            return r.json()

    def tokens(self, ids: list[str]) -> dict[str, dict]:
        """{requested_id: row}. 20 per call. Ids FOMO does not know are simply absent."""
        want = {_key(*i.split(":")): i for i in ids}
        out = {}
        for n in range(0, len(ids), BATCH):
            for r in _results(self.filter_tokens(ids[n:n + BATCH])):
                row = _row(r)
                if row and (tid := want.get(_key(row["address"], row["net"]))):
                    out[tid] = row
        return out


def _results(payload) -> list[dict]:
    """Find the list of result rows wherever the proxy nests it."""
    if isinstance(payload, list):
        if all(isinstance(x, dict) for x in payload):
            return payload
        return []
    if isinstance(payload, dict):
        for k in ("results", "data", "filterTokens", "tokens", "items"):
            if k in payload:
                found = _results(payload[k])
                if found:
                    return found
    return []


def _row(r: dict):
    tok = r.get("token") or {}
    addr = tok.get("address") or r.get("address")
    net = tok.get("networkId") or r.get("networkId")
    if not addr or net is None:
        return None
    holders = r.get("holders")
    return {"address": addr, "net": int(net),
            "symbol": tok.get("symbol") or r.get("symbol") or "?",
            "mcap": _num(r.get("marketCap")),
            "liq": _num(r.get("liquidity")),
            "vol24": _num(r.get("volume24")),
            "price": _num(r.get("priceUSD")),
            "holders": int(holders) if holders not in (None, "") else None,
            "change": {s: _num(r.get(f)) for s, f in CHANGE_FIELDS.items()},
            "created": r.get("createdAt") or tok.get("createdAt")}


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "probe":
        f = Fomo()
        raw = f.filter_tokens(sys.argv[2:22])
        print(json.dumps(raw, indent=2)[:6000])
        print("\nparsed:")
        for r in _results(raw):
            print(_row(r))
    else:
        print("usage: python fomo_api.py probe <address>:<netId> [...]")
