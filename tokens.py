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

_ENC = None


def _get_encoder():
    global _ENC
    if _ENC is None:
        if tiktoken is not None:
            try:
                _ENC = tiktoken.get_encoding("cl100k_base")
            except Exception:
                _ENC = False
        else:
            _ENC = False
    return _ENC if _ENC else None


def estimate_tokens(text: str = "") -> int:
    """Estimate the number of tokens in ``text``."""
    text = text or ""
    enc = _get_encoder()
    if enc is not None:
        try:
            return max(1, len(enc.encode(text)))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_message_tokens(message: dict) -> int:
    """Estimate tokens for a single OpenAI-format message dict."""
    total = estimate_tokens(message.get("content") or "")
    if message.get("role") == "assistant" and message.get("tool_calls"):
        for tc in message["tool_calls"]:
            fn = (tc.get("function") or {})
            total += estimate_tokens(fn.get("name") or "")
            total += estimate_tokens(fn.get("arguments") or "")
    elif message.get("role") == "tool":
        total += estimate_tokens(message.get("tool_call_id") or "")
    return total


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate the token count of a full API message list (histories)."""
    total = 0
    for m in messages:
        total += estimate_message_tokens(m)
    # A fixed modest overhead for the shared wire-format framing.
    total += 4 * len(messages)
    if not messages:
        return 0
    return total
