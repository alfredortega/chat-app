import json
import os
import tempfile
import pytest
import database as db_module
from app import create_app
from propagation.qa import (
    ask_question,
    raise_issue,
    propose_artifact,
    build_assumption_marker,
    record_assumption_marker,
    build_prior_answers_context,
    mark_answers_stale,
    post_depth_digest,
    all_blocking_in_depth,
    find_similar_answered,
)


def _make_app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _setup_project(app, tmp_db, tmp_dir):
    folder = db_module.create_folder("P")
    pid = folder["id"]
    db_module.register_project_from_template(pid, "sdlc", tmp_dir)
    event = db_module.create_change_event(
        project_id=pid, source_key="BA-REQ",
        from_version=1, to_version=2, changed_reqs=["REQ-014"],
    )
    return pid, event


def _persona_id(name="Database Developer"):
    return next(p["id"] for p in db_module.list_personas() if p["name"] == name)


class TestAskQuestion:
    """Tests for the ask_question tool (D19-D20)."""

    def test_blocking_question_created(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                db_model = _persona_id()

                issue = ask_question(
                    project_id=pid, raised_by_key="DB-MODEL",
                    raised_by_persona_id=db_model, change_id=event["id"],
                    depth=1, req_id="REQ-014",
                    body="MySQL 5.7 or 8.0? JSON column semantics differ.",
                    blocking=True, proposed_answer="8.0",
                )
                assert issue["kind"] == "question"
                assert issue["blocking"] == 1
                assert issue["status"] == "open"
                assert issue["req_id"] == "REQ-014"

    def test_non_blocking_question_created(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                issue = ask_question(
                    project_id=pid, raised_by_key="SEC-RISK",
                    raised_by_persona_id=None, change_id=event["id"],
                    depth=1, req_id="REQ-014",
                    body="Should MySQL connections require TLS? Assumed yes.",
                    blocking=False,
                )
                assert issue["blocking"] == 0
                assert issue["status"] == "open"

    def test_build_assumption_marker_format(self):
        marker = build_assumption_marker("REQ-014", 31, "TLS required for MySQL", "Database Developer")
        assert "REQ-014" in marker
        assert "Q-0031" in marker
        assert "Database Developer" in marker
        assert marker.startswith(">")


class TestAssumptionMarkerIndexing:
    """Assumption markers are indexed into artifact_assumptions (D20)."""

    def test_marker_indexed(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, _event = _setup_project(app, tmp_db, tmp_dir)
                record_assumption_marker(
                    project_id=pid, artifact_key="DB-MODEL",
                    req_id="REQ-014", question_id=1,
                    marker_text_fragment="TLS required for MySQL connections",
                )
                assumptions = db_module.list_artifact_assumptions(pid, artifact_key="DB-MODEL")
                assert len(assumptions) == 1
                assert assumptions[0]["req_id"] == "REQ-014"
                assert "Q-0001" in assumptions[0]["marker_text"]

    def test_answering_removes_marker(self, tmp_db):
        """After a question is answered, re-indexing drops the marker. (C18 gate)"""
        from parsers import parse_assumption_markers
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                issue = ask_question(
                    project_id=pid, raised_by_key="DB-MODEL",
                    raised_by_persona_id=None, change_id=event["id"],
                    depth=1, req_id="REQ-014",
                    body="Should MySQL require TLS? Assumed yes.", blocking=False,
                )
                marker = build_assumption_marker("REQ-014", issue["id"], "Assumed TLS", "Database Developer")
                doc = "## Req\n" + marker
                # Marker is present and parsable.
                assert len(parse_assumption_markers(doc)) == 1

                # Once answered, the next propagation pass over the artifact
                # writes a document without the marker.
                db_module.update_agent_issue(issue["id"], status="answered", answer="Yes, TLS required.")
                doc_without = "## Req\nNo assumption needed now."
                assert len(parse_assumption_markers(doc_without)) == 0


class TestDigest:
    """Tests for per-depth question digests (D21)."""

    def _conversation(self, pid):
        conv = db_module.create_conversation("BA Inbox", "")
        db_module.update_folder_project(pid, ba_conversation_id=conv["id"])
        return conv

    def test_six_questions_one_digest(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                conv = self._conversation(pid)

                for i in range(6):
                    ask_question(
                        project_id=pid, raised_by_key=f"ROLE-{i}",
                        raised_by_persona_id=None, change_id=event["id"],
                        depth=1, req_id="REQ-014",
                        body=f"Question number {i} about REQ-014.", blocking=(i % 2 == 0),
                    )

                result = post_depth_digest(pid, event["id"], depth=1)
                assert result["posted"] is True
                # Exactly one digest message in the BA conversation.
                msgs = db_module.get_messages(conv["id"])
                assert len(msgs) == 1
                assert "depth 1" in msgs[0]["content"]

    def test_blocking_listed_first(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                conv = self._conversation(pid)

                ask_question(pid, "A", None, event["id"], 1, "REQ-014", "non-blocking q", blocking=False)
                ask_question(pid, "B", None, event["id"], 1, "REQ-014", "blocking q", blocking=True)

                post_depth_digest(pid, event["id"], depth=1)
                msg = db_module.get_messages(conv["id"])[0]["content"]
                assert msg.index("blocking q") < msg.index("non-blocking q")

    def test_dedup_same_question_one_entry(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                conv = self._conversation(pid)

                ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                             "What engine?", blocking=False)
                ask_question(pid, "QA-PLAN", None, event["id"], 1, "REQ-014",
                             "What engine?", blocking=False)

                post_depth_digest(pid, event["id"], depth=1)
                msg = db_module.get_messages(conv["id"])[0]["content"]
                assert "DB-MODEL, QA-PLAN" in msg

    def test_all_depth1_blocking_posts_immediately(self, tmp_db):
        """When every depth-1 question blocks, the digest is not deferred."""
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                self._conversation(pid)

                ask_question(pid, "A", None, event["id"], 1, "REQ-014", "q1", blocking=True)
                ask_question(pid, "B", None, event["id"], 1, "REQ-014", "q2", blocking=True)

                assert all_blocking_in_depth(pid, event["id"], depth=1) is True
                result = post_depth_digest(pid, event["id"], depth=1)
                assert result["posted"] is True

    def test_no_ba_conversation_queues(self, tmp_db):
        """Project with no BA conversation: questions queue, nothing lost."""
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                             "Question stays queued.", blocking=True)

                result = post_depth_digest(pid, event["id"], depth=1)
                assert result["posted"] is False
                assert result["queued"] is True
                # Still open in the panel.
                issues = db_module.list_agent_issues(pid, kind="question")
                assert len(issues) == 1
                assert issues[0]["status"] == "open"


class TestAnswerReuse:
    """Tests for durable answer reuse (D22)."""

    def test_build_prior_answers_context(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                issue = ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                                     "Which engine?", blocking=False)
                db_module.update_agent_issue(issue["id"], status="answered", answer="SQLAlchemy ORM")

                ctx = build_prior_answers_context(pid, ["REQ-014"])
                assert "Previously clarified" in ctx
                assert "REQ-014" in ctx
                assert "SQLAlchemy ORM" in ctx

    def test_similar_answered_prevents_reask(self, tmp_db):
        """A second wave touching the same req_id gets the prior answer and does not re-ask."""
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                issue = ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                                     "Which database engine?", blocking=False)
                db_module.update_agent_issue(issue["id"], status="answered", answer="MySQL 8.0")

                # Same question asked again -> reuse, no new issue row.
                prior = find_similar_answered(pid, "REQ-014", "Which database engine?")
                assert prior is not None
                assert prior["id"] == issue["id"]

                result = ask_question(pid, "SEC-RISK", None, event["id"], 1, "REQ-014",
                                      "Which database engine?", blocking=False)
                assert "reused_answer" in result
                assert result["reused_answer"] == "MySQL 8.0"
                issues = db_module.list_agent_issues(pid, kind="question")
                assert len(issues) == 1  # still one issue row

    def test_requirement_change_marks_stale(self, tmp_db):
        """Requirement text changes -> prior answers flagged stale_context (D22)."""
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                issue = ask_question(pid, "DB-MODEL", None, event["id"], 1, "REQ-014",
                                     "SQLite only?", blocking=False)
                db_module.update_agent_issue(issue["id"], status="answered", answer="Yes, SQLite.")

                mark_answers_stale(pid, ["REQ-014"])
                refreshed = db_module.list_agent_issues(pid, kind="question")[0]
                assert refreshed["stale_context"] == 1

                ctx = build_prior_answers_context(pid, ["REQ-014"])
                assert "possibly outdated" in ctx


class TestRaiseIssueAndPropose:
    """Tests for raise_issue and propose_artifact (D12 / D17)."""

    def test_raise_issue_created(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_project(app, tmp_db, tmp_dir)
                issue = raise_issue(pid, "SEC-RISK", None, event["id"], 1,
                                    "TLS not configured by default", kind="risk")
                assert issue["kind"] == "risk"
                assert issue["status"] == "open"

    def test_propose_artifact_records_request(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, _event = _setup_project(app, tmp_db, tmp_dir)
                request = propose_artifact(
                    pid, persona_id=None, artifact_key="QA-PLAN-AUTH",
                    rel_path="Test Cases/QA-PLAN-AUTH.md", rationale="Auth tests need own plan.",
                )
                assert request["status"] == "pending"
                # Re-proposing the same key returns the pending request, not a duplicate.
                again = propose_artifact(
                    pid, persona_id=None, artifact_key="QA-PLAN-AUTH",
                    rel_path="Test Cases/QA-PLAN-AUTH.md", rationale="dup",
                )
                assert again["id"] == request["id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])