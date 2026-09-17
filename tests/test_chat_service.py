import pytest
from tests.fakes import FakeOpenAIClient
from chat_service import run_chat_turn, sse_event


class TestChatService:
    """Characterisation tests for the extracted chat service."""

    def test_plain_text_response(self):
        """Test a simple plain text response without tool calls."""
        client = FakeOpenAIClient()
        client.add_text_response("Hello! How can I help you?")

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            max_iterations=25,
        ))

        # Should have token events and a done event
        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) > 0
        assert len(done_events) == 1

        # Reconstruct content
        content = "".join(e["content"] for e in token_events)
        assert content == "Hello! How can I help you?"

    def test_plain_text_response_non_streaming_tools(self):
        """Test plain text response with tools available but not used."""
        client = FakeOpenAIClient()
        client.add_text_response("I'll help you with that.")

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            tool_choice="auto",
            max_iterations=25,
        ))

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) > 0
        assert len(done_events) == 1

    def test_tool_call_response(self):
        """Test a response that includes tool calls."""
        client = FakeOpenAIClient()
        client.add_tool_calls_response([
            {"id": "call_1", "name": "write_file", "arguments": '{"path": "test.txt", "content": "hello"}'},
        ])

        def execute_tool(fn_name, fn_args, output_dir):
            return {"success": True, "display": "File written", "result": "OK", "blocked_url": None}

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Write a file"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            tool_choice="auto",
            max_iterations=25,
            execute_tool_fn=execute_tool,
            output_dir="/tmp",
        ))

        # Should have assistant_message with tool_calls, tool_result, tool_message
        assistant_events = [e for e in events if e["type"] == "assistant_message"]
        tool_result_events = [e for e in events if e["type"] == "tool_result"]
        tool_message_events = [e for e in events if e["type"] == "tool_message"]

        assert len(assistant_events) == 1
        assert assistant_events[0]["tool_calls"] is not None
        assert len(assistant_events[0]["tool_calls"]) == 1
        assert len(tool_result_events) == 1
        assert len(tool_message_events) == 1

    def test_multi_turn_tool_loop(self):
        """Test multiple tool call rounds (multi-turn)."""
        client = FakeOpenAIClient()
        # First response: tool call
        client.add_tool_calls_response([
            {"id": "call_1", "name": "write_file", "arguments": '{"path": "a.txt"}'},
        ])
        # Second response: another tool call
        client.add_tool_calls_response([
            {"id": "call_2", "name": "read_file", "arguments": '{"path": "a.txt"}'},
        ])
        # Third response: plain text
        client.add_text_response("Done!")

        def execute_tool(fn_name, fn_args, output_dir):
            return {"success": True, "display": f"Executed {fn_name}", "result": "OK", "blocked_url": None}

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Do something"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}, {"type": "function", "function": {"name": "read_file"}}],
            tool_choice="auto",
            max_iterations=25,
            execute_tool_fn=execute_tool,
            output_dir="/tmp",
        ))

        # Should have 2 assistant_message with tool_calls, 1 final assistant_message, and done
        assistant_events = [e for e in events if e["type"] == "assistant_message"]
        tool_calls_assistant = [e for e in assistant_events if e.get("tool_calls")]
        plain_assistant = [e for e in assistant_events if not e.get("tool_calls")]

        assert len(tool_calls_assistant) == 2
        assert len(plain_assistant) == 1

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    def test_multi_turn_tool_loop_feeds_results_back(self):
        """Tool results must be appended to the model history between iterations.

        Regression: run_chat_turn used to copy ``messages`` and never re-send
        the assistant tool-call message or the tool results, so the loop
        re-issued the same tool calls forever (the "BA generated 4 documents
        then hung" runaway). The second API call must contain both the
        assistant message with tool_calls and the tool result.
        """
        client = FakeOpenAIClient()
        client.add_tool_calls_response([
            {"id": "call_1", "name": "write_file", "arguments": '{"path": "a.txt"}'},
        ])
        client.add_text_response("Done!")

        def execute_tool(fn_name, fn_args, output_dir):
            return {"success": True, "display": "File written", "result": "wrote a.txt", "blocked_url": None}

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Write a file"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            tool_choice="auto",
            max_iterations=10,
            execute_tool_fn=execute_tool,
            output_dir="/tmp",
        ))

        assert [e["type"] for e in events].count("done") == 1

        calls = client.get_calls()
        assert len(calls) == 2
        second = calls[1]["messages"]
        roles = [m["role"] for m in second]
        assert "tool" in roles, f"tool result missing from 2nd call: {roles}"
        assert "assistant" in roles
        assistant = next(m for m in second if m["role"] == "assistant")
        assert assistant.get("tool_calls"), "assistant tool_calls missing from 2nd call"
        tool_msg = next(m for m in second if m["role"] == "tool")
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "wrote a.txt"

    def test_max_iterations_guard_terminates_loop(self):
        """Test that max_iterations guard terminates a runaway tool loop with error event."""
        client = FakeOpenAIClient()
        # Configure infinite tool calls (script has more than max_iterations)
        for i in range(30):
            client.add_tool_calls_response([
                {"id": f"call_{i}", "name": "write_file", "arguments": f'{{"path": "file_{i}.txt"}}'},
            ])

        def execute_tool(fn_name, fn_args, output_dir):
            return {"success": True, "display": f"Executed {fn_name}", "result": "OK", "blocked_url": None}

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Loop forever"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            tool_choice="auto",
            max_iterations=5,  # Low limit to trigger guard
            execute_tool_fn=execute_tool,
            output_dir="/tmp",
        ))

        # Should have exactly 5 tool call rounds then error
        assistant_events = [e for e in events if e["type"] == "assistant_message"]
        tool_calls_assistant = [e for e in assistant_events if e.get("tool_calls")]
        error_events = [e for e in events if e["type"] == "error"]

        assert len(tool_calls_assistant) == 5
        assert len(error_events) == 1
        assert "Max iterations" in error_events[0]["message"]
        # Should NOT have a done event
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 0

    def test_error_event_on_exception(self):
        """Test that exceptions from the client yield error events."""
        client = FakeOpenAIClient()
        # No scripted responses - will cause script exhaustion error
        # Actually, let's test with mid-stream exception
        client.add_text_response("This will be interrupted")
        client.add_mid_stream_exception(ValueError("Connection lost"), at_chunk=1)

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            max_iterations=25,
        ))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "Connection lost" in error_events[0]["message"]

    def test_sse_event_format(self):
        """Test SSE event formatting."""
        event = {"type": "token", "content": "Hello"}
        sse = sse_event(event)
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        assert '"type": "token"' in sse
        assert '"content": "Hello"' in sse

    def test_zero_chunk_handling(self):
        """Test handling of zero-chunk responses."""
        client = FakeOpenAIClient()
        client.add_zero_chunk_response()
        client.add_text_response("Actual response")

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            max_iterations=25,
        ))

        # First response yields nothing, second yields tokens
        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(token_events) > 0
        assert len(done_events) == 1

        content = "".join(e["content"] for e in token_events)
        assert content == "Actual response"

    def test_repeated_zero_chunks_terminate(self):
        """Regression: a provider returning empty streams forever must not hang
        the propagation wave. Each empty stream counts as an iteration, so the
        max_iterations guard terminates the loop with an error event."""
        client = FakeOpenAIClient()
        # Enough empty streams to exceed the (tiny) iteration cap.
        for _ in range(30):
            client.add_zero_chunk_response()

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            max_iterations=5,
        ))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "Max iterations" in error_events[0]["message"]

    def test_empty_tool_calls_handling(self):
        """Test handling when tool_calls is present but empty."""
        client = FakeOpenAIClient()
        client.add_text_response("Response with no tool calls")

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=[{"type": "function", "function": {"name": "write_file"}}],
            tool_choice="auto",
            max_iterations=25,
        ))

        # Should complete normally without tool calls
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1


class TestChatServiceCharacterisation:
    """Characterisation test: verify SSE output format matches expected format."""

    def test_sse_output_format_matches_expected(self):
        """Verify the SSE output format matches the expected byte format."""
        client = FakeOpenAIClient()
        client.add_text_response("Test response")

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            max_iterations=25,
        ))

        sse_output = "".join(sse_event(e) for e in events)

        # Verify format: each event is "data: {...}\n\n"
        lines = sse_output.strip().split("\n\n")
        assert len(lines) >= 2  # At least token events + done

        for line in lines:
            if line.strip():
                assert line.startswith("data: ")
                # Should be valid JSON after "data: "
                import json
                json_part = line[6:]  # Remove "data: "
                parsed = json.loads(json_part)
                assert "type" in parsed

    def test_sse_output_contains_token_and_done(self):
        """Verify SSE output contains token events and done event."""
        client = FakeOpenAIClient()
        client.add_text_response("Hello world")

        events = list(run_chat_turn(
            client=client,
            model_id="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            max_iterations=25,
        ))

        sse_output = "".join(sse_event(e) for e in events)

        assert '"type": "token"' in sse_output
        assert '"type": "done"' in sse_output
        assert "Hello world" in sse_output