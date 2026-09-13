"""
conversation_context.py — single entry point for building the "context"
section of a chat system prompt.

Resolves what a conversation may reference, from most to least specific:

  - ``role``         — scoped per-role artifact context for project role
                       conversations (own + declared upstream artifacts, D13);
                       also applied to the designated BA inbox regardless of
                       persona (D19).
  - ``linked_folder``— the legacy whole-workspace dump (non-role project
                       chats and plain conversations with linked folders).
  - ``uploads``      — uploaded-file context.

Returns ``(text, meta)``; ``meta`` describes the scope so the UI can disclose
to the user what the model is actually seeing on every message.
"""

import database as db
from file_handler import (
    build_file_context,
    build_linked_folder_context,
    WARN_THRESHOLD,
)


def role_artifact_reads(template, role: str) -> list[str]:
    """Artifact keys a role reads: its own plus everything its declared
    upstream roles own (D13 — only the upstream artifacts a role needs, not
    the whole project folder)."""
    keys: set[str] = set()
    for spec in template.artifacts:
        if spec.role != role:
            continue
        keys.add(spec.key)
        for upstream_role in spec.upstream_roles:
            for up_spec in template.artifacts:
                if up_spec.role == upstream_role:
                    keys.add(up_spec.key)
    return sorted(keys)


def project_scoped_context(conv: dict) -> tuple[str, str, list[str]] | None:
    """Scoped artifact context for a role conversation inside a project folder.

    Returns ``(context_text, role, read_keys)`` or ``None`` when the
    conversation is not a role conversation, so callers fall back to the
    legacy linked-folder behaviour.
    """
    folder_id = conv.get("folder_id")
    if not folder_id:
        return None
    folder = db.get_folder(folder_id)
    if not folder or folder.get("kind") != "project":
        return None

    from templates import get_template
    template = get_template(folder.get("template_id") or "")
    if not template:
        return None

    # The BA role owns the requirements document ("BA-REQ").
    ba_spec = next((a for a in template.artifacts if a.key == "BA-REQ"), None)
    ba_role = ba_spec.role if ba_spec else None

    # Resolve the conversation's role: the designated Q&A inbox is always
    # treated as the BA role (D19 — regardless of its persona or title), then
    # by persona, then by title (covers roles whose persona is not yet seeded,
    # e.g. QA).
    role = None
    if folder.get("ba_conversation_id") == conv["id"] and ba_role:
        role = ba_role
    else:
        persona_name = ""
        if conv.get("persona_id"):
            persona = db.get_persona(conv["persona_id"])
            persona_name = (persona or {}).get("name") or ""
        role = next(
            (candidate for candidate, p in template.role_to_persona.items()
             if p == persona_name),
            None,
        )
    if role is None:
        role = next(
            (candidate for candidate in template.role_to_persona
             if candidate == conv.get("title")),
            None,
        )
    if role is None:
        return None

    read_keys = role_artifact_reads(template, role)
    try:
        context, _chars, _truncated = db.get_artifact_context(folder["id"], read_keys, None)
    except Exception:
        return None
    if not context:
        return None

    text = (
        f"The following project artifacts are in scope for your {role} role "
        "(read-only reference from the workspace):\n\n"
        + context
    )
    return text, role, read_keys


def build_conversation_context(conv: dict, conv_id: int) -> tuple[str, dict]:
    """
    Build the context section for a conversation + a ``meta`` dictionary
    describing the scope (``scope``, ``role``, ``chars``, ``warn``).

    This is the single place the chat/regenerate/token-count paths assemble
    injected context, so its behaviour stays consistent everywhere.
    """
    conv_files = db.list_conv_files(conv_id)
    linked_folders = db.list_linked_folders(conv_id)

    scoped = project_scoped_context(conv)
    if scoped is not None:
        text, role, read_keys = scoped
        if conv_files:
            upload_ctx = build_file_context(conv_files)
            if upload_ctx:
                text += "\n\n" + upload_ctx
        return text, {
            "scope": "role",
            "role": role,
            "chars": len(text),
            "artifacts": read_keys,
            "warn": len(text) > WARN_THRESHOLD,
        }

    if linked_folders:
        ctx, total_chars = build_linked_folder_context(linked_folders, conv_files)
        if ctx:
            return ctx, {
                "scope": "linked_folder",
                "chars": total_chars,
                "warn": total_chars > WARN_THRESHOLD,
            }
        return "", {"scope": "none", "chars": 0, "warn": False}

    if conv_files:
        ctx = build_file_context(conv_files)
        if ctx:
            return ctx, {
                "scope": "uploads",
                "chars": len(ctx),
                "warn": len(ctx) > WARN_THRESHOLD,
            }

    return "", {"scope": "none", "chars": 0, "warn": False}