import os
import json
import pytest
import database as db_module
from app import create_app
from templates import (
    get_template,
    list_templates,
    expand_role_edges_to_artifact_edges,
    topological_sort,
    compute_depths,
    CycleDetectedError,
    check_artifact_cap,
    get_artifact_spec,
    get_artifacts_by_role,
    ProjectTemplate,
    ArtifactSpec,
)


class TestProjectTemplates:
    """Tests for PROJECT_TEMPLATES registry."""

    def test_sdlc_template_exists(self):
        """SDLC template is registered."""
        template = get_template("sdlc")
        assert template is not None
        assert template.id == "sdlc"
        assert template.name == "SDLC Project"

    def test_list_templates(self):
        """List templates returns SDLC template."""
        templates = list_templates()
        assert len(templates) >= 1
        assert any(t.id == "sdlc" for t in templates)

    def test_sdlc_template_artifacts(self):
        """SDLC template has expected artifacts."""
        template = get_template("sdlc")
        artifact_keys = [a.key for a in template.artifacts]
        expected = ["BA-REQ", "UX-WIRE", "DB-MODEL", "QA-PLAN", "SEC-RISK", "PM-PLAN"]
        for key in expected:
            assert key in artifact_keys

    def test_sdlc_template_roles(self):
        """SDLC template has expected roles."""
        template = get_template("sdlc")
        roles = set(a.role for a in template.artifacts)
        expected_roles = {
            "Business Analyst",
            "UX Designer",
            "Database Developer",
            "QA/Tester",
            "Security Analyst",
            "Project Manager",
        }
        assert roles == expected_roles

    def test_sdlc_template_role_to_persona(self):
        """SDLC template maps roles to personas."""
        template = get_template("sdlc")
        assert template.role_to_persona["Business Analyst"] == "Business Analyst"
        assert template.role_to_persona["UX Designer"] == "UX Designer"
        assert template.role_to_persona["Database Developer"] == "Database Developer"

    def test_sdlc_template_default_mode(self):
        """SDLC template has propose as default propagation mode."""
        template = get_template("sdlc")
        assert template.default_propagation_mode == "propose"

    def test_get_artifact_spec(self):
        """Get artifact spec by key."""
        template = get_template("sdlc")
        spec = get_artifact_spec(template, "DB-MODEL")
        assert spec is not None
        assert spec.key == "DB-MODEL"
        assert spec.rel_path == "Data/DB-MODEL.md"
        assert spec.role == "Database Developer"

    def test_get_artifacts_by_role(self):
        """Get artifacts for a specific role."""
        template = get_template("sdlc")
        ba_artifacts = get_artifacts_by_role(template, "Business Analyst")
        assert len(ba_artifacts) == 1
        assert ba_artifacts[0].key == "BA-REQ"

        # QA has 1 artifact in minimal template
        qa_artifacts = get_artifacts_by_role(template, "QA/Tester")
        assert len(qa_artifacts) == 1
        assert qa_artifacts[0].key == "QA-PLAN"


class TestRoleEdgeExpansion:
    """Tests for role-level to artifact-level edge expansion."""

    def test_expand_edges(self):
        """Role edges expand to artifact edges."""
        template = get_template("sdlc")
        edges = expand_role_edges_to_artifact_edges(template)

        # BA-REQ has no upstream roles
        ba_upstream = [u for u, d in edges if d == "BA-REQ"]
        assert len(ba_upstream) == 0

        # UX-WIRE depends on BA-REQ
        ux_upstream = [u for u, d in edges if d == "UX-WIRE"]
        assert "BA-REQ" in ux_upstream

        # DB-MODEL depends on BA-REQ
        db_upstream = [u for u, d in edges if d == "DB-MODEL"]
        assert "BA-REQ" in db_upstream

        # QA-PLAN depends on BA-REQ and DB-MODEL
        qa_upstream = [u for u, d in edges if d == "QA-PLAN"]
        assert "BA-REQ" in qa_upstream
        assert "DB-MODEL" in qa_upstream

        # SEC-RISK depends on BA-REQ only (per golden scenario)
        sec_upstream = [u for u, d in edges if d == "SEC-RISK"]
        assert "BA-REQ" in sec_upstream
        assert "DB-MODEL" not in sec_upstream

        # PM-PLAN depends on UX-WIRE, DB-MODEL, QA-PLAN, SEC-RISK
        pm_upstream = [u for u, d in edges if d == "PM-PLAN"]
        assert "UX-WIRE" in pm_upstream
        assert "DB-MODEL" in pm_upstream
        assert "QA-PLAN" in pm_upstream
        assert "SEC-RISK" in pm_upstream

    def test_edge_count(self):
        """Correct number of edges for SDLC template."""
        template = get_template("sdlc")
        edges = expand_role_edges_to_artifact_edges(template)
        # BA->UX, BA->DB, BA->QA, DB->QA, BA->SEC
        # UX->PM, DB->PM, QA->PM, SEC->PM
        # Total: 9 edges
        assert len(edges) == 9


class TestTopologicalSort:
    """Tests for topological sort with cycle detection."""

    def test_topo_sort_sdlc(self):
        """Topological sort of SDLC artifacts yields correct depths."""
        template = get_template("sdlc")
        artifact_keys = [a.key for a in template.artifacts]
        edges = expand_role_edges_to_artifact_edges(template)

        sorted_keys = topological_sort(artifact_keys, edges)
        depths = compute_depths(artifact_keys, edges)

        # All artifacts should be in result
        assert set(sorted_keys) == set(artifact_keys)

        # Depth assertions for golden scenario
        assert depths["BA-REQ"] == 0  # Source
        assert depths["UX-WIRE"] == 1
        assert depths["DB-MODEL"] == 1
        assert depths["SEC-RISK"] == 1
        assert depths["QA-PLAN"] == 2  # Depends on BA-REQ (0) and DB-MODEL (1)
        assert depths["PM-PLAN"] == 3  # Depends on UX/DB/QA/SEC at depth 1/2

    def test_topo_sort_deterministic(self):
        """Topological sort is deterministic (stable ordering)."""
        template = get_template("sdlc")
        artifact_keys = [a.key for a in template.artifacts]
        edges = expand_role_edges_to_artifact_edges(template)

        # Run multiple times
        results = [topological_sort(artifact_keys, edges) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_topo_sort_sibling_ordering(self):
        """Sibling artifacts at same depth ordered by key."""
        # Create simple graph: A -> B, A -> C
        keys = ["A", "B", "C"]
        edges = [("A", "B"), ("A", "C")]
        sorted_keys = topological_sort(keys, edges)
        depths = compute_depths(keys, edges)

        assert depths["A"] == 0
        assert depths["B"] == 1
        assert depths["C"] == 1
        # B and C at same depth, ordered alphabetically
        assert sorted_keys.index("B") < sorted_keys.index("C")

    def test_cycle_detection(self):
        """Cycle detection raises CycleDetectedError."""
        keys = ["A", "B", "C"]
        edges = [("A", "B"), ("B", "C"), ("C", "A")]  # Cycle

        with pytest.raises(CycleDetectedError) as exc_info:
            topological_sort(keys, edges)

        assert "cycle" in str(exc_info.value).lower()
        assert len(exc_info.value.cycle) >= 3

    def test_self_cycle(self):
        """Self-loop detected as cycle."""
        keys = ["A"]
        edges = [("A", "A")]

        with pytest.raises(CycleDetectedError):
            topological_sort(keys, edges)

    def test_disconnected_graph(self):
        """Topological sort works with disconnected components."""
        keys = ["A", "B", "C", "D"]
        edges = [("A", "B"), ("C", "D")]  # Two separate chains

        sorted_keys = topological_sort(keys, edges)
        assert set(sorted_keys) == set(keys)
        # A before B, C before D
        assert sorted_keys.index("A") < sorted_keys.index("B")
        assert sorted_keys.index("C") < sorted_keys.index("D")


class TestProjectRegistration:
    """Tests for project registration from template."""

    def test_register_project_creates_artifacts(self, tmp_db):
        """Register project creates all template artifacts."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test SDLC Project")
                project_id = folder["id"]

                result = db_module.register_project_from_template(
                    project_id=project_id,
                    template_id="sdlc",
                    workspace_dir=tmp_dir,
                )

                assert result["project_id"] == project_id
                assert result["template_id"] == "sdlc"
                assert result["artifacts_registered"] == 6
                assert result["edges_created"] == 9
                assert len(result["artifact_keys"]) == 6

    def test_registered_artifacts_have_correct_data(self, tmp_db):
        """Registered artifacts have correct keys, paths, roles."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                artifacts = db_module.list_artifacts(project_id)
                assert len(artifacts) == 6

                # Check specific artifacts
                ba_req = next(a for a in artifacts if a["artifact_key"] == "BA-REQ")
                assert ba_req["rel_path"] == "Requirements/BA-REQ.md"
                assert ba_req["status"] == "current"

                db_model = next(a for a in artifacts if a["artifact_key"] == "DB-MODEL")
                assert db_model["rel_path"] == "Data/DB-MODEL.md"

    def test_artifact_deps_created(self, tmp_db):
        """Artifact dependencies are created from template."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                deps = db_module.list_artifact_deps(project_id)
                assert len(deps) == 9

                # Check specific deps
                ba_to_ux = [(d["upstream_key"], d["downstream_key"]) for d in deps
                           if d["upstream_key"] == "BA-REQ" and d["downstream_key"] == "UX-WIRE"]
                assert len(ba_to_ux) == 1

    def test_folder_updated_to_project(self, tmp_db):
        """Folder is updated to project kind with template info."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                updated = db_module.get_folder(project_id)
                assert updated["kind"] == "project"
                assert updated["template_id"] == "sdlc"
                assert updated["propagation_mode"] == "propose"
                assert updated["workspace_dir"] == tmp_dir

    def test_register_unknown_template_fails(self, tmp_db):
        """Registering unknown template raises ValueError."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            folder = db_module.create_folder("Test Project")
            project_id = folder["id"]

            with pytest.raises(ValueError, match="Unknown template"):
                db_module.register_project_from_template(project_id, "unknown", "")


class TestArtifactGraph:
    """Tests for get_project_artifact_graph."""

    def test_get_graph_returns_sorted_and_depths(self, tmp_db):
        """Graph returns sorted keys and correct depths."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                graph = db_module.get_project_artifact_graph(project_id)

                assert "artifact_keys" in graph
                assert "sorted_keys" in graph
                assert "edges" in graph
                assert "depths" in graph

                # Check depths match golden scenario
                depths = graph["depths"]
                assert depths["BA-REQ"] == 0
                assert depths["UX-WIRE"] == 1
                assert depths["DB-MODEL"] == 1
                assert depths["SEC-RISK"] == 1
                assert depths["QA-PLAN"] == 2
                assert depths["PM-PLAN"] == 3


class TestArtifactRegistrationCheck:
    """Tests for is_artifact_registered and write_artifact rejection."""

    def test_is_artifact_registered(self, tmp_db):
        """Check if artifact is registered."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                assert db_module.is_artifact_registered(project_id, "BA-REQ") is True
                assert db_module.is_artifact_registered(project_id, "DB-MODEL") is True
                assert db_module.is_artifact_registered(project_id, "UNKNOWN-KEY") is False

    def test_write_artifact_rejects_unregistered_key(self, tmp_db):
        """write_artifact rejects unregistered artifact_key."""
        import tools

        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                # Try to write unregistered artifact
                result = tools.execute_tool_call(
                    "write_artifact",
                    json.dumps({"artifact_key": "EVIL-KEY", "content": "# Evil"}),
                    context="propagation",
                    workspace_dir=tmp_dir,
                    project_id=project_id,
                )

                assert result["success"] is False
                assert "not registered" in result["result"] or "Unregistered" in result["result"]

    def test_write_artifact_allows_registered_key(self, tmp_db):
        """write_artifact allows registered artifact_key."""
        import tools

        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                # Write registered artifact - should succeed
                result = tools.execute_tool_call(
                    "write_artifact",
                    json.dumps({"artifact_key": "BA-REQ", "content": "# Requirements"}),
                    context="propagation",
                    workspace_dir=tmp_dir,
                    project_id=project_id,
                )

                assert result["success"] is True


class TestArtifactCap:
    """Tests for artifact cap enforcement."""

    def test_check_artifact_cap(self, tmp_db):
        """Check artifact cap enforcement."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                template = get_template("sdlc")

                # Should be allowed (0/10)
                allowed, current, cap = check_artifact_cap(project_id, template, "Business Analyst")
                assert allowed is True
                assert current == 1  # BA-REQ already registered
                assert cap == 10

    def test_cap_exceeded_after_10(self, tmp_db):
        """Cap exceeded after 10 artifacts for a role."""
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": tmp_db,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1"
        })

        with app.app_context():
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                folder = db_module.create_folder("Test Project")
                project_id = folder["id"]

                db_module.register_project_from_template(project_id, "sdlc", tmp_dir)

                template = get_template("sdlc")

                # Create 9 more BA artifacts (total 10)
                for i in range(9):
                    db_module.create_artifact(
                        project_id=project_id,
                        artifact_key=f"BA-EXTRA-{i:02d}",
                        rel_path=f"Requirements/BA-EXTRA-{i:02d}.md",
                        role_persona_id=1,  # Business Analyst persona
                    )

                # Should still be allowed at 10
                allowed, current, cap = check_artifact_cap(project_id, template, "Business Analyst")
                assert allowed is True
                assert current == 10

                # Add 11th - should be rejected
                db_module.create_artifact(
                    project_id=project_id,
                    artifact_key="BA-EXTRA-09",
                    rel_path="Requirements/BA-EXTRA-09.md",
                    role_persona_id=1,
                )

                allowed, current, cap = check_artifact_cap(project_id, template, "Business Analyst")
                assert allowed is False
                assert current == 11


class TestCustomTemplateCycleDetection:
    """Tests that custom templates with cycles are rejected."""

    def test_custom_template_with_cycle_rejected(self, tmp_db):
        """Custom template with cycle fails registration."""
        # Create a template with a cycle
        from templates import PROJECT_TEMPLATES, ProjectTemplate, ArtifactSpec

        bad_template = ProjectTemplate(
            id="bad",
            name="Bad Template",
            description="Has cycle",
            artifacts=[
                ArtifactSpec(key="A", rel_path="A.md", role="Role1", upstream_roles=["Role2"]),
                ArtifactSpec(key="B", rel_path="B.md", role="Role2", upstream_roles=["Role1"]),
            ],
            role_to_persona={"Role1": "Business Analyst", "Role2": "UX Designer"},
        )

        PROJECT_TEMPLATES["bad"] = bad_template

        try:
            app = create_app(config={
                "SQLALCHEMY_DATABASE_URI": tmp_db,
                "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
                "SKIP_DOTENV_WRITE": "1"
            })

            with app.app_context():
                import tempfile
                with tempfile.TemporaryDirectory() as tmp_dir:
                    folder = db_module.create_folder("Bad Project")
                    project_id = folder["id"]

                    with pytest.raises(CycleDetectedError):
                        db_module.register_project_from_template(project_id, "bad", tmp_dir)
        finally:
            del PROJECT_TEMPLATES["bad"]


class TestFixturesExtended:
    """Extended self-tests for C08 fixtures."""

    def test_templates_module_imports(self):
        """Templates module imports without error."""
        import templates
        assert hasattr(templates, "PROJECT_TEMPLATES")
        assert hasattr(templates, "get_template")
        assert hasattr(templates, "topological_sort")
        assert hasattr(templates, "CycleDetectedError")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])