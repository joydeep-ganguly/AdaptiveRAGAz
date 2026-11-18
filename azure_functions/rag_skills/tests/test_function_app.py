import json

from skills.endpoints import embed_endpoint, pdf_pages_endpoint


def test_not_json_is_400():
    assert pdf_pages_endpoint(b"not json")[0] == 400
    assert embed_endpoint(b"")[0] == 400


def test_not_a_skill_request_is_400():
    assert pdf_pages_endpoint(json.dumps({"x": 1}).encode())[0] == 400


def test_record_errors_come_back_as_200_with_errors():
    status, payload = pdf_pages_endpoint(json.dumps({"values": [{"recordId": "1", "data": {}}]}).encode())
    assert status == 200 and payload["values"][0]["errors"]
    status, payload = embed_endpoint(json.dumps({"values": [{"recordId": "1", "data": {}}]}).encode())
    assert status == 200 and payload["values"][0]["errors"]


def test_function_app_registers_the_routes():
    import function_app  # imports without Azure credentials or settings

    assert function_app.app is not None
