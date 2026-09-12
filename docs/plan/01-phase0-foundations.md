# Phase 0 — Foundations & Guardrails

> **Part of the [Agentic Persona Propagation plan](../../to_do.md).**
> Owns sections **0.1–0.6**. Section numbers are unchanged from the
> original single-file plan, so every `see 0.5` / `D11` / `2.3` cross-reference
> still resolves — use the file map in the hub to find the owning file.
> Decisions D1–D22 are settled: see [00-decisions.md](./00-decisions.md) and do
> not relitigate them.
>
> §0.7 (agent-session hazards) lives in the **hub**, because it applies to every commit, not just Phase 0.

---

## Phase 0 — Foundations & Guardrails

**Nothing else can be built or tested until these land.** Every item here is a
refactor or hardening task on existing code, with no user-visible change.

### Repository alignment prerequisite (C00)

This repository does not have the `routes/`, `chat_stream.py`, `clients.py`,
`prompt_builder.py`, or `sse.py` modules referenced by the original plan. Chat
and regeneration currently live in `app.py`, and the application initializes
the database during module import. Before the test harness is added:

- Extract `create_app(config=None)` while preserving existing routes and SSE
  behavior.
- Make the SQLAlchemy database URI and encryption key injectable for tests.
- Keep production defaults unchanged, including the existing SQLite database.
- Ensure importing the module does not initialize the production database or
  append `ENCRYPTION_KEY` to `.env`.
- Add a legacy compatibility smoke test covering app creation and one existing
  route.

This is C00 in the hub and is a prerequisite for all later test fixtures.

### 0.1 Add a real test framework

**Split into four commits — C01a–C01d.** The original single C01 was attempted
three times and hung every time; see 0.7 for the diagnosed causes. Do not
re-merge it.

#### 0.1a Harness only (C01a)

- `requirements-dev.txt`: `pytest`, `pytest-cov`, `pytest-timeout`, `freezegun`.
  - `pytest-timeout` is **mandatory, not optional**. The tool-call loop in
    the current chat loop in `app.py` is `while True:` with no upper bound until C02, so a badly
    scripted fake can hang the suite forever (0.7).
  - `freezegun` is listed for later use (debounce, 2.2). Nothing in Phase 0
    needs it.
- `pytest.ini` at the repo root:
  - `testpaths = tests`
  - `timeout = 30` — every test dies rather than hanging the session.
  - `addopts = -q`
- `tests/conftest.py` — **autouse `no_network` fixture**: monkeypatch
  `socket.socket.connect` (and `socket.create_connection`) to raise, so any
  accidental outbound call fails loudly instead of dialling a provider. This is
  the mechanism behind "zero network calls"; it was previously unspecified.
- One smoke test asserting the current `database`, `tools`, and `file_handler`
  modules import cleanly.
- The app import smoke test belongs to C00/C01c and must use the factory.

**Acceptance:** `pytest` green; `chat.db` mtime unchanged; `.env` unchanged.

#### 0.1b Live-provider utility boundary (C01b)

- There is currently no `run_tests.py` or live model matrix utility in this
  repository. Do not invent a rename or claim a migration was completed.
- If a live-provider utility is needed later, place it under `scripts/`, make
  it import-safe and explicitly opt-in, and keep it outside pytest discovery.
- Document the actual invocation and network requirement only after the utility
  exists.

**Acceptance:** pytest discovery is clean; any live utility import succeeds
without executing network code, and no nonexistent `run_tests.py` reference is
introduced.

#### 0.1c DB + workspace fixtures (C01c)

- C00 already solves the import-time initialization problem. Build fixtures
  around `create_app()` and an isolated SQLAlchemy SQLite URI, not a global
  `CHAT_DB_PATH` patch.
- Set a fixed throwaway `ENCRYPTION_KEY` before creating the app so the actual
  `database._get_encryption_key()` implementation never appends to `.env`.
- Fixtures: `tmp_db` (temporary database URI + `init_db()`), `workspace` (temp
  project directory tree), and `client` (Flask test client from `create_app()`).

**Acceptance:** fixture self-tests green; the real `chat.db` and `.env` remain
untouched (assert mtime); one legacy route is exercised through the factory.

#### 0.1d Fake LLM client (C01d) — see 0.2

**Acceptance:** unit tests *of the fake itself*. The multi-turn tool-loop drive
test belongs to C02, not here.

### 0.2 Deterministic fake LLM client

`tests/fakes.py` — `FakeOpenAIClient` mimicking the provider object used by
`app.get_client()` and `client.chat.completions.create(...)` for both
`stream=True` and `stream=False`.

- Scripted responses: a queue of `(text | tool_calls)` turns.
- Records every call so tests can assert *what the agent was told* (prompt
  contents, tool availability, model id).
- Injected through the app/service client factory; do not assume a nonexistent
  `clients.py` module.
- Must also be able to simulate the zero-chunk failure that the current chat
  path already guards against, and a mid-stream exception.
- **Script exhaustion must raise**, never repeat the last turn and never return
  an empty turn. A fake that re-yields a `tool_calls` response spins the
  unbounded `while True:` loop in the current chat path forever — this is the most
  likely cause of the three frozen C01 attempts (0.7).

**Acceptance (C01d):** unit tests of the fake in isolation — scripted text
turn, scripted tool-call turn, zero-chunk case, mid-stream exception,
exhaustion raises, call recording captures model id / messages / tools.

**Deferred to C02:** driving a full multi-turn tool-calling loop through the
extracted chat service. That test is only safe once `max_iterations` exists,
and it lands in the same commit as the guard.

### 0.3 Extract the bounded chat service and structured events

Chat and regeneration currently contain duplicated streaming/tool-call loops
in `app.py`, and both use `while True`. Extract the loop into a reusable
service that can be consumed by the HTTP SSE adapter and a background worker.

- Extract the loop body into `run_chat_turn(...) -> Iterator[dict]` yielding
  plain dicts (`{'type':'token',...}`, `{'type':'tool_result',...}`,
  `{'type':'error',...}`, `{'type':'done'}`).
- The HTTP SSE adapter becomes a thin adapter:
  `for ev in run_chat_turn(...): yield sse_event(ev)`.
- The worker consumes `run_chat_turn()` directly and discards token events.
- Add an optional `tools` parameter so callers can pass a restricted allowlist
  instead of the module-level `TOOLS` constant (needed by 0.5 / D11).
- Add an optional `max_iterations` guard — the `while True:` loop currently has
  no upper bound on tool-call rounds. **This guard is the prerequisite for every
  test that drives the loop**, which is why the multi-turn test moved here from
  0.2. Default it generously (e.g. 25) so live chat behaviour is unchanged.

**Acceptance:** characterisation test captures current SSE byte output for a
scripted conversation *before* the refactor, then asserts byte-identical output
after. Chat and regenerate behaviour unchanged. A scripted fake that would loop
forever terminates with an `error` event instead.

### 0.4 Make SQLAlchemy safe for a background thread

Three concerns in the current Flask-SQLAlchemy layer block D7:

1. **Session lifetime.** Worker jobs must use an app context and a scoped
  SQLAlchemy session, then remove/close it after every job.
2. **SQLite concurrency.** A background writer plus request-thread readers may
  hit `database is locked`. Configure SQLite connections with WAL and
  `busy_timeout` through the SQLAlchemy engine and verify this with the ORM.
3. **Repair work and startup cost.** Any existing message-repair routine must
  not rescan all conversations on every app initialization. Gate it behind a
  one-time `settings` watermark (`tool_call_repair_version`).

**Acceptance:** unit test spawns 4 app-context threads doing interleaved ORM
read/write with zero `OperationalError`; a worker-style test asserts sessions
are removed after repeated job execution.

### 0.5 Project-scoped, context-aware tools

`tools.py` blocks propagation in two ways:

1. `_write_file()` deliberately reduces any relative path to
   `os.path.basename(path)`, so it **cannot write to `Requirements/` or
   `Test Cases/`**. This is correct for the chat UI and must not change.
   → Add a *new* `write_artifact` tool, jailed to the project **workspace root**
   (not the output dir), that permits a whitelisted set of sub-directories and
   still rejects `..` traversal via the existing `commonpath` check.
2. `TOOLS` is a module-level constant handed wholesale to every caller.
   → Introduce `build_tools(context: str) -> list[dict]` with named profiles:
   - `chat` — today's four tools (unchanged default).
   - `propagation` — `read_artifact`, `write_artifact`, `list_artifacts`,
     `ask_question` (D19), `raise_issue` (D12), `propose_artifact` (D17).
     **No `run_python`** (unsandboxed subprocess, D11).
   → `execute_tool_call()` gains a `context`/`workspace_dir` parameter and
   refuses tools outside the active profile.

**Acceptance:** parametrised traversal tests (`../`, absolute paths, UNC paths,
`Requirements/../../x`) all rejected; a propagation-context call to
`run_python` returns an error rather than executing.

### 0.6 Scoped context builder

`file_handler.build_linked_folder_context()` re-reads and re-injects **every**
linked file on **every** turn, truncating at `HARD_LIMIT` (500k chars) with only
a footer note. For a 6-artifact fan-out this is expensive and risks truncating
the very artifact being edited.

- New `build_artifact_context(project_id, artifact_keys, change_event)` that
  injects only the declared upstream artifacts plus the change summary/diff.
- Also injects **prior answers** for the requirement IDs in scope (D22), under a
  `Previously clarified` heading, with any `stale_context` ones clearly marked as
  possibly outdated.
- Return an explicit `(context, char_count, truncated_keys)` triple so the
  caller can fail loudly rather than silently truncate.

**Acceptance:** context for the golden scenario contains `BA-REQ` and the diff
but not `PM-PLAN`; oversized input raises rather than silently truncates.
