import json
import os
import tempfile
import shutil
import pytest
import database as db_module
from app import create_app
from tests.fakes import FakeOpenAIClient
from propagation.agent import run_propagation_wave
from propagation.proposal import write_proposals, apply_all
from propagation.impact import queue_propagation_jobs
from propagation.loop_guard import (
    estimate_wave_tokens,
    require_auto_confirmation,
)


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _rewrite(key, body):
    return f"# {key}\n\n## placeholder\n\n{body}\n"


def _setup_wave(tmp_db, tmp_dir):
    app = _app(tmp_db)
    with app.app_context():
        pid = db_module.create_folder("P")["id"]
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


class TestAutoModeConfirmation:
    """C31: enabling auto mode requires explicit, non-bypassable confirmation."""

    def test_require_auto_confirmation_pure(self):
        require_auto_confirmation("auto", True)  # ok
        with pytest.raises(ValueError):
            require_auto_confirmation("auto", False)
        # Non-auto modes never need confirmation.
        require_auto_confirmation("propose", False)

    def test_settings_rejects_auto_without_confirmation(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as td:
                pid, _event = _setup_wave(tmp_db, td)
        with app.test_client() as client:
            resp = client.put(f"/api/projects/{pid}/settings", json={"propagation_mode": "auto"})
            assert resp.status_code == 400
            assert "confirm_auto" in resp.get_json()["error"]

    def test_settings_allows_auto_with_confirmation(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as td:
                pid, _event = _setup_wave(tmp_db, td)
        with app.test_client() as client:
            resp = client.put(
                f"/api/projects/{pid}/settings",
                json={"propagation_mode": "auto", "confirm_auto": True},
            )
            assert resp.status_code == 200
            assert resp.get_json()["propagation_mode"] == "auto"


class TestTokenEstimate:
    """C31: the pre-wave token estimate is shown before a wave runs."""

    def test_estimate_function(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as td:
                pid, event = _setup_wave(tmp_db, td)
                estimate = estimate_wave_tokens(pid, event["id"])
                assert estimate >= 1

    def test_estimate_route(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as td:
                pid, event = _setup_wave(tmp_db, td)
        with app.test_client() as client:
            resp = client.get(f"/api/projects/{pid}/changes/{event['id']}/estimate-tokens")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["estimated_tokens"] >= 1
            assert "token_budget" in data
            assert data["under_budget"] is True  # default budget 0 = unlimited

    def test_estimate_route_404(self, tmp_db):
        app = _app(tmp_db)
        with app.test_client() as client:
            resp = client.get("/api/projects/9999/changes/1/estimate-tokens")
            assert resp.status_code == 404


class TestAutoLoopPrevention:
    """C31: auto-mode apply flow still produces zero new change events (D6)."""

    def test_auto_wave_zero_new_events(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as td:
                pid, event = _setup_wave(tmp_db, td)
                before_count = len(db_module.list_change_events(pid))
                assert before_count == 1

                payloads = run_propagation_wave(app, pid, event["id"], _scripted_client())
                write_proposals(pid, event["id"], payloads)
                apply_all(pid, event["id"])

                from propagation.notify import run_notify_scan
                report = run_notify_scan(pid)
                assert report.change_events == []
                assert len(db_module.list_change_events(pid)) == before_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])