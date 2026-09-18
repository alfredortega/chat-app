"""
Propagation worker (C15).

A single daemon thread polls ``propagation_jobs``, claims each job with a
guarded ``UPDATE`` (so a future multi-worker setup stays correct), and never
processes depth N+1 until every depth-N job for the same change is terminal
(D4). No LLM invocation happens here yet -- the executor is injected (a
no-op executor by default). Token budgeting arrives in C19, question digests
in C18, and ``app.py`` wiring in C20.
"""

import threading
from database import (
    PropagationJob,
    db,
    list_propagation_jobs,
    update_propagation_job,
    create_propagation_job,
    run_with_session,
)

QUEUEABLE_STATES = ("pending", "queued")
RUNNING_STATE = "running"
TERMINAL_STATES = (
    "applied",
    "proposed",
    "failed",
    "needs_input",
    "cancelled",
    "completed",
    "rejected",
)

MAX_ATTEMPTS = 3


class NoopExecutor:
    """A safe default executor that performs no LLM/agent work (C15)."""

    def execute(self, job: dict) -> dict:
        return {"state": "completed", "output": "noop"}


def should_start_worker() -> bool:
    """
    Reload-safety guard: Flask's debug reloader runs two processes.

    Only the main ``WERKZEUG_RUN_MAIN`` process should start the worker so
    that jobs are not claimed twice.
    """
    value = __import__("os").environ.get("WERKZEUG_RUN_MAIN")
    # When the var is absent (production / tests) we treat the process as the
    # main one and allow the worker to start. In debug mode the reloader sets
    # it to "true" only in the reloaded child.
    return value is None or value.lower() == "true"


def recover_stuck_running() -> int:
    """
    Reset any ``running`` jobs back to ``pending`` (restart/crash safety).

    Returns the number of jobs recovered.
    """
    rows = PropagationJob.query.filter_by(state=RUNNING_STATE).all()
    count_reset = 0
    for row in rows:
        row.state = "pending"
        count_reset += 1
    if count_reset:
        db.session.commit()
    return count_reset


def _claim_job(change_id: int | None = None, depth: int | None = None) -> dict | None:
    """
    Atomically claim the next queueable job.

    Uses a guarded ``UPDATE ... WHERE state IN (pending, queued)`` so that two
    workers can never claim the same job.
    """
    query = PropagationJob.query.filter(
        PropagationJob.state.in_(QUEUEABLE_STATES)
    )
    if change_id is not None:
        query = query.filter(PropagationJob.change_id == change_id)
    if depth is not None:
        query = query.filter(PropagationJob.depth == depth)
    job = query.order_by(
        PropagationJob.change_id.asc(),
        PropagationJob.depth.asc(),
        PropagationJob.artifact_key.asc(),
    ).first()
    if job is None:
        return None

    # Guarded claim.
    claimed = (
        PropagationJob.query.filter(
            PropagationJob.id == job.id,
            PropagationJob.state.in_(QUEUEABLE_STATES),
        ).update({"state": RUNNING_STATE})
    )
    db.session.commit()
    if claimed:
        return job.to_dict()
    return None


def _depth_gated(project_id: int, change_id: int, depth: int) -> bool:
    """
    Never process depth N until every depth < N job for this change is terminal
    (D4). Returns True when it is safe to run the job now.
    """
    jobs = list_propagation_jobs(change_id)
    for job in jobs:
        if job["depth"] < depth and job["state"] not in TERMINAL_STATES:
            return False
    return True


class PropagationWorker:
    """Poll the job table and execute jobs via an injected executor.

    Polling is lazy: the loop runs fast while there is work to do and backs
    off to ``idle_backoff`` seconds when the queue is empty, so an idle worker
    does not hammer the database at ``poll_interval`` forever.
    """

    def __init__(
        self,
        app,
        executor=None,
        poll_interval: float = 0.2,
        idle_backoff: float = 1.0,
    ):
        self.app = app
        self.executor = executor or NoopExecutor()
        self.poll_interval = poll_interval
        self.idle_backoff = idle_backoff
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _run_loop(self) -> None:
        delay = self.poll_interval
        while not self._stop_event.is_set():
            if self._stop_event.wait(delay):
                break
            try:
                busy = run_with_session(self.app, self._tick)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                busy = False
            delay = self.poll_interval if busy else self.idle_backoff

    def _tick(self) -> bool:
        """
        Claim and run a single job.

        Returns True when a job was processed (fast-poll again), False when
        there was nothing to do or the job was depth-gated (back off).
        """
        job = self._next_job()
        if job is None:
            return False
        job_id = job["id"]
        change_id = job["change_id"]
        depth = job["depth"]
        project_id = self._project_id_for_change(change_id)

        # Attempts-based bounded retries.
        attempts = job.get("attempts", 0)
        if attempts >= MAX_ATTEMPTS:
            update_propagation_job(job_id, state="failed", error="Max attempts exceeded")
            return True

        if not _depth_gated(project_id, change_id, depth):
            # Release the job so it can be re-claimed later.
            update_propagation_job(job_id, state="pending")
            return False

        try:
            result = self.executor.execute(job)
            update_propagation_job(
                job_id,
                state=result.get("state", "completed"),
                attempts=attempts + 1,
            )
        except Exception as exc:
            update_propagation_job(
                job_id,
                state="failed",
                error=str(exc),
                attempts=attempts + 1,
            )
        return True

    def _next_job(self) -> dict | None:
        return _claim_job()

    def _project_id_for_change(self, change_id: int) -> int:
        from database import ChangeEvent
        row = db.session.get(ChangeEvent, change_id)
        return row.project_id if row else 0


def run_all_pending(app, executor=None) -> list[dict]:
    """
    Convenience helper: run every pending job to completion using ``executor``.

    Used by tests to drive the worker synchronously without a background
    thread. Returns the final job rows.
    """
    worker = PropagationWorker(app, executor=executor, poll_interval=0.001)
    with app.app_context():
        while True:
            job = _claim_job()
            if job is None:
                break
            change_id = job["change_id"]
            project_id = worker._project_id_for_change(change_id)
            if not _depth_gated(project_id, change_id, job["depth"]):
                update_propagation_job(job["id"], state="pending")
                break
            try:
                result = worker.executor.execute(job)
                update_propagation_job(
                    job["id"],
                    state=result.get("state", "completed"),
                    attempts=job.get("attempts", 0) + 1,
                )
            except Exception as exc:
                update_propagation_job(
                    job["id"], state="failed", error=str(exc),
                    attempts=job.get("attempts", 0) + 1,
                )
        final = _current_jobs()
    return final


def _current_jobs() -> list[dict]:
    rows = PropagationJob.query.order_by(
        PropagationJob.depth.asc(), PropagationJob.artifact_key.asc()
    ).all()
    return [r.to_dict() for r in rows]