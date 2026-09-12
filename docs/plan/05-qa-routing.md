# §5.4 Human Q&A Routing (D19–D22)

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **5.4**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.
>
> **Referenced by two commits in different phases:** C18 (worker mechanism) and C25 (UI). It is filed on its own rather than inside Phase 3 or Phase 5 for that reason.

---

### 5.4 Human Q&A routing (D19–D22) — answers Q18, Q21–Q23

**The question:** if the Database Developer has a question about a requirement,
how does it reach the user? And if everything funnels through the BA, should
persona selection be disabled on project folders?

**Answer: the BA conversation is the single inbox, but persona selection stays
enabled.** Disabling it would remove capability the user already has and expects,
and would break the manual workflow documented in `help.html` §11.

#### The `ask_question` tool

Distinct from `raise_issue` (D12), which reports a risk or a suggestion.

**Two severities (D20, Q21).** The agent must choose, and the prompt must make
the distinction concrete:

| | `blocking: true` | `blocking: false` |
|---|---|---|
| Meaning | The role **cannot** produce a correct artifact without an answer. | The role can proceed; an answer would improve the result. |
| Job outcome | Parks in `needs_input`; dependants `blocked`. | **Job completes.** The agent writes its best guess, marks the assumption inline, and the question rides along. |
| Example | "REQ-014 says MySQL but not which version — 5.7 and 8.0 have incompatible JSON semantics, so I cannot write the schema." | "Should MySQL connections require TLS? I have assumed yes." |

**Inline assumption marker.** A non-blocking question must leave a trace in the
artifact itself, not only in the issue table:

```markdown
> **⚠️ ASSUMPTION (Q-0031):** TLS is required for MySQL connections in shared
> deployments. REQ-014 does not specify. — Database Developer
```

- The marker carries the question ID so UI and document stay linked.
- The scanner indexes markers into `artifact_assumptions`, so the project panel
  can report "7 unresolved assumptions across 4 artifacts" — assumptions must be
  *visible debt*, not invisible drift.
- When the question is later answered, the marker is removed by the next
  propagation pass over that artifact. Verify this actually happens; a stale
  marker is worse than none.
- Markers are blockquote-formatted so they survive whole-file rewrites (D15) and
  are obvious to a human reader of the raw markdown.

**Escalation:** an agent may only use `blocking: true` when it genuinely cannot
proceed. If a wave produces more blocking than non-blocking questions, that is a
signal the persona prompts are miscalibrated — worth logging as a metric.

#### Batched digest (D21, Q22)

Six roles asking two questions each would put twelve messages in the BA thread
for one change. Instead:

- Questions accumulate against the change event; **nothing is posted immediately**.
- When **all jobs at a depth reach a terminal state**, one digest message is
  posted to the BA conversation covering that depth. A wave therefore produces at
  most one digest per depth (3 for the golden scenario), not one per question.
- Blocking questions are listed first, with the artifacts they block.
- Each entry keeps its own `[Accept proposed]` / `[Answer…]` / `[Not a
  requirement issue]` controls — the digest groups, it does not merge.
- **Exception:** if *every* depth-1 job blocks, post immediately rather than
  waiting. The wave has stalled and the user should know at once.
- Deduplicate within a digest: two roles asking the same thing about the same
  `req_id` appear once, attributed to both, and one answer resolves both.

#### Answer reuse (D22, Q23)

Answered questions become durable, reusable context:

- `agent_issues` rows are retained with their answers and indexed by
  `(project_id, req_id)`.
- `build_artifact_context()` (0.6) injects prior answers for the requirement IDs
  in scope, under a `Previously clarified` heading.
- Before inserting a new question, check for a semantically similar answered one
  on the same `req_id`. Start with exact/normalised text matching — **do not**
  add embeddings in v1.
- If a requirement's text later changes, its prior answers are marked
  `stale_context`: still shown, but flagged as possibly outdated. An answer about
  SQLite-only behaviour must not be silently reused after the MySQL change.
- The BA's own conversation history is *not* the mechanism — it is unbounded and
  the BA conversation may be deleted. The issue table is the record.

#### Routing

1. `ask_question` inserts an `agent_issues` row with `kind='question'`.
2. Questions accumulate; at the end of each depth a **digest** is posted into the
   BA conversation (`ba_conversation_id`), attributed per role:

   > **Questions from CHG-0007 (depth 1) — 1 blocking, 2 assumptions**
   >
   > **🛑 Database Developer — REQ-014** *(blocking `DB-MODEL-CORE`)*
   > MySQL 5.7 or 8.0? JSON column semantics differ incompatibly.
   > *Proposed: 8.0.*
   > `[Accept proposed]` `[Answer…]` `[Not a requirement issue]`
   >
   > **⚠️ DevSecOps Engineer — REQ-014** *(assumed, `SEC-RISK-DATA` written)*
   > Should MySQL connections require TLS? Assumed yes.
   > `[Accept proposed]` `[Answer…]` `[Not a requirement issue]`

3. The user answers in the BA conversation — the one place they already work.
4. The BA persona turns the answer into a **requirement change**, which is the
   correct outcome: the answer becomes durable in `BA-REQ`, not buried in a chat
   log. That edit produces a normal change event, which propagates normally.
5. A parked (blocking) job resumes with the answer injected, or is re-queued by
   the new change event. For a non-blocking question the artifact was already
   written, so answering simply removes the inline assumption marker on the next
   pass over that artifact.

**This is the key property:** a question is not answered *at* the asking role, it
is answered *into the requirements*. Otherwise the same question recurs on every
future wave and the requirement stays ambiguous forever.

"Not a requirement issue" is the escape hatch: the answer is recorded on the
issue and injected into that job only, without touching `BA-REQ`.

#### Why personas stay enabled on project folders

| Concern | Resolution |
|---|---|
| User wants to talk *directly* to the Database Developer | Fully supported. Any conversation in a project folder may use any persona, exactly as today. |
| Won't ad-hoc chats bypass the BA and create untracked decisions? | They cannot silently change artifacts — the `propagation` tool profile is not active in normal chat, and `write_file` still cannot reach workspace sub-directories (0.5). Manual edits are detected on the next scan and surface as `conflict` (2.4). |
| Two sources of truth? | No. Artifacts are the only source of truth, and only reviewed writes reach them. |
| Then what is special about the BA conversation? | Only that it is the **designated inbox** (`ba_conversation_id`). It is an ordinary conversation with the BA persona; the project just knows which one to post questions into. |

**Consequence for the UI:**

- The BA conversation is visually marked as the project inbox, with an unanswered
  count badge.
- If it is missing or deleted, the project panel offers *"Designate a BA
  conversation"* rather than failing. Questions queue in the panel meanwhile —
  they must never be lost.
- Unanswered blocking questions are shown on the project panel too, so they are
  not missed if the user is not reading the BA thread.
- **Unresolved assumptions get their own panel count** (D20). They are the quiet
  failure mode: the wave looks green, but N documents contain guesses. An
  artifact carrying assumptions renders as `current (N assumptions)`, not plain
  `current`.
- A question older than a configurable age is escalated visually; a wave with
  parked jobs must never look "finished".

**Acceptance tests:**

The tests below are split by delivery boundary: C18 covers the server-side
mechanism and persistence; C21 covers HTTP answer/dismiss integration; C25
covers rendering and browser controls. The end-to-end BA edit and
re-propagation check is not a C18 gate.

- **C18:** `ask_question(blocking=true)` → job `needs_input`, dependants `blocked`, entry
  in the depth digest attributed to the asker.
- **C18:** `ask_question(blocking=false)` → **job completes**, artifact written, inline
  `⚠️ ASSUMPTION (Q-nnnn)` marker present, question recorded.
- **C18:** Marker is indexed into `artifact_assumptions`; **C25:** the marker is
  counted in the panel.
- **C18:** Answering a non-blocking question → marker removed on the next pass over that
  artifact; assumption count drops.
- **C18:** Six questions across one depth → **one** digest message, blocking listed first.
- **C18:** All depth-1 jobs blocking → digest posted immediately, not deferred.
- **C18:** Two roles asking the same thing about one `req_id` → one digest entry, one
  answer resolves both.
- **C18:** Answer reuse: a second wave touching the same `req_id` receives the prior answer
  in context and does not re-ask.
- **C18:** Requirement text changes → its prior answers are flagged `stale_context`.
- **C21/C25:** Answering via *Accept proposed* → BA edits `BA-REQ` → new change event → parked
  job resumes and completes.
- **C21:** "Not a requirement issue" → job resumes, `BA-REQ` untouched.
- **C18/C25:** Project with no BA conversation → questions queue in the panel, nothing lost.
- **C18:** Ad-hoc chat with the Database Developer persona in a project folder cannot
  write to a registered artifact path.
- **C18:** A wave with one parked question does not report as complete.
