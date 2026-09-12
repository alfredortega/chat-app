import pytest
from tests.fakes import FakeOpenAIClient


class TestFakeOpenAIClient:
    """Unit tests for FakeOpenAIClient - testing the fake in isolation."""

    def test_scripted_text_response_streaming(self):
        """Test scripted text response with streaming."""
        client = FakeOpenAIClient()
        client.add_text_response("Hello, world!")

        stream = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )

        chunks = list(stream)
        assert len(chunks) > 0

        # Reconstruct content
        content = "".join(
            chunk.choices[0].delta.content or ""
            for chunk in chunks
            if chunk.choices[0].delta.content
        )
        assert content == "Hello, world!"

        # Last chunk should have finish_reason
        assert chunks[-1].choices[0].finish_reason == "stop"

    def test_scripted_text_response_non_streaming(self):
        """Test scripted text response without streaming."""
        client = FakeOpenAIClient()
        client.add_text_response("Hello, world!")

        completion = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            stream=False,
        )

        assert isinstance(completion, FakeCompletion)
        assert completion.choices[0].message.content == "Hello, world!"
        assert completion.choices[0].finish_reason == "stop"

    def test_scripted_tool_calls_response_streaming(self):
        """Test scripted tool calls response with streaming."""
        client = FakeOpenAIClient()
        client.add_tool_calls_response([
            {"id": "call_1", "name": "write_file", "arguments": '{"path": "test.txt"}'},
            {"id": "call_2", "name": "read_file", "arguments": '{"path": "test.txt"}'},
        ])

        stream = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Write and read a file"}],
            stream=True,
            tools=[{"type": "function", "function": {"name": "write_file"}}],
        )

        chunks = list(stream)
        assert len(chunks) >= 3  # initial + 2 tool calls + final

        # First chunk should have empty content
        assert chunks[0].choices[0].delta.content == ""

        # Middle chunks should have tool_calls
        tool_call_chunks = [c for c in chunks if c.choices[0].delta.tool_calls]
        assert len(tool_call_chunks) == 2

        # Check tool call details
        first_tc = tool_call_chunks[0].choices[0].delta.tool_calls[0]
        assert first_tc["index"] == 0
        assert first_tc["id"] == "call_1"
        assert first_tc["function"]["name"] == "write_file"

        second_tc = tool_call_chunks[1].choices[0].delta.tool_calls[0]
        assert second_tc["index"] == 1
        assert second_tc["id"] == "call_2"
        assert second_tc["function"]["name"] == "read_file"

        # Last chunk should have finish_reason
        assert chunks[-1].choices[0].finish_reason == "tool_calls"

    def test_scripted_tool_calls_response_non_streaming(self):
        """Test scripted tool calls response without streaming."""
        client = FakeOpenAIClient()
        client.add_tool_calls_response([
            {"id": "call_1", "name": "write_file", "arguments": '{"path": "test.txt"}'},
        ])

        completion = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Write a file"}],
            stream=False,
        )

        assert isinstance(completion, FakeCompletion)
        assert completion.choices[0].message.content == ""
        assert completion.choices[0].message.tool_calls is not None
        assert len(completion.choices[0].message.tool_calls) == 1
        assert completion.choices[0].finish_reason == "tool_calls"

    def test_zero_chunk_response(self):
        """Test zero-chunk response (empty stream)."""
        client = FakeOpenAIClient()
        client.add_zero_chunk_response()
        client.add_text_response("Fallback response")

        stream = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )

        chunks = list(stream)
        assert len(chunks) == 0  # Zero chunks yielded

        # Next response should work normally
        stream2 = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi again"}],
            stream=True,
        )
        chunks2 = list(stream2)
        assert len(chunks2) > 0

    def test_mid_stream_exception(self):
        """Test mid-stream exception simulation."""
        client = FakeOpenAIClient()
        client.add_text_response("This is a long response that will be interrupted")
        client.add_mid_stream_exception(ValueError("Connection lost"), at_chunk=2)

        stream = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )

        chunks = []
        with pytest.raises(ValueError, match="Connection lost"):
            for chunk in stream:
                chunks.append(chunk)

        # Should have yielded some chunks before exception
        assert len(chunks) >= 2

    def test_script_exhaustion_raises(self):
        """Test that script exhaustion raises an exception (not repeat/empty)."""
        client = FakeOpenAIClient()
        client.add_text_response("Only response")

        # First call works
        stream = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )
        list(stream)  # Consume

        # Second call should raise
        with pytest.raises(RuntimeError, match="script exhausted"):
            client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "Hi again"}],
                stream=True,
            )

    def test_call_recording_captures_model_id(self):
        """Test that call recording captures model_id."""
        client = FakeOpenAIClient()
        client.add_text_response("Response")

        client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )

        calls = client.get_calls()
        assert len(calls) == 1
        assert calls[0]["model"] == "gpt-4"

    def test_call_recording_captures_messages(self):
        """Test that call recording captures messages."""
        client = FakeOpenAIClient()
        client.add_text_response("Response")

        test_messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]

        client.chat.completions.create(
            model="test-model",
            messages=test_messages,
            stream=True,
        )

        calls = client.get_calls()
        assert calls[0]["messages"] == test_messages

    def test_call_recording_captures_tools(self):
        """Test that call recording captures tools."""
        client = FakeOpenAIClient()
        client.add_text_response("Response")

        test_tools = [
            {"type": "function", "function": {"name": "write_file", "description": "Write a file"}},
        ]

        client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
            tools=test_tools,
            tool_choice="auto",
        )

        calls = client.get_calls()
        assert calls[0]["tools"] == test_tools
        assert calls[0]["tool_choice"] == "auto"

    def test_call_recording_multiple_calls(self):
        """Test that multiple calls are recorded in order."""
        client = FakeOpenAIClient()
        client.add_text_response("Response 1")
        client.add_text_response("Response 2")

        client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi 1"}],
            stream=True,
        )
        client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi 2"}],
            stream=True,
        )

        calls = client.get_calls()
        assert len(calls) == 2
        assert calls[0]["messages"][0]["content"] == "Hi 1"
        assert calls[1]["messages"][0]["content"] == "Hi 2"

    def test_get_last_call(self):
        """Test get_last_call helper."""
        client = FakeOpenAIClient()
        client.add_text_response("Response 1")
        client.add_text_response("Response 2")

        client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi 1"}],
            stream=True,
        )
        client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "Hi 2"}],
            stream=True,
        )

        last = client.get_last_call()
        assert last["messages"][0]["content"] == "Hi 2"

        # After reset
        client.reset_calls()
        assert client.get_last_call() is None

    def test_clear_script(self):
        """Test clearing the script."""
        client = FakeOpenAIClient()
        client.add_text_response("Response 1")
        client.add_text_response("Response 2")
        client.clear_script()

        with pytest.raises(RuntimeError, match="script exhausted"):
            client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                stream=True,
            )

    def test_models_list(self):
        """Test models.list() returns fake models."""
        client = FakeOpenAIClient()
        models = client.models.list()

        assert hasattr(models, "data")
        assert len(models.data) >= 2
        assert models.data[0].id == "fake-model-1"
        assert models.data[1].id == "fake-model-2"

    def test_builder_pattern(self):
        """Test fluent builder pattern for adding responses."""
        client = FakeOpenAIClient()
        client.add_text_response("First").add_tool_calls_response([
            {"name": "tool1", "arguments": "{}"}
        ]).add_text_response("Second")

        assert len(client._script) == 3
        assert client._script[0] == ("text", "First")
        assert client._script[1][0] == "tool_calls"
        assert client._script[2] == ("text", "Second")


# Import the classes for type checking in tests
from tests.fakes import FakeCompletion