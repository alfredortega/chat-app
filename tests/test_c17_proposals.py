import json
import os
import tempfile
import pytest
import database as db_module
from app import create_app
from tests.fakes import FakeOpenAIClient
from propagation.agent import run_propagation_wave
from propagation.proposal import (
    write_proposals,
    list_proposals,
    apply_proposal,
    reject_proposal,
    apply_all,
    rollback_wave,
    change_event_dir,
)
from propagation.impact import queue_propagation_jobs


def _make_app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _rewrite(key, body):
    return f"# {key}\n\n## placeholder\n\n{body}\n"


def _scripted_client():
    client = FakeOpenAIClient()
    for key in ["DB-MODEL", "SEC-RISK", "UX-WIRE", "QA-PLAN", "PM-PLAN"]:
        client.add_tool_calls_response([{
            "name": "write_artifact",
            "arguments": json.dumps({
                "artifact_key": key,
                "content": _rewrite(key, f"## S\n{key} new body."),
            }),
        }])
        client.add_text_response("Done.")
    return client


def _snapshot(workspace):
    files = {}
    for root, _dirs, names in os.walk(workspace):
        if ".git" in root or ".agents" in root:
            continue
        for name in names:
            if name.endswith(".md") or name == ".gitignore":
                path = os.path.join(root, name)
                rel = os.path.relpath(path, workspace)
                with open(path, "rb") as f:
                    files[rel] = f.read()
    return files


def _setup_wave(app, tmp_db, tmp_dir):
    folder = db_module.create_folder("P")
    pid = folder["id"]
    db_module.register_project_from_template(pid, "sdlc", tmp_dir)
    for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
        db_module.create_artifact_trace(pid, key, "REQ-014")
    event = db_module.create_change_event(
        project_id=pid, source_key="BA-REQ",
        from_version=1, to_version=2, changed_reqs=["REQ-014"],
    )
    queue_propagation_jobs(pid, event["id"], ["REQ-014"])
    return pid, event


class TestProposeMode:
    """C17 gate: proposals written, nothing applied, rollback restores files."""

    def test_proposals_written_nothing_applied(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)

                before = _snapshot(tmp_dir)
                payloads = run_propagation_wave(app, pid, event["id"], _scripted_client())
                assert len([p for p in payloads if p.status == "ok"]) == 5

                written = write_proposals(pid, event["id"], payloads)
                assert len(written) == 5

                # Nothing applied to the real workspace.
                after = _snapshot(tmp_dir)
                assert after == before
                # Jobs are 'proposed'.
                states = {j["artifact_key"]: j["state"] for j in db_module.list_propagation_jobs(event["id"])}
                assert all(v == "proposed" for v in states.values())

                proposals = list_proposals(pid, event["id"])
                assert len(proposals) == 5
                assert "DB-MODEL" in {p["artifact_key"] for p in proposals}
                dir_path = change_event_dir(pid, event["id"])
                assert os.path.isdir(dir_path)

    def test_apply_all_then_rollback_byte_identical(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                before = _snapshot(tmp_dir)

                payloads = run_propagation_wave(app, pid, event["id"], _scripted_client())
                write_proposals(pid, event["id"], payloads)

                result = apply_all(pid, event["id"])
                assert len(result["applied"]) == 5
                # Artifact content now changed.
                assert _snapshot(tmp_dir) != before
                # Versions bumped, jobs applied.
                db_model = db_module.get_artifact(pid, "DB-MODEL")
                assert db_model["version"] == 2

                # Rollback -> byte-identical to before.
                rollback = rollback_wave(pid, event["id"])
                assert rollback["ok"] is True
                after_rollback = _snapshot(tmp_dir)
                assert after_rollback == before

    def test_reject_removes_proposal(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                payloads = run_propagation_wave(app, pid, event["id"], _scripted_client())
                write_proposals(pid, event["id"], payloads)

                job = next(j for j in db_module.list_propagation_jobs(event["id"]) if j["artifact_key"] == "UX-WIRE")
                reject_proposal(pid, job["id"])
                proposals = list_proposals(pid, event["id"])
                keys = {p["artifact_key"] for p in proposals}
                assert "UX-WIRE" not in keys
                assert len(keys) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])