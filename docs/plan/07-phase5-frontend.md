# Phase 5 — Frontend

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **5.1–5.3**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.
>
> §5.4 is in [05-qa-routing.md](./05-qa-routing.md).

---

## Phase 5 — Frontend

### 5.1 New files

- `static/js/projects.js` — new project panel: artifact status board
  (`current` / `stale` / `updating` / `conflict` / `needs_review` / `blocked`),
  **grouped by role with a roll-up status** — D16 means 20+ rows and a flat list
  will not scale — plus change history, read-only propagation-mode and token-budget
  status, pending artifact requests (D17), open questions and an **unresolved
  assumption count** (D20).
- `static/js/diff_view.js` — unified-diff renderer for the review modal.
  **No diff library is currently loaded**; either add `diff2html` via CDN
  (consistent with the existing Bootstrap/marked/highlight.js approach) or
  hand-roll ~60 lines of CSS. Decide in C24, when this renderer is implemented.
- Both must be added to the existing script block at the bottom of
  `static/index.html`. Preserve the current load order and initialize the panel
  from the existing `app.js` lifecycle rather than assuming a separate
  `project.js` entry point.

### 5.2 Changes to existing files

- `static/js/api.js` — add the Phase 4 calls under a new `// ── Propagation`
  section, matching the existing style.
- `static/js/folders.js` — render a project folder distinctly (icon + badge with
  pending-proposal count).
- `static/js/folders.js` / `static/js/app.js` — replace or extend the existing
  Code Folder creation flow. There is no `project.js`; the current UI posts
  `{name, code_folder}` to `/api/folders` and must gain an explicit managed
  project flow without breaking ordinary folders.
- `static/index.html` — project panel container, review modal, conflict modal.
- `static/css/style.css` — status badges, diff colours (must work in **both**
  themes; the app defaults to dark).

### 5.3 UX requirements

- Nothing is applied without review in `propose` mode — the default.
- A visible, cancellable progress indicator per wave.
- Rollback is one click on a change event, not per file.
- Conflicts are visually distinct from staleness and cannot be bulk-applied.
- Artifact-extension requests (D17) are approved/rejected from the project panel.

**Acceptance:** manual test script (Phase 7) walking the golden scenario
end-to-end in the browser, in both themes.
