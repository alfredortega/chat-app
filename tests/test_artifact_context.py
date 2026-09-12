import pytest
import file_handler


class TestBuildArtifactContext:
    """Tests for build_artifact_context() pure function (C05)."""

    def test_empty_artifacts_returns_empty_context(self):
        """Empty artifacts dict returns empty context."""
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts={},
            change_event={},
        )
        assert context == ""
        assert char_count == 0
        assert truncated == []

    def test_empty_artifacts_with_change_event(self):
        """Change event is included even with no artifacts."""
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts={},
            change_event={"summary": "Changed REQ-014", "diff": "- old\n+ new"},
        )
        assert "Change Summary: Changed REQ-014" in context
        assert "Diff:" in context
        assert char_count > 0
        assert truncated == []

    def test_single_artifact_included(self):
        """Single artifact is included in context."""
        artifacts = {
            "BA-REQ": {"content": "# Requirements\n\nREQ-014: Use SQLite"},
        }
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts=artifacts,
            change_event={},
        )
        assert "ARTIFACT: BA-REQ" in context
        assert "REQ-014: Use SQLite" in context
        assert char_count > 0
        assert truncated == []

    def test_multiple_artifacts_sorted_deterministically(self):
        """Multiple artifacts are included in sorted order."""
        artifacts = {
            "DB-MODEL": {"content": "# DB Model"},
            "BA-REQ": {"content": "# Requirements"},
            "QA-PLAN": {"content": "# QA Plan"},
        }
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts=artifacts,
            change_event={},
        )
        # Should be in alphabetical order: BA-REQ, DB-MODEL, QA-PLAN
        ba_pos = context.find("BA-REQ")
        db_pos = context.find("DB-MODEL")
        qa_pos = context.find("QA-PLAN")
        assert ba_pos < db_pos < qa_pos
        assert truncated == []

    def test_change_event_with_requirement_ids(self):
        """Change event includes requirement_ids."""
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts={},
            change_event={
                "summary": "Updated requirement",
                "requirement_ids": ["REQ-014", "REQ-015"],
            },
        )
        assert "REQ-014" in context
        assert "REQ-015" in context
        assert "Requirements in scope" in context

    def test_golden_scenario_contains_ba_req_and_diff_not_pm_plan(self):
        """
        Golden scenario: context contains BA-REQ + diff, but NOT PM-PLAN.
        This is the key acceptance criterion from the plan.
        """
        artifacts = {
            "BA-REQ": {"content": "REQ-014: Use SQLite and MySQL via SQLAlchemy"},
            "DB-MODEL": {"content": "# Database Model\n\nDual DB support"},
            "UX-WIRE": {"content": "# UX Wireframes"},
            "QA-PLAN": {"content": "# QA Plan"},
            "SEC-RISK": {"content": "# Security Risk"},
            "PM-PLAN": {"content": "# Project Plan\n\nThis should NOT appear in context"},
        }
        change_event = {
            "diff": "- REQ-014: local SQLite only\n+ REQ-014: SQLite and MySQL via SQLAlchemy",
            "summary": "Changed REQ-014 to support dual database",
            "requirement_ids": ["REQ-014"],
        }

        # Only pass upstream artifacts (BA-REQ, DB-MODEL) - NOT PM-PLAN
        upstream_artifacts = {
            "BA-REQ": artifacts["BA-REQ"],
            "DB-MODEL": artifacts["DB-MODEL"],
        }

        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts=upstream_artifacts,
            change_event=change_event,
        )

        # Should contain BA-REQ
        assert "BA-REQ" in context
        assert "REQ-014" in context
        assert "SQLAlchemy" in context

        # Should contain change event
        assert "Change Summary" in context
        assert "Diff:" in context
        assert "SQLite and MySQL" in context

        # Should NOT contain PM-PLAN (not passed in artifacts)
        assert "PM-PLAN" not in context
        assert "Project Plan" not in context

        assert truncated == []

    def test_oversized_input_raises_value_error(self):
        """Oversized input raises ValueError rather than silently truncating."""
        # Create artifacts that exceed HARD_LIMIT (500,000 chars)
        large_content = "x" * 300_000
        artifacts = {
            "ARTIFACT-1": {"content": large_content},
            "ARTIFACT-2": {"content": large_content},
            "ARTIFACT-3": {"content": large_content},
        }

        with pytest.raises(ValueError, match="exceeds hard limit"):
            file_handler.build_artifact_context(
                artifacts=artifacts,
                change_event={},
            )

    def test_oversized_with_custom_max_chars(self):
        """Custom max_chars limit is respected."""
        artifacts = {
            "A": {"content": "x" * 100},
            "B": {"content": "y" * 100},
        }

        # Should succeed with limit 300
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts=artifacts,
            change_event={},
            max_chars=300,
        )
        assert char_count <= 300

        # Should fail with limit 150
        with pytest.raises(ValueError, match="exceeds hard limit"):
            file_handler.build_artifact_context(
                artifacts=artifacts,
                change_event={},
                max_chars=150,
            )

    def test_oversized_input_raises_with_truncated_keys_in_error(self):
        """Oversized input raises ValueError with truncated_keys in error message."""
        artifacts = {
            "SMALL": {"content": "small"},
            "LARGE": {"content": "x" * 400_000},
        }

        # Use a smaller max_chars to force truncation
        with pytest.raises(ValueError, match="exceeds hard limit") as exc_info:
            file_handler.build_artifact_context(
                artifacts=artifacts,
                change_event={},
                max_chars=10_000,
            )

        # Error message should mention truncated keys
        assert "LARGE" in str(exc_info.value)
        assert "SMALL" not in str(exc_info.value)

    def test_change_event_truncated_appears_in_error(self):
        """Change event appears in truncated_keys in error when it doesn't fit."""
        artifacts = {
            "BA-REQ": {"content": "x" * 490_000},
        }
        change_event = {
            "diff": "y" * 20_000,  # This would push over 500k
        }

        with pytest.raises(ValueError, match="exceeds hard limit") as exc_info:
            file_handler.build_artifact_context(
                artifacts=artifacts,
                change_event=change_event,
            )

        assert "CHANGE_EVENT" in str(exc_info.value)

    def test_artifact_with_empty_content_skipped(self):
        """Artifacts with empty content are skipped."""
        artifacts = {
            "EMPTY": {"content": ""},
            "VALID": {"content": "valid content"},
        }
        context, char_count, truncated = file_handler.build_artifact_context(
            artifacts=artifacts,
            change_event={},
        )
        assert "VALID" in context
        assert "EMPTY" not in context
        assert truncated == []

    def test_returns_triple(self):
        """Function returns (context_str, char_count, truncated_keys_list)."""
        result = file_handler.build_artifact_context(
            artifacts={"A": {"content": "test"}},
            change_event={},
        )
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert isinstance(result[0], str)
        assert isinstance(result[1], int)
        assert isinstance(result[2], list)

    def test_no_db_access_pure_function(self):
        """Function is pure - no DB access, no side effects."""
        # Just verify it works without any DB setup
        artifacts = {"TEST": {"content": "test"}}
        change_event = {"summary": "test"}
        context, count, truncated = file_handler.build_artifact_context(
            artifacts=artifacts,
            change_event=change_event,
        )
        assert "TEST" in context
        assert count > 0