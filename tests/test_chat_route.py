"""
Route- and service-level regression tests for the chat SSE stream.

Covers two regressions introduced by the team-concept upgrade:

1. Streaming generators used ``current_app.app_context()`` after the request
   context was already popped, raising "Working outside of application
   context" on every prompt. Fixed with ``stream_with_context``.
2. ``run_chat_turn`` read tool-call deltas with dict-style ``.get()`` access,
   but the OpenAI SDK streams pydantic objects, raising AttributeError on any
   tool call. Fixed with attribute access that falls back to dict access.
"""

import json

import pytest

import database as db_module
from app import create_app


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1",
    })


def _setup_conversation(tmp_db):
    """Create an app + a ready conversation; give the client an OpenAI fake."""
    from tests.fakes import FakeOpenAIClient

    app = _app(tmp_db)
    fake = FakeOpenAIClient()

    with app.app_context():
        endpoint = db_module.create_endpoint(
            "Fake", "http://fase.openai.test/v1", api_key="f", default_model="test-model", is_default=True
        )
        conv = db_module.create_conversation("Test", "test-model", endpoint_id=endpoint["id"])
        conv_id = conv["id"]

    return app, fake, conv_id


def _parse_sse(body):
    """Parse ``data: `` lines from an SSE response body into parsed dicts."""
    events = []
    for raw in body.splitlines():
        raw = raw.strip()
        if not raw.startswith("data: "):
            continue
        events.append(json.loads(raw[6:]))
    return events


class TestChatRouteSse:
    def test_chat_streams_plain_response(self, tmp_db, monkeypatch):
        """POST /chat streams tokens + done as SSE and persists the exchange."""
        app, fake, conv_id = _setup_conversation(tmp_db)
        fake.add_text_response("Hello from the assistant.")
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "Say hello"},
            )
            assert rv.status_code == 200
            assert rv.content_type.startswith("text/event-stream")

            events = _parse_sse(rv.get_data(as_text=True))

        types = [e["type"] for e in events]
        assert "token" in types, f"no token events; saw {types}"
        assert "done" in types
        assert "error" not in types, [e for e in events if e["type"] == "error"]

        content = "".join(e.get("content", "") for e in events if e["type"] == "token")
        assert "Hello from the assistant" in content

        with app.app_context():
            roles = [m["role"] for m in db_module.get_messages(conv_id)]
        assert "user" in roles and "assistant" in roles

    def test_chat_emits_scope_event(self, tmp_db, monkeypatch):
        """Every chat stream opens with a 'scope' event describing the context
        injected into the system prompt for that message."""
        app, fake, conv_id = _setup_conversation(tmp_db)
        fake.add_text_response("hi")
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "Hello"},
            )
            events = _parse_sse(rv.get_data(as_text=True))

        scope = next((e for e in events if e["type"] == "scope"), None)
        assert scope is not None
        assert scope["meta"]["scope"] == "none"  # no linked folders / uploads
        assert "chars" in scope["meta"]
        assert "warn" in scope["meta"]

    def test_regenerate_streams_plain_response(self, tmp_db, monkeypatch):
        """POST /regenerate streams SSE too (same streaming fix)."""
        app, fake, conv_id = _setup_conversation(tmp_db)
        fake.add_text_response("A regenerated answer.")
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.app_context():
            db_module.add_message(conv_id, role="user", content="Say hello")

        with app.test_client() as client:
            rv = client.post(f"/api/conversations/{conv_id}/regenerate")
            assert rv.status_code == 200

            events = _parse_sse(rv.get_data(as_text=True))

        types = [e["type"] for e in events]
        assert "token" in types, f"no token events; saw {types}"
        assert "done" in types
        assert "error" not in types

        with app.app_context():
            roles = [m["role"] for m in db_module.get_messages(conv_id)]
        assert "assistant" in roles


class TestChatServiceRealSdkChunks:
    def test_tool_call_chunks_with_real_sdk_objects(self, tmp_db):
        """run_chat_turn must handle pydantic delta tool-call objects (regression)."""
        from openai.types.chat.chat_completion_chunk import (
            ChoiceDeltaToolCall,
            ChoiceDeltaToolCallFunction,
        )
        from tests.fakes import FakeChunk, FakeChoice, FakeChoiceDelta

        def chunk_for(first_call):
            if first_call:
                delta = FakeChoiceDelta(tool_calls=[
                    ChoiceDeltaToolCall(
                        index=0,
                        id="call_1",
                        type="function",
                        function=ChoiceDeltaToolCallFunction(name="write_file", arguments=""),
                    ),
                    ChoiceDeltaToolCall(
                        index=1,
                        id="call_2",
                        type="function",
                        function=ChoiceDeltaToolCallFunction(arguments='{"path": "a.txt"}'),
                    ),
                ])
            else:
                delta = FakeChoiceDelta(content="")
            return FakeChunk(choices=[FakeChoice(delta=delta, finish_reason=None)])

        class ScriptedCompletions:
            def __init__(self):
                self._turn = 0

            def create(self, **kwargs):
                self._turn += 1
                if self._turn == 1:
                    return iter([
                        chunk_for(True),
                        chunk_for(True),
                    ])
                return iter([chunk_for(False)])

        class ScriptedChat:
            completions = ScriptedCompletions()

        class ScriptedClient:
            chat = ScriptedChat()

        def execute_tool(fn_name, fn_args, output_dir):
            return {"success": True, "display": fn_name, "result": "OK", "blocked_url": None}

        from chat_service import run_chat_turn

        events = list(run_chat_turn(
            client=ScriptedClient(),
            model_id="test-model",
            messages=[{"role": "user", "content": "Write two files"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            tool_choice="auto",
            max_iterations=10,
            execute_tool_fn=execute_tool,
        ))

        error_events = [e for e in events if e["type"] == "error"]
        assert not error_events, error_events

        assistant_events = [e for e in events if e["type"] == "assistant_message"]
        tool_call_turns = [e for e in assistant_events if e.get("tool_calls")]
        assert len(tool_call_turns) == 1, assistant_events
        assert len(tool_call_turns[0]["tool_calls"]) == 2

        tool_result_events = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_result_events) == 2

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1


class TestAutoTitle:
    """First-prompt auto-title must not overwrite a user-set conversation name."""

    def _kick_off(self, tmp_db, monkeypatch, title):
        from tests.fakes import FakeOpenAIClient

        app = _app(tmp_db)
        fake = FakeOpenAIClient()
        fake.add_text_response("ok")
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)
        with app.app_context():
            endpoint = db_module.create_endpoint(
                "Fake", "http://fase.openai.test/v1", api_key="f", default_model="test-model", is_default=True
            )
            conv = db_module.create_conversation(title, "test-model", endpoint_id=endpoint["id"])
            conv_id = conv["id"]
        with app.test_client() as client:
            client.post(f"/api/conversations/{conv_id}/chat", json={"message": "Tell me about the budget"})
        return app, conv_id

    def test_default_name_is_auto_titled_on_first_prompt(self, tmp_db, monkeypatch):
        app, conv_id = self._kick_off(tmp_db, monkeypatch, "New Conversation")
        with app.app_context():
            title = db_module.get_conversation(conv_id)["title"]
        assert title and title != "New Conversation"
        assert "budget" in title

    def test_custom_name_is_preserved(self, tmp_db, monkeypatch):
        app, conv_id = self._kick_off(tmp_db, monkeypatch, "Security Analyst")
        with app.app_context():
            title = db_module.get_conversation(conv_id)["title"]
        assert title == "Security Analyst"