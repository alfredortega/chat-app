import os
import tempfile
import pytest
import database as db_module
from app import create_app
from git_integration import git_commit_all
from propagation.notify import run_notify_scan, notify_status
from propagation.scanner import compute_content_hash


def _make_project(tmp_db):
    app = create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })
    return app


def _register_sdlc(app, project_id, tmp_dir):
    from propagation.scanner import scan_project
    with app.app_context():
        db_module.register_project_from_template(project_id, "sdlc", tmp_dir)


class TestNotifyMode:
    """Tests for C14 notify-mode end-to-end detection."""

    def test_notify_scan_detects_change_and_marks_stale(self, tmp_db):
        """Modifying REQ-014 marks exactly the downstream artifacts stale."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                # Trace REQ-014 to DB-MODEL, UX-WIRE, SEC-RISK, QA-PLAN, PM-PLAN
                for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
                    db_module.create_artifact_trace(pid, key, "REQ-014")

                # Overwrite BA-REQ with the golden change (SQLite -> SQLite+MySQL).
                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                new_content = (
                    "---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 2\n"
                    "origin: human\nderives_from: []\n---\n\n"
                    "## REQ-014 — Use SQLite and MySQL via SQLAlchemy ORM\n"
                    "The application must support both SQLite and MySQL through SQLAlchemy.\n"
                )
                with open(req, "w") as f:
                    f.write(new_content)

                report = run_notify_scan(pid)

                assert len(report.change_events) == 1
                event = report.change_events[0]
                assert "REQ-014" in event["changed_reqs"]

                # Downstream artifacts are stale, BA-REQ is not.
                assert set(report.stale_artifacts) == {
                    "DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"
                }
                assert db_module.get_artifact(pid, "BA-REQ")["status"] == "current"
                assert db_module.get_artifact(pid, "DB-MODEL")["status"] == "stale"

    def test_whitespace_only_edit_queues_nothing(self, tmp_db):
        """Whitespace-only BA edit -> no change event, nothing stale (2.4)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK"]:
                    db_module.create_artifact_trace(pid, key, "REQ-014")

                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(req) as f:
                    base = f.read()
                with open(req, "w") as f:
                    f.write(base + "\n   \n\t\n")

                report = run_notify_scan(pid)
                assert report.change_events == []
                assert report.stale_artifacts == []

    def test_no_llm_spend(self, tmp_db):
        """Notify mode must not invoke any LLM summarizer (C14 gate)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                db_module.create_artifact_trace(pid, "DB-MODEL", "REQ-014")

                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(req, "w") as f:
                    f.write("---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 2\norigin: human\nderives_from: []\n---\n\n## REQ-014 — Dual DB\n")

                report = run_notify_scan(pid)
                assert len(report.change_events) == 1
                # Summary must be empty: no summarizer is ever invoked.
                assert report.change_events[0].get("summary", "") == ""

    def test_notify_status_snapshot(self, tmp_db):
        """notify_status reports counts without running change detection."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                status = notify_status(pid)
                assert status["project_id"] == pid
                assert status["stale_count"] == 0
                assert status["change_event_count"] == 0

    def test_trace_gaps_reported(self, tmp_db):
        """Trace coverage gaps are surfaced during notify scan."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)

                # Add a requirement with NO trace.
                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                content = (
                    "---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 2\n"
                    "origin: human\nderives_from: []\n---\n\n"
                    "## REQ-777 — Orphan requirement\n"
                )
                with open(req, "w") as f:
                    f.write(content)

                report = run_notify_scan(pid)
                assert "REQ-777" in report.untraced_requirements

    def test_recorded_change_event_persists(self, tmp_db):
        """Change events created by notify mode are stored in change_events."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                _register_sdlc(app, pid, tmp_dir)
                db_module.create_artifact_trace(pid, "QA-PLAN", "REQ-100")

                req = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(req, "w") as f:
                    f.write("---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 2\norigin: human\nderives_from: []\n---\n\n## REQ-100 — Audit trail\n")

                run_notify_scan(pid)
                events = db_module.list_change_events(pid)
                assert len(events) == 1
                assert events[0]["source_key"] == "BA-REQ"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])