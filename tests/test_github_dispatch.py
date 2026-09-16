from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

import github_dispatch as gd
from fakes import API, REPO, WORKFLOW, FakeResponse, make_run

IST = timezone(timedelta(hours=5, minutes=30))


# ── configuration ────────────────────────────────────────────────────────────

def test_from_env_is_off_without_a_token():
    assert gd.DispatchConfig.from_env({"RENDER_GIT_REPO_SLUG": REPO}) is None


def test_from_env_takes_repo_and_branch_from_render():
    cfg = gd.DispatchConfig.from_env({
        "GH_DISPATCH_TOKEN": " ghp_abc ",
        "RENDER_GIT_REPO_SLUG": REPO,
        "RENDER_GIT_BRANCH": "prod",
    })
    assert cfg == gd.DispatchConfig(token="ghp_abc", repo=REPO, workflow="attendance.yml", ref="prod")


def test_from_env_defaults_to_main_and_attendance_workflow():
    cfg = gd.DispatchConfig.from_env({"GH_DISPATCH_TOKEN": "ghp_abc", "GH_DISPATCH_REPO": REPO})
    assert (cfg.workflow, cfg.ref) == ("attendance.yml", "main")


def test_from_env_explicit_settings_beat_render_defaults():
    cfg = gd.DispatchConfig.from_env({
        "GH_DISPATCH_TOKEN": "ghp_abc",
        "GH_DISPATCH_REPO": "someone/fork",
        "GH_DISPATCH_WORKFLOW": "other.yml",
        "GH_DISPATCH_REF": "release",
        "RENDER_GIT_REPO_SLUG": REPO,
        "RENDER_GIT_BRANCH": "prod",
    })
    assert (cfg.repo, cfg.workflow, cfg.ref) == ("someone/fork", "other.yml", "release")


def test_from_env_rejects_a_token_without_a_repo():
    with pytest.raises(ValueError, match="GH_DISPATCH_REPO"):
        gd.DispatchConfig.from_env({"GH_DISPATCH_TOKEN": "ghp_abc"})


def test_config_repr_does_not_leak_the_token():
    assert "ghp_secret_value" not in repr(gd.DispatchConfig(token="ghp_secret_value", repo=REPO))


# ── dispatch ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("force, sent", [(False, "false"), (True, "true")])
def test_dispatch_triggers_the_workflow_and_returns_the_run(github, cfg, force, sent):
    run = gd.dispatch(cfg, "checkin", force=force, source="scheduler")

    method, url, kwargs = github.requests[-1]
    assert (method, url) == ("POST", f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches")
    assert kwargs["headers"]["Authorization"] == "Bearer ghp_test_token"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert kwargs["json"] == {
        "ref": "main",
        "inputs": {"action": "checkin", "force": sent, "source": "scheduler"},
        "return_run_details": True,
    }
    assert run == gd.DispatchedRun(id=9001, html_url=f"https://github.com/{REPO}/actions/runs/9001")


@pytest.mark.parametrize("blip", [
    requests.ConnectionError("connection reset"),
    requests.Timeout("read timed out"),
    FakeResponse(502, {"message": "Bad Gateway"}),
    FakeResponse(429, {"message": "Too Many Requests"}),
])
def test_dispatch_retries_transient_failures(github, cfg, blip):
    github.dispatch_replies = [blip]

    run = gd.dispatch(cfg, "checkout", source="scheduler", backoff_seconds=0)

    assert run.id == 9001
    assert len(github.dispatches) == 2


def test_dispatch_fails_fast_on_client_errors(github, cfg):
    github.dispatch_replies = [
        FakeResponse(422, {"message": "Workflow does not have 'workflow_dispatch' trigger"}),
    ]

    with pytest.raises(gd.DispatchError, match=r"422.*workflow_dispatch"):
        gd.dispatch(cfg, "checkin", source="scheduler", backoff_seconds=0)
    assert len(github.dispatches) == 1


def test_dispatch_gives_up_after_its_retries(github, cfg):
    github.dispatch_replies = [FakeResponse(503, {"message": "Service Unavailable"})] * 3

    with pytest.raises(gd.DispatchError, match="503"):
        gd.dispatch(cfg, "checkin", source="scheduler", retries=3, backoff_seconds=0)
    assert len(github.dispatches) == 3


# ── run history ──────────────────────────────────────────────────────────────

def test_list_runs_since_asks_for_dispatch_runs_created_after_a_time(github, cfg):
    github.history = [
        make_run(1, "checkin", created_at="2026-09-16T06:29:59Z"),
        make_run(2, "checkin", created_at="2026-09-16T06:30:05Z"),
    ]

    runs = gd.list_runs_since(cfg, datetime(2026, 9, 16, 12, 0, tzinfo=IST))

    method, url, kwargs = github.requests[-1]
    assert (method, url) == ("GET", f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs")
    assert kwargs["params"] == {
        "event": "workflow_dispatch",
        "created": ">=2026-09-16T06:30:00Z",
        "per_page": 100,
    }
    assert [r["id"] for r in runs] == [2]


def test_list_runs_since_raises_on_api_errors(github, cfg):
    github.list_replies = [FakeResponse(401, {"message": "Bad credentials"})]

    with pytest.raises(gd.DispatchError, match="401.*Bad credentials"):
        gd.list_runs_since(cfg, datetime(2026, 9, 16, 12, 0, tzinfo=IST))


@pytest.mark.parametrize("title, action", [
    ("attendance: checkin (scheduler)", "checkin"),
    ("attendance: checkout (catchup)", "checkout"),
    ("Zoho People Attendance (Browser)", None),  # runs from before run-name existed
])
def test_run_action_reads_the_run_name(title, action):
    assert gd.run_action({"display_title": title}) == action


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_handled_run_counts_unfinished_runs(status):
    run = make_run(1, "checkin", status=status)
    assert gd.handled_run([run], "checkin") is run


def test_handled_run_counts_successful_runs_for_that_action_only():
    checkout = make_run(1, "checkout")
    assert gd.handled_run([checkout], "checkout") is checkout
    assert gd.handled_run([checkout], "checkin") is None


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "startup_failure"])
def test_handled_run_ignores_runs_that_did_not_succeed(conclusion):
    assert gd.handled_run([make_run(1, "checkin", conclusion=conclusion)], "checkin") is None


@pytest.mark.parametrize("status", ["completed", "in_progress"])
def test_handled_run_ignores_dry_runs(status):
    # A dry run reads the widget but never punches, so it doesn't count.
    dry = make_run(1, "checkin", source="manual", status=status, dry_run=True)
    assert gd.handled_run([dry], "checkin") is None


# ── watching a run ───────────────────────────────────────────────────────────

def test_wait_for_completion_polls_until_the_run_finishes(github, cfg):
    github.run_states[77] = [("queued", None), ("in_progress", None), ("completed", "failure")]

    final = gd.wait_for_completion(cfg, 77, poll_seconds=0)

    assert (final["status"], final["conclusion"]) == ("completed", "failure")
    assert [url for _, url, _ in github.requests] == [f"{API}/repos/{REPO}/actions/runs/77"] * 3


def test_wait_for_completion_returns_the_last_state_seen_on_timeout(github, cfg):
    github.run_states[77] = [("in_progress", None)]

    final = gd.wait_for_completion(cfg, 77, poll_seconds=0, timeout_seconds=0)

    assert final["status"] == "in_progress"


@pytest.mark.parametrize("blip", [
    requests.ConnectionError("connection reset"),
    FakeResponse(500, {"message": "Server Error"}),
])
def test_wait_for_completion_rides_out_transient_poll_errors(github, cfg, blip):
    github.run_states[77] = [blip, ("completed", "success")]

    final = gd.wait_for_completion(cfg, 77, poll_seconds=0)

    assert final["conclusion"] == "success"


def test_wait_for_completion_reports_unknown_if_the_run_was_never_readable(github, cfg):
    github.run_states[77] = [requests.ConnectionError("down")]

    final = gd.wait_for_completion(cfg, 77, poll_seconds=0, timeout_seconds=0)

    assert final == {"id": 77, "status": "unknown", "conclusion": None, "html_url": None}
