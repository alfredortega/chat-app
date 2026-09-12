"""
Human Q&A routing (C18) -- server side of D19-D22.

The ``ask_question`` tool inserts an ``agent_issues`` row; ``raise_issue`` and
``propose_artifact`` behave analogously. Questions accumulate against a change
event and are batched into **one digest per depth**, posted to the BA
conversation (the single inbox). Answer reuse (D22) injects prior answers for
the requirements in scope under a ``Previously clarified`` heading and is keyed
by ``(project_id, req_id)``. Requirement changes flag prior answers as
``stale_context``.
"""

import re

from parsers import (
    extract_requirement_ids,
    parse_assumption_markers,
)

QUESTION_ID_PATTERN = re.compile(r"Q-(\d{4})")
ASSUMPTION_MARKER_OPEN = "[ASSUMPTION:"
ASSUMPTION_MARKER_CLOSE = "[/ASSUMPTION]"


def normalize_text(text: str) -> str:
    """Normalise question text for exact/semantic matching (D22, v1)."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def find_similar_answered(project_id: int, req_id: str, body: str) -> dict | None:
    """
    Look for an answered question on the same ``req_id`` (D22).

    v1 uses exact/normalised text matching on the issue body or proposed
    answer -- no embeddings.
    """
    import database as db_module

    issues = db_module.list_agent_issues(project_id, kind="question", status="answered")
    n_body = normalize_text(body)
    for issue in issues:
        if issue.get("req_id") != req_id:
            continue
        if issue.get("stale_context"):
            continue
        candidates = [issue.get("answer"), issue.get("proposed_answer"), issue.get("body")]
        for candidate in candidates:
            if candidate and normalize_text(candidate) == n_body:
                return issue
    return None


def ask_question(
    project_id: int,
    raised_by_key: str,
    raised_by_persona_id: int | None,
    change_id: int | None,
    depth: int,
    req_id: str | None,
    body: str,
    blocking: bool = False,
    proposed_answer: str | None = None,
) -> dict:
    """Handle a ``ask_question`` tool call (D20)."""
    import database as db_module

    # Answer reuse (D22): do not re-ask a question that has already been
    # answered for the same requirement.
    if req_id:
        prior = find_similar_answered(project_id, req_id, body)
        if prior:
            return {
                "reused_answer": prior.get("answer") or prior.get("proposed_answer"),
                "issue_id": prior["id"],
                "kind": "question",
            }

    issue = db_module.create_agent_issue(
        project_id=project_id,
        raised_by_key=raised_by_key,
        raised_by_persona_id=raised_by_persona_id,
        change_id=change_id,
        depth=depth,
        kind="question",
        req_id=req_id,
        blocking=1 if blocking else 0,
        body=body,
        proposed_answer=proposed_answer,
    )
    return issue


def raise_issue(
    project_id: int,
    raised_by_key: str,
    raised_by_persona_id: int | None,
    change_id: int | None,
    depth: int,
    body: str,
    kind: str = "risk",
    req_id: str | None = None,
) -> dict:
    """Handle a ``raise_issue`` tool call (D12)."""
    import database as db_module

    return db_module.create_agent_issue(
        project_id=project_id,
        raised_by_key=raised_by_key,
        raised_by_persona_id=raised_by_persona_id,
        change_id=change_id,
        depth=depth,
        kind=kind,
        req_id=req_id,
        blocking=0,
        body=body,
    )


def propose_artifact(
    project_id: int,
    persona_id: int | None,
    artifact_key: str,
    rel_path: str,
    rationale: str | None = None,
) -> dict:
    """Handle a ``propose_artifact`` tool call (D17): records a request."""
    import database as db_module

    existing = db_module.list_artifact_requests(project_id, artifact_key=artifact_key, status="pending")
    if existing:
        return existing[0]
    return db_module.create_artifact_request(
        project_id=project_id,
        persona_id=persona_id,
        artifact_key=artifact_key,
        rel_path=rel_path,
        rationale=rationale,
    )


def build_assumption_marker(req_id: str, question_id: int, text: str, role: str) -> str:
    """Build the blockquote-formatted inline assumption marker (D20 / 5.4)."""
    return (
        f"> {ASSUMPTION_MARKER_OPEN} {req_id}] "
        f"(Q-{question_id:04d}) {text} — {role} {ASSUMPTION_MARKER_CLOSE}"
    )


def record_assumption_marker(
    project_id: int,
    artifact_key: str,
    req_id: str,
    question_id: int,
    marker_text_fragment: str,
) -> dict:
    """Index an inline assumption marker into ``artifact_assumptions``."""
    import database as db_module

    return db_module.create_artifact_assumption(
        project_id=project_id,
        artifact_key=artifact_key,
        req_id=req_id,
        marker_text=f"(Q-{question_id:04d}) {marker_text_fragment}",
    )


def build_prior_answers_context(project_id: int, req_ids: list[str]) -> str:
    """
    Build the ``Previously clarified`` context block for the requirements in
    scope (D22 / 0.6). Stale answers are shown but flagged as possibly outdated.
    """
    import database as db_module

    if not req_ids:
        return ""

    issues = db_module.list_agent_issues(project_id, kind="question", status="answered")
    sections = []
    for req_id in sorted(dict.fromkeys(req_ids)):
        for issue in issues:
            if issue.get("req_id") != req_id:
                continue
            answer = issue.get("answer") or issue.get("proposed_answer") or ""
            stale = " [possibly outdated -- requirement text changed]" if issue.get("stale_context") else ""
            sections.append(f"- {req_id}: {answer}{stale}")

    if not sections:
        return ""
    return "Previously clarified:\n" + "\n".join(sections)


def mark_answers_stale(project_id: int, req_ids: list[str]) -> int:
    """
    Flag prior answers for changed requirements as ``stale_context`` (D22).
    Returns the number of issues flagged.
    """
    import database as db_module

    count = 0
    for req_id in req_ids:
        issues = db_module.list_agent_issues(project_id, kind="question", status="answered")
        for issue in issues:
            if issue.get("req_id") == req_id:
                db_module.update_agent_issue(issue["id"], stale_context=1)
                count += 1
    return count


def _dedup_digest_entries(issues: list[dict]) -> list[dict]:
    """
    Deduplicate within a digest: two roles asking the same thing about one
    ``req_id`` appear once, attributed to both, and one answer resolves both.
    """
    seen: dict[tuple, dict] = {}
    for issue in issues:
        key = (issue.get("req_id"), normalize_text(issue.get("body") or ""))
        if key in seen:
            seen[key]["raised_by_key"] = ", ".join(
                sorted(set(
                    seen[key]["raised_by_key"].split(", ") + [issue["raised_by_key"]]
                ))
            )
        else:
            seen[key] = dict(issue)
    return sorted(seen.values(), key=lambda i: (-(i.get("blocking") or 0), i.get("req_id") or ""))


def all_blocking_in_depth(project_id: int, change_id: int, depth: int) -> bool:
    """Return True when every open question in a depth is blocking (D21)."""
    import database as db_module

    issues = db_module.list_agent_issues(project_id, kind="question")
    in_depth = [
        i for i in issues
        if i.get("change_id") == change_id and i.get("depth") == depth
        and i.get("status") == "open"
    ]
    if not in_depth:
        return False
    return all(i.get("blocking") for i in in_depth)


def post_depth_digest(project_id: int, change_id: int, depth: int) -> dict:
    """
    Post ONE batched digest to the BA conversation covering a completed depth.

    Blocking questions are listed first; duplicates are merged. If the project
    has no designated BA conversation yet, the questions stay queued in the
    panel (nothing is lost) and ``posted`` is False.
    """
    import database as db_module

    project = db_module.get_folder(project_id)
    ba_conversation_id = project.get("ba_conversation_id") if project else None

    issues = db_module.list_agent_issues(project_id, kind="question")
    in_depth = [
        i for i in issues
        if i.get("change_id") == change_id and i.get("depth") == depth
        and i.get("status") == "open"
    ]
    if not in_depth:
        return {"posted": False, "entries": 0}

    entries = _dedup_digest_entries(in_depth)
    blocking = [e for e in entries if e.get("blocking")]
    assumptions = [e for e in entries if not e.get("blocking")]

    if not ba_conversation_id:
        return {
            "posted": False,
            "entries": len(entries),
            "queued": True,  # questions remain queued in the panel
        }

    lines = [f"**Questions from CHG-{change_id:04d} (depth {depth})** — "
             f"{len(blocking)} blocking, {len(assumptions)} assumptions"]
    for entry in blocking:
        lines.append(
            f"**🛑 {entry['raised_by_key']} — {entry.get('req_id') or 'general'}** "
            f"*(blocking)*\n{entry.get('body', '')}\n*Proposed: {entry.get('proposed_answer') or ''}*"
        )
    for entry in assumptions:
        lines.append(
            f"**⚠️ {entry['raised_by_key']} — {entry.get('req_id') or 'general'}** "
            f"*(assumed)*\n{entry.get('body', '')}"
        )
    digest_text = "\n\n".join(lines)

    # Post the digest into the BA conversation as a single message.
    try:
        db_module.add_message(
            conversation_id=ba_conversation_id,
            role="assistant",
            content=digest_text,
        )
    except Exception:
        pass

    # Mark the digest as posted on all grouped issues.
    for entry in entries:
        db_module.update_agent_issue(entry["id"], status="digested")
        db_module.update_agent_issue(entry["id"], digest_message_id=f"CHG-{change_id:04d}:D{depth}")

    return {"posted": True, "entries": len(entries)}