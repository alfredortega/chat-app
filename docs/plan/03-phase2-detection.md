# Phase 2 — Change Detection & Impact Analysis

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **2.1–2.4**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.

---

## Phase 2 — Change Detection & Impact Analysis

### 2.1 Artifact scanner

`propagation/scanner.py`

- `scan_project(project_id)` — walk the workspace, parse front-matter, compare
  `content_hash` against the `artifacts` index.
- Classify each artifact: `unchanged` / `modified` / `new` / `deleted`.
- **Index inline assumption markers** (D20) into `artifact_assumptions` on every
  scan, so the panel count is derived from the documents themselves rather than
  from job history. If a human deletes a marker by hand, the count must follow.
- Triggered on: project panel open, explicit "Check for changes", and after any
  `write_artifact` tool call.

### 2.2 Change event creation

- On a `modified` BA artifact with `origin: human`:
  - Compute a unified diff against the last committed version (git).
  - Extract the set of changed `REQ-nnn` IDs from the diff hunks.
  - Ask the LLM for **one thing only**: a one-paragraph plain-language change
    summary. All routing decisions stay deterministic (D3).
  - Insert a `change_events` row.
- **Debounce** (2 min default, configurable): three BA saves in a minute must
  produce one propagation wave, not three. Implemented by coalescing into the
  most recent unpropagated `change_events` row for that `source_key`.

### 2.3 Impact analysis — deterministic

```sql
SELECT DISTINCT artifact_key FROM artifact_traces
WHERE project_id = ? AND req_id IN (<changed ids>);
```

Union with all transitive downstream nodes from `artifact_deps`, then sort
topologically to assign each job a `depth`. Artifacts with no intersecting
requirement IDs are **not** queued — that is the whole point of D3.

**Selection must be artifact-level, not role-level (D16).** This is the single
most important correctness *and* cost property in the design: if a change to
`REQ-014` queues every artifact a role owns, granularity turns from a saving into
a multiplier. Test against a multi-artifact fixture where a role owns 4 artifacts
and only 1 traces to the changed requirement.

Also handle:

- **Deleted requirement** — downstream artifacts referencing a now-missing
  `REQ-nnn` must be queued with an explicit "requirement removed" instruction,
  not left silently stale.
- **Renumber detected** (1.2) — abort with a validation error; do not propagate.
- **Trace coverage report** — surface requirements traced by no artifact, and
  artifacts tracing to no requirement. Both are latent bugs.

### 2.4 Conflict detection

If a downstream artifact's current `content_hash` differs from the hash recorded
at its last propagation, a human has hand-edited it. Do **not** overwrite:
mark `status='conflict'` and require an explicit merge decision in the UI.

**Acceptance tests:**

- Golden scenario: editing `REQ-014` queues exactly `DB-MODEL`, `UX-WIRE`,
  `SEC-RISK`, `QA-PLAN`, `PM-PLAN` with depths `1,1,1,2,3`.
- **Granularity (D16):** in a fixture where QA owns 4 artifacts and only
  `QA-PLAN-DATA` traces to `REQ-014`, exactly one QA artifact is queued.
- Editing a requirement referenced only by `QA-PLAN` queues only `QA-PLAN`
  (and `PM-PLAN` downstream of it).
- Whitespace-only edit to the BA doc queues nothing.
- Hand-edited downstream artifact → `conflict`, not overwritten.
- Three rapid saves → one change event.
- Duplicate `REQ-nnn` in the BA doc → validation error, nothing queued.
- Renumbered requirement → validation error, nothing queued.
- Deleted requirement → dependants queued with the removal instruction.
