"""
Tests for create_project_team — the managed "team" project flow that replaced
the legacy create_code_folder.

A new code project must create a git-tracked workspace scaffolded from the
template (D8/D9), register the artifacts and dependency edges, create one
conversation per role (persona, linked to the workspace, output dir at the
role's sub-folder), and designate the Business Analyst conversation as the
project's Q&A inbox (D19).
"""

import os

import pytest

import database as db_module
from app import create_app

TEST_CONFIG = {
    "SQLALCHEMY_DATABASE_URI": None,  # filled per-test from tmp_db
    "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
    "SKIP_DOTENV_WRITE": "1",
}


def _app(tmp_db):
    config = dict(TEST_CONFIG)
    config["SQLALCHEMY_DATABASE_URI"] = tmp_db
    return create_app(config=config)


class TestCreateProjectTeam:
    def test_creates_managed_project_folder(self, tmp_db):
        with _app(tmp_db).app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_out:
                db_module.set_setting("output_dir", tmp_out)

                result = db_module.create_project_team("My App")
                project = result["project"]

                assert project["kind"] == "project"
                assert project["template_id"] == "sdlc"
                assert project["propagation_mode"] == "propose"
                assert project["next_req_seq"] == 1
                assert project["workspace_dir"] == os.path.join(tmp_out, "My App")
                assert result["workspace_dir"] == project["workspace_dir"]

    def test_scaffolds_workspace_artifacts_and_deps(self, tmp_db):
        with _app(tmp_db).app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_out:
                db_module.set_setting("output_dir", tmp_out)

                result = db_module.create_project_team("My App")
                project_id = result["project"]["id"]
                workspace = result["workspace_dir"]

                expected_files = {
                    "Requirements/BA-REQ.md",
                    "Design/UX-WIRE.md",
                    "Data/DB-MODEL.md",
                    "Test Cases/QA-PLAN.md",
                    "Security/SEC-RISK.md",
                    "Project Plan/PM-PLAN.md",
                }
                for rel in expected_files:
                    assert os.path.isfile(os.path.join(workspace, rel)), f"missing {rel}"

                # D8: workspace is a git repository.
                assert os.path.isdir(os.path.join(workspace, ".git"))

                artifacts = db_module.list_artifacts(project_id)
                assert len(artifacts) == 6
                assert result["artifacts_registered"] == 6

                deps = db_module.list_artifact_deps(project_id)
                assert result["edges_created"] == len(deps) == 9

    def test_creates_role_conversations(self, tmp_db):
        with _app(tmp_db).app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_out:
                db_module.set_setting("output_dir", tmp_out)

                result = db_module.create_project_team("My App")
                project = result["project"]
                workspace = result["workspace_dir"]

                convs = result["conversations"]
                by_title = {c["title"]: c for c in convs}
                assert set(by_title) == {
                    "Business Analyst",
                    "UX Designer",
                    "Database Developer",
                    "QA/Tester",
                    "Security Analyst",
                    "Project Manager",
                }

                persona_by_name = {p["name"]: p["id"] for p in db_module.list_personas()}
                for title, conv in by_title.items():
                    assert conv["folder_id"] == project["id"], title
                    # Linked to the workspace root.
                    linked = db_module.list_linked_folders(conv["id"])
                    assert [lf["folder_path"] for lf in linked] == [workspace], title
                    # Output dir points at the role's sub-folder.
                    sub = os.path.basename(conv["output_dir"].rstrip("/"))
                    assert sub in ("Requirements", "Design", "Data", "Test Cases", "Security", "Project Plan"), title
                    # Persona resolved (QA/Tester has no starter persona yet).
                    expected = persona_by_name.get(title)
                    assert conv["persona_id"] == expected, title

    def test_ba_conversation_is_designated_inbox(self, tmp_db):
        with _app(tmp_db).app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_out:
                db_module.set_setting("output_dir", tmp_out)

                result = db_module.create_project_team("My App")
                project = result["project"]
                project_id = project["id"]

                ba = next(c for c in result["conversations"] if c["title"] == "Business Analyst")
                assert project["ba_conversation_id"] == ba["id"]
                assert result["ba_conversation_id"] == ba["id"]
                # BA conversation points at the requirements folder.
                assert ba["output_dir"].endswith("Requirements")

    def test_missing_output_dir_raises_without_leaking_folder(self, tmp_db):
        with _app(tmp_db).app_context():
            folders_before = len(db_module.list_folders())
            with pytest.raises(ValueError):
                db_module.create_project_team("No Output Dir")
            # No plain folder was leaked.
            assert len(db_module.list_folders()) == folders_before

    def test_api_code_folder_creates_full_project(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_out:
                db_module.set_setting("output_dir", tmp_out)

                client = app.test_client()
                resp = client.post(
                    "/api/folders",
                    json={"name": "API Project", "code_folder": True},
                )
                assert resp.status_code == 201
                data = resp.get_json()

                assert data["project"]["kind"] == "project"
                assert data["ba_conversation_id"] is not None
                assert len(data["conversations"]) == 6
                assert data["artifacts_registered"] == 6

                # Role conversations exist in the sidebar data.
                conversations = db_module.list_conversations()
                folder_ids = {c["folder_id"] for c in conversations}
                assert data["project"]["id"] in folder_ids

    def test_api_projects_can_create_without_workspace_dir(self, tmp_db):
        """POST /api/projects with template_id auto-derives the workspace from
        the configured output folder — the single managed-project path."""
        app = _app(tmp_db)
        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_out:
                db_module.set_setting("output_dir", tmp_out)

                client = app.test_client()
                resp = client.post("/api/projects", json={
                    "name": "Canonical",
                    "template_id": "sdlc",
                })
                assert resp.status_code == 201
                data = resp.get_json()
                assert data["project"]["kind"] == "project"
                assert data["project"]["workspace_dir"] == os.path.join(tmp_out, "Canonical")
                assert data["ba_conversation_id"] is not None
                assert len(data["conversations"]) == 6

    def test_api_projects_without_template_is_plain_folder(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            client = app.test_client()
            resp = client.post("/api/projects", json={"name": "Plain"})
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["kind"] == "folder"
            assert "project" not in data

    def test_api_projects_requires_name(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            client = app.test_client()
            resp = client.post("/api/projects", json={"template_id": "sdlc"})
            assert resp.status_code == 400

    def test_api_plain_folder_unchanged(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            client = app.test_client()
            resp = client.post("/api/folders", json={"name": "Plain"})
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["kind"] == "folder"
            assert "project" not in data

    def test_api_code_folder_missing_output_dir_returns_400(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            client = app.test_client()
            resp = client.post(
                "/api/folders",
                json={"name": "Broken", "code_folder": True},
            )
            assert resp.status_code == 400
            assert "output folder" in resp.get_json()["error"]