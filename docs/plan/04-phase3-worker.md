# Phase 3 — The Propagation Worker

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **3.1–3.4**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.
>
> The Q&A mechanism §5.4 invoked from 3.2 lives in [05-qa-routing.md](./05-qa-routing.md).

---

## Phase 3 — The Propagation Worker

### 3.1 Worker (D7)

`propagation/worker.py`

- Single daemon thread started once by the C00 application factory, polling
  `propagation_jobs` for `state='queued'` ordered by `(change_id, depth)`.
- Claims a job with a guarded `UPDATE ... WHERE state='queued'` so a future
  multi-worker setup stays correct.
- **Never processes depth N+1 until all depth N jobs for that change are
  terminal** (D4) — QA must read the updated data model.
- Per-job: bounded retries (`attempts`), timeout, and `tokens_used` accounting
  against the project budget (D14). Budget exceeded → remaining jobs
  `state='cancelled'` with a clear reason.
- **On depth completion:** post the question digest for that depth (D21) before
  starting the next depth. A depth is complete when every job in it is terminal
  (`applied`/`proposed`/`failed`/`needs_input`/`cancelled`).
- Graceful shutdown flag; `close_thread_connection()` after each job (0.4).
- **Reload-safety:** Flask debug mode runs two processes. Guard on
  `WERKZEUG_RUN_MAIN` so only one worker starts.

### 3.2 Agent invocation

Each job creates a **real conversation row** (in the project folder, titled
e.g. `[auto] DB-MODEL ← CHG-0007`) so the entire agent run is inspectable in
the normal chat UI. This is the audit trail — no separate log format needed.

System prompt assembled from:

1. Base instructions + persona prompt (extract/reuse the current prompt-building
  logic in `app.py`; no `prompt_builder.py` exists yet).
2. Scoped upstream context via `build_artifact_context()` (0.6 / D13).
3. The change summary and diff.
4. The current content of the artifact being updated.
5. Explicit instructions: preserve unaffected sections verbatim, update
   `derives_from`, bump `version`, set `origin: propagation`, call
   `ask_question` when a requirement is genuinely ambiguous rather than guessing
   — choosing `blocking` correctly (D20) and leaving an inline `⚠️ ASSUMPTION`
   marker for non-blocking cases — `raise_issue` for a risk or suggestion (D12),
   and `propose_artifact` if a new document is genuinely needed (D17), never
   writing one unregistered.
6. **Previously clarified** answers for the requirements in scope (D22), so the
   same question is not asked twice.

Runs via `run_chat_turn()` with the `propagation` tool profile (0.5) and
`max_iterations`.

**Whole-artifact rewrite (D15).** The agent returns the complete new document via
`write_artifact`, not a patch. Required guards, because a full rewrite can lose
content silently:

- **Front-matter is re-applied by the server**, not trusted from the model.
  `version`, `origin` and `derives_from` are computed; the model's values are
  discarded. Models are unreliable at incrementing counters.
- **Shrink guard:** reject a rewrite that drops below a configurable fraction
  (default 60%) of the original length unless the change event explicitly removed
  a requirement — the classic failure is a model returning a summary instead of
  the document.
- **Heading-retention check:** every `##` heading present before must still be
  present, or the job goes to `needs_review` with the diff highlighted.
- **Assumption-marker retention:** unresolved `⚠️ ASSUMPTION (Q-nnnn)` markers
  must survive a rewrite unless their question has been answered (D20). A rewrite
  that silently drops one is turning visible debt back into invisible drift.
- **Requirement-reference check:** IDs the artifact traced to before must still
  appear unless the change removed them.

**Same-role batching (D16).** All artifacts owned by one role for one change event
are updated **sequentially inside a single conversation**, so artifact 2 sees
artifact 1's updated text. This is what prevents two QA documents from
contradicting each other, and it amortises the persona/context tokens across the
batch.

**Propagation may never create or delete artifacts** (1.3) — only update ones
already registered. A `write_artifact` call targeting an unregistered key is an
error, not an implicit create.

### 3.3 Proposal vs auto-apply (D5)

| Mode | Behaviour |
|---|---|
| `off` | No detection, no jobs. **Default for adopted projects** (Q3). |
| `notify` | Detect and mark `stale`; badge in UI; no LLM spend. |
| `propose` | **Default for new projects** (Q3). Write to `.agents/proposals/CHG-nnnn/`; `state='proposed'`. |
| `auto` | Write directly to the artifact, commit, `state='applied'`. |

Apply/reject is atomic per change event so a wave can be rolled back as a unit
(D8).

### 3.4 Loop prevention (D6)

- Propagation-authored writes carry `origin: propagation` → the scanner never
  opens a new change chain from them.
- `max_depth` cap per change event.
- Regression test: a propagation run must produce **zero** new change events.

**Acceptance tests:**

- Ordering: with `fake_llm`, assert `QA-PLAN`'s prompt contains the *updated*
  `DB-MODEL` text.
- **Same-role batching:** two artifacts owned by QA share one conversation, and
  the second prompt contains the first's updated output.
- **Shrink guard:** a scripted response returning a one-line summary is rejected,
  not written.
- **Heading retention:** a response dropping a `##` section → `needs_review`.
- **Front-matter is server-authored:** a model returning `version: 99` still
  results in a correct sequential bump.
- **No implicit create:** `write_artifact` with an unregistered key errors.
- Failure isolation: one agent erroring leaves siblings `applied` and the failed
  one `failed` with a message; downstream dependants are `blocked`, not silently
  skipped.
- Idempotence: re-running the same change event produces no duplicate jobs.
- Budget: a low token cap cancels remaining jobs cleanly.
- Kill switch mid-wave leaves no job stuck in `running`.
