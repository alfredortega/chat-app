# Phase 6 — Compatibility, Migration & Persona Contracts

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **6.1–6.4**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.

---

## Phase 6 — Compatibility, Migration & Persona Contracts

### 6.1 Existing Code Folders and conversation folders

This repository has no current `/api/projects` endpoint. Existing project-like
workspaces are created by `/api/folders` with `code_folder: true`, via
`database.create_code_folder()`. They may have the legacy directories
`Requirements/`, `Test Planning/`, `UX Design/`, `Data Modeling/`, and
`Project Management/`, plus conversations and linked-folder registrations.
They have no `project.yaml`, front-matter, `REQ-nnn` IDs, or artifact rows.
Provide an **"Adopt project"** flow:

1. Scan the workspace and let the user map existing files → artifact keys.
2. Offer an optional one-off LLM pass to insert `REQ-nnn` IDs into the
   requirements doc and generate front-matter — presented as a reviewable diff
   like any other change.
3. Record baseline hashes so the first real edit produces a clean change event.

Adoption must be **opt-in and non-destructive**. Never rewrite a user's document
without showing the diff. Ordinary conversation folders must remain ordinary
folders, and existing Code Folder creation must remain available until the
managed-project flow is proven compatible.

### 6.2 Folder export/import (`app.py`, then extracted module)

The manifest is `export_version: "1.0"` and carries conversations, messages,
uploaded files and linked-folder paths — but **not** artifacts, deps, traces,
change events, jobs or issues. Also, `linked_folder_paths` are absolute and
already break across machines; workspace paths will have the same problem.

- Bump to `export_version: "1.1"`, add the propagation tables and (optionally)
  the workspace files themselves.
- Import must still accept the existing `1.0` archives produced directly by
  `app.py`.
- Store workspace paths **relative to the archive** and re-resolve on import.

**Acceptance:** round-trip test — export a mid-propagation project, import it,
assert artifact/job/change state matches; and a `1.0` archive still imports.

### 6.3 Persona prompt contracts (`database.py`, then optional seed module)

Persona prompts are currently seeded by `database.STARTER_PERSONAS`; there is no
`seed_data.py`. Either keep the registry there or introduce a dedicated seed
module as a separate refactor. The file-prefix convention becomes load-bearing
and is currently inconsistent:

| Persona | Has prefix instruction? |
|---|---|
| Business Analyst (`BA_`) | yes |
| Project Manager (`PM_`) | yes |
| Security Analyst (`SA_`) | yes |
| UX Designer (`UX_`) | yes |
| QA Tester (`QA_`) | yes |
| **Database Developer** | **no** |
| **DevSecOps Engineer** | **no** |

Actions:

- Add `DB_` and `SEC_` prefix instructions to those two personas.
- Fix the missing space in the QA Tester prompt
  (`...without approval.All files you generate...`).
- Add to the BA prompt: **emit and never renumber stable `REQ-nnn` IDs.** The
  entire deterministic impact analysis (D3) depends on this.
- Add to every downstream persona: maintain `derives_from` front-matter, preserve
  unaffected sections verbatim, call `ask_question` when a requirement is
  genuinely ambiguous instead of guessing (D19), `raise_issue` for risks and
  suggestions (D12), and `propose_artifact` rather than writing an unregistered
  file (D17).
- Each persona must be told **it is not talking to the user directly** during
  propagation — questions go to the BA inbox, so they must be self-contained and
  answerable by a non-specialist. "It depends on your architecture" is a useless
  question; "should MySQL connections require TLS?" is a good one.
- Each persona must be told **when to block and when to assume** (D20), with a
  worked example of each. Getting this calibration wrong is the difference
  between a wave that stalls constantly and one full of silent guesses.
- Each persona must be told the **exact inline marker format**, since the scanner
  parses it:
  `> **⚠️ ASSUMPTION (Q-nnnn):** <text> — <Role>`
- **Migration:** `init_db()` currently only backfills `test_prompt` when empty
  and otherwise uses `INSERT OR IGNORE`, so **existing installs will not pick up
  edited starter prompts**. Decide: version the starter personas and offer a
  "reset starter personas to latest" action in Settings (must not clobber
  user-edited prompts silently).

### 6.4 Cost & safety review

- Token estimate shown **before** a wave runs; hard cap enforced (D14).
- Confirmation dialog before enabling `auto` mode, spelling out the risk.
- Document that propagation agents cannot run `run_python` (D11).
- Verify no API keys or workspace absolute paths leak into `.agents/` artifacts
  or the change-event log.
