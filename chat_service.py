"""
Bounded chat service extracted from app.py for reuse by HTTP SSE adapter and background worker.

Yields structured events as plain dicts:
- {'type': 'token', 'content': str}
- {'type': 'tool_result', 'success': bool, 'display': str, 'blocked_url': str|None}
- {'type': 'error', 'message': str}
- {'type': 'done'}
- {'type': 'title', 'title': str, 'conv_id': int}
"""

from typing import Iterator, Optional
import json


def run_chat_turn(
    *,
    client,
    model_id: str,
    messages: list[dict],
    tools: Optional[list] = None,
    tool_choice: Optional[str] = None,
    max_iterations: int = 25,
    execute_tool_fn=None,
    output_dir: str = "",
) -> Iterator[dict]:
    """
    Run a single chat turn with tool-call loop, yielding structured events.

    Args:
        client: OpenAI-compatible client with chat.completions.create()
        model_id: Model identifier to use
        messages: List of message dicts for the API (includes system prompt)
        tools: Optional list of tool definitions (restricted allowlist)
        tool_choice: Tool choice strategy ("auto", "none", or specific function)
        max_iterations: Maximum tool-call rounds before forcing termination
        execute_tool_fn: Function to execute tool calls (name, args, output_dir) -> result dict
        output_dir: Output directory for file operations

    Yields:
        Dict events: token, tool_result, error, done, title
    """
    iteration = 0
    history = list(messages)

    while True:
        if iteration >= max_iterations:
            yield {"type": "error", "message": f"Max iterations ({max_iterations}) reached"}
            return

        try:
            create_kwargs = {
                "model": model_id,
                "messages": history,
                "stream": True,
            }
            if tools:
                create_kwargs["tools"] = tools
                create_kwargs["tool_choice"] = tool_choice or "auto"
            stream = client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        assistant_content = ""
        tool_calls_accum = {}
        chunk_count = 0

        try:
            for chunk in stream:
                chunk_count += 1
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
                        if tc.function and tc.function.get("name"):
                            tool_calls_accum[idx]["name"] = tc.function["name"]
                        if tc.function and tc.function.get("arguments"):
                            tool_calls_accum[idx]["arguments"] += tc.function["arguments"]
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        # Handle zero-chunk response: if no chunks were yielded, continue to next iteration
        if chunk_count == 0:
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