import json
import os
import tempfile
import pytest
import database as db_module
from app import create_app
from tests.fakes import FakeOpenAIClient
from propagation.agent import (
    run_propagation_wave,
    assess_rewrite,
    normalize_rewrite,
    WaveOverlay,
    RewritePayload,
    build_agent_prompt,
    CURRENT_ARTIFACT_CAP,
)


def _make_app(tmp_db):
    return create_app(config={
        "SQLALCHEMY_DATABASE_URI": tmp_db,
        "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
        "SKIP_DOTENV_WRITE": "1"
    })


def _setup_wave(app, tmp_db, tmp_dir, req_ids=("REQ-014",)):
    """Register an SDLC project, create traces + event, queue jobs."""
    folder = db_module.create_folder("P")
    pid = folder["id"]
    db_module.register_project_from_template(pid, "sdlc", tmp_dir)

    for req in req_ids:
        for key in ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            db_module.create_artifact_trace(pid, key, req)

    event = db_module.create_change_event(
        project_id=pid, source_key="BA-REQ",
        from_version=1, to_version=2,
        changed_reqs=list(req_ids), diff="+SQLite and MySQL",
    )
    from propagation.impact import queue_propagation_jobs
    queue_propagation_jobs(pid, event["id"], list(req_ids))
    return pid, event


def _scripted_client(turns):
    """Build a fake client scripted with tool-call + final-text turns."""
    client = FakeOpenAIClient()
    for tc in turns:
        client.add_tool_calls_response([tc])
        client.add_text_response("Done.")
    return client


def _rewrite(key, body):
    """A whole-file rewrite that preserves the template '# {key}' + placeholder heading."""
    return f"# {key}\n\n## placeholder\n\n{body}\n"


def test_agent_prompt_includes_change_details():
    messages = build_agent_prompt(
        "persona",
        "upstream context",
        {
            "summary": "Add account sessions",
            "changed_reqs": ["REQ-001"],
            "diff": "+Session records are required.",
        },
        "# DB-MODEL\n",
    )

    assert "Add account sessions" in messages[1]["content"]
    assert "REQ-001" in messages[1]["content"]
    assert "+Session records are required." in messages[1]["content"]


def test_agent_prompt_directs_agent_to_read_complete_artifact_for_patch_context():
    current = "start\n" + ("x" * (CURRENT_ARTIFACT_CAP + 1)) + "\nunique final line\n"
    messages = build_agent_prompt("persona", "", {}, current, "DB-MODEL")

    assert "Call read_artifact for 'DB-MODEL'" in messages[1]["content"]
    assert current not in messages[1]["content"]


def test_placeholder_heading_can_be_replaced():
    assessment = assess_rewrite(
        "# DB-MODEL\n\n## placeholder\n",
        "# DB-MODEL\n\n## Entities\n\nSession records.\n",
    )

    assert assessment.ok


def test_agent_retries_when_model_replies_with_prose_after_read(tmp_db):
    """A prose response after the required read must not leave the job stale."""
    app = _make_app(tmp_db)
    with app.app_context():
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid, event = _setup_wave(app, tmp_db, tmp_dir)
            client = FakeOpenAIClient()
            client.add_tool_calls_response([{
                "name": "read_artifact",
                "arguments": json.dumps({"artifact_key": "DB-MODEL"}),
            }])
            client.add_text_response("I reviewed the artifact and will update it.")
            client.add_tool_calls_response([{
                "name": "write_artifact",
                "arguments": json.dumps({
                    "artifact_key": "DB-MODEL",
                    "content": _rewrite("DB-MODEL", "## Entities\nUpdated body."),
                }),
            }])
            client.add_text_response("Done.")
            for key in ["SEC-RISK", "UX-WIRE", "QA-PLAN", "PM-PLAN"]:
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": key,
                        "content": _rewrite(key, "## Section\nUpdated body."),
                    }),
                }])
                client.add_text_response("Done.")

            payloads = run_propagation_wave(app, pid, event["id"], client)
            db_model = next(p for p in payloads if p.artifact_key == "DB-MODEL")
            assert db_model.status == "ok"
            assert db_model.content != ""
            assert all(job["state"] == "completed"
                       for job in db_module.list_propagation_jobs(event["id"]))


class TestDownstreamPromptOverlay:
    """C16 gate: QA-PLAN prompt contains the updated DB-MODEL text."""

    def test_qa_prompt_contains_updated_db_model(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)

                client = FakeOpenAIClient(base_url="https://openrouter.ai/api/v1")
                # DB-MODEL rewrite (first job), then closing text.
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "DB-MODEL",
                        "content": _rewrite("DB-MODEL", "## Entities\nSupports SQLite and MySQL now."),
                    }),
                }])
                client.add_text_response("Done.")
                # Remaining jobs: UX-WIRE, SEC-RISK, QA-PLAN, PM-PLAN each just
                # write a trivial new body and stop.
                for key in ["UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## Section\nUpdated body."),
                        }),
                    }])
                    client.add_text_response("Done.")

                payloads = run_propagation_wave(app, pid, event["id"], client)

                # Every job produced a validated payload.
                assert {p.artifact_key for p in payloads} >= {
                    "DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"
                }
                db_model = next(p for p in payloads if p.artifact_key == "DB-MODEL")
                assert db_model.status == "ok"

                # The QA-PLAN prompt must contain the *updated* DB-MODEL text.
                qa_prompt = next(p for p in payloads if p.artifact_key == "QA-PLAN").prompt
                assert "Supports SQLite and MySQL now" in qa_prompt
                assert all(
                    call["kwargs"]["timeout"] == 180
                    for call in client.get_calls()
                )
                assert all(
                    call["kwargs"]["extra_body"] == {"reasoning": {"enabled": False}}
                    for call in client.get_calls()
                )
                assert all(
                    [tool["function"]["name"] for tool in call["tools"]] == ["read_artifact", "write_artifact", "ask_question"]
                    for call in client.get_calls()
                )
                calls = client.get_calls()
                assert all(
                    calls[index]["tool_choice"] == {
                        "type": "function",
                        "function": {"name": "read_artifact"},
                    }
                    for index in range(0, len(calls), 2)
                )
                assert all(
                    calls[index]["tool_choice"] == "auto"
                    for index in range(1, len(calls), 2)
                )


class TestShrinkGuard:
    """C16 gate: summary-instead-of-doc rejected."""

    def test_one_line_summary_rejected(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                client = FakeOpenAIClient()
                # First (DB-MODEL) is fine. Next depth-1 job by key is SEC-RISK,
                # which returns a one-line summary.
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "DB-MODEL",
                        "content": _rewrite("DB-MODEL", "## X\nBody."),
                    }),
                }])
                client.add_text_response("Done.")
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "SEC-RISK",
                        "content": "summary.",
                    }),
                }])
                client.add_text_response("Done.")
                for key in ["UX-WIRE", "QA-PLAN", "PM-PLAN"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## S\nBody."),
                        }),
                    }])
                    client.add_text_response("Done.")

                payloads = run_propagation_wave(app, pid, event["id"], client)
                sec = next(p for p in payloads if p.artifact_key == "SEC-RISK")
                assert sec.status == "rejected"
                assert any("Shrink guard" in e for e in sec.errors)


class TestSameRoleBatching:
    """Two artifacts owned by one role share a batch; second sees first's output."""

    def test_second_sibling_sees_first_rewrite(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("P")
                pid = folder["id"]
                # Create the QA persona BEFORE registration so the template can
                # map the "QA/Tester" role to it.
                qa_persona = db_module.create_persona("QA/Tester", "You are a QA tester.")
                qa_persona_id = qa_persona["id"]
                db_module.register_project_from_template(pid, "sdlc", tmp_dir)

                # Give QA a sibling artifact owned by the same role.
                db_module.create_artifact(
                    project_id=pid,
                    artifact_key="QA-EXTRA-1",
                    rel_path="Test Cases/QA-EXTRA-1.md",
                    role_persona_id=qa_persona_id,
                )
                db_module.create_artifact_dep(pid, "BA-REQ", "QA-EXTRA-1")
                db_module.create_artifact_trace(pid, "QA-EXTRA-1", "REQ-014")
                db_module.create_artifact_trace(pid, "QA-PLAN", "REQ-014")

                event = db_module.create_change_event(
                    project_id=pid, source_key="BA-REQ",
                    from_version=1, to_version=2, changed_reqs=["REQ-014"],
                )
                from propagation.impact import queue_propagation_jobs
                queue_propagation_jobs(pid, event["id"], ["REQ-014"])

                jobs = db_module.list_propagation_jobs(event["id"])
                qa_jobs = [j for j in jobs if j["artifact_key"].startswith("QA-")]
                qa_batches = {j["batch_id"] for j in qa_jobs}
                assert len(qa_batches) == 1

                client = FakeOpenAIClient()
                # DB-MODEL rewrite first (deepest/earliest).
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "DB-MODEL",
                        "content": _rewrite("DB-MODEL", "## E\nDB body."),
                    }),
                }])
                client.add_text_response("Done.")
                for key in ["UX-WIRE", "SEC-RISK"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## S\nbody."),
                        }),
                    }])
                    client.add_text_response("Done.")
                # QA-PLAN first (sibling order by key), then QA-EXTRA-1.
                for key in ["QA-PLAN", "QA-EXTRA-1"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## S\nquery body."),
                        }),
                    }])
                    client.add_text_response("Done.")
                for key in ["PM-PLAN"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## S\nbody."),
                        }),
                    }])
                    client.add_text_response("Done.")

                payloads = run_propagation_wave(app, pid, event["id"], client)
                qa_extra = next((p for p in payloads if p.artifact_key == "QA-EXTRA-1"), None)
                assert qa_extra is not None


class TestServerAuthoredFrontMatter:
    """Model-provided version numbers are discarded (D15)."""

    def test_version_bumped_by_server(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                client = _scripted_client([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "DB-MODEL",
                        "content": "---\nversion: 99\n---\n" + _rewrite("DB-MODEL", "## S\nbody."),
                    }),
                }])
                for key in ["UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## S\nbody."),
                        }),
                    }])
                    client.add_text_response("Done.")

                payloads = run_propagation_wave(app, pid, event["id"], client)
                db_model = next(p for p in payloads if p.artifact_key == "DB-MODEL")
                # Original placeholder version is 1 -> server-authored is 2.
                assert db_model.version == 2
                assert "version: 2" in db_model.content
                assert "version: 99" not in db_model.content


class TestRewriteGuards:
    """Pure-function tests for the D15 guards."""

    def test_heading_retention_fails(self):
        original = "# A\n\n## Section 1\nText\n## Section 2\nMore\n"
        rewrite = "# A\n\n## Section 1\nText\n"
        assessment = assess_rewrite(original, rewrite)
        assert assessment.needs_review
        assert any("Heading retention" in e for e in assessment.errors)

    def test_marker_retention_fails(self):
        original = "[ASSUMPTION: REQ-001] Assume TLS [/ASSUMPTION]\n## S\nbody\n"
        rewrite = "## S\nbody\n"
        assessment = assess_rewrite(original, rewrite)
        assert assessment.needs_review
        assert any("markers dropped" in e.lower() or "marker" in e.lower() for e in assessment.errors)

    def test_resolved_marker_allowed(self):
        original = "[ASSUMPTION: REQ-001] Assume TLS [/ASSUMPTION]\n## S\nbody\n"
        rewrite = "## S\nbody\n"
        assessment = assess_rewrite(original, rewrite, resolved_req_ids={"REQ-001"})
        assert not any("marker" in e.lower() for e in assessment.errors)

    def test_requirement_reference_retained(self):
        original = "## REQ-001 — TLS\n## REQ-002 — Auth\nbody\n"
        rewrite = "## REQ-001 — TLS\nbody\n"
        assessment = assess_rewrite(original, rewrite)
        assert any("Requirement references missing" in e for e in assessment.errors)

    def test_removed_req_allows_shrink_and_loss(self):
        original = "## REQ-001 — TLS\n## REQ-002 — Auth\nbody\n"
        rewrite = "## REQ-001 — TLS\nonly kept\n"
        assessment = assess_rewrite(original, rewrite, removed_reqs=["REQ-002"])
        assert not assessment.rejected


class TestDiffOutputWave:
    """The wave must accept agent diffs (not just whole-file content) and apply
    them to the current artifact before validation."""

    def test_wave_applies_diffs(self, tmp_db):
        import difflib
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                from propagation.scanner import read_artifact_file
                from propagation.impact import compute_affected_depths

                # The wave runs jobs in (depth, artifact_key) order — script the
                # fake client in that same order or the diffs get misapplied.
                order = sorted(
                    ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"],
                    key=lambda k: (
                        compute_affected_depths(pid, ["DB-MODEL", "UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]).get(k, 0),
                        k,
                    ),
                )
                client = FakeOpenAIClient()
                for key in order:
                    rel = next(a["rel_path"] for a in db_module.list_artifacts(pid) if a["artifact_key"] == key)
                    current, _ = read_artifact_file(tmp_dir, rel)
                    if current and not current.endswith("\n"):
                        current += "\n"  # mirror the wave's normalisation
                    new = current + "\n## updated\n{key} body rewritten.\n".format(key=key)
                    diff = "".join(difflib.unified_diff(
                        current.splitlines(keepends=True),
                        new.splitlines(keepends=True),
                        fromfile=f"a/{key}", tofile=f"b/{key}",
                    ))
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({"artifact_key": key, "diff": diff}),
                    }])
                    client.add_text_response("Done.")

                payloads = run_propagation_wave(app, pid, event["id"], client)
                by_key = {p.artifact_key: p for p in payloads}
                assert set(by_key) >= set(order)
                for p in payloads:
                    assert p.status in ("ok", "needs_review"), p
                # The applied diff content must actually be present in the ok payloads.
                for key, p in by_key.items():
                    if p.status == "ok":
                        assert f"{key} body rewritten." in p.content, (key, p.status)

    def test_unappliable_diff_is_rejected(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                client = FakeOpenAIClient()
                # Emit a diff that references lines that do not exist.
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "DB-MODEL",
                        "diff": "@@ -999,1 +999,1 @@\n-zzz\n+yyy\n",
                    }),
                }])
                client.add_text_response("Done.")

                payloads = run_propagation_wave(app, pid, event["id"], client)
                db_model = next(p for p in payloads if p.artifact_key == "DB-MODEL")
                assert db_model.status == "rejected"
                assert any("diff" in e.lower() for e in db_model.errors)
                job = next(
                    job for job in db_module.list_propagation_jobs(event["id"])
                    if job["artifact_key"] == "DB-MODEL"
                )
                assert job["state"] == "failed"
                assert db_module.get_artifact(pid, "DB-MODEL")["status"] == "stale"

    def test_retry_skips_already_applied_artifacts(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                db_model_job = next(
                    job for job in db_module.list_propagation_jobs(event["id"])
                    if job["artifact_key"] == "DB-MODEL"
                )
                db_module.update_propagation_job(db_model_job["id"], state="applied")

                client = _scripted_client([
                    {
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## Updated\nRetry content."),
                        }),
                    }
                    for key in ["SEC-RISK", "UX-WIRE", "QA-PLAN", "PM-PLAN"]
                ])
                payloads = run_propagation_wave(app, pid, event["id"], client)

                assert "DB-MODEL" not in {payload.artifact_key for payload in payloads}
                assert {payload.artifact_key for payload in payloads} == {
                    "SEC-RISK", "UX-WIRE", "QA-PLAN", "PM-PLAN",
                }

    def test_agent_question_is_recorded_during_assessment(self, tmp_db):
        app = _make_app(tmp_db)
        with app.app_context():
            with tempfile.TemporaryDirectory() as tmp_dir:
                pid, event = _setup_wave(app, tmp_db, tmp_dir)
                client = FakeOpenAIClient()
                client.add_tool_calls_response([{
                    "name": "ask_question",
                    "arguments": json.dumps({
                        "requirement_id": "REQ-014",
                        "question": "Which retention period applies to these records?",
                        "blocking": False,
                    }),
                }])
                client.add_tool_calls_response([{
                    "name": "write_artifact",
                    "arguments": json.dumps({
                        "artifact_key": "DB-MODEL",
                        "content": _rewrite("DB-MODEL", "## Entities\nUpdated body."),
                    }),
                }])
                client.add_text_response("Done.")
                for key in ["UX-WIRE", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
                    client.add_tool_calls_response([{
                        "name": "write_artifact",
                        "arguments": json.dumps({
                            "artifact_key": key,
                            "content": _rewrite(key, "## Updated\nUpdated body."),
                        }),
                    }])
                    client.add_text_response("Done.")

                run_propagation_wave(app, pid, event["id"], client)

                issues = db_module.list_agent_issues(pid, kind="question")
                assert len(issues) == 1
                assert issues[0]["raised_by_key"] == "DB-MODEL"
                assert issues[0]["req_id"] == "REQ-014"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
