#!/usr/bin/env python
"""
scripts/perf_profiler.py — profile the hot paths behind managed-project chat
responses and watch a synthetic propagation wave flow through the worker.

Anti-stall guarantees (mirrors the repo rules in to_do.md §0.7):

- Runs entirely against a throwaway SQLite DB + temp workspace (no .env,
  no chat.db, no network, no provider calls).
- Every loop is bounded (fixed deadlines) — nothing can hang.
- Does NOT start ``app.py``; the Flask app factory is used in-process only.

What it measures:

1. Per-turn linked-folder context build (called on EVERY chat message and every
   regenerate by ``_build_system_prompt``) — cold vs cached, plus the byte
   size that is shipped to the model each message.
2. Change-detection scan time (``scan_project``).
3. A "watch the agents" timeline: provoke a BA-REQ edit, let impact analysis
   queue the downstream jobs, then watch a background worker claim and complete
   them in dependency order in real time (fake executor — no LLM).

Run:  venv/bin/python -m scripts.perf_profiler
"""

import os
import sys
import time
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import database as db_module
import file_handler as file_handler_module
from app import create_app, _build_system_prompt

ENCRYPTION_KEY = "test-encryption-key-must-be-32-bytes-long-!!!!"

FRONT_MATTER = (
    "---\nartifact_id: %s\nrole: %s\nversion: 1\norigin: human\nderives_from: []\n---\n"
)


def format_seconds(seconds: float) -> str:
    if seconds >= 1.0:
        return f"{seconds:8.3f}s"
    if seconds >= 0.001:
        return f"{seconds * 1000:8.2f}ms"
    return f"{seconds * 1_000_000:7.1f}µs"


def make_app():
    tmp_root = tempfile.mkdtemp(prefix="chatperf-")
    db_uri = f"sqlite:///{os.path.join(tmp_root, 'perf.db')}"
    app = create_app(config={
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "ENCRYPTION_KEY": ENCRYPTION_KEY,
        "SKIP_DOTENV_WRITE": "1",
    })
    return app, tmp_root


def create_sample_project(app, root):
    """Create a managed project (create_project_team) and fatten the workspace."""
    output_root = os.path.join(root, "output")
    os.makedirs(output_root, exist_ok=True)

    with app.app_context():
        db_module.set_setting("output_dir", output_root)
        team = db_module.create_project_team("Performance Demo")
        project_id = team["project"]["id"]
        workspace = team["workspace_dir"]
        ba_conv = next(c for c in team["conversations"] if c["title"] == "Business Analyst")

        # A large reference document so the per-turn read is measurable.
        big = os.path.join(workspace, "Design", "REFERENCE.md")
        with open(big, "w", encoding="utf-8") as f:
            f.write("# Reference\n\n")
            f.write("filler line for the big linked reference document\n" * 220_000)

        # Give BA-REQ a real REQ-001 heading and commit it as the baseline.
        req_path = os.path.join(workspace, "Requirements", "BA-REQ.md")
        v1_content = (
            FRONT_MATTER % ("BA-REQ", "Business Analyst")
            + "## REQ-001 — Manage users\n\n"
            "The application must let an admin create and deactivate users.\n"
        )
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(v1_content)

        # Trace REQ-001 to every downstream artifact so impact analysis can
        # resolve what must be regenerated (D3).
        for key in ["UX-WIRE", "DB-MODEL", "SEC-RISK", "QA-PLAN", "PM-PLAN"]:
            db_module.create_artifact_trace(project_id, key, "REQ-001")

        from git_integration import git_commit_all
        assert git_commit_all(workspace, "baseline").success, "git baseline commit failed"

        return {
            "project_id": project_id,
            "workspace": workspace,
            "ba_conversation": ba_conv,
            "ba_conv_id": ba_conv["id"],
            "req_path": req_path,
        }


def benchmark_context_build(app, sample):
    """Time the per-turn system-prompt / linked-folder context build."""
    print("=" * 78)
    print("1) Per-turn prompt build  (runs once per message + per regenerate)")
    print("=" * 78)

    with app.app_context():
        # A) The scoped per-role prompt now used for project role conversations.
        db_conv = next(
            c for c in db_module.list_conversations()
            if c["folder_id"] == sample["project_id"] and c["title"] == "Database Developer"
        )
        scoped_prompt, _ = _build_system_prompt(db_conv, db_conv["id"], tools_on=True)
        print(f"  scoped per-role system prompt (Database Developer): "
              f"{len(scoped_prompt):,} chars (~{len(scoped_prompt) // 4:,} tokens)")

        # B) The legacy whole-workspace linked-folder dump (still used for
        #    non-role chats, and what project chats used before this change).
        linked_folders = db_module.list_linked_folders(db_conv["id"])
        conv_files = db_module.list_conv_files(db_conv["id"])

        def timed(label, n=3):
            best = None
            for _ in range(n):
                start = time.perf_counter()
                file_handler_module.build_linked_folder_context(linked_folders, conv_files)
                elapsed = time.perf_counter() - start
                best = elapsed if best is None else min(best, elapsed)
            print(f"  {label:<46} {format_seconds(best)}")
            return best

        cold = timed("legacy whole-workspace build (cold)", n=1)
        warm = timed("legacy whole-workspace build (cached)", n=5)

        legacy, legacy_chars = file_handler_module.build_linked_folder_context(
            linked_folders, conv_files,
        )
        print(f"\n  Legacy context shipped to the model: {legacy_chars:,} chars "
              f"(~{legacy_chars // 4:,} tokens) — this is what role chats sent "
              f"before scoping")
        print(f"  Saved by scoping: {legacy_chars - len(scoped_prompt):,} chars "
              f"per message (~{(legacy_chars - len(scoped_prompt)) // 4:,} tokens)")

        # Now touch a single file and measure the rebuild cost of the cache.
        big = os.path.join(sample["workspace"], "Design", "REFERENCE.md")
        with open(big, "a", encoding="utf-8") as f:
            f.write("# touched\n")
        st = os.stat(big)
        os.utime(big, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        reheat = timed("legacy rebuild after one file edit", n=1)

        print(f"\n  Cache speedup vs cold: {cold / warm:.1f}x  |  "
              f"rebuild-after-edit vs cold: {cold / max(reheat, 1e-9):.1f}x")
    return cold, warm


def benchmark_scan(app, sample):
    print("\n" + "=" * 78)
    print("2) Change-detection scan (scan_project, zero LLM)")
    print("=" * 78)
    from propagation.scanner import scan_project

    with app.app_context():
        start = time.perf_counter()
        result = scan_project(sample["project_id"])
        elapsed = time.perf_counter() - start
        print(f"  scanned {len(result.artifacts)} artifacts in {format_seconds(elapsed)}")
        print(f"  summary: {result.summary}")
    return elapsed


def watch_agents(app, sample):
    """
    Simulate a BA edit, queue the affected downstream jobs, and watch the
    worker complete them in dependency order in real time (fake executor).
    """
    print("\n" + "=" * 78)
    print("3) Watch the agents (synthetic wave, fake executor — no LLM)")
    print("=" * 78)

    project_id = sample["project_id"]

    # 3a. Human-style edit of the requirement body.
    with app.app_context():
        req = sample["req_path"]
        with open(req, "r", encoding="utf-8") as f:
            base = f.read()
        with open(req, "w", encoding="utf-8") as f:
            f.write(base + "\nThe workflow must warn before deactivating a user.\n")
        st = os.stat(req)
        os.utime(req, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

        from propagation.changes import scan_and_create_change_event
        event = scan_and_create_change_event(project_id, "BA-REQ")
        assert event, "change detection found no requirement change"
        print(f"  change event CHG-{event['id']:04d} • changed: "
              f"{event.get('changed_reqs')}")

        from propagation.impact import queue_propagation_jobs
        jobs = queue_propagation_jobs(
            project_id,
            event["id"],
            event.get("changed_reqs") or [],
            event.get("removed_reqs") or [],
        )
        print(f"  queued {len(jobs)} downstream jobs")

    # 3b. Start the worker with a fake executor and poll for a bounded time.
    from propagation.worker import PropagationWorker

    class FakeExecutiveAgent:
        """Pretends to regenerate an artifact — sleeps a little, 'completes'."""

        def execute(self, job):
            time.sleep(0.03)
            return {
                "state": "completed",
                "output": f"simulated rewrite of {job['artifact_key']}",
            }

    worker = PropagationWorker(app, executor=FakeExecutiveAgent(), poll_interval=0.005)
    deadline = time.time() + 15
    worker.start()
    try:
        previous = {}
        while time.time() < deadline:
            with app.app_context():
                snapshot = []
                for ev in db_module.list_change_events(project_id):
                    for job in db_module.list_propagation_jobs(ev["id"]):
                        snapshot.append((job["artifact_key"], job["state"]))
                terminal = all(
                    state in ("completed", "proposed", "applied", "failed",
                              "needs_input", "cancelled", "rejected")
                    for _, state in snapshot
                )
                if snapshot != previous:
                    line = ", ".join(f"{key:<9}{state}" for key, state in snapshot)
                    print(f"    t={time.time() - (deadline - 15):5.1f}s  {line}")
                    previous = snapshot
                if snapshot and terminal:
                    print("  all downstream jobs terminal ✔")
                    break
            time.sleep(0.05)
        else:
            print("  TIMEOUT waiting for jobs to settle (loop is bounded at 15s)")
    finally:
        worker.stop()


def main() -> None:
    app, root = make_app()
    sample = create_sample_project(app, root)

    cold, warm = benchmark_context_build(app, sample)
    benchmark_scan(app, sample)
    watch_agents(app, sample)

    print("\n" + "=" * 78)
    print(f"cache speedup: {cold / warm:.1f}x  |  sample workspace: {root}")
    print("= All measurements used a throwaway DB/workspace — nothing shared. =")
    print("=" * 78)


if __name__ == "__main__":
    main()