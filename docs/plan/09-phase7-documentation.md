# Phase 7 — Documentation

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **7.1–7.3**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.
>
> C10 (the `examples/sqlite-to-mysql/` fixture specified in 7.3) is delivered in Phase 1, not here — only the surrounding prose belongs to Phase 7.

---

## Phase 7 — Documentation

### 7.1 `static/help.html` — substantive rewrite, not an append

Section 11 currently teaches a **manual three-conversation pattern** (create a
conversation, set the persona, link `Requirements/`, set the output folder) which
this feature partly replaces. Required work:

- Rewrite §11 "Creating a Project": templates, the new sub-directories, and the
  managed-artifact concept. Keep the manual pattern as a documented fallback for
  non-project folders.
- New §12 "Automatic Updates Between Personas": change events, impact analysis,
  propagation modes, reviewing and applying diffs, conflicts, rollback,
  **how downstream roles ask questions and how they arrive in the BA
  conversation (D19)**, artifact-extension approvals (D17), cost control.
- State explicitly that **persona selection is not restricted in project
  folders** — users can still talk directly to any role; the BA conversation is
  simply the inbox for agent questions.
- Renumber the following sections and **update the `<nav>` contents list**
  (currently 1–14, becomes 1–15): Matrix Testing 12→13, Settings 13→14,
  Tips 14→15.
- Add propagation rows to the Tips & Troubleshooting table.
- The page is served both standalone and inside an iframe modal and has its own
  inline theme script — verify new content renders in both themes.

### 7.2 `README.md`

- Features list: projects, artifact propagation, review workflow.
- Project Structure tree: `propagation/`, `routes/projects.py`, `tests/`,
  `scripts/`, new JS files.
- Dependencies table: `PyYAML` (front-matter) and any dev deps; note that
  `git` is a hard runtime requirement for managed projects (D8).
- New "Projects & Automatic Propagation" section with the golden scenario as a
  worked example.
- Do not add a `run_tests.py` or `scripts/live_model_matrix.py` reference unless
  C01b actually introduces that opt-in utility; the current repository has no
  such script.
- Document the `REQ-nnn` convention as a user-facing contract.

### 7.3 Example project

`examples/sqlite-to-mysql/` — a complete before/after project that doubles as
the golden-test fixture and as documentation. Keeping these the same artifact
prevents the docs from drifting away from the tests.
