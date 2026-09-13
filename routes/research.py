"""
Research-sources blueprint.

Self-contained group extracted from app.py to reduce the legacy module's
surface area. Covers approved research sources (create/list/update/delete).
"""

from flask import Blueprint, jsonify, request

import database as db

research_bp = Blueprint("research", __name__, url_prefix="/api/research-sources")


@research_bp.get("")
def list_research_sources():
    return jsonify(db.list_research_sources())


@research_bp.post("")
def create_research_source():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip().rstrip("/")
    if not name or not url:
        return jsonify({"error": "name and url are required"}), 400
    if not url.startswith(("https://", "http://")):
        return jsonify({"error": "url must start with http:// or https://"}), 400
    return jsonify(db.create_research_source(name, url, bool(data.get("enabled", True)))), 201


@research_bp.put("/<int:source_id>")
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


@research_bp.delete("/<int:source_id>")
def delete_research_source(source_id):
    if not db.get_research_source(source_id):
        return jsonify({"error": "Not found"}), 404
    db.delete_research_source(source_id)
    return jsonify({"ok": True})