"""
Tests for Phases 4–6 of the token-minimisation work:

- Phase 4: selective injection (preview-only linked folders) + the
  conversation-scoped ``read_named_file`` tool.
- Phase 5: bounded diffs and bounded propagation agent prompt inputs.
- Phase 6: real token accounting (token_usage table, usage recording,
  pre-call context guard) and the deduplicated context build.
"""

import json
import os

import pytest

import database as db_module
import file_handler as fh
import tokens


# ── Phase 6: token estimator ───────────────────────────────────────────────────

class TestTokenEstimator:
    def test_estimate_tokens_positive(self):
        assert tokens.estimate_tokens("") >= 1
        assert tokens.estimate_tokens("a nice message") > 0

    def test_estimate_messages_counts_content_and_tool_payload(self):
        msgs = [
            {"role": "system", "content": "sys" * 100},
            {"role": "user", "content": "user"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/x.py"})}}]},
            {"role": "tool", "content": "result", "tool_call_id": "call_1"},
        ]
        total = tokens.estimate_messages_tokens(msgs)
        assert total > 0
        # A huge assistant payload must dominate.
        big = tokens.estimate_messages_tokens([{"role": "user", "content": "x" * 100_000}])
        assert big > tokens.estimate_messages_tokens([{"role": "user", "content": "hi"}])
        assert tokens.estimate_messages_tokens([]) == 0


# ── Phase 4: preview-only linked folder injection ─────────────────────────────

class TestPreviewOnlyLinkedFolders:
    def _write_files(self, tmp_path):
        ws = tmp_path / "linked"
        ws.mkdir()
        (ws / "small.md").write_text("small full text here\n", encoding="utf-8")
        (ws / "big.md").write_text("# Big doc\n\n" + ("filler line\n" * 5_000), encoding="utf-8")
        return str(ws)

    def test_small_file_injected_fully_small_big_previewed(self, tmp_path):
        ws = self._write_files(tmp_path)
        context, total = fh.build_linked_folder_context([{"folder_path": ws}], [], preview_only=True)
        assert "CONTEXT INDEX" in context
        assert "read_named_file" in context
        # Small file's full content survives the preview.
        assert "small full text here" in context
        # Big file is not dumped in full.
        long_run = "filler line\n" * 5_000
        assert long_run not in context
        assert "omitted" in context
        assert total > len(context)

    def test_uploaded_files_also_in_index(self, tmp_path, tmp_db):
        from app import create_app
        from file_handler import build_linked_folder_context
        import database as db

        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            conv = db.create_conversation("C", "m")
            up = os.path.join(tmp_path, "notes.txt")
            with open(up, "w", encoding="utf-8") as f:
                f.write("uploaded content\n")
            rec = db.add_conv_file(conv["id"], "notes.txt", up, os.path.getsize(up), 18, snippet="uploaded")
            linked = os.path.join(tmp_path, "linked")
            os.makedirs(linked, exist_ok=True)
            with open(os.path.join(linked, "a.md"), "w", encoding="utf-8") as f:
                f.write("linked content\n")
            ctx, _ = build_linked_folder_context([{"folder_path": linked}], [rec], preview_only=True)
        assert "notes.txt" in ctx
        assert "a.md" in ctx
        assert "read_named_file" in ctx


# ── Phase 4: read_named_file scoped tool ──────────────────────────────────────

class TestReadNamedFile:
    def _make_conv_files(self, tmp_db, tmp_path):
        from app import create_app
        import database as db
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            conv = db.create_conversation("C", "m")
            conv_id = conv["id"]
            up = os.path.join(tmp_path, "notes.txt")
            with open(up, "w", encoding="utf-8") as f:
                f.write("hello uploaded\n")
            db.add_conv_file(conv_id, "notes.txt", up, os.path.getsize(up), 15)
            linked = tmp_path / "linked"
            linked.mkdir()
            (linked / "src" / "app.py").parent.mkdir(parents=True)
            (linked / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
            db.add_linked_folder(conv_id, str(linked))
            return conv_id

    def test_reads_uploaded_file(self, tmp_db, tmp_path):
        from tools import execute_tool_call
        conv_id = self._make_conv_files(tmp_db, tmp_path)
        result = execute_tool_call(
            "read_named_file", json.dumps({"name": "notes.txt"}),
            context="chat", conv_id=conv_id,
        )
        assert result["success"]
        assert "hello uploaded" in result["result"]

    def test_reads_linked_file_by_rel_path(self, tmp_db, tmp_path):
        from tools import execute_tool_call
        conv_id = self._make_conv_files(tmp_db, tmp_path)
        result = execute_tool_call(
            "read_named_file", json.dumps({"name": "src/app.py"}),
            context="chat", conv_id=conv_id,
        )
        assert result["success"]
        assert "print('hi')" in result["result"]

    def test_missing_file_returns_available_hint(self, tmp_db, tmp_path):
        from tools import execute_tool_call
        conv_id = self._make_conv_files(tmp_db, tmp_path)
        result = execute_tool_call(
            "read_named_file", json.dumps({"name": "nope.md"}),
            context="chat", conv_id=conv_id,
        )
        assert not result["success"]
        assert "nope.md" in result["result"]
        assert "notes.txt" in result["result"]  # hint lists available files

    def test_rejected_outside_chat_profile(self, tmp_db, tmp_path):
        from tools import execute_tool_call
        result = execute_tool_call(
            "read_named_file", json.dumps({"name": "notes.txt"}),
            context="propagation",
        )
        assert not result["success"]


# ── Phase 5: bounded diffs and bounded agent prompts ──────────────────────────

class TestBoundedDiff:
    def test_git_diff_file_truncates(self, tmp_path):
        from git_integration import git_diff_file, git_init, git_commit_all
        repo = tmp_path / "repo"
        repo.mkdir()
        assert git_init(str(repo)).success
        f = repo / "doc.md"
        f.write_text("line\n" * 1000, encoding="utf-8")
        assert git_commit_all(str(repo), "init").success
        f.write_text("line\n" * 1000 + "changed\n", encoding="utf-8")

        full = git_diff_file(str(repo), "doc.md")
        assert full.success
        assert len(full.output) > 100

        small = git_diff_file(str(repo), "doc.md", max_chars=60)
        assert small.success
        assert len(small.output) <= 60 + 64  # truncation note appended
        assert "truncated" in small.output


class TestAgentPromptCaps:
    def test_build_agent_prompt_bounds_its_inputs(self):
        from propagation.agent import build_agent_prompt
        huge = "x" * (100_000)
        messages = build_agent_prompt(
            persona_prompt="p" * 20_000,
            context="C" * 200_000,
            change_event={"summary": "s", "diff": "D" * 100_000, "changed_reqs": ["REQ-1"]},
            current_artifact="A" * 300_000,
        )
        user_body = messages[1]["content"]
        # Current artifact cap is 60k chars + upstream 72k + diff 12k + persona.
        assert len(user_body) < 200_000
        assert "omitted" in user_body


# ── Phase 6: usage guard + recording in chat_service ──────────────────────────

class TestUsageRecordAndGuard:
    def _messages(self, big=1_000):
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u" * big},
        ]

    def test_guard_refuses_oversized_context(self):
        from tests.fakes import FakeOpenAIClient
        from chat_service import run_chat_turn
        fake = FakeOpenAIClient()
        fake.add_text_response("hi")
        events = list(run_chat_turn(
            client=fake,
            model_id="m",
            messages=self._messages(big=200_000),
            max_context_tokens=1_000,
        ))
        assert any(e["type"] == "error" for e in events)
        assert fake.get_calls() == []  # the model was never called

    def test_usage_callback_receives_estimate_without_usage_chunks(self):
        from tests.fakes import FakeOpenAIClient
        from chat_service import run_chat_turn
        fake = FakeOpenAIClient()
        fake.add_text_response("hello world result")
        recorded = []
        list(run_chat_turn(
            client=fake,
            model_id="m",
            messages=self._messages(),
            record_usage_fn=recorded.append,
        ))
        assert recorded
        usage = recorded[0]
        assert usage["prompt_tokens"] > 0
        assert usage["completion_tokens"] > 0
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


# ── Phase 6: DB token accounting + chat route persistence ─────────────────────

class TestTokenUsageDbAndRoute:
    def test_record_and_last_usage(self, tmp_db):
        from app import create_app
        import database as db
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            conv = db.create_conversation("C", "m")
            db.record_token_usage(conv["id"], prompt_tokens=10, completion_tokens=5,
                                  total_tokens=15, estimated=False)
            db.record_token_usage(conv["id"], prompt_tokens=20, completion_tokens=8,
                                  total_tokens=28, estimated=True)
            last = db.last_token_usage(conv["id"])
            assert last["prompt_tokens"] == 20
            assert last["estimated"] is True

    def test_chat_route_records_usage_and_exposes_it(self, tmp_db, monkeypatch):
        from app import create_app
        import database as db
        from tests.fakes import FakeOpenAIClient

        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        fake = FakeOpenAIClient()
        fake.add_text_response("hi")
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.app_context():
            endpoint = db.create_endpoint("Fake", "http://fake.test/v1", api_key="f",
                                          default_model="test-model", is_default=True)
            conv = db.create_conversation("Test", "test-model", endpoint_id=endpoint["id"])
            conv_id = conv["id"]

        with app.test_client() as client:
            rv = client.post(f"/api/conversations/{conv_id}/chat", json={"message": "Hello"})
            rv.get_data(as_text=True)  # consume the SSE stream so the generator runs
            assert rv.status_code == 200

        with app.app_context():
            assert db.last_token_usage(conv_id) is not None
            rv2 = client.get(f"/api/conversations/{conv_id}/token-count")
            body = rv2.get_json()
            assert body["last_usage"] is not None
            assert body["last_usage"]["total_tokens"] > 0


# ── Phase 6: deduplicated context build ───────────────────────────────────────

class TestContextBuildDedupe:
    def test_context_section_parameter_reused(self, tmp_db, tmp_path):
        from app import create_app, _build_system_prompt
        import database as db
        from conversation_context import build_conversation_context

        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            conv = db.create_conversation("C", "m")
            linked = tmp_path / "linked"
            linked.mkdir()
            (linked / "a.md").write_text("alpha content here\n", encoding="utf-8")
            db.add_linked_folder(conv["id"], str(linked))

            section, meta = build_conversation_context(conv, conv["id"])
            prompt, _ = _build_system_prompt(
                conv, conv["id"], tools_on=True, context_section=section,
            )
        assert "alpha content here" in prompt
        assert meta["scope"] == "linked_folder"