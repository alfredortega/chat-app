"""Post-BA-chat consolidation: BA-inbox output folded into BA-REQ.md."""

import json
import os
import tempfile
import shutil

import database as db_module
from app import create_app
from propagation.consolidate import consolidate_ba_output


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _project_with_ba_inbox(tmp_db, req_seq=1):
    """Create a project + BA inbox conversation + a BA written requirements doc."""
    app = _app(tmp_db)
    tmp_dir = tempfile.mkdtemp(prefix="baicon_")
    with app.app_context():
        pid = db_module.create_folder("P")["id"]
        db_module.register_project_from_template(pid, "sdlc", tmp_dir)
        ba_conv = db_module.create_conversation("Business Analyst", "", folder_id=pid)
        db_module.update_folder_project(pid, ba_conversation_id=ba_conv["id"], next_req_seq=req_seq)

        # Simulate the BA writing requirement docs via the chat write_file tool.
        req_dir = os.path.join(tmp_dir, "Requirements")
        os.makedirs(req_dir, exist_ok=True)
        doc = (
            "# Spec\n\n"
            "## 1. Authentication\n\n"
            "## 2. Markdown storage\n\n"
        )
        with open(os.path.join(req_dir, "01-requirements-specification.md"), "w", encoding="utf-8") as f:
            f.write(doc)
    return app, pid, ba_conv, tmp_dir


class TestConsolidateBaOutput:
    def test_consolidates_ba_docs_into_ba_req(self, tmp_db):
        app, pid, ba_conv, tmp_dir = _project_with_ba_inbox(tmp_db)
        try:
            with app.app_context():
                result = consolidate_ba_output(ba_conv["id"])
                assert result["consolidated"] is True, result
                assert result["changed"] is True

                from parsers import extract_requirement_ids
                ba_path = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(ba_path, "r", encoding="utf-8") as f:
                    content = f.read()
                reqs = extract_requirement_ids(content)
                assert "REQ-001" in reqs
                assert "REQ-002" in reqs
                assert "## REQ-001 — 1. Authentication" in content
                assert "## REQ-002 — 2. Markdown storage" in content

                # Downstream artifacts were seeded with traces to REQ ids.
                traces = db_module.list_artifact_traces(pid)
                downstream = sorted({t["artifact_key"] for t in traces})
                assert "DB-MODEL" in downstream and "UX-WIRE" in downstream

                # next_req_seq advanced.
                folder = db_module.get_folder(pid)
                assert folder["next_req_seq"] >= 3
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_idempotent_second_run_no_churn(self, tmp_db):
        app, pid, ba_conv, tmp_dir = _project_with_ba_inbox(tmp_db)
        try:
            with app.app_context():
                first = consolidate_ba_output(ba_conv["id"])
                ba_path = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
                with open(ba_path, "r", encoding="utf-8") as f:
                    c1 = f.read()
                second = consolidate_ba_output(ba_conv["id"])
                assert second["consolidated"] is True
                assert second["changed"] is False  # no churn on repeat pass
                with open(ba_path, "r", encoding="utf-8") as f:
                    c2 = f.read()
                assert c1 == c2
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_non_ba_conversation_noop(self, tmp_db):
        app, pid, ba_conv, tmp_dir = _project_with_ba_inbox(tmp_db)
        try:
            with app.app_context():
                other = db_module.create_conversation("Random chat", "")
                result = consolidate_ba_output(other["id"])
                assert result["consolidated"] is False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_locked_local_access_noop(self, tmp_db):
        app, pid, ba_conv, tmp_dir = _project_with_ba_inbox(tmp_db)
        try:
            with app.app_context():
                db_module.set_setting("allow_local_file_access", "0")
                result = consolidate_ba_output(ba_conv["id"])
                assert result["consolidated"] is False
                assert "disabled" in result.get("reason", "").lower()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_no_source_docs_noop(self, tmp_db):
        app, pid, ba_conv, tmp_dir = _project_with_ba_inbox(tmp_db)
        try:
            # Remove the BA-written doc -> nothing to consolidate.
            os.remove(os.path.join(tmp_dir, "Requirements", "01-requirements-specification.md"))
            with app.app_context():
                result = consolidate_ba_output(ba_conv["id"])
                assert result["consolidated"] is False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestEndToEndBAtoAgents:
    """The whole promise: BA chat writes requirement docs -> consolidation ->
    scan detects a change event -> Run propagation writers proposals."""

    @staticmethod
    def _scripted_client():
        from tests.fakes import FakeOpenAIClient

        client = FakeOpenAIClient()
        for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            client.add_tool_calls_response([{
                "name": "write_artifact",
                "arguments": json.dumps({
                    "artifact_key": key,
                    "content": f"# {key}\n\n## placeholder\n\n## S\n{key} body.\n",
                }),
            }])
            client.add_text_response("Done.")
        return client

    def test_consolidate_scan_run_propagation(self, tmp_db, monkeypatch):
        app, pid, ba_conv, tmp_dir = _project_with_ba_inbox(tmp_db)
        try:
            with app.app_context():
                db_module.create_endpoint(
                    "fake", "http://fase.openai.test/v1",
                    api_key="k", default_model="m", is_default=True,
                )

                # 1) BA chat wrote requirement docs; post-chat consolidation folds
                #    them into BA-REQ.md and seeds downstream traces.
                result = consolidate_ba_output(ba_conv["id"])
                assert result["consolidated"] and result["changed"]

                # The project's template provides a git baseline ("Initial
                # project structure"); consolidated BA-REQ.md is currently
                # uncommitted, mirroring a fresh BA chat turn.

            with app.app_context():
                from propagation.scanner import scan_project
                scan = scan_project(pid)
                ba = next(r for r in scan.artifacts if r.artifact_key == "BA-REQ")
                assert ba.status == "modified", ba.status

            monkeypatch.setattr("app.get_client", lambda endpoint: self._scripted_client())
            with app.test_client() as client:
                resp = client.post(f"/api/projects/{pid}/scan")
                assert resp.status_code == 200
                report = resp.get_json()
                assert len(report["change_events"]) >= 1, report

                change_id = report["change_events"][0]["id"]
                resp = client.post(f"/api/projects/{pid}/changes/{change_id}/run-propagation")
                assert resp.status_code == 200, resp.get_data(as_text=True)
                body = resp.get_json()
                assert body.get("started") is True, body

                # The wave runs in a background thread — poll until terminal.
                import time
                deadline = time.time() + 15
                while time.time() < deadline:
                    time.sleep(0.1)
                    with app.app_context():
                        jobs = db_module.list_propagation_jobs(change_id)
                    if jobs and all(j["state"] in (
                            "applied", "proposed", "failed", "needs_input",
                            "cancelled", "completed", "rejected") for j in jobs):
                        break

            with app.app_context():
                from propagation.proposal import list_proposals
                proposals = list_proposals(pid, change_id)
                assert len(proposals) >= 4
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)