"""
Tests for the scoped per-role project context (D13).

Project role conversations must NOT have the whole workspace dumped into the
system prompt on every message. Instead they receive only the artifacts their
role reads (own + declared upstream roles). Conversations that are not role
conversations keep the legacy linked-folder behaviour.
"""

import os

import pytest

import database as db_module
from app import create_app, _build_system_prompt

TEST_CONFIG = {
    "SQLALCHEMY_DATABASE_URI": None,
    "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
    "SKIP_DOTENV_WRITE": "1",
}


def _app(tmp_db):
    config = dict(TEST_CONFIG)
    config["SQLALCHEMY_DATABASE_URI"] = tmp_db
    return create_app(config=config)


class TestProjectScopedContext:
    def _setup_team(self, tmp_db, tmp_path):
        app = _app(tmp_db)
        with app.app_context():
            output_root = str(tmp_path / "output")
            os.makedirs(output_root, exist_ok=True)
            db_module.set_setting("output_dir", output_root)
            team = db_module.create_project_team("Scoped Project")
        return app, team

    def _convs_by_title(self, app, team):
        by_title = {c["title"]: c for c in team["conversations"]}
        with app.app_context():
            return {title: db_module.get_conversation(c["id"]) for title, c in by_title.items()}

    def _prompt_for(self, app, conv):
        with app.app_context():
            prompt, _ = _build_system_prompt(conv, conv["id"], tools_on=True)
        return prompt

    def test_database_role_gets_only_its_scope(self, tmp_db, tmp_path):
        app, team = self._setup_team(tmp_db, tmp_path)
        convs = self._convs_by_title(app, team)
        prompt = self._prompt_for(app, convs["Database Developer"])

        assert "ARTIFACT: BA-REQ" in prompt
        assert "ARTIFACT: DB-MODEL" in prompt
        assert "ARTIFACT: UX-WIRE" not in prompt
        assert "ARTIFACT: QA-PLAN" not in prompt
        assert "ARTIFACT: PM-PLAN" not in prompt
        assert "ARTIFACT: SEC-RISK" not in prompt
        # The scoped path must replace the whole-workspace dump.
        assert "LINKED FOLDER" not in prompt

    def test_ba_gets_only_ba_req(self, tmp_db, tmp_path):
        app, team = self._setup_team(tmp_db, tmp_path)
        convs = self._convs_by_title(app, team)
        prompt = self._prompt_for(app, convs["Business Analyst"])

        assert "ARTIFACT: BA-REQ" in prompt
        for absent in ("UX-WIRE", "DB-MODEL", "QA-PLAN", "SEC-RISK", "PM-PLAN"):
            assert f"ARTIFACT: {absent}" not in prompt
        assert "LINKED FOLDER" not in prompt

    def test_pm_reads_direct_upstreams_only(self, tmp_db, tmp_path):
        app, team = self._setup_team(tmp_db, tmp_path)
        convs = self._convs_by_title(app, team)
        prompt = self._prompt_for(app, convs["Project Manager"])

        for present in ("PM-PLAN", "UX-WIRE", "DB-MODEL", "QA-PLAN", "SEC-RISK"):
            assert f"ARTIFACT: {present}" in prompt
        # BA-REQ is not a direct upstream of the PM role.
        assert "ARTIFACT: BA-REQ" not in prompt

    def test_qa_role_uses_title_fallback(self, tmp_db, tmp_path):
        # No "QA/Tester" persona is seeded, so the role is resolved from the
        # conversation title; it must still get a scoped context.
        app, team = self._setup_team(tmp_db, tmp_path)
        convs = self._convs_by_title(app, team)
        prompt = self._prompt_for(app, convs["QA/Tester"])

        for present in ("QA-PLAN", "BA-REQ", "DB-MODEL"):
            assert f"ARTIFACT: {present}" in prompt
        assert "ARTIFACT: SEC-RISK" not in prompt
        assert "LINKED FOLDER" not in prompt

    def test_big_workspace_does_not_leak_into_role_prompt(self, tmp_db, tmp_path):
        app, team = self._setup_team(tmp_db, tmp_path)
        with app.app_context():
            workspace = team["workspace_dir"]
            with open(os.path.join(workspace, "Design", "REFERENCE.md"), "w", encoding="utf-8") as f:
                f.write("# Reference\n\n" + ("filler line for the big reference document\n" * 40_000))

        convs = self._convs_by_title(app, team)
        prompt = self._prompt_for(app, convs["Database Developer"])

        assert "filler line" not in prompt
        assert len(prompt) < 50_000  # a ~2MB workspace must not reach the model

    def test_plain_folder_conversation_keeps_linked_folder_context(self, tmp_db, tmp_path):
        app = _app(tmp_db)
        with app.app_context():
            marked = tmp_path / "linked"
            marked.mkdir()
            (marked / "notes.md").write_text("plain linked notes\n", encoding="utf-8")

            folder = db_module.create_folder("Plain Folder")
            conv = db_module.create_conversation(
                title="Plain Chat", model_id="", folder_id=folder["id"],
            )
            db_module.add_linked_folder(conv["id"], str(marked))
            conv = db_module.get_conversation(conv["id"])

        prompt = self._prompt_for(app, conv)
        assert "LINKED FOLDER" in prompt
        assert "plain linked notes" in prompt

    def test_adhoc_project_conversation_falls_back_to_linked_folder(self, tmp_db, tmp_path):
        app, team = self._setup_team(tmp_db, tmp_path)
        with app.app_context():
            conv = db_module.create_conversation(
                title="General Chat", model_id="", folder_id=team["project"]["id"],
            )
            db_module.add_linked_folder(conv["id"], team["workspace_dir"])
            conv = db_module.get_conversation(conv["id"])

        prompt = self._prompt_for(app, conv)
        assert "LINKED FOLDER" in prompt

    def test_focusing_ad_hoc_conversation_with_ba_persona_scopes_it(self, tmp_db, tmp_path):
        # An ad-hoc chat starts with the whole-workspace dump, but once the
        # user "focuses" it with the Business Analyst persona it must switch
        # to the scoped BA context (own artifact only) — same as the BA
        # conversation created by the project flow.
        app, team = self._setup_team(tmp_db, tmp_path)
        with app.app_context():
            workspace = team["workspace_dir"]
            with open(os.path.join(workspace, "Design", "REFERENCE.md"), "w", encoding="utf-8") as f:
                f.write("# Reference\n\n" + ("filler line for the big reference document\n" * 20_000))

            conv = db_module.create_conversation(
                title="General Chat", model_id="", folder_id=team["project"]["id"],
            )
            db_module.add_linked_folder(conv["id"], workspace)
            conv = db_module.get_conversation(conv["id"])

            ba_persona = next(
                (p for p in db_module.list_personas() if p["name"] == "Business Analyst"),
                None,
            )
            assert ba_persona is not None

            before = _build_system_prompt(conv, conv["id"], tools_on=True)[0]
            assert "LINKED FOLDER" in before
            assert "filler line" in before

            # Focus the conversation with the BA persona (UI persona selector).
            db_module.update_conversation(conv["id"], persona_id=ba_persona["id"])
            conv = db_module.get_conversation(conv["id"])

            after = _build_system_prompt(conv, conv["id"], tools_on=True)[0]
            assert "ARTIFACT: BA-REQ" in after
            assert "ARTIFACT: DB-MODEL" not in after
            assert "LINKED FOLDER" not in after
            assert "filler line" not in after

    def test_designated_inbox_is_scoped_as_ba_regardless_of_persona(self, tmp_db, tmp_path):
        # A conversation designated as the Q&A inbox (ba_conversation_id) is
        # scoped as the BA role even when it has no BA persona — e.g. an
        # ad-hoc chat focused with an unrelated persona (D19).
        app, team = self._setup_team(tmp_db, tmp_path)
        with app.app_context():
            workspace = team["workspace_dir"]
            with open(os.path.join(workspace, "Design", "REFERENCE.md"), "w", encoding="utf-8") as f:
                f.write("# Reference\n\n" + ("filler line for the big reference document\n" * 20_000))

            python_persona = next(
                (p for p in db_module.list_personas() if p["name"] == "Python Developer"),
                None,
            )
            assert python_persona is not None

            conv = db_module.create_conversation(
                title="Inbox Chat", model_id="", persona_id=python_persona["id"],
                folder_id=team["project"]["id"],
            )
            db_module.add_linked_folder(conv["id"], workspace)
            db_module.update_folder_project(team["project"]["id"], ba_conversation_id=conv["id"])
            conv = db_module.get_conversation(conv["id"])

            after = _build_system_prompt(conv, conv["id"], tools_on=True)[0]
            assert "ARTIFACT: BA-REQ" in after
            assert "ARTIFACT: DB-MODEL" not in after
            assert "LINKED FOLDER" not in after
            assert "filler line" not in after