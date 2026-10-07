"""The only place a bot or the shift touches the network for a judgement."""
import os

import requests


class JudgeMalformed(RuntimeError):
    """422. The question or state is wrong and will stay wrong. Never retry."""


class JudgeUnavailable(RuntimeError):
    """The judge could not answer. Missing is missing: stand down, never guess."""


def judge(question_set: str, state: dict) -> dict:
    url, secret = os.environ["JUDGE_URL"], os.environ["DESK_SECRET"]
    try:
        r = requests.post(url, timeout=30,
                          headers={"Authorization": f"Bearer {secret}"},
                          json={"question_set": question_set, "state": state})
    except requests.RequestException as e:
        raise JudgeUnavailable(f"{question_set}: {e}") from e
    if r.status_code == 422:
        raise JudgeMalformed(f"malformed question set {question_set}: {r.text}")
    if r.status_code != 200:
        raise JudgeUnavailable(f"{question_set}: {r.status_code} {r.text[:200]}")
    return r.json()
