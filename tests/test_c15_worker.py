import tempfile
import threading
import time
import pytest
import database as db_module
from app import create_app
from propagation.worker import (
    PropagationWorker,
    NoopExecutor,
    should_start_worker,
    recover_stuck_running,
    _claim_job,
    _depth_gated,
    run_all_pending,
)


def _make_app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _register_sdlc(app, project_id, tmp_dir):
    with app.app_context():
        db_module.register_project_from_template(project_id, "sdlc", tmp_dir)


class TestStartupGuard:
    """Tests for the reload-safe startup guard helper."""

    def test_should_start_in_test_env(self):
        """When WERKZEUG_RUN_MAIN is unset, the worker may start."""
        assert should_start_worker() is True

    def test_should_start_when_main(self, monkeypatch):
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
        assert should_start_worker() is True

    def test_should_not_start_in_reloader(self, monkeypatch):
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "false")
        assert should_start_worker() is False


class TestClaim:
    """Tests for guarded job claiming."""

    def test_claim_pending_job(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)

            claimed = _claim_job()
            assert claimed is not None
            assert claimed["state"] == "running"
            # A second claim returns nothing (job already running).
            assert _claim_job() is None

    def test_claim_order(self, tmp_db):
        """Claims are ordered by (change_id, depth, artifact_key)."""
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "QA-PLAN", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)

            first = _claim_job()
            assert first["artifact_key"] == "DB-MODEL"
            second = _claim_job()
            assert second["artifact_key"] == "QA-PLAN"


class TestDepthGate:
    """Tests for depth gating (D4)."""

    def test_depth_gate_blocks_when_upstream_pending(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)
            db_module.create_propagation_job(event["id"], "QA-PLAN", 1, 2)

            assert _depth_gated(pid, event["id"], 2) is False

    def test_depth_gate_allows_when_upstream_terminal(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)
            db_module.create_propagation_job(event["id"], "QA-PLAN", 1, 2)

            # Mark depth-1 terminal.
            db_module.update_propagation_job(
                [j for j in db_module.list_propagation_jobs(event["id"]) if j["artifact_key"] == "DB-MODEL"][0]["id"],
                state="applied",
            )
            assert _depth_gated(pid, event["id"], 2) is True


class FailingExecutor:
    def execute(self, job):
        raise ValueError("boom")


class TestWorkerDrive:
    """Tests driving the worker through a no-op executor."""

    def test_no_op_executor_completes_jobs(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)
            db_module.create_propagation_job(event["id"], "QA-PLAN", 1, 2)

            final = run_all_pending(app, executor=NoopExecutor())
            assert {j["artifact_key"]: j["state"] for j in final} == {
                "DB-MODEL": "completed",
                "QA-PLAN": "completed",
            }

    def test_failure_isolation(self, tmp_db):
        """A failing job becomes 'failed' and siblings can still run."""
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)
            db_module.create_propagation_job(event["id"], "UX-WIRE", 1, 1)

            final = run_all_pending(app, executor=FailingExecutor())
            states = {j["artifact_key"]: j["state"] for j in final}
            assert states["DB-MODEL"] == "failed"
            assert states["UX-WIRE"] == "failed"

    def test_no_job_stuck_running_after_crash(self, tmp_db):
        """recover_stuck_running resets any 'running' jobs to pending."""
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            job = db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)
            db_module.update_propagation_job(job["id"], state="running")

            recovered = recover_stuck_running()
            assert recovered == 1
            jobs = db_module.list_propagation_jobs(event["id"])
            assert jobs[0]["state"] == "pending"


class TestBackgroundWorker:
    """Tests for the real background thread worker."""

    def test_start_stop_graceful(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("P")
            pid = folder["id"]
            event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
            db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)

        worker = PropagationWorker(app, executor=NoopExecutor())
        worker.start()
        time.sleep(0.2)
        worker.stop()
        assert not worker.is_running

        with app.app_context():
            jobs = db_module.list_propagation_jobs(event["id"])
            assert jobs[0]["state"] == "completed"

    def test_shutdown_flag_stops_loop(self, tmp_db):
        app = _make_app(tmp_db)
        worker = PropagationWorker(app, executor=NoopExecutor())
        worker.start()
        assert worker.is_running
        worker.stop()
        time.sleep(0.1)
        assert not worker.is_running


if __name__ == "__main__":
    pytest.main([__file__, "-v"])