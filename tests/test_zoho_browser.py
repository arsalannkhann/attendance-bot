from __future__ import annotations

import pytest

import zoho_browser as zb


@pytest.fixture
def runs(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_run_attendance(action, *, jitter_seconds, skip_weekend_check, dry_run):
        calls.append({
            "action": action,
            "jitter_seconds": jitter_seconds,
            "skip_weekend_check": skip_weekend_check,
            "dry_run": dry_run,
        })
        return "ok"

    monkeypatch.setattr(zb, "run_attendance", fake_run_attendance)
    for name in ("ACTION", "FORCE", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    return calls


# ── CLI flags set by the GitHub workflow ─────────────────────────────────────

def test_cli_passes_workflow_inputs_through(monkeypatch, runs):
    monkeypatch.setenv("ACTION", "checkout")
    monkeypatch.setenv("FORCE", "true")
    monkeypatch.setenv("DRY_RUN", "true")

    zb._cli()

    assert runs == [{
        "action": "checkout", "jitter_seconds": 0, "skip_weekend_check": True, "dry_run": True,
    }]


@pytest.mark.parametrize("value", [None, "", "false", "0"])
def test_cli_leaves_force_and_dry_run_off_by_default(monkeypatch, runs, value):
    monkeypatch.setenv("ACTION", "checkin")
    if value is not None:
        monkeypatch.setenv("FORCE", value)
        monkeypatch.setenv("DRY_RUN", value)

    zb._cli()

    assert runs == [{
        "action": "checkin", "jitter_seconds": 0, "skip_weekend_check": False, "dry_run": False,
    }]


def test_dry_run_reaches_the_browser_attempt(monkeypatch):
    attempts = []

    def fake_do_attendance(cfg, action, *, dry_run):
        attempts.append((action, dry_run))
        return "dry-run"

    monkeypatch.setattr(zb, "_do_attendance", fake_do_attendance)
    monkeypatch.setenv("ZOHO_EMAIL", "me@example.com")
    monkeypatch.setenv("ZOHO_PASSWORD", "not-a-real-password")

    assert zb.run_attendance("checkin", jitter_seconds=0, skip_weekend_check=True, dry_run=True) == "dry-run"
    assert attempts == [("checkin", True)]


# ── what the widget label means ──────────────────────────────────────────────

@pytest.mark.parametrize("action, label, decision", [
    ("checkin", "check-in", (True, "checked-in")),
    ("checkin", "check-out", (False, "already-checked-in")),
    ("checkout", "check-out", (True, "checked-out")),
    ("checkout", "check-in", (False, "already-checked-out")),
])
def test_widget_label_decides_whether_to_click(action, label, decision):
    assert zb._decide(action, label, dry_run=False) == decision


def test_dry_run_never_clicks():
    should_click, result = zb._decide("checkin", "check-in", dry_run=True)

    assert should_click is False
    assert result == "dry-run: would click 'check-in' (checked-in)"


def test_dry_run_still_reports_work_already_done():
    assert zb._decide("checkout", "check-in", dry_run=True) == (False, "already-checked-out")
