"""
Bounded chat service extracted from app.py for reuse by HTTP SSE adapter and background worker.

Yields structured events as plain dicts:
- {'type': 'token', 'content': str}
- {'type': 'tool_result', 'success': bool, 'display': str, 'blocked_url': str|None}
- {'type': 'error', 'message': str}
- {'type': 'done'}
- {'type': 'title', 'title': str, 'conv_id': int}
"""

from typing import Iterator, Optional, Callable
import json

from tokens import estimate_messages_tokens, estimate_tokens

# Tool results fed back to the model on the next tool-call iteration are capped
# so one oversized result cannot blow the whole context window. The full result
# is still persisted to the conversation history for UI/preview.
TOOL_RESULT_CAP = 32_000


def _cap_tool_content(content: str) -> str:
    """Bound tool output re-sent to the model on subsequent iterations."""
    if content and len(content) > TOOL_RESULT_CAP:
        return content[:TOOL_RESULT_CAP] + (
            f"\n\n[… tool output truncated at {TOOL_RESULT_CAP:,} chars — "
            "full result kept in conversation history]"
        )
    return content


def _build_usage_record(
    history: list[dict],
    assistant_content: str,
    stream_usage,
) -> dict:
    """
    Turn a streamed usage object (or None) into a normalised usage dict.

    Prefers the provider-reported token counts and falls back to local
    estimates (Phase 6 real accounting).
    """
    est_prompt = estimate_messages_tokens(history)
    est_completion = estimate_tokens(assistant_content)

    if stream_usage is not None:
        pt = getattr(stream_usage, "prompt_tokens", None)
        ct = getattr(stream_usage, "completion_tokens", None)
        tt = getattr(stream_usage, "total_tokens", None)
        if pt is not None or ct is not None or tt is not None:
            return {
                "prompt_tokens": int(pt) if pt is not None else est_prompt,
                "completion_tokens": int(ct) if ct is not None else est_completion,
                "total_tokens": int(tt) if tt is not None else (
                    (int(pt) if pt is not None else est_prompt)
                    + (int(ct) if ct is not None else est_completion)
                ),
                "estimated": False,
            }

    return {
        "prompt_tokens": est_prompt,
        "completion_tokens": est_completion,
        "total_tokens": est_prompt + est_completion,
        "estimated": True,
    }


def run_chat_turn(
    *,
    client,
    model_id: str,
    messages: list[dict],
    tools: Optional[list] = None,
    tool_choice: Optional[str] = None,
    initial_tool_choice: Optional[dict] = None,
    max_iterations: int = 25,
    execute_tool_fn=None,
    output_dir: str = "",
    record_usage_fn: Optional[Callable[[dict], None]] = None,
    max_context_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    request_timeout: Optional[float] = None,
    request_extra_body: Optional[dict] = None,
) -> Iterator[dict]:
    """
    Run a single chat turn with tool-call loop, yielding structured events.

    Args:
        client: OpenAI-compatible client with chat.completions.create()
        model_id: Model identifier to use
        messages: List of message dicts for the API (includes system prompt)
        tools: Optional list of tool definitions (restricted allowlist)
        tool_choice: Tool choice strategy ("auto", "none", or specific function)
        initial_tool_choice: Tool choice applied only to the first model call.
            Subsequent calls use ``tool_choice`` so a required initial write does
            not force a redundant tool call after its result is returned.
        max_iterations: Maximum tool-call rounds before forcing termination
        execute_tool_fn: Function to execute tool calls (name, args, output_dir) -> result dict
        output_dir: Output directory for file operations
        record_usage_fn: Optional callback receiving a per-model-call usage dict
            (provider tokens when available, local estimates otherwise)
        max_context_tokens: When set, refuse to call the model if the estimated
            prompt exceeds this budget (Phase 6 pre-call guard)
        max_output_tokens: When set, pass ``max_tokens`` on each API call to cap
            generated output (e.g. propagation diff rewrites). Providers that
            reject the parameter fall back to an uncapped call.
        request_timeout: Maximum seconds to wait for each model request. This is
            passed to the OpenAI client rather than the model provider.
        request_extra_body: Provider-specific request fields passed through the
            OpenAI client, such as an OpenRouter reasoning configuration.

    Yields:
        Dict events: token, tool_result, error, done, title
    """
    iteration = 0
    history = list(messages)

    while True:
        if iteration >= max_iterations:
            yield {"type": "error", "message": f"Max iterations ({max_iterations}) reached"}
            return

        if max_context_tokens:
            estimate = estimate_messages_tokens(history)
            if estimate > max_context_tokens:
                yield {
                    "type": "error",
                    "message": (
                        f"Estimated context is {estimate:,} tokens, over the "
                        f"{max_context_tokens:,}-token budget. Compact the conversation "
                        "or remove attached files/linked folders before continuing."
                    ),
                }
                return

        try:
            create_kwargs = {
                "model": model_id,
                "messages": history,
                "stream": True,
            }
            if tools:
                create_kwargs["tools"] = tools
                create_kwargs["tool_choice"] = (
                    initial_tool_choice if iteration == 0 and initial_tool_choice
                    else tool_choice or "auto"
                )
            if max_output_tokens:
                create_kwargs["max_tokens"] = max_output_tokens
            if request_timeout is not None:
                create_kwargs["timeout"] = request_timeout
            if request_extra_body:
                create_kwargs["extra_body"] = request_extra_body

            # Ask for usage metadata; some OpenAI-compatible backends reject the
            # parameter, so fall back to a plain request (estimates are used).
            create_kwargs["stream_options"] = {"include_usage": True}
            try:
                stream = client.chat.completions.create(**create_kwargs)
                stream_supports_usage = True
            except Exception:
                create_kwargs.pop("stream_options", None)
                try:
                    stream = client.chat.completions.create(**create_kwargs)
                except Exception:
                    # Some providers reject max_tokens too (e.g. older local
                    # servers) — retry uncapped so a hard cap never breaks chat.
                    create_kwargs.pop("max_tokens", None)
                    stream = client.chat.completions.create(**create_kwargs)
                stream_supports_usage = False
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        assistant_content = ""
        tool_calls_accum = {}
        chunk_count = 0
        last_usage = None

        try:
            for chunk in stream:
                chunk_count += 1
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    last_usage = usage

                choice = chunk.choices[0] if chunk.choices else None
                if choice is None:
                    continue

                delta = choice.delta

                if delta.content is not None:
                    assistant_content += delta.content
                    if delta.content:
                        yield {"type": "token", "content": delta.content}

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            tool_calls_accum[idx]["id"] = tc.id
                        if tc.function:
                            fn = tc.function
                            name = getattr(fn, "name", None)
                            if name is None and isinstance(fn, dict):
                                name = fn.get("name")
                            if name:
                                tool_calls_accum[idx]["name"] = name
                            args_fragment = getattr(fn, "arguments", None)
                            if args_fragment is None and isinstance(fn, dict):
                                args_fragment = fn.get("arguments")
                            if args_fragment:
                                tool_calls_accum[idx]["arguments"] += args_fragment
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        # Persist per-call token accounting (provider usage when available).
        if record_usage_fn:
            record_usage_fn(_build_usage_record(history, assistant_content, last_usage))

        # Handle zero-chunk response: if no chunks were yielded, continue to the
        # next iteration but count it as progress so a provider that repeatedly
        # returns an empty stream cannot loop forever (max_iterations still
        # bounds the loop).
        if chunk_count == 0:
            iteration += 1
            continue

        if tool_calls_accum:
            tc_list = [
                {
                    "id": v["id"],
                    "type": "function",
                    "function": {"name": v["name"], "arguments": v["arguments"]},
                }
                for v in tool_calls_accum.values()
            ]

            # Feed the assistant's tool-call message back into the conversation
            # so the model sees it (and the tool results below) on the next
            # iteration. Without this the loop re-sends the original messages
            # and the model re-issues the same calls forever.
            history.append({
                "role": "assistant",
                "content": assistant_content or None,
                "tool_calls": tc_list,
            })

            yield {"type": "assistant_message", "content": assistant_content, "tool_calls": tc_list}

            for tc in tc_list:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                tool_call_id = tc["id"]

                if execute_tool_fn:
                    result = execute_tool_fn(fn_name, fn_args, output_dir=output_dir)
                else:
                    result = {"success": False, "display": "No tool executor provided", "result": "", "blocked_url": None}

                yield {
                    "type": "tool_result",
                    "success": result["success"],
                    "display": result["display"],
                    "blocked_url": result.get("blocked_url"),
                }

                yield {"type": "tool_message", "tool_call_id": tool_call_id, "content": result["result"]}

                # Append the tool result so the model can act on it next round.
                history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": _cap_tool_content(result["result"]),
                })

            iteration += 1
            continue

        else:
            if assistant_content:
                yield {"type": "assistant_message", "content": assistant_content, "tool_calls": None}
            yield {"type": "done"}
            return


def sse_event(event: dict) -> str:
    """Convert a structured event dict to SSE format string."""
    return f"data: {json.dumps(event)}\n\n"


def sse_stream(events: Iterator[dict]) -> Iterator[str]:
    """Convert an iterator of structured events to SSE format strings."""
    for event in events:
        yield sse_event(event)
