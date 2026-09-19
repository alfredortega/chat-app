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


class TestRunPropagationRoute:
    """The 'Run propagation' button end-to-end: queues jobs, runs the agent
    wave through a scripted client, and applies validated artifact updates."""

    def _scripted_client(self):
        from tests.fakes import FakeOpenAIClient

        client = FakeOpenAIClient()
        for key in ["BA-REQ", "DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            client.add_tool_calls_response([{
                "name": "write_artifact",
                "arguments": json.dumps({
                    "artifact_key": key,
                    "content": f"# {key}\n\n## placeholder\n\n## S\n{key} new body.\n",
                }),
            }])
            client.add_text_response("Done.")
        return client

    def test_run_propagation_checks_all_roles_and_applies_updates(self, tmp_db, monkeypatch):
        app = _app(tmp_db)
        with app.app_context():
            db_module.set_setting("allow_local_file_access", "1")
            tmp_dir = tempfile.mkdtemp()
            folder = db_module.create_folder("SDLC")
            pid = folder["id"]
            db_module.register_project_from_template(pid, "sdlc", tmp_dir)
            for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
                db_module.create_artifact_trace(pid, key, "REQ-014")
            event = db_module.create_change_event(
                project_id=pid, source_key="BA-REQ",
                from_version=1, to_version=2, changed_reqs=["REQ-014"],
            )
            db_module.create_endpoint(
                "fake", "http://fase.openai.test/v1",
                api_key="k", default_model="m", is_default=True,
            )

        fake = self._scripted_client()
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        try:
            with app.test_client() as client:
                resp = client.post(
                    f"/api/projects/{pid}/changes/{event['id']}/run-propagation"
                )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body is not None
            assert body.get("error") is None, body
            # Route returns immediately; the wave runs in a background thread.
            assert body.get("started") is True, body

            # Poll until the background wave marks jobs terminal.
            import time
            deadline = time.time() + 15
            while time.time() < deadline:
                time.sleep(0.1)
                with app.app_context():
                    jobs = db_module.list_propagation_jobs(event["id"])
                if jobs and all(j["state"] in (
                        "applied", "proposed", "failed", "needs_input",
                        "cancelled", "completed", "rejected") for j in jobs):
                    break
            else:
                raise AssertionError("wave did not complete in time")

            with app.app_context():
                from propagation.proposal import list_proposals
                proposals = list_proposals(pid, event["id"])
                jobs = db_module.list_propagation_jobs(event["id"])
                artifacts = db_module.list_artifacts(pid)
            assert len(proposals) == 6, proposals
            assert {p["artifact_key"] for p in proposals} == {
                "BA-REQ", "DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN",
            }
            assert {job["state"] for job in jobs} == {"applied"}
            assert {artifact["status"] for artifact in artifacts} == {"current"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_run_propagation_blocks_when_locked(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            db_module.set_setting("allow_local_file_access", "0")
            tmp_dir = tempfile.mkdtemp()
            folder = db_module.create_folder("SDLC")
            pid = folder["id"]
            db_module.register_project_from_template(pid, "sdlc", tmp_dir)
            event = db_module.create_change_event(
                project_id=pid, source_key="BA-REQ",
                from_version=1, to_version=2, changed_reqs=["REQ-014"],
            )
        try:
            with app.test_client() as client:
                resp = client.post(
                    f"/api/projects/{pid}/changes/{event['id']}/run-propagation"
                )
            assert resp.status_code == 409
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_run_propagation_queues_jobs_until_the_wave_claims_them(self, tmp_db, monkeypatch):
        """Only the active artifact is running; queued siblings must not mask it."""
        app = _app(tmp_db)
        tmp_dir = tempfile.mkdtemp()

        class ThreadThatDoesNotRun:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

        monkeypatch.setattr("routes.projects.threading.Thread", ThreadThatDoesNotRun)
        try:
            with app.app_context():
                db_module.set_setting("allow_local_file_access", "1")
                pid = db_module.create_folder("SDLC")["id"]
                db_module.register_project_from_template(pid, "sdlc", tmp_dir)
                db_module.create_artifact_trace(pid, "DB-MODEL", "REQ-014")
                event = db_module.create_change_event(
                    project_id=pid, source_key="BA-REQ",
                    from_version=1, to_version=2, changed_reqs=["REQ-014"],
                )
                db_module.create_endpoint(
                    "fake", "http://fase.openai.test/v1",
                    api_key="k", default_model="m", is_default=True,
                )

            with app.test_client() as client:
                response = client.post(
                    f"/api/projects/{pid}/changes/{event['id']}/run-propagation"
                )
            assert response.status_code == 200

            with app.app_context():
                jobs = db_module.list_propagation_jobs(event["id"])
            assert jobs
            assert {job["state"] for job in jobs} == {"queued"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_background_bookkeeping_uses_payload_attributes(self, tmp_db, monkeypatch):
        """Regression: the wave's error bookkeeping crashed with
        "RewritePayload object is not subscriptable" because it used dict-style
        access on dataclasses. A rejected payload must surface its own error on
        the job instead of nuking everything with a crash traceback."""
        from propagation.agent import RewritePayload

        def fake_run_wave(app, project_id, change_id, client, model_id="", on_payload=None, artifact_keys=None):
            return [
                RewritePayload(
                    artifact_key="DB-MODEL", content="",
                    version=2, status="rejected",
                    errors=["Shrink guard: rewrite is a summary"],
                ),
                RewritePayload(
                    artifact_key="UX-WIRE", content="# UX-WIRE\nbody",
                    version=2, status="ok", errors=[],
                ),
            ]

        def fake_write_proposals(project_id, change_id, payloads):
            return [{"artifact_key": p.artifact_key} for p in payloads]

        monkeypatch.setattr("app.get_client", lambda endpoint: object())
        # _run_wave_background imports these inside its task() via
        # `from propagation.agent import run_propagation_wave` / `from
        # propagation.proposal import write_proposals`, so we patch the source
        # modules.
        monkeypatch.setattr("propagation.agent.run_propagation_wave", fake_run_wave)
        monkeypatch.setattr("propagation.proposal.write_proposals", fake_write_proposals)
        # Cache a reference so the imports above resolve inside the test body.
        import propagation.agent  # noqa: F401
        import propagation.proposal  # noqa: F401

        app = _app(tmp_db)
        tmp_dir = tempfile.mkdtemp()
        try:
            with app.app_context():
                folder = db_module.create_folder("SDLC")
                pid = folder["id"]
                db_module.register_project_from_template(pid, "sdlc", tmp_dir)
                event = db_module.create_change_event(
                    project_id=pid, source_key="BA-REQ",
                    from_version=1, to_version=2, changed_reqs=["REQ-014"],
                )
                db_module.create_propagation_job(event["id"], "DB-MODEL", persona_id=None, depth=1)
                db_module.create_propagation_job(event["id"], "UX-WIRE", persona_id=None, depth=1)
                for job in db_module.list_propagation_jobs(event["id"]):
                    db_module.update_propagation_job(job["id"], state="queued")

                from routes.projects import _run_wave_background
                with app.test_request_context():
                    _run_wave_background(app, pid, event["id"], endpoint=None, model_id="m")

                jobs = {j["artifact_key"]: j for j in db_module.list_propagation_jobs(event["id"])}
                # DB-MODEL carries its own rejection reason, parsed correctly.
                assert jobs["DB-MODEL"]["state"] == "failed"
                assert jobs["DB-MODEL"]["error"] == "Shrink guard: rewrite is a summary"
                # UX-WIRE was 'ok' but the fake wave never wrote its proposal, so
                # it is still running -> surfaced as a clean failure, not a crash.
                assert jobs["UX-WIRE"]["state"] == "failed"
                assert "object is not subscriptable" not in jobs["UX-WIRE"]["error"]
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
