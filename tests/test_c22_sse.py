import json
import tempfile
import shutil
import threading
import time
import pytest
import database as db_module
from app import create_app
from routes.projects import stream_job_events


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _setup_project(tmp_db):
    """Return (pid, event, tmp_dir) with a registered SDLC project + queued jobs."""
    app = _app(tmp_db)
    tmp_dir = tempfile.mkdtemp()
    with app.app_context():
        pid = db_module.create_folder("SDLC")["id"]
        db_module.register_project_from_template(pid, "sdlc", tmp_dir)
        for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            db_module.create_artifact_trace(pid, key, "REQ-014")
        event = db_module.create_change_event(
            project_id=pid, source_key="BA-REQ",
            from_version=1, to_version=2, changed_reqs=["REQ-014"],
        )
        from propagation.impact import queue_propagation_jobs
        queue_propagation_jobs(pid, event["id"], ["REQ-014"])
    return pid, event, tmp_dir


def _parse(iterator):
    """Parse SSE lines from the generator for a bounded number of events."""
    out = []
    for chunk in iterator:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        if len(out) >= 8:
            break
    return out


class TestJobStream:
    def test_stream_emits_snapshot(self, tmp_db):
        pid, event, tmp_dir = _setup_project(tmp_db)
        try:
            app = _app(tmp_db)
            with app.app_context():
                events = _parse(stream_job_events(pid))
            types = [e["type"] for e in events]
            assert "snapshot" in types
            snap = next(e for e in events if e["type"] == "snapshot")
            assert len(snap["jobs"]) == 5
            assert "ping" in types
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_stream_reports_job_updates_and_done(self, tmp_db):
        """Live progress is observed during a wave (C22 gate)."""
        pid, event, tmp_dir = _setup_project(tmp_db)
        try:
            app = _app(tmp_db)
            collected = []

            def consume():
                with app.app_context():
                    for ev in _parse(stream_job_events(pid)):
                        collected.append(ev)

            thread = threading.Thread(target=consume)
            thread.daemon = True
            thread.start()

            time.sleep(0.5)
            # Drive all jobs to a terminal state (simulates a wave completing).
            with app.app_context():
                for job in db_module.list_propagation_jobs(event["id"]):
                    db_module.update_propagation_job(job["id"], state="proposed")

            time.sleep(1.0)
            thread.join(timeout=2)

            types = [e["type"] for e in collected]
            assert "job_update" in types, f"no job_update; saw {types}"
            assert any(
                e["type"] == "job_update" and e["jobs"] and e["jobs"][0]["state"] == "proposed"
                for e in collected
            ), collected
            assert "all_done" in types
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_stream_404(self, tmp_db):
        app = _app(tmp_db)
        with app.test_client() as client:
            resp = client.get("/api/projects/9999/jobs/stream")
            assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])