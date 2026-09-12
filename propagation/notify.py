"""
``notify`` mode end-to-end detection (C14).

This is the first genuinely useful shipping point: scan the workspace, create
change events for human-authored BA edits, run deterministic impact analysis,
mark affected artifacts stale, and report traceability gaps -- with **zero**
LLM spend (no summarizer is invoked on this path).
"""

from dataclasses import dataclass, field


@dataclass
class NotifyReport:
    """Result of a ``notify``-mode detection pass for a project."""
    project_id: int
    change_events: list[dict] = field(default_factory=list)
    stale_artifacts: list[str] = field(default_factory=list)
    affected_known_requirements: list[str] = field(default_factory=list)
    untraced_requirements: list[str] = field(default_factory=list)
    untraced_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "change_events": self.change_events,
            "stale_artifacts": self.stale_artifacts,
            "affected_known_requirements": self.affected_known_requirements,
            "untraced_requirements": self.untraced_requirements,
            "untraced_artifacts": self.untraced_artifacts,
        }


def run_notify_scan(project_id: int) -> NotifyReport:
    """
    Run a full detection pass in ``notify`` mode.

    Steps:
      1. Scan the workspace for modified/new human-authored BA artifacts.
      2. Create a change event per modified BA artifact (whitespace-only edits
         produce none).
      3. Run deterministic impact analysis per event.
      4. Mark every affected downstream artifact ``stale``.
      5. Produce a trace-coverage report.

    No LLM call is made on this path (C14 gate).
    """
    import database as db_module
    from propagation.scanner import scan_project
    from propagation.changes import scan_and_create_change_event
    from propagation.impact import (
        select_affected_artifact_keys,
        trace_coverage_report,
    )

    report = NotifyReport(project_id=project_id)

    project = db_module.get_folder(project_id)
    if not project:
        return report

    workspace_dir = project.get("workspace_dir", "")
    if not workspace_dir:
        return report

    scan = scan_project(project_id)

    # Find human-authored BA artifacts the scanner reports as modified/new.
    ba_artifacts = [
        r for r in scan.artifacts
        if r.role == "Business Analyst" and r.status in ("modified", "new")
    ]

    affected_union: set[str] = set()
    for artifact in ba_artifacts:
        try:
            event = scan_and_create_change_event(project_id, artifact.artifact_key)
        except Exception:
            # Validation failures (renumber/duplicate) must not crash the pass.
            event = None
        if event:
            report.change_events.append(event)
            changed_reqs = event.get("changed_reqs") or []
            removed_reqs = event.get("removed_reqs") or []
            affected_union.update(
                select_affected_artifact_keys(project_id, changed_reqs, removed_reqs)
            )
            report.affected_known_requirements.extend(changed_reqs + removed_reqs)

    # Mark affected downstream artifacts stale (nothing is regenerated in notify).
    for key in sorted(affected_union):
        db_module.update_artifact(project_id, key, status="stale")
        report.stale_artifacts.append(key)

    coverage = trace_coverage_report(project_id)
    report.untraced_requirements = coverage["untraced_requirements"]
    report.untraced_artifacts = coverage["untraced_artifacts"]

    return report


def notify_status(project_id: int) -> dict:
    """
    Lightweight status snapshot used by the UI panel (read-only).

    Returns counts of stale artifacts, open pre-change events, trace gaps, and
    conflicts without triggering any LLM work.
    """
    import database as db_module
    from propagation.impact import trace_coverage_report

    artifacts = db_module.list_artifacts(project_id)
    stale = [a for a in artifacts if a.get("status") == "stale"]
    conflicted = [a for a in artifacts if a.get("status") == "conflict"]
    changes = db_module.list_change_events(project_id)
    coverage = trace_coverage_report(project_id)

    return {
        "project_id": project_id,
        "stale_count": len(stale),
        "conflict_count": len(conflicted),
        "change_event_count": len(changes),
        "untraced_requirements": coverage["untraced_requirements"],
        "untraced_artifacts": coverage["untraced_artifacts"],
    }