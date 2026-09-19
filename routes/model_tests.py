"""
Model test-runner blueprint.

Self-contained group for the Model Tests window. Sends one prompt to every
selected model on a given endpoint (defaulting to the active conversation's
endpoint) and returns a side-by-side comparison table. Responses longer than
200 characters are persisted to ``model_test_results/<model>_<yyyy.mm.dd>.md``
(overwritten on each run, per spec).
"""

from datetime import datetime
import hashlib
import os
import re

from flask import Blueprint, jsonify, request

import database as db

model_tests_bp = Blueprint("model-tests", __name__, url_prefix="/api/model-tests")

MODEL_TEST_RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "model_test_results",
)

# Responses longer than this are written to a Markdown file instead of being
# shown in full inside the comparison table.
MODEL_TEST_SAVE_THRESHOLD = 200


def _sanitize_model_name(model: str) -> str:
    """Turn a model id into a filesystem-safe name (openai/gpt-4o -> openai_gpt-4o)."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", model or "").strip("._")
    return name or "model"


def _is_safe_test_filename(filename: str) -> bool:
    """Only accept flat, bare Markdown filenames inside the results folder."""
    return bool(filename) and filename == os.path.basename(filename) and filename.lower().endswith(".md")


def _resolve_endpoint(endpoint_id):
    if endpoint_id:
        ep = db.get_endpoint(int(endpoint_id))
        if ep:
            return ep
    return db.get_default_endpoint()


@model_tests_bp.post("/run")
def run_model_tests():
    data = request.get_json(force=True)
    prompt = (data.get("prompt") or "").strip()
    models = [m for m in (data.get("models") or []) if isinstance(m, str) and m.strip()]
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400
    if not models:
        return jsonify({"error": "Select at least one model."}), 400

    from app import MODEL_MAX_OUTPUT_TOKENS, get_client

    endpoint = _resolve_endpoint(data.get("endpoint_id"))
    client = get_client(endpoint)

    try:
        os.makedirs(MODEL_TEST_RESULTS_DIR, exist_ok=True)
    except OSError:
        pass

    date_stamp = datetime.now().strftime("%Y.%m.%d")
    max_tokens = int(MODEL_MAX_OUTPUT_TOKENS or 4096)

    results = []
    for model_id in models:
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                stream=False,
            )
            text = (response.choices[0].message.content or "") if response.choices else ""
        except Exception as exc:
            results.append({
                "model": model_id,
                "error": str(exc),
                "response": "",
                "chars": 0,
                "saved": False,
                "file_name": None,
            })
            continue

        file_name = None
        if len(text) > MODEL_TEST_SAVE_THRESHOLD:
            file_name = f"{_sanitize_model_name(model_id)}_{date_stamp}.md"
            try:
                with open(os.path.join(MODEL_TEST_RESULTS_DIR, file_name), "w", encoding="utf-8") as fh:
                    fh.write(text)
            except OSError:
                file_name = None  # a missing file must not fail the whole run

        results.append({
            "model": model_id,
            "error": None,
            "response": text,
            "chars": len(text),
            "saved": file_name is not None,
            "file_name": file_name,
        })

    return jsonify({"results": results})


@model_tests_bp.get("/files/<path:filename>")
def get_model_test_file(filename):
    """Return a saved test result as a document object for the Markdown editor."""
    if not _is_safe_test_filename(filename):
        return jsonify({"error": "invalid_name", "message": "Malformed test-result filename."}), 400

    root = os.path.realpath(MODEL_TEST_RESULTS_DIR)
    path = os.path.realpath(os.path.join(root, filename))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        return jsonify({"error": "not_found", "message": "Test result file not found."}), 404

    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        return jsonify({"error": "read_error", "message": str(exc)}), 500

    return jsonify({
        "id": f"test:{filename}",
        "name": filename,
        "kind": "output",
        "content": content,
        "content_hash": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "modified_at": None,
        "size_bytes": len(content.encode("utf-8")),
        "editable": False,
        "managed_artifact": False,
        "artifact_key": None,
        "project_id": None,
    })