"""What each HTTP route does with a request body. function_app.py only wires these to routes."""

import json

from skills.handlers import embed, pdf_pages
from skills.protocol import BadRequest, handle_batch_request, handle_request


def _answer(raw_body: bytes, runner) -> tuple[int, dict]:
    try:
        body = json.loads(raw_body or b"")
    except ValueError:
        return 400, {"error": "The body is not valid JSON"}
    try:
        return 200, runner(body)
    except BadRequest as exc:
        return 400, {"error": str(exc)}


def pdf_pages_endpoint(raw_body: bytes) -> tuple[int, dict]:
    return _answer(raw_body, lambda body: handle_request(body, pdf_pages))


def embed_endpoint(raw_body: bytes) -> tuple[int, dict]:
    return _answer(raw_body, lambda body: handle_batch_request(body, embed))
