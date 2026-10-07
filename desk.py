"""The desk's outside world, as main.py sees it: X reads and reports.

There is no order delivery here. Live execution is disabled; orders go to the paper
venue in venue.py and nowhere else.

    X_READER_URL         SOCIAL's endpoint: POST {"handle"} -> X block or null
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   one message per cycle and per exit
"""
import json
import logging
import os

import requests

log = logging.getLogger("desk")


class Desk:
    def __init__(self):
        self.secret = os.environ.get("DESK_SECRET", "")

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

    def report(self, what):
        line = what if isinstance(what, str) else json.dumps(what, default=str)
        log.info(line)
        self._telegram(line[:4000])

    def _telegram(self, text: str):
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not (tok and chat):
            return
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": text})
        except Exception as e:
            log.warning("telegram failed: %s", e)
