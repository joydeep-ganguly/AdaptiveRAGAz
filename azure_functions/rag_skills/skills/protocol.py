"""The custom Web API skill contract: {"values": [{"recordId", "data"}]} in and out."""

import logging
from typing import Callable

from skills.errors import SkillError

logger = logging.getLogger(__name__)

Handler = Callable[[dict], tuple[dict, list[str]]]
# Gets every (recordId, data) of the request at once and returns, per recordId,
# either (output, warnings) or a SkillError for that record.
BatchHandler = Callable[[list[tuple[object, dict]]], dict]


class BadRequest(Exception):
    """The body is not a skill request at all (answered with HTTP 400)."""


def handle_request(body, handler: Handler) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("values"), list):
        raise BadRequest("The body must be a JSON object with a 'values' array")

    results = []
    for record in body["values"]:
        if not isinstance(record, dict) or "recordId" not in record:
            raise BadRequest("Every record needs a 'recordId'")

        record_id = record["recordId"]
        data = record.get("data")
        data = data if isinstance(data, dict) else {}
        try:
            output, warnings = handler(data)
            results.append({
                "recordId": record_id,
                "data": output,
                "errors": None,
                "warnings": [{"message": w} for w in warnings] or None,
            })
        except SkillError as exc:
            results.append({"recordId": record_id, "data": {}, "errors": [{"message": str(exc)}], "warnings": None})
        except Exception as exc:  # one bad record must not fail the whole batch
            logger.exception("Unexpected error in record %s", record_id)
            results.append({
                "recordId": record_id,
                "data": {},
                "errors": [{"message": f"Unexpected error: {type(exc).__name__}: {exc}"}],
                "warnings": None,
            })
    return {"values": results}


def handle_batch_request(body, batch_handler: BatchHandler) -> dict:
    """For skills that work better on all records of a request together (embeddings)."""
    if not isinstance(body, dict) or not isinstance(body.get("values"), list):
        raise BadRequest("The body must be a JSON object with a 'values' array")

    records = []
    for record in body["values"]:
        if not isinstance(record, dict) or "recordId" not in record:
            raise BadRequest("Every record needs a 'recordId'")
        data = record.get("data")
        records.append((record["recordId"], data if isinstance(data, dict) else {}))

    try:
        outcomes = batch_handler(records)
    except SkillError as exc:
        outcomes = {record_id: exc for record_id, _ in records}
    except Exception as exc:
        logger.exception("Unexpected error in batch")
        failure = SkillError(f"Unexpected error: {type(exc).__name__}: {exc}")
        outcomes = {record_id: failure for record_id, _ in records}

    results = []
    for record_id, _ in records:
        outcome = outcomes.get(record_id)
        if isinstance(outcome, SkillError) or outcome is None:
            message = str(outcome) if outcome is not None else "No result for this record"
            results.append({"recordId": record_id, "data": {}, "errors": [{"message": message}], "warnings": None})
        else:
            output, warnings = outcome
            results.append({
                "recordId": record_id,
                "data": output,
                "errors": None,
                "warnings": [{"message": w} for w in warnings] or None,
            })
    return {"values": results}
