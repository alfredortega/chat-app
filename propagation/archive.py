"""
Export / import for managed projects (C28).

Bumps the folder archive to ``export_version: "1.1"`` by adding the propagation
tables (artifacts, deps, traces, change events, jobs, issues, assumptions,
requests) and the artifact files themselves. Workspace paths are stored
relative to the archive and re-resolved on import. Existing ``1.0`` archives
(produced directly by ``app.py``) continue to import through the legacy route.
"""

import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone


def build_export(folder_id: int) -> bytes:
    """
    Build a ``1.1`` ZIP archive for a folder (including propagation state).
    """
    import database as db

    folder = db.get_folder(folder_id)
    if not folder:
        raise ValueError(f"Folder not found: {folder_id}")

    is_project = folder.get("kind") == "project"
    workspace_dir = folder.get("workspace_dir", "")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "export_version": "1.1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "folder": {
                "id": folder["id"],
                "name": folder["name"],
                "position": folder["position"],
            },
            "conversations": [],
        }

        if is_project:
            # Store the workspace relative to the archive.
            manifest["folder"]["kind"] = "project"
            manifest["folder"]["template_id"] = folder.get("template_id", "")
            manifest["folder"]["propagation_mode"] = folder.get("propagation_mode", "off")
            manifest["folder"]["next_req_seq"] = folder.get("next_req_seq", 1)
            manifest["folder"]["token_budget"] = folder.get("token_budget", 0)
            manifest["folder"]["ba_conversation_id"] = folder.get("ba_conversation_id")
            manifest["workspace_root"] = "workspace/"

            # Pack artifact files under workspace/ preserving relative paths.
            if workspace_dir and os.path.isdir(workspace_dir):
                for root, _dirs, names in os.walk(workspace_dir):
                    if ".git" in root or ".agents" in root:
                        continue
                    for name in names:
                        if not name.endswith(".md"):
                            continue
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, workspace_dir).replace("\\", "/")
                        zf.write(full, f"workspace/{rel}")

            # Propagation tables.
            manifest["artifacts"] = db.list_artifacts(folder_id)
            manifest["artifact_deps"] = db.list_artifact_deps(folder_id)
            manifest["artifact_traces"] = db.list_artifact_traces(folder_id)
            manifest["change_events"] = db.list_change_events(folder_id)
            manifest["propagation_jobs"] = [
                row
                for change in db.list_change_events(folder_id)
                for row in db.list_propagation_jobs(change["id"])
            ]
            manifest["agent_issues"] = db.list_agent_issues(folder_id)
            manifest["artifact_assumptions"] = db.list_artifact_assumptions(folder_id)
            manifest["artifact_requests"] = db.list_artifact_requests(folder_id)

        for conv in [c for c in db.list_conversations() if c.get("folder_id") == folder_id]:
            conv_id = conv["id"]
            messages = db.get_messages(conv_id)
            up_files = db.list_conv_files(conv_id)
            lf_rows = db.list_linked_folders(conv_id)

            msgs_arc_path = f"conversations/{conv_id}/messages.json"
            zf.writestr(msgs_arc_path, json.dumps(messages, indent=2))

            packed_files = []
            for uf in up_files:
                disk_path = uf.get("disk_path", "")
                arc_path = ""
                if disk_path and os.path.isfile(disk_path):
                    arc_path = f"conversations/{conv_id}/uploaded_files/{uf['original_name']}"
                    zf.write(disk_path, arc_path)
                packed_files.append({
                    "original_name": uf["original_name"],
                    "size_bytes": uf["size_bytes"],
                    "char_count": uf["char_count"],
                    "arc_path": arc_path,
                })

            manifest["conversations"].append({
                "id": conv_id,
                "title": conv["title"],
                "model_id": conv["model_id"],
                "output_dir": conv.get("output_dir", ""),
                "enable_tools": conv.get("enable_tools", 1),
                "created_at": conv["created_at"],
                "updated_at": conv["updated_at"],
                "messages_arc_path": msgs_arc_path,
                "uploaded_files": packed_files,
                "linked_folder_paths": [lf["folder_path"] for lf in lf_rows],
            })

        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    buf.seek(0)
    return buf.read()


def _restore_workspace(zf: zipfile.ZipFile, manifest: dict, dest_dir: str) -> None:
    """Extract ``workspace/`` files into ``dest_dir`` (resolved relative)."""
    workspace_root = manifest.get("workspace_root")
    if not workspace_root:
        return
    os.makedirs(dest_dir, exist_ok=True)
    for name in zf.namelist():
        if not name.startswith(workspace_root) or name.endswith("/"):
            continue
        rel = os.path.relpath(name, workspace_root)
        target = os.path.join(dest_dir, rel)
        # Path jail: never write outside dest_dir.
        if not os.path.abspath(target).startswith(os.path.abspath(dest_dir)):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(name) as src, open(target, "wb") as dst:
            dst.write(src.read())


def import_archive(zip_bytes: bytes, workspace_root: str | None = None) -> dict:
    """
    Import a ``1.1`` (or ``1.0``) archive, recreating folder + propagation state.

    Args:
        zip_bytes: The ZIP archive bytes.
        workspace_root: Directory where a ``workspace/`` tree should be
            materialised (when the archive is a 1.1 project export).

    Returns:
        {"ok": True, "folder_id": ..., "folder_name": ..., "export_version": ...}
    """
    import database as db

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    if "manifest.json" not in zf.namelist():
        raise ValueError("manifest.json missing from archive")
    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Could not parse manifest.json: {exc}")

    folder_meta = manifest.get("folder", {})
    folder_name = folder_meta.get("name", "Imported Folder")

    # Recreate the folder (workspace tree is created first so artifacts can
    # reference real files).
    ws_dir = None
    is_11 = manifest.get("export_version", "1.0") == "1.1"
    if is_11 and folder_meta.get("kind") == "project" and workspace_root:
        ws_dir = os.path.join(workspace_root, folder_name)
        _restore_workspace(zf, manifest, ws_dir)

    new_folder = db.create_folder(folder_name)
    folder_id = new_folder["id"]

    if is_11:
        db.update_folder_project(
            folder_id,
            kind=folder_meta.get("kind", "folder"),
            workspace_dir=ws_dir or "",
            template_id=folder_meta.get("template_id", ""),
            propagation_mode=folder_meta.get("propagation_mode", "off"),
            next_req_seq=folder_meta.get("next_req_seq", 1),
            token_budget=folder_meta.get("token_budget", 0),
            ba_conversation_id=folder_meta.get("ba_conversation_id"),
        )

        # Restore artifacts (idempotent create).
        for art in manifest.get("artifacts", []):
            db.create_artifact(
                project_id=folder_id,
                artifact_key=art["artifact_key"],
                rel_path=art["rel_path"],
                version=art.get("version", 1),
                content_hash=art.get("content_hash", ""),
                status=art.get("status", "current"),
                origin=art.get("origin", "human"),
            )
        for dep in manifest.get("artifact_deps", []):
            db.create_artifact_dep(folder_id, dep["upstream_key"], dep["downstream_key"])
        for trace in manifest.get("artifact_traces", []):
            db.create_artifact_trace(folder_id, trace["artifact_key"], trace["req_id"])

        # Track old -> new change event IDs so jobs/issues re-link correctly.
        change_id_map: dict[int, int] = {}
        for event in manifest.get("change_events", []):
            created = db.create_change_event(
                project_id=folder_id,
                source_key=event["source_key"],
                from_version=event["from_version"],
                to_version=event["to_version"],
                summary=event.get("summary", ""),
                changed_reqs=event.get("changed_reqs") or [],
                removed_reqs=event.get("removed_reqs") or [],
                diff=event.get("diff", ""),
                origin=event.get("origin", "human"),
            )
            change_id_map[event["id"]] = created["id"]
        for job in manifest.get("propagation_jobs", []):
            db.create_propagation_job(
                change_id=change_id_map.get(job["change_id"], job["change_id"]),
                artifact_key=job["artifact_key"],
                persona_id=job.get("persona_id"),
                depth=job.get("depth", 0),
                batch_id=job.get("batch_id"),
                conversation_id=job.get("conversation_id"),
            )
        for issue in manifest.get("agent_issues", []):
            db.create_agent_issue(
                project_id=folder_id,
                raised_by_key=issue["raised_by_key"],
                raised_by_persona_id=issue.get("raised_by_persona_id"),
                change_id=change_id_map.get(issue.get("change_id")) if issue.get("change_id") else None,
                depth=issue.get("depth", 0),
                kind=issue.get("kind", "question"),
                req_id=issue.get("req_id"),
                blocking=issue.get("blocking", 0),
                body=issue.get("body"),
                proposed_answer=issue.get("proposed_answer"),
            )
        for assumption in manifest.get("artifact_assumptions", []):
            db.create_artifact_assumption(
                folder_id, assumption["artifact_key"], assumption["req_id"],
                assumption.get("marker_text", ""),
            )
        for request in manifest.get("artifact_requests", []):
            db.create_artifact_request(
                folder_id, request.get("persona_id"), request["artifact_key"],
                request.get("rel_path", ""), request.get("rationale"),
            )

    # Restore conversations (works for both 1.0 and 1.1 manifests).
    for conv_meta in manifest.get("conversations", []):
        new_conv = db.create_conversation(
            title=conv_meta.get("title", "Imported Conversation"),
            model_id=conv_meta.get("model_id", ""),
            folder_id=folder_id,
        )
        new_conv_id = new_conv["id"]
        msgs_arc = conv_meta.get("messages_arc_path", "")
        if msgs_arc and msgs_arc in zf.namelist():
            try:
                for msg in json.loads(zf.read(msgs_arc).decode("utf-8")):
                    db.add_message(
                        new_conv_id, role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                        tool_call_id=msg.get("tool_call_id"),
                        tool_calls_json=msg.get("tool_calls_json"),
                    )
            except Exception:
                pass
        for lf_path in conv_meta.get("linked_folder_paths", []):
            if lf_path:
                db.add_linked_folder(new_conv_id, lf_path)

    return {
        "ok": True,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "export_version": manifest.get("export_version", "1.0"),
    }