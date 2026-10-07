"""The watchlist: discovered tokens kept until they can be judged or can never be.

Discovery only sees GeckoTerminal's newest pools, and on Solana those are minutes old,
younger than the 15-minute floor. Without a memory the desk would only ever see tokens
too young to trade: by the time one matured it had left the newest pages for good.

Every token FOMO returns, and that is not already too old, is watched. Each cycle the
watched tokens are re-fetched from FOMO alongside the newest discoveries, so they are
always evaluated on current data, never on what was seen when they were found.

    expiry     created_at + max token age (thresholds.HARD). A token with no known
               launch time expires max age after it was first seen.
    removal    expired, observed too old, or rejected for a reason that benches longer
               than max age (a permanent fact such as a honeypot)
    bound      at most max_size entries; when full, the earliest discovered is evicted
    persists   in desk.db (DESK_DB), next to the bench, across restarts and resumes

The scan is not replayed: its output is journaled per cycle, so the watchlist changes
what the scan returns, never what replay does with it.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist(
  tid TEXT PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  created_at REAL, expires_at REAL NOT NULL, times_seen INTEGER NOT NULL DEFAULT 1);
"""


class Watchlist:
    def __init__(self, db: sqlite3.Connection, max_age_hours: float, max_size: int,
                 lock=None):
        self.db, self.max_age_s, self.max_size = db, max_age_hours * 3600, max_size
        self.lock = lock
        db.executescript(SCHEMA)
        db.commit()

    def _q(self, sql, args=()):
        if self.lock:
            with self.lock:
                cur = self.db.execute(sql, args)
                self.db.commit()
                return cur.fetchall()
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.fetchall()

    def prune(self, now: float) -> int:
        """Drop what has passed max age. Returns how many."""
        n = self._q("SELECT COUNT(*) FROM watchlist WHERE expires_at < ?", (now,))[0][0]
        self._q("DELETE FROM watchlist WHERE expires_at < ?", (now,))
        return n

    def active(self, now: float) -> list[str]:
        """Watched tokens not yet expired, earliest discovered first: a stable order."""
        return [r[0] for r in self._q(
            "SELECT tid FROM watchlist WHERE expires_at >= ? ORDER BY first_seen, tid",
            (now,))]

    def observe(self, tid: str, created_at: float | None, now: float) -> int:
        """Watch a token, or refresh one already watched. A second discovery keeps the
           first_seen time; a launch time learned later sets the expiry. Returns how many
           entries were evicted to stay within max_size."""
        row = self._q("SELECT first_seen, created_at FROM watchlist WHERE tid=?", (tid,))
        if row:
            first, known = row[0]
            created = created_at if created_at is not None else known
            self._q("UPDATE watchlist SET last_seen=?, created_at=?, expires_at=?, "
                    "times_seen = times_seen + 1 WHERE tid=?",
                    (now, created, (created if created is not None else first)
                     + self.max_age_s, tid))
            return 0
        self._q("INSERT INTO watchlist VALUES (?,?,?,?,?,1)",
                (tid, now, now, created_at,
                 (created_at if created_at is not None else now) + self.max_age_s))
        return self._evict()

    def drop(self, tid: str):
        self._q("DELETE FROM watchlist WHERE tid=?", (tid,))

    def _evict(self) -> int:
        n = self._q("SELECT COUNT(*) FROM watchlist")[0][0] - self.max_size
        if n <= 0:
            return 0
        self._q("DELETE FROM watchlist WHERE tid IN (SELECT tid FROM watchlist "
                "ORDER BY first_seen, tid LIMIT ?)", (n,))
        return n

    def size(self) -> int:
        return self._q("SELECT COUNT(*) FROM watchlist")[0][0]

    def entry(self, tid: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM watchlist WHERE tid=?", (tid,))
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None
