import pytest
import threading
import time
import os
from tests.conftest import tmp_db  # noqa: F401
from tests.conftest import client, tmp_db as tmp_db_fixture  # noqa: F401


class TestSQLAlchemyThreadSafety:
    """Tests for SQLAlchemy thread safety with WAL mode and busy_timeout."""

    def test_wal_mode_enabled(self, tmp_db):
        """Verify SQLite WAL mode is enabled."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            # Check journal mode
            result = db_module.db.session.execute(db_module.text("PRAGMA journal_mode")).fetchone()
            assert result[0].upper() == "WAL", f"Expected WAL mode, got {result[0]}"
            
            # Check busy_timeout
            result = db_module.db.session.execute(db_module.text("PRAGMA busy_timeout")).fetchone()
            assert result[0] >= 5000, f"Expected busy_timeout >= 5000, got {result[0]}"

    def test_concurrent_read_write_threads(self, tmp_db):
        """Test 4 threads doing interleaved read/write with zero OperationalError."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        errors = []
        results = {"reads": 0, "writes": 0}
        results_lock = threading.Lock()
        
        def writer_thread(thread_id):
            try:
                with app.app_context():
                    for i in range(10):
                        folder = db_module.create_folder(f"Thread-{thread_id}-Folder-{i}")
                        with results_lock:
                            results["writes"] += 1
                        time.sleep(0.001)  # Small delay to increase contention
            except Exception as e:
                errors.append(f"Writer {thread_id}: {e}")
        
        def reader_thread(thread_id):
            try:
                with app.app_context():
                    for i in range(10):
                        folders = db_module.list_folders()
                        with results_lock:
                            results["reads"] += 1
                        time.sleep(0.001)
            except Exception as e:
                errors.append(f"Reader {thread_id}: {e}")
        
        threads = []
        # 2 writers, 2 readers
        for i in range(2):
            t = threading.Thread(target=writer_thread, args=(i,))
            threads.append(t)
        for i in range(2):
            t = threading.Thread(target=reader_thread, args=(i,))
            threads.append(t)
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Check for OperationalError (database is locked)
        operational_errors = [e for e in errors if "OperationalError" in str(e) or "database is locked" in str(e)]
        assert len(operational_errors) == 0, f"OperationalErrors occurred: {operational_errors}"
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert results["writes"] == 20
        assert results["reads"] == 20

    def test_scoped_session_cleanup(self, tmp_db):
        """Test that scoped sessions are properly removed after job execution."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        # Run multiple "worker-style" jobs
        for job_id in range(5):
            def do_work():
                folder = db_module.create_folder(f"Job-{job_id}")
                return folder["id"]
            
            db_module.run_with_session(app, do_work)
        
        # Verify sessions are cleaned up (no leaked connections)
        # Just verify we can still do operations
        with app.app_context():
            folders = db_module.list_folders()
            assert len(folders) == 5

    def test_run_with_session_helper(self, tmp_db):
        """Test the run_with_session helper function."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        def do_work():
            folder = db_module.create_folder("Helper-Test")
            return folder["id"]
        
        folder_id = db_module.run_with_session(app, do_work)
        assert folder_id > 0
        
        # Verify it persisted
        with app.app_context():
            folder = db_module.get_folder(folder_id)
            assert folder is not None
            assert folder["name"] == "Helper-Test"

    def test_run_with_session_rollback_on_error(self, tmp_db):
        """Test that run_with_session rolls back on error."""
        import database as db_module
        from app import create_app
        from database import Folder
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        def do_work_fail():
            # Create a folder without committing (direct model manipulation)
            now = db_module._now()
            f = Folder(name="Will-Rollback", position=999, created_at=now, updated_at=now)
            db_module.db.session.add(f)
            # Don't commit - let run_with_session handle it
            raise ValueError("Intentional error")
        
        with pytest.raises(ValueError, match="Intentional error"):
            db_module.run_with_session(app, do_work_fail)
        
        # Verify rollback - folder should not exist
        with app.app_context():
            folders = db_module.list_folders()
            names = [f["name"] for f in folders]
            assert "Will-Rollback" not in names


class TestRepairWatermark:
    """Tests for the repair watermark mechanism."""

    def test_repair_watermark_created_on_init(self, tmp_db):
        """Test that repair watermark is created on init_db."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            setting = db_module.db.session.get(db_module.Setting, "tool_call_repair_version")
            assert setting is not None
            assert setting.value == "1"

    def test_should_run_repair_returns_true_when_missing(self, tmp_db):
        """Test should_run_repair returns True when watermark is missing."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            # Remove the watermark
            setting = db_module.db.session.get(db_module.Setting, "tool_call_repair_version")
            if setting:
                db_module.db.session.delete(setting)
                db_module.db.session.commit()
            
            assert db_module.should_run_repair("1") is True

    def test_should_run_repair_returns_false_when_current(self, tmp_db):
        """Test should_run_repair returns False when watermark is current."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            assert db_module.should_run_repair("1") is False

    def test_should_run_repair_returns_true_when_version_changed(self, tmp_db):
        """Test should_run_repair returns True when version changes."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            # Set to old version
            db_module.mark_repair_complete("0")
            assert db_module.should_run_repair("1") is True

    def test_mark_repair_complete_updates_watermark(self, tmp_db):
        """Test mark_repair_complete updates the watermark."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            db_module.mark_repair_complete("2")
            setting = db_module.db.session.get(db_module.Setting, "tool_call_repair_version")
            assert setting.value == "2"
            assert db_module.should_run_repair("2") is False