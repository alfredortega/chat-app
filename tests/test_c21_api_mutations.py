import json
import os
import tempfile
import shutil
import pytest
import database as db_module
from app import create_app


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


@pytest.fixture
def project_env(tmp_db, request):
    """Yield (client, pid, event, tmp_dir) with a live workspace + git baseline."""
    app = _app(tmp_db)
    tmp_dir = tempfile.mkdtemp(prefix="projtest_")
    request.addfinalizer(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

    with app.app_context():
        folder = db_module.create_folder("SDLC")
        pid = folder["id"]
        db_module.register_project_from_template(pid, "sdlc", tmp_dir)
        for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            db_module.create_artifact_trace(pid, key, "REQ-014")
        event = db_module.create_change_event(
            project_id=pid, source_key="BA-REQ",
            from_version=1, to_version=2, changed_reqs=["REQ-014"],
        )
    with app.test_client() as client:
        yield client, pid, event, tmp_dir


def _make_proposals(tmp_db, pid, event, tmp_dir):
    """Write proposal files + mark jobs proposed (DB-level), for apply/reject tests."""
    with _app(tmp_db).app_context():
        from propagation.impact import queue_propagation_jobs
        from propagation.proposal import write_proposals
        from propagation.agent import RewritePayload
        queue_propagation_jobs(pid, event["id"], ["REQ-014"])
        jobs = db_module.list_propagation_jobs(event["id"])
        payloads = [
            RewritePayload(
                artifact_key=job["artifact_key"],
                content=f"# {job['artifact_key']}\n\n## placeholder\n\n## S\nNew body.\n",
                version=2,
                status="ok",
                errors=[],
            )
            for job in jobs
        ]
        write_proposals(pid, event["id"], payloads)
        return jobs


class TestScanRoute:
    def test_scan_happy(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/scan")
        assert resp.status_code == 200
        assert "project_id" in resp.get_json()

    def test_scan_404(self, tmp_db, project_env):
        client, _pid, _event, _ = project_env
        resp = client.post("/api/projects/9999/scan")
        assert resp.status_code == 404


class TestPropagateRoute:
    def test_propagate_queues_jobs(self, tmp_db, project_env):
        client, pid, event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/changes/{event['id']}/propagate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["queued"] >= 1

    def test_propagate_missing_change_404(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/changes/999/propagate")
        assert resp.status_code == 404


class TestApplyRejectRoutes:
    def test_apply_proposal_happy(self, tmp_db, project_env):
        client, pid, event, tmp_dir = project_env
        jobs = _make_proposals(tmp_db, pid, event, tmp_dir)
        job = jobs[0]
        resp = client.post(f"/api/projects/{pid}/proposals/{job['id']}/apply")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "applied"

    def test_apply_stale_proposal_409(self, tmp_db, project_env):
        client, pid, event, tmp_dir = project_env
        jobs = _make_proposals(tmp_db, pid, event, tmp_dir)
        job = jobs[0]
        # Mutate the artifact underneath the proposal -> stale (409).
        with _app(tmp_db).app_context():
            artifact = db_module.get_artifact(pid, job["artifact_key"])
            full = os.path.join(tmp_dir, artifact["rel_path"])
            with open(full, "w") as f:
                f.write("---\nartifact_id: X\nrole: Y\nversion: 999\norigin: human\nderives_from: []\n---\n\n## H\nchanged\n")

        resp = client.post(f"/api/projects/{pid}/proposals/{job['id']}/apply")
        assert resp.status_code == 409

    def test_reject_proposal_happy(self, tmp_db, project_env):
        client, pid, event, tmp_dir = project_env
        jobs = _make_proposals(tmp_db, pid, event, tmp_dir)
        job = jobs[0]
        resp = client.post(f"/api/projects/{pid}/proposals/{job['id']}/reject")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "rejected"

    def test_proposal_404(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/proposals/9999/apply")
        assert resp.status_code == 404


class TestApplyAllRollbackRoutes:
    def test_apply_all_and_rollback(self, tmp_db, project_env):
        client, pid, event, tmp_dir = project_env
        jobs = _make_proposals(tmp_db, pid, event, tmp_dir)

        resp = client.post(f"/api/projects/{pid}/changes/{event['id']}/apply-all")
        assert resp.status_code == 200
        assert len(resp.get_json()["applied"]) >= 1

        resp = client.post(f"/api/projects/{pid}/changes/{event['id']}/rollback")
        assert resp.status_code == 200


class TestIssueRoutes:
    def test_answer_issue(self, tmp_db, project_env):
        client, pid, event, _ = project_env
        with _app(tmp_db).app_context():
            from propagation.qa import ask_question
            issue = ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                                 "Which engine?", blocking=True)

        resp = client.post(f"/api/projects/{pid}/issues/{issue['id']}/answer", json={"answer": "MySQL 8.0"})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "answered"

    def test_answer_issue_404(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/issues/9999/answer", json={"answer": "x"})
        assert resp.status_code == 404

    def test_dismiss_issue(self, tmp_db, project_env):
        client, pid, event, _ = project_env
        with _app(tmp_db).app_context():
            from propagation.qa import ask_question
            issue = ask_question(pid, "SEC-RISK", None, event["id"], 1, "REQ-014",
                                 "TLS?", blocking=False)

        resp = client.post(f"/api/projects/{pid}/issues/{issue['id']}/dismiss", json={"note": "not a requirement issue"})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "dismissed"


class TestArtifactRequestRoutes:
    def test_create_and_approve(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/artifact-requests", json={
            "artifact_key": "QA-PLAN-AUTH",
            "rel_path": "Test Cases/QA-PLAN-AUTH.md",
            "rationale": "Auth needs its own plan.",
        })
        assert resp.status_code == 201
        request = resp.get_json()
        assert request["status"] == "pending"

        approve = client.post(f"/api/projects/{pid}/artifact-requests/{request['id']}/approve")
        assert approve.status_code == 200
        assert approve.get_json()["status"] == "approved"
        with _app(tmp_db).app_context():
            assert db_module.is_artifact_registered(pid, "QA-PLAN-AUTH") is True

    def test_reject_request(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.post(f"/api/projects/{pid}/artifact-requests", json={
            "artifact_key": "BAD-KEY",
            "rel_path": "X/BAD-KEY.md",
        })
        request = resp.get_json()
        reject = client.post(f"/api/projects/{pid}/artifact-requests/{request['id']}/reject")
        assert reject.status_code == 200
        assert reject.get_json()["status"] == "rejected"


class TestSettingsRoutes:
    def test_update_settings(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.put(f"/api/projects/{pid}/settings", json={
            "propagation_mode": "notify",
            "token_budget": 50000,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["propagation_mode"] == "notify"
        assert data["token_budget"] == 50000

    def test_update_settings_invalid_mode(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        resp = client.put(f"/api/projects/{pid}/settings", json={"propagation_mode": "bogus"})
        assert resp.status_code == 400

    def test_set_ba_conversation(self, tmp_db, project_env):
        client, pid, _event, _ = project_env
        with _app(tmp_db).app_context():
            conv = db_module.create_conversation("BA Inbox", "")
            conv_id = conv["id"]
        resp = client.put(f"/api/projects/{pid}/ba-conversation", json={"conversation_id": conv_id})
        assert resp.status_code == 200
        assert resp.get_json()["ba_conversation_id"] == conv_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])