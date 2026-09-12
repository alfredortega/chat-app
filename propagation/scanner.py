"""
Artifact scanner for change detection.

Scans project workspace, parses front-matter, compares content hashes,
and classifies artifacts as unchanged/modified/new/deleted.
Indexes inline assumption markers into artifact_assumptions.
"""

import hashlib
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from parsers import parse_front_matter, parse_assumption_markers


@dataclass
class ArtifactScanResult:
    """Result of scanning a single artifact."""
    artifact_key: str
    rel_path: str
    status: str  # unchanged | modified | new | deleted
    content_hash: str
    version: int
    role: str
    front_matter: dict
    assumptions: list[dict]


@dataclass
class ProjectScanResult:
    """Result of scanning an entire project."""
    project_id: int
    artifacts: list[ArtifactScanResult]
    summary: dict  # counts per status


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def read_artifact_file(workspace_dir: str, rel_path: str) -> tuple[str, bool]:
    """
    Read artifact file from disk.

    Returns:
        (content, exists)
    """
    full_path = os.path.join(workspace_dir, rel_path)
    if not os.path.exists(full_path):
        return "", False
    try:
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return content, True
    except Exception:
        return "", False


def parse_artifact_content(content: str) -> tuple[dict, str, list[dict]]:
    """
    Parse artifact content into front-matter, body, and assumptions.

    Returns:
        (front_matter_dict, body_content, assumptions_list)
    """
    try:
        front_matter, body = parse_front_matter(content)
    except Exception:
        front_matter = {}
        body = content

    assumptions = parse_assumption_markers(content)
    return front_matter, body, assumptions


def classify_artifact(
    db_artifact: dict,
    file_content: str,
    file_exists: bool,
) -> tuple[str, str]:
    """
    Classify artifact status by comparing with database record.

    Returns:
        (status, content_hash)
    """
    content_hash = compute_content_hash(file_content) if file_content else ""

    if not file_exists:
        return "deleted", content_hash

    if not db_artifact:
        return "new", content_hash

    db_hash = db_artifact.get("content_hash", "")
    if content_hash != db_hash:
        return "modified", content_hash

    return "unchanged", content_hash


def scan_project(project_id: int) -> ProjectScanResult:
    """
    Scan a project workspace for artifact changes.

    Walks the workspace, parses front-matter, compares content hashes
    against the artifacts index, and indexes assumption markers.

    Args:
        project_id: Folder/project ID.

    Returns:
        ProjectScanResult with all artifact scan results and summary.
    """
    import database as db_module

    # Get project info
    project = db_module.get_folder(project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")

    workspace_dir = project.get("workspace_dir", "")
    if not workspace_dir or not os.path.isdir(workspace_dir):
        return ProjectScanResult(
            project_id=project_id,
            artifacts=[],
            summary={"unchanged": 0, "modified": 0, "new": 0, "deleted": 0},
        )

    # Get registered artifacts from DB
    db_artifacts = db_module.list_artifacts(project_id)
    db_artifacts_by_key = {a["artifact_key"]: a for a in db_artifacts}

    # Get all artifact files from workspace
    artifact_files = _find_artifact_files(workspace_dir)

    # Scan each artifact
    scan_results = []
    for artifact_key, rel_path in artifact_files.items():
        db_artifact = db_artifacts_by_key.get(artifact_key)
        full_path = os.path.join(workspace_dir, rel_path)
        content, file_exists = read_artifact_file(workspace_dir, rel_path)

        status, content_hash = classify_artifact(db_artifact, content, file_exists)

        front_matter = {}
        assumptions = []
        role = ""

        if file_exists and content:
            front_matter, body, assumptions = parse_artifact_content(content)
            role = front_matter.get("role", "")

            # Index assumptions into artifact_assumptions table
            if assumptions:
                _index_assumptions(project_id, artifact_key, assumptions)

        result = ArtifactScanResult(
            artifact_key=artifact_key,
            rel_path=rel_path,
            status=status,
            content_hash=content_hash,
            version=front_matter.get("version", 1) if front_matter else (db_artifact.get("version", 1) if db_artifact else 1),
            role=role,
            front_matter=front_matter,
            assumptions=assumptions,
        )
        scan_results.append(result)

    # Check for deleted artifacts (in DB but not on disk)
    for db_artifact in db_artifacts:
        if db_artifact["artifact_key"] not in artifact_files:
            result = ArtifactScanResult(
                artifact_key=db_artifact["artifact_key"],
                rel_path=db_artifact["rel_path"],
                status="deleted",
                content_hash=db_artifact.get("content_hash", ""),
                version=db_artifact.get("version", 1),
                role="",
                front_matter={},
                assumptions=[],
            )
            scan_results.append(result)

    # Build summary
    summary = {
        "unchanged": sum(1 for r in scan_results if r.status == "unchanged"),
        "modified": sum(1 for r in scan_results if r.status == "modified"),
        "new": sum(1 for r in scan_results if r.status == "new"),
        "deleted": sum(1 for r in scan_results if r.status == "deleted"),
    }

    return ProjectScanResult(
        project_id=project_id,
        artifacts=scan_results,
        summary=summary,
    )


def _find_artifact_files(workspace_dir: str) -> dict[str, str]:
    """
    Find all artifact files in workspace.

    Returns:
        Dict mapping artifact_key -> rel_path
    """
    artifact_files = {}
    artifact_dirs = [
        "Requirements",
        "Design",
        "Data",
        "Test Cases",
        "Security",
        "Project Plan",
    ]

    for artifact_dir in artifact_dirs:
        dir_path = os.path.join(workspace_dir, artifact_dir)
        if not os.path.isdir(dir_path):
            continue

        for root, dirs, files in os.walk(dir_path):
            for file in files:
                if file.endswith('.md'):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workspace_dir).replace("\\", "/")
                    # Extract artifact_key from filename (e.g., BA-REQ.md -> BA-REQ)
                    artifact_key = os.path.splitext(file)[0]
                    artifact_files[artifact_key] = rel_path

    return artifact_files


def _index_assumptions(project_id: int, artifact_key: str, assumptions: list[dict]) -> None:
    """
    Index assumption markers into artifact_assumptions table.

    Creates/updates artifact_assumptions records for each marker.
    """
    import database as db_module

    # Get existing assumptions for this artifact
    existing = db_module.list_artifact_assumptions(project_id, artifact_key=artifact_key)
    existing_by_req = {a["req_id"]: a for a in existing}

    for assumption in assumptions:
        req_id = assumption["req_id"]
        marker_text = assumption["assumption"]

        if req_id in existing_by_req:
            # Update existing
            db_module.update_artifact_assumption(
                existing_by_req[req_id]["id"],
                marker_text=marker_text,
                resolved=0,  # Reset resolved status on re-scan
            )
        else:
            # Create new
            db_module.create_artifact_assumption(
                project_id=project_id,
                artifact_key=artifact_key,
                req_id=req_id,
                marker_text=marker_text,
            )


def get_changed_artifacts(project_id: int) -> list[ArtifactScanResult]:
    """
    Get only artifacts classified as 'modified' or 'new'.

    Used by change detection to identify what needs propagation.
    """
    result = scan_project(project_id)
    return [r for r in result.artifacts if r.status in ("modified", "new")]


def get_conflicted_artifacts(project_id: int) -> list[dict]:
    """
    Get artifacts with 'conflict' status from database.

    These are artifacts that were hand-edited after propagation.
    """
    import database as db_module

    artifacts = db_module.list_artifacts(project_id)
    return [a for a in artifacts if a.get("status") == "conflict"]


def create_change_event_from_scan(
    project_id: int,
    scan_result: ProjectScanResult,
    changed_req_ids: list[str],
    llm_summary: str = "",
) -> dict:
    """
    Create a change_event row from scan results.

    Only creates event for human-modified BA artifacts (origin: human).
    """
    import database as db_module
    import json

    # Find the BA artifact that was modified
    ba_artifact = None
    for artifact in scan_result.artifacts:
        if artifact.status == "modified" and artifact.role == "Business Analyst":
            ba_artifact = artifact
            break

    if not ba_artifact:
        return None

    # Get the artifact from DB for version info
    db_artifact = db_module.get_artifact(project_id, ba_artifact.artifact_key)
    if not db_artifact:
        return None

    # Generate diff using git
    from git_integration import git_diff_file
    project = db_module.get_folder(project_id)
    workspace_dir = project.get("workspace_dir", "") if project else ""
    diff = ""
    if workspace_dir:
        diff_result = git_diff_file(workspace_dir, ba_artifact.rel_path)
        if diff_result.success:
            diff = diff_result.output

    change_event = db_module.create_change_event(
        project_id=project_id,
        source_key=ba_artifact.artifact_key,
        from_version=db_artifact["version"],
        to_version=db_artifact["version"] + 1,
        summary=llm_summary,
        changed_reqs=changed_req_ids,
        removed_reqs=[],  # Would need renumber/deletion detection
        diff=diff,
        origin="human",
    )

    # Update artifact status to 'stale' for downstream artifacts
    # (they will be regenerated)
    _mark_downstream_stale(project_id, ba_artifact.artifact_key)

    return change_event


def _mark_downstream_stale(project_id: int, source_key: str) -> None:
    """Mark all downstream artifacts as stale."""
    import database as db_module

    # Get all downstream artifacts via artifact_deps
    deps = db_module.list_artifact_deps(project_id)
    downstream_keys = set()
    for dep in deps:
        if dep["upstream_key"] == source_key:
            downstream_keys.add(dep["downstream_key"])

    # Also get transitive downstream
    changed = True
    while changed:
        changed = False
        for dep in deps:
            if dep["upstream_key"] in downstream_keys:
                if dep["downstream_key"] not in downstream_keys:
                    downstream_keys.add(dep["downstream_key"])
                    changed = True

    for key in downstream_keys:
        db_module.update_artifact(
            project_id=project_id,
            artifact_key=key,
            status="stale",
        )


def debounce_change_events(project_id: int, source_key: str, max_age_seconds: int = 120) -> dict | None:
    """
    Debounce change events for the same source_key.

    If there's an unpropagated change event for this source_key within
    max_age_seconds, return it instead of creating a new one.
    """
    import database as db_module
    import time

    events = db_module.list_change_events(project_id)
    now = time.time()

    for event in events:
        if (event["source_key"] == source_key and
            event["origin"] == "human"):
            # Check if event is unpropagated: no propagation jobs at all,
            # OR has uncompleted jobs. An event is "fully propagated" only
            # when all its jobs are terminal (applied/proposed/failed/etc.).
            jobs = db_module.list_propagation_jobs(event["id"])
            if jobs:
                terminal = [j for j in jobs if j["state"] in ("applied", "proposed", "failed", "needs_input", "cancelled")]
                uncompleted = len(jobs) - len(terminal)
            else:
                uncompleted = 1  # no jobs yet => not propagated
            if uncompleted:
                # Check age
                from datetime import datetime
                created = datetime.fromisoformat(event["created_at"].replace('Z', '+00:00'))
                age = now - created.timestamp()
                if age < max_age_seconds:
                    return event

    return None