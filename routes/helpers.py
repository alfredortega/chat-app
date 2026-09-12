"""Route helpers for consistent JSON responses."""

from flask import jsonify


def api_error(message: str, status: int = 400, **extra):
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status


def api_ok(data=None, status: int = 200):
    return jsonify(data if data is not None else {"ok": True}), status