import json
import os
import tempfile
import pytest
import database as db_module
from app import create_app
from tests.fakes import FakeOpenAIClient
from propagation.agent import run_propagation_wave
from propagation.proposal import write_proposals, apply_all
from propagation.impact import queue_propagation_jobs
from propagation.loop_guard import (
    validate_wave_depth,
    enforce_token_budget,
    kill_switch_enabled,
    set_kill_switch,
    cancel_remaining_jobs,
    count_new_change_events,
    account_tokens,
)


def _make_app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _rewrite(key, body):
    return f"# {key}\n\n## placeholder\n\n{body}\n"


def _setup_wave(app, tmp_db, tmp_dir, max_depth_ok=True):
    folder = db_module.create_folder("P")
    pid = folder["id"]
    db_module.register_project_from_template(pid, "sdlc", tmp_dir)
    for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
        db_module.create_artifact_trace(pid, key, "REQ-014")
    event = db_module.create_change_event(
        project_id=pid, source_key="BA-REQ",
        from_version=1, to_version=2, changed_reqs=["REQ-014"],
    )
    queue_propagation_jobs(pid, event["id"], ["REQ-014"])
    return pid, event


def _scripted_client():
    client = FakeOpenAIClient()
    for key in ["DB-MODEL", "SEC-RISK", "UX-WIRE", "QA-PLAN", "PM-PLAN"]:
        client.add_tool_calls_response([{
            "name": "write_artifact",
            "arguments": json.dumps({
                "artifact_key": key,
                "content": _rewrite(key, f"## S\n{key} new body."),
            }),
        }])
        client.add_text_response("Done.")
    return client


class TestLoopPrevention:
    """Regression: a propagation run produces zero new change events (D6)."""

    def test_wave_produces_zero_change_events(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                before_count = len(db_module.list_change_events(pid))
                assert before_count == 1

                payloads = run_propagation_wave(app, pid, event["id"], _scripted_client())
                write_proposals(pid, event["id"], payloads)
                apply_all(pid, event["id"])

                # A scan after the wave must not create any new change events.
                from propagation.notify import run_notify_scan
                report = run_notify_scan(pid)
                new_events = count_new_change_events(pid, before_count)
                assert new_events == 0
                assert report.change_events == []

    def test_applied_artifacts_marked_propagation_origin(self, tmp_db):
        remote = _make_app(tmp_db)
        with remote.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(remote, tmp_db, tmp_dir)
                payloads = run_propagation_wave(remote, pid, event["id"], _scripted_client())
                write_proposals(pid, event["id"], payloads)
                apply_all(pid, event["id"])

                db_model = db_module.get_artifact(pid, "DB-MODEL")
                assert db_model["origin"] == "propagation"


class TestMaxDepth:
    """Tests for the max_depth cap (3.4)."""

    def test_depth_cap_cancels_deep_jobs(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                # Add an artificially deep job.
                db_module.create_propagation_job(event["id"], "PM-DEEP", 1, depth=11)

                cancelled = validate_wave_depth(pid, event["id"], max_depth=10)
                assert "PM-DEEP" in cancelled

                jobs = db_module.list_propagation_jobs(event["id"])
                deep = next(j for j in jobs if j["artifact_key"] == "PM-DEEP")
                assert deep["state"] == "cancelled"


class TestTokenBudget:
    """Tests for the token budget enforcement (D14)."""

    def test_low_budget_cancels_remaining_jobs(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)

                cancelled = enforce_token_budget(event["id"], budget=1, tokens_used=5000)
                assert len(cancelled) >= 1

                jobs = db_module.list_propagation_jobs(event["id"])
                cancelled_jobs = [j for j in jobs if j["state"] == "cancelled"]
                assert len(cancelled_jobs) == len(jobs)
                assert any("Token budget" in (j.get("error") or "") for j in cancelled_jobs)

    def test_unlimited_budget_not_enforced(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                cancelled = enforce_token_budget(event["id"], budget=0, tokens_used=10**9)
                assert cancelled == []

    def test_account_tokens_accumulates(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                job = db_module.list_propagation_jobs(event["id"])[0]
                account_tokens(job["id"], 100)
                total = db_module.update_propagation_job(job["id"])["tokens_used"]
                assert total == 100
                account_tokens(job["id"], 50)
                total = db_module.update_propagation_job(job["id"])["tokens_used"]
                assert total == 150


class TestKillSwitch:
    """Tests for the kill switch (D14)."""

    def test_set_and_check(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            assert kill_switch_enabled() is False
            set_kill_switch(True)
            assert kill_switch_enabled() is True
            set_kill_switch(False)
            assert kill_switch_enabled() is False

    def test_cancel_remaining_on_kill(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                cancelled = cancel_remaining_jobs(event["id"], "Kill switch pulled")
                assert len(cancelled) == 5
                jobs = db_module.list_propagation_jobs(event["id"])
                assert all(j["state"] == "cancelled" for j in jobs)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])