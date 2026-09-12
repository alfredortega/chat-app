# Phase 4 — API Layer

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **Phase 4 (route table)**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.

---

## Phase 4 — API Layer

Add a project blueprint/module and register it through the C00 application
factory. This repository currently has no `routes/` package, no `/api/projects`
endpoint, and no `project.js`; existing folder routes are defined directly in
`app.py`. Extracting the project routes must not break the legacy
`/api/folders` contract.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/projects` | List projects (`folders WHERE kind='project'`) |
| POST | `/api/projects` | Create — extended with `template_id` (back-compatible) |
| POST | `/api/projects/<id>/adopt` | Convert an existing plain folder/workspace into a managed project (see 6.1) |
| GET | `/api/projects/<id>/artifacts` | Status board data |
| POST | `/api/projects/<id>/scan` | Trigger change detection |
| GET | `/api/projects/<id>/changes` | Change-event history |
| POST | `/api/projects/<id>/changes/<cid>/propagate` | Queue jobs |
| GET | `/api/projects/<id>/changes/<cid>/proposals` | Diffs awaiting review |
| POST | `/api/projects/<id>/proposals/<jid>/apply` | Apply one |
| POST | `/api/projects/<id>/proposals/<jid>/reject` | Reject one |
| POST | `/api/projects/<id>/changes/<cid>/apply-all` | Apply the wave |
| POST | `/api/projects/<id>/changes/<cid>/rollback` | Revert the wave |
| GET | `/api/projects/<id>/jobs/stream` | SSE live job progress |
| GET/POST | `/api/projects/<id>/issues` | D12/D19 questions & issues (filterable by `blocking`, `status`) |
| POST | `/api/projects/<id>/issues/<iid>/answer` | Answer a question; optionally route into `BA-REQ` (D19) |
| POST | `/api/projects/<id>/issues/<iid>/dismiss` | "Not a requirement issue" — answer this job only |
| GET | `/api/projects/<id>/assumptions` | D20 unresolved inline assumptions, grouped by artifact |
| GET | `/api/projects/<id>/settings` | Current propagation mode, model per role, token budget |
| GET/POST | `/api/projects/<id>/artifact-requests` | D17 extension requests |
| POST | `/api/projects/<id>/artifact-requests/<rid>/approve` | Register the artifact; raise the role cap if needed (D18) |
| POST | `/api/projects/<id>/artifact-requests/<rid>/reject` | Record the rejection so it is not re-proposed |
| PUT | `/api/projects/<id>/ba-conversation` | Designate the Q&A inbox conversation (D19) |
| PUT | `/api/projects/<id>/settings` | Update propagation mode, model per role, token budget |

Create or reuse a small route-helper module for consistent JSON errors. There
is currently no `routes/helpers.py`, so do not treat that path as an existing
dependency.

**Acceptance:** route tests for happy path, 404, and 409 (apply a proposal whose
artifact changed underneath it) on every endpoint.
