"""
End-to-end demo.

Starts the FastAPI app in-process (via httpx ASGITransport -- no network port
needed) and fires two requests:

  1. A SUCCESSFUL request  -> POST /ask {"question": "generate a report"}
  2. A FAILING request     -> POST /ask {"question": "boom"}   (forces a 502)
  3. An INVALID request     -> POST /ask {"question": ""}       (422 validation)

For each it prints the response, the correlation ID, and points out how the
structured logs (printed to stdout by the app) let you trace the failure.
Finally it dumps the /metrics scrape so you can see the counters move.
"""

import asyncio
import json

import httpx

from app.main import app


def banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        banner("1. HEALTH CHECK  ->  GET /health")
        r = await client.get("/health")
        print(f"HTTP {r.status_code}  X-Request-ID={r.headers.get('X-Request-ID')}")
        print(json.dumps(r.json(), indent=2))

        banner("2. SUCCESS  ->  POST /ask {'question': 'generate a weekly report'}")
        r = await client.post("/ask", json={"question": "generate a weekly report"})
        print(f"HTTP {r.status_code}  X-Request-ID={r.headers.get('X-Request-ID')}")
        print(json.dumps(r.json(), indent=2))

        banner("3. HANDLED FAILURE  ->  POST /ask {'question': 'boom'}")
        r = await client.post("/ask", json={"question": "boom"})
        print(f"HTTP {r.status_code}  X-Request-ID={r.headers.get('X-Request-ID')}")
        print(json.dumps(r.json(), indent=2))
        print(">> Grep the logs above for this request_id to see 'agent.downstream_failure'.")

        banner("4. INVALID INPUT  ->  POST /ask {'question': ''}")
        r = await client.post("/ask", json={"question": ""})
        print(f"HTTP {r.status_code}")
        print(json.dumps(r.json(), indent=2))
        print(">> FastAPI/Pydantic rejects empty questions before the agent runs (422).")

        banner("5. METRICS  ->  GET /metrics  (Prometheus format, filtered)")
        r = await client.get("/metrics")
        for line in r.text.splitlines():
            if line.startswith(("http_requests_total", "http_errors_total",
                                "ask_business_actions_total")) and "{" in line:
                print(line)


if __name__ == "__main__":
    asyncio.run(main())
