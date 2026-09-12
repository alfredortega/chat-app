"""
Project management blueprint (Phase 4).

Read-only routes first (C20): list projects, artifact status board, change
history, proposals, issues, assumptions, and read-only settings. Mutating
routes land in C21 and the SSE job stream in C22.
"""

import os

from flask import Blueprint, request, Response, stream_with_context

import database as db
from routes.helpers import api_error, api_ok

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")


def _sse(data: dict) -> str:
    import json as _json
    return f"data: {_json.dumps(data)}\n\n"


@projects_bp.get("/<int:project_id>/jobs/stream")
def stream_jobs(project_id: int):
    """SSE live job-progress stream (C22)."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    return Response(stream_with_context(stream_job_events(project_id)), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


def stream_job_events(project_id: int):
    """
    Yield SSE ``data:`` lines describing job-progress for a project.

    Emits an initial snapshot, then a ``job_update`` event whenever any job
    changes state, an ``all_done`` event once every job is terminal, and
    periodic heartbeats.
    """
    import time
    last_state: dict[int, dict] = {}
    announced_done = False
    first = True
    while True:
        jobs = []
        for event in db.list_change_events(project_id):
            for job in db.list_propagation_jobs(event["id"]):
                job["change_id"] = event["id"]
                jobs.append(job)

        if first:
            yield _sse({"type": "snapshot", "jobs": jobs})
            first = False

        changed = []
        all_terminal = bool(jobs)
        for job in jobs:
            job_id = job["id"]
            prev = last_state.get(job_id)
            if prev is None or prev.get("state") != job["state"]:
                changed.append(job)
            last_state[job_id] = job
            if job["state"] not in (
                "applied", "proposed", "failed", "needs_input", "cancelled",
                "completed", "rejected",
            ):
                all_terminal = False

        if changed:
            yield _sse({"type": "job_update", "jobs": changed})

        if all_terminal and not announced_done:
            yield _sse({"type": "all_done", "jobs": jobs})
            announced_done = True

        time.sleep(0.2)
        yield _sse({"type": "ping", "ts": int(time.time())})


def _get_project(project_id: int):
    project = db.get_folder(project_id)
    if not project:
        return None
    if project.get("kind") != "project":
        return None
    return project


@projects_bp.get("")
def list_projects():
    """List all managed projects (folders WHERE kind='project')."""
    folders = db.list_folders()
    projects = [f for f in folders if f.get("kind") == "project"]
    return api_ok(projects)


@projects_bp.post("")
def create_project():
    """Create a folder (back-compatible) and, when template_id is provided,
    register it as a managed project."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return api_error("name is required", 400)

    folder = db.create_folder(name)
    template_id = data.get("template_id", "").strip()
    if not template_id:
        # Plain folder creation, back-compatible with the legacy flow.
        return api_ok(folder, 201)

    workspace = data.get("workspace_dir", "")
    if not workspace:
        return api_error("workspace_dir is required when template_id is set", 400)

    try:
        result = db.register_project_from_template(folder["id"], template_id, workspace)
    except Exception as exc:
        return api_error(str(exc), 400)
    return api_ok(result, 201)


@projects_bp.get("/<int:project_id>/artifacts")
def get_artifacts(project_id: int):
    """Status board data for a project."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    artifacts = db.list_artifacts(project_id)

    from propagation.impact import _role_for_artifact
    artifact_rows = {a["artifact_key"]: a for a in artifacts}
    for art in artifacts:
        art["role"] = _role_for_artifact(project_id, art["artifact_key"], artifacts=artifact_rows) or "Unknown role"

    # Group by role with a roll-up status.
    deps = db.list_artifact_deps(project_id)
    return api_ok({
        "project_id": project_id,
        "artifacts": artifacts,
        "deps": deps,
        "counts": {
            "current": sum(1 for a in artifacts if a["status"] == "current"),
            "stale": sum(1 for a in artifacts if a["status"] == "stale"),
            "conflict": sum(1 for a in artifacts if a["status"] == "conflict"),
            "updating": sum(1 for a in artifacts if a["status"] == "updating"),
        },
    })


@projects_bp.get("/<int:project_id>/changes")
def get_changes(project_id: int):
    """Change-event history for a project."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    events = db.list_change_events(project_id)
    for event in events:
        event["jobs"] = db.list_propagation_jobs(event["id"])
    return api_ok(events)


@projects_bp.get("/<int:project_id>/changes/<int:change_id>/proposals")
def get_proposals(project_id: int, change_id: int):
    """Diffs (proposals) awaiting review for a change event."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    event = next((e for e in db.list_change_events(project_id) if e["id"] == change_id), None)
    if event is None:
        return api_error("Change event not found", 404)

    from propagation.proposal import list_proposals as _list
    from propagation.scanner import read_artifact_file
    proposals = _list(project_id, change_id)

    import difflib
    project = db.get_folder(project_id)
    workspace = project.get("workspace_dir", "") if project else ""

    enriched = []
    for proposal in proposals:
        artifact = db.get_artifact(project_id, proposal["artifact_key"])
        old_content = ""
        if artifact and workspace:
            old_content, _ = read_artifact_file(workspace, artifact["rel_path"])
        diff = "".join(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            proposal["content"].splitlines(keepends=True),
            fromfile=f"a/{proposal['artifact_key']}",
            tofile=f"b/{proposal['artifact_key']}",
        ))
        proposal["diff"] = diff
        proposal["old_content"] = old_content
        enriched.append(proposal)

    return api_ok({"change_id": change_id, "proposals": enriched})


@projects_bp.get("/<int:project_id>/issues")
def get_issues(project_id: int):
    """D12/D19 questions & issues, filterable by kind/status/blocking."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    kind = request.args.get("kind")
    status = request.args.get("status")
    blocking = request.args.get("blocking")
    blocking = 1 if blocking == "1" else (0 if blocking == "0" else None)
    issues = db.list_agent_issues(project_id, kind=kind, status=status, blocking=blocking)
    return api_ok(issues)


@projects_bp.get("/<int:project_id>/assumptions")
def get_assumptions(project_id: int):
    """Unresolved inline assumptions (D20), grouped by artifact."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    rows = db.list_artifact_assumptions(project_id)
    grouped = {}
    for row in rows:
        if row["resolved"]:
            continue
        grouped.setdefault(row["artifact_key"], []).append({
            "req_id": row["req_id"],
            "marker_text": row["marker_text"],
        })
    return api_ok({
        "project_id": project_id,
        "unresolved_count": sum(len(v) for v in grouped.values()),
        "by_artifact": grouped,
    })


@projects_bp.get("/<int:project_id>/settings")
def get_settings(project_id: int):
    """Read-only propagation settings & token budget."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    project = db.get_folder(project_id)
    return api_ok({
        "project_id": project_id,
        "propagation_mode": project.get("propagation_mode", "off"),
        "template_id": project.get("template_id", ""),
        "token_budget": project.get("token_budget", 0),
        "next_req_seq": project.get("next_req_seq", 1),
        "ba_conversation_id": project.get("ba_conversation_id"),
    })


# ── Mutating routes (C21) ──────────────────────────────────────────────────────

@projects_bp.post("/<int:project_id>/scan")
def scan_project_route(project_id: int):
    """Trigger change detection (notify-mode scan)."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    from propagation.notify import run_notify_scan
    report = run_notify_scan(project_id)
    return api_ok(report.to_dict())


@projects_bp.post("/<int:project_id>/adopt")
def adopt_project_route(project_id: int):
    """Convert an existing plain folder/workspace into a managed project (C27)."""
    project = db.get_folder(project_id)
    if not project:
        return api_error("Folder not found", 404)
    body = request.get_json(silent=True) or {}
    workspace = body.get("workspace_dir", "") or project.get("workspace_dir", "")
    if not workspace:
        return api_error("workspace_dir is required", 400)

    from propagation.adopt import adopt_workspace
    dry_run = bool(body.get("dry_run"))
    try:
        result = adopt_workspace(
            project_id,
            workspace,
            template_id=body.get("template_id", "sdlc"),
            generate_front_matter=bool(body.get("generate_front_matter")),
            dry_run=dry_run,
        )
    except Exception as exc:
        return api_error(str(exc), 400)
    return api_ok(result)


@projects_bp.post("/<int:project_id>/changes/<int:change_id>/propagate")
def propagate_route(project_id: int, change_id: int):
    """Queue propagation jobs for a change event."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    event = next((e for e in db.list_change_events(project_id) if e["id"] == change_id), None)
    if event is None:
        return api_error("Change event not found", 404)

    from propagation.impact import queue_propagation_jobs
    jobs = queue_propagation_jobs(project_id, change_id,
                                  event.get("changed_reqs") or [],
                                  event.get("removed_reqs") or [])
    return api_ok({"queued": len(jobs), "jobs": jobs})


@projects_bp.post("/<int:project_id>/proposals/<int:job_id>/apply")
def apply_proposal_route(project_id: int, job_id: int):
    """Apply a single proposal (409 when it went stale underneath)."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    from propagation.proposal import apply_proposal, proposal_is_stale

    job = _get_job(project_id, job_id)
    if job is None:
        return api_error("Proposal not found", 404)

    if proposal_is_stale(project_id, job_id):
        return api_error("Proposal is stale: the artifact changed underneath it.", 409)

    result = apply_proposal(project_id, job_id)
    if result and result.get("error"):
        return api_error(result["error"], 409)
    return api_ok(result)


@projects_bp.post("/<int:project_id>/proposals/<int:job_id>/reject")
def reject_proposal_route(project_id: int, job_id: int):
    """Reject a single proposal."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    from propagation.proposal import reject_proposal

    job = _get_job(project_id, job_id)
    if job is None:
        return api_error("Proposal not found", 404)
    result = reject_proposal(project_id, job_id)
    return api_ok(result)


@projects_bp.post("/<int:project_id>/changes/<int:change_id>/apply-all")
def apply_all_route(project_id: int, change_id: int):
    """Apply the whole wave."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    from propagation.proposal import apply_all
    result = apply_all(project_id, change_id)
    return api_ok(result)


@projects_bp.post("/<int:project_id>/changes/<int:change_id>/rollback")
def rollback_route(project_id: int, change_id: int):
    """Revert the wave atomically via git."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    from propagation.proposal import rollback_wave
    result = rollback_wave(project_id, change_id)
    if not result.get("ok"):
        return api_error(result.get("error", "rollback failed"), 409)
    return api_ok(result)


@projects_bp.post("/<int:project_id>/issues/<int:issue_id>/answer")
def answer_issue_route(project_id: int, issue_id: int):
    """Answer a question; optionally route into BA-REQ (D19)."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    body = request.get_json(silent=True) or {}
    answer = body.get("answer", "").strip()
    if not answer:
        return api_error("answer is required", 400)

    issue = _get_issue(project_id, issue_id)
    if issue is None:
        return api_error("Issue not found", 404)

    db.update_agent_issue(issue_id, status="answered", answer=answer)
    return api_ok(db.update_agent_issue(issue_id))


@projects_bp.post("/<int:project_id>/issues/<int:issue_id>/dismiss")
def dismiss_issue_route(project_id: int, issue_id: int):
    """"Not a requirement issue" -- record the answer on this job only."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    issue = _get_issue(project_id, issue_id)
    if issue is None:
        return api_error("Issue not found", 404)
    body = request.get_json(silent=True) or {}
    db.update_agent_issue(issue_id, status="dismissed", answer=body.get("note", ""))
    return api_ok(db.update_agent_issue(issue_id))


@projects_bp.get("/<int:project_id>/artifact-requests")
def list_artifact_requests_route(project_id: int):
    """List D17 extension requests."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    rows = db.list_artifact_requests(project_id)
    return api_ok(rows)


@projects_bp.post("/<int:project_id>/artifact-requests")
def create_artifact_request_route(project_id: int):
    """Record a new artifact extension request."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    body = request.get_json(silent=True) or {}
    artifact_key = body.get("artifact_key", "").strip()
    rel_path = body.get("rel_path", "").strip()
    if not artifact_key or not rel_path:
        return api_error("artifact_key and rel_path are required", 400)
    from propagation.qa import propose_artifact
    req = propose_artifact(project_id, persona_id=None, artifact_key=artifact_key,
                           rel_path=rel_path, rationale=body.get("rationale"))
    return api_ok(req, 201)


@projects_bp.post("/<int:project_id>/artifact-requests/<int:request_id>/approve")
def approve_artifact_request_route(project_id: int, request_id: int):
    """Register the artifact and raise the role cap if needed (D18)."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    request = _get_artifact_request(project_id, request_id)
    if request is None:
        return api_error("Request not found", 404)

    # Register the artifact (idempotent) and approve the request.
    if not db.is_artifact_registered(project_id, request["artifact_key"]):
        db.create_artifact(
            project_id=project_id,
            artifact_key=request["artifact_key"],
            role_persona_id=request.get("persona_id"),
            rel_path=request["rel_path"],
            status="current",
            origin="human",
        )
    db.update_artifact_request(request_id, "approved")
    return api_ok(db.update_artifact_request(request_id))


@projects_bp.post("/<int:project_id>/artifact-requests/<int:request_id>/reject")
def reject_artifact_request_route(project_id: int, request_id: int):
    """Record the rejection so the role does not re-propose it every wave."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    request = _get_artifact_request(project_id, request_id)
    if request is None:
        return api_error("Request not found", 404)
    db.update_artifact_request(request_id, "rejected")
    return api_ok(db.update_artifact_request(request_id))


@projects_bp.put("/<int:project_id>/ba-conversation")
def set_ba_conversation_route(project_id: int):
    """Designate the Q&A inbox conversation (D19)."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    body = request.get_json(silent=True) or {}
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        return api_error("conversation_id is required", 400)
    db.update_folder_project(project_id, ba_conversation_id=int(conversation_id))
    return api_ok(db.get_folder(project_id))


@projects_bp.put("/<int:project_id>/settings")
def update_settings_route(project_id: int):
    """Update propagation mode, model per role, token budget."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    body = request.get_json(silent=True) or {}
    updates = {}
    if "propagation_mode" in body:
        mode = body["propagation_mode"]
        if mode not in ("off", "notify", "propose", "auto"):
            return api_error(f"Invalid propagation_mode: {mode}", 400)
        # auto mode requires explicit, non-bypassable confirmation (C31).
        if mode == "auto":
            from propagation.loop_guard import require_auto_confirmation
            try:
                require_auto_confirmation(mode, bool(body.get("confirm_auto")))
            except ValueError as exc:
                return api_error(str(exc), 400)
        updates["propagation_mode"] = mode
    if "token_budget" in body:
        updates["token_budget"] = int(body["token_budget"])
    if "next_req_seq" in body:
        updates["next_req_seq"] = int(body["next_req_seq"])
    db.update_folder_project(project_id, **updates)
    return api_ok(db.get_folder(project_id))


@projects_bp.get("/<int:project_id>/changes/<int:change_id>/estimate-tokens")
def estimate_tokens_route(project_id: int, change_id: int):
    """Pre-wave token estimate (6.4 / C31) — shown before a wave runs."""
    if not _get_project(project_id):
        return api_error("Project not found", 404)
    from propagation.loop_guard import estimate_wave_tokens
    estimate = estimate_wave_tokens(project_id, change_id)
    project = db.get_folder(project_id)
    budget = project.get("token_budget", 0) if project else 0
    return api_ok({
        "change_id": change_id,
        "estimated_tokens": estimate,
        "token_budget": budget,
        "under_budget": budget == 0 or estimate <= budget,
    })


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_job(project_id: int, job_id: int):
    jobs = []
    for event in db.list_change_events(project_id):
        jobs.extend(db.list_propagation_jobs(event["id"]))
    return next((j for j in jobs if j["id"] == job_id), None)


def _get_issue(project_id: int, issue_id: int):
    return next(
        (i for i in db.list_agent_issues(project_id) if i["id"] == issue_id),
        None,
    )


def _get_artifact_request(project_id: int, request_id: int):
    return next(
        (r for r in db.list_artifact_requests(project_id) if r["id"] == request_id),
        None,
    )