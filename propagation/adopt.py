"""
Adopt-existing-project flow (C27).

Converts an existing plain folder/workspace into a managed project without
rewriting any user document. It scans the workspace, maps every discoverable
markdown file onto a registered artifact, records baseline hashes so the first
real edit produces a clean change event, and offers an optional reviewable
front-matter pass. Adoption is strictly opt-in and non-destructive.
"""

import os


def _discover_markdown_files(workspace_dir: str) -> dict[str, str]:
    """Walk a workspace and map ``<basename> -> rel_path`` for every .md file."""
    files: dict[str, str] = {}
    if not os.path.isdir(workspace_dir):
        return files
    for root, _dirs, names in os.walk(workspace_dir):
        if ".git" in root or ".agents" in root:
            continue
        for name in names:
            if name.endswith(".md"):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, workspace_dir).replace("\\", "/")
                key = os.path.splitext(name)[0]
                files[key] = rel
    return files


def adopt_workspace(
    project_id: int,
    workspace_dir: str,
    template_id: str = "sdlc",
    generate_front_matter: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Adopt a workspace as a managed project.

    - Never rewrites a document unless ``generate_front_matter`` is requested
      (and even then the caller must present the diff for approval).
    - Registers every discovered markdown file as an artifact, recording its
      baseline ``content_hash`` so the next human edit becomes a clean change
      event.
    - Expands template role edges to artifact edges for the found keys.

    Returns a report dict.
    """
    import database as db_module
    from propagation.scanner import compute_content_hash, read_artifact_file
    from templates import get_template, expand_role_edges_to_artifact_edges
    from parsers import build_front_matter

    project = db_module.get_folder(project_id)
    if not project:
        raise ValueError(f"Folder not found: {project_id}")

    template = get_template(template_id)
    if not template:
        raise ValueError(f"Unknown template: {template_id}")

    files = _discover_markdown_files(workspace_dir)
    keys = sorted(files.keys())

    if dry_run:
        return {
            "project_id": project_id,
            "dry_run": True,
            "discovered_files": files,
            "would_register": len(keys),
        }

    # Update folder to remain a folder container but become a managed project.
    db_module.update_folder_project(
        project_id,
        kind="project",
        workspace_dir=workspace_dir,
        template_id=template_id,
        propagation_mode="off",  # Q3: adopted projects default to 'off'
    )

    registered = []
    rewrites = []  # (path, proposed_content) when front-matter generation is on

    for key in keys:
        rel_path = files[key]
        content, exists = read_artifact_file(workspace_dir, rel_path)

        # Determine role from the template for this key (or default).
        spec = next((a for a in template.artifacts if a.key == key), None)
        role = spec.role if spec else "Unknown role"

        if generate_front_matter:
            from parsers import parse_front_matter
            try:
                parse_front_matter(content)
                new_content = content
            except Exception:
                fm = build_front_matter(artifact_id=key, role=role, version=1, origin="human")
                new_content = fm + "\n" + content.lstrip("\n")
                rewrites.append({"rel_path": rel_path, "proposed_content": new_content})
                content = new_content
                exists = True

        content_hash = compute_content_hash(content) if content else ""
        artifact = db_module.create_artifact(
            project_id=project_id,
            artifact_key=key,
            rel_path=rel_path,
            version=1,
            content_hash=content_hash,
            status="current",
            origin="human",
        )
        registered.append(artifact["artifact_key"])

    # Expand template role edges to artifact edges for the discovered keys.
    edges = expand_role_edges_to_artifact_edges(template)
    db_module.delete_artifact_deps(project_id)
    for upstream, downstream in edges:
        if upstream in keys and downstream in keys:
            db_module.create_artifact_dep(project_id, upstream, downstream)

    return {
        "project_id": project_id,
        "artifacts_registered": registered,
        "edges_created": sum(1 for u, d in edges if u in keys and d in keys),
        "rewrites_proposed": len(rewrites),
        "rewrites": rewrites,
    }