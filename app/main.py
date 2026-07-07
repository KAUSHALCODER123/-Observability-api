"""
FastAPI application wiring together the API, the mock agent, and the
observability layer.

Endpoints:
  POST /ask      -- process an AI question, run a business action.
  GET  /health   -- liveness/readiness probe.
  GET  /metrics  -- Prometheus scrape endpoint.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import __version__
from .agent import AgentError, process_question
from .observability import (
    ERROR_COUNT,
    REQUEST_COUNT,
    REQUEST_LATENCY,
    configure_logging,
    get_request_id,
    log,
    new_request_id,
    set_request_id,
)

logger = configure_logging()
app = FastAPI(title="Observable AI Backend", version=__version__)

REQUEST_ID_HEADER = "X-Request-ID"


# --------------------------------------------------------------------------- #
# Correlation-ID + metrics middleware
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    # Reuse an inbound correlation ID if the caller (or an upstream proxy)
    # supplied one; otherwise mint a fresh one. This is what lets a trace
    # survive across service hops.
    request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
    set_request_id(request_id)

    endpoint = request.url.path
    start = time.perf_counter()
    log(logger, logging.INFO, "request.started",
        method=request.method, endpoint=endpoint,
        client=request.client.host if request.client else None)

    try:
        response = await call_next(request)
    except Exception as exc:  # unhandled -> 500
        duration = time.perf_counter() - start
        REQUEST_LATENCY.labels(request.method, endpoint).observe(duration)
        REQUEST_COUNT.labels(request.method, endpoint, "500").inc()
        ERROR_COUNT.labels(endpoint, type(exc).__name__).inc()
        log(logger, logging.ERROR, "request.unhandled_error",
            endpoint=endpoint, error=str(exc), duration_ms=round(duration * 1000, 2))
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "request_id": request_id},
            headers={REQUEST_ID_HEADER: request_id},
        )

    duration = time.perf_counter() - start
    REQUEST_LATENCY.labels(request.method, endpoint).observe(duration)
    REQUEST_COUNT.labels(request.method, endpoint, str(response.status_code)).inc()
    if response.status_code >= 500:
        ERROR_COUNT.labels(endpoint, "http_5xx").inc()

    response.headers[REQUEST_ID_HEADER] = request_id
    log(logger, logging.INFO, "request.completed",
        endpoint=endpoint, status_code=response.status_code,
        duration_ms=round(duration * 1000, 2))
    return response


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="The natural-language question to process.")


class AskResponse(BaseModel):
    request_id: str
    action: str
    answer: str
    data: dict
    tokens: int
    latency_ms: float


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest):
    start = time.perf_counter()
    log(logger, logging.INFO, "ask.received", question_len=len(payload.question))
    try:
        result = process_question(payload.question)
    except AgentError as exc:
        # Handled business failure -> 502, still fully observable.
        ERROR_COUNT.labels("/ask", "AgentError").inc()
        log(logger, logging.ERROR, "ask.agent_error", error=str(exc))
        return JSONResponse(
            status_code=502,
            content={
                "error": "agent_failure",
                "detail": str(exc),
                "request_id": get_request_id(),
            },
        )

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return AskResponse(
        request_id=get_request_id(),
        action=result.action,
        answer=result.answer,
        data=result.data,
        tokens=result.tokens,
        latency_ms=latency_ms,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__, "request_id": get_request_id()}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    return {
        "service": "Observable AI Backend",
        "version": __version__,
        "endpoints": ["POST /ask", "GET /health", "GET /metrics"],
    }
