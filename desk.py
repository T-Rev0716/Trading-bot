"""The Grok Bot side of the shift, as main.py sees it.

main.py needs five things from the desk. This default wires them to environment
variables so the shift runs on its own; swap any method for your own integration.

    DESK_BANK_USD        free cash the shift sizes against            (required)
    X_READER_URL         SOCIAL's endpoint: POST {"handle"} -> X block or null
    SEATS_WEBHOOK_URL    where a live order is POSTed for SIZE -> FILLS -> RISK
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   one line per cycle, trade or no trade
    SHADOW_LOG           shadow week rows, default shadow.jsonl
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger("desk")


class Desk:
    def __init__(self):
        self.secret = os.environ.get("DESK_SECRET", "")
        self.shadow_path = os.environ.get("SHADOW_LOG", "shadow.jsonl")

    def bank(self) -> float:
        return float(os.environ["DESK_BANK_USD"])

    def read_x(self, handle: str):
        """The X block SOCIAL collects with its plugin, or None. Never a guess."""
        url = os.environ.get("X_READER_URL")
        if not url:
            return None
        try:
            r = requests.post(url, json={"handle": handle}, timeout=60,
                              headers={"Authorization": f"Bearer {self.secret}"})
            r.raise_for_status()
            block = r.json()
        except Exception as e:
            log.warning("x read %s failed: %s", handle, e)
            return None
        return block.get("x_account", block) if isinstance(block, dict) else None

    def log_shadow(self, order, stats):
        with open(self.shadow_path, "a") as f:
            f.write(json.dumps({"ts": time.time(), "order": order, "stats": stats},
                               default=str) + "\n")

    def report(self, order, stats, note: str = ""):
        if order:
            t = order["token"]
            line = (f"ORDER {t['ticker']} {t['chain']} {t['address']} "
                    f"size_factor {order['size_factor']} conf {order['confidence']} "
                    f"model {order['model']}")
        elif stats.get("held"):
            line = f"holding {stats['held']} for {stats['minutes']} min, no scan"
        else:
            line = (f"no trade{': ' + note if note else ''}. seen {stats.get('seen', 0)}, "
                    f"benched {stats.get('benched', 0)}, free {stats.get('free', {})}, "
                    f"trade {stats.get('trade', {})}, chain {stats.get('chain', {})}, "
                    f"soft {stats.get('soft', {})}")
        log.info(line)
        self._telegram(line)

    def send_to_seats(self, order):
        """Hand it to SIZE, then FILLS, then RISK. Raises if nobody received it."""
        url = os.environ.get("SEATS_WEBHOOK_URL")
        if not url:
            raise RuntimeError("SEATS_WEBHOOK_URL is not set, the order has nowhere to go")
        r = requests.post(url, json=order, timeout=30,
                          headers={"Authorization": f"Bearer {self.secret}"})
        r.raise_for_status()
        self._telegram(json.dumps(order, indent=1, default=str)[:4000])

    def _telegram(self, text: str):
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not (tok and chat):
            return
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": text})
        except Exception as e:
            log.warning("telegram failed: %s", e)
