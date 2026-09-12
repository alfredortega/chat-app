# Agentic Persona Propagation — Implementation & Test Plan

> **Status:** complete — all 35 delivery commits (C00, C01a–C31) are done and the
> full test suite is green.
> **Owner:** —
> **Last updated:** 2025-XX-XX (C01 split into C01a–C01d after three frozen
> attempts; see 0.7)
>
> **Resolved:** Q1 (stable `REQ-nnn` IDs — **yes**), Q2 (granularity — **N artifacts
> per role, not one per folder**), Q3 (`propose` default / `off` for adopted),
> Q4 (**git required**), Q5 (**whole-artifact rewrite**), Q14
> (**template-with-optional-extension**), Q15 (**soft cap of 10 artifacts per
> role, then prompt**), Q18 (**BA conversation is the single Q&A inbox; personas
> stay enabled**), Q21 (**non-blocking questions → inline assumption marker**),
> Q22 (**digest per depth**), Q23 (**answer reuse**). See D3, D8, D15–D22.
>
> **All blocking questions are now answered.** Phase 0 can begin.

**Goal:** When the Business Analyst changes a requirement, every downstream
persona artifact (UX, Database, QA, DevSecOps, Project Manager) is
automatically identified as affected, regenerated in dependency order, and
presented as a reviewable diff — with optional full auto-apply.

**Canonical acceptance scenario (the "golden test"):**

> `REQ-014` changes from "local SQLite only" to "SQLite **and MySQL**, accessed
> via the SQLAlchemy ORM". Without further human prompting the system must
> produce updated `DB-MODEL`, `UX-WIRE`, `QA-PLAN`, `SEC-RISK` and `PM-PLAN`
> artifacts that each reflect dual-database support and SQLAlchemy, in that
> dependency order, with a traceable audit record.

**What exists today that this builds on**

| Existing | Role in this feature |
|---|---|
| `folders` model + `/api/folders` export/import in `app.py` | Legacy conversation folders; candidate project container, but requires a migration and lifecycle policy (D9) |
| `database.STARTER_PERSONAS` + `personas` table | Current persona registry; no `seed_data.py` module exists yet |
| `database.create_code_folder()` | Legacy project-like scaffolder with `Requirements`, `Test Planning`, `UX Design`, `Data Modeling`, and `Project Management`; must be replaced or migrated to the template flow |
| `static/js/folders.js` + `static/js/api.js` | Current folder UI/API integration; no `project.js` exists |
| `linked_folders` + `file_handler.build_linked_folder_context()` | Read-access pattern for upstream artifacts, but too coarse and truncation-prone for propagation (0.6) |
| Chat and regeneration loops in `app.py` | Current SSE tool-call implementation; must be extracted into a reusable bounded service before worker use (0.3) |
| `tools.py` `TOOLS` / `execute_tool_call()` | Current global tool registry and executor; needs project-scoped profiles and an artifact path jail (0.5) |
| Flask-SQLAlchemy models and `db.create_all()` | Current persistence layer; schema work must use SQLAlchemy-compatible migrations rather than raw SQLite connection code |

## -1. Repository alignment prerequisite

The copied plan was originally written for a different application. Before the
current Phase 0 commits, land **C00** as a compatibility boundary:

| # | Status | Commit | Gate | Defer |
|---|---|---|---|---|
| C00 | `[x]` | Extract `create_app()` from `app.py`; make database URI and encryption key injectable; preserve the existing `/api` routes and SSE chat behavior; add a legacy compatibility smoke test | Existing chat, settings, folders, personas, uploads, and export/import still work through the factory; importing the module does not initialize the production database or write `.env` | Propagation schema, worker startup, and project routes remain deferred to their assigned commits |

C00 is required because the current application creates a global Flask app and
calls `db.init_db(app)` during import. It also loads `ENCRYPTION_KEY` from the
actual implementation in `database.py`; the plan must not refer to a separate
`crypto` module unless one is deliberately introduced.

---

## How this plan is organised

This file is the **hub and the source of truth for status**. The detailed
specification lives in `docs/plan/`, split so that a single working session can
read only what its commit needs. The original single file was ~78 KB / ~20k
tokens — large enough that reading it consumed most of an agent's context before
any code was written (a diagnosed cause of the three frozen C01 attempts, 0.7).

**Section numbers are unchanged.** Every existing cross-reference (`see 0.5`,
`D11`, `2.3`, `1.3`) still resolves; use this map to find the owning file.

| Sections | File | ~tokens |
|---|---|---|
| D1–D22, non-goals | [docs/plan/00-decisions.md](docs/plan/00-decisions.md) | 1.3k |
| 0.1–0.6 | [docs/plan/01-phase0-foundations.md](docs/plan/01-phase0-foundations.md) | 2.4k |
| 0.7 | **this file** (applies to every commit) | 0.6k |
| 1.1–1.6 | [docs/plan/02-phase1-data-model.md](docs/plan/02-phase1-data-model.md) | 2.6k |
| 2.1–2.4 | [docs/plan/03-phase2-detection.md](docs/plan/03-phase2-detection.md) | 0.9k |
| 3.1–3.4 | [docs/plan/04-phase3-worker.md](docs/plan/04-phase3-worker.md) | 1.4k |
| **5.4** (Q&A routing) | [docs/plan/05-qa-routing.md](docs/plan/05-qa-routing.md) | 2.3k |
| Phase 4 routes | [docs/plan/06-phase4-api.md](docs/plan/06-phase4-api.md) | 0.6k |
| 5.1–5.3 | [docs/plan/07-phase5-frontend.md](docs/plan/07-phase5-frontend.md) | 0.6k |
| 6.1–6.4 | [docs/plan/08-phase6-compatibility.md](docs/plan/08-phase6-compatibility.md) | 1.0k |
| 7.1–7.3 | [docs/plan/09-phase7-documentation.md](docs/plan/09-phase7-documentation.md) | 0.6k |
| 8.1–8.4 | [docs/plan/10-test-strategy.md](docs/plan/10-test-strategy.md) | 1.0k |
| Open questions, Phase 9 shape | [docs/plan/11-open-questions.md](docs/plan/11-open-questions.md) | 1.3k |

**Rules that keep this from rotting:**

1. **The Delivery Commits table exists only in this file.** Never copy it into a
   phase file — duplicated status guarantees divergence.
2. **§0.7 lives here, not under Phase 0**, because its hazards apply to every
   commit through C31, not only to the foundations.
3. **§5.4 is its own file** because two commits in different phases reference it:
   C18 (worker mechanism) and C25 (UI).
4. A commit's `Ref` column names sections; the map above resolves them to files.
   A session should read: this file's hazards + its own row + its `Ref` file(s).
5. **Every commit row carries a `Defer` cell**, because the plan sections describe
   the finished design rather than any one increment. §0.6, for example, specifies
   a DB-backed `build_artifact_context()` that C05 must *not* build. A section's
   silence about a deferral is not permission — the `Defer` cell overrides it.

---

## 0.7 Agent-session hazards (read before starting any commit)

The original C01 was attempted three times by three different models and froze
every time. The causes are specific to this repo and will recur on later commits
unless avoided deliberately.

| Hazard | Mechanism | Avoidance |
|---|---|---|
| **Unbounded tool-call loop** | The chat and regeneration generators in `app.py` use `while True:` with no iteration cap. A fake LLM that repeats or replays a `tool_calls` turn spins forever and `pytest` never returns. | `pytest-timeout` in C01a; script exhaustion raises (0.2); the loop-driving test deferred to C02 where `max_iterations` lands. |
| **Blocking terminal commands** | `python start.py` never exits (it `wait()`s on the server and launches Chrome). `python app.py` never exits. A pager-enabled `git` command blocks too. | Never run `start.py` or `app.py` to "verify". Verify with `pytest`. Use `git --no-pager`. |
| **Context exhaustion** | The plan was one ~20k-token file; reading it plus `database.py`, `app.py`, `tools.py`, `file_handler.py` and `README.md` consumed most of a context window before any code was written. The split into `docs/plan/` fixes the plan side of this; the code side is still on you. | Read this hub plus only the `Ref` file(s) for your commit. Read only the source files named in that section. Never read `docs/plan/00-decisions.md` unless resolving a contradiction. |
| **Import-time side effects** | `app.py` creates the Flask app and runs `db.init_db(app)` at import. `database._get_encryption_key()` may append `ENCRYPTION_KEY` to `.env`. | C00 introduces `create_app()` and injectable configuration. Tests use a temporary SQLAlchemy database URI and fixed `ENCRYPTION_KEY`; never import the production app configuration or mutate `.env`. |
| **Interactive prompts** | `pip install` without `--quiet`, or any command awaiting confirmation. | Pass non-interactive flags; prefer `venv\Scripts\python.exe -m pip install -r requirements-dev.txt --quiet`. |

**Rule for every commit from here on:** if a task's acceptance criterion requires
driving a loop, a thread or a subprocess, the iteration/time bound must exist
*before* the test that exercises it — in the same commit or an earlier one.

**Commit sizing rule.** If a row in the Delivery Commits table implies more than
roughly three new files plus one refactor of existing code, split it. C01 is the
worked example: it bundled dependency setup, a config file, a network guard, a
file move with import-path fallout, two fixtures with import-order blockers, and
a non-trivial test double — six unrelated failure modes in one session.

---

## Delivery Commits

Legend: `[ ]` not started &nbsp;|&nbsp; `[~]` in progress &nbsp;|&nbsp; `[x]` done &nbsp;|&nbsp; `[!]` blocked

**Update the checkbox and the Status column as part of the same commit as the
code.** With a plan this size spanning many sessions, this table is the source of
truth for what is finished. A commit is not done until its row says so.

`Ref` = the section that specifies the work. Resolve section numbers to files via
the **file map** above — e.g. `Ref 2.3` → `docs/plan/03-phase2-detection.md`.
`Gate` = the objective check that must pass before the row may be marked `[x]`.
`Defer` = work the `Ref` section describes that this commit must **not** do,
with the commit that owns it. `—` means nothing is deferred. **Copy this cell
verbatim into the session prompt.** Where a `Ref` section and a `Defer` cell
conflict, the `Defer` cell wins — the sections describe the finished design, not
the increment.

### Phase 0 - Foundations (no user-visible change)

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C01a | `[x]` | `requirements-dev.txt` (incl. **`pytest-timeout`**), `pytest.ini`, autouse `no_network` guard, import smoke test | 0.1a | `pytest` green; production `chat.db` + `.env` mtime unchanged | — (`freezegun` is installed but unused until C12; that is expected, not an omission) |
| C01b | `[x]` | Add a bounded live-provider utility under `scripts/` only if needed; otherwise document that no `run_tests.py` exists in this repository | 0.1b | Test discovery is clean; importing any live utility runs nothing and makes no network call | — |
| C01c | `[x]` | SQLAlchemy test database configuration; `tmp_db` + `workspace` fixtures; Flask `client` fixture backed by `create_app()` from C00 | 0.1c | Fixture self-tests green; production `chat.db`/`.env` provably untouched; legacy route smoke test passes | **No production app import-time workaround and no `CHAT_DB_PATH`-only design. C00 owns the factory and injectable database URI.** |
| C01d | `[x]` | `tests/fakes.py::FakeOpenAIClient` (stream + non-stream, call recording, zero-chunk, mid-stream raise, exhaustion raises) | 0.2 | Unit tests **of the fake only**; no test drives the chat service yet | **Multi-turn loop drive test -> C02.** Test the fake in isolation; do not import the extracted service |
| C02 | `[x]` | Extract bounded `chat_service` from `app.py`; structured events; restricted tools + `max_iterations`; multi-turn loop test | 0.3 | Characterisation test proves SSE output byte-identical; runaway script terminates with an `error` event | — |
| C03 | `[x]` | SQLAlchemy thread safety: scoped sessions, SQLite WAL + `busy_timeout`, connection cleanup, repair watermark | 0.4 | 4-thread interleaved read/write, zero `OperationalError`; sessions/connections released after worker jobs | — |
| C04 | `[x]` | `build_tools(context)`, `propagation` profile, `write_artifact` path jail | 0.5 | Traversal tests rejected; `run_python` refused in propagation context | **Rejecting an unregistered `artifact_key` -> C08.** No artifact registry exists yet. Ship the path jail + profile gating only. `read_artifact` / `list_artifacts` / `ask_question` / `raise_issue` / `propose_artifact` may be **declared** in the profile but need no working implementation |
| C05 | `[x]` | `build_artifact_context()` as a **pure function** over loaded artifact dicts | 0.6 | Golden context contains `BA-REQ` + diff, not `PM-PLAN`; oversized input raises | **§0.6's signature is the eventual DB-backed one — do not implement it.** No `project_id` lookup, no `agent_issues` query, no "Previously clarified" answer injection (-> C07/C18). Take already-loaded artifact dicts as an argument and return the triple |

**C01 was split into C01a-C01d** after three consecutive frozen attempts. The
causes are catalogued in 0.7 — read that section before starting any Phase 0
commit. Do not re-merge these four rows.

**C02 must be a commit on its own.** It touches the live chat path, and the
characterisation test has to be committed and passing against *unmodified* code
before the refactor begins. It also owns the `max_iterations` guard, which is why
the multi-turn tool-loop test moved here from C01d — driving that loop before the
guard exists is what hung the earlier attempts.

### Phase 1 - Data model

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C06 | `[x]` | Add runtime `PyYAML` dependency; pure parsers: front-matter, `REQ-nnn` extractor + validator, assumption-marker parser, ID allocator | 1.2, 5.4 (marker format only) | Unit tests incl. malformed YAML, duplicate ID, renumber, deleted req | — (pure functions; no DB access even though 1.2 mentions `project.yaml`; parse markers only, do not implement Q&A storage/routing) |
| C07 | `[x]` | Schema migrations; `folders` columns; artifact index CRUD; DB-backed artifact-context lookup (no answer reuse) | 1.4, 0.6 | Existing `chat.db` migrates cleanly and idempotently; context lookup reads registered artifacts | — (create the tables; populating traces/deps is C08; answer reuse injection is C18) |
| C08 | `[x]` | `PROJECT_TEMPLATES`; artifact registration; role->artifact edge expansion; topo sort + cycle detection; caps | 1.3, 1.6, D17, D18 | Golden graph yields depths 1,1,1,2,3; cycle rejected; 11th artifact parked | — (also lands C04's deferred unregistered-key rejection, and unblocks C14's coverage report) |
| C09 | `[x]` | Git integration (`init`, commit, diff, rollback), startup/operation git-availability checks + cascade/lifecycle cleanup | D8, 1.5 | Commit/rollback round-trip; missing git produces a clear warning/error; no orphan rows after folder delete or purge | — |
| C10 | `[x]` | `examples/sqlite-to-mysql/` before+after fixture (multi-artifact) | 7.3 | Loads as a valid project; used by C13 | **Fixture files only.** The Phase 7 prose about the example (§7.3's documentation role) is C30 |

**C10 is listed in Phase 7 of this document but is needed here** - it is the
golden-test fixture, so it must exist before impact analysis can be tested. Only
the surrounding *prose* belongs in Phase 7.

### Phase 2 - Detection (ships `notify` mode)

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C11 | `[x]` | Scanner: hashing, classification, assumption indexing | 2.1 | Hand-edit detected; marker count follows the document | — (§2.1's "triggered after any `write_artifact` call" hook needs the worker; wire it in C16) |
| C12 | `[x]` | Change events: diff, LLM summary, debounce, renumber/duplicate validation | 2.2 | 3 rapid saves -> 1 event; renumber -> validation error, nothing queued | — (first real use of `freezegun`, installed back in C01a) |
| C13 | `[x]` | Impact analysis, depth assignment, conflict detection | 2.3, 2.4 | **Multi-artifact fixture**: role owns 4, only the tracing 1 is queued | — (needs C10's fixture; queue jobs only, never execute them) |
| C14 | `[x]` | `notify` mode end-to-end + trace coverage report | 3.3, 1.3 | Artifacts are marked stale and trace gaps are reported with zero propagation LLM spend | **Only the `notify` row of §3.3.** UI badges -> C23/C26, `propose` -> C17, `auto` -> C31. No LLM call on this path at all |

**C14 is the first genuinely useful shipping point.**

### Phase 3 - Worker (ships `propose` mode)

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C15 | `[x]` | Worker module: job claiming, depth gating, shutdown hook, startup guard helper - **no LLM yet and no `app.py` wiring** | 3.1 | Jobs transition correctly with a no-op executor; no job stuck `running`; startup guard helper verified without importing `app.py` in tests | **No agent invocation (§3.2) and no digest posting.** §3.1 mentions both; use a no-op executor. Token budget -> C19. Do not add a `client` fixture, import `app.py` in tests, or wire worker startup into `app.py`; route/app-factory and app startup wiring remain C20 |
| C16 | `[x]` | Agent invocation returning validated rewrite payloads; in-memory wave overlay for downstream prompts; same-role batching; rewrite guards (shrink / heading / marker / trace) | 3.2, D15, D16 | `QA-PLAN` prompt contains updated `DB-MODEL`; summary-instead-of-doc rejected | **Proposal persistence/apply/reject/rollback -> C17.** Do not write final artifacts or `.agents/proposals` here; keep validated rewrites in memory only so downstream prompts can be tested. **`ask_question` / `raise_issue` / `propose_artifact` behaviour -> C18.** §3.2 item 5 lists them in the prompt; the tools may be offered but their handling lands in C18. "Previously clarified" (item 6) -> C18 |
| C17 | `[x]` | Proposals, apply/reject, wave rollback | 3.3 | 5 proposals written, nothing applied; rollback byte-identical to `before/` | **Only the `propose` row of §3.3.** `auto` -> C31 |
| C18 | `[x]` | `ask_question`; blocking vs assumption; per-depth digest; answer reuse | 5.4, D19-D22 | Non-blocking -> job completes with inline marker; 6 questions -> 1 digest | **Server side only.** §5.4's "Consequence for the UI" (panel counts, inbox badge, escalation styling) -> C25. Answer reuse extends C05's context builder — that is expected here, since C07 now exists |
| C19 | `[x]` | Loop prevention, token budget, kill switch | 3.4, D14 | A propagation wave produces **zero** new change events | — (§6.4's pre-wave token *estimate UI* -> C31; enforce the cap server-side here. Read-only token-budget display remains C23; settings mutation -> C21) |

**C19 completes the golden scenario (8.2) end-to-end.**

### Phase 4 - API

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C20 | `[x]` | Extract/register project blueprint, worker startup wiring, and read-only routes: list, artifacts, changes, proposals, issues, assumptions | Phase 4 | Route tests: happy path + 404; worker starts once under the C00 app factory | **C00 owns `create_app()`; C01c owns the test client. C15's deferred worker startup wiring lands here. Mutating routes -> C21; SSE -> C22** |
| C21 | `[x]` | Mutating routes: scan, propagate, apply, reject, rollback, answer, artifact-requests, settings | Phase 4 | Happy / 404 / 409 (stale proposal) | — |
| C22 | `[x]` | SSE job-progress stream | Phase 4 | Live progress observed during a wave | — |

### Phase 5 - Frontend

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C23 | `[x]` | `api.js` additions + project panel (read-only, grouped by role with roll-up) | 5.1, 5.2 | Panel renders the golden project correctly | **Read-only.** Display propagation settings and token budget, but do not edit them; settings mutation -> C21. Review modal -> C24; Q&A rendering/controls/assumption counts -> C25; folder badges/`project.js` -> C26 |
| C24 | `[x]` | `diff_view.js` + review modal | 5.1 | Approve/reject works; readable in **both** themes | — (resolve §5.1's open `diff2html`-vs-hand-rolled choice here) |
| C25 | `[x]` | Q&A UI: digest rendering, answer controls, assumption counts | 5.4 | Answer -> BA edit -> re-propagation completes | **UI only** — the mechanism landed in C18. Read §5.4's "Consequence for the UI" and skip the server-side spec |
| C26 | `[x]` | `folders.js` / `project.js` integration, badges, artifact-request approval | 5.2, D17 | Project visually distinct; extension approval flow works | — |

### Phase 6-9 - Compatibility, docs, auto mode

| # | Status | Commit | Ref | Gate | Defer |
|---|---|---|---|---|---|
| C27 | `[x]` | Adopt-existing-project flow | 6.1 | Non-destructive; every rewrite shown as a diff first | — |
| C28 | `[x]` | Export/import `1.1` + `1.0` backward compatibility | 6.2 | Mid-propagation round-trip; a `1.0` archive still imports | — |
| C29 | `[x]` | Persona prompt contracts + starter-persona versioning | 6.3 | Existing installs can opt in without clobbering edited prompts | — |
| C30 | `[x]` | `help.html` 11-12 rewrite + renumber; `README.md`; manual checklist | 7.1, 7.2, 8.1 | Docs match behaviour; checklist passes in both themes | — (C01b must verify that no copied `run_tests.py` reference remains; there is no such script in the current repository) |
| C31 | `[x]` | `auto` mode behind an explicit confirmation + pre-wave token estimate UI | 3.3, 6.4 | Loop-prevention regression green; confirmation cannot be bypassed; token estimate shown before a wave runs | — (last row: nothing left to defer) |

### Progress

Update this line with each commit.

```
Prereq   [x         ]  1/1
Phase 0  [xxxxxxxx  ]  8/8
Phase 1  [xxxxx     ]  5/5
Phase 2  [xxxx      ]  4/4
Phase 3  [xxxxx     ]  5/5
Phase 4  [xxx       ]  3/3
Phase 5  [xxxx      ]  4/4
Phase 6+ [xxxxx     ]  5/5
TOTAL                  35/35
```

### Known cross-commit dependencies

**This table is now derived — the authoritative copy of each deferral lives in the
`Defer` column of the commit that must not do it.** Keep both in sync; the table
below exists to answer "when does X finally land?", which the per-commit cells
cannot.

| Deferred item | Deferred by | Lands in | Why |
|---|---|---|---|
| Multi-turn tool-loop drive test | C01d (0.2) | **C02** | The current `app.py` chat loop has no iteration cap until C02. Driving it earlier hangs the suite (0.7). |
| `create_app()` factory and injectable app/database configuration | C00 | **C00** | The current `app.py` initializes the production database during import; all later tests and worker code need an isolated application boundary. |
| `client` Flask-test-client fixture | C01c | **C01c** | It depends on the C00 factory and is needed for legacy route smoke tests before project routes exist. |
| Worker startup wiring into the Flask app | C15 | **C20** | C15 builds and tests the worker module without starting it; C20 registers the worker once through the C00 factory. |
| `write_artifact` rejects unregistered `artifact_key` | C04 (0.5) | **C08** | Needs the artifact registry. C04 ships the path jail and profile gating only. |
| `build_artifact_context()` reading from the DB | C05 (0.6) | **C07** | §0.6 documents the eventual DB-backed signature. C05 is deliberately a pure function over loaded dicts so it stays testable in Phase 0. |
| Answer-reuse injection into context | C05, C16 | **C18** | Needs both the context builder and `agent_issues`. |
| `propagation` tool *behaviour* (`ask_question`, `raise_issue`, `propose_artifact`) | C04 declares, C16 offers | **C18** | C04 only lists them in the profile; C16 may pass them to the model; C18 implements what happens when they are called. |
| Proposal persistence/apply/reject/rollback | C16 | **C17** | C16 validates model rewrites but does not persist proposals or mutate artifacts; C17 owns the propose-mode lifecycle. |
| Golden scenario test (8.2) | C13 onward | **C10** (fixture) | Needs the example fixture to exist first. |
| Trace coverage report | C14 | **C08** | Needs `artifact_traces` populated by registration. |
| Notify/stale UI badges | C14 | **C23/C26** | C14 is server-side notify mode only; frontend panel/folder badges are Phase 5 work. |
| `freezegun` actually used | C01a installs it | **C12** | Installed early for convenience; only the debounce test (2.2) needs it. |
| `propose` / `auto` rows of §3.3 | C14 ships `notify` | **C17** / **C31** | §3.3 documents all four modes in one table; they ship in three separate commits. |
| Pre-wave token estimate UI | C19 enforces budget server-side | **C31** | The server-side cap exists in C19; the final pre-run UI confirmation belongs with auto-mode/cost-safety UX. |
| §5.4's UI consequences | C18 ships the mechanism | **C25** | One section, two commits in different phases. |

### Session prompt template

Fill the four slots from this commit's row: `<C##>`, `<deliverable>`, `<Ref>`,
`<Gate>`, `<Defer>`. Copy the `Defer` cell **verbatim** — do not summarise it, and
do not omit it when it is `—` (an explicit "nothing deferred" is itself useful).

```text
Read `to_do.md` (the hub - status table + section 0.7 hazards + file map), then
read ONLY `docs/plan/<file>` for section <Ref>. Do not read any other plan file.
Design decisions D1-D22 are settled - do not open 00-decisions.md and do not
relitigate them.

Scope: **commit <C##> only** - <deliverable>. Do not start the next commit.

Read only the source files named in section <Ref> before writing anything. The
gate that must pass is: <Gate>.

Out of scope for this commit - deferred by design. Do NOT implement these, and do
not "improve" on this by adding them:
<Defer>

The plan sections describe the **finished** design, not this increment. Where
section <Ref> and the list above conflict, **the list above wins.** Section <Ref>
will not flag the deferral, so do not treat its silence as permission. If the
gate or referenced section appears to require UI, API, persistence, app startup
wiring, or another capability assigned to a later commit, implement only the
minimal seam/stub needed for this commit and call out the dependency rather than
pulling the later work forward.

Constraints:
- Linux / bash; use `venv/bin/python` (or the activated virtual environment).
- Never modify `.env` or `chat.db`.
- No network calls in tests.
- **Never run `start.py` or `app.py`** - neither ever exits and the session will
  hang. Verify with `pytest` only. Use `git --no-pager` for any git command.
- Every test run must be bounded: `pytest` with `pytest-timeout` configured.
  Never write a test that drives an unbounded loop before its bound exists.

When done: run the suite, show the output, then update this commit's Status to
`[x]` in `to_do.md` and update the Progress block. Status lives ONLY in the hub -
never copy the Delivery Commits table into a plan file. Report and stop.

If anything in the plan is ambiguous, ask rather than guessing. In particular,
stop and ask if the commit appears to require importing `app.py`, touching
`.env`, driving a loop with no iteration cap, or creating a table or module that
the "out of scope" list says belongs to a later commit.
```
