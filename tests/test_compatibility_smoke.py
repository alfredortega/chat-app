import os
import pytest
import tempfile
from app import create_app
import database as db

def test_legacy_compatibility_smoke():
    """Verify that create_app successfully runs without loading .env or writing to chat.db."""
    # Ensure any attempts to write .env or modify production database are disabled
    os.environ["SKIP_DOTENV"] = "1"
    os.environ["SKIP_DOTENV_WRITE"] = "1"
    os.environ["ENCRYPTION_KEY"] = "test-encryption-key-must-be-32-bytes-long-!!!!"

    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db_path = os.path.join(tmp_dir, "test_chat.db")
        config = {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{test_db_path}",
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        }

        # Create the app through factory
        app = create_app(config=config)
        
        # Verify app creation and configuration
        assert app.config["SQLALCHEMY_DATABASE_URI"] == f"sqlite:///{test_db_path}"
        
        # Test one existing route (e.g. settings or static home)
        with app.test_client() as client:
            response = client.get("/api/settings")
            assert response.status_code == 200
            data = response.get_json()
            assert "output_dir" in data

def test_imports_clean():
    """Assert main modules import without side effects."""
    import database
    import file_handler
    import tools
    import app
    assert hasattr(database, "init_db")
    assert hasattr(file_handler, "build_file_context")
    assert hasattr(tools, "execute_tool_call")
    assert hasattr(app, "create_app")
