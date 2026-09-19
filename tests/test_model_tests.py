"""
Route-level tests for the Model Tests window backend.

Covers the /api/model-tests/run endpoint: validation, per-model dispatch,
the >200 char persistence rule (model_test_results/<model>_<yyyy.mm.dd>.md),
per-model error isolation, and retrieval of saved result files.
"""

import os

import pytest

import database as db_module
from app import create_app
from routes import model_tests as model_tests_route


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1",
    })


def _app_with_endpoint(tmp_db):
    """App plus a default endpoint pointing at the fake, and its id."""
    from tests.fakes import FakeOpenAIClient

    app = _app(tmp_db)
    fake = FakeOpenAIClient()
    with app.app_context():
        endpoint = db_module.create_endpoint(
            "Fake", "http://fake.openai.test/v1",
            api_key="f", default_model="test-model", is_default=True,
        )
        endpoint_id = endpoint["id"]
    return app, fake, endpoint_id


class TestRunModelTests:
    def test_prompt_is_required(self, tmp_db, monkeypatch, tmp_path):
        app, fake, _ = _app_with_endpoint(tmp_db)
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post("/api/model-tests/run", json={"prompt": "  ", "models": ["m1"]})
        assert rv.status_code == 400
        assert "Prompt" in rv.get_json()["error"]

    def test_at_least_one_model_is_required(self, tmp_db, monkeypatch, tmp_path):
        app, fake, _ = _app_with_endpoint(tmp_db)
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post("/api/model-tests/run", json={"prompt": "Hi", "models": []})
        assert rv.status_code == 400
        assert "model" in rv.get_json()["error"]

    def test_short_responses_return_inline(self, tmp_db, monkeypatch, tmp_path):
        """Responses <= 200 chars are returned inline and nothing is saved."""
        app, fake, endpoint_id = _app_with_endpoint(tmp_db)
        fake.add_text_response("Short answer one.")
        fake.add_text_response("Short answer two.")
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post("/api/model-tests/run", json={
                "endpoint_id": endpoint_id,
                "prompt": "Same prompt for all",
                "models": ["model-a", "model-b"],
            })
        assert rv.status_code == 200
        results = rv.get_json()["results"]
        assert [r["model"] for r in results] == ["model-a", "model-b"]
        assert all(r["error"] is None and not r["saved"] for r in results)
        assert [r["response"] for r in results] == ["Short answer one.", "Short answer two."]
        assert os.listdir(tmp_path) == []

    def test_long_response_is_saved_with_naming_convention(self, tmp_db, monkeypatch, tmp_path):
        """A response > 200 chars is written to <model>_<yyyy.mm.dd>.md."""
        app, fake, endpoint_id = _app_with_endpoint(tmp_db)
        long_text = ("A" * 250)
        fake.add_text_response(long_text)
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post("/api/model-tests/run", json={
                "endpoint_id": endpoint_id,
                "prompt": "Tell me everything",
                "models": ["openai/gpt-4o"],
            })
        assert rv.status_code == 200
        result = rv.get_json()["results"][0]
        assert result["error"] is None
        assert result["chars"] == 250
        assert result["saved"] is True
        assert result["file_name"] is not None

        # Naming: slashes sanitized, date in yyyy.mm.dd form.
        import re
        from datetime import datetime
        assert re.fullmatch(r"openai_gpt-4o_\d{4}\.\d{2}\.\d{2}\.md", result["file_name"])
        assert datetime.now().strftime("%Y.%m.%d") in result["file_name"]

        saved = os.path.join(tmp_path, result["file_name"])
        with open(saved, encoding="utf-8") as fh:
            assert fh.read() == long_text

    def test_model_error_does_not_abort_other_models(self, tmp_db, monkeypatch, tmp_path):
        app, fake, endpoint_id = _app_with_endpoint(tmp_db)
        fake._script = [("text", "ok")]  # one response; second model raises instead
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))

        def flaky_client(endpoint):
            return fake

        original_create = fake.chat.completions.create

        def create_that_raises_for_bad(*, model=None, **kwargs):
            if model == "good-model":
                return original_create(model=model, **kwargs)
            raise RuntimeError("downstream exploded")

        fake.chat.completions.create = create_that_raises_for_bad
        monkeypatch.setattr("app.get_client", flaky_client)

        with app.test_client() as client:
            rv = client.post("/api/model-tests/run", json={
                "endpoint_id": endpoint_id,
                "prompt": "p",
                "models": ["good-model", "bad-model"],
            })
        assert rv.status_code == 200
        results = rv.get_json()["results"]
        by_model = {r["model"]: r for r in results}
        assert by_model["good-model"]["error"] is None
        assert by_model["good-model"]["response"] == "ok"
        assert by_model["bad-model"]["error"] is not None
        assert by_model["bad-model"]["saved"] is False


class TestModelTestFiles:
    def test_get_saved_file_returns_document_object(self, tmp_db, monkeypatch, tmp_path):
        app, fake, endpoint_id = _app_with_endpoint(tmp_db)
        fake.add_text_response("B" * 300)
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.post("/api/model-tests/run", json={
                "endpoint_id": endpoint_id,
                "prompt": "p",
                "models": ["some-model"],
            })
            file_name = rv.get_json()["results"][0]["file_name"]

            rv = client.get(f"/api/model-tests/files/{file_name}")
        assert rv.status_code == 200
        doc = rv.get_json()
        assert doc["name"] == file_name
        assert doc["content"] == "B" * 300
        assert doc["editable"] is False
        assert doc["kind"] == "output"

    def test_get_rejects_traversal(self, tmp_db, monkeypatch, tmp_path):
        app, fake, _ = _app_with_endpoint(tmp_db)
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.get("/api/model-tests/files/../../chat.db")
        assert rv.status_code == 400

    def test_get_404_for_missing_file(self, tmp_db, monkeypatch, tmp_path):
        app, fake, _ = _app_with_endpoint(tmp_db)
        monkeypatch.setattr(model_tests_route, "MODEL_TEST_RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr("app.get_client", lambda endpoint: fake)

        with app.test_client() as client:
            rv = client.get("/api/model-tests/files/does-not-exist.md")
        assert rv.status_code == 404