"""attendance.yml is half of a contract with github_dispatch.py: Render sends
its inputs, and the startup catch-up reads its run-name."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import github_dispatch as gd

WORKFLOW = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "attendance.yml").read_text()
)
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))  # PyYAML reads a bare `on:` key as True
STEPS = WORKFLOW["jobs"]["attendance"]["steps"]


def _script_step() -> dict:
    return next(s for s in STEPS if s.get("run", "").strip() == "python zoho_browser.py")


def test_only_a_dispatch_starts_the_workflow():
    assert list(TRIGGERS) == ["workflow_dispatch"]  # no cron: Render keeps time


def test_every_input_dispatch_sends_is_declared(github, cfg):
    gd.dispatch(cfg, "checkin", source="scheduler")

    assert set(github.dispatches[0]["inputs"]) <= set(TRIGGERS["workflow_dispatch"]["inputs"])


def test_inputs_are_typed_for_render_and_manual_runs():
    inputs = TRIGGERS["workflow_dispatch"]["inputs"]
    assert (inputs["action"]["type"], inputs["action"]["options"]) == ("choice", ["checkin", "checkout"])
    assert inputs["force"]["type"] == "boolean"
    assert inputs["dry_run"]["type"] == "boolean"
    assert inputs["source"]["type"] == "string"


def _run_name(*, action: str, source: str, dry_run: bool) -> str:
    return (WORKFLOW["run-name"]
            .replace("${{ inputs.action }}", action)
            .replace("${{ inputs.source }}", source)
            .replace("${{ inputs.dry_run && ' dry-run' || '' }}", " dry-run" if dry_run else ""))


@pytest.mark.parametrize("dry_run, counts_as_handled", [(False, True), (True, False)])
def test_run_name_lets_catch_up_tell_real_runs_from_dry_runs(dry_run, counts_as_handled):
    run = {
        "display_title": _run_name(action="checkout", source="catchup", dry_run=dry_run),
        "status": "completed",
        "conclusion": "success",
    }

    assert gd.run_action(run) == "checkout"
    assert (gd.handled_run([run], "checkout") is run) == counts_as_handled


def test_attendance_runs_one_at_a_time():
    assert WORKFLOW["concurrency"]["group"]
    assert WORKFLOW["concurrency"]["cancel-in-progress"] is False


def test_script_gets_the_inputs_as_env():
    env = _script_step()["env"]
    assert env["ACTION"] == "${{ inputs.action }}"
    assert env["FORCE"] == "${{ inputs.force }}"
    assert env["DRY_RUN"] == "${{ inputs.dry_run }}"


def test_failure_screenshots_are_uploaded_from_the_debug_dir():
    upload = next(s for s in STEPS if s.get("uses", "").startswith("actions/upload-artifact"))

    assert upload["if"] == "failure()"
    assert upload["with"]["path"] == _script_step()["env"]["DEBUG_DIR"]
