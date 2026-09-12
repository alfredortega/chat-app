import pytest
import database as db_module
from app import create_app


def _app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _personas():
    return {p["name"]: p["prompt"] for p in db_module.list_personas()}


class TestPromptContracts:
    """Starter persona prompts carry the propagation contract (§6.3)."""

    def test_ba_prompt_has_req_contract(self, tmp_db):
        with _app(tmp_db).app_context():
            prompt = _personas()["Business Analyst"]
            assert "REQ-nnn" in prompt
            assert "renumber" in prompt.lower()

    def test_database_developer_has_db_prefix_and_contract(self, tmp_db):
        with _app(tmp_db).app_context():
            prompt = _personas()["Database Developer"]
            assert "DB_" in prompt
            assert "ask_question" in prompt
            assert "raise_issue" in prompt
            assert "propose_artifact" in prompt
            assert "derives_from" in prompt

    def test_devsecops_has_sec_prefix_and_contract(self, tmp_db):
        with _app(tmp_db).app_context():
            prompt = _personas()["DevSecOps Engineer"]
            assert "SEC_" in prompt
            assert "ask_question" in prompt
            assert "assumption marker" in prompt.lower()

    def test_downstream_personas_get_contract(self, tmp_db):
        with _app(tmp_db).app_context():
            for name in ["UX Designer", "Security Analyst", "Project Manager"]:
                prompt = _personas()[name]
                assert "ask_question" in prompt
                assert "not talking to the user directly" in prompt or "NOT talking to the user directly" in prompt

    def test_marker_format_exact(self, tmp_db):
        with _app(tmp_db).app_context():
            prompt = _personas()["Database Developer"]
            assert "⚠️ ASSUMPTION (Q-nnnn)" in prompt


class TestStarterPersonaVersioning:
    def test_version_setting_created_on_init(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            setting = db_module.db.session.get(db_module.Setting, "starter_personas_version")
            assert setting is not None
            assert setting.value == db_module.STARTER_PERSONAS_VERSION

    def test_get_version(self, tmp_db):
        with _app(tmp_db).app_context():
            assert db_module.get_starter_personas_version() == db_module.STARTER_PERSONAS_VERSION

    def test_reset_updates_unmodified_personas(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            # Simulate a persona that was seeded with OLD text (updated_at is
            # still equal to created_at so it looks unedited by a human).
            persona = next(p for p in db_module.list_personas() if p["name"] == "Database Developer")
            from database import Persona
            row = db_module.db.session.get(Persona, persona["id"])
            row.prompt = "Old DB prompt without contract"
            row.updated_at = row.created_at
            db_module.db.session.commit()

            result = db_module.reset_starter_personas()
            assert "Database Developer" in result["updated"]
            refreshed = next(p for p in db_module.list_personas() if p["name"] == "Database Developer")
            assert "ask_question" in refreshed["prompt"]

    def test_reset_does_not_clobber_edited_persona(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            persona = next(p for p in db_module.list_personas() if p["name"] == "UX Designer")
            # Simulate a user edit (updated_at differs from created_at).
            db_module.update_persona(persona["id"], prompt="User's custom UX prompt")
            result = db_module.reset_starter_personas()
            assert "UX Designer" in result["skipped"]
            refreshed = next(p for p in db_module.list_personas() if p["name"] == "UX Designer")
            assert refreshed["prompt"] == "User's custom UX prompt"

    def test_reset_force_overrides_edits(self, tmp_db):
        app = _app(tmp_db)
        with app.app_context():
            persona = next(p for p in db_module.list_personas() if p["name"] == "UX Designer")
            db_module.update_persona(persona["id"], prompt="Custom edited prompt")
            result = db_module.reset_starter_personas(force=True)
            assert "UX Designer" in result["updated"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])