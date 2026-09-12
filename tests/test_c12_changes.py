import os
import tempfile
import pytest
import database as db_module
from app import create_app
from parsers import RequirementValidationError
from propagation.changes import (
    analyse_requirement_change,
    extract_req_ids_from_diff,
    create_change_event_for_source,
    scan_and_create_change_event,
    RequirementChange,
)
from propagation.scanner import scan_project
from freezegun import freeze_time


def _make_project(tmp_db):
    """Create an app context with a registered SDLC project."""
    app = create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })
    return app


class TestRequirementChangeAnalysis:
    """Tests for deterministic REQ-nnn change classification (D3)."""

    def test_no_change(self):
        old = "## REQ-001 — Use SQLite\nSome text"
        new = "## REQ-001 — Use SQLite\nSome text"
        analysis = analyse_requirement_change(old, new)
        assert analysis.valid
        assert analysis.changed_reqs == []
        assert analysis.removed_reqs == []

    def test_added_requirement(self):
        old = "## REQ-001 — Use SQLite"
        new = "## REQ-001 — Use SQLite\n## REQ-002 — Use MySQL"
        analysis = analyse_requirement_change(old, new)
        assert analysis.added_reqs == ["REQ-002"]
        assert "REQ-002" in analysis.changed_reqs

    def test_removed_requirement(self):
        old = "## REQ-001 — Use SQLite\n## REQ-002 — Use MySQL"
        new = "## REQ-001 — Use SQLite"
        analysis = analyse_requirement_change(old, new)
        assert analysis.removed_reqs == ["REQ-002"]
        assert "REQ-002" in analysis.changed_reqs

    def test_modified_requirement_title(self):
        old = "## REQ-001 — Use SQLite"
        new = "## REQ-001 — Use SQLite and MySQL"
        analysis = analyse_requirement_change(old, new)
        assert analysis.modified_reqs == ["REQ-001"]
        assert analysis.changed_reqs == ["REQ-001"]

    def test_diff_ids_union(self):
        """IDs referenced in the diff are included even when headings match."""
        old = "## REQ-001 — Use SQLite\nSQLite local only."
        new = "## REQ-001 — Use SQLite\nSQLite and MySQL via SQLAlchemy."
        diff = """--- a/Requirements/BA-REQ.md
+++ b/Requirements/BA-REQ.md
@@ -1,2 +1,2 @@
-SQLite local only.
+SQLite and MySQL via SQLAlchemy.
"""
        analysis = analyse_requirement_change(old, new, diff=diff)
        assert "REQ-001" in analysis.changed_reqs

    def test_extract_req_ids_from_diff(self):
        diff = """@@ -1,2 +1,2 @@
-SQLite only REQ-001
+SQLite and MySQL REQ-001 REQ-014
"""
        assert extract_req_ids_from_diff(diff) == ["REQ-001", "REQ-014"]

    def test_extract_req_ids_skips_file_headers(self):
        diff = """--- a/Requirements/BA-REQ.md
+++ b/Requirements/BA-REQ.md
@@ -1 +1 @@
-REQ-001
+REQ-002
"""
        assert extract_req_ids_from_diff(diff) == ["REQ-001", "REQ-002"]


class TestValidation:
    """Tests for duplicate and renumber detection (C12 gate)."""

    def test_duplicate_req_id_raises(self):
        old = "## REQ-001 — Use SQLite"
        new = "## REQ-001 — Use SQLite\n## REQ-001 — Use MySQL"
        analysis = analyse_requirement_change(old, new)
        assert analysis.valid is False
        assert len(analysis.errors) > 0

    def test_duplicate_req_id_nothing_queued(self, tmp_db):
        """Duplicate REQ-nnn -> validation error, nothing queued."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                old = "## REQ-001 — Use SQLite\n"
                new = "## REQ-001 — Use SQLite\n## REQ-001 — Duplicate\n"

                with pytest.raises(RequirementValidationError):
                    create_change_event_for_source(
                        project_id, "BA-REQ", old, new, diff=""
                    )
                assert db_module.list_change_events(project_id) == []

    def test_renumber_detected(self):
        """Renumbered REQ ID is a hard validation failure (1.2)."""
        old = "## REQ-001 — Use SQLite only\n"
        new = "## REQ-002 — Use SQLite only\n"  # Renamed, same text
        analysis = analyse_requirement_change(old, new)
        assert analysis.valid is False
        assert any("Renumber" in e for e in analysis.errors)

    def test_renumber_refuses_propagation(self, tmp_db):
        """Renumber -> validation error, nothing queued (C12 gate)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                old = "## REQ-001 — Use SQLite only\n"
                new = "## REQ-002 — Use SQLite only\n"

                with pytest.raises(RequirementValidationError):
                    create_change_event_for_source(
                        project_id, "BA-REQ", old, new, diff=""
                    )
                assert db_module.list_change_events(project_id) == []


class TestChangeEventCreation:
    """Tests for change event row creation."""

    def test_create_event_for_modified_requirement(self, tmp_db):
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                old = "## REQ-001 — Use SQLite only\n"
                new = "## REQ-001 — Use SQLite and MySQL\n"

                event = create_change_event_for_source(
                    project_id, "BA-REQ", old, new, diff="+SQLite and MySQL"
                )
                assert event is not None
                assert event["source_key"] == "BA-REQ"
                assert "REQ-001" in event["changed_reqs"]

                events = db_module.list_change_events(project_id)
                assert len(events) == 1

    def test_no_change_no_event(self, tmp_db):
        """Whitespace-only / no-op edit queues nothing (2.4)."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                old = "## REQ-001 — Use SQLite only\n\nExtra text.\n"
                new = "## REQ-001 — Use SQLite only\n\nExtra text.\n"
                event = create_change_event_for_source(
                    project_id, "BA-REQ", old, new, diff=""
                )
                assert event is None
                assert db_module.list_change_events(project_id) == []

    def test_summarizer_called(self, tmp_db):
        """The LLM summarizer is called exactly once per new event."""
        calls = []

        def fake_summarizer(diff, changed_reqs):
            calls.append((diff, changed_reqs))
            return "Test summary."

        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                old = "## REQ-001 — Use SQLite only\n"
                new = "## REQ-001 — Use SQLite and MySQL\n"

                event = create_change_event_for_source(
                    project_id, "BA-REQ", old, new, diff="+x", summarizer=fake_summarizer
                )
                assert len(calls) == 1
                assert event["summary"] == "Test summary."


class TestDebounce:
    """Tests for change-event debouncing (2.2, C12 gate)."""

    def test_three_rapid_saves_one_event(self, tmp_db):
        """3 rapid saves within the debounce window -> 1 event."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                def save(content):
                    create_change_event_for_source(
                        project_id, "BA-REQ",
                        "## REQ-001 — Old\n",
                        content,
                        diff=f"+{content}",
                    )

                with freeze_time("2025-01-01 10:00:00"):
                    save("## REQ-001 — Change A\n")
                with freeze_time("2025-01-01 10:00:10"):
                    save("## REQ-001 — Change B\n")
                with freeze_time("2025-01-01 10:00:20"):
                    save("## REQ-001 — Change C\n")

                events = db_module.list_change_events(project_id)
                assert len(events) == 1

    def test_outside_window_creates_new_event(self, tmp_db):
        """Saves separated by more than the debounce window -> 2 events."""
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                with freeze_time("2025-01-01 10:00:00"):
                    create_change_event_for_source(
                        project_id, "BA-REQ",
                        "## REQ-001 — Old\n",
                        "## REQ-001 — Change A\n",
                        diff="+A",
                    )
                with freeze_time("2025-01-01 10:03:00"):
                    create_change_event_for_source(
                        project_id, "BA-REQ",
                        "## REQ-001 — Old\n",
                        "## REQ-001 — Change B\n",
                        diff="+B",
                    )

                events = db_module.list_change_events(project_id)
                assert len(events) == 2


class TestScanAndCreateChangeEvent:
    """Tests for the full scan -> change event flow."""

    def test_scan_and_create_event_from_workspace(self, tmp_db):
        """Modify BA-REQ on disk, then create a change event from the scan."""
        from git_integration import git_commit_all
        app = _make_project(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]
                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                req_path = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(req_path, "w") as f:
                    f.write("---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 1\norigin: human\nderives_from: []\n---\n\n## REQ-001 — SQLite only\n\nNew content.\n")
                git_commit_all(tmp_dir, "baseline")

                # Modify the requirement
                with open(req_path, "w") as f:
                    f.write("---\nartifact_id: BA-REQ\nrole: Business Analyst\nversion: 1\norigin: human\nderives_from: []\n---\n\n## REQ-001 — SQLite and MySQL\n\nVia SQLAlchemy.\n")

                event = scan_and_create_change_event(project_id, "BA-REQ")
                assert event is not None
                assert "REQ-001" in event["changed_reqs"]
                assert event["diff"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])