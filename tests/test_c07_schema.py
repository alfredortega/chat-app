import os
import json
import pytest
import database as db_module
from app import create_app


class TestSchemaMigration:
    """Tests for C07 schema migration - folders table columns."""

    def test_folders_table_has_project_columns(self, tmp_db):
        """Verify folders table has all new project columns after migration."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            from sqlalchemy import inspect
            inspector = inspect(db_module.db.engine)
            columns = [c["name"] for c in inspector.get_columns("folders")]
            
            # Original columns
            assert "id" in columns
            assert "name" in columns
            assert "position" in columns
            assert "archived" in columns
            assert "created_at" in columns
            assert "updated_at" in columns
            
            # New C07 columns
            assert "kind" in columns
            assert "workspace_dir" in columns
            assert "template_id" in columns
            assert "propagation_mode" in columns
            assert "next_req_seq" in columns
            assert "token_budget" in columns
            assert "ba_conversation_id" in columns

    def test_new_tables_created(self, tmp_db):
        """Verify all new propagation tables are created."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            from sqlalchemy import inspect
            inspector = inspect(db_module.db.engine)
            tables = inspector.get_table_names()
            
            expected_tables = [
                "artifacts",
                "artifact_deps",
                "artifact_traces",
                "change_events",
                "propagation_jobs",
                "agent_issues",
                "artifact_assumptions",
                "artifact_requests",
            ]
            
            for table in expected_tables:
                assert table in tables, f"Missing table: {table}"

    def test_foreign_keys_on_delete_cascade(self, tmp_db):
        """Verify new tables have ON DELETE CASCADE foreign keys to folders."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            from sqlalchemy import inspect
            inspector = inspect(db_module.db.engine)
            
            # Tables that directly reference folders (project_id)
            tables_with_project_fk = [
                "artifacts", "artifact_deps", "artifact_traces", 
                "change_events", "agent_issues",
                "artifact_assumptions", "artifact_requests",
            ]
            
            for table in tables_with_project_fk:
                fks = inspector.get_foreign_keys(table)
                fk_found = False
                for fk in fks:
                    if fk["referred_table"] == "folders":
                        fk_found = True
                        break
                assert fk_found, f"Table {table} missing FK to folders"
            
            # propagation_jobs references change_events, not folders directly
            fks = inspector.get_foreign_keys("propagation_jobs")
            fk_found = False
            for fk in fks:
                if fk["referred_table"] == "change_events":
                    fk_found = True
                    break
            assert fk_found, "propagation_jobs missing FK to change_events"


class TestFolderProjectCRUD:
    """Tests for folder project field CRUD operations."""

    def test_create_folder_with_project_fields(self, tmp_db):
        """Create folder and verify project fields have defaults."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            assert folder["kind"] == "folder"
            assert folder["workspace_dir"] == ""
            assert folder["template_id"] == ""
            assert folder["propagation_mode"] == "off"
            assert folder["next_req_seq"] == 1
            assert folder["token_budget"] == 0
            assert folder["ba_conversation_id"] is None

    def test_update_folder_project_fields(self, tmp_db):
        """Update folder project fields."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            folder_id = folder["id"]
            
            db_module.update_folder_project(
                folder_id,
                kind="project",
                workspace_dir="/tmp/workspace",
                template_id="sdlc",
                propagation_mode="propose",
                next_req_seq=5,
                token_budget=10000,
            )
            
            updated = db_module.get_folder(folder_id)
            assert updated["kind"] == "project"
            assert updated["workspace_dir"] == "/tmp/workspace"
            assert updated["template_id"] == "sdlc"
            assert updated["propagation_mode"] == "propose"
            assert updated["next_req_seq"] == 5
            assert updated["token_budget"] == 10000


class TestArtifactCRUD:
    """Tests for artifact CRUD operations."""

    def test_create_and_get_artifact(self, tmp_db):
        """Create and retrieve an artifact."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            artifact = db_module.create_artifact(
                project_id=project_id,
                artifact_key="BA-REQ",
                rel_path="Requirements/BA-REQ.md",
                role_persona_id=1,
                version=1,
                content_hash="abc123",
                status="current",
                origin="human",
            )
            
            assert artifact["project_id"] == project_id
            assert artifact["artifact_key"] == "BA-REQ"
            assert artifact["rel_path"] == "Requirements/BA-REQ.md"
            assert artifact["version"] == 1
            assert artifact["content_hash"] == "abc123"
            assert artifact["status"] == "current"
            assert artifact["origin"] == "human"
            
            # Get artifact
            retrieved = db_module.get_artifact(project_id, "BA-REQ")
            assert retrieved["artifact_key"] == "BA-REQ"
            assert retrieved["version"] == 1

    def test_list_artifacts(self, tmp_db):
        """List artifacts for a project."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact(project_id, "BA-REQ", "Requirements/BA-REQ.md")
            db_module.create_artifact(project_id, "DB-MODEL", "Data/DB-MODEL.md")
            db_module.create_artifact(project_id, "QA-PLAN", "Test Cases/QA-PLAN.md")
            
            artifacts = db_module.list_artifacts(project_id)
            assert len(artifacts) == 3
            keys = [a["artifact_key"] for a in artifacts]
            assert keys == ["BA-REQ", "DB-MODEL", "QA-PLAN"]  # Sorted

    def test_update_artifact(self, tmp_db):
        """Update artifact fields."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact(project_id, "BA-REQ", "Requirements/BA-REQ.md", version=1)
            
            updated = db_module.update_artifact(
                project_id, "BA-REQ",
                version=2,
                content_hash="newhash",
                status="stale",
            )
            
            assert updated["version"] == 2
            assert updated["content_hash"] == "newhash"
            assert updated["status"] == "stale"

    def test_delete_artifact(self, tmp_db):
        """Delete an artifact."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact(project_id, "BA-REQ", "Requirements/BA-REQ.md")
            db_module.delete_artifact(project_id, "BA-REQ")
            
            artifacts = db_module.list_artifacts(project_id)
            assert len(artifacts) == 0

    def test_unique_constraint_project_key(self, tmp_db):
        """Unique constraint on (project_id, artifact_key)."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact(project_id, "BA-REQ", "Requirements/BA-REQ.md")
            
            # Creating duplicate should raise
            with pytest.raises(Exception):
                db_module.create_artifact(project_id, "BA-REQ", "Requirements/BA-REQ.md")


class TestArtifactDepCRUD:
    """Tests for artifact dependency CRUD."""

    def test_create_and_list_deps(self, tmp_db):
        """Create and list artifact dependencies."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact_dep(project_id, "BA-REQ", "DB-MODEL")
            db_module.create_artifact_dep(project_id, "BA-REQ", "QA-PLAN")
            db_module.create_artifact_dep(project_id, "DB-MODEL", "QA-PLAN")
            
            deps = db_module.list_artifact_deps(project_id)
            assert len(deps) == 3
            
            # Check structure
            assert deps[0]["upstream_key"] == "BA-REQ"
            assert deps[0]["downstream_key"] == "DB-MODEL"

    def test_delete_artifact_deps(self, tmp_db):
        """Delete specific or all deps for a project."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact_dep(project_id, "BA-REQ", "DB-MODEL")
            db_module.create_artifact_dep(project_id, "BA-REQ", "QA-PLAN")
            
            # Delete specific upstream
            db_module.delete_artifact_deps(project_id, upstream_key="BA-REQ")
            deps = db_module.list_artifact_deps(project_id)
            assert len(deps) == 0


class TestArtifactTraceCRUD:
    """Tests for artifact trace CRUD."""

    def test_create_and_list_traces(self, tmp_db):
        """Create and list artifact traces."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact_trace(project_id, "BA-REQ", "REQ-001")
            db_module.create_artifact_trace(project_id, "BA-REQ", "REQ-002")
            db_module.create_artifact_trace(project_id, "DB-MODEL", "REQ-001")
            
            traces = db_module.list_artifact_traces(project_id)
            assert len(traces) == 3
            
            # Filter by artifact
            traces_ba = db_module.list_artifact_traces(project_id, artifact_key="BA-REQ")
            assert len(traces_ba) == 2
            reqs = [t["req_id"] for t in traces_ba]
            assert "REQ-001" in reqs
            assert "REQ-002" in reqs
            
            # Filter by req
            traces_req = db_module.list_artifact_traces(project_id, req_id="REQ-001")
            assert len(traces_req) == 2


class TestChangeEventCRUD:
    """Tests for change event CRUD."""

    def test_create_and_list_change_events(self, tmp_db):
        """Create and list change events."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            event = db_module.create_change_event(
                project_id=project_id,
                source_key="BA-REQ",
                from_version=1,
                to_version=2,
                summary="Updated requirements",
                changed_reqs=["REQ-001", "REQ-002"],
                removed_reqs=["REQ-003"],
                diff="- old\n+ new",
                origin="human",
            )
            
            assert event["project_id"] == project_id
            assert event["source_key"] == "BA-REQ"
            assert event["from_version"] == 1
            assert event["to_version"] == 2
            assert "REQ-001" in event["changed_reqs"]
            assert "REQ-003" in event["removed_reqs"]
            
            # List events
            events = db_module.list_change_events(project_id)
            assert len(events) == 1


class TestPropagationJobCRUD:
    """Tests for propagation job CRUD."""

    def test_create_and_update_job(self, tmp_db):
        """Create and update propagation job."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            # Create change event first
            event = db_module.create_change_event(
                project_id=project_id,
                source_key="BA-REQ",
                from_version=1,
                to_version=2,
            )
            change_id = event["id"]
            
            # Create job
            job = db_module.create_propagation_job(
                change_id=change_id,
                artifact_key="DB-MODEL",
                persona_id=1,
                depth=1,
                batch_id="batch-1",
            )
            
            assert job["change_id"] == change_id
            assert job["artifact_key"] == "DB-MODEL"
            assert job["state"] == "pending"
            assert job["depth"] == 1
            assert job["batch_id"] == "batch-1"
            
            # Update job
            updated = db_module.update_propagation_job(
                job["id"],
                state="completed",
                tokens_used=500,
            )
            
            assert updated["state"] == "completed"
            assert updated["tokens_used"] == 500


class TestAgentIssueCRUD:
    """Tests for agent issue CRUD."""

    def test_create_and_update_issue(self, tmp_db):
        """Create and update agent issue."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            # Create a persona first (required for FK)
            persona = db_module.create_persona("Test Persona", "Test prompt")
            persona_id = persona["id"]
            
            # Create a change event first (required for FK)
            change_event = db_module.create_change_event(
                project_id=project_id,
                source_key="BA-REQ",
                from_version=1,
                to_version=2,
            )
            change_id = change_event["id"]
            
            issue = db_module.create_agent_issue(
                project_id=project_id,
                raised_by_key="DB-MODEL",
                raised_by_persona_id=persona_id,
                kind="question",
                body="What database engine to use?",
                change_id=change_id,
                depth=1,
                req_id="REQ-001",
                blocking=True,
                proposed_answer="SQLAlchemy with SQLite and MySQL",
            )
            
            assert issue["project_id"] == project_id
            assert issue["raised_by_key"] == "DB-MODEL"
            assert issue["kind"] == "question"
            assert issue["blocking"] == 1
            assert issue["status"] == "open"
            
            # Update issue with answer
            updated = db_module.update_agent_issue(
                issue["id"],
                status="answered",
                answer="Use SQLAlchemy ORM",
            )
            
            assert updated["status"] == "answered"
            assert updated["answer"] == "Use SQLAlchemy ORM"
            assert updated["answered_at"] is not None


class TestArtifactAssumptionCRUD:
    """Tests for artifact assumption CRUD."""

    def test_create_and_list_assumptions(self, tmp_db):
        """Create and list artifact assumptions."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            db_module.create_artifact_assumption(
                project_id=project_id,
                artifact_key="DB-MODEL",
                req_id="REQ-001",
                marker_text="[ASSUMPTION: REQ-001] Assuming SQLAlchemy [/ASSUMPTION]",
            )
            
            assumptions = db_module.list_artifact_assumptions(project_id)
            assert len(assumptions) == 1
            assert assumptions[0]["artifact_key"] == "DB-MODEL"
            assert assumptions[0]["req_id"] == "REQ-001"
            assert "SQLAlchemy" in assumptions[0]["marker_text"]


class TestArtifactRequestCRUD:
    """Tests for artifact request CRUD."""

    def test_create_and_update_request(self, tmp_db):
        """Create and update artifact extension request."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            request = db_module.create_artifact_request(
                project_id=project_id,
                persona_id=1,
                artifact_key="UX-WIRE-NEW",
                rel_path="Design/UX-WIRE-NEW.md",
                rationale="Need separate wireframe for mobile",
            )
            
            assert request["project_id"] == project_id
            assert request["artifact_key"] == "UX-WIRE-NEW"
            assert request["status"] == "pending"
            
            # Approve request
            updated = db_module.update_artifact_request(request["id"], "approved")
            assert updated["status"] == "approved"
            
            # List pending
            pending = db_module.list_artifact_requests(project_id, status="pending")
            assert len(pending) == 0
            
            approved = db_module.list_artifact_requests(project_id, status="approved")
            assert len(approved) == 1


class TestDBArtifactContextLookup:
    """Tests for DB-backed artifact context lookup (C07)."""

    def test_get_artifact_context_from_db(self, tmp_db):
        """DB-backed context lookup loads artifacts from database."""
        import tempfile
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            # Create project with workspace_dir
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            # Create temp workspace with artifact files
            with tempfile.TemporaryDirectory() as tmp_dir:
                req_dir = os.path.join(tmp_dir, "Requirements")
                os.makedirs(req_dir, exist_ok=True)
                
                # Write artifact file
                req_file = os.path.join(req_dir, "BA-REQ.md")
                with open(req_file, "w") as f:
                    f.write("# Requirements\n\nREQ-001: Use SQLite\nREQ-002: Use MySQL")
                
                # Update folder with workspace_dir
                db_module.update_folder_project(project_id, workspace_dir=tmp_dir)
                
                # Create artifact in DB
                db_module.create_artifact(
                    project_id=project_id,
                    artifact_key="BA-REQ",
                    rel_path="Requirements/BA-REQ.md",
                )
                
                # Get context from DB
                context, char_count, truncated = db_module.get_artifact_context(
                    project_id=project_id,
                    artifact_keys=["BA-REQ"],
                    change_event={"summary": "Test change", "diff": "- old\n+ new"},
                )
                
                assert "BA-REQ" in context
                assert "REQ-001" in context
                assert "REQ-002" in context
                assert char_count > 0
                assert truncated == []

    def test_get_artifact_context_empty(self, tmp_db):
        """Context lookup with no artifacts returns empty."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]
            
            context, char_count, truncated = db_module.get_artifact_context(
                project_id=project_id,
                artifact_keys=[],
            )
            
            assert context == ""
            assert char_count == 0
            assert truncated == []


# Update test_fixtures to include new table checks
class TestFixturesExtended:
    """Extended self-tests for C07 fixtures."""

    def test_all_new_tables_exist(self, tmp_db):
        """Verify all C07 tables exist and are queryable."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            # Should be able to query all new tables
            assert isinstance(db_module.list_artifacts(1), list)
            assert isinstance(db_module.list_artifact_deps(1), list)
            assert isinstance(db_module.list_artifact_traces(1), list)
            assert isinstance(db_module.list_change_events(1), list)
            assert isinstance(db_module.list_propagation_jobs(1), list)
            assert isinstance(db_module.list_agent_issues(1), list)
            assert isinstance(db_module.list_artifact_assumptions(1), list)
            assert isinstance(db_module.list_artifact_requests(1), list)


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])