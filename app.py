import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone

def load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    os.environ[key] = val

# Do not load dotenv automatically at import time to prevent side effects in tests
# load_dotenv()

from flask import Flask, g, jsonify, request, Response, send_from_directory, send_file, Blueprint, current_app, stream_with_context
from flask_cors import CORS
from openai import OpenAI

import database as db
from tools import TOOLS, build_tools, execute_tool_call
from tokens import estimate_messages_tokens, estimate_request_tokens, estimate_tokens
from file_handler import (
    ensure_upload_dir, allowed_extension, extract_text, evict_upload_cache,
    build_file_context,
    build_linked_folder_context,
    scan_linked_folder, WARN_THRESHOLD,
)
from documents import (
    DocumentError,
    list_documents as list_documents_service,
    read_document as read_document_service,
    update_document as update_document_service,
    rename_document as rename_document_service,
    effective_output_dir,
    resolve_output_path_for,
    is_markdown_name,
    export_document_docx,
    MAX_EDITABLE_BYTES,
)

# Headroom above the editable-content ceiling for the JSON wrapper + hash.
MAX_DOC_REQUEST_BYTES = MAX_EDITABLE_BYTES + 64 * 1024

# Tool results (read_file, fetch_webpage, run_python, etc.) can be enormous. The
# full result is persisted in the conversation history for UI/preview, but only
# a capped slice is re-sent to the model on subsequent turns — otherwise one
# big tool call blows the whole context window forever.
TOOL_RESULT_CAP = 16_000

# Pre-call context guard and request budget settings.
MODEL_CONTEXT_BUDGET = int(os.environ.get("MODEL_CONTEXT_BUDGET", "128000"))
MODEL_MAX_OUTPUT_TOKENS = int(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "4096"))
MODEL_MAX_TOOL_ITERATIONS = int(os.environ.get("MODEL_MAX_TOOL_ITERATIONS", "12"))
MODEL_HISTORY_KEEP_RECENT = int(os.environ.get("MODEL_HISTORY_KEEP_RECENT", "8"))
MODEL_CONTEXT_RESERVE = int(os.environ.get("MODEL_CONTEXT_RESERVE", "4096"))
MODEL_CONTEXT_CHARS = int(os.environ.get("MODEL_CONTEXT_CHARS", "60000"))
MODEL_PRICING = json.loads(os.environ.get("MODEL_PRICING_JSON", "{}"))
MODEL_REQUEST_EXTRA_BODY = json.loads(os.environ.get("MODEL_REQUEST_EXTRA_BODY_JSON", "{}"))


def _context_char_budget() -> int:
    """Reserve room for history, tools, and the requested completion."""
    token_budget = max(1, MODEL_CONTEXT_BUDGET - MODEL_CONTEXT_RESERVE - MODEL_MAX_OUTPUT_TOKENS)
    return max(12_000, min(MODEL_CONTEXT_CHARS, token_budget * 4 // 3))


def _usage_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model_id) or MODEL_PRICING.get("default") or {}
    input_rate = float(pricing.get("input_per_million", 0) or 0)
    output_rate = float(pricing.get("output_per_million", 0) or 0)
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


def _cap_tool_content(content: str) -> str:
    """Bound tool output sent to the model while leaving the stored row intact."""
    if content and len(content) > TOOL_RESULT_CAP:
        return content[:TOOL_RESULT_CAP] + (
            f"\n\n[… tool output truncated at {TOOL_RESULT_CAP:,} chars — "
            "full result kept in conversation history]"
        )
    return content
from chat_service import compact_api_history, run_chat_turn, sse_event

def create_app(config=None):
    """Application factory for creating the Flask app."""
    if not os.environ.get("SKIP_DOTENV") == "1":
        load_dotenv()
        db.load_dotenv()

    if config:
        if "ENCRYPTION_KEY" in config:
            os.environ["ENCRYPTION_KEY"] = config["ENCRYPTION_KEY"]
        if "SKIP_DOTENV_WRITE" in config:
            os.environ["SKIP_DOTENV_WRITE"] = config["SKIP_DOTENV_WRITE"]

    app_inst = Flask(__name__, static_folder="static", static_url_path="")
    CORS(app_inst)

    db_uri = None
    if config and "SQLALCHEMY_DATABASE_URI" in config:
        db_uri = config["SQLALCHEMY_DATABASE_URI"]
    else:
        db_uri = os.environ.get(
            "DATABASE_URL",
            f"sqlite:///{db.DB_PATH}"
        )

    app_inst.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app_inst.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.db.init_app(app_inst)
    db.init_db(app_inst)

    app_inst.register_blueprint(legacy_bp)

    # Phase 4: project blueprint.
    from routes.projects import projects_bp
    app_inst.register_blueprint(projects_bp)

    # Extracted legacy groups.
    from routes.research import research_bp
    app_inst.register_blueprint(research_bp)

    # Phase 4 (C20): worker startup wiring. Starts exactly one daemon worker
    # thread through the application factory.
    if config and config.get("START_PROPAGATION_WORKER"):
        from propagation.worker import PropagationWorker, should_start_worker
        if should_start_worker():
            worker = PropagationWorker(app_inst)
            worker.start()
            app_inst.config["PROPAGATION_WORKER"] = worker

    # Crash/restart safety: any propagation job left in 'running' by a wave that
    # was interrupted (server restart, dev-reloader reload, process kill) would
    # otherwise stay 'running' forever — no proposals, no status updates, and a
    # UI that times out. Reset those to 'pending' so the wave can be re-run.
    try:
        with app_inst.app_context():
            from propagation.worker import recover_stuck_running
            recover_stuck_running()
    except Exception:
        pass

    return app_inst

legacy_bp = Blueprint("legacy", __name__)


def get_client(endpoint: dict = None) -> OpenAI:
    # The OpenAI SDK uses base_url as a literal prefix — it does NOT
    # automatically append /v1.  Because different OpenAI-compatible
    # providers expect different paths, we use whatever the user
    # configured verbatim (only trimming a trailing slash).  Examples:
    #   OpenAI    -> https://api.openai.com/v1
    #   GSA USAi  -> https://api.gsa.usai.gov/api/v1
    #   LMStudio  -> http://localhost:1234/v1
    #   Ollama    -> http://localhost:11434/v1
    #
    # An `endpoint` dict (from the endpoints table) takes priority; otherwise
    # we fall back to the DB default endpoint.
    if endpoint is None:
        endpoint = db.get_default_endpoint()

    if endpoint:
        base_url = (endpoint.get("base_url") or "").strip().rstrip("/")
        api_key  = (endpoint.get("api_key") or "").strip()
    else:
        base_url = ""
        api_key  = ""

    # Fall back to environment variables if no key is configured in the database
    if not api_key:
        if "openrouter.ai" in base_url:
            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        elif "googleapis.com" in base_url:
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        else:
            api_key = os.environ.get("OPENAI_API_KEY") or ""

    # Local servers (LMStudio, Ollama, etc.) often don't require an API key,
    # but the OpenAI SDK still needs a non-empty string, so provide a
    # harmless placeholder when one isn't configured.
    api_key = api_key or "not-needed"

    return OpenAI(
        api_key=api_key,
        base_url=base_url or None,
    )


def _endpoint_for_conversation(conv: dict) -> dict | None:
    """Resolve which endpoint a conversation should use: its own, else default."""
    ep_id = conv.get("endpoint_id")
    if ep_id:
        ep = db.get_endpoint(ep_id)
        if ep:
            return ep
    return db.get_default_endpoint()



# ── Static ─────────────────────────────────────────────────────────────────────

@legacy_bp.route("/")
def index():
    return send_from_directory(current_app.static_folder, "index.html")


# ── Models ─────────────────────────────────────────────────────────────────────

@legacy_bp.route("/api/models", methods=["GET"])
def get_models():
    try:
        # Allow the caller to request models for a specific endpoint.
        endpoint = None
        ep_id = request.args.get("endpoint_id")
        if ep_id:
            endpoint = db.get_endpoint(int(ep_id))
        else:
            # Allow probing an ad-hoc endpoint (used by the New Endpoint form
            # before the endpoint has been saved) via query parameters.
            base_url = request.args.get("base_url")
            api_key  = request.args.get("api_key")
            if base_url:
                endpoint = {"base_url": base_url, "api_key": api_key or ""}
        client = get_client(endpoint)
        models = client.models.list()
        model_list = sorted([m.id for m in models.data])
        return jsonify({"models": model_list})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


# ── Endpoints ──────────────────────────────────────────────────────────────────

def _public_endpoint(ep: dict) -> dict:
    """Strip the raw API key from an endpoint before returning it to the client;
    expose only whether a key is set."""
    if not ep:
        return ep
    out = {k: v for k, v in ep.items() if k != "api_key"}
    out["api_key_set"] = bool((ep.get("api_key") or "").strip())
    out["is_default"] = bool(ep.get("is_default"))
    out["default_model"] = ep.get("default_model") or ""
    out["model_filter"] = ep.get("model_filter") or ""
    return out


@legacy_bp.route("/api/endpoints", methods=["GET"])
def list_endpoints():
    return jsonify([_public_endpoint(e) for e in db.list_endpoints()])


@legacy_bp.route("/api/endpoints", methods=["POST"])
def create_endpoint():
    data = request.get_json(force=True)
    name     = (data.get("name") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    api_key  = (data.get("api_key") or "").strip()
    default_model = (data.get("default_model") or "").strip()
    model_filter  = (data.get("model_filter") or "").strip()
    is_default = bool(data.get("is_default"))
    if not name or not base_url:
        return jsonify({"error": "name and base_url are required"}), 400
    ep = db.create_endpoint(name=name, base_url=base_url, api_key=api_key,
                            default_model=default_model, is_default=is_default,
                            model_filter=model_filter)
    return jsonify(_public_endpoint(ep)), 201


@legacy_bp.route("/api/endpoints/<int:endpoint_id>", methods=["PUT"])
def update_endpoint(endpoint_id):
    ep = db.get_endpoint(endpoint_id)
    if not ep:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    name     = data.get("name")
    base_url = data.get("base_url")
    # Only overwrite the key when a non-empty value is supplied.
    api_key  = data.get("api_key")
    if api_key is not None and not str(api_key).strip():
        api_key = None
    default_model = data.get("default_model")
    model_filter  = data.get("model_filter")
    is_default = data.get("is_default") if "is_default" in data else None
    updated = db.update_endpoint(
        endpoint_id,
        name=(name.strip() if isinstance(name, str) else None),
        base_url=(base_url.strip() if isinstance(base_url, str) else None),
        api_key=(api_key.strip() if isinstance(api_key, str) else None),
        default_model=(default_model.strip() if isinstance(default_model, str) else None),
        is_default=(bool(is_default) if is_default is not None else None),
        model_filter=(model_filter.strip() if isinstance(model_filter, str) else None),
    )
    return jsonify(_public_endpoint(updated))


@legacy_bp.route("/api/endpoints/<int:endpoint_id>", methods=["DELETE"])
def delete_endpoint(endpoint_id):
    ep = db.get_endpoint(endpoint_id)
    if not ep:
        return jsonify({"error": "Not found"}), 404
    db.delete_endpoint(endpoint_id)
    return jsonify({"ok": True})


# ── Settings ───────────────────────────────────────────────────────────────────

@legacy_bp.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({
        "output_dir":     db.get_setting("output_dir")     or "",
        "browser_root":   db.get_setting("browser_root")   or "",
        "allow_local_file_access": int(db.local_file_access_enabled()),
    })


@legacy_bp.route("/api/settings", methods=["PUT"])
def update_settings():
    data = request.get_json(force=True)
    if "output_dir" in data:
        db.set_setting("output_dir", data["output_dir"])
    if "browser_root" in data:
        db.set_setting("browser_root", data["browser_root"])
    if "allow_local_file_access" in data:
        db.set_setting("allow_local_file_access", "1" if data["allow_local_file_access"] else "0")
    return jsonify({"ok": True})


@legacy_bp.route("/api/settings/reset", methods=["POST"])
def reset_settings():
    """Reset the endpoint default model,
    browser starting path and default output folder to empty."""
    result = db.reset_settings()
    return jsonify({"ok": True, "settings": result})


@legacy_bp.route("/api/open-output-dir", methods=["POST"])
def open_output_dir():
    """Open the active conversation's effective output folder locally."""
    data = request.get_json(silent=True) or {}
    conversation_id = data.get("conversation_id")
    conversation = db.get_conversation(int(conversation_id)) if conversation_id else None
    output_dir = (conversation or {}).get("output_dir") or db.get_setting("output_dir") or ""
    output_dir = os.path.abspath(os.path.expanduser(output_dir.strip())) if output_dir.strip() else ""

    if not output_dir:
        return jsonify({"error": "No output folder is configured."}), 400
    if not os.path.isdir(output_dir):
        return jsonify({"error": f"Output folder does not exist: {output_dir}"}), 400

    try:
        if os.name == "nt":
            os.startfile(output_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", output_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            opener = shutil.which("xdg-open") or shutil.which("gio")
            if not opener:
                return jsonify({"error": "No file manager opener is available on this system."}), 500
            command = [opener, "open", output_dir] if os.path.basename(opener) == "gio" else [opener, output_dir]
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return jsonify({"error": f"Could not open output folder: {exc}"}), 500

    return jsonify({"ok": True, "path": output_dir})


# ── Conversations ──────────────────────────────────────────────────────────────

@legacy_bp.route("/api/conversations", methods=["GET"])
def list_conversations():
    return jsonify(db.list_conversations())


@legacy_bp.route("/api/conversations", methods=["POST"])
def create_conversation():
    data = request.get_json(force=True)
    endpoint_id = data.get("endpoint_id") or None
    # Resolve the endpoint that will be used so we can fall back to its
    # per-endpoint default model when the client doesn't specify one.
    ep = db.get_endpoint(endpoint_id) if endpoint_id else db.get_default_endpoint()
    model_id = (
        data.get("model_id")
        or (ep.get("default_model") if ep else "")
        or ""
    )
    title = data.get("title", "New Conversation")
    persona_id = data.get("persona_id") or None
    folder_id  = data.get("folder_id") or None
    conv = db.create_conversation(title=title, model_id=model_id, persona_id=persona_id, endpoint_id=endpoint_id, folder_id=folder_id)
    return jsonify(conv), 201


@legacy_bp.route("/api/conversations/<int:conv_id>", methods=["GET"])
def get_conversation(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    messages = db.get_messages(conv_id)
    return jsonify({**conv, "messages": messages})


@legacy_bp.route("/api/conversations/<int:conv_id>", methods=["PUT"])
def update_conversation(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    # persona_id=0 or null both mean "clear the persona"
    raw_persona   = data.get("persona_id")
    persona_id    = int(raw_persona) if raw_persona else None
    clear_persona = "persona_id" in data and not raw_persona
    output_dir    = data.get("output_dir")  # None means "don't touch it"
    enable_tools  = data.get("enable_tools") if "enable_tools" in data else None
    # endpoint_id=0 or null both mean "clear/use default endpoint"
    raw_endpoint   = data.get("endpoint_id")
    endpoint_id    = int(raw_endpoint) if raw_endpoint else None
    clear_endpoint = "endpoint_id" in data and not raw_endpoint
    # folder_id=0 or null both mean "remove from folder"
    raw_folder   = data.get("folder_id")
    folder_id    = int(raw_folder) if raw_folder else None
    clear_folder = "folder_id" in data and not raw_folder
    
    raw_archived = data.get("archived")
    archived = bool(raw_archived) if raw_archived is not None else None

    db.update_conversation(
        conv_id,
        title=data.get("title"),
        model_id=data.get("model_id"),
        persona_id=persona_id,
        clear_persona=clear_persona,
        output_dir=output_dir,
        enable_tools=enable_tools,
        endpoint_id=endpoint_id,
        clear_endpoint=clear_endpoint,
        folder_id=folder_id,
        clear_folder=clear_folder,
        archived=archived,
    )
    return jsonify(db.get_conversation(conv_id))


@legacy_bp.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    db.delete_conversation(conv_id)
    return jsonify({"ok": True})


# ── Chat ───────────────────────────────────────────────────────────────────────

@legacy_bp.route("/api/conversations/<int:conv_id>/chat", methods=["POST"])
def chat(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404

    data = request.get_json(force=True)
    user_content = data.get("message", "").strip()
    if not user_content:
        return jsonify({"error": "Empty message"}), 400

    # Auto-generate title from the first user message, but only when the
    # conversation still has its default name — a custom/user-set name must
    # never be overwritten.
    _default_title = "New Conversation"
    has_default_title = not (conv["title"] or "").strip() or (conv["title"] or "").strip() == _default_title

    # Persist the user message
    db.add_message(conv_id, "user", user_content)
    db.touch_conversation(conv_id)

    # Auto-generate title from the first user message
    messages_so_far = db.get_messages(conv_id)
    user_messages = [m for m in messages_so_far if m["role"] == "user"]
    if len(user_messages) == 1 and has_default_title:
        auto_title = _make_title(user_content)
        db.update_conversation(conv_id, title=auto_title)

    # Fix #10: resolve endpoint once; reuse for both model_id fallback and client
    endpoint = _endpoint_for_conversation(conv)
    model_id = conv["model_id"] or (endpoint or {}).get("default_model") or ""

    # Build the file/artifact context ONCE so the system prompt and the SSE
    # scope disclosure share the same snapshot (no double build per request).
    from conversation_context import build_conversation_context
    context_section, scope_meta = build_conversation_context(
        conv, conv_id, max_chars=_context_char_budget()
    )
    base_system, output_dir = _build_system_prompt(
        conv, conv_id, _tools_enabled(conv), context_section=context_section,
    )
    tools_on = _tools_enabled(conv)
    system_prompt = {"role": "system", "content": base_system}

    # Fix #3: reuse already-fetched messages for API history (no second DB query)
    tool_names = _chat_tool_names(user_content, context_section) if tools_on else set()
    tools = build_tools("chat", tool_names) if tools_on else None
    history = compact_api_history(
        [system_prompt] + _build_api_messages(messages_so_far),
        max(1, MODEL_CONTEXT_BUDGET - MODEL_CONTEXT_RESERVE),
        keep_recent=MODEL_HISTORY_KEEP_RECENT,
    )

    client = get_client(endpoint)

    def record_usage(usage: dict):
        usage["model_id"] = model_id
        usage["provider"] = (endpoint or {}).get("base_url", "")
        usage["cost_usd"] = _usage_cost(
            model_id, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        )
        db.record_token_usage(conv_id, **usage)
        return None

    def generate():
        """Stream SSE events back to the browser."""
        # Send updated title if this was the first message (and the
        # conversation still had the default name).
        if len(user_messages) == 1 and has_default_title:
            yield sse_event({"type": "title", "title": _make_title(user_content), "conv_id": conv_id})

        # Disclose what context was injected for this message (scope/tokens/warn)
        yield sse_event({"type": "scope", "meta": scope_meta})

        def execute_tool_fn(fn_name, fn_args, output_dir):
            result = execute_tool_call(
                fn_name, fn_args, output_dir=output_dir, conv_id=conv_id,
            )
            return result

        # Use the extracted chat service
        events = run_chat_turn(
            client=client,
            model_id=model_id,
            messages=history,
            tools=tools,
            tool_choice="auto" if tools_on else None,
            max_iterations=MODEL_MAX_TOOL_ITERATIONS,
            execute_tool_fn=execute_tool_fn,
            output_dir=output_dir,
            record_usage_fn=record_usage,
            max_context_tokens=MODEL_CONTEXT_BUDGET,
            max_output_tokens=MODEL_MAX_OUTPUT_TOKENS,
            request_extra_body=MODEL_REQUEST_EXTRA_BODY or None,
        )

        # Process events and persist messages
        pending_tool_calls = None
        pending_assistant_content = ""

        try:
            for event in events:
                if event["type"] == "token":
                    yield sse_event(event)

                elif event["type"] == "assistant_message":
                    pending_assistant_content = event.get("content", "")
                    if event.get("tool_calls"):
                        pending_tool_calls = event["tool_calls"]
                        # Persist assistant message with tool calls
                        db.add_message(
                            conv_id,
                            role="assistant",
                            content=pending_assistant_content,
                            tool_calls_json=json.dumps(pending_tool_calls),
                        )
                        # Add to history for next iteration
                        history.append({
                            "role": "assistant",
                            "content": pending_assistant_content or None,
                            "tool_calls": pending_tool_calls,
                        })
                    else:
                        # Plain assistant message
                        if pending_assistant_content:
                            db.add_message(conv_id, role="assistant", content=pending_assistant_content)
                        yield sse_event({"type": "done"})
                        return

                elif event["type"] == "tool_result":
                    # The tool was executed by chat_service, we just yield the result
                    yield sse_event(event)
                    # After a successful Markdown write the frontend refreshes
                    # its document panel.
                    if event.get("document"):
                        yield sse_event({"type": "document_created", "document": event["document"]})

                elif event["type"] == "tool_message":
                    # Persist tool result message
                    db.add_message(
                        conv_id,
                        role="tool",
                        content=event["content"],
                        tool_call_id=event["tool_call_id"],
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": event["tool_call_id"],
                        "content": _cap_tool_content(event["content"]),
                    })

                elif event["type"] == "error":
                    yield sse_event(event)
                    return

                elif event["type"] == "done":
                    yield sse_event(event)
                    return
        finally:
            # After a BA-inbox turn, fold any requirement docs the BA wrote via
            # write_file into the registered BA-REQ artifact so scanning and
            # propagation can react. Deterministic and guarded: never breaks the
            # stream, and is a no-op for non-BA conversations.
            try:
                from propagation.consolidate import consolidate_ba_output
                consolidate_ba_output(conv_id)
            except Exception:
                pass

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@legacy_bp.route("/api/conversations/<int:conv_id>/regenerate", methods=["POST"])
def regenerate(conv_id):
    """
    Delete the last assistant message (and any preceding tool messages) then
    re-run the model — streaming the new response as SSE.
    Fix #1: shares _build_system_prompt and _build_api_messages with chat(),
    eliminating the duplicated generate() closure.
    Fix #6: removed dead flask/request imports and unused ctx variable.
    """
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404

    # Remove trailing assistant+tool messages so we can regenerate
    db.delete_last_assistant_turn(conv_id)

    messages = db.get_messages(conv_id)
    if not messages or messages[-1]["role"] != "user":
        return jsonify({"error": "No user message to regenerate from"}), 400

    # Fix #10: resolve endpoint once; reuse for both model_id fallback and client
    endpoint = _endpoint_for_conversation(conv)
    model_id = conv["model_id"] or (endpoint or {}).get("default_model") or ""

    # Build context once and reuse for the SSE scope disclosure (no double build).
    from conversation_context import build_conversation_context
    context_section, scope_meta = build_conversation_context(
        conv, conv_id, max_chars=_context_char_budget()
    )
    base_system, output_dir = _build_system_prompt(
        conv, conv_id, _tools_enabled(conv), context_section=context_section,
    )
    tools_on = _tools_enabled(conv)
    tool_names = _chat_tool_names(messages[-1].get("content", ""), context_section) if tools_on else set()
    tools = build_tools("chat", tool_names) if tools_on else None
    history = compact_api_history(
        [{"role": "system", "content": base_system}] + _build_api_messages(messages),
        max(1, MODEL_CONTEXT_BUDGET - MODEL_CONTEXT_RESERVE),
        keep_recent=MODEL_HISTORY_KEEP_RECENT,
    )
    client = get_client(endpoint)

    def record_usage(usage: dict):
        usage["model_id"] = model_id
        usage["provider"] = (endpoint or {}).get("base_url", "")
        usage["cost_usd"] = _usage_cost(
            model_id, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        )
        db.record_token_usage(conv_id, **usage)
        return None

    def generate():
        # Disclose what context was injected for this message (scope/tokens/warn)
        yield sse_event({"type": "scope", "meta": scope_meta})

        def execute_tool_fn(fn_name, fn_args, output_dir):
            result = execute_tool_call(
                fn_name, fn_args, output_dir=output_dir, conv_id=conv_id,
            )
            return result

        events = run_chat_turn(
            client=client,
            model_id=model_id,
            messages=history,
            tools=tools,
            tool_choice="auto" if tools_on else None,
            max_iterations=MODEL_MAX_TOOL_ITERATIONS,
            execute_tool_fn=execute_tool_fn,
            output_dir=output_dir,
            record_usage_fn=record_usage,
            max_context_tokens=MODEL_CONTEXT_BUDGET,
            max_output_tokens=MODEL_MAX_OUTPUT_TOKENS,
            request_extra_body=MODEL_REQUEST_EXTRA_BODY or None,
        )

        pending_assistant_content = ""

        try:
            for event in events:
                if event["type"] == "token":
                    yield sse_event(event)

                elif event["type"] == "assistant_message":
                    pending_assistant_content = event.get("content", "")
                    if event.get("tool_calls"):
                        # Persist assistant message with tool calls
                        db.add_message(
                            conv_id,
                            role="assistant",
                            content=pending_assistant_content,
                            tool_calls_json=json.dumps(event["tool_calls"]),
                        )
                        history.append({
                            "role": "assistant",
                            "content": pending_assistant_content or None,
                            "tool_calls": event["tool_calls"],
                        })
                    else:
                        if pending_assistant_content:
                            db.add_message(conv_id, role="assistant", content=pending_assistant_content)
                        yield sse_event({"type": "done"})
                        return

                elif event["type"] == "tool_result":
                    yield sse_event(event)

                elif event["type"] == "tool_message":
                    db.add_message(
                        conv_id,
                        role="tool",
                        content=event["content"],
                        tool_call_id=event["tool_call_id"],
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": event["tool_call_id"],
                        "content": _cap_tool_content(event["content"]),
                    })

                elif event["type"] == "error":
                    yield sse_event(event)
                    return

                elif event["type"] == "done":
                    yield sse_event(event)
                    return
        finally:
            # Same post-BA-chat consolidation as /chat (see above).
            try:
                from propagation.consolidate import consolidate_ba_output
                consolidate_ba_output(conv_id)
            except Exception:
                pass

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@legacy_bp.route("/api/conversations/<int:conv_id>/messages/<int:msg_id>", methods=["PUT"])
def edit_message(conv_id, msg_id):
    """Edit a user message and delete all subsequent messages so the conversation can be re-sent."""
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    new_content = data.get("content", "").strip()
    if not new_content:
        return jsonify({"error": "content is required"}), 400
    db.edit_message_and_truncate(conv_id, msg_id, new_content)
    return jsonify({"ok": True})


@legacy_bp.route("/api/conversations/<int:conv_id>/search", methods=["GET"])
def search_conversation(conv_id):
    """Search messages in a conversation by keyword."""
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify([])
    messages = db.get_messages(conv_id)
    results = [m for m in messages if query in (m.get("content") or "").lower()]
    return jsonify(results)


@legacy_bp.route("/api/search", methods=["GET"])
def search_all():
    """Search across all conversations by keyword. Returns matching conversations with snippet."""
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify([])
    results = db.search_all_conversations(query)
    return jsonify(results)


@legacy_bp.route("/api/conversations/<int:conv_id>/token-count", methods=["GET"])
def token_count(conv_id):
    """Return an estimated token count for the next model request."""
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    messages = db.get_messages(conv_id)
    from conversation_context import build_conversation_context

    context_section, scope_meta = build_conversation_context(
        conv, conv_id, max_chars=_context_char_budget()
    )
    tools_on = _tools_enabled(conv)
    system_prompt, _output_dir = _build_system_prompt(
        conv, conv_id, tools_on, context_section=context_section,
    )
    api_messages = compact_api_history(
        [{"role": "system", "content": system_prompt}] + _build_api_messages(messages),
        max(1, MODEL_CONTEXT_BUDGET - MODEL_CONTEXT_RESERVE),
        keep_recent=MODEL_HISTORY_KEEP_RECENT,
    )
    last_user_content = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    tool_names = _chat_tool_names(last_user_content, context_section) if tools_on else set()
    tools = build_tools("chat", tool_names) if tools_on else None
    message_tokens = estimate_messages_tokens(api_messages)
    tool_tokens = estimate_request_tokens([], tools, conv.get("model_id") or "")
    estimated_tokens = message_tokens + tool_tokens
    message_chars = sum(len(str(m.get("content") or "")) for m in api_messages)
    file_chars = scope_meta.get("chars", 0)
    return jsonify({
        "estimated_tokens": estimated_tokens,
        "message_chars": message_chars,
        "file_chars": file_chars,
        "total_chars": message_chars,
        "prompt_tokens": message_tokens,
        "tool_schema_tokens": tool_tokens,
        "history_compacted": len(api_messages) < len(messages) + 1,
        "last_usage": db.last_token_usage(conv_id),
    })


@legacy_bp.route("/api/conversations/<int:conv_id>/compact", methods=["POST"])
def compact_conversation_route(conv_id):
    """Drop older turns so the model only sees the recent conversation context.

    Keeps the opening user message for task context plus the final N messages;
    everything in between is removed from the conversation history (and thus
    out of the model context window on subsequent turns).
    """
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Conversation not found"}), 404
    result = db.compact_conversation(conv_id)
    db.touch_conversation(conv_id)
    return jsonify(result)


@legacy_bp.route("/api/conversations/<int:conv_id>/files/<int:file_id>/preview", methods=["GET"])
def preview_file(conv_id, file_id):
    """Return the extracted text content of an uploaded file for preview."""
    record = db.get_conv_file(file_id)
    if not record or record["conversation_id"] != conv_id:
        return jsonify({"error": "Not found"}), 404
    text, truncated = extract_text(record["disk_path"], record["original_name"])
    return jsonify({
        "original_name": record["original_name"],
        "content": text,
        "truncated": truncated,
        "char_count": len(text),
    })


# ── Personas ───────────────────────────────────────────────────────────────────

@legacy_bp.route("/api/personas", methods=["GET"])
def list_personas():
    return jsonify(db.list_personas())


@legacy_bp.route("/api/personas", methods=["POST"])
def create_persona():
    data = request.get_json(force=True)
    name   = data.get("name", "").strip()
    prompt = data.get("prompt", "").strip()
    if not name or not prompt:
        return jsonify({"error": "name and prompt are required"}), 400
    try:
        persona = db.create_persona(name, prompt)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(persona), 201


@legacy_bp.route("/api/personas/<int:persona_id>", methods=["PUT"])
def update_persona(persona_id):
    persona = db.get_persona(persona_id)
    if not persona:
        return jsonify({"error": "Not found"}), 404
    data   = request.get_json(force=True)
    name   = data.get("name", "").strip() or None
    prompt = data.get("prompt", "").strip() or None
    try:
        db.update_persona(persona_id, name=name, prompt=prompt)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(db.get_persona(persona_id))


@legacy_bp.route("/api/personas/<int:persona_id>", methods=["DELETE"])
def delete_persona(persona_id):
    persona = db.get_persona(persona_id)
    if not persona:
        return jsonify({"error": "Not found"}), 404
    db.delete_persona(persona_id)
    return jsonify({"ok": True})


# ── Conversation files ─────────────────────────────────────────────────────────

@legacy_bp.route("/api/conversations/<int:conv_id>/files", methods=["GET"])
def list_files(conv_id):
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    return jsonify(db.list_conv_files(conv_id))


@legacy_bp.route("/api/conversations/<int:conv_id>/files", methods=["POST"])
def upload_file(conv_id):
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Conversation not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    # original_name preserves relative folder path e.g. "requirements/req1.docx"
    original_name = f.filename.replace("\\", "/")  # normalise to forward slashes
    base_name     = os.path.basename(original_name)

    if not allowed_extension(base_name):
        return jsonify({"error": f"File type not supported: {os.path.splitext(base_name)[1]}"}), 415

    # Save to disk with a unique name to avoid collisions
    upload_dir = ensure_upload_dir(conv_id)
    ext        = os.path.splitext(base_name)[1].lower()
    disk_name  = f"{uuid.uuid4().hex}{ext}"
    disk_path  = os.path.join(upload_dir, disk_name)
    f.save(disk_path)

    size_bytes = os.path.getsize(disk_path)

    # Extract text and count characters for size warning
    text, truncated = extract_text(disk_path, base_name)
    chars = len(text)
    snippet = text[:500]  # store a short preview for later system‑prompt use

    record = db.add_conv_file(
        conversation_id=conv_id,
        original_name=original_name,   # store relative path so UI shows folder context
        disk_path=disk_path,
        size_bytes=size_bytes,
        char_count=chars,
        snippet=snippet,
    )

    return jsonify({
        **record,
        "warn_large": chars > WARN_THRESHOLD,
        "truncated":  truncated,
    }), 201


@legacy_bp.route("/api/conversations/<int:conv_id>/files/<int:file_id>", methods=["DELETE"])
def delete_file(conv_id, file_id):
    record = db.get_conv_file(file_id)
    if not record or record["conversation_id"] != conv_id:
        return jsonify({"error": "Not found"}), 404

    # Remove from disk
    try:
        if os.path.exists(record["disk_path"]):
            os.remove(record["disk_path"])
    except OSError:
        pass  # log but don't block deletion
    evict_upload_cache(record["disk_path"])

    db.delete_conv_file(file_id)
    return jsonify({"ok": True})


# ── Markdown documents (unified output + upload backends) ────────────────────

@legacy_bp.route("/api/conversations/<int:conv_id>/documents", methods=["GET"])
def list_documents_route(conv_id):
    """List Markdown documents available to the conversation."""
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    try:
        return jsonify({"documents": list_documents_service(conv_id)})
    except DocumentError as exc:
        return jsonify(exc.payload), exc.status


def _valid_document_id(document_id: str) -> bool:
    return bool(document_id) and (document_id.startswith("output:") or document_id.startswith("upload:"))


@legacy_bp.route("/api/conversations/<int:conv_id>/documents/<path:document_id>", methods=["GET"])
def read_document_route(conv_id, document_id):
    """Read one Markdown document (raw content; rendering happens client-side)."""
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    if not _valid_document_id(document_id):
        return jsonify({"error": "invalid_document_id", "message": "Malformed document identifier."}), 400
    try:
        return jsonify(read_document_service(conv_id, document_id))
    except DocumentError as exc:
        return jsonify(exc.payload), exc.status


@legacy_bp.route("/api/conversations/<int:conv_id>/documents/<path:document_id>", methods=["PUT"])
def update_document_route(conv_id, document_id):
    """Save a document with hash-based optimistic concurrency + atomic replace."""
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    if not _valid_document_id(document_id):
        return jsonify({"error": "invalid_document_id", "message": "Malformed document identifier."}), 400

    if request.content_length is not None and request.content_length > MAX_DOC_REQUEST_BYTES:
        return jsonify({
            "error": "document_too_large",
            "message": f"Document exceeds the {MAX_EDITABLE_BYTES // (1024 * 1024)} MB edit limit.",
        }), 413

    data = request.get_json(silent=True) or {}
    content = data.get("content")
    expected_hash = data.get("expected_hash")
    if not isinstance(content, str):
        return jsonify({"error": "invalid_content", "message": "Document content must be text."}), 400
    try:
        result = update_document_service(conv_id, document_id, content, expected_hash)
        return jsonify(result), 200
    except DocumentError as exc:
        return jsonify(exc.payload), exc.status


@legacy_bp.route("/api/conversations/<int:conv_id>/documents/<path:document_id>/rename", methods=["POST"])
def rename_document_route(conv_id, document_id):
    """Rename a Markdown document (jail-checked on the server)."""
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    if not _valid_document_id(document_id):
        return jsonify({"error": "invalid_document_id", "message": "Malformed document identifier."}), 400
    data = request.get_json(silent=True) or {}
    try:
        result = rename_document_service(conv_id, document_id, data.get("name"))
        return jsonify(result), 200
    except DocumentError as exc:
        return jsonify(exc.payload), exc.status


@legacy_bp.route("/api/conversations/<int:conv_id>/documents/<path:document_id>/export", methods=["GET", "POST"])
def export_document_route(conv_id, document_id):
    """Export a Markdown document.

    GET  -> exports the saved server version.
    POST -> exports an explicit ``{ "content": ... }`` payload, so unsaved
            editor edits are reflected.
    Only ``format=docx`` is supported.
    """
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    if not _valid_document_id(document_id):
        return jsonify({"error": "invalid_document_id", "message": "Malformed document identifier."}), 400

    fmt = request.args.get("format", "docx")
    if fmt != "docx":
        return jsonify({"error": "unsupported_format", "message": f"Unsupported export format: {fmt}."}), 400

    data = request.get_json(silent=True) or {}
    content = data.get("content") if isinstance(data.get("content"), str) else None
    if content is None:
        try:
            doc = read_document_service(conv_id, document_id)
        except DocumentError as exc:
            return jsonify(exc.payload), exc.status
        content = doc["content"]
        name = doc["name"]
    else:
        name = data.get("name") or "document.md"

    base = os.path.splitext(os.path.basename(name))[0] or "document"
    try:
        payload = export_document_docx(content)
    except DocumentError as exc:
        return jsonify(exc.payload), exc.status
    return Response(
        payload,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{base}.docx"'},
    )


# ── Import Markdown from the output folder ───────────────────────────────────

IMPORT_EXTS = {".md", ".markdown", ".txt"}


@legacy_bp.route("/api/conversations/<int:conv_id>/import-browse", methods=["GET"])
def import_browse(conv_id):
    """Browse the conversation's effective output directory (jail-scoped).

    ``?path`` is a relative path inside the output directory. Only folders and
    Markdown/txt files are listed; symlinks resolving outside the root are
    skipped. The OS file picker cannot start in a chosen folder, so this
    server-side browser gives the Import Markdown action its starting point.
    """
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    output_dir = effective_output_dir(conv)
    if not output_dir:
        return jsonify({"error": "no_output_dir",
                        "message": "No output folder is configured for this conversation."}), 400

    rel = (request.args.get("path") or "").strip()
    if rel:
        current_dir = resolve_output_path_for(conv, rel)
    else:
        current_dir = os.path.realpath(output_dir)
    if current_dir is None:
        return jsonify({"error": "invalid_path", "message": "That path is not allowed."}), 400
    if not os.path.isdir(current_dir):
        return jsonify({"error": "invalid_path", "message": "Not a directory."}), 400

    root = os.path.realpath(output_dir)
    current_rel = os.path.relpath(current_dir, root).replace("\\", "/")
    if current_rel == ".":
        current_rel = ""

    entries = []
    try:
        with os.scandir(current_dir) as it:
            scan = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return jsonify({"error": "permission_denied", "message": "Permission denied."}), 403

    for entry in scan:
        if entry.name.startswith("."):
            continue
        real = os.path.realpath(entry.path)
        if os.path.commonpath([root, real]) != root:
            continue  # symlink escaping the output folder
        child_rel = (current_rel + "/" + entry.name) if current_rel else entry.name
        if entry.is_dir():
            entries.append({
                "name": entry.name, "is_dir": True, "ext": "", "size_bytes": 0,
                "rel_path": child_rel,
            })
        elif entry.is_file() and os.path.splitext(entry.name)[1].lower() in IMPORT_EXTS:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            entries.append({
                "name": entry.name, "is_dir": False,
                "ext": os.path.splitext(entry.name)[1].lower(),
                "size_bytes": size, "rel_path": child_rel,
            })

    up = None
    if current_rel:
        parent = os.path.dirname(current_rel)
        up = "" if not parent else parent

    return jsonify({
        "output_dir": output_dir,
        "current": current_rel,
        "up": up,
        "entries": entries,
    })


@legacy_bp.route("/api/conversations/<int:conv_id>/import-from-output", methods=["POST"])
def import_from_output(conv_id):
    """Copy a Markdown file from the output folder into the conversation and
    register it as an upload so it shows as context and in the doc panel."""
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    rel = (data.get("path") or "").strip()
    if not rel:
        return jsonify({"error": "invalid_path", "message": "A file path is required."}), 400

    output_dir = effective_output_dir(conv)
    if not output_dir:
        return jsonify({"error": "no_output_dir",
                        "message": "No output folder is configured for this conversation."}), 400
    abs_path = resolve_output_path_for(conv, rel)
    if abs_path is None:
        return jsonify({"error": "invalid_path", "message": "That path is not allowed."}), 400
    if not os.path.isfile(abs_path):
        return jsonify({"error": "not_found", "message": "File not found."}), 404

    base_name = os.path.basename(abs_path)
    if not is_markdown_name(base_name) and os.path.splitext(base_name)[1].lower() != ".txt":
        return jsonify({"error": "invalid_file",
                        "message": "Only Markdown or text files can be imported."}), 415

    upload_dir = ensure_upload_dir(conv_id)
    ext = os.path.splitext(base_name)[1].lower()
    disk_name = f"{uuid.uuid4().hex}{ext}"
    disk_path = os.path.join(upload_dir, disk_name)
    try:
        shutil.copyfile(abs_path, disk_path)
    except OSError as exc:
        return jsonify({"error": "import_failed", "message": f"Could not read the file: {exc}"}), 500

    size_bytes = os.path.getsize(disk_path)
    text, truncated = extract_text(disk_path, base_name)
    record = db.add_conv_file(
        conversation_id=conv_id,
        original_name=base_name,
        disk_path=disk_path,
        size_bytes=size_bytes,
        char_count=len(text),
        snippet=text[:500],
    )

    payload = {
        **record,
        "success": True,
        "truncated": truncated,
        "warn_large": len(text) > WARN_THRESHOLD,
    }
    if is_markdown_name(base_name):
        payload["document"] = {
            "id": f"upload:{record['id']}",
            "name": base_name,
            "kind": "upload",
            "size_bytes": size_bytes,
            "content_hash": db.compute_content_hash(_raw_markdown(disk_path)),
        }
    return jsonify(payload), 201


def _raw_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ── Folder browser ─────────────────────────────────────────────────────────────

@legacy_bp.route("/api/browse")
def browse():
    """
    Return the contents of a directory one level at a time.
    Query params:
        path  — absolute path to list (defaults to browser_root or drive root)
    Returns:
        { path, parent, entries: [{name, is_dir, ext, size_bytes}] }
    Fix #8: use os.scandir() to avoid double stat calls per entry.
    Fix #11: removed unused `import string`.
    """
    raw_path = request.args.get("path", "").strip()

    # Default to browser_root setting, then the first available drive / home dir
    if not raw_path:
        raw_path = db.get_setting("browser_root") or ""
    if not raw_path:
        raw_path = os.path.expanduser("~")

    path = os.path.normpath(raw_path)

    if not os.path.isdir(path):
        return jsonify({"error": f"Not a directory: {path}"}), 400

    try:
        # os.scandir gives us is_dir()/stat() from a single syscall per entry
        with os.scandir(path) as it:
            scan_entries = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    entries = []
    for entry in scan_entries:
        if entry.name.startswith("."):
            continue
        is_dir = entry.is_dir()
        try:
            size = 0 if is_dir else entry.stat().st_size
        except OSError:
            size = 0
        ext = "" if is_dir else os.path.splitext(entry.name)[1].lower()
        entries.append({
            "name":       entry.name,
            "is_dir":     is_dir,
            "ext":        ext,
            "size_bytes": size,
        })

    # Parent path (None if already at root)
    parent = str(os.path.dirname(path))
    if parent == path:
        parent = None

    return jsonify({
        "path":    str(path),
        "parent":  parent,
        "entries": entries,
    })


# ── Linked folders ─────────────────────────────────────────────────────────────

@legacy_bp.route("/api/conversations/<int:conv_id>/linked-folders", methods=["GET"])
def list_linked_folders(conv_id):
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    folders = db.list_linked_folders(conv_id)
    # Annotate each with a live file count and whether it's a single file
    result = []
    for f in folders:
        file_entries = scan_linked_folder(f["folder_path"])
        result.append({
            **f,
            "file_count": len(file_entries),
            "is_file": os.path.isfile(f["folder_path"]),
        })
    return jsonify(result)


@legacy_bp.route("/api/conversations/<int:conv_id>/linked-folders", methods=["POST"])
def add_linked_folder(conv_id):
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Conversation not found"}), 404
    data        = request.get_json(force=True)
    folder_path = data.get("folder_path", "").strip()
    if not folder_path:
        return jsonify({"error": "folder_path is required"}), 400
    folder_path = os.path.normpath(folder_path)
    is_file = os.path.isfile(folder_path)
    if not is_file and not os.path.isdir(folder_path):
        return jsonify({"error": f"Not a file or directory: {folder_path}"}), 400
    if is_file and not allowed_extension(os.path.basename(folder_path)):
        ext = os.path.splitext(folder_path)[1]
        return jsonify({"error": f"File type not supported: {ext}"}), 415

    # Warn if total chars across the linked entry exceeds threshold
    file_entries = scan_linked_folder(folder_path)
    total_chars  = 0
    for entry in file_entries:
        try:
            # Fix #12: use the already top-level imported extract_text (no local re-import)
            text, _ = extract_text(entry["abs_path"], entry["filename"])
            total_chars += len(text)
        except Exception:
            pass

    record = db.add_linked_folder(conv_id, folder_path)
    return jsonify({
        **record,
        "is_file": is_file,
        "file_count": len(file_entries),
        "warn_large": total_chars > WARN_THRESHOLD,
        "total_chars": total_chars,
    }), 201


@legacy_bp.route("/api/conversations/<int:conv_id>/linked-folders/<int:folder_id>", methods=["DELETE"])
def delete_linked_folder(conv_id, folder_id):
    record = db.get_linked_folder(folder_id)
    if not record or record["conversation_id"] != conv_id:
        return jsonify({"error": "Not found"}), 404
    db.delete_linked_folder(folder_id)
    return jsonify({"ok": True})


@legacy_bp.route("/api/purge", methods=["POST"])
def purge_all():
    """Delete all conversations, messages, uploaded files and linked folders."""
    summary = db.purge_all_conversations()
    return jsonify({"ok": True, "summary": summary})


# ── Folders ────────────────────────────────────────────────────────────────────

@legacy_bp.route("/api/folders", methods=["GET"])
def list_folders():
    return jsonify(db.list_folders())


@legacy_bp.route("/api/folders", methods=["POST"])
def create_folder():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    code_folder = bool(data.get("code_folder"))
    if not name:
        return jsonify({"error": "name is required"}), 400
    if code_folder:
        try:
            folder = db.create_project_team(name)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        folder = db.create_folder(name)
    return jsonify(folder), 201


@legacy_bp.route("/api/folders/<int:folder_id>", methods=["PUT"])
def update_folder(folder_id):
    folder = db.get_folder(folder_id)
    if not folder:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    name     = data.get("name")
    position = data.get("position")
    archived = data.get("archived")
    db.update_folder(
        folder_id,
        name=(name.strip() if isinstance(name, str) else None),
        position=(int(position) if position is not None else None),
        archived=(bool(archived) if archived is not None else None),
    )
    return jsonify(db.get_folder(folder_id))


@legacy_bp.route("/api/folders/<int:folder_id>", methods=["DELETE"])
def delete_folder(folder_id):
    folder = db.get_folder(folder_id)
    if not folder:
        return jsonify({"error": "Not found"}), 404
    db.delete_folder(folder_id)
    return jsonify({"ok": True})


# ── Folder export / import ─────────────────────────────────────────────────────

@legacy_bp.route("/api/folders/<int:folder_id>/export", methods=["GET"])
def export_folder(folder_id):
    """
    Build a ZIP archive for a conversation folder and stream it back.

    Archive layout
    ──────────────
    manifest.json                 ← table-of-contents (folder + conversations metadata)
    conversations/
      <conv_id>/
        messages.json             ← all messages for the conversation
        uploaded_files/
          <original_name>         ← actual uploaded file bytes (preserves sub-paths)
    """
    folder = db.get_folder(folder_id)
    if not folder:
        return jsonify({"error": "Folder not found"}), 404

    all_convs    = db.list_conversations()
    folder_convs = [c for c in all_convs if c.get("folder_id") == folder_id]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "export_version": "1.0",
            "exported_at":    datetime.now(timezone.utc).isoformat(),
            "folder": {
                "id":       folder["id"],
                "name":     folder["name"],
                "position": folder["position"],
            },
            "conversations": [],
        }

        for conv in folder_convs:
            conv_id  = conv["id"]
            messages = db.get_messages(conv_id)
            up_files = db.list_conv_files(conv_id)
            lf_rows  = db.list_linked_folders(conv_id)

            # Write messages JSON into the archive
            msgs_arc_path = f"conversations/{conv_id}/messages.json"
            zf.writestr(msgs_arc_path, json.dumps(messages, indent=2))

            # Copy uploaded files, preserving original_name (may include sub-dirs)
            packed_files = []
            for uf in up_files:
                disk_path = uf.get("disk_path", "")
                if disk_path and os.path.isfile(disk_path):
                    arc_path = f"conversations/{conv_id}/uploaded_files/{uf['original_name']}"
                    zf.write(disk_path, arc_path)
                else:
                    arc_path = ""
                packed_files.append({
                    "original_name": uf["original_name"],
                    "size_bytes":    uf["size_bytes"],
                    "char_count":    uf["char_count"],
                    "arc_path":      arc_path,
                })

            manifest["conversations"].append({
                "id":                  conv_id,
                "title":               conv["title"],
                "model_id":            conv["model_id"],
                "output_dir":          conv.get("output_dir", ""),
                "enable_tools":        conv.get("enable_tools", 1),
                "created_at":          conv["created_at"],
                "updated_at":          conv["updated_at"],
                "messages_arc_path":   msgs_arc_path,
                "uploaded_files":      packed_files,
                "linked_folder_paths": [lf["folder_path"] for lf in lf_rows],
            })

        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    buf.seek(0)
    safe_name = re.sub(r"[^\w\-]", "_", folder["name"])
    filename  = f"folder_{safe_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@legacy_bp.route("/api/folders/import", methods=["POST"])
def import_folder():
    """
    Accept a ZIP produced by export_folder(), recreate the folder, its
    conversations, messages, uploaded files and linked-folder registrations.

    Returns { ok, folder_id, folder_name, conversations_imported, files_imported }.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400
    zfile = request.files["file"]
    if not zfile.filename:
        return jsonify({"error": "Empty filename"}), 400

    try:
        zf = zipfile.ZipFile(io.BytesIO(zfile.read()))
    except zipfile.BadZipFile:
        return jsonify({"error": "Uploaded file is not a valid ZIP archive"}), 400

    if "manifest.json" not in zf.namelist():
        return jsonify({"error": "manifest.json missing from archive"}), 400

    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as exc:
        return jsonify({"error": f"Could not parse manifest.json: {exc}"}), 400

    folder_meta = manifest.get("folder", {})
    folder_name = folder_meta.get("name", "Imported Folder")

    new_folder = db.create_folder(folder_name)
    folder_id  = new_folder["id"]

    convs_imported = 0
    files_imported = 0

    for conv_meta in manifest.get("conversations", []):
        new_conv = db.create_conversation(
            title=conv_meta.get("title", "Imported Conversation"),
            model_id=conv_meta.get("model_id", ""),
            folder_id=folder_id,
        )
        new_conv_id = new_conv["id"]

        db.update_conversation(
            new_conv_id,
            output_dir=conv_meta.get("output_dir") or "",
            enable_tools=bool(conv_meta.get("enable_tools", 1)),
        )

        # Restore messages
        msgs_arc = conv_meta.get("messages_arc_path", "")
        if msgs_arc and msgs_arc in zf.namelist():
            try:
                messages = json.loads(zf.read(msgs_arc).decode("utf-8"))
                for msg in messages:
                    db.add_message(
                        new_conv_id,
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                        tool_call_id=msg.get("tool_call_id"),
                        tool_calls_json=msg.get("tool_calls_json"),
                    )
            except Exception:
                pass

        # Restore uploaded files
        upload_dir = ensure_upload_dir(new_conv_id)
        for uf_meta in conv_meta.get("uploaded_files", []):
            arc_path = uf_meta.get("arc_path", "")
            if not arc_path or arc_path not in zf.namelist():
                continue
            original_name = uf_meta.get("original_name", os.path.basename(arc_path))
            base_name     = os.path.basename(original_name)
            ext           = os.path.splitext(base_name)[1].lower()
            disk_name     = f"{uuid.uuid4().hex}{ext}"
            disk_path     = os.path.join(upload_dir, disk_name)
            try:
                with zf.open(arc_path) as src, open(disk_path, "wb") as dst:
                    dst.write(src.read())
            except Exception:
                continue
            size_bytes = os.path.getsize(disk_path)
            text, _    = extract_text(disk_path, base_name)
            db.add_conv_file(
                conversation_id=new_conv_id,
                original_name=original_name,
                disk_path=disk_path,
                size_bytes=size_bytes,
                char_count=len(text),
            )
            files_imported += 1

        # Re-register linked folder paths (stored as absolute paths; user must
        # ensure they still exist on the importing machine)
        for lf_path in conv_meta.get("linked_folder_paths", []):
            if lf_path:
                db.add_linked_folder(new_conv_id, lf_path)

        convs_imported += 1

    return jsonify({
        "ok":                     True,
        "folder_id":              folder_id,
        "folder_name":            folder_name,
        "conversations_imported": convs_imported,
        "files_imported":         files_imported,
    }), 201


# ── Direct write_file endpoint (used by "Save as files" UI) ──────────────────

@legacy_bp.route("/api/write_file", methods=["POST"])
def write_file_direct():
    """Write content directly to a file path without going through the chat loop.
    Fix #13: removed redundant local re-import of execute_tool_call and json.
    The write is jailed to the configured output directory via _write_file.

    When global local file access is disabled (Settings → "Allow local file
    access"), the write is re-routed into the conversation's upload folder and
    registered as an uploaded file, so it shows in the file list, is previewable,
    and is readable by the model via read_named_file. ``conversation_id`` is then
    required.
    """
    data    = request.get_json(force=True)
    path    = data.get("path", "").strip()
    content = data.get("content", "")
    if not path:
        return jsonify({"success": False, "display": "No path provided", "result": "No path"}), 400

    if not db.local_file_access_enabled():
        conv_id = data.get("conversation_id")
        if not conv_id:
            return jsonify({"success": False,
                            "display": "Local file access is disabled — saving needs a conversation_id",
                            "result": "Local file access is disabled"}), 400
        try:
            conv_id = int(conv_id)
        except (TypeError, ValueError):
            return jsonify({"success": False,
                            "display": "Invalid conversation_id",
                            "result": "Invalid conversation_id"}), 400
        try:
            from file_handler import ensure_upload_dir, extract_text
            base_name = os.path.basename(path.replace("\\", "/"))
            upload_dir = ensure_upload_dir(conv_id)
            ext = os.path.splitext(base_name)[1].lower()
            disk_name = f"{uuid.uuid4().hex}{ext}"
            disk_path = os.path.join(upload_dir, disk_name)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            size_bytes = os.path.getsize(disk_path)
            text, _ = extract_text(disk_path, base_name)
            record = db.add_conv_file(
                conversation_id=conv_id,
                original_name=base_name,
                disk_path=disk_path,
                size_bytes=size_bytes,
                char_count=len(text),
                snippet=text[:500],
            )
            payload = {
                "success": True,
                "display": f"✅ Saved to conversation files: `{base_name}`",
                "result": f"File saved to conversation uploads: {disk_path}",
            }
            if base_name.lower().endswith((".md", ".markdown")):
                payload["document"] = {
                    "id": f"upload:{record['id']}",
                    "name": base_name,
                    "kind": "upload",
                    "size_bytes": size_bytes,
                    "content_hash": db.compute_content_hash(content),
                }
            return jsonify(payload)
        except Exception as exc:
            return jsonify({"success": False, "display": f"❌ Failed to save file: {exc}",
                            "result": f"Failed to save file: {exc}"}), 500

    # Resolve the effective output directory (conversation override → app
    # default), mirroring the chat loop, so writes to a conversation-specific
    # folder pass the write_file jail check instead of being rejected against
    # the app default alone.
    effective_dir = db.get_setting("output_dir") or ""
    conv_id = data.get("conversation_id")
    if conv_id is not None:
        try:
            conv_id = int(conv_id)
        except (TypeError, ValueError):
            conv_id = None
        if conv_id is not None:
            conv = db.get_conversation(conv_id)
            if conv and (conv.get("output_dir") or "").strip():
                effective_dir = conv["output_dir"].strip()
    if not effective_dir.strip():
        return jsonify({"success": False, "display": "No output directory configured",
                        "result": "No output directory configured"}), 400
    result = execute_tool_call(
        "write_file", json.dumps({"path": path, "content": content}), output_dir=effective_dir)
    return jsonify(result)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tools_enabled(conv: dict) -> bool:
    """Whether function-calling tools should be offered to the model for
    the given conversation.

    Disable this (per conversation) for local/OpenAI-compatible models that
    don't support the OpenAI function-calling schema (e.g. some models served
    via LMStudio or Ollama), which may error or misbehave when `tools` is
    supplied.
    """
    # enable_tools is stored as INTEGER (1/0); default to enabled if absent.
    return bool(conv.get("enable_tools", 1))


def _chat_tool_names(user_content: str, context_section: str) -> set[str]:
    """Choose a small tool schema for the current request."""
    text = f"{user_content}\n{context_section}".lower()
    names = set()
    if context_section:
        names.add("read_named_file")
    if any(word in text for word in ("http://", "https://", "web search", "research", "source")):
        names.add("fetch_webpage")
    if any(word in text for word in ("run", "execute", "calculate", "python", "pandas", "plot", "csv")):
        names.add("run_python")
    if any(word in text for word in ("save", "write", "create a file", "edit a file", "update a file")):
        names.update({"write_file", "read_file"})
    if any(word in text for word in ("list files", "directory", "folder contents")):
        names.add("list_directory")
    return names


def _build_system_prompt(
    conv: dict,
    conv_id: int,
    tools_on: bool,
    context_section: str = None,
) -> tuple[str, str]:
    """
    Single, shared helper that builds the system prompt string and resolves the
    effective output directory.  Used by both chat() and regenerate().

    ``context_section`` may be passed in (already built by the caller, e.g. when
    the scope metadata is needed for the SSE scope event) to avoid building the
    file/artifact context twice per request.

    Returns (base_system, output_dir).
    """
    if tools_on:
        if db.local_file_access_enabled():
            base_system = (
                "You are a helpful, knowledgeable general-purpose AI assistant. "
                "Answer all questions, help with analysis, writing, coding, math, "
                "research, and any other topic the user asks about. "
                "You have access to the following tools — use them only when they "
                "genuinely help the user:\n"
                "- write_file: save content to disk (only when user asks to save/create a file)\n"
                "- read_file: read an existing file from disk by absolute path\n"
                "- read_named_file: read the full content of a conversation file or "
                "linked-folder file by name (use when a file was only previewed)\n"
                "- list_directory: list files and folders in a directory by absolute path\n"
                "- run_python: execute a Python snippet and return its output\n"
                "For normal conversational responses, answer directly in the chat. "
                "When the output naturally consists of multiple distinct documents, "
                "call write_file separately for each one with a descriptive filename."
            )
        else:
            base_system = (
                "You are a helpful, knowledgeable general-purpose AI assistant. "
                "Answer all questions, help with analysis, writing, coding, math, "
                "research, and any other topic the user asks about. "
                "You have access to the following tools — use them only when they "
                "genuinely help the user:\n"
                "- read_named_file: read the full content of a file uploaded to this "
                "conversation by name (use when a file was only previewed)\n"
                "- fetch_webpage: fetch an approved research webpage\n"
                "For normal conversational responses, answer directly in the chat. "
                "Local file access is disabled: you cannot read, write or list files "
                "on local disk, and you cannot run code. Files you may reference are "
                "limited to those uploaded to this conversation."
            )
    else:
        base_system = (
            "You are a helpful, knowledgeable general-purpose AI assistant. "
            "Answer all questions, help with analysis, writing, coding, math, "
            "research, and any other topic the user asks about. "
            "Answer directly in the chat."
        )

    output_dir = conv.get("output_dir") or db.get_setting("output_dir") or ""
    if output_dir and tools_on and db.local_file_access_enabled():
        base_system += (
            f"\n\nWhen using write_file, pass ONLY a bare filename (e.g. 'report.md') "
            f"— never include a directory path. Files are automatically saved to: {output_dir}"
        )

    persona_id = conv.get("persona_id")
    if persona_id:
        persona = db.get_persona(persona_id)
        if persona:
            base_system += "\n\nPersona instructions: " + persona["prompt"]

    if context_section is None:
        from conversation_context import build_conversation_context
        context_section, _scope_meta = build_conversation_context(conv, conv_id)
    if context_section:
        base_system += "\n\n" + context_section

    # Token-efficient model integration: remind the model that a document was
    # recently edited without re-injecting its full contents.
    try:
        recent_note = db.get_recent_document_edit_note(conv_id)
    except Exception:
        recent_note = ""
    if recent_note:
        base_system += "\n\n" + recent_note

    return base_system, output_dir


def _make_title(text: str, max_len: int = 50) -> str:
    """Create a short conversation title from the first user message."""
    title = re.sub(r"\s+", " ", text).strip()
    if len(title) > max_len:
        title = title[:max_len].rsplit(" ", 1)[0] + "…"
    return title or "New Conversation"


def _build_api_messages(rows: list[dict]) -> list[dict]:
    """Convert stored messages into the list format expected by the OpenAI API.
    Fix #3: accepts the already-fetched rows so callers don't re-query the DB."""
    api_messages = []
    for row in rows:
        role = row["role"]
        content = row["content"]
        tool_calls_json = row.get("tool_calls_json")
        tool_call_id = row.get("tool_call_id")

        if role == "assistant" and tool_calls_json:
            msg = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": json.loads(tool_calls_json),
            }
        elif role == "tool":
            msg = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": _cap_tool_content(content),
            }
        else:
            msg = {"role": role, "content": content}

        api_messages.append(msg)
    return api_messages


if __name__ == "__main__":
    app_to_run = create_app()
    app_to_run.run(debug=True, port=5000)
