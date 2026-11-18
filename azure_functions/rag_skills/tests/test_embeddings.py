import pytest
from google.genai import errors

from skills.embeddings import FALLBACK_CHARS, embed_texts
from skills.errors import SkillError


class Item:
    def __init__(self, values):
        self.values = values


class Result:
    def __init__(self, vectors):
        self.embeddings = [Item(v) for v in vectors]


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def embed_content(self, model, contents, config):
        texts = [c["parts"][0]["text"] for c in contents]
        self.calls.append((model, texts, config.task_type))
        return self.behaviour(texts)


class FakeClient:
    def __init__(self, behaviour):
        self.models = FakeModels(behaviour)


def api_error(code):
    return errors.ClientError(code, {"error": {"message": "x", "status": "S"}}) if code < 500 \
        else errors.ServerError(code, {"error": {"message": "x", "status": "S"}})


def test_documents_use_the_document_task_type_and_keep_order():
    client = FakeClient(lambda texts: Result([[float(len(t))] for t in texts]))
    out = embed_texts(["a", "bbb"], client=client, model="m", sleep=lambda s: None)
    assert [o.vector for o in out] == [[1.0], [3.0]]
    assert client.models.calls == [("m", ["a", "bbb"], "RETRIEVAL_DOCUMENT")]


def test_rate_limit_is_retried():
    attempts = []

    def behaviour(texts):
        attempts.append(1)
        if len(attempts) < 3:
            raise api_error(429)
        return Result([[1.0]])

    slept = []
    out = embed_texts(["a"], client=FakeClient(behaviour), model="m", sleep=slept.append)
    assert out[0].vector == [1.0]
    assert slept == [2, 5]


def test_server_error_that_never_clears_is_a_skill_error():
    with pytest.raises(SkillError):
        embed_texts(["a"], client=FakeClient(lambda t: (_ for _ in ()).throw(api_error(503))),
                    model="m", sleep=lambda s: None)


def test_refused_text_is_retried_shortened_with_a_warning_and_others_are_unaffected():
    long_text = "x" * (FALLBACK_CHARS + 500)

    def behaviour(texts):
        if any(len(t) > FALLBACK_CHARS for t in texts):
            raise api_error(400)
        return Result([[float(len(t))] for t in texts])

    out = embed_texts(["ok", long_text], client=FakeClient(behaviour), model="m", sleep=lambda s: None)
    assert out[0].vector == [2.0] and out[0].warning is None
    assert out[1].vector == [float(FALLBACK_CHARS)] and "first 6000" in out[1].warning


def test_text_refused_even_when_shortened_is_an_error_for_that_text_only():
    def behaviour(texts):
        if "bad" in texts:
            raise api_error(400)
        return Result([[1.0] for _ in texts])

    out = embed_texts(["fine", "bad"], client=FakeClient(behaviour), model="m", sleep=lambda s: None)
    assert out[0].vector == [1.0]
    assert isinstance(out[1], SkillError)
