import pytest

from skills.errors import SkillError
from skills.protocol import BadRequest, handle_batch_request, handle_request


def test_each_record_is_answered_and_errors_do_not_stop_the_batch():
    def handler(data):
        if data.get("fail") == "skill":
            raise SkillError("bad input")
        if data.get("fail") == "crash":
            raise RuntimeError("boom")
        return {"x": data["n"]}, ["careful"] if data["n"] == 2 else []

    body = {"values": [
        {"recordId": "a", "data": {"n": 1}},
        {"recordId": "b", "data": {"n": 2}},
        {"recordId": "c", "data": {"fail": "skill"}},
        {"recordId": "d", "data": {"fail": "crash"}},
    ]}
    values = {v["recordId"]: v for v in handle_request(body, handler)["values"]}

    assert values["a"] == {"recordId": "a", "data": {"x": 1}, "errors": None, "warnings": None}
    assert values["b"]["warnings"] == [{"message": "careful"}]
    assert values["c"]["errors"] == [{"message": "bad input"}] and values["c"]["data"] == {}
    assert "boom" in values["d"]["errors"][0]["message"]


@pytest.mark.parametrize("body", [None, [], {}, {"values": "x"}, {"values": [{"data": {}}]}])
def test_not_a_skill_request(body):
    with pytest.raises(BadRequest):
        handle_request(body, lambda d: ({}, []))
    with pytest.raises(BadRequest):
        handle_batch_request(body, lambda r: {})


def test_batch_missing_outcome_is_an_error_for_that_record():
    body = {"values": [{"recordId": "1", "data": {}}, {"recordId": "2", "data": {}}]}
    out = handle_batch_request(body, lambda records: {"1": ({"v": 1}, [])})
    assert out["values"][0]["data"] == {"v": 1}
    assert out["values"][1]["errors"]


def test_batch_exception_fails_every_record_but_still_answers():
    body = {"values": [{"recordId": "1", "data": {}}]}

    def boom(records):
        raise RuntimeError("down")

    out = handle_batch_request(body, boom)
    assert out["values"][0]["errors"][0]["message"].startswith("Unexpected error")
