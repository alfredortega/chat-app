"""
Proposal persistence, apply/reject, and wave rollback (C17).

In ``propose`` mode (the default for new projects), validated rewrites are
written to ``.agents/proposals/CHG-nnnn/`` and jobs transition to
``state='proposed'``. Apply/reject is atomic per change event: a wave is rolled
back as a unit through git (D8). Artifacts are never mutated until the human
approves the diff.
"""

import os
import json

from parsers import parse_front_matter


def change_event_dir(project_id: int, change_id: int) -> str:
    """Return the absolute path of ``.agents/proposals/CHG-nnnn``."""
    import database as db_module
    project = db_module.get_folder(project_id)
    workspace = project.get("workspace_dir", "") if project else ""
    return os.path.join(workspace, ".agents", "proposals", f"CHG-{change_id:04d}")


def write_proposals(project_id: int, change_id: int, payloads: list) -> list[dict]:
    """
    Persist validated rewrites as proposal files and mark jobs ``proposed``.

    ``payloads`` are ``RewritePayload`` objects (C16). Rows are created by the
    caller before invoking this; jobs that already exist for the artifacts get
    their state updated. A ``.base`` file records the artifact's hash at
    proposal time so apply-time 409 stale-propagation checks can compare.
    """
    import database as db_module
    from propagation.scanner import compute_content_hash, read_artifact_file

    proposal_dir = change_event_dir(project_id, change_id)
    os.makedirs(proposal_dir, exist_ok=True)

    workspace = db_module.get_folder(project_id).get("workspace_dir", "") if db_module.get_folder(project_id) else ""
    jobs = {j["artifact_key"]: j for j in db_module.list_propagation_jobs(change_id)}
    written = []
    for payload in payloads:
        if payload.status != "ok":
            continue
        path = os.path.join(proposal_dir, f"{payload.artifact_key}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload.content)

        # Record the base hash of the artifact the proposal was built on.
        artifact = db_module.get_artifact(project_id, payload.artifact_key)
        base_hash = ""
        if artifact and workspace:
            content, _ = read_artifact_file(workspace, artifact["rel_path"])
            base_hash = compute_content_hash(content) if content else ""
        with open(path + ".base", "w", encoding="utf-8") as f:
            f.write(base_hash)

        job = jobs.get(payload.artifact_key)
        if job:
            db_module.update_propagation_job(job["id"], state="proposed")
        written.append({
            "artifact_key": payload.artifact_key,
            "path": path,
            "version": payload.version,
        })
    return written


def list_proposals(project_id: int, change_id: int) -> list[dict]:
    """List proposal files for a change event."""
    proposal_dir = change_event_dir(project_id, change_id)
    proposals = []
    if not os.path.isdir(proposal_dir):
        return proposals
    for name in sorted(os.listdir(proposal_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(proposal_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        proposals.append({
            "artifact_key": name[:-3],
            "path": path,
            "content": content,
        })
    return proposals


def _proposal_file_for(project_id: int, job: dict) -> str | None:
    change_id = job["change_id"]
    proposal_dir = change_event_dir(project_id, change_id)
    path = os.path.join(proposal_dir, f"{job['artifact_key']}.md")
    return path if os.path.isfile(path) else None


def proposal_is_stale(project_id: int, job_id: int) -> bool:
    """
    Return True when the artifact changed under the proposal (409).

    Compares the artifact's current content hash with the hash recorded at
    proposal-write time.
    """
    import database as db_module
    from propagation.scanner import compute_content_hash, read_artifact_file

    job = db_module.update_propagation_job(job_id)
    if not job:
        return False
    proposal_path = _proposal_file_for(project_id, job)
    base_path = proposal_path + ".base" if proposal_path else None
    if not base_path or not os.path.isfile(base_path):
        return False
    with open(base_path, "r", encoding="utf-8") as f:
        base_hash = f.read().strip()

    artifact = db_module.get_artifact(project_id, job["artifact_key"])
    if not artifact:
        return False
    workspace = db_module.get_folder(project_id).get("workspace_dir", "")
    content, _ = read_artifact_file(workspace, artifact["rel_path"])
    current_hash = compute_content_hash(content) if content else ""
    return base_hash != current_hash


def apply_proposal(project_id: int, job_id: int) -> dict | None:
    """
    Apply a single proposal: write the artifact, bump/register the version and
    hash, then mark the job ``applied``.
    """
    import database as db_module
    from propagation.scanner import compute_content_hash

    job = db_module.update_propagation_job(job_id)
    if not job:
        return None
    if job["state"] not in ("proposed",):
        return {"error": f"Job {job_id} is not in 'proposed' state (current: {job['state']})"}

    proposal_path = _proposal_file_for(project_id, job)
    if not proposal_path:
        return {"error": f"No proposal file for job {job_id}"}

    with open(proposal_path, "r", encoding="utf-8") as f:
        content = f.read()

    artifact = db_module.get_artifact(project_id, job["artifact_key"])
    if not artifact:
        return {"error": f"Artifact {job['artifact_key']} is not registered"}

    # Write to the real artifact path.
    workspace = db_module.get_folder(project_id).get("workspace_dir", "")
    full_path = os.path.join(workspace, artifact["rel_path"])
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    try:
        fm, _ = parse_front_matter(content)
    except Exception:
        fm = {}
    version = fm.get("version", artifact.get("version", 1) + 1)

    db_module.update_artifact(
        project_id,
        job["artifact_key"],
        version=version,
        content_hash=compute_content_hash(content),
        status="current",
        origin="propagation",
    )

    db_module.update_propagation_job(job_id, state="applied")
    return db_module.update_propagation_job(job_id)


def reject_proposal(project_id: int, job_id: int) -> dict | None:
    """Reject a proposal: mark the job rejected and remove the proposal file."""
    import database as db_module

    job = db_module.update_propagation_job(job_id)
    if not job:
        return None
    proposal_path = _proposal_file_for(project_id, job)
    if proposal_path and os.path.isfile(proposal_path):
        os.remove(proposal_path)
    db_module.update_propagation_job(job_id, state="rejected")
    return db_module.update_propagation_job(job_id)


def apply_all(project_id: int, change_id: int) -> dict:
    """Apply every proposal in a change event as a unit, then commit the wave."""
    import database as db_module
    from git_integration import create_wave_commit

    jobs = db_module.list_propagation_jobs(change_id)
    applied = []
    for job in jobs:
        if job["state"] != "proposed":
            continue
        result = apply_proposal(project_id, job["id"])
        if result and not result.get("error"):
            applied.append(job["artifact_key"])

    event = next((e for e in db_module.list_change_events(project_id) if e["id"] == change_id), None)
    project = db_module.get_folder(project_id)
    workspace = project.get("workspace_dir", "")
    commit_result = None
    if workspace and applied:
        summary = (event or {}).get("summary") or f"change {change_id}"
        commit_result = create_wave_commit(workspace, summary[:80])

    return {
        "applied": applied,
        "commit": commit_result.output if commit_result else None,
    }


def rollback_wave(project_id: int, change_id: int) -> dict:
    """
    Roll back an applied wave atomically using git (D8).

    Resets the workspace to the commit from before the wave, restoring every
    artifact byte-identically.
    """
    import database as db_module
    from git_integration import run_git_command

    project = db_module.get_folder(project_id)
    workspace = project.get("workspace_dir", "")
    if not workspace:
        return {"ok": False, "error": "No workspace directory"}

    # Find the pre-wave commit: the last commit whose message is NOT a wave.
    result = run_git_command(workspace, "log", "--oneline", "-20")
    prior_commit = None
    for line in result.output.strip().splitlines():
        parts = line.split(" ", 1)
        msg = parts[1] if len(parts) == 2 else ""
        if not msg.startswith("Propagation wave"):
            prior_commit = parts[0]
            break

    if not prior_commit:
        return {"ok": False, "error": "No pre-wave commit found; cannot roll back."}

    reset = run_git_command(workspace, "reset", "--hard", prior_commit)
    return {"ok": reset.success, "error": reset.error or ""}