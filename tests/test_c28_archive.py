import io
import json
import os
import tempfile
import zipfile
import shutil
import pytest
import database as db_module
from app import create_app
from propagation.archive import build_export, import_archive


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _mid_propagation_project(app, tmp_db, workspace):
    """Prepare a project with artifacts, traces, events, jobs, an issue, and
    a BA conversation message — a 'mid-propagation' state."""
    with app.app_context():
        pid = db_module.create_folder("Roundtrip")["id"]
        db_module.register_project_from_template(pid, "sdlc", workspace)
        for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            db_module.create_artifact_trace(pid, key, "REQ-014")
        event = db_module.create_change_event(
            project_id=pid, source_key="BA-REQ",
            from_version=1, to_version=2, changed_reqs=["REQ-014"], diff="+dual-db",
        )
        from propagation.impact import queue_propagation_jobs
        queue_propagation_jobs(pid, event["id"], ["REQ-014"])
        from propagation.qa import ask_question
        ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                     "Which engine?", blocking=True)
        return pid, event


class TestExport:
    def test_export_is_1_1(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as ws:
                pid, _event = _mid_propagation_project(app, tmp_db, ws)
                data = build_export(pid)
                zf = zipfile.ZipFile(io.BytesIO(data))
                manifest = json.loads(zf.read("manifest.json").decode())
                assert manifest["export_version"] == "1.1"
                assert manifest["folder"]["kind"] == "project"
                # Workspace files packed relative to the archive.
                names = zf.namelist()
                assert any(n.startswith("workspace/Requirements/BA-REQ.md") for n in names)
                # Propagation tables present.
                assert len(manifest["artifacts"]) == 6
                assert len(manifest["change_events"]) == 1
                assert len(manifest["propagation_jobs"]) == 5
                assert len(manifest["agent_issues"]) == 1
                assert len(manifest["artifact_traces"]) == 5


class TestRoundTrip:
    def test_mid_propagation_round_trip(self, tmp_db):
        """Export a mid-propagation project, import it, assert state matches."""
        app = _app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as ws:
                pid, event = _mid_propagation_project(app, tmp_db, ws)
                data = build_export(pid)

                import_root = tempfile.mkdtemp()
                result = import_archive(data, workspace_root=import_root)
                assert result["ok"] is True
                new_pid = result["folder_id"]

                # Reconciliation.
                artifacts = db_module.list_artifacts(new_pid)
                assert len(artifacts) == 6
                assert len(db_module.list_artifact_traces(new_pid)) == 5
                events = db_module.list_change_events(new_pid)
                assert len(events) == 1
                assert "REQ-014" in events[0]["changed_reqs"]
                assert len(db_module.list_propagation_jobs(events[0]["id"])) == 5
                assert len(db_module.list_agent_issues(new_pid)) == 1

                # Workspace tree restored.
                project = db_module.get_folder(new_pid)
                ba_path = os.path.join(project["workspace_dir"], "Requirements", "BA-REQ.md")
                assert os.path.isfile(ba_path)
                shutil.rmtree(import_root, ignore_errors=True)


class TestBackwardCompatibility:
    def test_10_archive_still_imports(self, tmp_db):
        """A 1.0 archive (produced by the legacy path) still imports."""
        app = _app(tmp_db)
        with app.app_context():
            folder = db_module.create_folder("Legacy Folder")
            folder_id = folder["id"]
            conv = db_module.create_conversation("Legacy Conv", "model-x", folder_id=folder_id)
            db_module.add_message(conv["id"], "user", "Hello from 1.0")
            db_module.add_message(conv["id"], "assistant", "Hi!")

            # Build a 1.0 manifest + messages payload.
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                manifest = {
                    "export_version": "1.0",
                    "folder": {"id": folder_id, "name": "Legacy Folder"},
                    "conversations": [{
                        "id": conv["id"],
                        "title": "Legacy Conv",
                        "model_id": "model-x",
                        "messages_arc_path": f"conversations/{conv['id']}/messages.json",
                        "uploaded_files": [],
                        "linked_folder_paths": [],
                    }],
                }
                zf.writestr("manifest.json", json.dumps(manifest))
                zf.writestr(
                    f"conversations/{conv['id']}/messages.json",
                    json.dumps(db_module.get_messages(conv["id"])),
                )
            buf.seek(0)

            result = import_archive(buf.read())
            assert result["ok"] is True
            assert result["export_version"] == "1.0"
            new_folder = db_module.get_folder(result["folder_id"])
            assert new_folder["name"] == "Legacy Folder"
            assert new_folder["kind"] == "folder"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])