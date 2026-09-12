import os
import tempfile
import pytest
import database as db_module
from app import create_app
from propagation.impact import (
    select_affected_artifact_keys,
    compute_affected_depths,
    queue_propagation_jobs,
    detect_conflicts,
    trace_coverage_report,
    ImpactResult,
)


def _make_project(tmp_db):
    app = create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })
    return app


def _register_sdlc(app, project_id, tmp_dir):
    with app.app_context():
        db_module.register_project_from_template(project_id, "sdlc", tmp_dir)


def _add_trace(project_id, artifact_key, req_id):
    db_module.create_artifact_trace(project_id, artifact_key, req_id)


class TestSelectAffectedArtifacts:
    """Tests for deterministic artifact-level selection (D3, D16)."""

    def test_no_traces_no_affected(self, tmp_db):
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                assert select_affected_artifact_keys(pid, ["REQ-014"]) == []

    def test_only_tracing_artifacts_selected(self, tmp_db):
        """Only artifacts tracing to the changed req are selected (D16)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                _add_trace(pid, "DB-MODEL", "REQ-014")
                _add_trace(pid, "UX-WIRE", "REQ-014")
                _add_trace(pid, "SEC-RISK", "REQ-014")
                _add_trace(pid, "QA-PLAN", "REQ-014")
                _add_trace(pid, "PM-PLAN", "REQ-014")

                affected = select_affected_artifact_keys(pid, ["REQ-014"])
                assert affected == ["DB-MODEL", "PM-PLAN", "QA-PLAN", "SEC-RISK", "UX-WIRE"]

    def test_req_not_traced_is_not_selected(self, tmp_db):
        """A requirement referenced only by QA-PLAN queues only QA-PLAN."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                _add_trace(pid, "QA-PLAN", "REQ-100")

                affected = select_affected_artifact_keys(pid, ["REQ-100"])
                assert "QA-PLAN" in affected
                assert "DB-MODEL" not in affected

    def test_transitive_downstream_included(self, tmp_db):
        """Transitive downstream of a directly-affected artifact is queued (D4)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                # DB-MODEL -> QA-PLAN -> PM-PLAN downstream chain exists in sdlc template
                _add_trace(pid, "DB-MODEL", "REQ-014")

                affected = select_affected_artifact_keys(pid, ["REQ-014"])
                assert "DB-MODEL" in affected
                assert "QA-PLAN" in affected
                assert "PM-PLAN" in affected

    def test_removed_req_queues_dependants(self, tmp_db):
        """Deleted requirement -> dependants still queued (2.3)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                _add_trace(pid, "SEC-RISK", "REQ-017")

                affected = select_affected_artifact_keys(pid, [], removed_reqs=["REQ-017"])
                assert "SEC-RISK" in affected


class TestDepths:
    """Tests for dependency depth assignment (golden scenario)."""

    def test_golden_scenario_depths(self, tmp_db):
        """Editing REQ-014 queues UX/DB/SEC at 1, QA at 2, PM at 3."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                _add_trace(pid, "DB-MODEL", "REQ-014")
                _add_trace(pid, "UX-WIRE", "REQ-014")
                _add_trace(pid, "SEC-RISK", "REQ-014")
                _add_trace(pid, "QA-PLAN", "REQ-014")
                _add_trace(pid, "PM-PLAN", "REQ-014")

                affected = select_affected_artifact_keys(pid, ["REQ-014"])
                depths = compute_affected_depths(pid, affected)

                assert depths["UX-WIRE"] == 1
                assert depths["DB-MODEL"] == 1
                assert depths["SEC-RISK"] == 1
                assert depths["QA-PLAN"] == 2
                assert depths["PM-PLAN"] == 3


class TestQueueJobs:
    """Tests for propagation job queueing (no execution)."""

    def test_queue_jobs_no_execution(self, tmp_db):
        """Jobs are queued only; nothing runs (C13 gate)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                _add_trace(pid, "DB-MODEL", "REQ-014")
                _add_trace(pid, "UX-WIRE", "REQ-014")

                event = db_module.create_change_event(
                    project_id=pid, source_key="BA-REQ",
                    from_version=1, to_version=2,
                    changed_reqs=["REQ-014"],
                )
                jobs = queue_propagation_jobs(pid, event["id"], ["REQ-014"])
                assert len(jobs) > 0
                for job in jobs:
                    assert job["state"] == "pending"
                # Nothing applied.
                assert len(db_module.list_propagation_jobs(event["id"])) == len(jobs)

    def test_granularity_role_owns_four_only_one_queued(self, tmp_db):
        """Multi-artifact fixture: role owns 4, only the tracing 1 is queued (D16)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                # QA role owns 4 artifacts: QA-PLAN plus three siblings.
                for i in range(1, 4):
                    db_module.create_artifact(
                        project_id=pid,
                        artifact_key=f"QA-EXTRA-{i}",
                        rel_path=f"Test Cases/QA-EXTRA-{i}.md",
                        role_persona_id=1,
                    )
                    # QA-EXTRA depends on BA-REQ
                    db_module.create_artifact_dep(pid, "BA-REQ", f"QA-EXTRA-{i}")

                # Only QA-EXTRA-1 traces to REQ-014.
                _add_trace(pid, "QA-EXTRA-1", "REQ-014")

                affected = select_affected_artifact_keys(pid, ["REQ-014"])
                qa_affected = [k for k in affected if k.startswith("QA-")]
                assert qa_affected == ["QA-EXTRA-1"]

                event = db_module.create_change_event(
                    project_id=pid, source_key="BA-REQ",
                    from_version=1, to_version=2, changed_reqs=["REQ-014"],
                )
                jobs = queue_propagation_jobs(pid, event["id"], ["REQ-014"])
                job_keys = [j["artifact_key"] for j in jobs]
                qa_jobs = [k for k in job_keys if k.startswith("QA-")]
                assert qa_jobs == ["QA-EXTRA-1"]

    def test_same_role_shared_batch(self, tmp_db):
        """Same-role artifacts get the same batch id (D16)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                for i in range(1, 3):
                    db_module.create_artifact(
                        project_id=pid,
                        artifact_key=f"QA-EXTRA-{i}",
                        rel_path=f"Test Cases/QA-EXTRA-{i}.md",
                        role_persona_id=1,
                    )
                    db_module.create_artifact_dep(pid, "BA-REQ", f"QA-EXTRA-{i}")
                    _add_trace(pid, f"QA-EXTRA-{i}", "REQ-014")

                event = db_module.create_change_event(
                    project_id=pid, source_key="BA-REQ",
                    from_version=1, to_version=2, changed_reqs=["REQ-014"],
                )
                jobs = queue_propagation_jobs(pid, event["id"], ["REQ-014"])
                qa_batches = {j["batch_id"] for j in jobs if j["artifact_key"].startswith("QA-")}
                assert len(qa_batches) == 1


class TestConflictDetection:
    """Tests for hand-edit conflict detection (2.4)."""

    def test_hand_edited_downstream_becomes_conflict(self, tmp_db):
        """Hand-edited downstream artifact -> conflict, not overwritten."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                # Originally 'current' (registered hash = on-disk hash).
                conflicts = detect_conflicts(pid)
                assert conflicts == []

                # Hand-edit a downstream artifact on disk.
                db_model = os.path.join(tmp_dir, "Data", "DB-MODEL.md")
                with open(db_model, "w") as f:
                    f.write("---\nartifact_id: DB-MODEL\nrole: Database Developer\nversion: 1\norigin: human\nderives_from: []\n---\n\n## Changed by hand\n")

                conflicts = detect_conflicts(pid)
                assert "DB-MODEL" in conflicts
                artifact = db_module.get_artifact(pid, "DB-MODEL")
                assert artifact["status"] == "conflict"

    def test_ba_edit_is_not_conflict(self, tmp_db):
        """A BA-REQ edit is a normal modification, not a conflict."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(req, "w") as f:
                    f.write("---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 1\norigin: human\nderives_from: []\n---\n\n## REQ-001 — New\n")

                conflicts = detect_conflicts(pid)
                assert "BA-REQ" not in conflicts


class TestTraceCoverage:
    """Tests for the trace coverage report (1.3 / 2.3)."""

    def test_coverage_report_untraced(self, tmp_db):
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                # Add a requirement to BA-REQ with no trace.
                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(req, "w") as f:
                    f.write("---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 1\norigin: human\nderives_from: []\n---\n\n## REQ-200 — Untraced req\n")
                # Update hash so scan sees it as current.
                from propagation.scanner import compute_content_hash
                with open(req) as f:
                    content = f.read()
                db_module.update_artifact(pid, "BA-REQ", content_hash=compute_content_hash(content))

                report = trace_coverage_report(pid)
                assert "REQ-200" in report["untraced_requirements"]
                assert "DB-MODEL" in report["untraced_artifacts"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])