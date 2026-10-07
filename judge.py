"""The judge. Runs on one machine, holds the only Jev key, makes no trading decision.

Bots get DESK_SECRET and never the TypeSafe key.

    uvicorn judge:app --host 0.0.0.0 --port 8080
"""
import hmac
import logging
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typesafe_sdk import (AsyncTypeSafeClient, TypeSafeAPIConnectionError,
                          TypeSafeAPIError, TypeSafeUnprocessableEntityError)

import book
from questions import SETS

DESK_SECRET = os.environ["DESK_SECRET"]     # for the bots. NOT the TypeSafe key.
log = logging.getLogger("judge")
app = FastAPI()
_client = None


def client() -> AsyncTypeSafeClient:
    global _client
    if _client is None:
        _client = AsyncTypeSafeClient()     # reads TYPESAFE_API_KEY itself
    return _client


def _auth(authorization: str):
    if not hmac.compare_digest(authorization.encode(), f"Bearer {DESK_SECRET}".encode()):
        raise HTTPException(401, "bad desk secret")


class Ask(BaseModel):
    question_set: str
    state: dict


@app.post("/judge")
async def judge(ask: Ask, authorization: str = Header("")):
    _auth(authorization)
    if ask.question_set not in SETS:
        raise HTTPException(422, f"unknown question set {ask.question_set}")

    qs = SETS[ask.question_set]
    try:
        qs = qs(ask.state) if callable(qs) else qs    # pick builds options at call time
    except (KeyError, TypeError) as e:
        raise HTTPException(422, f"state does not fit {ask.question_set}: {e}")

    # one call per token, never one per question. The SDK backs off on 429 and 5xx itself.
    try:
        r = await client().system_one(state=ask.state, questions=qs)
    except TypeSafeUnprocessableEntityError as e:
        raise HTTPException(422, f"jev rejected {ask.question_set}: {e}")   # never retry
    except TypeSafeAPIConnectionError as e:
        raise HTTPException(503, f"jev unreachable: {e}")
    except TypeSafeAPIError as e:
        # 401/403 here is OUR key, not the bot's secret. Never hand the bot a 401 for it.
        raise HTTPException(502 if e.status < 500 else 503, f"jev error {e.status}")

    log.info("set=%s model=%s usage=%s", ask.question_set, r.model, r.usage.model_dump())
    # raw answers out. never flattened, never thresholded here.
    return {"model": r.model,
            "answers": {k: v.model_dump() for k, v in r.answers.items()},
            "usage": r.usage.model_dump()}


# RISK lives in xAI's cloud and needs a route to free the book after a close fills.
# This is bookkeeping, not judgement: it only says whether a position is open.
@app.get("/book/held")
async def book_held(authorization: str = Header("")):
    _auth(authorization)
    return {"held": book.held()}


@app.post("/book/release")
async def book_release(authorization: str = Header("")):
    _auth(authorization)
    was = book.held()
    book.release()
    log.warning("book released by RISK, was %s", was)
    return {"released": was}
