"""
tokens.py — best-effort token estimation for context budgeting.

Prefers a real tokenizer (tiktoken, cl100k_base) when it is installed and
falls back to the approximate "1 token ≈ 4 characters" rule used elsewhere in
the app, so the rest of the code base can count tokens without hard
dependencies or network access.
"""

import json

try:
    import tiktoken
except Exception:  # pragma: no cover - optional dependency
    tiktoken = None

_ENCODERS = {}


def _get_encoder(model_id: str | None = None):
    key = model_id or "cl100k_base"
    if key not in _ENCODERS:
        encoder = None
        if tiktoken is not None:
            try:
                try:
                    encoder = tiktoken.encoding_for_model(model_id) if model_id else None
                except Exception:
                    encoder = tiktoken.get_encoding("cl100k_base")
            except Exception:
                encoder = False
        _ENCODERS[key] = encoder or False
    return _ENCODERS[key] if _ENCODERS[key] else None


def estimate_tokens(text: str = "", model_id: str | None = None) -> int:
    """Estimate the number of tokens in ``text``."""
    text = text or ""
    enc = _get_encoder(model_id)
    if enc is not None:
        try:
            return max(1, len(enc.encode(text)))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_message_tokens(message: dict, model_id: str | None = None) -> int:
    """Estimate tokens for a single OpenAI-format message dict."""
    total = estimate_tokens(message.get("content") or "", model_id)
    if message.get("role") == "assistant" and message.get("tool_calls"):
        for tc in message["tool_calls"]:
            fn = (tc.get("function") or {})
            total += estimate_tokens(fn.get("name") or "", model_id)
            total += estimate_tokens(fn.get("arguments") or "", model_id)
    elif message.get("role") == "tool":
        total += estimate_tokens(message.get("tool_call_id") or "", model_id)
    return total


def estimate_tools_tokens(tools: list[dict] | None, model_id: str | None = None) -> int:
    """Estimate the input tokens occupied by function-tool schemas."""
    if not tools:
        return 0
    return estimate_tokens(json.dumps(tools, separators=(",", ":")), model_id)


def estimate_request_tokens(
    messages: list[dict], tools: list[dict] | None = None, model_id: str | None = None
) -> int:
    """Estimate all prompt tokens sent in one chat-completions request."""
    return estimate_messages_tokens(messages, model_id) + estimate_tools_tokens(tools, model_id)


def estimate_messages_tokens(messages: list[dict], model_id: str | None = None) -> int:
    """Estimate the token count of a full API message list (histories)."""
    total = 0
    for m in messages:
        total += estimate_message_tokens(m, model_id)
    # A fixed modest overhead for the shared wire-format framing.
    total += 4 * len(messages)
    if not messages:
        return 0
    return total
