"""
Test doubles for the GitHub REST API.

FakeGitHub stands in for api.github.com at the HTTP boundary (requests.get /
requests.post), so dispatch and catch-up logic runs for real against
responses shaped like GitHub's `workflow-dispatch-response` and `workflow-run`
schemas.
"""

from __future__ import annotations

import json
import re

API = "https://api.github.com"
REPO = "arsalannkhann/zoho-bot"
WORKFLOW = "attendance.yml"


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("No JSON body")
        return self._body


def make_run(
    run_id: int,
    action: str,
    *,
    source: str = "scheduler",
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = "2026-09-16T06:30:05Z",
) -> dict:
    """A workflow run carrying the `workflow-run` fields the bot reads."""
    return {
        "id": run_id,
        "name": "Zoho People Attendance (Browser)",
        "display_title": f"attendance: {action} ({source})",
        "event": "workflow_dispatch",
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "head_branch": "main",
        "run_number": run_id % 1000,
        "run_attempt": 1,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        "url": f"{API}/repos/{REPO}/actions/runs/{run_id}",
        "created_at": created_at,
        "updated_at": created_at,
    }


class FakeGitHub:
    """Replies are consumed in order; once a queue is empty the happy path
    applies (dispatch → 200 with a new run id, run → completed/success)."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.history: list[dict] = []
        self.dispatch_replies: list = []
        self.list_replies: list = []
        self.run_states: dict[int, list] = {}
        self._dispatched: dict[int, dict] = {}
        self._next_id = 9001

    @property
    def dispatches(self) -> list[dict]:
        return [kwargs["json"] for method, _, kwargs in self.requests if method == "POST"]

    @staticmethod
    def _queued(replies: list):
        reply = replies.pop(0) if replies else None
        if isinstance(reply, Exception):
            raise reply
        return reply

    def post(self, url: str, **kwargs):
        self.requests.append(("POST", url, kwargs))
        assert url == f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches", url
        reply = self._queued(self.dispatch_replies)
        if reply is not None:
            return reply
        run_id = self._next_id
        self._next_id += 1
        self._dispatched[run_id] = kwargs["json"]["inputs"]
        return FakeResponse(200, {
            "workflow_run_id": run_id,
            "run_url": f"{API}/repos/{REPO}/actions/runs/{run_id}",
            "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        })

    def get(self, url: str, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if url == f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs":
            reply = self._queued(self.list_replies)
            if reply is not None:
                return reply
            params = kwargs["params"]
            since = params["created"].removeprefix(">=")
            runs = [r for r in self.history
                    if r["event"] == params["event"] and r["created_at"] >= since]
            return FakeResponse(200, {"total_count": len(runs), "workflow_runs": runs})

        match = re.fullmatch(rf"{API}/repos/{REPO}/actions/runs/(\d+)", url)
        assert match, url
        run_id = int(match.group(1))
        states = self.run_states.get(run_id) or [("completed", "success")]
        state = states.pop(0) if len(states) > 1 else states[0]
        if isinstance(state, Exception):
            raise state
        if isinstance(state, FakeResponse):
            return state
        status, conclusion = state
        inputs = self._dispatched.get(run_id, {"action": "checkin", "source": "scheduler"})
        return FakeResponse(200, make_run(
            run_id, inputs["action"], source=inputs["source"],
            status=status, conclusion=conclusion,
        ))
