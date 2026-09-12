"""
Pure parser functions for artifact front-matter, REQ-nnn extraction,
assumption markers, and ID allocation.

No DB access - pure functions only.
"""

import re
import yaml
from typing import Optional


# ── Front-matter parser ────────────────────────────────────────────────────────

FRONT_MATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class FrontMatterError(Exception):
    """Raised when front-matter parsing fails."""
    pass


def parse_front_matter(content: str) -> tuple[dict, str]:
    """
    Parse YAML front-matter from markdown content.

    Args:
        content: Full markdown content with optional front-matter.

    Returns:
        (front_matter_dict, remaining_content)

    Raises:
        FrontMatterError: If front-matter is malformed or missing required fields.
    """
    match = FRONT_MATTER_PATTERN.match(content)
    if not match:
        raise FrontMatterError("No front-matter found (expected '---' at start)")

    yaml_text = match.group(1)
    try:
        fm = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"Invalid YAML in front-matter: {exc}")

    if not isinstance(fm, dict):
        raise FrontMatterError("Front-matter must be a YAML mapping")

    # Validate required fields
    required = ["artifact_id", "role", "version"]
    for field in required:
        if field not in fm:
            raise FrontMatterError(f"Missing required front-matter field: {field}")

    # Validate types
    if not isinstance(fm["artifact_id"], str):
        raise FrontMatterError("artifact_id must be a string")
    if not isinstance(fm["role"], str):
        raise FrontMatterError("role must be a string")
    if not isinstance(fm["version"], int):
        raise FrontMatterError("version must be an integer")

    # Validate derives_from if present
    if "derives_from" in fm:
        if not isinstance(fm["derives_from"], list):
            raise FrontMatterError("derives_from must be a list")
        for dep in fm["derives_from"]:
            if not isinstance(dep, dict):
                raise FrontMatterError("Each derives_from entry must be a mapping")
            if "artifact" not in dep:
                raise FrontMatterError("derives_from entry missing 'artifact' key")
            if not isinstance(dep["artifact"], str):
                raise FrontMatterError("derives_from.artifact must be a string")
            if "version" in dep and not isinstance(dep["version"], int):
                raise FrontMatterError("derives_from.version must be an integer")
            if "requirements" in dep:
                if not isinstance(dep["requirements"], list):
                    raise FrontMatterError("derives_from.requirements must be a list")
                for req in dep["requirements"]:
                    if not isinstance(req, str):
                        raise FrontMatterError("derives_from.requirements items must be strings")

    remaining = content[match.end():]
    return fm, remaining


def build_front_matter(
    artifact_id: str,
    role: str,
    version: int,
    origin: str = "propagation",
    derives_from: Optional[list[dict]] = None,
) -> str:
    """
    Build YAML front-matter string for an artifact.

    Args:
        artifact_id: Unique artifact key (e.g., "DB-MODEL")
        role: Persona role (e.g., "Database Developer")
        version: Integer version number
        origin: "human" or "propagation"
        derives_from: List of dependency dicts

    Returns:
        Formatted front-matter string with surrounding '---' delimiters.
    """
    fm = {
        "artifact_id": artifact_id,
        "role": role,
        "version": version,
        "origin": origin,
    }
    if derives_from:
        fm["derives_from"] = derives_from

    yaml_str = yaml.dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_str}\n---\n"


# ── REQ-nnn extractor & validator ──────────────────────────────────────────────

REQ_PATTERN = re.compile(r"^##\s+(REQ-\d{3})\s+—\s+(.+)$", re.MULTILINE)


def extract_requirement_ids(content: str) -> list[str]:
    """
    Extract all REQ-nnn IDs from requirement headings in content.

    Looks for headings matching: `## REQ-014 — Some title`

    Args:
        content: Markdown content to scan.

    Returns:
        List of requirement IDs found (e.g., ["REQ-014", "REQ-015"]).
        Order preserved as they appear in the document.
    """
    matches = REQ_PATTERN.findall(content)
    return [m[0] for m in matches]


def extract_requirements_with_titles(content: str) -> list[tuple[str, str]]:
    """
    Extract requirement IDs with their titles.

    Returns:
        List of (req_id, title) tuples.
    """
    return REQ_PATTERN.findall(content)


class RequirementValidationError(Exception):
    """Raised when requirement validation fails."""
    pass


def validate_requirements(content: str, next_req_seq: int = None) -> dict:
    """
    Validate requirement IDs in a document.

    Checks for:
    - Duplicate IDs
    - Malformed IDs (not matching REQ-nnn pattern)
    - Gaps in sequence (if next_req_seq provided)
    - Deleted requirements (IDs referenced but not found)

    Args:
        content: Markdown content to validate.
        next_req_seq: Expected next sequence number (for gap detection).

    Returns:
        Dict with validation results:
        {
            "ids": list of found IDs,
            "duplicates": list of duplicate IDs,
            "malformed": list of malformed IDs,
            "gaps": list of missing sequence numbers,
            "deleted": list of IDs that were expected but not found,
        }

    Raises:
        RequirementValidationError: If critical validation fails (duplicates).
    """
    ids = extract_requirement_ids(content)
    result = {
        "ids": ids,
        "duplicates": [],
        "malformed": [],
        "gaps": [],
        "deleted": [],
    }

    # Check duplicates
    seen = set()
    for req_id in ids:
        if req_id in seen:
            result["duplicates"].append(req_id)
        seen.add(req_id)

    # Check sequence gaps
    if next_req_seq is not None and next_req_seq > 1:
        expected = set(f"REQ-{i:03d}" for i in range(1, next_req_seq))
        found = set(ids)
        missing = expected - found
        if missing:
            result["gaps"] = sorted(missing, key=lambda x: int(x.split("-")[1]))

    if result["duplicates"]:
        raise RequirementValidationError(f"Duplicate requirement IDs: {result['duplicates']}")

    return result


def detect_renumber(old_content: str, new_content: str, similarity_threshold: float = 0.9) -> list[tuple[str, str]]:
    """
    Detect if requirements were renumbered (deleted + added with similar text).

    Compares requirement texts between old and new content. If an ID disappeared
    but a new ID appeared with very similar text, it's likely a renumber.

    Args:
        old_content: Previous version of the document.
        new_content: New version of the document.
        similarity_threshold: Text similarity ratio (0-1) to consider a renumber.

    Returns:
        List of (old_id, new_id) tuples that appear to be renumbers.
    """
    from difflib import SequenceMatcher

    old_reqs = dict(extract_requirements_with_titles(old_content))
    new_reqs = dict(extract_requirements_with_titles(new_content))

    renumbers = []
    for old_id, old_title in old_reqs.items():
        if old_id in new_reqs:
            continue  # Still exists
        # Find best match in new requirements
        best_match = None
        best_ratio = 0
        for new_id, new_title in new_reqs.items():
            if new_id in old_reqs:
                continue  # Already matched
            ratio = SequenceMatcher(None, old_title, new_title).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = new_id
        if best_match and best_ratio >= similarity_threshold:
            renumbers.append((old_id, best_match))

    return renumbers


# ── Assumption marker parser ───────────────────────────────────────────────────

ASSUMPTION_MARKER_PATTERN = re.compile(
    r"\[ASSUMPTION:\s*REQ-(\d{3})\]\s*(.+?)\s*\[/ASSUMPTION\]",
    re.DOTALL
)


def parse_assumption_markers(content: str) -> list[dict]:
    """
    Parse inline assumption markers from artifact content.

    Marker format: `[ASSUMPTION: REQ-014] Assumed X because Y [/ASSUMPTION]`

    Args:
        content: Markdown content to scan.

    Returns:
        List of dicts: {"req_id": "REQ-014", "assumption": "Assumed X because Y"}
    """
    matches = ASSUMPTION_MARKER_PATTERN.findall(content)
    return [{"req_id": f"REQ-{m[0]}", "assumption": m[1].strip()} for m in matches]


def build_assumption_marker(req_id: str, assumption: str) -> str:
    """
    Build an assumption marker string.

    Args:
        req_id: Requirement ID (e.g., "REQ-014")
        assumption: Assumption text.

    Returns:
        Formatted marker string.
    """
    return f"[ASSUMPTION: {req_id}] {assumption} [/ASSUMPTION]"


# ── ID allocator ───────────────────────────────────────────────────────────────

class IDAllocator:
    """
    Manages REQ-nnn ID allocation.

    Ensures IDs are never reused and tracks the next available sequence number.
    """

    def __init__(self, next_seq: int = 1):
        self.next_seq = max(1, next_seq)

    def allocate(self, count: int = 1) -> list[str]:
        """
        Allocate one or more new requirement IDs.

        Args:
            count: Number of IDs to allocate.

        Returns:
            List of allocated IDs (e.g., ["REQ-014", "REQ-015"]).
        """
        ids = [f"REQ-{i:03d}" for i in range(self.next_seq, self.next_seq + count)]
        self.next_seq += count
        return ids

    def peek_next(self) -> str:
        """Return the next ID that would be allocated without consuming it."""
        return f"REQ-{self.next_seq:03d}"

    def set_next_seq(self, seq: int):
        """Set the next sequence number (for migration/import)."""
        self.next_seq = max(1, seq)

    def validate_no_reuse(self, existing_ids: list[str]) -> bool:
        """
        Check that none of the existing IDs would be reused by future allocations.

        Args:
            existing_ids: List of IDs currently in use.

        Returns:
            True if safe, False if any existing ID >= next_seq.
        """
        for req_id in existing_ids:
            match = re.match(r"REQ-(\d{3})", req_id)
            if match:
                seq = int(match.group(1))
                if seq >= self.next_seq:
                    return False
        return True