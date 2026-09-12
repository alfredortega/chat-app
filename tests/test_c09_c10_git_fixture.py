import os
import tempfile
import pytest
import database as db_module
from app import create_app
from git_integration import (
    check_git_available,
    ensure_git_available,
    git_init,
    git_status,
    git_add,
    git_commit,
    git_commit_all,
    git_diff,
    git_log,
    git_reset_hard,
    git_checkout,
    git_get_head_commit,
    git_list_files,
    git_is_repo,
    init_project_workspace,
    create_wave_commit,
    rollback_to_commit,
    rollback_file,
    get_wave_history,
    GitNotAvailableError,
)


class TestGitAvailability:
    """Tests for git availability checks."""

    def test_check_git_available(self):
        """Git should be available in test environment."""
        assert check_git_available() is True

    def test_ensure_git_available(self):
        """Ensure git available should not raise in test env."""
        ensure_git_available()  # Should not raise


class TestGitOperations:
    """Tests for basic git operations."""

    def test_git_init_and_status(self, tmp_path):
        """Initialize repo and check status."""
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        result = git_init(str(repo_dir))
        assert result.success is True

        status = git_status(str(repo_dir))
        assert status.success is True

    def test_git_commit_all(self, tmp_path):
        """Commit all changes."""
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        git_init(str(repo_dir))

        # Create a file
        test_file = repo_dir / "test.txt"
        test_file.write_text("Hello, World!")

        result = git_commit_all(str(repo_dir), "Initial commit")
        assert result.success is True

        # Verify commit
        log = git_log(str(repo_dir), max_count=1)
        assert log.success is True
        assert "Initial commit" in log.output

    def test_git_diff(self, tmp_path):
        """Show diff of changes."""
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        git_init(str(repo_dir))
        test_file = repo_dir / "test.txt"
        test_file.write_text("Original")
        git_commit_all(str(repo_dir), "Initial")

        # Modify file
        test_file.write_text("Modified")

        diff = git_diff(str(repo_dir))
        assert diff.success is True
        assert "Original" in diff.output or "Modified" in diff.output

    def test_git_reset_hard(self, tmp_path):
        """Hard reset to previous commit."""
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        git_init(str(repo_dir))
        test_file = repo_dir / "test.txt"
        test_file.write_text("Version 1")
        git_commit_all(str(repo_dir), "Commit 1")

        test_file.write_text("Version 2")
        git_commit_all(str(repo_dir), "Commit 2")

        # Get first commit hash
        log = git_log(str(repo_dir), max_count=2)
        lines = log.output.strip().split("\n")
        first_commit = lines[1].split(" ")[0]  # Second line is first commit

        # Reset to first commit
        result = git_reset_hard(str(repo_dir), first_commit)
        assert result.success is True

        # Verify file content
        assert test_file.read_text() == "Version 1"

    def test_git_checkout_file(self, tmp_path):
        """Checkout a single file from a commit."""
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        git_init(str(repo_dir))
        test_file = repo_dir / "test.txt"
        test_file.write_text("Version 1")
        git_commit_all(str(repo_dir), "Commit 1")

        test_file.write_text("Version 2")
        git_commit_all(str(repo_dir), "Commit 2")

        # Get first commit hash
        log = git_log(str(repo_dir), max_count=2)
        lines = log.output.strip().split("\n")
        first_commit = lines[1].split(" ")[0]

        # Checkout file from first commit
        result = git_checkout(str(repo_dir), first_commit, "test.txt")
        assert result.success is True

        assert test_file.read_text() == "Version 1"


class TestProjectWorkspace:
    """Tests for project workspace initialization."""

    def test_init_project_workspace(self, tmp_path):
        """Initialize a complete project workspace."""
        workspace_dir = tmp_path / "my_project"

        result = init_project_workspace(str(workspace_dir))
        assert result.success is True

        # Check structure
        assert (workspace_dir / ".git").is_dir()
        assert (workspace_dir / "Requirements").is_dir()
        assert (workspace_dir / "Design").is_dir()
        assert (workspace_dir / "Data").is_dir()
        assert (workspace_dir / "Test Cases").is_dir()
        assert (workspace_dir / "Security").is_dir()
        assert (workspace_dir / "Project Plan").is_dir()
        assert (workspace_dir / ".agents" / "changes").is_dir()
        assert (workspace_dir / ".agents" / "proposals").is_dir()
        assert (workspace_dir / ".gitignore").is_file()

        # Check initial commit
        log = git_log(str(workspace_dir), max_count=1)
        assert "Initial project structure" in log.output

    def test_create_wave_commit(self, tmp_path):
        """Create a propagation wave commit."""
        workspace_dir = tmp_path / "my_project"
        init_project_workspace(str(workspace_dir))

        # Modify a file
        req_file = workspace_dir / "Requirements" / "BA-REQ.md"
        req_file.write_text("# Requirements\n\nREQ-001: Test")

        result = create_wave_commit(str(workspace_dir), "Added REQ-001")
        assert result.success is True

        log = git_log(str(workspace_dir), max_count=1)
        assert "Propagation wave" in log.output

    def test_rollback_to_commit(self, tmp_path):
        """Rollback workspace to a previous commit."""
        workspace_dir = tmp_path / "my_project"
        init_project_workspace(str(workspace_dir))

        # Make two changes
        req_file = workspace_dir / "Requirements" / "BA-REQ.md"
        req_file.write_text("# Requirements\n\nREQ-001: First")
        create_wave_commit(str(workspace_dir), "First change")

        req_file.write_text("# Requirements\n\nREQ-001: Second")
        create_wave_commit(str(workspace_dir), "Second change")

        # Get first wave commit
        waves = get_wave_history(str(workspace_dir))
        assert len(waves) >= 2

        # Rollback to first wave
        first_wave_commit = waves[1]["commit"]
        result = rollback_to_commit(str(workspace_dir), first_wave_commit)
        assert result.success is True

        assert "First" in req_file.read_text()

    def test_rollback_file(self, tmp_path):
        """Rollback a single file."""
        workspace_dir = tmp_path / "my_project"
        init_project_workspace(str(workspace_dir))

        req_file = workspace_dir / "Requirements" / "BA-REQ.md"
        req_file.write_text("# Requirements\n\nREQ-001: First")
        create_wave_commit(str(workspace_dir), "First change")

        req_file.write_text("# Requirements\n\nREQ-001: Second")
        create_wave_commit(str(workspace_dir), "Second change")

        # Get first wave commit
        waves = get_wave_history(str(workspace_dir))
        first_wave_commit = waves[1]["commit"]

        # Rollback just the requirements file
        result = rollback_file(str(workspace_dir), first_wave_commit, "Requirements/BA-REQ.md")
        assert result.success is True

        assert "First" in req_file.read_text()


class TestGitIntegrationWithDatabase:
    """Tests integrating git operations with database."""

    def test_register_project_creates_git_repo(self, tmp_db):
        """Register project should initialize git repo."""
        import tempfile
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Git Project")
                project_id = folder["id"]

                result = db_module.register_project_from_template(
                    project_id=project_id,
                    template_id="sdlc",
                    workspace_dir=tmp_dir,
                )

                assert result["project_id"] == project_id
                assert result["artifacts_registered"] == 6

                # Check git repo was created
                from git_integration import git_is_repo, git_log
                assert git_is_repo(tmp_dir)
                log = git_log(tmp_dir, max_count=1)
                assert "Initial project structure" in log.output


class TestCascadeCleanup:
    """Tests for cascade/lifecycle cleanup (C09)."""

    def test_cleanup_on_folder_delete(self, tmp_db):
        """Cleanup on folder delete preserves workspace."""
        import tempfile
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                result = db_module.cleanup_on_folder_delete(project_id, tmp_dir)

                assert result["project_id"] == project_id
                assert result["workspace_preserved"] is True
                assert len(result["warnings"]) > 0
                # The warning should indicate workspace is preserved
                assert "preserved" in result["warnings"][0].lower() or "not be deleted" in result["warnings"][0].lower()

    def test_cleanup_on_purge(self):
        """Cleanup on purge returns preservation message."""
        result = db_module.cleanup_on_purge()
        assert "preserved" in result["message"].lower()
        assert "artifacts" in str(result["preserved"])

    def test_verify_git_on_startup(self):
        """Verify git on startup returns available status."""
        available, message = db_module.verify_git_on_startup()
        assert available is True
        assert "Git available" in message


class TestFixtureLoading:
    """Tests for loading the sqlite-to-mysql fixture."""

    def test_before_fixture_exists(self):
        """Before fixture directory exists with all artifacts."""
        base = "/home/alfred/Dev/chat-app/examples/sqlite-to-mysql/before"
        
        assert os.path.exists(os.path.join(base, "project.yaml"))
        assert os.path.exists(os.path.join(base, "Requirements", "BA-REQ.md"))
        assert os.path.exists(os.path.join(base, "Design", "UX-WIRE.md"))
        assert os.path.exists(os.path.join(base, "Data", "DB-MODEL.md"))
        assert os.path.exists(os.path.join(base, "Test Cases", "QA-PLAN.md"))
        assert os.path.exists(os.path.join(base, "Security", "SEC-RISK.md"))
        assert os.path.exists(os.path.join(base, "Project Plan", "PM-PLAN.md"))

    def test_after_fixture_exists(self):
        """After fixture directory exists with all artifacts."""
        base = "/home/alfred/Dev/chat-app/examples/sqlite-to-mysql/after"
        
        assert os.path.exists(os.path.join(base, "project.yaml"))
        assert os.path.exists(os.path.join(base, "Requirements", "BA-REQ.md"))
        assert os.path.exists(os.path.join(base, "Design", "UX-WIRE.md"))
        assert os.path.exists(os.path.join(base, "Data", "DB-MODEL.md"))
        assert os.path.exists(os.path.join(base, "Test Cases", "QA-PLAN.md"))
        assert os.path.exists(os.path.join(base, "Security", "SEC-RISK.md"))
        assert os.path.exists(os.path.join(base, "Project Plan", "PM-PLAN.md"))

    def test_fixture_versions_differ(self):
        """Before and after artifacts have different versions."""
        from parsers import parse_front_matter

        base = "/home/alfred/Dev/chat-app/examples/sqlite-to-mysql"
        
        # Check BA-REQ version
        with open(os.path.join(base, "before", "Requirements", "BA-REQ.md")) as f:
            before_fm, _ = parse_front_matter(f.read())
        
        with open(os.path.join(base, "after", "Requirements", "BA-REQ.md")) as f:
            after_fm, _ = parse_front_matter(f.read())
        
        assert before_fm["version"] == 2
        assert after_fm["version"] == 3

        # Check DB-MODEL version
        with open(os.path.join(base, "before", "Data", "DB-MODEL.md")) as f:
            before_fm, _ = parse_front_matter(f.read())
        
        with open(os.path.join(base, "after", "Data", "DB-MODEL.md")) as f:
            after_fm, _ = parse_front_matter(f.read())
        
        assert before_fm["version"] == 1
        assert after_fm["version"] == 2

    def test_fixture_requirement_changed(self):
        """REQ-002 changed from single-engine to dual-engine."""
        base = "/home/alfred/Dev/chat-app/examples/sqlite-to-mysql"
        
        with open(os.path.join(base, "before", "Requirements", "BA-REQ.md")) as f:
            before_content = f.read()
        
        with open(os.path.join(base, "after", "Requirements", "BA-REQ.md")) as f:
            after_content = f.read()
        
        # Before: single engine
        assert "Single Database Engine" in before_content
        assert "No other database engines" in before_content
        
        # After: dual engine
        assert "SQLite and MySQL" in after_content
        assert "SQLAlchemy ORM" in after_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])