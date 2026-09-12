"""
Change event creation for artifact propagation (C12).

Given a human-modified BA artifact, this module:
- computes/accepts a unified diff against the last committed version,
- deterministically extracts the changed/removed ``REQ-nnn`` IDs (D3),
- validates the document (duplicate IDs, renumbering, deleted requirements),
- asks the LLM for one thing only -- a plain-language change summary,
- inserts (or debounces/coalesces) a ``change_events`` row.

All routing decisions stay deterministic; the LLM is never asked to decide
*what* changed.
"""

import re
from dataclasses import dataclass, field

from parsers import (
    extract_requirement_ids,
    extract_requirements_with_titles,
    validate_requirements,
    detect_renumber,
    RequirementValidationError,
)

REQ_ID_PATTERN = re.compile(r"REQ-\d{3}")
REQ_HEADING_PATTERN = re.compile(r"^##\s+(REQ-\d{3})\s*—\s*.+$", re.MULTILINE)


def _split_requirements(content: str) -> dict[str, str]:
    """
    Split requirements content into ``{req_id: section_text}`` blocks.

    A section runs from its ``## REQ-nnn — Title`` heading up to (but not
    including) the next heading, so body-only changes are detectable
    deterministically (D3).
    """
    headings = list(REQ_HEADING_PATTERN.finditer(content))
    blocks: dict[str, str] = {}
    for idx, match in enumerate(headings):
        req_id = match.group(1)
        start = match.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(content)
        blocks[req_id] = content[start:end].strip()
    return blocks


@dataclass
class RequirementChange:
    """Result of analysing a requirements document before/after a change."""
    changed_reqs: list[str] = field(default_factory=list)
    removed_reqs: list[str] = field(default_factory=list)
    added_reqs: list[str] = field(default_factory=list)
    modified_reqs: list[str] = field(default_factory=list)
    renumbers: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    valid: bool = True

    def summary_dict(self) -> dict:
        return {
            "changed_reqs": sorted(self.changed_reqs),
            "removed_reqs": sorted(self.removed_reqs),
            "added_reqs": sorted(self.added_reqs),
            "modified_reqs": sorted(self.modified_reqs),
            "renumbers": self.renumbers,
        }


def extract_req_ids_from_diff(diff: str) -> list[str]:
    """
    Extract the set of ``REQ-nnn`` IDs referenced on added/removed diff lines.

    Header lines (``+++`` / ``---``) that mention the file name are skipped.
    """
    ids = set()
    if not diff:
        return []
    for line in diff.splitlines():
        if not (line.startswith("+") or line.startswith("-")):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        for match in REQ_ID_PATTERN.findall(line):
            ids.add(match)
    return sorted(ids)


def analyse_requirement_change(old_content: str, new_content: str, diff: str = "") -> RequirementChange:
    """
    Deterministically classify a requirements-document change (D3).

    Compares whole requirement blocks (headings + body) between the old and
    new versions, and unions in any ``REQ-nnn`` IDs referenced on the diff's
    added/removed lines. Returns a ``RequirementChange``. Duplicates and
    renumbering are hard validation failures (1.2); a change event must not be
    created when they occur.
    """
    result = RequirementChange()

    old_blocks = _split_requirements(old_content)
    new_blocks = _split_requirements(new_content)

    old_ids = set(old_blocks.keys())
    new_ids = set(new_blocks.keys())

    result.added_reqs = sorted(new_ids - old_ids)
    result.removed_reqs = sorted(old_ids - new_ids)
    result.modified_reqs = sorted(
        req for req in (old_ids & new_ids) if old_blocks[req] != new_blocks[req]
    )

    # Union in IDs referenced on the diff's changed lines.
    for req_id in extract_req_ids_from_diff(diff):
        if req_id in (old_ids | new_ids) and req_id not in result.modified_reqs:
            result.modified_reqs.append(req_id)

    result.changed_reqs = list(dict.fromkeys(
        result.added_reqs + result.modified_reqs + result.removed_reqs
    ))

    # Duplicate / malformed / gap validation on the new document.
    try:
        validate_requirements(new_content)
    except RequirementValidationError as exc:
        result.errors.append(str(exc))
        result.valid = False

    # Renumber detection -- a hard validation failure (1.2).
    result.renumbers = detect_renumber(old_content, new_content)
    if result.renumbers:
        old_to_new = ", ".join(f"{o}->{n}" for o, n in result.renumbers)
        result.errors.append(
            f"Renumber detected ({old_to_new}): REQ-nnn IDs must be stable and "
            "never renumbered. Refusing to propagate until the BA fixes the document."
        )
        result.valid = False

    return result


def summarize_change(
    source_key: str,
    diff: str,
    changed_reqs: list[str],
    summarizer=None,
) -> str:
    """
    Ask the LLM for one thing only: a one-paragraph plain-language summary.

    ``summarizer`` is an injectable callable ``fn(diff, changed_reqs) -> str``.
    When omitted (and for tests where no network is allowed) the summary is an
    empty string -- routing decisions never depend on it.
    """
    if summarizer is not None:
        try:
            return summarizer(diff, changed_reqs) or ""
        except Exception:
            return ""
    return ""


def get_committed_content(workspace_dir: str, rel_path: str) -> str:
    """
    Fetch the content of ``rel_path`` from the workspace's HEAD commit.

    Returns an empty string if the file has never been committed or git is
    unavailable.
    """
    from git_integration import run_git_command

    result = run_git_command(workspace_dir, "show", f"HEAD:{rel_path}")
    if result.success:
        return result.output
    return ""


def create_change_event_for_source(
    project_id: int,
    source_key: str,
    old_content: str,
    new_content: str,
    diff: str = "",
    origin: str = "human",
    summarizer=None,
    debounce_seconds: int | None = None,
) -> dict | None:
    """
    Validate a requirements change and create (or debounce) a change event.

    Returns the inserted (or reused) ``change_events`` row, or ``None`` when
    there is nothing to propagate (no changed requirement IDs). Raises
    ``RequirementValidationError`` on duplicate IDs or renumbering, in which
    case nothing is queued.
    """
    import database as db_module
    from propagation.scanner import debounce_change_events as _debounce

    analysis = analyse_requirement_change(old_content, new_content, diff)
    if not analysis.valid:
        raise RequirementValidationError("; ".join(analysis.errors))

    if not analysis.changed_reqs:
        return None

    # Debounce: coalesce into the most recent unpropagated event for the source.
    if debounce_seconds is None:
        debounce_seconds = 120
    existing = _debounce(
        project_id, source_key, max_age_seconds=debounce_seconds
    )
    if existing:
        # Coalesce the diff into the existing row.
        merged_diff = existing.get("diff") or ""
        if diff and diff not in merged_diff:
            merged_diff = (merged_diff + "\n" + diff).strip()
            db_module.update_change_event(existing["id"], diff=merged_diff)
        return existing

    summary = summarize_change(source_key, diff, analysis.changed_reqs, summarizer=summarizer)

    from database import create_change_event as _create_event

    current_artifact = db_module.get_artifact(project_id, source_key)
    current_version = current_artifact["version"] if current_artifact else 1

    event = _create_event(
        project_id=project_id,
        source_key=source_key,
        from_version=current_version,
        to_version=current_version + 1,
        summary=summary or "",
        changed_reqs=analysis.changed_reqs,
        removed_reqs=analysis.removed_reqs,
        diff=diff,
        origin=origin,
    )

    # Mark downstream artifacts stale (they must be regenerated).
    from propagation.scanner import _mark_downstream_stale
    _mark_downstream_stale(project_id, source_key)

    return event


def scan_and_create_change_event(project_id: int, source_key: str, summarizer=None) -> dict | None:
    """
    Scan a single modified BA artifact and create a change event for it.

    Loads the committed (``HEAD``) content and compares it with the current
    on-disk content. Raises ``RequirementValidationError`` on invalid
    renumbering / duplicates.
    """
    import os
    import database as db_module
    from propagation.scanner import scan_project, read_artifact_file

    project = db_module.get_folder(project_id)
    if not project:
        return None
    workspace_dir = project.get("workspace_dir", "")
    if not workspace_dir:
        return None

    artifact = db_module.get_artifact(project_id, source_key)
    if not artifact:
        return None

    rel_path = artifact["rel_path"]
    new_content, exists = read_artifact_file(workspace_dir, rel_path)
    if not exists:
        return None

    old_content = get_committed_content(workspace_dir, rel_path)

    from git_integration import git_diff_file
    diff_result = git_diff_file(workspace_dir, rel_path)
    diff = diff_result.output if diff_result.success else ""

    return create_change_event_for_source(
        project_id,
        source_key,
        old_content,
        new_content,
        diff=diff,
        summarizer=summarizer,
    )