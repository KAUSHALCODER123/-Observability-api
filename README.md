# Observable AI Backend

A small, production-shaped FastAPI service that processes AI requests and is
instrumented so an engineer can **monitor, debug, and troubleshoot** it in
production. The AI is a deterministic mock agent so the focus stays on
*observability engineering*, not model quality.

```
┌────────────┐   POST /ask    ┌──────────────────────────────────────────┐
│  client    │ ─────────────► │  observability middleware                 │
└────────────┘                │   • mint/propagate X-Request-ID           │
       ▲                      │   • time request  → latency histogram     │
       │  JSON + X-Request-ID │   • count request → counters              │
       │                      │   • structured JSON log (start/complete)  │
       │                      └───────────────────┬──────────────────────┘
       │                                          ▼
       │                              ┌───────────────────────┐
       └──────────────────────────────┤  mock agent           │
                                       │  classify → action →  │
                                       │  mock data / failure  │
                                       └───────────────────────┘
                    /metrics  ← Prometheus scrape (counters + histogram)
                    /health   ← liveness / readiness
```

## Endpoints

| Method | Path       | Purpose                                              |
|--------|------------|------------------------------------------------------|
| POST   | `/ask`     | Process a question; run a business action.           |
| GET    | `/health`  | Liveness/readiness probe.                            |
| GET    | `/metrics` | Prometheus scrape endpoint (counters + histogram).   |
| GET    | `/`        | Service metadata.                                    |

`POST /ask` accepts `{"question": "..."}` and routes it to one of five
**business actions** by keyword intent classification:

| If the question mentions… | Action              | What it does (mock)                         |
|---------------------------|---------------------|---------------------------------------------|
| "report"                  | `generate_report`   | Builds a weekly ops report from mock metrics|
| "ticket" / "issue"        | `create_ticket`     | Creates an incrementing support ticket      |
| "workflow" / "deploy"     | `trigger_workflow`  | Queues a `nightly-sync` workflow run        |
| "data" / "users" / …      | `query_data`        | Returns mock platform metrics               |
| "boom" / "crash"          | `simulate_failure`  | Forces a downstream 503 → 502 (for demos)   |
| anything else             | `answer_general`    | Generic answer                              |

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # (Linux/Mac: .venv/bin/pip)

# Option A — the guided end-to-end demo (no port needed, prints logs+metrics):
.venv/Scripts/python demo.py

# Option B — a real server:
.venv/Scripts/uvicorn app.main:app --reload
#   curl -X POST localhost:8000/ask -H 'content-type: application/json' \
#        -d '{"question":"generate a report"}'
#   curl localhost:8000/health
#   curl localhost:8000/metrics

# Tests:
.venv/Scripts/python -m pytest -q

# Docker:
docker build -t observable-ai . && docker run -p 8000:8000 observable-ai
```

## The three pillars of observability

**1. Structured logging.** Every log line is a single-line JSON object
(`app/observability.py::JsonLogFormatter`) with `timestamp`, `level`,
`request_id`, `message`, and arbitrary structured fields. Machine-parseable
logs are what let Loki/ELK filter by `request_id` or `action`. The agent emits
lifecycle events (`agent.classified`, `agent.completed`,
`agent.downstream_failure`) so you can reconstruct exactly what happened inside
a single request.

**2. Metrics** (`/metrics`, Prometheus format — dashboard-ready):
- `http_requests_total{method,endpoint,status_code}` — traffic & status mix.
- `http_request_duration_seconds` — a **histogram**, so Prometheus can compute
  p50/p95/p99 latency (`histogram_quantile(0.95, ...)`).
- `http_errors_total{endpoint,error_type}` — error rate by type.
- `ask_business_actions_total{action}` — which business actions get used.

**3. Tracing (lightweight).** Because the correlation ID lives in a
`ContextVar`, every log line emitted while handling one request shares the same
`request_id`. Filtering the log stream to a single ID gives you the ordered
"trace" of that request's spans (received → classified → completed/failed →
response). The ID is accepted from an inbound `X-Request-ID` header if present,
so the trace survives across service hops — the same seam OpenTelemetry's
`traceparent` uses. Swapping this for full OTel spans is a drop-in upgrade
(instrument once in the middleware; the propagation contract is already here).

## The one real engineering improvement: **Request Correlation IDs**

**What.** Each request is assigned a 16-char correlation ID. It is:
- reused from the inbound `X-Request-ID` header if the caller supplied one,
  otherwise freshly minted (`app/observability.py::new_request_id`);
- stored in a coroutine-safe `ContextVar` so **any** code path — middleware,
  route, agent — can attach it to a log line without threading it through every
  function signature;
- injected into every structured log record automatically by the formatter;
- returned to the caller in both the `X-Request-ID` **response header** and the
  JSON **response body**.

**Why.** This is the single highest-leverage observability primitive for
debugging production. When a user reports "my request failed at 14:32", the
first question is always *"which request?"*. With correlation IDs the user (or
your API gateway) hands you one string; `grep <id>` collapses thousands of
interleaved concurrent log lines into the exact story of that one request —
across every component and, via header propagation, across every service. It
turns "search the haystack" into "follow the thread," and it's the foundation
distributed tracing is built on. Everything else (latency histograms, error
counters) tells you *that* something is wrong; the correlation ID tells you
*which* request and lets you replay *why*.

## Two test inputs — and how observability finds the problem

### ✅ Successful request

```json
POST /ask  {"question": "generate a weekly report"}
→ 200
{"request_id":"406361531c10478a","action":"generate_report",
 "answer":"Weekly operations report generated.","tokens":142,"latency_ms":30.49, ...}
```

Logs show the full happy-path trace for `406361531c10478a`:
`request.started → ask.received → agent.classified(generate_report) →
agent.completed → request.completed(200)`. Metrics:
`http_requests_total{status_code="200"}` and
`ask_business_actions_total{action="generate_report"}` both increment.

### ❌ Failing request

```json
POST /ask  {"question": "boom"}
→ 502
{"error":"agent_failure",
 "detail":"Downstream LLM provider returned 503 Service Unavailable",
 "request_id":"1195a2d9acb24f4b"}
```

**How observability pinpoints it:**
1. **Metric** — `http_errors_total{endpoint="/ask",error_type="AgentError"}`
   ticks up; on a dashboard this is the alert that fires.
2. **Correlation ID** — the 502 body hands you `1195a2d9acb24f4b`.
3. **Logs** — `grep 1195a2d9acb24f4b` yields the exact failing span:
   ```json
   {"level":"ERROR","message":"agent.downstream_failure",
    "request_id":"1195a2d9acb24f4b","reason":"simulated LLM provider 503"}
   ```
   → root cause (downstream 503), not just the symptom (502).

A second failure mode — **invalid input** `{"question": ""}` → `422` — is
rejected by Pydantic validation *before* the agent runs, and is visible as
`http_requests_total{status_code="422"}` (a client error, distinct from the
502 server-side error class, so alerting can treat them differently).

## Debugging & performance workflow this enables

- **"Is it broken?"** → watch `http_errors_total` rate and the `status_code`
  breakdown on `http_requests_total`.
- **"Is it slow?"** → `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))`.
  The agent's simulated 20 ms think-time shows up as real p95 signal.
- **"Which request, and why?"** → correlation ID from the response → grep the
  structured logs → the ordered per-request trace.

## Project layout

```
app/
  main.py            FastAPI app, middleware, routes, schemas
  agent.py           mock AI agent: intent classification + business actions
  observability.py   JSON logging, correlation-ID ContextVar, Prometheus metrics
demo.py              in-process end-to-end demo (success + failure + metrics)
tests/test_api.py    pytest suite (7 tests)
Dockerfile           container image
requirements.txt
```

## Design decisions worth calling out

- **`ContextVar`, not a passed-around argument.** Correlation-ID propagation
  that relies on every function accepting a `request_id` param rots the moment
  someone forgets. A `ContextVar` is coroutine-safe and makes correct behavior
  the default.
- **Histogram, not a gauge, for latency.** A gauge shows only the last value;
  a histogram lets Prometheus compute percentiles across the fleet — the metric
  that actually matters for SLOs.
- **Distinguish client (4xx) from server (5xx) errors.** Empty input is a
  `422` and is *not* counted in `http_errors_total` as a server fault, so a
  spike in bad client requests doesn't page the on-call engineer for a
  service that's actually healthy.
- **Handled agent failures return `502`, not `500`.** The service is up; the
  *downstream* dependency failed. The status code carries that distinction to
  dashboards and alerts.
- **Single uvicorn worker by default.** Keeps the in-process Prometheus
  registry coherent; the README notes the multiprocess-collector path for
  scaling out.
