"""
BA-inbox output consolidation (post-chat, deterministic).

The Business Analyst conversation writes user-facing requirement documents
(``01-requirements-specification.md``, ``02-user-stories...`` etc.) into the
project's ``Requirements/`` folder through the chat ``write_file`` tool.
Propagation, however, indexes requirements exclusively from the registered
artifact ``Requirements/BA-REQ.md`` using stable ``## REQ-nnn — Title``
headings and REQ IDs (see ``parsers.extract_requirement_ids``).

This module bridges the two worlds after a BA inbox chat turn finishes:

1. For every ``Requirements/*.md`` document other than ``BA-REQ.md`` itself,
   each ``## `` section becomes a ``## REQ-nnn — <heading>`` block.
2. REQ IDs are allocated from the project's ``next_req_seq`` and kept stable
   across runs via ``.agents/ba_index.json`` — so the scanner sees genuine
   modifications (never re-numbers, which would be a hard validation failure).
3. ``BA-REQ.md`` is regenerated with matching front-matter.
4. Direct downstream artifacts of ``BA-REQ`` get ``artifact_traces`` rows so
   impact analysis seeds from the changed REQ IDs.

The whole pass is deterministic (no LLM). It only runs when local file access
is enabled, since it writes into the workspace.
"""

import json
import os
import re

from parsers import build_front_matter, IDAllocator

BA_REQ_KEY = "BA-REQ"


def _split_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown into ``(heading, body_without_heading)`` sections by '## '."""
    matches = list(re.finditer(r"^##\s+(.+)$", content, re.MULTILINE))
    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        block = content[start:end].rstrip()
        body = block.split("\n", 1)[1] if "\n" in block else ""
        sections.append((m.group(1).strip(), body.lstrip("\n")))
    return sections


def _load_index(workspace: str) -> dict:
    path = os.path.join(workspace, ".agents", "ba_index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception:
        return {}


def _save_index(workspace: str, index: dict) -> None:
    agents_dir = os.path.join(workspace, ".agents")
    os.makedirs(agents_dir, exist_ok=True)
    path = os.path.join(agents_dir, "ba_index.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)


def consolidate_ba_output(
    conv_id: int,
    project_id: int | None = None,
) -> dict:
    """
    Consolidate a BA inbox conversation's written requirement docs into
    ``Requirements/BA-REQ.md``. Safe to call on any conversation; it no-ops
    unless the conversation is the project's designated BA inbox.

    Returns a summary dict, e.g. ``{"consolidated": False, "reason": "..."}``.
    """
    import database as db_module

    conv = db_module.get_conversation(conv_id)
    if not conv:
        return {"consolidated": False, "reason": "no conversation"}

    folder = None
    if conv.get("folder_id"):
        folder = db_module.get_folder(conv["folder_id"])
    if not folder or folder.get("kind") != "project":
        return {"consolidated": False, "reason": "not a project conversation"}

    project_id = folder["id"]
    if folder.get("ba_conversation_id") != conv_id:
        return {"consolidated": False, "reason": "not the BA inbox"}

    if not db_module.local_file_access_enabled():
        return {"consolidated": False, "reason": "local file access disabled"}

    workspace = folder.get("workspace_dir", "")
    if not workspace or not os.path.isdir(workspace):
        return {"consolidated": False, "reason": "no workspace"}

    reqs_dir = os.path.join(workspace, "Requirements")
    if not os.path.isdir(reqs_dir):
        return {"consolidated": False, "reason": "no Requirements dir"}

    # Gather BA-written requirement docs (everything except the artifact file).
    source_docs = []
    for name in sorted(os.listdir(reqs_dir)):
        if not name.endswith(".md"):
            continue
        if name == "BA-REQ.md":
            continue
        path = os.path.join(reqs_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        if content.strip():
            source_docs.append((name, content))

    if not source_docs:
        return {"consolidated": False, "reason": "no BA docs to consolidate"}

    artifact = db_module.get_artifact(project_id, BA_REQ_KEY)
    artifact_version = artifact.get("version", 1) if artifact else 1

    # Stable REQ ID allocation keyed by (source file, heading).
    index = _load_index(workspace)
    existing_ids = {v for v in index.values()}
    next_seq = 1
    if existing_ids:
        next_seq = max(int(i.split("-")[1]) for i in existing_ids
                       if re.fullmatch(r"REQ-\d{3}", i)) + 1
    folder_seq = folder.get("next_req_seq", 1)
    allocator = IDAllocator(max(folder_seq, next_seq))

    blocks = []
    new_ids = []
    for name, content in source_docs:
        for heading, body in _split_sections(content):
            key = f"{name}::{heading}"
            req_id = index.get(key)
            if not req_id or not re.fullmatch(r"REQ-\d{3}", req_id):
                req_id = allocator.allocate(1)[0]
                index[key] = req_id
            new_ids.append(req_id)
            blocks.append((req_id, heading, body))

    if not blocks:
        return {"consolidated": False, "reason": "no sections found"}

    new_ids = sorted(set(new_ids))
    _save_index(workspace, index)
    db_module.update_folder_project(
        project_id, next_req_seq=allocator.next_seq if allocator.next_seq > folder_seq else folder_seq
    )

    # Build BA-REQ.md.
    body_lines = []
    for req_id, heading, body in sorted(blocks, key=lambda b: int(b[0].split("-")[1])):
        body_lines.append(f"## {req_id} — {heading}\n\n{body}".rstrip() + "\n")
    body = "\n".join(body_lines) + "\n"

    front_matter = build_front_matter(
        artifact_id=BA_REQ_KEY,
        role="Business Analyst",
        version=artifact_version,
        origin="human",
    )
    new_content = front_matter + body

    ba_path = os.path.join(reqs_dir, "BA-REQ.md")
    try:
        with open(ba_path, "r", encoding="utf-8") as f:
            old_content = f.read()
    except Exception:
        old_content = ""
    changed = old_content != new_content
    if changed:
        with open(ba_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    # Seed traces from every downstream artifact of BA-REQ to each REQ ID, so
    # impact analysis finds the affected artifacts. Idempotent.
    traced = _seed_downstream_traces(project_id, new_ids)

    return {
        "consolidated": True,
        "changed": changed,
        "project_id": project_id,
        "req_ids": new_ids,
        "count": len(new_ids),
        "traces_added": traced,
    }


def _seed_downstream_traces(project_id: int, req_ids: list[str]) -> int:
    """Create ``artifact_traces`` from every direct downstream artifact of
    BA-REQ to each REQ ID (idempotent). Returns the number of rows added."""
    import database as db_module

    deps = db_module.list_artifact_deps(project_id)
    downstream = sorted({d["downstream_key"] for d in deps if d["upstream_key"] == BA_REQ_KEY})
    existing = db_module.list_artifact_traces(project_id)
    have = {(t["artifact_key"], t["req_id"]) for t in existing}

    added = 0
    for key in downstream:
        for req_id in req_ids:
            if (key, req_id) in have:
                continue
            db_module.create_artifact_trace(project_id, key, req_id)
            have.add((key, req_id))
            added += 1
    return added