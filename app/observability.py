"""
Observability layer: structured JSON logging, Prometheus metrics, and
request correlation IDs.

The correlation ID is the backbone that ties the three pillars together:
- It is generated (or accepted from the caller) once per request.
- It is stored in a ContextVar so *any* log line emitted while handling the
  request automatically carries it -- no need to thread it through every call.
- It is attached to every metric-adjacent log and returned to the caller in
  the `X-Request-ID` header and the response body, so a user reporting a
  problem can hand you the exact ID to grep for.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar

from prometheus_client import Counter, Histogram

# --------------------------------------------------------------------------- #
# Correlation ID context
# --------------------------------------------------------------------------- #
# ContextVar is coroutine-safe: each request handled by the event loop sees its
# own value even under concurrency.
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    """Generate a fresh correlation ID."""
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str) -> None:
    _request_id_ctx.set(request_id)


def get_request_id() -> str:
    return _request_id_ctx.get()


# --------------------------------------------------------------------------- #
# Structured JSON logging
# --------------------------------------------------------------------------- #
class JsonLogFormatter(logging.Formatter):
    """Render each log record as a single-line JSON object.

    Machine-parseable logs are what make a log pipeline (Loki, ELK,
    CloudWatch) able to filter by request_id, level, or any extra field.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        # Merge any structured fields passed via `extra={"context": {...}}`.
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Install the JSON formatter on the root logger and return the app logger.

    Logs always go to stdout; if LOG_FILE is set, they are also appended to
    that file so tools like grep can query request history.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonLogFormatter())
        root.addHandler(file_handler)
    root.setLevel(level)

    # Quiet uvicorn's own access logs -- our middleware emits richer ones.
    logging.getLogger("uvicorn.access").disabled = True

    return logging.getLogger("app")


def log(logger: logging.Logger, level: int, message: str, **fields) -> None:
    """Helper to emit a structured log line with arbitrary extra fields."""
    logger.log(level, message, extra={"context": fields})


# --------------------------------------------------------------------------- #
# Prometheus metrics (dashboard-ready, /metrics scrape target)
# --------------------------------------------------------------------------- #
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests processed.",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds.",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

ERROR_COUNT = Counter(
    "http_errors_total",
    "Total HTTP requests that resulted in an error (status >= 500 or handled failure).",
    ["endpoint", "error_type"],
)

ASK_ACTIONS = Counter(
    "ask_business_actions_total",
    "Business actions triggered by the /ask endpoint.",
    ["action"],
)
