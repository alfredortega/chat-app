"""
documents.py — unified Markdown document service.

Exposes Markdown files from two conversation-scoped storage backends:

  * ``output``  — files under the conversation's *effective* output directory
                  (conversation override → app default), resolved server-side.
  * ``upload``  — files stored in the conversation upload directory and
                  represented by a ``ConvFile`` row (also the storage used
                  when local file access is disabled).

Every document is addressed by an opaque server-defined identifier:

    output:report.md
    upload:42

The service enforces conversation ownership, a strict output-directory jail
(realpath + commonpath validation), Markdown-only, size limits, optimistic
concurrency via SHA-256 hashes, and atomic replacement writes.

Managed project artifacts are detected separately and an edit is routed
through the existing project workflow (human-authored change event + git)
instead of a silent generic write. Propagation is only ever triggered via the
existing project routes/worker — never here.
"""

import os
from datetime import datetime, timezone

import database as db

MARKDOWN_EXTS = (".md", ".markdown")

# Editable-content ceiling for PUT (request bodies above this are rejected).
MAX_EDITABLE_BYTES = 2_000_000
# Ceiling for reading a document's raw content into memory.
MAX_READ_BYTES = 10 * 1024 * 1024

# Lightweight editor backups (M4): before replacing an ordinary output file we
# keep a short history of hashed versions under ``<output>/.agents/editor-backups``.
BACKUP_KEEP = 8
BACKUP_MAX_TOTAL_BYTES = 20 * 1024 * 1024
BACKUP_DIRNAME = os.path.join(".agents", "editor-backups")

# Identifier prefixes.
OUTPUT_PREFIX = "output:"
UPLOAD_PREFIX = "upload:"


class DocumentError(Exception):
    """Raised for document-API failures with an HTTP status and JSON body."""

    def __init__(self, status: int, error: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = {"error": error, "message": message}
        if extra:
            self.payload.update(extra)


# ── small helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_bytes(data: bytes) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _hash_file(path: str, max_bytes: int = MAX_READ_BYTES) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _read_raw(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _is_markdown(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in MARKDOWN_EXTS


def _mtime_iso(path: str) -> str:
    try:
        ts = os.stat(path).st_mtime
    except OSError:
        return _now_iso()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def effective_output_dir(conv: dict) -> str | None:
    """Resolve the conversation's effective output directory server-side.

    The browser never chooses this directory — it is derived from the
    conversation override, falling back to the application setting. Returns
    ``None`` when nothing is configured or the directory does not exist.
    """
    raw = (conv.get("output_dir") or db.get_setting("output_dir") or "").strip()
    if not raw:
        return None
    path = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(path):
        return None
    return path


def resolve_output_path_for(conv: dict, rel: str) -> str | None:
    """Public jail-checked resolver used by the import-browser endpoints."""
    output_dir = effective_output_dir(conv)
    if not output_dir:
        return None
    return _resolve_output_path(output_dir, rel)


def is_markdown_name(name: str) -> bool:
    return _is_markdown(name or "")


def _parse_document_id(document_id: str) -> tuple[str, str] | None:
    """Split a document id into ``(kind, payload)`` or return None."""
    if not document_id or not isinstance(document_id, str):
        return None
    if "\x00" in document_id:
        return None
    if document_id.startswith(OUTPUT_PREFIX):
        return "output", document_id[len(OUTPUT_PREFIX):]
    if document_id.startswith(UPLOAD_PREFIX):
        return "upload", document_id[len(UPLOAD_PREFIX):]
    return None


def _validate_rel_path(rel: str) -> str | None:
    """Validate a client-supplied relative path for the output jail.

    Rejects null bytes, backslashes, absolute paths, empty/`.`/`..` components.
    Returns the normalised relative path (POSIX separators) or None.
    """
    if not rel or not isinstance(rel, str):
        return None
    if "\x00" in rel or "\\" in rel:
        return None
    if rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts)


def _resolve_output_path(output_dir: str, rel: str) -> str | None:
    """Resolve ``rel`` under ``output_dir`` and confirm it stays inside.

    Symlinks are resolved with ``os.path.realpath`` and the result validated
    with ``os.path.commonpath`` against the real output root.
    """
    safe = _validate_rel_path(rel)
    if safe is None:
        return None
    root = os.path.realpath(output_dir)
    candidate = os.path.realpath(os.path.join(root, *safe.split("/")))
    if os.path.commonpath([root, candidate]) != root:
        return None
    return candidate


# ── managed project artifacts ────────────────────────────────────────────────

def managed_artifact_for(conv: dict, abs_path: str) -> dict | None:
    """Return artifact metadata if ``abs_path`` is a registered project artifact.

    A document is a managed artifact when the conversation belongs to a
    project folder and the file lives inside the project workspace at a path
    matching one of its registered artifacts. Returns ``None`` otherwise.
    """
    folder_id = conv.get("folder_id")
    if not folder_id:
        return None
    try:
        project = db.get_folder(folder_id)
    except Exception:
        return None
    if not project or project.get("kind") != "project":
        return None
    workspace = project.get("workspace_dir", "")
    if not workspace or not os.path.isdir(workspace):
        return None
    real = os.path.realpath(abs_path)
    workspace_real = os.path.realpath(workspace)
    if os.path.commonpath([workspace_real, real]) != workspace_real:
        return None
    for artifact in db.list_artifacts(folder_id):
        rel_path = artifact.get("rel_path") or ""
        if not rel_path:
            continue
        full = os.path.realpath(os.path.join(workspace_real, *rel_path.split("/")))
        if full == real:
            role_name = artifact.get("role_persona_id")
            return {
                "project_id": folder_id,
                "artifact_key": artifact["artifact_key"],
                "rel_path": rel_path,
                "workspace_dir": workspace_real,
                "version": artifact.get("version", 1),
                "role": role_name,
            }
    return None


def _compute_changed_reqs(artifact_key: str, content: str) -> list[str]:
    """REQ-nnn ids in scope for the requirements artifact; empty otherwise."""
    from parsers import extract_requirement_ids
    if artifact_key == "BA-REQ":
        return extract_requirement_ids(content) or []
    return []


def _record_managed_artifact_edit(
    project_id: int,
    artifact: dict,
    workspace_dir: str,
    rel_path: str,
    old_content: str,
    new_content: str,
) -> dict:
    """Record a human-authored edit in the project workflow.

    Creates (or coalesces into) a ``change_events`` row with ``origin=human``,
    keeps the artifact registry consistent (bumped version + content hash),
    marks downstream artifacts stale, and commits to git as a human change so
    diffs/rollback keep working. Propagation itself is never triggered here.
    """
    import difflib

    from propagation.scanner import debounce_change_events, _mark_downstream_stale

    artifact_key = artifact["artifact_key"]
    current = db.get_artifact(project_id, artifact_key) or artifact
    from_version = current.get("version", 1)
    to_version = from_version + 1

    diff = "".join(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    ))

    changed_reqs = _compute_changed_reqs(artifact_key, new_content)

    existing = debounce_change_events(project_id, artifact_key, max_age_seconds=120)
    if existing:
        merged = existing.get("diff") or ""
        if diff and diff not in merged:
            db.update_change_event(existing["id"], diff=(merged + "\n" + diff).strip())
        event = db.update_change_event(existing["id"])
    else:
        event = db.create_change_event(
            project_id=project_id,
            source_key=artifact_key,
            from_version=from_version,
            to_version=to_version,
            summary="Edited in the document editor (human-authored).",
            changed_reqs=changed_reqs,
            removed_reqs=[],
            diff=diff or None,
            origin="human",
        )
        _mark_downstream_stale(project_id, artifact_key)

    # Registry stays consistent so scanning does not re-detect the edit that
    # was just recorded above (the human content is the new source of truth).
    db.update_artifact(
        project_id,
        artifact_key,
        version=to_version,
        content_hash=db.compute_content_hash(new_content),
    )

    try:
        from git_integration import git_add, git_commit
        git_add(workspace_dir, rel_path)
        git_commit(workspace_dir, f"Human edit (document editor): {artifact_key}")
    except Exception:
        pass  # git absence must never break the editor save

    return event or {}


# ── document metadata ────────────────────────────────────────────────────────

def _build_meta(
    *,
    document_id: str,
    name: str,
    kind: str,
    path: str,
    abs_path: str,
    file_id,
    editable: bool,
    managed: dict | None,
    size_bytes: int,
    modified_at: str,
    content_hash: str,
) -> dict:
    return {
        "id": document_id,
        "name": name,
        "kind": kind,
        "path": path,
        "file_id": file_id,
        "size_bytes": size_bytes,
        "modified_at": modified_at,
        "content_hash": content_hash,
        "editable": editable,
        "managed_artifact": managed is not None,
        "artifact_key": managed["artifact_key"] if managed else None,
        "project_id": managed["project_id"] if managed else None,
    }


def _scan_output_documents(conv: dict, editable_flags: tuple) -> list[dict]:
    """Discover Markdown files under the effective output directory."""
    output_dir = effective_output_dir(conv)
    if not output_dir:
        return []
    editable_base, _ = editable_flags
    docs: list[dict] = []
    root = os.path.realpath(output_dir)
    for dirpath, dirnames, filenames in os.walk(output_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            if not _is_markdown(fname):
                continue
            full = os.path.join(dirpath, fname)
            real = os.path.realpath(full)
            if os.path.commonpath([root, real]) != root:
                continue  # symlink resolved outside the jail — skip
            rel = os.path.relpath(real, root).replace("\\", "/")
            try:
                size = os.path.getsize(real)
                if size > MAX_READ_BYTES:
                    continue
            except OSError:
                continue
            doc_id = f"{OUTPUT_PREFIX}{rel}"
            docs.append(_build_meta(
                document_id=doc_id,
                name=os.path.basename(real),
                kind="output",
                path=rel,
                abs_path=real,
                file_id=None,
                editable=editable_base,
                managed=managed_artifact_for(conv, real),
                size_bytes=size,
                modified_at=_mtime_iso(real),
                content_hash=_hash_file(real),
            ))
    return docs


def list_documents(conv_id: int) -> list[dict]:
    """Return Markdown documents available to the conversation.

    Searches only the effective output directory and the conversation's own
    upload records. Both backends' results are deduplicated by display name
    (output takes precedence, then uploads in original order).
    """
    conv = db.get_conversation(conv_id)
    if not conv:
        raise DocumentError(404, "not_found", "Conversation not found.")

    local_access = db.local_file_access_enabled()
    editable_flags = (local_access, True)  # (output, upload)

    docs = _scan_output_documents(conv, editable_flags)

    seen_names: set[str] = {d["name"].lower() for d in docs}
    for f in db.list_conv_files(conv_id):
        name = os.path.basename(f["original_name"])
        if not _is_markdown(name):
            continue
        if name.lower() in seen_names:
            continue
        disk_path = f["disk_path"]
        if not os.path.isfile(disk_path):
            continue
        try:
            size = os.path.getsize(disk_path)
        except OSError:
            continue
        doc_id = f"{UPLOAD_PREFIX}{f['id']}"
        docs.append(_build_meta(
            document_id=doc_id,
            name=name,
            kind="upload",
            path=f["original_name"],
            abs_path=disk_path,
            file_id=f["id"],
            editable=editable_flags[1],
            managed=None,
            size_bytes=size,
            modified_at=_mtime_iso(disk_path),
            content_hash=_hash_file(disk_path),
        ))
        seen_names.add(name.lower())

    # Deterministic kind + name ordering.
    docs.sort(key=lambda d: (0 if d["kind"] == "output" else 1, d["name"].lower()))
    return docs


# ── resolution + read ─────────────────────────────────────────────────────────

def _resolve_document(conv: dict, document_id: str) -> dict:
    """Resolve and validate a document identifier, returning its metadata
    (including the on-disk absolute path). Raises ``DocumentError`` when the
    id is malformed, out of scope, or missing."""
    parsed = _parse_document_id(document_id)
    if parsed is None:
        raise DocumentError(400, "invalid_document_id", "Malformed document identifier.")
    kind, payload = parsed

    if kind == "upload":
        if not payload.isdigit():
            raise DocumentError(400, "invalid_document_id", "Malformed upload document identifier.")
        record = db.get_conv_file(int(payload))
        if not record or record["conversation_id"] != conv["id"]:
            raise DocumentError(404, "not_found", "Document not found.")
        if not _is_markdown(os.path.basename(record["original_name"])):
            raise DocumentError(400, "not_markdown", "Only Markdown documents are supported.")
        if not os.path.isfile(record["disk_path"]):
            raise DocumentError(404, "not_found", "Document is no longer available.")
        abs_path = record["disk_path"]
        return {
            "document_id": document_id,
            "name": os.path.basename(record["original_name"]),
            "kind": "upload",
            "path": record["original_name"],
            "abs_path": abs_path,
            "file_id": record["id"],
            "editable": True,
            "managed": None,
        }

    # output kind
    output_dir = effective_output_dir(conv)
    if not output_dir:
        raise DocumentError(404, "not_found", "Document not found.")
    abs_path = _resolve_output_path(output_dir, payload)
    if abs_path is None:
        raise DocumentError(400, "invalid_document_id", "Document path is not allowed.")
    if not os.path.isfile(abs_path):
        raise DocumentError(404, "not_found", "Document not found.")
    rel = os.path.relpath(abs_path, os.path.realpath(output_dir)).replace("\\", "/")
    if not _is_markdown(os.path.basename(abs_path)):
        raise DocumentError(400, "not_markdown", "Only Markdown documents are supported.")
    return {
        "document_id": document_id,
        "name": os.path.basename(abs_path),
        "kind": "output",
        "path": rel,
        "abs_path": abs_path,
        "file_id": None,
        "editable": db.local_file_access_enabled(),
        "managed": managed_artifact_for(conv, abs_path),
    }


def read_document(conv_id: int, document_id: str) -> dict:
    """Return the raw Markdown document plus metadata."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise DocumentError(404, "not_found", "Conversation not found.")
    resolved = _resolve_document(conv, document_id)
    abs_path = resolved["abs_path"]
    content = _read_raw(abs_path)
    byte_size = len(content.encode("utf-8"))
    return {
        "id": resolved["document_id"],
        "name": resolved["name"],
        "kind": resolved["kind"],
        "content": content,
        "content_hash": _hash_bytes(content.encode("utf-8")),
        "modified_at": _mtime_iso(abs_path),
        "size_bytes": byte_size,
        "editable": resolved["editable"],
        "managed_artifact": resolved["managed"] is not None,
        "artifact_key": resolved["managed"]["artifact_key"] if resolved["managed"] else None,
        "project_id": resolved["managed"]["project_id"] if resolved["managed"] else None,
    }


def _backup_output_file(conv: dict, path: str, old_content: str) -> None:
    """Keep a bounded history of ordinary output files (M4, non-blocking)."""
    import hashlib
    try:
        output_dir = effective_output_dir(conv)
        if not output_dir:
            return
        backup_dir = os.path.join(output_dir, BACKUP_DIRNAME)
        os.makedirs(backup_dir, exist_ok=True)
        digest = hashlib.sha256(old_content.encode("utf-8")).hexdigest()
        target = os.path.join(backup_dir, f"{digest}.md")
        if not os.path.exists(target):
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(old_content)
        # Retain only the most recent BACKUP_KEEP versions, capped by total size.
        backups = sorted(
            (f for f in os.listdir(backup_dir) if f.endswith(".md")),
            key=lambda n: os.path.getmtime(os.path.join(backup_dir, n)),
            reverse=True,
        )
        total = sum(os.path.getsize(os.path.join(backup_dir, n)) for n in backups)
        for name in list(backups[BACKUP_KEEP:]):
            os.remove(os.path.join(backup_dir, name))
            total -= 0
        for name in backups[BACKUP_KEEP:]:
            path_b = os.path.join(backup_dir, name)
            if os.path.exists(path_b):
                try:
                    total = sum(
                        os.path.getsize(os.path.join(backup_dir, n))
                        for n in os.listdir(backup_dir) if n.endswith(".md")
                    )
                except OSError:
                    pass
        for name in sorted(
            (f for f in os.listdir(backup_dir) if f.endswith(".md")),
            key=lambda n: os.path.getmtime(os.path.join(backup_dir, n)),
        ):
            if total <= BACKUP_MAX_TOTAL_BYTES:
                break
            try:
                total -= os.path.getsize(os.path.join(backup_dir, name))
                os.remove(os.path.join(backup_dir, name))
            except OSError:
                pass
    except Exception:
        pass  # backups must never break an editor save


def rename_document(
    conv_id: int,
    document_id: str,
    new_name,
):
    """Rename a document, returning its new metadata.

    Output documents are renamed on disk (jail-checked); upload records keep
    their uuid disk name and only ``original_name`` is updated. Renaming a
    managed project artifact is rejected — that is owned by the workspace.
    """
    if not isinstance(new_name, str) or not new_name.strip():
        raise DocumentError(400, "invalid_name", "A document name is required.")
    new_name = new_name.strip()
    if "\x00" in new_name or "/" in new_name or "\\" in new_name:
        raise DocumentError(400, "invalid_name", "Document names may not contain path separators.")
    if new_name.startswith(".") or not _is_markdown(new_name):
        raise DocumentError(400, "invalid_name", "Only Markdown document names are allowed.")

    conv = db.get_conversation(conv_id)
    if not conv:
        raise DocumentError(404, "not_found", "Conversation not found.")
    resolved = _resolve_document(conv, document_id)
    if resolved["managed"]:
        raise DocumentError(409, "managed_artifact",
                            "Managed project artifacts are renamed through the project workspace.")

    if resolved["kind"] == "upload":
        same = resolved["name"].lower() == new_name.lower()
        if same:
            return {"success": True, "id": resolved["document_id"], "name": resolved["name"], "kind": "upload"}
        db.update_conv_file(resolved["file_id"], original_name=new_name)
        return {"success": True,
                "id": resolved["document_id"], "name": new_name, "kind": "upload",
                "path": new_name}

    # output kind: rename inside the same directory, within the jail.
    output_dir = effective_output_dir(conv)
    if not output_dir:
        raise DocumentError(404, "not_found", "Document not found.")
    rel_dir = os.path.dirname(resolved["path"])
    safe_rel = (rel_dir + "/" + new_name) if rel_dir else new_name
    new_target = _resolve_output_path(output_dir, safe_rel)
    if new_target is None:
        raise DocumentError(400, "invalid_name", "The new name is not allowed.")
    if os.path.exists(new_target):
        raise DocumentError(409, "document_exists", "A document with that name already exists.")
    try:
        os.replace(resolved["abs_path"], new_target)
    except OSError as exc:
        raise DocumentError(500, "rename_failed", f"Could not rename the document: {exc}")
    rel = os.path.relpath(new_target, os.path.realpath(output_dir)).replace("\\", "/")
    return {
        "success": True,
        "id": f"{OUTPUT_PREFIX}{rel}",
        "name": new_name,
        "kind": "output",
        "path": rel,
    }


# ── update (optimistic concurrency + atomic replace) ─────────────────────────

def _atomic_write(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically (temp file + fsync + replace).

    Raises ``OSError`` on failure; the original file is never left partial.
    """
    import tempfile
    dir_name = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_name, prefix=".doc-write-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_document(
    conv_id: int,
    document_id: str,
    content,
    expected_hash: str | None = None,
) -> dict:
    """Save ``content`` to the document if it still matches ``expected_hash``.

    Returns ``{success, content_hash, modified_at, size_bytes, ...}``.
    Raises ``DocumentError(409, "document_changed", ...)`` on optimistic-concurrency
    conflict including the server's current content for manual merge.
    """
    if not isinstance(content, str):
        raise DocumentError(400, "invalid_content", "Document content must be text.")
    byte_size = len(content.encode("utf-8"))
    if byte_size > MAX_EDITABLE_BYTES:
        raise DocumentError(413, "document_too_large",
                            f"Document exceeds the {MAX_EDITABLE_BYTES // 1024 // 1024} MB edit limit.")

    conv = db.get_conversation(conv_id)
    if not conv:
        raise DocumentError(404, "not_found", "Conversation not found.")
    resolved = _resolve_document(conv, document_id)

    if not resolved["editable"]:
        raise DocumentError(403, "read_only",
                            "Editing this document is disabled while local file access is off.")

    abs_path = resolved["abs_path"]
    old_content = _read_raw(abs_path)
    current_hash = _hash_bytes(old_content.encode("utf-8"))

    if expected_hash is not None and expected_hash != current_hash:
        raise DocumentError(
            409, "document_changed",
            "The file changed outside the editor.",
            extra={"current_hash": current_hash, "current_content": old_content},
        )

    # Lightweight version history for ordinary output files (never managed
    # artifacts, which are owned by git / the project workflow).
    if resolved["kind"] == "output" and not resolved["managed"]:
        _backup_output_file(conv, abs_path, old_content)

    _atomic_write(abs_path, content)

    new_hash = _hash_bytes(content.encode("utf-8"))
    modified_at = _mtime_iso(abs_path)

    managed = resolved["managed"]
    if managed and resolved["kind"] == "output":
        _record_managed_artifact_edit(
            managed["project_id"],
            {"artifact_key": managed["artifact_key"], "rel_path": managed["rel_path"]},
            managed["workspace_dir"],
            managed["rel_path"],
            old_content,
            content,
        )

    if resolved["kind"] == "upload":
        db.update_conv_file(
            resolved["file_id"],
            size_bytes=byte_size,
            char_count=len(content),
            snippet=content[:500],
        )
        from file_handler import evict_upload_cache
        evict_upload_cache(abs_path)

    db.mark_recent_document_edit(conv_id, resolved["name"], resolved["kind"])

    resp = {
        "success": True,
        "content_hash": new_hash,
        "modified_at": modified_at,
        "size_bytes": byte_size,
        "id": resolved["document_id"],
        "name": resolved["name"],
        "kind": resolved["kind"],
    }
    if managed and resolved["kind"] == "output":
        resp["managed_artifact"] = True
        resp["project_id"] = managed["project_id"]
    return resp