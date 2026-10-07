import re

from typesafe_sdk import Choice, Noul, Score

from collect import social_state
from questions import SETS, STATE_FIELDS, PICK


def _instr_fields(q):
    return set(re.findall(r"`([a-z_0-9]+)(?:\.[a-z_0-9]+)?`", q.instructions or ""))


def test_every_set_builds_and_is_typed():
    for name, qs in SETS.items():
        if callable(qs):
            qs = qs({"candidates": [{"label": "A [solana:abc]", "summary": "x"},
                                    {"label": "B [bsc:0x1]", "summary": "y"}]})
        assert qs, name
        for q in qs.values():
            assert isinstance(q, (Choice, Noul, Score))
            q.model_dump()                       # serialises to the wire form


def test_questions_only_read_fields_the_state_carries():
    social_fields = set(social_state({"x_account": {}, "x_handle": "a", "ticker": "T"}))
    for name, qs in SETS.items():
        if callable(qs):
            continue
        fields = set(STATE_FIELDS.get(name, social_fields))
        for qname, q in qs.items():
            missing = _instr_fields(q) - fields - {"state"}
            assert not missing, f"{name}.{qname} reads {missing} that the state never carries"


def test_pick_labels_are_the_options():
    qs = PICK({"candidates": [{"label": "X [solana:aaa]", "summary": "s1"},
                              {"label": "X [solana:bbb]", "summary": "s2"}]})
    assert list(qs["best"].criteria) == ["X [solana:aaa]", "X [solana:bbb]"]
