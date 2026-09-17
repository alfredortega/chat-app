import os
import tempfile
import pytest
import database as db_module
from app import create_app


def _client_with_setup(tmp_db):
    """Return (client, app, pid) with a registered SDLC project."""
    app = create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })
    with app.app_context():
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = db_module.create_folder("SDLC Project")
            pid = folder["id"]
            db_module.register_project_from_template(pid, "sdlc", tmp_dir)
            # A change event for the issue/digest coverage.
            event = db_module.create_change_event(
                project_id=pid, source_key="BA-REQ",
                from_version=1, to_version=2, changed_reqs=["REQ-014"],
            )
    with app.test_client() as client:
        return client, pid, event


class TestListProjects:
    def test_list_projects_empty(self, tmp_db):
        client = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        }).test_client()
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_projects_contains_managed(self, tmp_db):
        client, pid, _event = _client_with_setup(tmp_db)
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.get_json()
        assert any(p["id"] == pid and p["kind"] == "project" for p in projects)


class TestArtifactsRoute:
    def test_artifacts_happy_path(self, tmp_db):
        client, pid, _event = _client_with_setup(tmp_db)
        resp = client.get(f"/api/projects/{pid}/artifacts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["artifacts"]) == 6
        assert data["counts"]["current"] == 6

    def test_artifacts_404(self, tmp_db):
        client, _, _ = _client_with_setup(tmp_db)
        resp = client.get("/api/projects/9999/artifacts")
        assert resp.status_code == 404


class TestChangesRoute:
    def test_changes_happy_path(self, tmp_db):
        client, pid, event = _client_with_setup(tmp_db)
        resp = client.get(f"/api/projects/{pid}/changes")
        assert resp.status_code == 200
        events = resp.get_json()
        assert len(events) == 1
        assert events[0]["id"] == event["id"]

    def test_changes_404(self, tmp_db):
        client, _, _ = _client_with_setup(tmp_db)
        resp = client.get("/api/projects/9999/changes")
        assert resp.status_code == 404


class TestProposalsRoute:
    def test_proposals_empty_happy_path(self, tmp_db):
        client, pid, event = _client_with_setup(tmp_db)
        resp = client.get(f"/api/projects/{pid}/changes/{event['id']}/proposals")
        assert resp.status_code == 200
        assert resp.get_json()["proposals"] == []

    def test_proposals_missing_change_404(self, tmp_db):
        client, pid, _event = _client_with_setup(tmp_db)
        resp = client.get(f"/api/projects/{pid}/changes/999/proposals")
        assert resp.status_code == 404

    def test_proposals_missing_project_404(self, tmp_db):
        client, _, _ = _client_with_setup(tmp_db)
        resp = client.get("/api/projects/9999/changes/1/proposals")
        assert resp.status_code == 404


class TestIssuesRoute:
    def test_issues_happy_path(self, tmp_db):
        client, pid, event = _client_with_setup(tmp_db)
        with create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        }).app_context():
            from propagation.qa import ask_question
            ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                         "Which engine?", blocking=True)
        resp = client.get(f"/api/projects/{pid}/issues")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_issues_404(self, tmp_db):
        client, _, _ = _client_with_setup(tmp_db)
        resp = client.get("/api/projects/9999/issues")
        assert resp.status_code == 404


class TestAssumptionsRoute:
    def test_assumptions_happy_path(self, tmp_db):
        client, pid, event = _client_with_setup(tmp_db)
        with create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        }).app_context():
            from propagation.qa import record_assumption_marker
            record_assumption_marker(pid, "DB-MODEL", "REQ-014", 2, "TLS required")
        resp = client.get(f"/api/projects/{pid}/assumptions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["unresolved_count"] == 1
        assert "DB-MODEL" in data["by_artifact"]


class TestSettingsRoute:
    def test_settings_happy_path(self, tmp_db):
        client, pid, _event = _client_with_setup(tmp_db)
        resp = client.get(f"/api/projects/{pid}/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["project_id"] == pid
        assert data["template_id"] == "sdlc"
        assert data["propagation_mode"] == "propose"

    def test_settings_404(self, tmp_db):
        client, _, _ = _client_with_setup(tmp_db)
        resp = client.get("/api/projects/9999/settings")
        assert resp.status_code == 404


class TestCreateProjectRoute:
    def test_create_project_with_template(self, tmp_db):
        client = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        with client.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                with client.test_client() as c:
                    resp = c.post("/api/projects", json={
                        "name": "New Managed Project",
                        "template_id": "sdlc",
                        "workspace_dir": tmp_dir,
                    })
                    assert resp.status_code == 201
                    data = resp.get_json()
                    assert data["artifacts_registered"] == 6

    def test_create_project_requires_name(self, tmp_db):
        client = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        }).test_client()
        resp = client.post("/api/projects", json={})
        assert resp.status_code == 400


class TestWorkerStartup:
    def test_worker_starts_once_under_factory(self, tmp_db):
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
            "START_PROPAGATION_WORKER": True,
        })
        worker = app.config.get("PROPAGATION_WORKER")
        assert worker is not None
        assert worker.is_running
        worker.stop()
        assert not worker.is_running

    def test_worker_not_started_by_default(self, tmp_db):
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        assert app.config.get("PROPAGATION_WORKER") is None

    def test_app_startup_recovers_stuck_running_jobs(self, tmp_db):
        """A job left 'running' by a killed wave is reset to 'pending' on the
        next app boot so the user can re-run propagation."""
        # Simulate a crashed wave: create a job, then strand it in 'running'.
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        folder = db_module.create_folder("P")
        pid = folder["id"]
        event = db_module.create_change_event(pid, "BA-REQ", 1, 2)
        job = db_module.create_propagation_job(event["id"], "DB-MODEL", 1, 1)
        db_module.update_propagation_job(job["id"], state="running")

        # A fresh boot (new create_app) must reset the stranded job.
        app2 = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app2.app_context():
            rows = db_module.list_propagation_jobs(event["id"])
        assert rows[0]["state"] == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])