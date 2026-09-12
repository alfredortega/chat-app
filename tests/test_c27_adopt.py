import os
import tempfile
import shutil
import pytest
import database as db_module
from app import create_app
from propagation.adopt import adopt_workspace, _discover_markdown_files


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _legacy_workspace(tmp_dir):
    """Create a legacy code-folder-style workspace with files and NO front-matter."""
    dirs = ["Requirements", "Test Planning", "UX Design", "Data Modeling", "Project Management"]
    for d in dirs:
        os.makedirs(os.path.join(tmp_dir, d), exist_ok=True)
    with open(os.path.join(tmp_dir, "Requirements", "BA-REQ.md"), "w") as f:
        f.write("# Requirements\n\nREQ-001: Use SQLite only\n")
    with open(os.path.join(tmp_dir, "Data Modeling", "DB-MODEL.md"), "w") as f:
        f.write("# Data model\n\n## Entity: User\n")
    with open(os.path.join(tmp_dir, "Project Management", "PM-PLAN.md"), "w") as f:
        f.write("# Plan\n\n## Milestones\n")
    return tmp_dir


class TestDiscover:
    def test_discovers_markdown_files(self):
        with tempfile.TemporaryDirectory() as td:
            _legacy_workspace(td)
            files = _discover_markdown_files(td)
            assert "BA-REQ" in files
            assert "DB-MODEL" in files
            assert "PM-PLAN" in files

    def test_ignores_git_and_agents(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".agents", "proposals"))
            os.makedirs(os.path.join(td, "Requirements"))
            with open(os.path.join(td, ".agents", "proposals", "X.md"), "w") as f:
                f.write("x")
            with open(os.path.join(td, "Requirements", "BA-REQ.md"), "w") as f:
                f.write("req")
            files = _discover_markdown_files(td)
            assert "X" not in files
            assert "BA-REQ" in files


class TestAdoptWorkspace:
    def test_adopt_registers_artifacts_non_destructively(self, tmp_db):
        """Adoption registers files and baseline hashes without rewriting them."""
        app = _app(tmp_db)
        with app.app_context():
            tmp_dir = tempfile.mkdtemp()
            try:
                _legacy_workspace(tmp_dir)
                folder = db_module.create_folder("Legacy")
                pid = folder["id"]
                # Capture the original file content.
                with open(os.path.join(tmp_dir, "Requirements", "BA-REQ.md")) as f:
                    original = f.read()

                result = adopt_workspace(pid, tmp_dir, template_id="sdlc")

                assert len(result["artifacts_registered"]) == 3
                assert result["rewrites_proposed"] == 0

                # File untouched (non-destructive).
                with open(os.path.join(tmp_dir, "Requirements", "BA-REQ.md")) as f:
                    assert f.read() == original

                folder_now = db_module.get_folder(pid)
                assert folder_now["kind"] == "project"
                assert folder_now["propagation_mode"] == "off"  # Q3 default for adopted

                artifacts = db_module.list_artifacts(pid)
                assert len(artifacts) == 3
                ba = next(a for a in artifacts if a["artifact_key"] == "BA-REQ")
                # Baseline hash recorded for a clean first change event.
                assert ba["content_hash"]
                assert ba["status"] == "current"
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_adopt_dry_run_does_not_register(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            tmp_dir = tempfile.mkdtemp()
            try:
                _legacy_workspace(tmp_dir)
                pid = db_module.create_folder("Legacy")["id"]
                result = adopt_workspace(pid, tmp_dir, template_id="sdlc", dry_run=True)
                assert result["dry_run"] is True
                assert result["would_register"] == 3
                assert db_module.list_artifacts(pid) == []
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_adopt_with_front_matter_generation_proposes_rewrites(self, tmp_db):
        """Optional LLM/front-matter pass presents rewrites as diffs, not writes."""
        app = _app(tmp_db)
        with app.app_context():
            tmp_dir = tempfile.mkdtemp()
            try:
                _legacy_workspace(tmp_dir)
                pid = db_module.create_folder("Legacy")["id"]
                result = adopt_workspace(
                    pid, tmp_dir, template_id="sdlc", generate_front_matter=True
                )
                # Rewrites proposed for files missing front-matter.
                assert result["rewrites_proposed"] >= 1
                # But the on-disk content remains unchanged until approved.
                with open(os.path.join(tmp_dir, "Requirements", "BA-REQ.md")) as f:
                    assert "artifact_id: BA-REQ" not in f.read()
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_adopt_unknown_template(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            tmp_dir = tempfile.mkdtemp()
            try:
                _legacy_workspace(tmp_dir)
                pid = db_module.create_folder("Legacy")["id"]
                with pytest.raises(ValueError, match="Unknown template"):
                    adopt_workspace(pid, tmp_dir, template_id="bogus")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)


class TestAdoptRoute:
    def test_adopt_route_happy(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            tmp_dir = tempfile.mkdtemp()
            _legacy_workspace(tmp_dir)
            pid = db_module.create_folder("Legacy")["id"]
        try:
            with app.test_client() as client:
                resp = client.post(f"/api/projects/{pid}/adopt", json={
                    "workspace_dir": tmp_dir,
                    "template_id": "sdlc",
                })
                assert resp.status_code == 200
                data = resp.get_json()
                assert len(data["artifacts_registered"]) == 3
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_adopt_route_404(self, tmp_db):
        app = _app(tmp_db)
        with app.test_client() as client:
            resp = client.post("/api/projects/9999/adopt", json={"workspace_dir": "/tmp"})
            assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])