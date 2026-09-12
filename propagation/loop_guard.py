"""
Loop prevention, token budget and kill switch (C19).

- Propagation-authored writes carry ``origin: propagation`` so the scanner
  never opens a new change chain from them (D6).
- ``max_depth`` caps how deep a single change event may propagate (3.4).
- A token budget (D14) forcibly cancels remaining jobs once exceeded.
- A kill switch stops the worker from claiming/executing further jobs.
"""

from database import db, Setting, list_propagation_jobs, update_propagation_job

MAX_DEPTH = 10

KILL_SWITCH_SETTING = "propagation_kill_switch"


def validate_wave_depth(project_id: int, change_id: int, max_depth: int = MAX_DEPTH) -> list[str]:
    """
    Cancel any job whose depth exceeds ``max_depth`` (3.4).

    Returns the keys of the cancelled jobs.
    """
    cancelled = []
    for job in list_propagation_jobs(change_id):
        if job["depth"] > max_depth:
            update_propagation_job(
                job["id"],
                state="cancelled",
                error=f"Depth {job['depth']} exceeds max_depth {max_depth}",
            )
            cancelled.append(job["artifact_key"])
    return cancelled


def account_tokens(job_id: int, tokens: int) -> int:
    """Accumulate tokens used onto a job and return the new total."""
    job = update_propagation_job(job_id)
    if not job:
        return 0
    total = job.get("tokens_used", 0) + tokens
    update_propagation_job(job_id, tokens_used=total)
    return total


def enforce_token_budget(change_id: int, budget: int, tokens_used: int) -> list[str]:
    """
    Cancel remaining non-terminal jobs once the budget is exceeded (D14).

    ``budget`` of 0 means unlimited. Returns the keys cancelled.
    """
    if not budget:
        return []
    if tokens_used <= budget:
        return []

    cancelled = []
    for job in list_propagation_jobs(change_id):
        if job["state"] not in (
            "applied", "proposed", "failed", "needs_input", "cancelled", "completed",
        ):
            update_propagation_job(
                job["id"],
                state="cancelled",
                error=f"Token budget exceeded: {tokens_used} > {budget}",
            )
            cancelled.append(job["artifact_key"])
    return cancelled


def kill_switch_enabled() -> bool:
    """Return True if the propagation kill switch has been pulled."""
    row = db.session.get(Setting, KILL_SWITCH_SETTING)
    return bool(row and row.value == "1")


def set_kill_switch(on: bool) -> None:
    """Pull (or release) the kill switch."""
    row = db.session.get(Setting, KILL_SWITCH_SETTING)
    value = "1" if on else "0"
    if row:
        row.value = value
    else:
        db.session.add(Setting(key=KILL_SWITCH_SETTING, value=value))
    db.session.commit()


def cancel_remaining_jobs(change_id: int, reason: str) -> list[str]:
    """Cancel every non-terminal job for a change event (kill switch)."""
    cancelled = []
    for job in list_propagation_jobs(change_id):
        if job["state"] not in (
            "applied", "proposed", "failed", "needs_input", "cancelled", "completed",
        ):
            update_propagation_job(job["id"], state="cancelled", error=reason)
            cancelled.append(job["artifact_key"])
    return cancelled


def count_new_change_events(project_id: int, before_count: int) -> int:
    """
    Count how many new change events a propagation run introduced (D6).

    Apply/reject writes the artifact with ``origin: propagation``; the scanner
    must not open a new change chain from them, so this regression helper
    should always return 0 after a wave.
    """
    import database as db_module
    events = db_module.list_change_events(project_id)
    return max(0, len(events) - before_count)


def estimate_wave_tokens(project_id: int, change_id: int) -> int:
    """
    Estimate the tokens a propagation wave will consume (6.4 / C31).

    Deterministic estimate based on the number of affected artifacts, their
    content size (~4 chars/token) and a per-depth prompt overhead.
    """
    import database as db_module
    from propagation.scanner import read_artifact_file

    project = db_module.get_folder(project_id)
    workspace = project.get("workspace_dir", "") if project else ""
    jobs = list_propagation_jobs(change_id)

    total_chars = 0
    for job in jobs:
        artifact = db_module.get_artifact(project_id, job["artifact_key"])
        if artifact and workspace:
            content, _ = read_artifact_file(workspace, artifact["rel_path"])
            total_chars += len(content)

    depths = {job["depth"] for job in jobs}
    # ~4 chars per token for content + a fixed prompt overhead per job.
    estimate = total_chars // 4 + len(jobs) * 200 + len(depths) * 100
    return max(estimate, 1)


def require_auto_confirmation(mode: str, confirm_auto: bool) -> None:
    """
    Enforce that ``auto`` mode requires an explicit confirmation (C31).

    Raises ``ValueError`` when ``mode`` is ``auto`` but ``confirm_auto`` is not
    truthy — the confirmation cannot be bypassed via the API.
    """
    if mode == "auto" and not confirm_auto:
        raise ValueError(
            "Enabling 'auto' mode requires explicit confirmation — it writes "
            "directly to artifacts without review. Set 'confirm_auto': true."
        )