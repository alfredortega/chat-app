"""
Git integration for project workspace versioning.

Provides git operations for the project workspace: init, commit, diff, rollback.
Git is a hard requirement (D8) — every project workspace is a git repository.
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Tuple, List
from dataclasses import dataclass


@dataclass
class GitResult:
    """Result of a git operation."""
    success: bool
    output: str = ""
    error: str = ""


class GitNotAvailableError(Exception):
    """Raised when git is not available on the system."""
    pass


class GitCommandError(Exception):
    """Raised when a git command fails."""
    def __init__(self, command: str, stdout: str, stderr: str, returncode: int):
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"Git command failed: {command}\nstdout: {stdout}\nstderr: {stderr}")


def check_git_available() -> bool:
    """Check if git is available on the system."""
    return shutil.which("git") is not None


def ensure_git_available() -> None:
    """Ensure git is available, raise GitNotAvailableError if not."""
    if not check_git_available():
        raise GitNotAvailableError(
            "Git is not installed or not in PATH. "
            "Git is a hard requirement for managed projects (D8)."
        )


def run_git_command(repo_path: str, *args: str) -> GitResult:
    """
    Run a git command in the specified repository.

    Args:
        repo_path: Path to the git repository.
        *args: Git command arguments (e.g., "status", "commit", "-m", "msg").

    Returns:
        GitResult with success, output, and error.
    """
    ensure_git_available()
    repo_path = os.path.abspath(repo_path)

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitResult(
            success=result.returncode == 0,
            output=result.stdout.strip(),
            error=result.stderr.strip(),
        )
    except subprocess.TimeoutExpired:
        return GitResult(
            success=False,
            error=f"Git command timed out: git {' '.join(args)}",
        )
    except Exception as e:
        return GitResult(
            success=False,
            error=f"Failed to run git command: {e}",
        )


def git_init(repo_path: str) -> GitResult:
    """Initialize a new git repository."""
    result = run_git_command(repo_path, "init")
    if result.success:
        # Configure user for commits (required for commit to work)
        run_git_command(repo_path, "config", "user.name", "Artifact Propagation")
        run_git_command(repo_path, "config", "user.email", "propagation@local")
    return result


def git_status(repo_path: str) -> GitResult:
    """Get git status."""
    return run_git_command(repo_path, "status", "--porcelain")


def git_add(repo_path: str, pathspec: str = ".") -> GitResult:
    """Add files to staging area."""
    return run_git_command(repo_path, "add", pathspec)


def git_commit(repo_path: str, message: str) -> GitResult:
    """Create a commit with the given message."""
    return run_git_command(repo_path, "commit", "-m", message)


def git_commit_all(repo_path: str, message: str) -> GitResult:
    """Stage all changes and commit."""
    add_result = git_add(repo_path, ".")
    if not add_result.success:
        return add_result
    return git_commit(repo_path, message)


def git_diff(repo_path: str, cached: bool = False) -> GitResult:
    """Show diff of changes."""
    args = ["diff"]
    if cached:
        args.append("--cached")
    return run_git_command(repo_path, *args)


def git_diff_file(repo_path: str, file_path: str, cached: bool = False) -> GitResult:
    """Show diff for a specific file."""
    args = ["diff"]
    if cached:
        args.append("--cached")
    args.extend(["--", file_path])
    return run_git_command(repo_path, *args)


def git_log(repo_path: str, max_count: int = 10) -> GitResult:
    """Show recent commit log."""
    return run_git_command(repo_path, "log", f"--oneline", f"-{max_count}")


def git_show(repo_path: str, commit: str = "HEAD") -> GitResult:
    """Show commit details."""
    return run_git_command(repo_path, "show", commit)


def git_reset_hard(repo_path: str, commit: str = "HEAD") -> GitResult:
    """Hard reset to a commit (discards all changes)."""
    return run_git_command(repo_path, "reset", "--hard", commit)


def git_checkout(repo_path: str, commit: str, file_path: str = None) -> GitResult:
    """Checkout a commit or specific file from a commit."""
    args = ["checkout", commit]
    if file_path:
        args.extend(["--", file_path])
    return run_git_command(repo_path, *args)


def git_get_head_commit(repo_path: str) -> Optional[str]:
    """Get the current HEAD commit hash."""
    result = run_git_command(repo_path, "rev-parse", "HEAD")
    if result.success:
        return result.output.strip()
    return None


def git_list_files(repo_path: str) -> List[str]:
    """List all tracked files in the repository."""
    result = run_git_command(repo_path, "ls-files")
    if result.success:
        return result.output.strip().split("\n") if result.output.strip() else []
    return []


def git_is_repo(repo_path: str) -> bool:
    """Check if a directory is a git repository."""
    git_dir = os.path.join(repo_path, ".git")
    return os.path.isdir(git_dir)


def init_project_workspace(workspace_dir: str) -> GitResult:
    """
    Initialize a project workspace as a git repository.

    Creates .git directory, initial commit with empty project structure.

    Args:
        workspace_dir: Path to the project workspace.

    Returns:
        GitResult of the init operation.
    """
    os.makedirs(workspace_dir, exist_ok=True)

    # Check if already a repo
    if git_is_repo(workspace_dir):
        return GitResult(success=True, output="Already a git repository")

    # Initialize
    result = git_init(workspace_dir)
    if not result.success:
        return result

    # Create initial directory structure
    subdirs = [
        "Requirements",
        "Design",
        "Data",
        "Test Cases",
        "Security",
        "Project Plan",
        ".agents",
        ".agents/changes",
        ".agents/proposals",
    ]
    for subdir in subdirs:
        os.makedirs(os.path.join(workspace_dir, subdir), exist_ok=True)

    # Create .gitignore
    gitignore_path = os.path.join(workspace_dir, ".gitignore")
    with open(gitignore_path, "w") as f:
        f.write("""# Artifact Propagation
.agents/proposals/*
!.agents/proposals/.gitkeep
""")
    
    # Create .gitkeep files for empty directories
    for subdir in [".agents/proposals", ".agents/changes"]:
        gitkeep = os.path.join(workspace_dir, subdir, ".gitkeep")
        with open(gitkeep, "w") as f:
            f.write("")

    # Initial commit
    return git_commit_all(workspace_dir, "Initial project structure")


def create_wave_commit(workspace_dir: str, change_summary: str, changed_files: List[str] = None) -> GitResult:
    """
    Create a commit for a propagation wave.

    Args:
        workspace_dir: Project workspace path.
        change_summary: Summary of changes for commit message.
        changed_files: Optional list of specific files to commit.

    Returns:
        GitResult of the commit operation.
    """
    if changed_files:
        # Add only specified files
        for f in changed_files:
            result = git_add(workspace_dir, f)
            if not result.success:
                return result
    else:
        # Add all changes
        result = git_add(workspace_dir, ".")
        if not result.success:
            return result

    commit_msg = f"Propagation wave: {change_summary}"
    return git_commit(workspace_dir, commit_msg)


def rollback_to_commit(workspace_dir: str, commit_hash: str) -> GitResult:
    """
    Rollback workspace to a specific commit.

    Args:
        workspace_dir: Project workspace path.
        commit_hash: Commit hash to rollback to.

    Returns:
        GitResult of the rollback operation.
    """
    return git_reset_hard(workspace_dir, commit_hash)


def rollback_file(workspace_dir: str, commit_hash: str, file_path: str) -> GitResult:
    """
    Rollback a single file to its state at a specific commit.

    Args:
        workspace_dir: Project workspace path.
        commit_hash: Commit hash to restore from.
        file_path: Path to the file (relative to workspace root).

    Returns:
        GitResult of the checkout operation.
    """
    return git_checkout(workspace_dir, commit_hash, file_path)


def get_wave_history(workspace_dir: str, max_waves: int = 20) -> List[dict]:
    """
    Get history of propagation waves (commits with propagation prefix).

    Args:
        workspace_dir: Project workspace path.
        max_waves: Maximum number of waves to return.

    Returns:
        List of wave commit info dicts.
    """
    result = run_git_command(
        workspace_dir,
        "log",
        f"--oneline",
        f"-{max_waves}",
        "--grep=Propagation wave",
    )
    if not result.success:
        return []

    waves = []
    for line in result.output.strip().split("\n"):
        if line:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                waves.append({"commit": parts[0], "message": parts[1]})
    return waves


# ── Cascade/Lifecycle cleanup (C09) ────────────────────────────────────────────

def cleanup_on_folder_delete(project_id: int, workspace_dir: str) -> dict:
    """
    Cleanup when a project folder is deleted.

    Per D9: never delete user documents from sidebar action; warn instead.
    This function should be called before deleting a project folder.

    Args:
        project_id: The folder/project ID.
        workspace_dir: The workspace directory path.

    Returns:
        Dict with cleanup status and warnings.
    """
    warnings = []
    actions = []

    if os.path.exists(workspace_dir):
        if git_is_repo(workspace_dir):
            warnings.append(
                f"Project workspace at {workspace_dir} will NOT be deleted. "
                "Git repository preserved per D9 policy."
            )
        else:
            warnings.append(
                f"Project workspace at {workspace_dir} is not a git repository. "
                "Consider manual review before deletion."
            )
    else:
        warnings.append("Workspace directory does not exist.")

    return {
        "project_id": project_id,
        "workspace_preserved": True,
        "warnings": warnings,
        "actions": actions,
    }


def cleanup_on_purge() -> dict:
    """
    Cleanup when all conversations are purged.

    Per D9: clear jobs/changes or explicitly preserve them.
    This should be called from database.purge_all_conversations().
    """
    return {
        "message": "Purge completed. Project workspaces (git repositories) preserved per D9 policy.",
        "preserved": ["artifacts", "change_events", "propagation_jobs"],
    }


def verify_git_on_startup() -> Tuple[bool, str]:
    """
    Verify git is available on application startup.

    Returns:
        (available, message) tuple.
    """
    if check_git_available():
        return True, "Git available"
    return False, "Git not found in PATH — managed projects will not work. Install git to enable propagation features."