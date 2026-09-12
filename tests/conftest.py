import os
import tempfile
import pytest
import socket

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Raise an error for any attempted socket network connection."""
    def guarded_connect(*args, **kwargs):
        raise RuntimeError("Network calls are forbidden during testing!")
    def guarded_create_connection(*args, **kwargs):
        raise RuntimeError("Network calls are forbidden during testing!")
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture
def tmp_db():
    """Provide a temporary SQLite database URI and initialize it."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_chat.db")
        db_uri = f"sqlite:///{db_path}"
        
        # Set fixed encryption key before any database operations
        os.environ["ENCRYPTION_KEY"] = "test-encryption-key-must-be-32-bytes-long-!!!!"
        os.environ["SKIP_DOTENV_WRITE"] = "1"
        
        from app import create_app
        import database as db_module
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": db_uri,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            db_module.init_db(app)
            yield db_uri


@pytest.fixture
def workspace():
    """Provide a temporary project workspace directory tree."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create standard project structure
        dirs = [
            "Requirements",
            "Design",
            "Data",
            "Test Cases",
            "Security",
            "Project Plan",
            ".agents",
            ".agents/changes",
            ".agents/proposals",
        ]
        for d in dirs:
            os.makedirs(os.path.join(tmp_dir, d), exist_ok=True)
        yield tmp_dir


@pytest.fixture
def client(tmp_db):
    """Provide a Flask test client backed by the temporary database."""
    from app import create_app
    
    app = create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })
    
    with app.test_client() as test_client:
        yield test_client


class TestFixtures:
    """Self-tests for the C01c fixtures."""
    
    def test_tmp_db_fixture_works(self, tmp_db):
        """Verify tmp_db fixture provides a working database URI."""
        import database as db_module
        from app import create_app
        
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })
        
        with app.app_context():
            # Should be able to query the database
            folders = db_module.list_folders()
            assert isinstance(folders, list)
            
            # Should be able to create a folder
            folder = db_module.create_folder("Test Folder")
            assert folder["name"] == "Test Folder"
            assert folder["id"] > 0
    
    def test_workspace_fixture_creates_structure(self, workspace):
        """Verify workspace fixture creates the expected directory structure."""
        expected_dirs = [
            "Requirements", "Design", "Data", "Test Cases",
            "Security", "Project Plan", ".agents", 
            ".agents/changes", ".agents/proposals"
        ]
        
        for d in expected_dirs:
            path = os.path.join(workspace, d)
            assert os.path.isdir(path), f"Missing directory: {path}"
    
    def test_client_fixture_works(self, client):
        """Verify client fixture provides a working test client."""
        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.get_json()
        assert "output_dir" in data
        assert "browser_root" in data
    
    def test_production_chat_db_untouched(self, tmp_db):
        """Verify production chat.db is not modified by tests."""
        prod_db_path = os.path.join(os.path.dirname(__file__), "..", "chat.db")
        prod_db_path = os.path.normpath(prod_db_path)
        
        if os.path.exists(prod_db_path):
            mtime_before = os.path.getmtime(prod_db_path)
            
            # Use the tmp_db fixture (which runs init_db)
            import database as db_module
            from app import create_app
            
            app = create_app(config={
                "SQLALCHEMY_DATABASE_URI": tmp_db,
                "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
                "SKIP_DOTENV_WRITE": "1"
            })
            
            with app.app_context():
                db_module.create_folder("Another Test")
            
            mtime_after = os.path.getmtime(prod_db_path)
            assert mtime_before == mtime_after, "Production chat.db was modified!"
    
    def test_production_env_untouched(self, tmp_db):
        """Verify production .env is not modified by tests."""
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        env_path = os.path.normpath(env_path)
        
        if os.path.exists(env_path):
            mtime_before = os.path.getmtime(env_path)
            
            # Use the tmp_db fixture
            import database as db_module
            from app import create_app
            
            app = create_app(config={
                "SQLALCHEMY_DATABASE_URI": tmp_db,
                "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
                "SKIP_DOTENV_WRITE": "1"
            })
            
            with app.app_context():
                db_module.create_folder("Yet Another Test")
            
            mtime_after = os.path.getmtime(env_path)
            assert mtime_before == mtime_after, "Production .env was modified!"
    
    def test_legacy_route_exercised(self, client):
        """Verify at least one legacy route works through the factory."""
        # Test /api/settings (already tested in test_client_fixture_works)
        response = client.get("/api/settings")
        assert response.status_code == 200
        
        # Test /api/folders
        response = client.get("/api/folders")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        
        # Test /api/personas
        response = client.get("/api/personas")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        # Should have starter personas seeded
        assert len(data) > 0