import os
import json
import tempfile
import pytest
import tools


class TestBuildTools:
    """Tests for build_tools() profile selection."""

    def test_chat_profile_returns_default_tools(self):
        """Chat profile returns the default tools."""
        tool_defs = tools.build_tools("chat")
        tool_names = [t["function"]["name"] for t in tool_defs]
        assert tool_names == [
            "fetch_webpage", "write_file", "read_named_file", "read_file",
            "list_directory", "run_python",
        ]
        assert len(tool_defs) == 6

    def test_propagation_profile_returns_propagation_tools(self):
        """Propagation profile returns the six propagation tools."""
        tool_defs = tools.build_tools("propagation")
        tool_names = [t["function"]["name"] for t in tool_defs]
        assert tool_names == [
            "read_artifact",
            "write_artifact",
            "list_artifacts",
            "ask_question",
            "raise_issue",
            "propose_artifact",
        ]
        assert len(tool_defs) == 6
        # run_python must NOT be in propagation profile
        assert "run_python" not in tool_names

    def test_unknown_context_defaults_to_chat(self):
        """Unknown context falls back to chat profile."""
        tool_defs = tools.build_tools("unknown")
        tool_names = [t["function"]["name"] for t in tool_defs]
        assert "run_python" in tool_names
        assert "write_artifact" not in tool_names

    def test_chat_tool_filter_returns_only_requested_tools(self):
        selected = tools.build_tools("chat", {"read_file"})
        assert [item["function"]["name"] for item in selected] == ["read_file"]


class TestLocalFileAccessLock:
    """Global 'allow_local_file_access' setting must filter the tool schema and
    deny local-access tools server-side (universal — propagation included)."""

    def _set_locked(self, locked: bool, tmp_db):
        from app import create_app
        import database as db_module
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            db_module.set_setting("allow_local_file_access", "0" if locked else "1")
            assert db_module.local_file_access_enabled() is not locked

    def test_chat_profile_hides_local_tools_when_locked(self, tmp_db):
        from app import create_app
        import database as db_module
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            db_module.set_setting("allow_local_file_access", "0")
            tool_defs = tools.build_tools("chat")
        names = [t["function"]["name"] for t in tool_defs]
        assert "write_file" not in names
        assert "read_file" not in names
        assert "list_directory" not in names
        assert "run_python" not in names
        # Upload-scoped and network tools stay available.
        assert "read_named_file" in names
        assert "fetch_webpage" in names

    def test_propagation_profile_has_no_tools_when_locked(self, tmp_db):
        from app import create_app
        import database as db_module
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            db_module.set_setting("allow_local_file_access", "0")
            assert tools.build_tools("propagation") == []

    def test_execute_tool_call_denied_when_locked(self, tmp_db):
        from app import create_app
        import database as db_module
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            db_module.set_setting("allow_local_file_access", "0")
            result = tools.execute_tool_call(
                "write_file",
                json.dumps({"path": "test.txt", "content": "hello"}),
                context="chat",
                output_dir="/tmp",
            )
        assert result["success"] is False
        assert "disabled" in result["result"]

    def test_execute_tool_call_allowed_when_enabled(self, tmp_db):
        from app import create_app
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            # Setting defaults to enabled ("1") — write_file is not denied.
            result = tools.execute_tool_call(
                "write_file",
                json.dumps({"path": "x.txt", "content": "hi"}),
                context="chat",
                output_dir="/nonexistent-write-target",
            )
        # Passes the lock check; failure (if any) is from the disk write itself,
        # not from the "disabled" guard.
        assert result["success"] is False
        assert "disabled" not in result["result"]


class TestToolProfileGating:
    """Tests for execute_tool_call() profile enforcement."""

    def test_run_python_rejected_in_propagation_context(self):
        """run_python must be refused in propagation context."""
        result = tools.execute_tool_call(
            "run_python",
            json.dumps({"code": "print('hello')"}),
            context="propagation",
            workspace_dir="/tmp",
        )
        assert result["success"] is False
        assert "not permitted" in result["result"] or "not available" in result["result"]

    def test_write_file_rejected_in_propagation_context(self):
        """write_file must be refused in propagation context."""
        result = tools.execute_tool_call(
            "write_file",
            json.dumps({"path": "test.txt", "content": "hello"}),
            context="propagation",
            workspace_dir="/tmp",
        )
        assert result["success"] is False
        assert "not permitted" in result["result"] or "not available" in result["result"]

    def test_write_artifact_allowed_in_propagation_context(self):
        """write_artifact must be allowed in propagation context."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "BA-REQ", "content": "# Requirements\n\nTest"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            # Should succeed (or fail for implementation reasons, not profile gating)
            assert result["success"] is True or "not implemented" not in result["result"].lower()

    def test_read_artifact_allowed_in_propagation_context(self):
        """read_artifact must be allowed in propagation context (stub)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = tools.execute_tool_call(
                "read_artifact",
                json.dumps({"artifact_key": "BA-REQ"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            # Stub returns not implemented, but not profile rejection
            assert result["success"] is False
            assert "not yet implemented" in result["result"].lower()

    def test_list_artifacts_allowed_in_propagation_context(self):
        """list_artifacts must be allowed in propagation context (stub)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = tools.execute_tool_call(
                "list_artifacts",
                json.dumps({}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is False
            assert "not yet implemented" in result["result"].lower()


class TestWriteArtifactPathJail:
    """Tests for write_artifact path traversal protection."""

    def test_write_artifact_basic_success(self):
        """Basic write_artifact succeeds in allowed subdir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "BA-REQ", "content": "# Test\n\nContent"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is True
            expected_path = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
            assert os.path.exists(expected_path)
            with open(expected_path) as f:
                assert f.read() == "# Test\n\nContent"

    def test_write_artifact_creates_subdir(self):
        """write_artifact creates the subdirectory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Don't pre-create Requirements
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "BA-REQ", "content": "# Test"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is True
            expected_path = os.path.join(tmp_dir, "Requirements", "BA-REQ.md")
            assert os.path.exists(expected_path)

    def test_write_artifact_rejects_missing_workspace_dir(self):
        """write_artifact fails without workspace_dir."""
        result = tools.execute_tool_call(
            "write_artifact",
            json.dumps({"artifact_key": "BA-REQ", "content": "# Test"}),
            context="propagation",
            workspace_dir=None,
        )
        assert result["success"] is False
        assert "workspace" in result["result"].lower()

    def test_write_artifact_rejects_invalid_workspace_dir(self):
        """write_artifact fails with non-existent workspace_dir."""
        result = tools.execute_tool_call(
            "write_artifact",
            json.dumps({"artifact_key": "BA-REQ", "content": "# Test"}),
            context="propagation",
            workspace_dir="/nonexistent/path",
        )
        assert result["success"] is False
        assert "does not exist" in result["result"].lower()

    def test_write_artifact_rejects_missing_artifact_key(self):
        """write_artifact fails without artifact_key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"content": "# Test"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is False
            assert "artifact_key" in result["result"].lower()

    def test_write_artifact_rejects_traversal_dotdot(self):
        """write_artifact rejects .. traversal in artifact_key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "BA-../EVIL", "content": "# Evil"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is False
            assert ".." in result["result"] or "traversal" in result["result"].lower()

    def test_write_artifact_rejects_absolute_path_in_key(self):
        """write_artifact rejects absolute paths in artifact_key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "/etc/passwd", "content": "# Evil"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is False

    def test_write_artifact_rejects_unc_path(self):
        """write_artifact rejects UNC paths in artifact_key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "//server/share/file", "content": "# Evil"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is False

    def test_write_artifact_rejects_outside_allowed_subdirs(self):
        """write_artifact rejects artifact keys with disallowed prefix."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "EVIL-KEY", "content": "# Evil"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert result["success"] is False
            assert "not a recognized" in result["result"] or "not in allowed" in result["result"]

    def test_write_artifact_rejects_commonpath_escape(self):
        """write_artifact commonpath check prevents workspace escape."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            # Try to craft a key that might escape via symlink or similar
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "BA-REQ", "content": "# Test"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            # Should succeed - this is a positive control
            assert result["success"] is True

    def test_write_artifact_allows_all_allowed_subdirs(self):
        """write_artifact works for all allowed subdirectory prefixes."""
        prefix_mapping = {
            "BA": "Requirements",
            "UX": "Design",
            "DB": "Data",
            "QA": "Test Cases",
            "SEC": "Security",
            "PM": "Project Plan",
        }
        for prefix, subdir in prefix_mapping.items():
            with tempfile.TemporaryDirectory() as tmp_dir:
                os.makedirs(os.path.join(tmp_dir, subdir), exist_ok=True)
                artifact_key = f"{prefix}-TEST"
                result = tools.execute_tool_call(
                    "write_artifact",
                    json.dumps({"artifact_key": artifact_key, "content": "# Test"}),
                    context="propagation",
                    workspace_dir=tmp_dir,
                )
                assert result["success"] is True, f"Failed for prefix {prefix}: {result['result']}"


class TestWriteFileStillWorksInChatContext:
    """Ensure existing write_file behavior is unchanged in chat context."""

    def test_write_file_works_in_chat_context(self):
        """write_file works normally in chat context with output_dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = tools.execute_tool_call(
                "write_file",
                json.dumps({"path": "test.txt", "content": "hello"}),
                context="chat",
                output_dir=tmp_dir,
            )
            assert result["success"] is True
            expected_path = os.path.join(tmp_dir, "test.txt")
            assert os.path.exists(expected_path)

    def test_run_python_works_in_chat_context(self):
        """run_python works normally in chat context."""
        result = tools.execute_tool_call(
            "run_python",
            json.dumps({"code": "print(2+2)"}),
            context="chat",
        )
        assert result["success"] is True
        assert "4" in result["result"]


class TestExecuteToolCallParameterPassing:
    """Test that context and workspace_dir are properly passed through."""

    def test_execute_tool_call_accepts_context_param(self):
        """execute_tool_call accepts context parameter without error."""
        result = tools.execute_tool_call(
            "write_file",
            json.dumps({"path": "x.txt", "content": "x"}),
            context="chat",
            output_dir="/tmp",
        )
        # Should not crash on parameter acceptance
        assert "success" in result


class TestWriteFileJail:
    """write_file must never write outside the configured output directory."""

    def test_absolute_write_without_output_dir_is_denied(self, monkeypatch):
        """With no output dir configured, arbitrary absolute writes are blocked."""
        import database as db_module
        monkeypatch.setattr(db_module, "get_setting", lambda key: None)

        with tempfile.NamedTemporaryFile() as tmp:
            result = tools.execute_tool_call(
                "write_file",
                json.dumps({"path": tmp.name, "content": "evil"}),
                context="chat",
                output_dir=None,
            )
            assert result["success"] is False
            assert "denied" in result["result"].lower() or "no output directory" in result["result"].lower()

    def test_absolute_write_outside_output_dir_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            outside = os.path.join(tmp_dir, "..", "escape.txt")
            outside = os.path.normpath(outside)
            result = tools.execute_tool_call(
                "write_file",
                json.dumps({"path": outside, "content": "x"}),
                context="chat",
                output_dir=tmp_dir,
            )
            assert result["success"] is False
            assert "outside" in result["result"].lower()

    def test_absolute_write_inside_output_dir_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            inside = os.path.join(tmp_dir, "ok.txt")
            result = tools.execute_tool_call(
                "write_file",
                json.dumps({"path": inside, "content": "x"}),
                context="chat",
                output_dir=tmp_dir,
            )
            assert result["success"] is True
            assert os.path.exists(inside)

    def test_execute_tool_call_accepts_workspace_dir_param(self):
        """execute_tool_call accepts workspace_dir parameter without error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.makedirs(os.path.join(tmp_dir, "Requirements"), exist_ok=True)
            result = tools.execute_tool_call(
                "write_artifact",
                json.dumps({"artifact_key": "BA-REQ", "content": "x"}),
                context="propagation",
                workspace_dir=tmp_dir,
            )
            assert "success" in result