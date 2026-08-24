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

load_dotenv()

from flask import Flask, g, jsonify, request, Response, send_from_directory, send_file
from flask_cors import CORS
from openai import OpenAI

import database as db
from tools import TOOLS, execute_tool_call
from file_handler import (
    ensure_upload_dir, allowed_extension, extract_text,
    build_file_context, build_linked_folder_context,
    scan_linked_folder, WARN_THRESHOLD,
)

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{db.DB_PATH}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.db.init_app(app)
db.init_db(app)


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

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── Models ─────────────────────────────────────────────────────────────────────

@app.route("/api/models", methods=["GET"])
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


@app.route("/api/endpoints", methods=["GET"])
def list_endpoints():
    return jsonify([_public_endpoint(e) for e in db.list_endpoints()])


@app.route("/api/endpoints", methods=["POST"])
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


@app.route("/api/endpoints/<int:endpoint_id>", methods=["PUT"])
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


@app.route("/api/endpoints/<int:endpoint_id>", methods=["DELETE"])
def delete_endpoint(endpoint_id):
    ep = db.get_endpoint(endpoint_id)
    if not ep:
        return jsonify({"error": "Not found"}), 404
    db.delete_endpoint(endpoint_id)
    return jsonify({"ok": True})


# ── Settings ───────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({
        "output_dir":     db.get_setting("output_dir")     or "",
        "browser_root":   db.get_setting("browser_root")   or "",
    })


@app.route("/api/settings", methods=["PUT"])
def update_settings():
    data = request.get_json(force=True)
    if "output_dir" in data:
        db.set_setting("output_dir", data["output_dir"])
    if "browser_root" in data:
        db.set_setting("browser_root", data["browser_root"])
    return jsonify({"ok": True})


@app.route("/api/settings/reset", methods=["POST"])
def reset_settings():
    """Reset the endpoint default model,
    browser starting path and default output folder to empty."""
    result = db.reset_settings()
    return jsonify({"ok": True, "settings": result})


# ── Research sources ──────────────────────────────────────────────────────────

@app.route("/api/research-sources", methods=["GET"])
def list_research_sources():
    return jsonify(db.list_research_sources())


@app.route("/api/research-sources", methods=["POST"])
def create_research_source():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip().rstrip("/")
    if not name or not url:
        return jsonify({"error": "name and url are required"}), 400
    if not url.startswith(("https://", "http://")):
        return jsonify({"error": "url must start with http:// or https://"}), 400
    return jsonify(db.create_research_source(name, url, bool(data.get("enabled", True)))), 201


@app.route("/api/research-sources/<int:source_id>", methods=["PUT"])
def update_research_source(source_id):
    source = db.get_research_source(source_id)
    if not source:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    name = data.get("name")
    url = data.get("url")
    if isinstance(url, str):
        url = url.strip().rstrip("/")
        if not url.startswith(("https://", "http://")):
            return jsonify({"error": "url must start with http:// or https://"}), 400
    updated = db.update_research_source(
        source_id,
        name=name.strip() if isinstance(name, str) else None,
        url=url,
        enabled=bool(data["enabled"]) if "enabled" in data else None,
    )
    return jsonify(updated)


@app.route("/api/research-sources/<int:source_id>", methods=["DELETE"])
def delete_research_source(source_id):
    if not db.get_research_source(source_id):
        return jsonify({"error": "Not found"}), 404
    db.delete_research_source(source_id)
    return jsonify({"ok": True})


@app.route("/api/open-output-dir", methods=["POST"])
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

@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    return jsonify(db.list_conversations())


@app.route("/api/conversations", methods=["POST"])
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


@app.route("/api/conversations/<int:conv_id>", methods=["GET"])
def get_conversation(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    messages = db.get_messages(conv_id)
    return jsonify({**conv, "messages": messages})


@app.route("/api/conversations/<int:conv_id>", methods=["PUT"])
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


@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    db.delete_conversation(conv_id)
    return jsonify({"ok": True})


# ── Chat ───────────────────────────────────────────────────────────────────────

@app.route("/api/conversations/<int:conv_id>/chat", methods=["POST"])
def chat(conv_id):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404

    data = request.get_json(force=True)
    user_content = data.get("message", "").strip()
    if not user_content:
        return jsonify({"error": "Empty message"}), 400

    # Persist the user message
    db.add_message(conv_id, "user", user_content)
    db.touch_conversation(conv_id)

    # Auto-generate title from the first user message
    messages_so_far = db.get_messages(conv_id)
    user_messages = [m for m in messages_so_far if m["role"] == "user"]
    if len(user_messages) == 1:
        auto_title = _make_title(user_content)
        db.update_conversation(conv_id, title=auto_title)

    # Fix #10: resolve endpoint once; reuse for both model_id fallback and client
    endpoint   = _endpoint_for_conversation(conv)
    model_id   = conv["model_id"] or (endpoint or {}).get("default_model") or ""

    def generate():
        """Stream SSE events back to the browser."""
        ctx = app.app_context()
        ctx.push()
        try:
            # Send updated title if this was the first message
            if len(user_messages) == 1:
                title_event = json.dumps({"type": "title", "title": _make_title(user_content), "conv_id": conv_id})
                yield f"data: {title_event}\n\n"

            base_system, output_dir = _build_system_prompt(conv, conv_id, _tools_enabled(conv))
            tools_on = _tools_enabled(conv)
            system_prompt = {"role": "system", "content": base_system}

            # Fix #3: reuse already-fetched messages for API history (no second DB query)
            history = [system_prompt] + _build_api_messages(messages_so_far)

            client = get_client(endpoint)

            # We loop only when the model issues tool calls; plain replies exit immediately.
            while True:
                try:
                    create_kwargs = {
                        "model": model_id,
                        "messages": history,
                        "stream": True,
                    }
                    if tools_on:
                        create_kwargs["tools"] = TOOLS
                        create_kwargs["tool_choice"] = "auto"
                    stream = client.chat.completions.create(**create_kwargs)
                except Exception as exc:
                    err = json.dumps({"type": "error", "message": str(exc)})
                    yield f"data: {err}\n\n"
                    return

                # Accumulate the full streamed response before deciding what to do
                assistant_content = ""
                tool_calls_accum = {}   # index -> {id, name, arguments}
                final_finish_reason = None

                for chunk in stream:
                    choice = chunk.choices[0] if chunk.choices else None
                    if choice is None:
                        continue

                    delta = choice.delta

                    # Stream text tokens to the browser as they arrive
                    if delta.content is not None:
                        assistant_content += delta.content
                        if delta.content:   # don't send empty string tokens
                            token_event = json.dumps({"type": "token", "content": delta.content})
                            yield f"data: {token_event}\n\n"

                    # Accumulate tool-call fragments
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_accum[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_accum[idx]["arguments"] += tc.function.arguments

                    # Capture the finish reason (arrives on the last chunk)
                    if choice.finish_reason is not None:
                        final_finish_reason = choice.finish_reason

                # ── Tool-call branch ──────────────────────────────────────────────
                if tool_calls_accum:
                    tc_list = [
                        {
                            "id": v["id"],
                            "type": "function",
                            "function": {"name": v["name"], "arguments": v["arguments"]},
                        }
                        for v in tool_calls_accum.values()
                    ]

                    # Persist the assistant's tool-call message
                    db.add_message(
                        conv_id,
                        role="assistant",
                        content=assistant_content,
                        tool_calls_json=json.dumps(tc_list),
                    )
                    history.append({
                        "role": "assistant",
                        "content": assistant_content or None,
                        "tool_calls": tc_list,
                    })

                    # Execute each tool and feed the results back into history
                    for tc in tc_list:
                        fn_name = tc["function"]["name"]
                        fn_args = tc["function"]["arguments"]
                        tool_call_id = tc["id"]

                        result = execute_tool_call(fn_name, fn_args, output_dir=output_dir)

                        # Notify the browser
                        tool_event = json.dumps({
                            "type": "tool_result",
                            "success": result["success"],
                            "display": result["display"],
                            "blocked_url": result.get("blocked_url"),
                        })
                        yield f"data: {tool_event}\n\n"

                        # Persist tool result and add to history
                        db.add_message(
                            conv_id,
                            role="tool",
                            content=result["result"],
                            tool_call_id=tool_call_id,
                        )
                        history.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result["result"],
                        })

                    # Let the model continue after receiving the tool results
                    continue

                # ── Plain text branch ─────────────────────────────────────────────
                else:
                    if assistant_content:
                        db.add_message(conv_id, role="assistant", content=assistant_content)
                    done_event = json.dumps({"type": "done"})
                    yield f"data: {done_event}\n\n"
                    return
        finally:
            ctx.pop()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/api/conversations/<int:conv_id>/regenerate", methods=["POST"])
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

    def generate():
        ctx = app.app_context()
        ctx.push()
        try:
            # Fix #1: delegate to shared helpers instead of duplicating the logic
            base_system, output_dir = _build_system_prompt(conv, conv_id, _tools_enabled(conv))
            tools_on = _tools_enabled(conv)
            history  = [{"role": "system", "content": base_system}] + _build_api_messages(messages)
            client   = get_client(endpoint)

            while True:
                try:
                    create_kwargs = {"model": model_id, "messages": history, "stream": True}
                    if tools_on:
                        create_kwargs["tools"] = TOOLS
                        create_kwargs["tool_choice"] = "auto"
                    stream = client.chat.completions.create(**create_kwargs)
                except Exception as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
                    return

                assistant_content = ""
                tool_calls_accum  = {}
                for chunk in stream:
                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice:
                        continue
                    delta = choice.delta
                    if delta.content is not None:
                        assistant_content += delta.content
                        if delta.content:
                            yield f"data: {json.dumps({'type': 'token', 'content': delta.content})}\n\n"
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_accum[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_accum[idx]["arguments"] += tc.function.arguments

                if tool_calls_accum:
                    tc_list = [
                        {"id": v["id"], "type": "function", "function": {"name": v["name"], "arguments": v["arguments"]}}
                        for v in tool_calls_accum.values()
                    ]
                    db.add_message(conv_id, "assistant", assistant_content, tool_calls_json=json.dumps(tc_list))
                    history.append({"role": "assistant", "content": assistant_content or None, "tool_calls": tc_list})
                    for tc in tc_list:
                        result = execute_tool_call(tc["function"]["name"], tc["function"]["arguments"], output_dir=output_dir)
                        yield f"data: {json.dumps({'type': 'tool_result', 'success': result['success'], 'display': result['display'], 'blocked_url': result.get('blocked_url')})}\n\n"
                        db.add_message(conv_id, "tool", result["result"], tool_call_id=tc["id"])
                        history.append({"role": "tool", "tool_call_id": tc["id"], "content": result["result"]})
                    continue
                else:
                    if assistant_content:
                        db.add_message(conv_id, "assistant", assistant_content)
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return
        finally:
            ctx.pop()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/api/conversations/<int:conv_id>/messages/<int:msg_id>", methods=["PUT"])
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


@app.route("/api/conversations/<int:conv_id>/search", methods=["GET"])
def search_conversation(conv_id):
    """Search messages in a conversation by keyword."""
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify([])
    messages = db.get_messages(conv_id)
    results = [m for m in messages if query in (m.get("content") or "").lower()]
    return jsonify(results)


@app.route("/api/search", methods=["GET"])
def search_all():
    """Search across all conversations by keyword. Returns matching conversations with snippet."""
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify([])
    results = db.search_all_conversations(query)
    return jsonify(results)


@app.route("/api/conversations/<int:conv_id>/token-count", methods=["GET"])
def token_count(conv_id):
    """Return an estimated token count for the current conversation context."""
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Not found"}), 404
    messages = db.get_messages(conv_id)
    total_chars = sum(len(m.get("content") or "") for m in messages)
    conv_files     = db.list_conv_files(conv_id)
    linked_folders = db.list_linked_folders(conv_id)
    file_chars = 0
    if linked_folders:
        _, file_chars = build_linked_folder_context(linked_folders, conv_files)
    elif conv_files:
        for f in conv_files:
            file_chars += f.get("char_count", 0)
    total_chars += file_chars
    # Rough approximation: 1 token ≈ 4 characters
    estimated_tokens = total_chars // 4
    return jsonify({
        "estimated_tokens": estimated_tokens,
        "message_chars": total_chars - file_chars,
        "file_chars": file_chars,
        "total_chars": total_chars,
    })


@app.route("/api/conversations/<int:conv_id>/files/<int:file_id>/preview", methods=["GET"])
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

@app.route("/api/personas", methods=["GET"])
def list_personas():
    return jsonify(db.list_personas())


@app.route("/api/personas", methods=["POST"])
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


@app.route("/api/personas/<int:persona_id>", methods=["PUT"])
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


@app.route("/api/personas/<int:persona_id>", methods=["DELETE"])
def delete_persona(persona_id):
    persona = db.get_persona(persona_id)
    if not persona:
        return jsonify({"error": "Not found"}), 404
    db.delete_persona(persona_id)
    return jsonify({"ok": True})


# ── Conversation files ─────────────────────────────────────────────────────────

@app.route("/api/conversations/<int:conv_id>/files", methods=["GET"])
def list_files(conv_id):
    if not db.get_conversation(conv_id):
        return jsonify({"error": "Not found"}), 404
    return jsonify(db.list_conv_files(conv_id))


@app.route("/api/conversations/<int:conv_id>/files", methods=["POST"])
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


@app.route("/api/conversations/<int:conv_id>/files/<int:file_id>", methods=["DELETE"])
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

    db.delete_conv_file(file_id)
    return jsonify({"ok": True})


# ── Folder browser ─────────────────────────────────────────────────────────────

@app.route("/api/browse")
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

@app.route("/api/conversations/<int:conv_id>/linked-folders", methods=["GET"])
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


@app.route("/api/conversations/<int:conv_id>/linked-folders", methods=["POST"])
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


@app.route("/api/conversations/<int:conv_id>/linked-folders/<int:folder_id>", methods=["DELETE"])
def delete_linked_folder(conv_id, folder_id):
    record = db.get_linked_folder(folder_id)
    if not record or record["conversation_id"] != conv_id:
        return jsonify({"error": "Not found"}), 404
    db.delete_linked_folder(folder_id)
    return jsonify({"ok": True})


@app.route("/api/purge", methods=["POST"])
def purge_all():
    """Delete all conversations, messages, uploaded files and linked folders."""
    summary = db.purge_all_conversations()
    return jsonify({"ok": True, "summary": summary})


# ── Folders ────────────────────────────────────────────────────────────────────

@app.route("/api/folders", methods=["GET"])
def list_folders():
    return jsonify(db.list_folders())


@app.route("/api/folders", methods=["POST"])
def create_folder():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    folder = db.create_folder(name)
    return jsonify(folder), 201


@app.route("/api/folders/<int:folder_id>", methods=["PUT"])
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


@app.route("/api/folders/<int:folder_id>", methods=["DELETE"])
def delete_folder(folder_id):
    folder = db.get_folder(folder_id)
    if not folder:
        return jsonify({"error": "Not found"}), 404
    db.delete_folder(folder_id)
    return jsonify({"ok": True})


# ── Folder export / import ─────────────────────────────────────────────────────

@app.route("/api/folders/<int:folder_id>/export", methods=["GET"])
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


@app.route("/api/folders/import", methods=["POST"])
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

@app.route("/api/write_file", methods=["POST"])
def write_file_direct():
    """Write content directly to a file path without going through the chat loop.
    Fix #13: removed redundant local re-import of execute_tool_call and json."""
    data    = request.get_json(force=True)
    path    = data.get("path", "").strip()
    content = data.get("content", "")
    if not path:
        return jsonify({"success": False, "display": "No path provided", "result": "No path"}), 400
    result = execute_tool_call("write_file", json.dumps({"path": path, "content": content}))
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


def _build_system_prompt(conv: dict, conv_id: int, tools_on: bool) -> tuple[str, str]:
    """
    Fix #1/#5: single, shared helper that builds the system prompt string and
    resolves the effective output directory.  Used by both chat() and
    regenerate() so the logic lives in exactly one place.
    Returns (base_system, output_dir).
    """
    if tools_on:
        base_system = (
            "You are a helpful, knowledgeable general-purpose AI assistant. "
            "Answer all questions, help with analysis, writing, coding, math, "
            "research, and any other topic the user asks about. "
            "You have access to the following tools — use them only when they "
            "genuinely help the user:\n"
            "- write_file: save content to disk (only when user asks to save/create a file)\n"
            "- read_file: read an existing file from disk by absolute path\n"
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
            "Answer directly in the chat."
        )

    output_dir = conv.get("output_dir") or db.get_setting("output_dir") or ""
    if output_dir and tools_on:
        base_system += (
            f"\n\nWhen using write_file, pass ONLY a bare filename (e.g. 'report.md') "
            f"— never include a directory path. Files are automatically saved to: {output_dir}"
        )

    persona_id = conv.get("persona_id")
    if persona_id:
        persona = db.get_persona(persona_id)
        if persona:
            base_system += "\n\nPersona instructions: " + persona["prompt"]

    conv_files     = db.list_conv_files(conv_id)
    linked_folders = db.list_linked_folders(conv_id)
    if linked_folders:
        file_context, _ = build_linked_folder_context(linked_folders, conv_files)
        if file_context:
            base_system += "\n\n" + file_context
    elif conv_files:
        file_context = build_file_context(conv_files)
        if file_context:
            base_system += "\n\n" + file_context

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
                "content": content,
            }
        else:
            msg = {"role": role, "content": content}

        api_messages.append(msg)
    return api_messages


if __name__ == "__main__":
    app.run(debug=True, port=5000)
