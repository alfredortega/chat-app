"""
Deterministic impact analysis and job queueing (C13).

Given a validated change event, ``select_affected_artifact_keys`` computes the
exact set of artifacts that must be regenerated using a SQL intersect on
``artifact_traces`` (D3), expanded transitively through ``artifact_deps``.
Artifacts whose requirements overlap are selected at the artifact level (D16).
Jobs are queued with a topological ``depth``; nothing is executed here.
"""

from dataclasses import dataclass, field

from templates import compute_depths


@dataclass
class ImpactResult:
    """Result of deterministic impact analysis."""
    change_id: int
    affected_keys: list[str] = field(default_factory=list)
    depths: dict[str, int] = field(default_factory=dict)
    jobs: list[dict] = field(default_factory=list)
    untraced_requirements: list[str] = field(default_factory=list)
    untraced_artifacts: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "affected_keys": self.affected_keys,
            "depths": self.depths,
            "jobs": self.jobs,
            "untraced_requirements": self.untraced_requirements,
            "untraced_artifacts": self.untraced_artifacts,
            "conflicts": self.conflicts,
        }


def select_affected_artifact_keys(
    project_id: int,
    changed_reqs: list[str],
    removed_reqs: list[str] | None = None,
) -> list[str]:
    """
    Select the exact set of artifacts affected by a requirements change.

    Seeds the set with every artifact tracing to a changed (or removed)
    requirement, then expands to the transitive downstream closure via
    ``artifact_deps``. Returns keys sorted deterministically.
    """
    import database as db_module

    removed_reqs = removed_reqs or []
    all_reqs = list(dict.fromkeys((changed_reqs or []) + list(removed_reqs)))
    if not all_reqs:
        return []

    traces = db_module.list_artifact_traces(project_id)
    seeded_keys = set()
    for trace in traces:
        if trace["req_id"] in all_reqs:
            seeded_keys.add(trace["artifact_key"])

    deps = db_module.list_artifact_deps(project_id)
    adjacency: dict[str, set[str]] = {}
    for dep in deps:
        adjacency.setdefault(dep["upstream_key"], set()).add(dep["downstream_key"])

    # Transitive downstream closure (D4).
    closure = set(seeded_keys)
    stack = list(seeded_keys)
    while stack:
        node = stack.pop()
        for child in adjacency.get(node, []):
            if child not in closure:
                closure.add(child)
                stack.append(child)

    return sorted(closure)


def compute_affected_depths(project_id: int, affected_keys: list[str]) -> dict[str, int]:
    """Compute dependency depth over the full project graph (D4)."""
    import database as db_module

    artifacts = db_module.list_artifacts(project_id)
    all_keys = [a["artifact_key"] for a in artifacts]
    edges = [
        (dep["upstream_key"], dep["downstream_key"])
        for dep in db_module.list_artifact_deps(project_id)
    ]
    depths = compute_depths(all_keys, edges)
    return {key: depths.get(key, 0) for key in affected_keys}


def queue_propagation_jobs(
    project_id: int,
    change_id: int,
    changed_reqs: list[str],
    removed_reqs: list[str] | None = None,
    artefact_keys: list[str] | None = None,
) -> list[dict]:
    """
    Queue propagation jobs for every affected artifact, in dependency order.

    Same-role artifacts for the same change share a batch id so the worker can
    run them sequentially in a single conversation (D16). Jobs are only
    created -- never executed.
    """
    import database as db_module

    removed_reqs = removed_reqs or []
    affected = artefact_keys
    if affected is None:
        affected = select_affected_artifact_keys(project_id, changed_reqs, removed_reqs)

    if not affected:
        return []

    depths = compute_affected_depths(project_id, affected)

    # Group by role so same-role artifacts share a batch.
    artifact_rows = {a["artifact_key"]: a for a in db_module.list_artifacts(project_id)}
    role_by_key: dict[str, str] = {}
    persona_by_key: dict[str, int | None] = {}
    for key in affected:
        artifact = artifact_rows.get(key)
        if artifact is None:
            continue
        persona_by_key[key] = artifact.get("role_persona_id")
        role_by_key[key] = _role_for_artifact(project_id, key, artifacts=artifact_rows) or (
            str(artifact.get("role_persona_id") or key)
        )

    jobs: list[dict] = []
    # Depth ascending, artifact_key ascending for deterministic queue order.
    for depth in sorted(set(depths.values())):
        for key in sorted(depths.keys()):
            if depths[key] != depth:
                continue
            batch_group = role_by_key.get(key, key)
            batch_id = f"{change_id}:{batch_group}"
            job = db_module.create_propagation_job(
                change_id=change_id,
                artifact_key=key,
                persona_id=persona_by_key.get(key),
                depth=depth,
                batch_id=batch_id,
            )
            jobs.append(job)

    return jobs


def _role_for_artifact(project_id: int, artifact_key: str, artifacts: dict[str, dict] | None = None) -> str | None:
    """Resolve an artifact's role from its project template (if any), else persona name."""
    import database as db_module
    from templates import get_template
    project = db_module.get_folder(project_id)
    template_id = project.get("template_id", "") if project else ""
    template = get_template(template_id) if template_id else None
    if template:
        spec = next((a for a in template.artifacts if a.key == artifact_key), None)
        if spec:
            return spec.role
    # Fall back to the persona's name as the role descriptor.
    if artifacts:
        persona_id = artifacts.get(artifact_key, {}).get("role_persona_id")
        if persona_id:
            persona = db_module.db.session.get(db_module.Persona, persona_id)
            if persona:
                return persona.name
    return None


def detect_conflicts(project_id: int) -> list[str]:
    """
    Mark hand-edited downstream artifacts as ``conflict`` (2.4).

    A downstream (non-BA) artifact whose on-disk hash differs from its
    registered hash has been hand-edited after the last propagation; do not
    overwrite it. Returns the list of newly-conflicted keys.
    """
    import database as db_module
    from propagation.scanner import scan_project

    result = scan_project(project_id)
    conflicted = []
    for artifact in result.artifacts:
        if artifact.status != "modified":
            continue
        if artifact.role == "Business Analyst":
            continue
        conf = db_module.update_artifact(
            project_id, artifact.artifact_key, status="conflict"
        )
        if conf:
            conflicted.append(artifact.artifact_key)
    return conflicted


def trace_coverage_report(project_id: int) -> dict:
    """
    Report traceability gaps (1.3 / 2.3).

    Returns:
        {
            "untraced_requirements": [...],  # in BA-REQ but traced by no artifact
            "untraced_artifacts": [...],     # artifacts tracing to no requirement
        }
    """
    import database as db_module
    from parsers import extract_requirement_ids
    from propagation.scanner import read_artifact_file

    project = db_module.get_folder(project_id)
    workspace_dir = project.get("workspace_dir", "") if project else ""

    artifacts = db_module.list_artifacts(project_id)
    traces = db_module.list_artifact_traces(project_id)

    traced_reqs = {t["req_id"] for t in traces}
    traced_artifacts = {t["artifact_key"] for t in traces}

    # Requirements in the BA doc.
    requirement_ids: set[str] = set()
    ba_req = next((a for a in artifacts if a["artifact_key"] == "BA-REQ"), None)
    if ba_req and workspace_dir:
        content, _ = read_artifact_file(workspace_dir, ba_req["rel_path"])
        requirement_ids = set(extract_requirement_ids(content))

    untraced_requirements = sorted(requirement_ids - traced_reqs)
    untraced_artifacts = sorted(
        a["artifact_key"] for a in artifacts if a["artifact_key"] not in traced_artifacts
    )

    return {
        "untraced_requirements": untraced_requirements,
        "untraced_artifacts": untraced_artifacts,
    }


def impact_for_change(project_id: int, change_id: int) -> ImpactResult | None:
    """
    Run the full deterministic impact pipeline for a change event.

    Loads the change event, selects affected artifacts, queues jobs, and
    reports coverage gaps and conflicts.
    """
    import database as db_module

    change_events = db_module.list_change_events(project_id)
    event = next((e for e in change_events if e["id"] == change_id), None)
    if event is None:
        return None

    changed_reqs = event.get("changed_reqs") or []
    removed_reqs = event.get("removed_reqs") or []

    jobs = queue_propagation_jobs(project_id, change_id, changed_reqs, removed_reqs)
    affected_keys = [j["artifact_key"] for j in jobs]
    depths = {j["artifact_key"]: j["depth"] for j in jobs}

    coverage = trace_coverage_report(project_id)
    conflicts = detect_conflicts(project_id)

    result = ImpactResult(
        change_id=change_id,
        affected_keys=affected_keys,
        depths=depths,
        jobs=jobs,
        untraced_requirements=coverage["untraced_requirements"],
        untraced_artifacts=coverage["untraced_artifacts"],
        conflicts=conflicts,
    )
    return result