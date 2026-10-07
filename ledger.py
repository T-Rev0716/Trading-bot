"""The paper ledger: cash, orders, positions, equity marks. One SQLite file, many ledgers.

Order lifecycle
    NEW -> SUBMITTED -> FILLED | REJECTED | UNKNOWN
    UNKNOWN -> FILLED | REJECTED          only through reconciliation

UNKNOWN means the venue did not acknowledge. It is never read as failure: the cash a buy
reserved stays reserved, a closing position stays CLOSING, and the ledger refuses every
new order until reconciliation settles what happened.
"""
import json
import sqlite3
import time
import uuid

from thresholds import PAPER

LIVE = ("NEW", "SUBMITTED", "UNKNOWN")
NEXT = {"NEW": {"SUBMITTED", "REJECTED"},
        "SUBMITTED": {"FILLED", "REJECTED", "UNKNOWN"},
        "UNKNOWN": {"FILLED", "REJECTED"}}

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  ledger TEXT PRIMARY KEY, starting_cash REAL NOT NULL, cash REAL NOT NULL, created_at REAL);
CREATE TABLE IF NOT EXISTS orders(
  order_id TEXT PRIMARY KEY, ledger TEXT NOT NULL, idem_key TEXT NOT NULL UNIQUE,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')), token_key TEXT NOT NULL, ticker TEXT,
  position_id TEXT, notional_usd REAL, qty REAL, reserve_usd REAL NOT NULL DEFAULT 0,
  state TEXT NOT NULL, reason TEXT, created_at REAL, updated_at REAL,
  fill_price REAL, fill_qty REAL, fill_notional REAL, fee_usd REAL, slippage_bps REAL,
  flags TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS order_events(
  order_id TEXT, ts REAL, from_state TEXT, to_state TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS positions(
  position_id TEXT PRIMARY KEY, ledger TEXT NOT NULL, token_key TEXT NOT NULL, ticker TEXT,
  status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSING','CLOSED')),
  entry_order_id TEXT NOT NULL UNIQUE, exit_order_id TEXT UNIQUE,
  qty REAL, entry_price REAL, entry_notional REAL, entry_fee REAL, opened_at REAL,
  exit_price REAL, exit_notional REAL, exit_fee REAL, closed_at REAL, exit_reason TEXT,
  realized_pnl REAL, last_price REAL, last_liquidity REAL, last_mark_at REAL,
  quote_failures INTEGER NOT NULL DEFAULT 0, meta TEXT);
CREATE TABLE IF NOT EXISTS marks(ledger TEXT, ts REAL, cash REAL, equity REAL);
"""


class LedgerError(RuntimeError):
    pass


class DuplicateOrder(LedgerError):
    pass


class OrderBlocked(LedgerError):
    pass


class ReleaseMismatch(LedgerError):
    pass


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


class Ledger:
    def __init__(self, db: sqlite3.Connection, name: str, *, starting_cash=None,
                 clock=time.time, new_id=lambda: uuid.uuid4().hex):
        self.db, self.name, self.clock, self.new_id = db, name, clock, new_id
        start = PAPER["starting_cash_usd"] if starting_cash is None else starting_cash
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO accounts VALUES (?,?,?,?)",
                            (name, start, start, clock()))

    # ---- reads ------------------------------------------------------------------------
    def cash(self) -> float:
        return self.db.execute("SELECT cash FROM accounts WHERE ledger=?",
                               (self.name,)).fetchone()[0]

    def starting_cash(self) -> float:
        return self.db.execute("SELECT starting_cash FROM accounts WHERE ledger=?",
                               (self.name,)).fetchone()[0]

    def reserved(self) -> float:
        """Cash promised to buys that have not settled, UNKNOWN ones included."""
        return self.db.execute(
            f"SELECT COALESCE(SUM(reserve_usd),0) FROM orders WHERE ledger=? AND side='buy' "
            f"AND state IN {LIVE}", (self.name,)).fetchone()[0]

    def free_cash(self) -> float:
        return max(0.0, self.cash() - self.reserved())

    def order(self, order_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return dict(r) if r else None

    def orders(self, *states) -> list[dict]:
        q, args = "SELECT * FROM orders WHERE ledger=?", [self.name]
        if states:
            q += f" AND state IN ({','.join('?' * len(states))})"
            args += states
        return [dict(r) for r in self.db.execute(q + " ORDER BY created_at, rowid", args)]

    def unknown_orders(self) -> list[dict]:
        return self.orders("UNKNOWN")

    def position(self, position_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM positions WHERE position_id=?",
                            (position_id,)).fetchone()
        return dict(r) if r else None

    def positions(self, *statuses) -> list[dict]:
        statuses = statuses or ("OPEN", "CLOSING")
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM positions WHERE ledger=? AND status IN "
            f"({','.join('?' * len(statuses))}) ORDER BY opened_at, rowid",
            [self.name, *statuses])]

    def blocked_reason(self, side: str) -> str | None:
        """Why no new order of this side may be created right now, or None."""
        if self.unknown_orders():
            return "unknown_order"                 # nothing new until reconciled
        if self.orders("NEW", "SUBMITTED"):
            return "order_in_flight"
        if side == "buy" and self.positions():
            return "position_open"                 # one position at a time
        return None

    # ---- order lifecycle ------------------------------------------------------------------
    def create_order(self, *, side, token_key, ticker, idem_key, notional_usd=None, qty=None,
                     reserve_usd=0.0, position_id=None, meta=None) -> dict:
        if (why := self.blocked_reason(side)):
            raise OrderBlocked(why)
        if side == "buy":
            if not notional_usd or notional_usd <= 0:
                raise LedgerError("buy needs a positive notional")
            if reserve_usd > self.free_cash() + 1e-9:
                raise LedgerError(f"reserve {reserve_usd:.2f} over free cash "
                                  f"{self.free_cash():.2f}")
        else:
            p = self.position(position_id) if position_id else None
            if not p or p["ledger"] != self.name or p["status"] != "OPEN":
                raise LedgerError(f"sell needs an OPEN position of this ledger, got {p and p['status']}")
            if p["token_key"] != token_key:
                raise LedgerError("sell token does not match its position")
            qty = p["qty"]
        oid, now = self.new_id(), self.clock()
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO orders (order_id, ledger, idem_key, side, token_key, ticker, "
                    "position_id, notional_usd, qty, reserve_usd, state, created_at, updated_at, "
                    "meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, self.name, idem_key, side, token_key, ticker, position_id,
                     notional_usd, qty, reserve_usd if side == "buy" else 0.0, "NEW", now, now,
                     json.dumps(meta or {}, default=str)))
                self._event(oid, None, "NEW", idem_key)
                if side == "sell":
                    self.db.execute("UPDATE positions SET status='CLOSING' WHERE position_id=?",
                                    (position_id,))
        except sqlite3.IntegrityError as e:
            raise DuplicateOrder(f"{idem_key}: {e}") from e
        return self.order(oid)

    def _event(self, oid, frm, to, note=""):
        self.db.execute("INSERT INTO order_events VALUES (?,?,?,?,?)",
                        (oid, self.clock(), frm, to, note))

    def _move(self, oid: str, to: str, note: str = "", **fields) -> dict:
        o = self.order(oid)
        if not o or o["ledger"] != self.name:
            raise LedgerError(f"no order {oid} on {self.name}")
        if to not in NEXT.get(o["state"], set()):
            raise LedgerError(f"order {oid}: {o['state']} -> {to} is not allowed")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE orders SET state=?, updated_at=?{', ' + sets if sets else ''} "
                        f"WHERE order_id=? AND state=?",
                        (to, self.clock(), *fields.values(), oid, o["state"]))
        self._event(oid, o["state"], to, note)
        return o

    def submitted(self, oid: str):
        with self.db:
            self._move(oid, "SUBMITTED")

    def unknown(self, oid: str, note: str):
        with self.db:
            self._move(oid, "UNKNOWN", note)

    def rejected(self, oid: str, reason: str):
        with self.db:
            o = self._move(oid, "REJECTED", reason, reason=reason)
            if o["side"] == "sell":                # nothing sold: the position is still held
                self.db.execute("UPDATE positions SET status='OPEN' WHERE position_id=? "
                                "AND status='CLOSING'", (o["position_id"],))

    def filled(self, oid: str, fill, exit_reason: str | None = None) -> dict:
        """Apply a fill. A buy opens a position that references it; a sell releases the
           position it was created for, through release(), which checks the match."""
        with self.db:
            o = self._move(oid, "FILLED", "", fill_price=fill.price, fill_qty=fill.qty,
                           fill_notional=fill.notional_usd, fee_usd=fill.fee_usd,
                           slippage_bps=fill.slippage_bps, flags=json.dumps(list(fill.flags)))
            if o["side"] == "buy":
                spend = fill.notional_usd + fill.fee_usd
                self.db.execute("UPDATE accounts SET cash = cash - ? WHERE ledger=?",
                                (spend, self.name))
                pid = self.new_id()
                self.db.execute(
                    "INSERT INTO positions (position_id, ledger, token_key, ticker, status, "
                    "entry_order_id, qty, entry_price, entry_notional, entry_fee, opened_at, "
                    "last_price, last_mark_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, self.name, o["token_key"], o["ticker"], "OPEN", oid, fill.qty,
                     fill.price, fill.notional_usd, fill.fee_usd, self.clock(), fill.price,
                     self.clock(), o["meta"]))
                return self.position(pid)
            self.db.execute("UPDATE accounts SET cash = cash + ? WHERE ledger=?",
                            (fill.notional_usd - fill.fee_usd, self.name))
            self._release(o["position_id"], oid, fill,
                          exit_reason or json.loads(o["meta"] or "{}").get("exit_reason"))
            return self.position(o["position_id"])

    def release(self, position_id: str, exit_order_id: str):
        """Close a position against the sell order that closed it. Kept public so the
           check is the only way a position can be closed."""
        o = self.order(exit_order_id)
        if not o or o["state"] != "FILLED":
            raise ReleaseMismatch(f"order {exit_order_id} is not a filled order")
        p = self.position(position_id)
        if p and p["status"] == "CLOSED" and p["exit_order_id"] == exit_order_id:
            return p                                # already released by this order
        with self.db:
            from venue import Fill
            fill = Fill(o["fill_price"], o["fill_qty"], o["fill_notional"], o["fee_usd"],
                        o["slippage_bps"], tuple(json.loads(o["flags"] or "[]")))
            self._release(position_id, exit_order_id, fill, None)
        return self.position(position_id)

    def _release(self, position_id, exit_order_id, fill, exit_reason):
        o = self.order(exit_order_id)
        p = self.position(position_id)
        if not p:
            raise ReleaseMismatch(f"no position {position_id}")
        if p["ledger"] != self.name or o["ledger"] != self.name:
            raise ReleaseMismatch("position and order belong to different ledgers")
        if o["side"] != "sell":
            raise ReleaseMismatch(f"order {exit_order_id} is a {o['side']}, not the close")
        if o["position_id"] != position_id:
            raise ReleaseMismatch(f"order {exit_order_id} closes {o['position_id']}, "
                                  f"not {position_id}")
        if o["token_key"] != p["token_key"]:
            raise ReleaseMismatch("order and position are different tokens")
        if p["status"] != "CLOSING":
            raise ReleaseMismatch(f"position {position_id} is {p['status']}, not CLOSING")
        pnl = (fill.notional_usd - fill.fee_usd) - (p["entry_notional"] + p["entry_fee"])
        self.db.execute(
            "UPDATE positions SET status='CLOSED', exit_order_id=?, exit_price=?, "
            "exit_notional=?, exit_fee=?, closed_at=?, exit_reason=?, realized_pnl=?, "
            "last_price=? WHERE position_id=? AND status='CLOSING'",
            (exit_order_id, fill.price, fill.notional_usd, fill.fee_usd, self.clock(),
             exit_reason, pnl, fill.price, position_id))

    # ---- marks ----------------------------------------------------------------------------
    def mark(self, position_id: str, price=None, liquidity=None, failed=False):
        with self.db:
            if failed:
                self.db.execute("UPDATE positions SET quote_failures = quote_failures + 1 "
                                "WHERE position_id=?", (position_id,))
            else:
                self.db.execute("UPDATE positions SET last_price=?, last_liquidity="
                                "COALESCE(?, last_liquidity), last_mark_at=?, quote_failures=0 "
                                "WHERE position_id=?",
                                (price, liquidity, self.clock(), position_id))

    def equity(self) -> float:
        held = sum((p["qty"] or 0) * (p["last_price"] or 0) for p in self.positions())
        return self.cash() + held

    def record_equity(self):
        with self.db:
            self.db.execute("INSERT INTO marks VALUES (?,?,?,?)",
                            (self.name, self.clock(), self.cash(), self.equity()))
