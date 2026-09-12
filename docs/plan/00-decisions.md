# Design Decisions D1–D22 & Non-goals

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **0 (D1–D22), Non-goals**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
>
> **These decisions are settled. Read this file once, or not at all.** It is
> settled input, not work, and no commit's `Ref` column points here. Only open it
> to resolve a contradiction between two other sections.

---

## 0. Design Decisions (agreed before coding starts)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Artifacts are files on disk** in a project workspace; the DB only holds an index (path, hash, version, status). | Files stay human-editable and Git-diffable. No content duplication to keep in sync. |
| D2 | Every artifact carries **YAML front-matter** with `artifact_id`, `role`, `version`, `derives_from`. | Makes the dependency graph and traceability data, not prose. |
| D3 | Requirements carry **stable IDs** (`REQ-014`). Impact analysis is a deterministic set-intersection on these IDs, *not* an LLM guess. | Cheap, repeatable, testable. LLM is used only to *write*, never to *decide what changed*. |
| D4 | Propagation runs in **topological order** of the dependency graph. | QA must read the *updated* data model, not the stale one. |
| D5 | Default mode is **`propose`** (write diffs to a staging area, human approves). `auto` is opt-in per project. | An LLM silently rewriting six documents is unreviewable. |
| D6 | Only **human-authored** edits open a new change chain. Propagation-authored edits do not cascade further (`origin` flag + `max_depth`). | Prevents infinite agent loops. |
| D7 | Job execution uses a **single daemon worker thread** polling a SQLite job table. | The app is single-process Flask; no Celery/Redis dependency. Survives browser disconnect, which SSE-driven work does not. |
| D8 | Versioning via **Git in the workspace**. *(Confirmed — git is a hard requirement.)* `git init` on project create; every applied wave is one commit. | Free history, diff and one-click rollback. Especially valuable given whole-file rewrites (D15). |
| D9 | Extend the existing **`folders`** table into the project container rather than adding a parallel concept. | The sidebar folder, the on-disk project and the artifact set are the same thing to the user. |
| D10 | `role`→`artifact` mapping comes from a **project template**, seeded like `STARTER_PERSONAS`. | Non-SDLC project types can be added later without code changes. |
| D11 | Propagation agents get a **restricted tool allowlist** — no `run_python`, no unrestricted `write_file`. | An unattended agent with an unsandboxed subprocess is a different risk class from a supervised chat. |
| D12 | A downstream role that cannot reconcile a change **raises an issue back to the BA** (`needs_input`) instead of silently writing something wrong. | Otherwise genuine insights are buried in a document nobody re-reads. |
| D13 | Agents receive **only the upstream artifacts their role declares**, not the whole project folder. | `build_linked_folder_context()` re-injects every file every turn and silently truncates at 500k chars. |
| D14 | Every propagation run has a **token budget** and a kill switch; runs exceeding it stop and report. | Cost control on a 6-agent fan-out. |
| D15 | Agents perform a **whole-artifact rewrite**, not targeted patches. *(Confirmed)* | Far more reliable with current models. The cost is mitigated by D16 — small artifacts mean small rewrites. |
| D16 | A role may own **any number of artifacts**. One-document-per-folder is a default, never a rule. *(Confirmed)* | Keeps each whole-file rewrite (D15) small, cheap and low-variance. See 1.3 for the constraints this imposes. |
| D17 | The artifact set comes from a **template, extensible with human approval**. *(Confirmed)* Propagation itself may never add or remove artifacts. | Stable, testable baseline; inbound `derives_from` edges never break under an agent's whim. |
| D18 | **Soft cap of 10 artifacts per role.** Exceeding it requires explicit user approval, raising that role's cap. *(Confirmed)* | Guards against runaway document sprawl and runaway cost, without a hard ceiling. |
| D19 | **All human Q&A flows through the Business Analyst conversation.** Downstream roles raise structured questions; the BA is the single inbox. Personas are *not* disabled on project folders — see 5.4. | One place to answer, and answers land where they belong: in the requirements. |
| D20 | Questions are **blocking** or **non-blocking**. Non-blocking → the agent writes its best guess, marks the assumption **inline in the artifact**, and completes. *(Confirmed)* | A wave must not stall on trivia, but a guess must never be invisible. |
| D21 | Questions are **batched into one digest per depth**, posted when that depth completes. *(Confirmed)* | 6 roles × 2 questions would otherwise be 12 messages for one change. |
| D22 | Answers are **retained and reused** as context, keyed by `req_id`, and marked `stale_context` if the requirement later changes. *(Confirmed)* | Never ask the same question twice — but never reuse an answer that the requirement has invalidated. |

### Non-goals for v1

- Multi-user concurrent editing / merge conflict resolution.
- Real-time filesystem watching (polling on demand is enough).
- Propagating into source code. Documents only.
- Automatic requirement *authoring* — the BA is still human-driven.
