"""
Propagation agent invocation (C16).

Each propagation job runs as a real model conversation (audit trail lives in
the normal chat tables). The agent produces an artifact **update** (D15) through
the ``write_artifact`` tool — most cheaply as a unified diff, with full-content
fallback for tiny files. The update is applied in memory, validated by the
server-side guards below, and kept as a wave overlay — nothing is written to
disk and no proposals are persisted in this commit (deferred to C17).

Same-role batching (D16): artifacts owned by one role for one change event run
sequentially inside a single conversation so artifact 2 sees artifact 1's
updated text through the overlay.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable

from parsers import parse_front_matter, build_front_matter, parse_assumption_markers, extract_requirement_ids

MIN_SHRINK_RATIO = 0.6
HEADING_PATTERN = re.compile(r"^##\s+.+$", re.MULTILINE)

# Prompt-input caps (Phase 5). Whole-artifact rewrites still get the full
# current artifact, but each upstream artifact and the change diff are bounded
# so one oversized document cannot blow a job's context window.
UPSTREAM_ARTIFACT_CAP = 24_000
CURRENT_ARTIFACT_CAP = 60_000
DIFF_CAP = 12_000
PERSONA_CAP = 4_000

# Cap generated output on every propagation model call. Agents now return
# compact unified diffs, so a few thousand tokens is plenty and a runaway that
# used to emit 100k+ tokens is bounded. Override with PROPAGATION_MAX_OUT_TOKENS.
MAX_OUTPUT_TOKENS = int(os.getenv("PROPAGATION_MAX_OUT_TOKENS", "6000"))


# ── Rewrite guards (pure, server-side) ─────────────────────────────────────────

@dataclass
class RewriteAssessment:
    """Result of validating a whole-artifact rewrite (D15)."""
    ok: bool = True
    rejected: bool = False
    needs_review: bool = False
    errors: list[str] = field(default_factory=list)
    content: str = ""


def _headings(content: str) -> set[str]:
    return {h.strip() for h in HEADING_PATTERN.findall(content)}


def assess_rewrite(
    original: str,
    rewrite: str,
    removed_reqs: list[str] | None = None,
    resolved_req_ids: set[str] | None = None,
) -> RewriteAssessment:
    """
    Apply the whole-artifact rewrite guards (D15).

    - **Shrink guard:** reject a rewrite below 60% of the original length
      unless the change removed a requirement -- the classic failure being a
      summary instead of the document.
    - **Heading retention:** every ``##`` heading present before must still be
      present, else the job needs review.
    - **Assumption-marker retention:** unresolved markers must survive.
    - **Requirement-reference check:** IDs the artifact traced to must still
      appear unless the change removed them.
    """
    removed_reqs = removed_reqs or []
    resolved = resolved_req_ids or set()
    assessment = RewriteAssessment(content=rewrite)

    original_body = _strip_front_matter(original)
    rewrite_body = _strip_front_matter(rewrite)

    # Shrink guard.
    if original_body and not removed_reqs:
        if len(rewrite_body) < MIN_SHRINK_RATIO * len(original_body):
            assessment.ok = False
            assessment.rejected = True
            assessment.errors.append(
                f"Shrink guard: rewrite is {len(rewrite_body)} chars vs original "
                f"{len(original_body)} ({len(rewrite_body)/max(1, len(original_body)):.0%}). "
                "This looks like a summary, not a whole-artifact rewrite."
            )

    # Heading retention.
    missing = _headings(original) - _headings(rewrite)
    if missing:
        assessment.needs_review = True
        assessment.ok = False
        assessment.errors.append(f"Heading retention: missing headings {sorted(missing)}")

    # Assumption-marker retention.
    original_markers = {m["req_id"] for m in parse_assumption_markers(original)} - resolved
    new_markers = {m["req_id"] for m in parse_assumption_markers(rewrite)}
    dropped_markers = original_markers - new_markers
    if dropped_markers:
        assessment.needs_review = True
        assessment.ok = False
        assessment.errors.append(f"Assumption markers dropped: {sorted(dropped_markers)}")

    # Requirement-reference check.
    original_reqs = set(extract_requirement_ids(original))
    new_reqs = set(extract_requirement_ids(rewrite))
    removed_set = set(removed_reqs)
    missing_reqs = original_reqs - new_reqs - removed_set
    if missing_reqs:
        assessment.needs_review = True
        assessment.ok = False
        assessment.errors.append(f"Requirement references missing: {sorted(missing_reqs)}")

    return assessment


FRONT_MATTER_BLOCK = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)


def _strip_front_matter(content: str) -> str:
    try:
        _, body = parse_front_matter(content)
        return body
    except Exception:
        # Even if the YAML is malformed/partial, discard a leading '---' block
        # so the model's front-matter never survives into the body (D15).
        match = FRONT_MATTER_BLOCK.match(content)
        if match:
            return content[match.end():]
        return content


def normalize_rewrite(
    artifact_key: str,
    role: str,
    old_content: str,
    model_content: str,
    old_version: int,
    derives_from: list[dict] | None,
) -> str:
    """
    Server-author the front-matter for a rewrite (D15).

    ``version``, ``origin`` and ``derives_from`` are computed; the model's
    values are discarded.
    """
    body = _strip_front_matter(model_content)
    derived = derives_from or []
    if old_version is None:
        old_version = 0
    fm = build_front_matter(
        artifact_id=artifact_key,
        role=role,
        version=old_version + 1,
        origin="propagation",
        derives_from=derived,
    )
    return fm + "\n" + body.lstrip("\n")


# ── In-memory wave overlay ─────────────────────────────────────────────────────

class WaveOverlay:
    """
    In-memory snapshot of artifact contents being regenerated in a wave.

    Downstream prompts read from the overlay so a rewritten upstream artifact
    is immediately visible to downstream artifacts (D4), even before any
    proposal is persisted.
    """

    def __init__(self, base_contents: dict[str, str]):
        self._contents = dict(base_contents)
        # Caches the smart-previewed form of each artifact so the same huge
        # upstream (e.g. BA-REQ) is only downsampled once per wave instead of
        # once per job. Invalidated on set().
        self._preview_cache: dict[str, tuple[str, str]] = {}

    def get(self, artifact_key: str, default: str = "") -> str:
        return self._contents.get(artifact_key, default)

    def set(self, artifact_key: str, content: str) -> None:
        self._contents[artifact_key] = content
        self._preview_cache.pop(artifact_key, None)

    def preview(self, artifact_key: str, max_chars: int) -> tuple[str, int]:
        """
        Return ``(smart_preview, omitted_chars)`` for an artifact, memoized for
        the lifetime of the wave (invalidated whenever the artifact is set).
        """
        from file_handler import _smart_preview

        cached = self._preview_cache.get(artifact_key)
        if cached is not None and cached[0] == str(max_chars):
            return cached[1]
        content = self._contents.get(artifact_key, "")
        preview = _smart_preview(content, max_chars)
        self._preview_cache[artifact_key] = (str(max_chars), preview)
        return preview

    def keys(self):
        return list(self._contents.keys())


# ── Prompt assembly ───────────────────────────────────────────────────────────

def build_agent_prompt(
    persona_prompt: str,
    context: str,
    change_event: dict,
    current_artifact: str,
) -> list[dict]:
    """Assemble the API message list for a propagation job.

    All inputs are bounded (Phase 5): oversize upstream context, diffs and
    artifacts are downsampled to a structural preview instead of blocking or
    blowing the model window.
    """
    from file_handler import _smart_preview

    persona_prompt = _smart_preview(persona_prompt, PERSONA_CAP)[0]
    context = _smart_preview(context, UPSTREAM_ARTIFACT_CAP * 3)[0]
    current_artifact = _smart_preview(current_artifact, CURRENT_ARTIFACT_CAP)[0]

    change_summary = change_event.get("summary") or ""
    diff = change_event.get("diff") or ""
    diff = _smart_preview(diff, DIFF_CAP)[0]
    changed_reqs = change_event.get("changed_reqs") or []

    change_section = "CHANGE EVENT\n"
    if change_summary:
        change_section += f"Summary: {change_summary}\n"
    if changed_reqs:
        change_section += f"Requirements in scope: {', '.join(changed_reqs)}\n"
    if diff:
        change_section += f"Diff:\n{diff}"

    instructions = (
        "You are updating ONE artifact as part of a coordinated change. "
        "Emit the changes as a unified diff and pass it to the write_artifact "
        "tool in the 'diff' parameter. The server will apply the diff to the "
        "current artifact. Only include what actually changed from the CURRENT "
        "ARTIFACT; do not regenerate unchanged sections, do not summarise, and "
        "do not pass the whole file as 'content' unless the artifact is tiny. "
        "Use standard hunks (\"@@ -l,c +l,c @@\" with context, removed and added "
        "lines). The server will set front-matter (version, origin, derives_from) - "
        "do not invent your own version numbers."
    )

    user_content = (
        f"{context}\n\n---\n\n"
        f"The article below is the CURRENT content of the artifact you are updating:\n\n"
        f"<CURRENT_ARTIFACT>\n{current_artifact}\n</CURRENT_ARTIFACT>\n\n"
        f"---\n\n{instructions}"
    )

    return [
        {"role": "system", "content": persona_prompt},
        {"role": "user", "content": user_content},
    ]


# ── Wave runner ────────────────────────────────────────────────────────────────

RewardHandler = Callable[[str, str], dict]


@dataclass
class RewritePayload:
    """A validated, in-memory rewrite for one artifact (no persistence)."""
    artifact_key: str
    content: str
    version: int
    status: str  # 'ok' | 'rejected' | 'needs_review'
    errors: list[str] = field(default_factory=list)
    prompt: str = ""


def run_propagation_wave(
    app,
    project_id: int,
    change_id: int,
    client,
    model_id: str = "fake-model",
    max_iterations: int = 25,
) -> list[RewritePayload]:
    """
    Run every queued job for a change event using ``client`` (a fake or real
    model client) and return validated in-memory rewrite payloads.

    Nothing is written to disk and no proposals are persisted. Same-role
    artifacts for one change share a single conversation (D16).
    """
    import database as db_module
    from chat_service import run_chat_turn
    from tools import build_tools, _is_tool_allowed_for_context

    with app.app_context():
        project = db_module.get_folder(project_id)
        workspace_dir = project.get("workspace_dir", "")

        # Load every registered artifact's current content into the overlay.
        artifacts = db_module.list_artifacts(project_id)
        base_contents: dict[str, str] = {}
        personas: dict[int, str] = {}
        for persona in db_module.list_personas():
            personas[persona["id"]] = persona["prompt"]

        overlay = WaveOverlay(base_contents)
        artifact_meta: dict[str, dict] = {}
        for art in artifacts:
            artifact_meta[art["artifact_key"]] = art
            from propagation.scanner import read_artifact_file
            content, _ = read_artifact_file(workspace_dir, art["rel_path"])
            # Normalise to a single trailing newline: difflib/git unidiffs are
            # only clean when the source is newline-terminated (files without a
            # trailing newline produce fused -/+ lines that cannot be applied).
            if content and not content.endswith("\n"):
                content += "\n"
            overlay.set(art["artifact_key"], content)

        event = next(
            (e for e in db_module.list_change_events(project_id) if e["id"] == change_id),
            None,
        )
        if event is None:
            return []

        changed_reqs = event.get("changed_reqs") or []
        removed_reqs = event.get("removed_reqs") or []

        jobs = db_module.list_propagation_jobs(change_id)

        # Load the dependency graph ONCE per wave — every job's upstream prompt
        # reads from this same snapshot (D13). Re-querying per job was wasted DB
        # work on projects with many artifacts.
        deps = db_module.list_artifact_deps(project_id)

        from propagation.impact import compute_affected_depths
        affected = [j["artifact_key"] for j in jobs]
        depths = compute_affected_depths(project_id, affected)

        # Order jobs: depth ascending, artifact_key ascending.
        jobs_sorted = sorted(jobs, key=lambda j: (depths.get(j["artifact_key"], 0), j["artifact_key"]))

        payloads: list[RewritePayload] = []
        for job in jobs_sorted:
            artifact_key = job["artifact_key"]
            # Surfaces per-agent progress: mark THIS job as the one currently
            # being worked and push previously finished jobs to a terminal
            # state so the UI can show "who is assessing changes now".
            db_module.update_propagation_job(job["id"], state="running")
            meta = artifact_meta.get(artifact_key, {})
            role = meta.get("role", "") or _role_from_template(project_id, artifact_key)
            persona_id = meta.get("role_persona_id")
            persona_prompt = personas.get(persona_id, "You are an expert document author.")

            current = overlay.get(artifact_key)

            # Scoped upstream context from the overlay (only declared upstreams).
            context = _build_upstream_context(project_id, artifact_key, overlay, deps=deps)

            messages = build_agent_prompt(
                persona_prompt,
                context,
                event,
                current,
            )

            tools = build_tools("propagation")
            old_version = meta.get("version", 1)
            derives_from = meta.get("derives_from") or []

            rewrite_diff = {"diff": None}

            def make_tool_handler() -> Callable[[str, str], dict]:
                def handler(name: str, args_json: str, output_dir: str = ""):
                    nonlocal rewrite_diff
                    if name == "write_artifact":
                        try:
                            args = json.loads(args_json)
                        except Exception:
                            return {"success": False, "display": "bad json", "result": ""}
                        target_key = args.get("artifact_key", "")
                        # Propagation may never create artifacts (3.2): an
                        # unregistered key is an error, not an implicit create.
                        if not db_module.is_artifact_registered(project_id, target_key):
                            return {
                                "success": False,
                                "display": f"Unregistered artifact key: {target_key}",
                                "result": f"Artifact key '{target_key}' is not registered.",
                            }
                        # Prefer the cheap diff form; fall back to full content
                        # for tiny artifacts or older model behaviour.
                        if "diff" in args and args.get("diff"):
                            rewrite_diff["diff"] = args["diff"]
                        elif "content" in args:
                            rewrite_diff["content"] = args["content"]
                        else:
                            return {
                                "success": False,
                                "display": "No diff or content in write_artifact.",
                                "result": "Provide either a unified diff or the full content.",
                            }
                        return {
                            "success": True,
                            "display": "Captured rewrite in memory.",
                            "result": "Artifact rewrite captured.",
                        }
                    if name == "read_artifact":
                        try:
                            args = json.loads(args_json)
                        except Exception:
                            return {"success": False, "display": "bad json", "result": ""}
                        return {
                            "success": True,
                            "display": "ok",
                            "result": overlay.get(args.get("artifact_key", "")),
                        }
                    return {
                        "success": False,
                        "display": f"Tool '{name}' not handled in memory (C18).",
                        "result": "",
                    }
                return handler

            handler = make_tool_handler()
            events = run_chat_turn(
                client=client,
                model_id=model_id,
                messages=messages,
                tools=tools,
                max_iterations=max_iterations,
                execute_tool_fn=handler,
                output_dir=workspace_dir,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            for ev in events:
                if ev.get("type") == "error":
                    payloads.append(RewritePayload(
                        artifact_key=artifact_key,
                        content="",
                        version=old_version,
                        status="rejected",
                        errors=[ev.get("message", "")],
                        prompt=json.dumps(messages, default=str),
                    ))
                    break

            # Resolve the rewritten full content: either the model handed us
            # the whole file ('content') or, much more cheaply, a unified diff
            # that we apply against the artifact's current overlay content.
            from propagation.diff import apply_unified_diff

            model_content = None
            diff_error = None
            if "content" in rewrite_diff:
                model_content = rewrite_diff["content"]
            elif rewrite_diff.get("diff"):
                model_content = apply_unified_diff(current, rewrite_diff["diff"])
                if model_content is None:
                    diff_error = "The model returned a diff that could not be applied to the current artifact."
                    model_content = None

            if model_content is None:
                # No usable rewrite was captured (missing, or an unappliable
                # diff). Record a rejected payload so the assertion works.
                payloads.append(RewritePayload(
                    artifact_key=artifact_key,
                    content="",
                    version=old_version,
                    status="rejected",
                    errors=[diff_error or "No write_artifact call returned by the agent."],
                    prompt=json.dumps(messages, default=str),
                ))
                continue

            # Validate the rewrite. If it passes, apply the guards + overlay.
            normalized = normalize_rewrite(
                artifact_key, role, current, model_content, old_version, derives_from
            )
            assessment = assess_rewrite(current, normalized, removed_reqs=removed_reqs)

            status = "ok"
            if assessment.rejected:
                status = "rejected"
            elif assessment.needs_review:
                status = "needs_review"

            payload = RewritePayload(
                artifact_key=artifact_key,
                content=assessment.content if assessment.ok else "",
                version=old_version + 1,
                status=status,
                errors=assessment.errors,
                prompt=json.dumps(messages, default=str),
            )
            payloads.append(payload)

            if assessment.ok:
                # This artifact is done: flip the job to a terminal state so a
                # polling UI sees the wave advancing ("which agent is assessing
                # changes now" = the first non-terminal job). write_proposals
                # upgrades ok jobs to 'proposed' after the loop.
                db_module.update_propagation_job(job["id"], state="completed")
                # Publish into the wave overlay for downstream prompts (D4).
                try:
                    fm, _ = parse_front_matter(normalized)
                except Exception:
                    fm = {}
                overlay.set(artifact_key, normalized)

        return payloads


def _role_from_template(project_id: int, artifact_key: str) -> str:
    import database as db_module
    from templates import get_template
    project = db_module.get_folder(project_id)
    template_id = project.get("template_id", "") if project else ""
    template = get_template(template_id) if template_id else None
    if template:
        spec = next((a for a in template.artifacts if a.key == artifact_key), None)
        if spec:
            return spec.role
    return "Document Author"


def _build_upstream_context(project_id: int, artifact_key: str, overlay: WaveOverlay, deps: list[dict] | None = None) -> str:
    """
    Build scoped upstream context from the overlay for the artifact being
    updated (D13). Only declared upstream artifacts are injected.

    ``deps`` may be passed in (loaded once per wave) to avoid re-querying the
    dependency graph for every job; previews come from the overlay's memo cache.
    """
    if deps is None:
        import database as db_module
        deps = db_module.list_artifact_deps(project_id)
    upstream_keys = [d["upstream_key"] for d in deps if d["downstream_key"] == artifact_key]

    parts = []
    for key in sorted(upstream_keys):
        content = overlay.get(key, "")
        if content:
            preview, omitted = overlay.preview(key, UPSTREAM_ARTIFACT_CAP)
            note = f" [preview: {omitted:,} chars omitted]" if omitted else ""
            parts.append(f"--- ARTIFACT: {key} ---{note}\n{preview}\n--- END: {key} ---")
    return "\n\n".join(parts) if parts else "(no upstream artifacts declared)"