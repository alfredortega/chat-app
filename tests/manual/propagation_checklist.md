# Manual Propagation Checklist (8.1)

Run in the browser against a live install, in **both** the dark and light themes.
Every item below must hold; mark it only when you have verified it in *both*
themes.

## Setup

- [ ] `git` is installed (`git --version` succeeds).
- [ ] A project was created (Folder → New Folder → code/project toggle) and the
      sidebar shows it with the blue **Project** badge.
- [ ] The workspace contains `Requirements/`, `Design/`, `Data/`, `Test Cases/`,
      `Security/`, `Project Plan/` and `.agents/`.

## Golden scenario (8.2)

1. [ ] Open the project panel; it lists one artifact per role with a roll-up
       status badge (`current`).
2. [ ] In the BA conversation, edit `Requirements/BA-REQ.md` so `REQ-014`
       changes from "SQLite only" to "SQLite **and** MySQL via SQLAlchemy".
3. [ ] Trigger *Scan* — the project panel shows exactly one change event whose
       changed requirements include `REQ-014`.
4. [ ] *Propagate* — the wave runs (SSE progress stream shows jobs updating) and
       five proposals appear under `propose` mode; nothing is applied yet.
5. [ ] The review modal shows each proposal as a unified diff.
6. [ ] Approve all → every artifact is `current` again with `version` bumped and
       `derives_from` updated.
7. [ ] Rollback → the workspace is byte-identical to the state before the wave.
8. [ ] A second scan produces **zero** new change events (no loop).

## Questions and assumptions (5.4)

- [ ] Script a non-blocking assumption: the downstream artifact contains an
      inline `⚠️ ASSUMPTION (Q-…)` marker and the assumptions count on the panel
      increments.
- [ ] Script a blocking question: the wave parks the job, its dependants are
      blocked, and the BA conversation receives one digest for that depth.
- [ ] Answer in the BA conversation → answer appears in subsequent prompts
      ("Previously clarified") and the same question is not re-asked.
- [ ] Delete the designated BA conversation: questions remain queued in the
      panel and nothing is lost.

## Conflicts and safety

- [ ] Hand-edit a downstream artifact → the next scan marks it `conflict`, and a
      propagation pass does **not** overwrite it.
- [ ] Set a low token budget → a wave cancels remaining jobs with a clear reason.
- [ ] Pull the kill switch → mid-wave jobs are cancelled, none stay `running`.

## Both themes

- [ ] Status badges, the project panel, and the diff colours are readable in both
      the dark and light themes.