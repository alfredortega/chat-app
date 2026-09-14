"""
Tool definitions and execution for function-calling.
Tools: write_file, read_file, list_directory, run_python, write_artifact,
read_artifact, list_artifacts, ask_question, raise_issue, propose_artifact
"""

import os
import json
import ipaddress
import socket
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import database as db

# ── Tool schema (sent to the model) ───────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": (
                "Fetch and read an approved research webpage. Use this for academic "
                "research when the user provides a URL or when a URL can be constructed "
                "for an enabled source such as Google Scholar. Only URLs matching an "
                "enabled research source are permitted. Do not use this for arbitrary URLs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The approved webpage URL to fetch."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write text content to a file on the local filesystem. "
                "Only invoke this tool when the user explicitly requests that a file "
                "be saved, created, or written to disk. Never use this tool for normal "
                "conversational responses — answer those directly in the chat. "
                "When calling this tool, provide ONLY a bare filename with extension "
                "(e.g. 'notes.md', 'report.txt') as the path — do NOT include any "
                "directory or folder component. The server will automatically place "
                "the file in the configured output directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Bare filename with extension only — no directory path. "
                            "Examples: 'notes.md', 'report.txt', 'analysis.py'. "
                            "The server resolves the full path using the configured output directory."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_named_file",
            "description": (
                "Read the full content of a file that belongs to this "
                "conversation: an uploaded file, or a file inside a linked "
                "folder. Pass the file's name exactly as shown in the context "
                "index (e.g. 'notes.md', 'src/app.py'). Use this when the user "
                "asks about a file whose contents were only previewed, or "
                "when you need details beyond the preview. Returns up to "
                "32,000 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The file name/path to read exactly as listed in the context index.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the text content of a file from the local filesystem. "
                "Use this when the user asks you to review, analyse, or update an "
                "existing file on disk. Returns the file's content as a string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to read, e.g. 'C:/Users/me/docs/report.txt'.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List the files and subdirectories inside a directory on the local filesystem. "
                "Use this to explore what files exist before reading or writing them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the directory to list, e.g. 'C:/Users/me/projects'.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute a Python code snippet and return its stdout output. "
                "Use this for data analysis, calculations, transformations, or any task "
                "where running code would give a concrete result. "
                "The following third-party libraries are installed and can be imported: "
                "pandas, numpy, scikit-learn (import as sklearn), and matplotlib. "
                "These are especially useful for data-science tasks (data exploration, "
                "feature engineering, model training/evaluation, and plotting). "
                "matplotlib runs headless, so save any figures to a file (e.g. "
                "plt.savefig('plot.png')) rather than calling plt.show(). "
                "Code runs in an isolated subprocess with a 30-second timeout. "
                "Do not use for code that requires user interaction or GUI output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute. Use print() to produce output.",
                    },
                },
                "required": ["code"],
            },
        },
    },
]


PROPAGATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": (
                "Read the content of a registered project artifact by its key. "
                "Artifacts are versioned documents in the project workspace (e.g., "
                "requirements, design docs, test plans). Use this to reference "
                "upstream artifacts when working on downstream deliverables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_key": {
                        "type": "string",
                        "description": "The unique key of the artifact to read (e.g., 'BA-REQ', 'DB-MODEL')."
                    },
                },
                "required": ["artifact_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_artifact",
            "description": (
                "Write or update a project artifact in the workspace. "
                "Only permitted subdirectories under the workspace root are allowed. "
                "Path traversal (..) is rejected. The artifact_key must be registered "
                "in the project's artifact index."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_key": {
                        "type": "string",
                        "description": "The unique key of the artifact (e.g., 'DB-MODEL', 'QA-PLAN')."
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write to the artifact file.",
                    },
                },
                "required": ["artifact_key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_artifacts",
            "description": (
                "List all registered artifacts in the project with their keys, "
                "titles, and current versions. Use this to discover what artifacts "
                "exist before reading or writing them."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_question",
            "description": (
                "Ask a clarifying question to the Business Analyst about a requirement. "
                "The question is routed to the BA's conversation inbox. If marked "
                "blocking, the current artifact regeneration pauses until answered. "
                "If non-blocking, an inline assumption marker is inserted and work continues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "requirement_id": {
                        "type": "string",
                        "description": "The REQ-nnn identifier the question relates to."
                    },
                    "question": {
                        "type": "string",
                        "description": "The clarifying question to ask the Business Analyst."
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "Whether this question blocks further work (true) or can be assumed (false). Default: false.",
                        "default": False,
                    },
                },
                "required": ["requirement_id", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "raise_issue",
            "description": (
                "Raise a non-blocking issue or observation about an artifact. "
                "Issues are recorded for review but do not pause propagation. "
                "Use for concerns that don't require immediate clarification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_key": {
                        "type": "string",
                        "description": "The artifact key the issue relates to."
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Issue severity level.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Description of the issue.",
                    },
                },
                "required": ["artifact_key", "severity", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_artifact",
            "description": (
                "Propose a new artifact that doesn't exist in the current project template. "
                "Requires approval before being added to the artifact registry. "
                "Use when a role discovers a need for a new deliverable type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "The persona role proposing the artifact (e.g., 'UX', 'QA', 'SEC')."
                    },
                    "artifact_key": {
                        "type": "string",
                        "description": "Proposed unique key for the new artifact."
                    },
                    "title": {
                        "type": "string",
                        "description": "Human-readable title for the artifact."
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this artifact is needed."
                    },
                },
                "required": ["role", "artifact_key", "title", "rationale"],
            },
        },
    },
]


ALLOWED_ARTIFACT_SUBDIRS = {
    "Requirements",
    "Design",
    "Data",
    "Test Cases",
    "Security",
    "Project Plan",
    ".agents",
    ".agents/changes",
    ".agents/proposals",
}

ARTIFACT_PREFIX_TO_SUBDIR = {
    "BA": "Requirements",
    "UX": "Design",
    "DB": "Data",
    "QA": "Test Cases",
    "SEC": "Security",
    "PM": "Project Plan",
}


PROFILE_TOOL_MAP = {
    "chat": [t["function"]["name"] for t in TOOLS],
    "propagation": [t["function"]["name"] for t in PROPAGATION_TOOLS],
}


def build_tools(context: str) -> list[dict]:
    """
    Return the tool definitions for the given context/profile.

    Args:
        context: Profile name - "chat" (default) or "propagation"

    Returns:
        List of tool definition dicts compatible with OpenAI function-calling API.
    """
    if context == "propagation":
        return PROPAGATION_TOOLS
    return TOOLS


def _is_tool_allowed_for_context(tool_name: str, context: str) -> bool:
    """Check if a tool is allowed in the given context/profile."""
    allowed = PROFILE_TOOL_MAP.get(context, [])
    return tool_name in allowed


# ── Tool execution ─────────────────────────────────────────────────────────────

def execute_tool_call(
    name: str,
    arguments_json: str,
    output_dir: str = None,
    context: str = "chat",
    workspace_dir: str = None,
    project_id: int = None,
    conv_id: int = None,
) -> dict:
    """
    Execute a named tool call.

    ``output_dir`` is the *effective* output directory for the current
    conversation (conversation override → app default, already resolved by
    the caller).  When supplied it takes precedence over the DB setting so
    that per-conversation output directories are honoured.

    ``context`` is the tool profile: "chat" (default) or "propagation".
    Tools not in the active profile are rejected.

    ``workspace_dir`` is the project workspace root for propagation-context
    tools (e.g., write_artifact). Required when context="propagation".

    ``project_id`` is the project (folder) ID for artifact registration checks.

    ``conv_id`` scopes read_named_file to the conversation's own uploaded files
    and linked folders.
    """
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "result": f"Invalid arguments JSON: {exc}",
            "display": "❌ Tool call failed — could not parse arguments.",
        }

    if not _is_tool_allowed_for_context(name, context):
        return {
            "success": False,
            "result": f"Tool '{name}' is not available in '{context}' context.",
            "display": f"❌ Tool '{name}' not permitted in {context} mode.",
        }

    if name == "write_file":
        return _write_file(args, output_dir=output_dir)
    if name == "fetch_webpage":
        return _fetch_webpage(args)
    if name == "read_file":
        return _read_file(args)
    if name == "read_named_file":
        return _read_named_file(args, conv_id=conv_id)
    if name == "list_directory":
        return _list_directory(args)
    if name == "run_python":
        return _run_python(args)
    if name == "write_artifact":
        return _write_artifact(args, workspace_dir=workspace_dir, project_id=project_id)
    if name == "read_artifact":
        return _read_artifact(args, workspace_dir=workspace_dir)
    if name == "list_artifacts":
        return _list_artifacts(args, workspace_dir=workspace_dir)
    if name == "ask_question":
        return _ask_question(args, workspace_dir=workspace_dir)
    if name == "raise_issue":
        return _raise_issue(args, workspace_dir=workspace_dir)
    if name == "propose_artifact":
        return _propose_artifact(args, workspace_dir=workspace_dir)

    return {
        "success": False,
        "result": f"Unknown tool: {name}",
        "display": f"❌ Unknown tool '{name}'.",
    }


def _write_artifact(
    args: dict,
    workspace_dir: str = None,
    project_id: int = None,
) -> dict:
    """Write an artifact file jailed to the workspace root with allowed subdirs."""
    artifact_key = args.get("artifact_key", "").strip()
    content = args.get("content", "")

    if not artifact_key:
        return {
            "success": False,
            "result": "No artifact_key provided.",
            "display": "❌ Artifact write failed — no artifact_key specified.",
        }

    if not workspace_dir:
        return {
            "success": False,
            "result": "No workspace_dir configured for propagation context.",
            "display": "❌ Workspace directory not configured for artifact operations.",
        }

    workspace_dir = os.path.normpath(os.path.expanduser(workspace_dir))

    if not os.path.isdir(workspace_dir):
        return {
            "success": False,
            "result": f"Workspace directory does not exist: {workspace_dir}",
            "display": f"❌ Workspace not found: `{workspace_dir}`",
        }

    if ".." in artifact_key:
        return {
            "success": False,
            "result": "Path traversal denied: artifact_key contains '..'.",
            "display": "❌ Path traversal denied: artifact_key must not contain '..'.",
        }

    # Check if artifact_key is registered for this project (C08)
    if project_id is not None:
        try:
            import database as db_module
            if not db_module.is_artifact_registered(project_id, artifact_key):
                return {
                    "success": False,
                    "result": f"Artifact key '{artifact_key}' is not registered for this project. Use propose_artifact tool to request it.",
                    "display": f"❌ Unregistered artifact: `{artifact_key}` not in project registry.",
                }
        except Exception as exc:
            # If DB check fails, log but don't block (backward compatibility)
            pass

    prefix = artifact_key.split("-")[0] if "-" in artifact_key else artifact_key
    subdir = ARTIFACT_PREFIX_TO_SUBDIR.get(prefix)
    if not subdir:
        return {
            "success": False,
            "result": f"Artifact key prefix '{prefix}' is not a recognized role prefix.",
            "display": f"❌ Invalid artifact prefix: `{prefix}` not in {sorted(ARTIFACT_PREFIX_TO_SUBDIR.keys())}.",
        }
    if subdir not in ALLOWED_ARTIFACT_SUBDIRS:
        return {
            "success": False,
            "result": f"Artifact key prefix '{prefix}' maps to disallowed subdirectory '{subdir}'.",
            "display": f"❌ Invalid artifact path: `{subdir}` not in allowed directories.",
        }

    safe_filename = f"{artifact_key}.md"
    target_path = os.path.join(workspace_dir, subdir, safe_filename)
    target_path = os.path.normpath(target_path)

    if not os.path.commonpath([workspace_dir, target_path]) == workspace_dir:
        return {
            "success": False,
            "result": "Path traversal denied: artifact path escapes workspace root.",
            "display": "❌ Path traversal denied: artifact must be under workspace root.",
        }

    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {
            "success": True,
            "result": f"Artifact written to: {target_path}",
            "display": f"✅ Artifact saved: `{artifact_key}`",
        }
    except OSError as exc:
        return {
            "success": False,
            "result": f"Failed to write artifact: {exc}",
            "display": f"❌ Artifact write failed: {exc}",
        }


def _read_artifact(args: dict, workspace_dir: str = None) -> dict:
    """Read an artifact file from the workspace (stub for C04)."""
    artifact_key = args.get("artifact_key", "").strip()

    if not artifact_key:
        return {
            "success": False,
            "result": "No artifact_key provided.",
            "display": "❌ Artifact read failed — no artifact_key specified.",
        }

    if not workspace_dir:
        return {
            "success": False,
            "result": "No workspace_dir configured for propagation context.",
            "display": "❌ Workspace directory not configured for artifact operations.",
        }

    return {
        "success": False,
        "result": "read_artifact not yet implemented (C08).",
        "display": "⚠️ read_artifact is declared but not implemented.",
    }


def _list_artifacts(args: dict, workspace_dir: str = None) -> dict:
    """List registered artifacts (stub for C04)."""
    if not workspace_dir:
        return {
            "success": False,
            "result": "No workspace_dir configured for propagation context.",
            "display": "❌ Workspace directory not configured for artifact operations.",
        }

    return {
        "success": False,
        "result": "list_artifacts not yet implemented (C08).",
        "display": "⚠️ list_artifacts is declared but not implemented.",
    }


def _ask_question(args: dict, workspace_dir: str = None) -> dict:
    """Ask a clarifying question (stub for C04)."""
    return {
        "success": False,
        "result": "ask_question not yet implemented (C18).",
        "display": "⚠️ ask_question is declared but not implemented.",
    }


def _raise_issue(args: dict, workspace_dir: str = None) -> dict:
    """Raise an issue (stub for C04)."""
    return {
        "success": False,
        "result": "raise_issue not yet implemented (C18).",
        "display": "⚠️ raise_issue is declared but not implemented.",
    }


def _propose_artifact(args: dict, workspace_dir: str = None) -> dict:
    """Propose a new artifact (stub for C04)."""
    return {
        "success": False,
        "result": "propose_artifact not yet implemented (C18).",
        "display": "⚠️ propose_artifact is declared but not implemented.",
    }


class _PageTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        elif self.skip_depth == 0 and tag.lower() in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        elif self.skip_depth == 0 and tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)


def _url_is_allowed(url: str) -> bool:
    target = urlsplit(url)
    if target.scheme not in {"http", "https"} or not target.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(target.hostname, None)
        if any(ipaddress.ip_address(item[4][0]).is_private or ipaddress.ip_address(item[4][0]).is_loopback for item in addresses):
            return False
    except (OSError, ValueError):
        return False
    for source in db.list_research_sources():
        if not source["enabled"]:
            continue
        allowed = urlsplit(source["url"])
        if target.scheme == allowed.scheme and target.hostname.lower() == allowed.hostname.lower():
            allowed_path = allowed.path.rstrip("/")
            if not allowed_path or target.path == allowed_path or target.path.startswith(allowed_path + "/"):
                return True
    return False


class _AllowedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_is_allowed(newurl):
            raise ValueError("redirect target is not on the enabled research source list")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_webpage(args: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not _url_is_allowed(url):
        return {"success": False, "result": "URL is not permitted by the enabled research source list.",
                "display": "Research fetch blocked: URL is not on the enabled source list.",
                "blocked_url": url}
    try:
        request = Request(url, headers={"User-Agent": "ResearchAssistant/1.0"})
        opener = build_opener(_AllowedRedirectHandler())
        with opener.open(request, timeout=15) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/pdf"}:
                return {"success": False, "result": f"Unsupported content type: {content_type}",
                        "display": f"Research fetch skipped: unsupported content type {content_type}."}
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                body = body[:2_000_000]
                truncated = True
            else:
                truncated = False
            if content_type == "application/pdf":
                from pypdf import PdfReader
                import io
                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)
            elif content_type == "text/html":
                parser = _PageTextParser()
                parser.feed(body.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
                text = " ".join("".join(parser.parts).split())
            else:
                text = body.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        note = "\n\n[Response truncated at 2,000,000 bytes]" if truncated else ""
        return {"success": True, "result": f"Source URL: {url}\n\n{text}{note}",
                "display": f"Fetched research source: `{url}`"}
    except Exception as exc:
        return {"success": False, "result": f"Failed to fetch webpage: {exc}",
                "display": f"Research fetch failed: {exc}"}


# ── write_file ─────────────────────────────────────────────────────────────────

def _write_file(args: dict, output_dir: str = None) -> dict:
    path = args.get("path", "").strip()
    content = args.get("content", "")

    if not path:
        return {"success": False, "result": "No file path provided.",
                "display": "❌ File write failed — no path was specified."}

    path = os.path.expanduser(path)

    # Determine the effective output directory (conversation override → app default).
    effective_dir = (output_dir or db.get_setting("output_dir") or "").strip()

    if not os.path.isabs(path):
        # Relative path: place it under the effective directory, using only the basename
        # to prevent directory traversal.
        if effective_dir:
            path = os.path.join(effective_dir, os.path.basename(path))
        else:
            return {"success": False, "result": "No output directory configured.",
                    "display": "❌ No output directory configured for relative path."}
    else:
        # Absolute path: must be within the allowed output directory. When no
        # output directory is configured, absolute writes are denied outright.
        if not effective_dir:
            return {"success": False,
                    "result": "No output directory configured; absolute writes are denied.",
                    "display": "❌ No output directory configured for absolute path."}
        norm_effective = os.path.normpath(effective_dir)
        if not (path.startswith(norm_effective + os.sep) or path == norm_effective):
            return {"success": False, "result": "Absolute path is outside the allowed output directory.",
                    "display": "❌ Path traversal denied: absolute path must be under the output directory."}
        path = os.path.normpath(path)

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {"success": True, "result": f"File written to: {path}",
                "display": f"✅ File saved: `{path}`"}
    except OSError as exc:
        return {"success": False, "result": f"Failed to write file: {exc}",
                "display": f"❌ File write failed: {exc}"}


# ── read_named_file ───────────────────────────────────────────────────────────

def _read_named_file(args: dict, conv_id: int = None) -> dict:
    """
    Read a conversation-scoped file (an uploaded file or a file inside a linked
    folder) by name. Resolution is restricted to the conversation's own files,
    so — unlike ``read_file`` — this never reads arbitrary paths.
    """
    name = (args.get("name") or "").strip().lstrip("/")
    if not name:
        return {"success": False, "result": "No file name provided.",
                "display": "❌ Read failed — no file name specified."}

    candidates = []
    if conv_id is not None:
        for f in db.list_conv_files(conv_id):
            candidates.append({
                "path": f["disk_path"],
                "label": f["original_name"],
                "name": os.path.basename(f["original_name"]),
            })
        from file_handler import scan_linked_folder
        for lf in db.list_linked_folders(conv_id):
            for entry in scan_linked_folder(lf["folder_path"]):
                candidates.append({
                    "path": entry["abs_path"],
                    "label": entry["rel_path"],
                    "name": entry["filename"],
                })

    if not candidates:
        return {"success": False,
                "result": "No uploaded files or linked folders on this conversation.",
                "display": "⚠️ No conversation files available."}

    lowered = name.lower()
    matches = [
        c for c in candidates
        if name in (c["label"], c["name"]) or lowered in c["label"].lower() or lowered in c["name"].lower()
    ]
    # 'app.py' already matches; prefer an exact basename/label over a substring.
    exact = [c for c in matches if name in (c["label"], c["name"])]
    if not matches:
        available = ", ".join(sorted({c["label"] for c in candidates})[:40])
        return {"success": False,
                "result": f"File not found: '{name}'. Available files: {available}",
                "display": f"❌ File not found: `{name}`"}

    match = (exact or matches)[0]
    try:
        from file_handler import extract_text
        text, truncated = extract_text(match["path"], os.path.basename(match["path"]))
        cap = 32_000
        if len(text) > cap:
            text = text[:cap] + f"\n… [truncated at {cap:,} chars]"
        note = " (truncated)" if truncated else ""
        return {"success": True, "result": text,
                "display": f"📄 Read file: `{match['label']}`{note}"}
    except Exception as exc:
        return {"success": False, "result": f"Failed to read file: {exc}",
                "display": f"❌ Read failed: `{match['label']}`"}


# ── read_file ──────────────────────────────────────────────────────────────────

def _read_file(args: dict) -> dict:
    path = args.get("path", "").strip()
    if not path:
        return {"success": False, "result": "No file path provided.",
                "display": "❌ Read failed — no path specified."}

    path = os.path.normpath(os.path.expanduser(path))

    if not os.path.exists(path):
        return {"success": False, "result": f"File not found: {path}",
                "display": f"❌ File not found: `{path}`"}
    if not os.path.isfile(path):
        return {"success": False, "result": f"Path is not a file: {path}",
                "display": f"❌ Not a file: `{path}`"}

    try:
        from file_handler import extract_text, HARD_LIMIT
        text, truncated = extract_text(path, os.path.basename(path))
        note = f"\n\n[Truncated at {HARD_LIMIT:,} characters]" if truncated else ""
        return {
            "success": True,
            "result": text + note,
            "display": f"📄 Read file: `{path}`" + (" *(truncated)*" if truncated else ""),
        }
    except Exception as exc:
        return {"success": False, "result": f"Failed to read file: {exc}",
                "display": f"❌ Read failed: {exc}"}


# ── list_directory ─────────────────────────────────────────────────────────────

def _list_directory(args: dict) -> dict:
    path = args.get("path", "").strip()
    if not path:
        return {"success": False, "result": "No path provided.",
                "display": "❌ List failed — no path specified."}

    path = os.path.normpath(os.path.expanduser(path))

    if not os.path.exists(path):
        return {"success": False, "result": f"Path not found: {path}",
                "display": f"❌ Path not found: `{path}`"}
    if not os.path.isdir(path):
        return {"success": False, "result": f"Not a directory: {path}",
                "display": f"❌ Not a directory: `{path}`"}

    try:
        entries = sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))
        lines = []
        for name in entries:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                lines.append(f"[DIR]  {name}/")
            else:
                try:
                    size = os.path.getsize(full)
                    size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB" if size < 1048576 else f"{size/1048576:.1f} MB"
                except OSError:
                    size_str = "?"
                lines.append(f"[FILE] {name}  ({size_str})")
        result = f"Contents of {path} ({len(entries)} items):\n" + "\n".join(lines)
        return {"success": True, "result": result,
                "display": f"📁 Listed directory: `{path}` ({len(entries)} items)"}
    except PermissionError:
        return {"success": False, "result": f"Permission denied: {path}",
                "display": f"❌ Permission denied: `{path}`"}


# ── run_python ─────────────────────────────────────────────────────────────────

def _run_python(args: dict) -> dict:
    code = args.get("code", "").strip()
    if not code:
        return {"success": False, "result": "No code provided.",
                "display": "❌ Run failed — no code provided."}

    # Fix #9: initialise tmp_path before the try block so the finally
    # clause never raises NameError if NamedTemporaryFile itself fails.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            output = stdout or "(no output)"
            return {
                "success": True,
                "result": output,
                "display": f"🐍 Python executed successfully",
            }
        else:
            error_msg = stderr or stdout or "Unknown error"
            return {
                "success": False,
                "result": f"Script exited with code {result.returncode}:\n{error_msg}",
                "display": f"❌ Python error (exit {result.returncode})",
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "result": "Execution timed out after 30 seconds.",
                "display": "❌ Python execution timed out (30s limit)"}
    except Exception as exc:
        return {"success": False, "result": f"Failed to run code: {exc}",
                "display": f"❌ Run failed: {exc}"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
