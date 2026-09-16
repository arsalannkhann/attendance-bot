"""
github_dispatch.py — run the attendance browser on GitHub Actions.

Render's free instance (512 MB RAM, 0.15 CPU) can't reliably hold Chromium plus
the Zoho People app, so app.py keeps the exact-time schedule and hands each run
to the `attendance.yml` workflow on a GitHub-hosted runner. GitHub's run
history doubles as the durable record the startup catch-up uses to tell
whether a scheduled run already happened.

Env vars:
  GH_DISPATCH_TOKEN     fine-grained PAT for this repo, Actions: read & write
  GH_DISPATCH_REPO      owner/name (default: Render's RENDER_GIT_REPO_SLUG)
  GH_DISPATCH_WORKFLOW  workflow file name (default: attendance.yml)
  GH_DISPATCH_REF       branch to run (default: RENDER_GIT_BRANCH, else main)
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping

import requests

log = logging.getLogger(__name__)

API = "https://api.github.com"
API_VERSION = "2022-11-28"

# Must match `run-name` in .github/workflows/attendance.yml.
_RUN_NAME_RE = re.compile(r"^attendance: (checkin|checkout)\b")
_UNFINISHED = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})


class DispatchError(RuntimeError):
    """GitHub rejected or failed an Actions API call."""


@dataclass(frozen=True)
class DispatchConfig:
    token: str = field(repr=False)
    repo: str
    workflow: str = "attendance.yml"
    ref: str = "main"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> DispatchConfig | None:
        """None when GH_DISPATCH_TOKEN is unset — the browser then runs in-process."""

        def get(key: str) -> str:
            return env.get(key, "").strip()

        token = get("GH_DISPATCH_TOKEN")
        if not token:
            return None
        repo = get("GH_DISPATCH_REPO") or get("RENDER_GIT_REPO_SLUG")
        if not repo:
            raise ValueError(
                "GH_DISPATCH_TOKEN is set but the repo is unknown: set GH_DISPATCH_REPO=owner/name"
            )
        return cls(
            token=token,
            repo=repo,
            workflow=get("GH_DISPATCH_WORKFLOW") or "attendance.yml",
            ref=get("GH_DISPATCH_REF") or get("RENDER_GIT_BRANCH") or "main",
        )


@dataclass(frozen=True)
class DispatchedRun:
    id: int
    html_url: str


def _headers(cfg: DispatchConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _error(resp) -> DispatchError:
    try:
        message = resp.json().get("message", "")
    except ValueError:
        message = resp.text[:200]
    return DispatchError(f"GitHub API returned HTTP {resp.status_code}: {message}")


def dispatch(
    cfg: DispatchConfig,
    action: str,
    *,
    force: bool = False,
    source: str = "scheduler",
    retries: int = 3,
    backoff_seconds: float = 10,
) -> DispatchedRun:
    """Start the workflow for `action` and return the run GitHub created.

    Network errors, 5xx and 429 are retried. If a request was accepted but its
    reply got lost, the retry only queues a second run behind the first: the
    workflow's concurrency group serialises them, and the second finds the
    widget already flipped and no-ops.
    """
    url = f"{API}/repos/{cfg.repo}/actions/workflows/{cfg.workflow}/dispatches"
    body = {
        "ref": cfg.ref,
        "inputs": {"action": action, "force": "true" if force else "false", "source": source},
        "return_run_details": True,
    }
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=body, headers=_headers(cfg), timeout=30)
        except requests.RequestException as exc:
            last_error = exc
        else:
            if resp.status_code == 200:
                data = resp.json()
                return DispatchedRun(id=data["workflow_run_id"], html_url=data["html_url"])
            last_error = _error(resp)
            if resp.status_code != 429 and resp.status_code < 500:
                raise last_error
        if attempt < retries:
            delay = backoff_seconds * 2 ** (attempt - 1)
            log.warning(
                "GitHub dispatch attempt %d/%d failed (%s); retrying in %ss",
                attempt, retries, last_error, delay,
            )
            time.sleep(delay)
    raise DispatchError(f"GitHub dispatch failed after {retries} attempts: {last_error}")


def list_runs_since(cfg: DispatchConfig, since: datetime) -> list[dict]:
    """Workflow runs dispatched at or after `since`, newest first."""
    resp = requests.get(
        f"{API}/repos/{cfg.repo}/actions/workflows/{cfg.workflow}/runs",
        params={
            "event": "workflow_dispatch",
            "created": ">=" + since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "per_page": 100,
        },
        headers=_headers(cfg),
        timeout=30,
    )
    if resp.status_code != 200:
        raise _error(resp)
    return resp.json()["workflow_runs"]


def run_action(run: Mapping) -> str | None:
    """The attendance action a run was dispatched for, read from its run-name."""
    match = _RUN_NAME_RE.match(run.get("display_title") or "")
    return match.group(1) if match else None


def handled_run(runs: Iterable[Mapping], action: str) -> Mapping | None:
    """First run for `action` that is still going or already succeeded."""
    for run in runs:
        if run_action(run) != action:
            continue
        if run.get("status") in _UNFINISHED or run.get("conclusion") == "success":
            return run
    return None


def _get_run(cfg: DispatchConfig, run_id: int) -> dict:
    resp = requests.get(
        f"{API}/repos/{cfg.repo}/actions/runs/{run_id}", headers=_headers(cfg), timeout=30,
    )
    if resp.status_code != 200:
        raise _error(resp)
    return resp.json()


def wait_for_completion(
    cfg: DispatchConfig,
    run_id: int,
    *,
    poll_seconds: float = 30,
    timeout_seconds: float = 30 * 60,
) -> dict:
    """Poll a run until it completes; on timeout return the last state seen."""
    deadline = time.monotonic() + timeout_seconds
    last: dict = {"id": run_id, "status": "unknown", "conclusion": None, "html_url": None}
    while True:
        try:
            run = _get_run(cfg, run_id)
        except (requests.RequestException, DispatchError) as exc:
            log.warning("Couldn't read GitHub run %s: %s", run_id, exc)
        else:
            if run.get("status") != last["status"]:
                log.info("GitHub run %s: %s", run_id, run.get("status"))
            last = run
            if run.get("status") == "completed":
                return run
        if time.monotonic() >= deadline:
            return last
        time.sleep(poll_seconds)
