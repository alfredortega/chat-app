# Phase 1 — Data Model & Artifact Format

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **1.1–1.6**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.

---

## Phase 1 — Data Model & Artifact Format

### 1.1 Workspace layout

```
<output_dir>/<Project Name>/
├─ project.yaml                 # template id, roles, propagation mode, next_req_seq, budget
├─ Requirements/
│  ├─ BA_requirements_core.md   # BA-REQ-CORE
│  └─ BA_requirements_data.md   # BA-REQ-DATA
├─ Design/UX_wireframes_*.md
├─ Data/DB_data_model_*.md
├─ Test Cases/QA_test_plan_*.md
├─ Security/SEC_risk_register_*.md
├─ Project Plan/PM_implementation_plan_*.md
└─ .agents/
   ├─ changes/CHG-0007.json     # change events
   └─ proposals/CHG-0007/       # pending artifact versions awaiting approval
```

The workspace is a **git repository** (D8) — `git init` on create, one commit per
applied wave. There is no snapshot directory; git is the only version mechanism.

Per D16 each directory holds **one or more** artifacts; the `*` above is
deliberate. This repository's existing Code Folder flow creates `Requirements/`,
`Test Planning/`, `UX Design/`, `Data Modeling/`, and `Project Management/`.
Adoption must recognize that layout, preserve existing files, and offer an
explicit migration to the managed template layout. The new project scaffolder
must be idempotent and must not silently convert a legacy conversation folder.

### 1.2 Artifact front-matter (D2)

```markdown
---
artifact_id: DB-MODEL
role: Database Developer
version: 4
origin: propagation          # human | propagation
derives_from:
  - artifact: BA-REQ
    version: 7
    requirements: [REQ-011, REQ-014, REQ-022]
---
```

Requirements documents use `## REQ-014 — <title>` headings. A parser extracts
IDs with a single regex; **no LLM involvement in impact analysis** (D3).

**Confirmed (Q1):** the BA persona is required to emit stable `REQ-nnn` IDs and
never renumber them. This makes D3 a SQL query. Consequences that must be built:

- **ID allocator.** The BA must never reuse an ID. Store `next_req_seq` in
  `project.yaml` and inject the next free number into the BA's prompt. Do not
  rely on the model to count.
- **Renumber detection.** If a scan finds an ID that vanished while another
  appeared with near-identical text, that is a renumber, not a change. Treat it
  as a **hard validation failure** and refuse to propagate until the human fixes
  it — silently accepting it would corrupt every `derives_from` in the project.
- **Validation on every scan:** duplicate IDs, gaps that indicate deletion,
  malformed IDs, and IDs referenced in `derives_from` that no longer exist.
- **Deleted requirement** is a first-class change type: downstream artifacts
  referencing it must be flagged, not silently left stale.

### 1.3 Artifact granularity (Q2 — confirmed: many per role)

**No concern with N artifacts per role — it is the better design**, and it makes
D15 (whole-file rewrite) affordable. But it is only cheaper if the *scoping* is
right, so these become requirements rather than optional refinements:

| Concern | Requirement |
|---|---|
| **Fan-out** | Impact analysis must select at the **artifact** level, not the role level. Changing `REQ-014` must queue only the 2 QA artifacts that trace to it, not all 15. If we ever fall back to "role owns everything", granularity becomes a cost *increase*. |
| **Trace precision** | `artifact_traces` is now the load-bearing table. A missing row means a document silently goes stale; a spurious row means wasted spend. Needs a coverage report: "requirements traced by no artifact" and "artifacts tracing to no requirement". |
| **Who decides the split?** | The **template** does (D17). A role may extend its own set, but only with **explicit human approval** — never mid-propagation. An agent inventing a new file layout on each run would break every inbound `derives_from`. Rule: **propagation may never create or delete artifacts**. |
| **Soft cap (D18)** | 10 artifacts per role. The 11th requires user approval, which raises `artifact_cap` for that role in `project.yaml`. Enforced server-side at registration, not just in the UI. |
| **Cross-artifact consistency** | Two QA artifacts updated by separate jobs can contradict each other. Mitigation: artifacts owned by the same role for the same change event run **sequentially in one conversation**, so each sees its siblings' updated output. |
| **Dependency edges** | `artifact_deps` is now artifact-to-artifact, so the graph gets wider. Keep the *template* declaring role-to-role edges and **expand** to artifact edges at registration time — authors should not hand-maintain N×M edges. |
| **Ordering within a depth** | Sibling artifacts at the same depth need deterministic ordering (by `artifact_key`) or the golden test is flaky. |
| **UI** | A status board of 20+ rows needs grouping by role with a roll-up status; a flat list will not scale. |
| **Splitting an existing artifact** | Splitting `QA-PLAN` into `QA-PLAN-AUTH` + `QA-PLAN-DATA` is a migration: inbound `derives_from` and `artifact_traces` must be rewritten. Needs an explicit, human-reviewed operation — **not** something an agent does mid-propagation. |

**Naming:** `<ROLE>-<SLUG>` (`QA-PLAN-AUTH`, `DB-MODEL-CORE`). Keys are immutable
once referenced; renaming is a migration, like splitting.

**Cost sanity check for the golden scenario.** With one 3,000-word document per
role, `REQ-014` triggers 5 full rewrites ≈ all of them. With 4 smaller artifacts
per role, it should trigger ~6–8 small rewrites — cheaper *and* more precise.
**This is only true if trace-level selection works**, so 2.3 must be tested
against a multi-artifact fixture, not the simple one-per-role graph.

**Template + optional extension (D17, Q14).** `PROJECT_TEMPLATES` must be
introduced in a new template/seed module or deliberately added beside the
current `database.STARTER_PERSONAS`; it does not exist today. It declares, per role: the artifact keys, their `rel_path`, their
upstream roles, and an `artifact_cap` (default 10, D18). Extension flow:

1. A role proposes a new artifact via the `propose_artifact` tool — which
   *records a request*, it does not create a file.
2. The request appears in the project panel for approval, showing the key, path,
   rationale and the role's current count against its cap.
3. On approval the artifact is registered, edges expanded, traces indexed, and
   the role's cap raised if the request pushed past it.
4. Only then may a propagation job write to it.

Rejecting a request is normal and must be cheap. A rejected request is recorded
so the same role does not re-propose it every wave.

### 1.4 Schema additions (`database.py`)

The current application uses Flask-SQLAlchemy and `db.create_all()` with a small
ad-hoc column migration helper. Add a real migration mechanism before adding
these tables (Alembic/Flask-Migrate or an equivalent versioned SQLAlchemy
migration module). Do not assume an existing `_migrations` list or raw SQLite
connection API.

The migration must extend the existing `folders` model/table rather than
creating a parallel project-folder concept:

```sql
ALTER TABLE folders ADD COLUMN kind             TEXT NOT NULL DEFAULT 'folder';  -- folder | project
ALTER TABLE folders ADD COLUMN workspace_dir    TEXT NOT NULL DEFAULT '';
ALTER TABLE folders ADD COLUMN template_id      TEXT NOT NULL DEFAULT '';
ALTER TABLE folders ADD COLUMN propagation_mode TEXT NOT NULL DEFAULT 'off';     -- off|notify|propose|auto
ALTER TABLE folders ADD COLUMN next_req_seq     INTEGER NOT NULL DEFAULT 1;      -- REQ id allocator (1.2)
ALTER TABLE folders ADD COLUMN token_budget     INTEGER NOT NULL DEFAULT 0;      -- 0 = unlimited (D14)
ALTER TABLE folders ADD COLUMN ba_conversation_id INTEGER;                        -- the single Q&A inbox (D19)
```

New tables (full DDL in the implementation commit):

| Table | Purpose |
|---|---|
| `artifacts` | Index: `project_id`, `artifact_key`, `role_persona_id`, `rel_path`, `version`, `content_hash`, `status` (`current`/`stale`/`updating`/`conflict`/`needs_review`), `origin`, `heading_index` (JSON, for the retention check in 3.2) |
| `artifact_deps` | Artifact-level edges: `project_id`, `upstream_key`, `downstream_key`. Expanded from role-level template edges at registration (1.3) |
| `artifact_traces` | `project_id`, `artifact_key`, `req_id` — the traceability index that makes D3 a SQL query. **Load-bearing under D16**; needs a coverage report |
| `change_events` | `project_id`, `source_key`, `from_version`, `to_version`, `summary`, `changed_reqs` (JSON), `removed_reqs` (JSON), `diff`, `origin`, `created_at` |
| `propagation_jobs` | `change_id`, `artifact_key`, `persona_id`, `state`, `depth`, `batch_id` (same-role batching, 3.2), `conversation_id`, `proposal_path`, `tokens_used`, `error`, `attempts` |
| `agent_issues` | D12/D19–D22 question & issue channel: `project_id`, `raised_by_key`, `raised_by_persona_id`, `change_id`, `depth`, `kind` (`question`/`risk`/`conflict`/`suggestion`), `req_id` (nullable), `blocking` (INTEGER), `body`, `proposed_answer`, `status` (`open`/`answered`/`dismissed`/`resolved`), `answer`, `answered_at`, `stale_context` (INTEGER), `digest_message_id` |
| `artifact_assumptions` | D20 inline-marker index: `project_id`, `artifact_key`, `issue_id`, `req_id`, `marker_text`, `resolved` — makes assumptions countable, so they are visible debt rather than invisible drift |
| `artifact_requests` | D17 extension requests: `project_id`, `persona_id`, `artifact_key`, `rel_path`, `rationale`, `status` (`pending`/`approved`/`rejected`) |

All new tables `REFERENCES folders(id) ON DELETE CASCADE`.

Migration acceptance must include an existing production-shaped database with
conversations, personas, endpoints, uploaded files, linked folders, archived
flags, and encrypted endpoint keys. The migration must be idempotent and must
not delete or rewrite legacy rows.

### 1.5 Cascade / lifecycle cleanup

Audit every existing deletion path for orphans:

- `database.purge_all_conversations()` — deletes conversations only; must also
  clear jobs/changes or explicitly preserve them.
- `database.delete_folder(delete_contents=False)` — moves conversations out but
  the folder row (and its artifacts) is deleted; decide and test the semantics.
- Deleting a *project* folder: does it delete the on-disk workspace? **Proposed:
  no** — never delete user documents from a sidebar action; warn instead.

### 1.6 Dependency graph (the golden scenario)

```
BA-REQ ──┬──> UX-WIRE ───────────┐
         ├──> DB-MODEL ──┬──> QA-PLAN ──┬──> PM-PLAN
         ├──> SEC-RISK ──┘              │
         └──────────────────────────────┘
```

Role-level edges as declared by the template. These are **expanded to
artifact-level edges at registration time** (1.3) so authors never hand-maintain
N x M edges. Depths for the golden scenario: `UX-WIRE`, `DB-MODEL`, `SEC-RISK` =
1; `QA-PLAN` = 2; `PM-PLAN` = 3. Sibling artifacts at the same depth are ordered
deterministically by `artifact_key`.

Seeded from the new template registry, so an "SDLC project" gets it
automatically and other templates can be added later (D10). The registry must
not be confused with the current persona seed list in `database.py`.
Topological sort with **explicit cycle detection** — a malformed custom template
must fail fast, not hang the worker.

**Acceptance:** unit tests for the front-matter parser (round-trip, missing
fields, malformed YAML), the `REQ-nnn` extractor, the topological sort, and
cycle rejection.
