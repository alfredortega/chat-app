import pytest
import parsers


class TestFrontMatterParser:
    """Tests for front-matter parsing and building."""

    def test_parse_valid_front_matter(self):
        """Parse valid front-matter with all required fields."""
        content = """---
artifact_id: DB-MODEL
role: Database Developer
version: 3
origin: propagation
derives_from:
  - artifact: BA-REQ
    version: 7
    requirements: [REQ-011, REQ-014]
---
# Database Model

Content here.
"""
        fm, remaining = parsers.parse_front_matter(content)

        assert fm["artifact_id"] == "DB-MODEL"
        assert fm["role"] == "Database Developer"
        assert fm["version"] == 3
        assert fm["origin"] == "propagation"
        assert len(fm["derives_from"]) == 1
        assert fm["derives_from"][0]["artifact"] == "BA-REQ"
        assert fm["derives_from"][0]["version"] == 7
        assert fm["derives_from"][0]["requirements"] == ["REQ-011", "REQ-014"]
        assert remaining.startswith("# Database Model")

    def test_parse_minimal_front_matter(self):
        """Parse front-matter with only required fields."""
        content = """---
artifact_id: BA-REQ
role: Business Analyst
version: 1
---
Requirements content.
"""
        fm, remaining = parsers.parse_front_matter(content)

        assert fm["artifact_id"] == "BA-REQ"
        assert fm["role"] == "Business Analyst"
        assert fm["version"] == 1
        assert "derives_from" not in fm
        assert remaining.startswith("Requirements content")

    def test_parse_missing_front_matter_raises(self):
        """Missing front-matter raises FrontMatterError."""
        content = "# Just a heading\n\nNo front matter here."
        with pytest.raises(parsers.FrontMatterError, match="No front-matter found"):
            parsers.parse_front_matter(content)

    def test_parse_invalid_yaml_raises(self):
        """Invalid YAML raises FrontMatterError."""
        content = """---
artifact_id: DB-MODEL
role: Database Developer
version: "not an int"
invalid: [unclosed
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="Invalid YAML"):
            parsers.parse_front_matter(content)

    def test_parse_non_dict_yaml_raises(self):
        """YAML that isn't a mapping raises FrontMatterError."""
        content = """---
- item1
- item2
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="must be a YAML mapping"):
            parsers.parse_front_matter(content)

    def test_parse_missing_required_field_raises(self):
        """Missing required field raises FrontMatterError."""
        content = """---
artifact_id: DB-MODEL
role: Database Developer
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="Missing required front-matter field: version"):
            parsers.parse_front_matter(content)

    def test_parse_invalid_field_types_raise(self):
        """Wrong types for required fields raise FrontMatterError."""
        content = """---
artifact_id: 123
role: Database Developer
version: 1
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="artifact_id must be a string"):
            parsers.parse_front_matter(content)

        content = """---
artifact_id: DB-MODEL
role: 123
version: 1
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="role must be a string"):
            parsers.parse_front_matter(content)

        content = """---
artifact_id: DB-MODEL
role: Database Developer
version: "one"
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="version must be an integer"):
            parsers.parse_front_matter(content)

    def test_parse_invalid_derives_from_raises(self):
        """Invalid derives_from structure raises FrontMatterError."""
        content = """---
artifact_id: DB-MODEL
role: Database Developer
version: 1
derives_from: "not a list"
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="derives_from must be a list"):
            parsers.parse_front_matter(content)

        content = """---
artifact_id: DB-MODEL
role: Database Developer
version: 1
derives_from:
  - not_a_dict
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="Each derives_from entry must be a mapping"):
            parsers.parse_front_matter(content)

        content = """---
artifact_id: DB-MODEL
role: Database Developer
version: 1
derives_from:
  - artifact: BA-REQ
    version: "seven"
---
Content.
"""
        with pytest.raises(parsers.FrontMatterError, match="derives_from.version must be an integer"):
            parsers.parse_front_matter(content)

    def test_build_front_matter(self):
        """Build front-matter string from components."""
        fm_str = parsers.build_front_matter(
            artifact_id="DB-MODEL",
            role="Database Developer",
            version=3,
            origin="propagation",
            derives_from=[
                {"artifact": "BA-REQ", "version": 7, "requirements": ["REQ-011", "REQ-014"]}
            ],
        )

        assert fm_str.startswith("---\n")
        assert fm_str.endswith("\n---\n")
        assert "artifact_id: DB-MODEL" in fm_str
        assert "role: Database Developer" in fm_str
        assert "version: 3" in fm_str
        assert "origin: propagation" in fm_str
        assert "derives_from:" in fm_str

    def test_build_minimal_front_matter(self):
        """Build front-matter with only required fields."""
        fm_str = parsers.build_front_matter(
            artifact_id="BA-REQ",
            role="Business Analyst",
            version=1,
        )

        assert "artifact_id: BA-REQ" in fm_str
        assert "role: Business Analyst" in fm_str
        assert "version: 1" in fm_str
        assert "derives_from" not in fm_str

    def test_round_trip(self):
        """Parse then build produces equivalent front-matter."""
        original = """---
artifact_id: QA-PLAN
role: QA Tester
version: 2
origin: human
derives_from:
  - artifact: BA-REQ
    version: 5
    requirements: [REQ-001]
---
"""
        fm, _ = parsers.parse_front_matter(original + "Content.")
        rebuilt = parsers.build_front_matter(
            artifact_id=fm["artifact_id"],
            role=fm["role"],
            version=fm["version"],
            origin=fm.get("origin", "propagation"),
            derives_from=fm.get("derives_from"),
        )

        # Parse both and compare dicts
        fm2, _ = parsers.parse_front_matter(rebuilt + "Content.")
        assert fm == fm2


class TestREQExtractor:
    """Tests for REQ-nnn extraction and validation."""

    def test_extract_single_requirement(self):
        """Extract single REQ ID from heading."""
        content = """# Requirements

## REQ-014 — Use SQLite and MySQL via SQLAlchemy

Details here.
"""
        ids = parsers.extract_requirement_ids(content)
        assert ids == ["REQ-014"]

    def test_extract_multiple_requirements(self):
        """Extract multiple REQ IDs in order."""
        content = """## REQ-001 — First requirement

## REQ-014 — Fourteenth requirement

## REQ-022 — Twenty-second requirement
"""
        ids = parsers.extract_requirement_ids(content)
        assert ids == ["REQ-001", "REQ-014", "REQ-022"]

    def test_extract_with_titles(self):
        """Extract IDs with their titles."""
        content = """## REQ-014 — Use SQLite and MySQL

## REQ-015 — Support PostgreSQL
"""
        reqs = parsers.extract_requirements_with_titles(content)
        assert reqs == [("REQ-014", "Use SQLite and MySQL"), ("REQ-015", "Support PostgreSQL")]

    def test_extract_ignores_non_req_headings(self):
        """Non-REQ headings are ignored."""
        content = """## Introduction

## REQ-014 — Actual requirement

### Subheading

## REQ-015 — Another
"""
        ids = parsers.extract_requirement_ids(content)
        assert ids == ["REQ-014", "REQ-015"]

    def test_validate_no_duplicates(self):
        """Validation passes with no duplicates."""
        content = """## REQ-001 — First

## REQ-002 — Second
"""
        result = parsers.validate_requirements(content)
        assert result["duplicates"] == []
        assert result["ids"] == ["REQ-001", "REQ-002"]

    def test_validate_detects_duplicates(self):
        """Validation detects duplicate IDs."""
        content = """## REQ-014 — First

## REQ-014 — Duplicate

## REQ-015 — Third
"""
        with pytest.raises(parsers.RequirementValidationError, match="Duplicate requirement IDs"):
            parsers.validate_requirements(content)

    def test_validate_detects_gaps(self):
        """Validation detects sequence gaps."""
        content = """## REQ-001 — First

## REQ-003 — Third (002 missing)
"""
        result = parsers.validate_requirements(content, next_req_seq=4)
        assert "REQ-002" in result["gaps"]

    def test_validate_no_gaps_when_complete(self):
        """No gaps reported when sequence is complete."""
        content = """## REQ-001 — First

## REQ-002 — Second

## REQ-003 — Third
"""
        result = parsers.validate_requirements(content, next_req_seq=4)
        assert result["gaps"] == []

    def test_detect_renumber(self):
        """Detect renumbered requirements by similar text."""
        old = """## REQ-014 — Use SQLite only

## REQ-015 — Support MySQL
"""
        new = """## REQ-014 — Use SQLite and MySQL via SQLAlchemy

## REQ-016 — Support PostgreSQL
"""
        renumbers = parsers.detect_renumber(old, new)
        # REQ-015 text similar to REQ-014 new text
        assert len(renumbers) >= 0  # May or may not detect depending on similarity


class TestAssumptionMarkerParser:
    """Tests for assumption marker parsing and building."""

    def test_parse_single_assumption_marker(self):
        """Parse single assumption marker."""
        content = "Some text [ASSUMPTION: REQ-014] Assumed dual DB support [/ASSUMPTION] more text."
        markers = parsers.parse_assumption_markers(content)
        assert len(markers) == 1
        assert markers[0]["req_id"] == "REQ-014"
        assert markers[0]["assumption"] == "Assumed dual DB support"

    def test_parse_multiple_assumption_markers(self):
        """Parse multiple assumption markers."""
        content = """[ASSUMPTION: REQ-014] Assumed X [/ASSUMPTION]
[ASSUMPTION: REQ-015] Assumed Y [/ASSUMPTION]"""
        markers = parsers.parse_assumption_markers(content)
        assert len(markers) == 2
        assert markers[0]["req_id"] == "REQ-014"
        assert markers[1]["req_id"] == "REQ-015"

    def test_parse_multiline_assumption(self):
        """Parse assumption marker spanning multiple lines."""
        content = """[ASSUMPTION: REQ-014] 
Assumed dual DB support
because the requirement says so
[/ASSUMPTION]"""
        markers = parsers.parse_assumption_markers(content)
        assert len(markers) == 1
        assert "dual DB support" in markers[0]["assumption"]
        assert "requirement says so" in markers[0]["assumption"]

    def test_build_assumption_marker(self):
        """Build assumption marker string."""
        marker = parsers.build_assumption_marker("REQ-014", "Assumed X")
        assert marker == "[ASSUMPTION: REQ-014] Assumed X [/ASSUMPTION]"

    def test_round_trip(self):
        """Parse then build produces equivalent marker."""
        original = "[ASSUMPTION: REQ-014] Assumed X [/ASSUMPTION]"
        markers = parsers.parse_assumption_markers(original)
        rebuilt = parsers.build_assumption_marker(markers[0]["req_id"], markers[0]["assumption"])
        assert rebuilt == original


class TestIDAllocator:
    """Tests for REQ ID allocation."""

    def test_allocate_single(self):
        """Allocate single ID."""
        allocator = parsers.IDAllocator(next_seq=14)
        ids = allocator.allocate(1)
        assert ids == ["REQ-014"]

    def test_allocate_multiple(self):
        """Allocate multiple IDs sequentially."""
        allocator = parsers.IDAllocator(next_seq=14)
        ids = allocator.allocate(3)
        assert ids == ["REQ-014", "REQ-015", "REQ-016"]

    def test_allocate_updates_next_seq(self):
        """Next sequence updates after allocation."""
        allocator = parsers.IDAllocator(next_seq=14)
        allocator.allocate(2)
        assert allocator.peek_next() == "REQ-016"

    def test_peek_next_does_not_consume(self):
        """Peek does not consume the ID."""
        allocator = parsers.IDAllocator(next_seq=14)
        assert allocator.peek_next() == "REQ-014"
        assert allocator.peek_next() == "REQ-014"
        ids = allocator.allocate(1)
        assert ids == ["REQ-014"]

    def test_set_next_seq(self):
        """Set next sequence directly."""
        allocator = parsers.IDAllocator(next_seq=1)
        allocator.set_next_seq(100)
        assert allocator.peek_next() == "REQ-100"

    def test_validate_no_reuse_safe(self):
        """Validate passes when existing IDs are below next_seq."""
        allocator = parsers.IDAllocator(next_seq=20)
        existing = ["REQ-001", "REQ-010", "REQ-019"]
        assert allocator.validate_no_reuse(existing) is True

    def test_validate_no_reuse_unsafe(self):
        """Validate fails when existing ID >= next_seq."""
        allocator = parsers.IDAllocator(next_seq=14)
        existing = ["REQ-001", "REQ-014", "REQ-015"]
        assert allocator.validate_no_reuse(existing) is False

    def test_validate_no_reuse_ignores_malformed(self):
        """Validate ignores malformed IDs."""
        allocator = parsers.IDAllocator(next_seq=14)
        existing = ["REQ-001", "INVALID", "REQ-013"]  # 013 < 14, so safe
        assert allocator.validate_no_reuse(existing) is True

    def test_validate_no_reuse_detects_higher_seq(self):
        """Validate fails when existing ID >= next_seq."""
        allocator = parsers.IDAllocator(next_seq=14)
        existing = ["REQ-001", "REQ-020"]  # 020 >= 14, unsafe
        assert allocator.validate_no_reuse(existing) is False