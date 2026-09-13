# My Chat App

A rich web-based chat application built on a Python Flask and SQLite backend, with a highly interactive vanilla JavaScript sidebar and chat interface.

## Core Features

- **API Endpoints:** Connect to multiple hosted (OpenAI, Gemini) or local (LMStudio, Ollama) providers.
- **Personas:** Steer responses with specialized prompts like Python Developer, Technical Writer, or DevSecOps Engineer.
- **Sidebar Organization:** Create, rename, delete, and import/export folders to keep conversations structured.
- **Projects & Automatic Persona Propagation:** Managed workspaces where a change to a requirement automatically identifies affected artifacts (UX, Database, QA, Security, Project Plan), regenerates them in dependency order, and presents every rewrite as a reviewable diff before anything is applied.
- **File Reference:** Live-link folders or upload individual files (PDFs, Docx, spreadsheets, CSV, JSON/YAML, text) to provide background context.
- **Saving Outputs:** Automatically save generated code and content splits directly to local output paths.

## Projects & Automatic Propagation

A managed project scaffolds six artifacts across six roles. Each artifact lives in a git-tracked workspace and carries YAML front-matter; the requirements document uses stable `## REQ-nnn — Title` headings.

The propagation pipeline is intentionally deterministic:

1. **Detect** — a human edit to `BA-REQ` becomes a change event listing exactly which `REQ-nnn` IDs changed.
2. **Analyse** — traceability (which artifacts reference the changed IDs) plus the dependency graph decide what must be regenerated.
3. **Propose** (default mode) — each affected artifact is rewritten into `.agents/proposals/`; you approve, reject, apply-all or roll back.
4. **Loop prevention** — propagation-authored writes never open a new change chain, and a token budget caps spend.

Downstream roles raise *blocking* questions (wave pauses) or record *assumptions* inline; questions arrive as a batched digest in a designated BA conversation and answers become reusable context. `notify`, `propose`, and opt-in `auto` modes control how far automation goes.

Example: changing `REQ-014` from "SQLite only" to "SQLite **and** MySQL via SQLAlchemy" queues `UX-WIRE`, `DB-MODEL`, `SEC-RISK`, `QA-PLAN` and `PM-PLAN` at dependency depths `1,1,1,2,3` and produces an updated artifact for each — see `examples/sqlite-to-mysql/`.

## Project Structure

```
app.py                # Flask application factory + legacy routes
chat_service.py       # Bounded, reusable chat/tool-loop service
conversation_context.py # Single entry point for chat context (role-scoped / linked-folder)
database.py           # SQLAlchemy models; artifact, change-event, job, issue tables
file_handler.py       # Uploads, file context, scoped artifact context builder
propagation/          # scanner, changes, impact, worker, agent, proposal, qa,
                      # notify, loop_guard, adopt, archive
routes/projects.py    # Phase 4 project API blueprint
routes/research.py    # Research-sources blueprint
templates.py          # Project template registry + role->artifact edge expansion
git_integration.py    # Git init/commit/diff/rollback for workspaces
migrations/           # Alembic migration tooling (forward only — runtime
                      # bootstrap still uses db.create_all() + self-heal)
tests/                # pytest suite (unit + integration, no network)
```

## Dependencies

| Runtime | Purpose |
|---|---|
| Flask, Flask-SQLAlchemy | web server + ORM |
| openai | chat/model client |
| PyYAML | artifact front-matter parsing |
| pymysql, cryptography | MySQL support, API-key encryption |
| pypdf, python-docx, openpyxl | file text extraction |
| **git (system)** | hard runtime requirement for managed projects (D8) |

Dev: `pytest`, `pytest-cov`, `pytest-timeout`, `freezegun`.

## Getting Started

1. **Setup the Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run the Application:**
   ```bash
   python app.py
   ```
   Open your browser and navigate to `http://localhost:5000`.
