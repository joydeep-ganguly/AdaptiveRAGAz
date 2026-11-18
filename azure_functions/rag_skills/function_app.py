import json
import logging

import azure.functions as func

from skills.endpoints import embed_endpoint, pdf_pages_endpoint

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _respond(status: int, payload: dict) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload), status_code=status, mimetype="application/json")


@app.route(route="pdf-pages", methods=["POST"])
def pdf_pages_skill(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("pdf-pages request")
    return _respond(*pdf_pages_endpoint(req.get_body()))


@app.route(route="embed", methods=["POST"])
def embed_skill(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("embed request")
    return _respond(*embed_endpoint(req.get_body()))


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _respond(200, {"status": "ok"})
