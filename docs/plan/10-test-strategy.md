# Phase 8 — Test Strategy

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **8.1–8.4**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.

---

## Phase 8 — Test Strategy

### 8.1 Layers

| Layer | Scope | Network |
|---|---|---|
| Unit | front-matter parser, `REQ` extractor + validator, ID allocator, topo sort, diff, path jail, hashing, shrink guard, heading retention, **assumption-marker parser** | none |
| Integration | scanner → change event → impact → job queue → worker → proposal, via `fake_llm` | none |
| **Granularity** | multi-artifact fixture (4 artifacts per role) proving artifact-level selection and same-role batching | none |
| API | every Phase 4 route, happy/404/409 | none |
| Characterisation | existing chat & regenerate SSE output unchanged after 0.3 | none |
| Golden scenario | full SQLite→MySQL wave with scripted responses | none |
| Live smoke | `scripts/live_propagation_smoke.py` — real endpoint, manual, opt-in | yes |
| Manual UI | `tests/manual/propagation_checklist.md`, both themes | yes |

Before these propagation layers, add a compatibility layer covering the
existing application: `create_app()` isolation, legacy conversation chat and
regeneration SSE, settings, personas, folders, uploads, linked folders, and
1.0 export/import. These tests must remain green throughout the propagation
work.

### 8.2 Golden-scenario test in detail

`tests/test_golden_sqlite_to_mysql.py`

1. Build a managed project from `examples/sqlite-to-mysql/before/` using the
  new project flow.
2. Register artifacts and deps; assert all `current`.
3. Overwrite `REQ-014` with the MySQL + SQLAlchemy text.
4. `scan_project()` → assert exactly one change event, `changed_reqs == ['REQ-014']`.
5. Assert queued jobs and depths: `DB-MODEL/UX-WIRE/SEC-RISK`=1, `QA-PLAN`=2, `PM-PLAN`=3.
6. Run the worker with `fake_llm` scripted per role.
7. Assert `QA-PLAN`'s prompt contained the **updated** `DB-MODEL` (D4).
8. Assert five proposals written, nothing applied (`propose` mode, D5).
9. Apply all → versions bumped, `derives_from` updated, hashes reindexed.
10. Assert **zero** new change events (D6).
11. Rollback → byte-identical to `before/`.

### 8.3 Adversarial / failure cases

- Malformed YAML front-matter.
- Agent returns prose with no tool call.
- **Agent returns a summary instead of the document** (shrink guard, D15).
- **Agent drops a `##` section** during a whole-file rewrite.
- **Agent invents a new `artifact_key`** → rejected, no implicit create (D17).
- **Agent exceeds the 10-artifact soft cap** → request parked for approval (D18).
- **Agent asks a question with no `req_id`** → still routed, flagged as general.
- **Non-blocking question but no inline marker written** → job → `needs_review`
  (the guess would otherwise be invisible, D20).
- **Malformed assumption marker** (missing/duplicate `Q-nnnn`) → parser reports,
  does not crash the scan.
- **Rewrite silently drops an unresolved marker** → rejected.
- **Human hand-deletes a marker** → next scan lowers the count; question stays open.
- **Every depth-1 job blocks** → digest posted immediately, not deferred.
- **Requirement changes after an answer** → prior answer flagged `stale_context`
  and not presented as authoritative.
- **Question raised while the BA conversation is deleted** → queued, not lost.
- **Two roles ask about the same `req_id`** → one digest entry, one answer
  resolves both.
- **Agent renumbers `REQ-nnn` IDs** → validation failure, propagation refused.
- **Duplicate `REQ-nnn`** in the requirements document.
- Agent tries `write_artifact` with `../../etc/passwd`.
- Agent tries `run_python` in propagation context.
- Provider returns zero chunks (the case the extracted chat service must guard).
- Mid-stream exception; worker crash mid-job (restart must not leave `running`).
- Workspace deleted while jobs are queued.
- Read-only workspace / permission denied.
- Two projects propagating simultaneously.
- Cyclic dependency graph in a custom template.
- Unicode / very long requirement text; CRLF vs LF line endings on Windows.

### 8.4 CI

- `pytest -q --cov=. --cov-report=term-missing`, with the autouse `no_network`
  socket guard (0.1a) and `pytest-timeout` on by default.
- Target: >85% on `propagation/`, >70% overall.
- Linux is the current development platform; run CI on Linux first. Add
  Windows coverage later if the application continues to support it, especially
  for path-jail, ZIP import/export, and line-ending behavior.
