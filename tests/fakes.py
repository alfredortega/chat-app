from typing import Any, Iterator, Optional
from dataclasses import dataclass, field


@dataclass
class FakeToolCall:
    index: int
    id: Optional[str] = None
    function: Optional[dict] = None

    def __post_init__(self):
        if self.function is None:
            self.function = {"name": "", "arguments": ""}

    # Make subscriptable for backward compatibility
    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)


@dataclass
class FakeChoiceDelta:
    content: Optional[str] = None
    tool_calls: Optional[list[FakeToolCall]] = None


@dataclass
class FakeChoice:
    delta: FakeChoiceDelta
    finish_reason: Optional[str] = None
    index: int = 0


@dataclass
class FakeChunk:
    choices: list[FakeChoice]


@dataclass
class FakeMessage:
    content: Optional[str] = None
    tool_calls: Optional[list] = None


@dataclass
class FakeCompletionChoice:
    message: FakeMessage
    finish_reason: Optional[str] = None
    index: int = 0


@dataclass
class FakeCompletion:
    choices: list[FakeCompletionChoice]
    model: str = "fake-model"


class FakeCompletions:
    def __init__(self, parent: "FakeOpenAIClient"):
        self._parent = parent

    def create(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = True,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> Iterator[FakeChunk] | FakeCompletion:
        return self._parent._create_completion(
            model=model,
            messages=messages,
            stream=stream,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )


class FakeChat:
    def __init__(self, parent: "FakeOpenAIClient"):
        self.completions = FakeCompletions(parent)


class FakeModels:
    def list(self):
        class FakeModel:
            def __init__(self, id: str):
                self.id = id

        class FakeModelsList:
            data = [FakeModel("fake-model-1"), FakeModel("fake-model-2")]

        return FakeModelsList()


class FakeOpenAIClient:
    """
    Deterministic fake OpenAI client for testing.

    Mimics the OpenAI SDK interface used by app.get_client():
    - client.chat.completions.create(...) for both stream=True and stream=False
    - client.models.list()

    Scripted responses are queued as turns. Each turn can be:
    - A string (text response)
    - A list of tool_call dicts (tool call response)
    - A special sentinel for zero-chunk or mid-stream exception

    Call recording captures model_id, messages, tools for test assertions.

    Script exhaustion raises an exception (never repeats or returns empty).
    """

    def __init__(self, *, api_key: str = "fake-key", base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url
        self.chat = FakeChat(self)
        self.models = FakeModels()

        # Scripted responses queue
        self._script: list[Any] = []

        # Call recording
        self.calls: list[dict] = []

        # Configuration for special behaviors
        self._mid_stream_exception: Optional[Exception] = None
        self._stream_exception_at_chunk: Optional[int] = None

    # ── Script configuration ──

    def add_text_response(self, text: str) -> "FakeOpenAIClient":
        """Add a plain text response to the script."""
        self._script.append(("text", text))
        return self

    def add_tool_calls_response(self, tool_calls: list[dict]) -> "FakeOpenAIClient":
        """Add a tool-calls response to the script."""
        self._script.append(("tool_calls", tool_calls))
        return self

    def add_zero_chunk_response(self) -> "FakeOpenAIClient":
        """Configure next response to yield zero chunks (empty stream)."""
        self._script.append(("zero_chunk", None))
        return self

    def add_mid_stream_exception(self, exc: Exception, at_chunk: int = 2) -> "FakeOpenAIClient":
        """Configure next response to raise an exception mid-stream."""
        self._mid_stream_exception = exc
        self._stream_exception_at_chunk = at_chunk
        return self

    def clear_script(self) -> "FakeOpenAIClient":
        """Clear the scripted responses."""
        self._script.clear()
        return self

    # ── Call recording ──

    def get_calls(self) -> list[dict]:
        """Return recorded calls with model, messages, tools."""
        return self.calls

    def get_last_call(self) -> Optional[dict]:
        """Return the last recorded call."""
        return self.calls[-1] if self.calls else None

    def reset_calls(self) -> "FakeOpenAIClient":
        """Clear recorded calls."""
        self.calls.clear()
        return self

    # ── Internal completion creation ──

    def _create_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool,
        tools: Optional[list],
        tool_choice: Optional[str],
        **kwargs: Any,
    ) -> Iterator[FakeChunk] | FakeCompletion:
        # Record the call
        self.calls.append({
            "model": model,
            "messages": messages,
            "stream": stream,
            "tools": tools,
            "tool_choice": tool_choice,
            "kwargs": kwargs,
        })

        # Get next scripted response
        if not self._script:
            raise RuntimeError(
                "FakeOpenAIClient script exhausted - no more responses configured. "
                "This is intentional: script exhaustion must raise to prevent "
                "infinite loops in the chat service (see 0.7 hazards)."
            )

        response_type, response_data = self._script.pop(0)

        if stream:
            return self._create_stream_response(response_type, response_data)
        else:
            return self._create_non_stream_response(response_type, response_data)

    def _create_stream_response(
        self, response_type: str, response_data: Any
    ) -> Iterator[FakeChunk]:
        if response_type == "zero_chunk":
            # Yield nothing - empty iterator
            return
            yield  # Make this a generator

        if response_type == "text":
            text = response_data
            # Yield text in chunks (simulate streaming)
            chunk_size = 10
            for i in range(0, len(text), chunk_size):
                chunk_text = text[i:i + chunk_size]
                yield FakeChunk(choices=[
                    FakeChoice(
                        delta=FakeChoiceDelta(content=chunk_text),
                        finish_reason=None if i + chunk_size < len(text) else "stop"
                    )
                ])

                # Check for mid-stream exception
                if self._mid_stream_exception and self._stream_exception_at_chunk is not None:
                    self._stream_exception_at_chunk -= 1
                    if self._stream_exception_at_chunk <= 0:
                        exc = self._mid_stream_exception
                        self._mid_stream_exception = None
                        self._stream_exception_at_chunk = None
                        raise exc

        elif response_type == "tool_calls":
            tool_calls = response_data
            # First yield empty content chunk (assistant_content can be empty)
            yield FakeChunk(choices=[
                FakeChoice(delta=FakeChoiceDelta(content=""), finish_reason=None)
            ])

            # Then yield tool call chunks
            for idx, tc in enumerate(tool_calls):
                yield FakeChunk(choices=[
                    FakeChoice(
                        delta=FakeChoiceDelta(tool_calls=[
                            FakeToolCall(
                                index=idx,
                                id=tc.get("id", f"call_{idx}"),
                                function={
                                    "name": tc.get("name", ""),
                                    "arguments": tc.get("arguments", "")
                                }
                            )
                        ]),
                        finish_reason=None
                    )
                ])

            # Final chunk with finish_reason
            yield FakeChunk(choices=[
                FakeChoice(
                    delta=FakeChoiceDelta(content=None),
                    finish_reason="tool_calls"
                )
            ])

    def _create_non_stream_response(
        self, response_type: str, response_data: Any
    ) -> FakeCompletion:
        if response_type == "text":
            message = FakeMessage(content=response_data, tool_calls=None)
            finish_reason = "stop"
        elif response_type == "tool_calls":
            # Convert dict tool_calls to FakeToolCall objects
            fake_tool_calls = []
            for idx, tc in enumerate(response_data):
                fake_tool_calls.append(FakeToolCall(
                    index=idx,
                    id=tc.get("id", f"call_{idx}"),
                    function={
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", "")
                    }
                ))
            message = FakeMessage(content="", tool_calls=fake_tool_calls)
            finish_reason = "tool_calls"
        else:
            message = FakeMessage(content="", tool_calls=None)
            finish_reason = "stop"

        return FakeCompletion(
            choices=[FakeCompletionChoice(message=message, finish_reason=finish_reason)],
            model="fake-model"
        )