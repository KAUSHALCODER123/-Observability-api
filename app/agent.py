"""
Mock AI agent.

`process_question` stands in for a real LLM/agent call. It:
  1. Classifies the question into an intent (a business action).
  2. Executes that action against mock data.
  3. Returns a structured answer plus metadata about the "reasoning".

Because there is no external LLM dependency, the failure modes here are the
deterministic ones the observability stack is meant to catch: validation
errors and simulated downstream faults. Sending a question containing the
word "boom" forces a downstream failure so the error path can be demonstrated.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .observability import ASK_ACTIONS, get_request_id, log

logger = logging.getLogger("app.agent")


class AgentError(Exception):
    """Raised when the agent cannot fulfil a request (a handled failure)."""


# --------------------------------------------------------------------------- #
# Mock "database"
# --------------------------------------------------------------------------- #
_MOCK_METRICS = {
    "active_users": 1_284,
    "revenue_usd": 48_210,
    "open_tickets": 7,
    "uptime_pct": 99.94,
}

_ticket_seq = 1000


@dataclass
class AgentResult:
    action: str
    answer: str
    data: dict = field(default_factory=dict)
    tokens: int = 0


def _classify(question: str) -> str:
    """Very small intent router -- keyword based, stands in for an LLM classifier."""
    q = question.lower()
    if "boom" in q or "crash" in q:
        return "simulate_failure"
    if "report" in q:
        return "generate_report"
    if "ticket" in q or "issue" in q:
        return "create_ticket"
    if "workflow" in q or "trigger" in q or "deploy" in q:
        return "trigger_workflow"
    if "how many" in q or "metric" in q or "data" in q or "users" in q or "revenue" in q:
        return "query_data"
    return "answer_general"


def process_question(question: str) -> AgentResult:
    """Process a question and return a structured result. Emits step logs."""
    action = _classify(question)
    log(logger, logging.INFO, "agent.classified", action=action)

    # Simulate model "thinking" latency so the latency histogram has signal.
    time.sleep(0.02)

    if action == "simulate_failure":
        # A deterministic downstream failure to demonstrate error observability.
        log(logger, logging.ERROR, "agent.downstream_failure",
            reason="simulated LLM provider 503")
        raise AgentError("Downstream LLM provider returned 503 Service Unavailable")

    ASK_ACTIONS.labels(action=action).inc()

    if action == "generate_report":
        result = AgentResult(
            action=action,
            answer="Weekly operations report generated.",
            data={
                "report_id": f"rpt-{get_request_id()}",
                "sections": ["usage", "revenue", "reliability"],
                "summary": _MOCK_METRICS,
            },
            tokens=142,
        )
    elif action == "create_ticket":
        global _ticket_seq
        _ticket_seq += 1
        result = AgentResult(
            action=action,
            answer=f"Support ticket #{_ticket_seq} created and assigned to on-call.",
            data={"ticket_id": _ticket_seq, "status": "open", "priority": "medium"},
            tokens=88,
        )
    elif action == "trigger_workflow":
        result = AgentResult(
            action=action,
            answer="Workflow 'nightly-sync' triggered.",
            data={"workflow": "nightly-sync", "run_id": f"run-{get_request_id()}",
                  "state": "queued"},
            tokens=64,
        )
    elif action == "query_data":
        result = AgentResult(
            action=action,
            answer="Here are the current platform metrics.",
            data={"metrics": _MOCK_METRICS},
            tokens=57,
        )
    else:
        result = AgentResult(
            action=action,
            answer=(
                f"I processed your question: '{question[:80]}'. "
                "Ask me to generate a report, create a ticket, query data, "
                "or trigger a workflow."
            ),
            tokens=40,
        )

    log(logger, logging.INFO, "agent.completed", action=action, tokens=result.tokens)
    return result
