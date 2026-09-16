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


# ── today's attendance record ────────────────────────────────────────────────

def test_record_reads_a_completed_day():
    # aria-labels of Wed 16's row on the Attendance page, as served 2026-09-17
    labels = ["Wed 16", "Remote In Check-in Time: 01:04 PM", "Present",
              "Check-out Time :  04:02 AM", "hours worked: 14:58"]

    record = zb._parse_record(labels)

    assert record == zb.AttendanceRecord(check_in="01:04 PM", check_out="04:02 AM")
    assert record.checked_in is False


def test_record_reads_an_open_check_in():
    record = zb._parse_record(["Today 17", "Remote In Check-in Time: 12:00 PM", "Present",
                               "hours worked: 00:00"])

    assert record == zb.AttendanceRecord(check_in="12:00 PM", check_out=None)
    assert record.checked_in is True


def test_record_reads_a_day_with_no_entries():
    record = zb._parse_record(["Today 17", "", "hours worked: 00:00"])

    assert record == zb.AttendanceRecord(check_in=None, check_out=None)
    assert record.checked_in is False


# ── deciding from the button and the record ──────────────────────────────────

NOT_IN = zb.AttendanceRecord(check_in=None, check_out=None)
IN = zb.AttendanceRecord(check_in="12:00 PM", check_out=None)
OUT = zb.AttendanceRecord(check_in="12:00 PM", check_out="10:00 PM")


@pytest.mark.parametrize("action, label, record, decision", [
    ("checkin", "check-in", NOT_IN, (True, "checked-in")),
    ("checkin", "check-out", IN, (False, "already-checked-in")),
    ("checkout", "check-out", IN, (True, "checked-out")),
    ("checkout", "check-in", OUT, (False, "already-checked-out")),
    ("checkout", "check-in", NOT_IN, (False, "already-checked-out")),
])
def test_button_and_record_that_agree_decide_the_punch(action, label, record, decision):
    assert zb._decide(action, label, record) == decision


@pytest.mark.parametrize("action, label, record", [
    ("checkout", "check-in", IN),  # 2026-09-16 22:01: checked in, button said Check-in
    ("checkin", "check-in", IN),
    ("checkin", "check-out", NOT_IN),
    ("checkout", "check-out", OUT),
])
def test_button_contradicting_the_record_stops_the_run(action, label, record):
    with pytest.raises(zb.AttendanceMismatch):
        zb._decide(action, label, record)


# ── confirming a punch landed ────────────────────────────────────────────────

def test_confirm_punch_rereads_until_the_record_shows_it(monkeypatch):
    monkeypatch.setattr(zb, "_CONFIRM_WAIT_SECONDS", 0)
    reads = iter([IN, IN, OUT])

    assert zb._confirm_punch(lambda: next(reads), "checkout") == OUT


def test_confirm_punch_gives_up_loudly(monkeypatch):
    monkeypatch.setattr(zb, "_CONFIRM_WAIT_SECONDS", 0)
    monkeypatch.setattr(zb, "_CONFIRM_ATTEMPTS", 3)
    reads = []

    def read():
        reads.append(IN)
        return IN

    with pytest.raises(zb.AttendanceError, match="still shows"):
        zb._confirm_punch(read, "checkout")
    assert len(reads) == 3
