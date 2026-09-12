# Open Questions

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **Open Questions, Phase 9 shape**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.
>
> The Resolved table is kept for provenance only — those answers are already folded into D1–D22. Q6–Q13 and Q19–Q27 are genuinely open but **non-blocking**.

---

## Open Questions

### Resolved

| Q | Answer | Recorded as |
|---|---|---|
| 1 | **Yes** — requiring the BA to emit stable `REQ-nnn` IDs is acceptable. | D3, 1.2 |
| 2 | **Many artifacts per role.** One-per-folder is a default, not a rule. | D16, 1.3 |
| 3 | **Confirmed** — `propose` for new projects, `off` for adopted ones. | 3.3 |
| 4 | **Yes** — git is a hard requirement. No snapshot fallback needed. | D8 |
| 5 | **Whole-artifact rewrite**, not targeted patches. | D15, 3.2 |
| 14 | **Template with optional, human-approved extension.** | D17, 1.3 |
| 15 | **Soft cap of 10 per role**, then prompt the user to raise it. | D18, 1.3 |
| 18 | Questions route to the **BA conversation as a single inbox**; personas stay **enabled** on project folders. | D19, 5.4 |
| 21 | **Non-blocking** questions → write the best guess, mark the assumption **inline**, complete the job. | D20, 5.4 |
| 22 | **One digest per depth**, not one message per question. | D21, 5.4 |
| 23 | **Reuse prior answers** by `req_id`; flag `stale_context` when the requirement changes. | D22, 5.4, 0.6 |

**No blocking questions remain.** The design is settled enough to start Phase 0.

These interlock: whole-file rewrites (Q5) are affordable only because artifacts
are small (Q2); small artifacts are only cheaper if selection is
artifact-level (2.3); and that is only deterministic because requirement IDs are
stable (Q1). **Artifact-level trace selection is the load-bearing piece.**

### Simplifications unlocked

- **Q4 → git required** removes the entire `.agents/versions/` snapshot fallback,
  its tests, and the "which mechanism is active?" branching. `start.py` should
  check for `git` at launch and warn clearly if absent.

### New, raised by the Q15 / Q18 answers

19. **Cap scope.** Is the 10-artifact cap **per role** (60 total for six roles) or
    **per project**? I have specified per role — confirm.
20. **Who approves an extension request?** Any user, or should it be gated behind
    the same confirmation as enabling `auto` mode? Approving an artifact
    permanently widens the graph and increases every future wave's cost.

### New, raised by the Q21–Q23 answers

24. **Assumption debt policy.** Should a wave refuse to run while more than N
    unresolved assumptions exist, or is a visible count enough? A count is
    simpler; a gate prevents compounding guesses on guesses.
25. **Are assumption markers rendered specially in the chat/preview UI**, or left
    as plain markdown blockquotes? Plain is zero work and survives everywhere.
26. **Answer-reuse matching.** Exact/normalised text match on `req_id` for v1 —
    confirm no embeddings? Risk is a near-duplicate question slipping through,
    which seems acceptable versus adding a vector dependency.
27. **Digest timing when a depth is slow.** If depth 1 takes ten minutes, does the
    user wait for the digest, or should a blocking question surface immediately in
    the *panel* while the digest still batches for the *conversation*? I lean the
    latter — panel is live, digest is the readable summary.

### Important

6. Per-role model selection — worth the config surface, or use the conversation's
   model for everything?
7. Should propagation conversations appear in the sidebar (transparent but
   noisy), be collapsed under the project, or be hidden with a drill-in? With
   same-role batching this is one conversation per role per wave, not per
   artifact — so up to 6 per wave.
8. Does the D12/D19 feedback loop need to *auto-create* a draft requirement, or
   only raise the question for the BA to turn into one? Currently specified as
   the latter, with the BA doing the authoring.
9. Scope of "project": only ever the six SDLC roles, or a general mechanism where
   any persona set can be wired into a graph?
10. Concurrency — single-user assumption safe, or is a shared DB on a network
    share in play?

### Nice to know

11. Token budget default per wave?
12. Retention — how long to keep proposals, change events and agent
    conversations?
13. Should the matrix-test harness be extended to evaluate *propagation quality*
    across models?

---

## Phase 9 — Delivery Order

> Superseded by the **Delivery Commits** section at the end of this document,
> which breaks these nine coarse steps into 34 individually verifiable commits
> with tracked status. Keep this table only as the high-level shape.

| Step | Deliverable | Gate |
|---|---|---|
| 1 | C00 + Phase 0 | Legacy compatibility green; `pytest` green; chat SSE unchanged |
| 2 | Phase 1 schema + parsers | Unit tests green; existing DBs migrate cleanly |
| 3 | Phase 2 scanner & impact analysis, `notify` mode only | Golden scenario stops after correct job queue |
| 4 | Phase 3 worker, `propose` mode | Golden scenario produces 5 correct proposals |
| 5 | Phase 4 API | Route tests green |
| 6 | Phase 5 UI | Manual checklist passes |
| 7 | Phase 6 migration/compat + persona prompts | Export/import round-trip; adopt flow works |
| 8 | Phase 7 docs (`help.html`, `README.md`, example) | Docs match behaviour |
| 9 | `auto` mode behind a confirmation | Loop-prevention regression green |
