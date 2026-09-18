import os
import json
import subprocess
import tempfile
import shutil
import pytest
import database as db_module
from app import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


@pytest.fixture
def project_env(tmp_db, request):
    app = _app(tmp_db)
    tmp_dir = tempfile.mkdtemp()
    request.addfinalizer(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
    with app.app_context():
        pid = db_module.create_folder("SDLC")["id"]
        db_module.register_project_from_template(pid, "sdlc", tmp_dir)
        for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            db_module.create_artifact_trace(pid, key, "REQ-014")
        event = db_module.create_change_event(
            project_id=pid, source_key="BA-REQ",
            from_version=1, to_version=2, changed_reqs=["REQ-014"],
        )
        from propagation.impact import queue_propagation_jobs
        from propagation.proposal import write_proposals
        from propagation.agent import RewritePayload
        queue_propagation_jobs(pid, event["id"], ["REQ-014"])
        jobs = db_module.list_propagation_jobs(event["id"])
        payloads = [
            RewritePayload(
                artifact_key=j["artifact_key"],
                content=f"# {j['artifact_key']}\n\n## placeholder\n\n## S\nReviewed body.\n",
                version=2, status="ok", errors=[],
            )
            for j in jobs
        ]
        write_proposals(pid, event["id"], payloads)
    with app.test_client() as client:
        yield client, pid, event


class TestStaticAssetsExist:
    """The Phase 5 files specified in §5.1/5.2 exist."""

    @pytest.mark.parametrize("path", [
        "static/js/projects.js",
        "static/js/diff_view.js",
        "static/js/documents.js",
        "static/js/markdown_import.js",
    ])
    def test_js_file_exists(self, path):
        assert os.path.isfile(os.path.join(ROOT, path))

    def test_index_html_loads_script_tags(self):
        path = os.path.join(ROOT, "static", "index.html")
        with open(path) as f:
            html = f.read()
        assert "projects.js" in html
        assert "diff_view.js" in html
        assert "documents.js" in html
        assert "markdown_import.js" in html
        assert "easymde" in html or "EasyMDE" in html
        assert "dompurify" in html or "DOMPurify" in html

    def test_index_html_has_panel_and_modals(self):
        path = os.path.join(ROOT, "static", "index.html")
        with open(path) as f:
            html = f.read()
        assert 'id="projectPanelContainer"' in html
        assert 'id="reviewModal"' in html
        assert 'id="conflictModal"' in html
        assert 'id="btnDocuments"' in html
        assert 'id="btnDocumentsOpenFolder"' in html
        assert 'id="documentsModal"' in html
        assert 'id="documentEditorModal"' in html
        assert 'id="docEditorSource"' in html
        assert 'id="docEditorPreview"' in html
        assert 'id="btnDocFullscreen"' in html
        assert 'id="mdImportModal"' in html
        assert 'id="btnMdImportUp"' in html
        assert 'id="btnMdImportNative"' in html

    def test_js_files_parse(self):
        """The new JS modules are syntactically valid (C23)."""
        for rel in ["static/js/projects.js", "static/js/diff_view.js",
                    "static/js/documents.js", "static/js/markdown_import.js",
                    "static/js/api.js", "static/js/conversations.js"]:
            full = os.path.join(ROOT, rel)
            result = subprocess.run(
                ["node", "--check", full],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, f"{rel}: {result.stderr}"

    def test_css_has_status_badges_and_diff_colours(self):
        path = os.path.join(ROOT, "static", "css", "style.css")
        with open(path) as f:
            css = f.read()
        assert ".status-badge" in css
        assert ".status-conflict" in css
        assert ".status-stale" in css
        assert ".diff-add" in css
        assert ".diff-del" in css
        assert ".folder-project-badge" in css
        assert ".doc-editor-split" in css

    def test_check_for_changes_retries_failed_wave(self):
        path = os.path.join(ROOT, "static", "js", "projects.js")
        with open(path) as f:
            js = f.read()
        assert '"failed", "needs_input"' in js
        assert "retryEvent" in js


class TestAPIShapeForUI:
    """The API returns the shapes the project panel / review modal expect."""

    def test_artifacts_include_role(self, tmp_db, project_env):
        client, pid, _event = project_env
        resp = client.get(f"/api/projects/{pid}/artifacts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["artifacts"]) == 6
        for art in data["artifacts"]:
            assert "role" in art
        roles = {a["role"] for a in data["artifacts"]}
        assert "Business Analyst" in roles

    def test_proposals_include_diff(self, tmp_db, project_env):
        client, pid, event = project_env
        resp = client.get(f"/api/projects/{pid}/changes/{event['id']}/proposals")
        assert resp.status_code == 200
        proposals = resp.get_json()["proposals"]
        assert len(proposals) == 5
        for proposal in proposals:
            assert "diff" in proposal
            assert "+" in proposal["diff"]
            assert "-" in proposal["diff"]

    def test_issues_and_assumptions_endpoints(self, tmp_db, project_env):
        client, pid, _event = project_env
        resp = client.get(f"/api/projects/{pid}/issues")
        assert resp.status_code == 200
        resp = client.get(f"/api/projects/{pid}/assumptions")
        assert resp.status_code == 200
        assert "unresolved_count" in resp.get_json()


class TestProjectFolderDistinct:
    """Project folders are visually distinct in the API list (C26)."""

    def test_folder_list_includes_kind(self, tmp_db, project_env):
        client, pid, _event = project_env
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        project = next(p for p in resp.get_json() if p["id"] == pid)
        assert project["kind"] == "project"

    def test_conversations_js_renders_project_badge(self):
        path = os.path.join(ROOT, "static", "js", "conversations.js")
        with open(path) as f:
            js = f.read()
        assert "folder-project-badge" in js
        assert "Project" in js


class TestMarkdownDocumentPanel:
    """Frontend contract for the Markdown document panel + editor (M1/M2)."""

    def test_api_has_document_methods(self):
        path = os.path.join(ROOT, "static", "js", "api.js")
        with open(path) as f:
            js = f.read()
        assert "listDocuments" in js
        assert "getDocument" in js
        assert "saveDocument" in js
        assert "expected_hash" in js
        assert "status === 409" in js or "409" in js

    def test_app_handles_document_created_sse(self):
        path = os.path.join(ROOT, "static", "js", "app.js")
        with open(path) as f:
            js = f.read()
        assert '"document_created"' in js
        assert "MarkdownDocuments.refreshList" in js
        assert "MarkdownDocuments.init" in js

    def test_save_files_refreshes_documents(self):
        path = os.path.join(ROOT, "static", "js", "save_files.js")
        with open(path) as f:
            js = f.read()
        assert "MarkdownDocuments.refreshList" in js

    def test_documents_js_uses_sanitisation_and_conflict_safety(self):
        path = os.path.join(ROOT, "static", "js", "documents.js")
        with open(path) as f:
            js = f.read()
        assert "DOMPurify.sanitize" in js
        assert "rel" in js and "noopener" in js
        assert "expected_hash" in js
        assert "current_content" in js
        assert "reloadServerVersion" in js
        assert "Never overwrite" in js or "not silently overwrite" in js
        # CodeMirror/EasyMDE-enhanced editing with graceful textarea fallback.
        assert "EasyMDE" in js
        assert "toggleFullscreen" in js
        assert "modal-fullscreen" in js
        # No Delete action: deletion is intentionally not implemented yet.
        assert "data-doc-action=\"delete\"" not in js

    def test_documents_sim_harness_passes(self):
        """Run the node DOM-stub simulation of the document panel flows."""
        sim = os.path.join(ROOT, "tests", "frontend", "documents_sim.js")
        result = subprocess.run(
            ["node", sim], capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "All frontend document-panel checks passed." in result.stdout

    def test_markdown_import_sim_harness_passes(self):
        """Run the node DOM-stub simulation of the output-folder import browser."""
        sim = os.path.join(ROOT, "tests", "frontend", "markdown_import_sim.js")
        result = subprocess.run(
            ["node", sim], capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "All markdown-import browser checks passed." in result.stdout

    def test_api_has_import_methods(self):
        path = os.path.join(ROOT, "static", "js", "api.js")
        with open(path) as f:
            js = f.read()
        assert "browseOutputImport" in js
        assert "importMarkdownFromOutput" in js
        assert "import-browse" in js

    def test_import_markdown_opens_output_folder_browser(self):
        path = os.path.join(ROOT, "static", "js", "markdown_import.js")
        with open(path) as f:
            js = f.read()
        assert "browseOutputImport" in js
        assert "mdImportModal" in js
        # Native pickers cannot be pointed at a folder, so browsing is server-side.
        assert "btnMdImportNative" in js

    def test_api_backend_endpoints_present(self, tmp_db):
        from app import create_app
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        import database as db_module
        with app.app_context():
            conv = db_module.create_conversation("C", "m")
        with app.test_client() as client:
            resp = client.get(f"/api/conversations/{conv['id']}/documents")
            assert resp.status_code == 200
            assert "documents" in resp.get_json()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
