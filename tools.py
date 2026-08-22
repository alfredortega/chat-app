"""
Tool definitions and execution for function-calling.
Tools: write_file, read_file, list_directory, run_python
"""

import os
import json
import subprocess
import sys
import tempfile
import database as db

# ── Tool schema (sent to the model) ───────────────────────────────────────────

TOOLS = [
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


# ── Tool execution ─────────────────────────────────────────────────────────────

def execute_tool_call(name: str, arguments_json: str, output_dir: str = None) -> dict:
    """
    Execute a named tool call.

    ``output_dir`` is the *effective* output directory for the current
    conversation (conversation override → app default, already resolved by
    the caller).  When supplied it takes precedence over the DB setting so
    that per-conversation output directories are honoured.
    """
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "result": f"Invalid arguments JSON: {exc}",
            "display": "❌ Tool call failed — could not parse arguments.",
        }

    if name == "write_file":
        return _write_file(args, output_dir=output_dir)
    if name == "read_file":
        return _read_file(args)
    if name == "list_directory":
        return _list_directory(args)
    if name == "run_python":
        return _run_python(args)

    return {
        "success": False,
        "result": f"Unknown tool: {name}",
        "display": f"❌ Unknown tool '{name}'.",
    }


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
        # Absolute path: must be within the allowed output directory.
        if effective_dir:
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
