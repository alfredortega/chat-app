---
artifact_id: DB-MODEL
role: Database Developer
version: 2
origin: propagation
derives_from:
  - artifact: BA-REQ
    version: 3
    requirements: [REQ-001, REQ-002]
---
# Database Model

## Schema Overview

Dual-engine schema compatible with SQLite and MySQL via SQLAlchemy ORM:

- `users` — User accounts and authentication
- `conversations` — Chat conversations
- `messages` — Individual messages with tool call support
- `settings` — Application and user settings
- `projects` — Project workspace metadata (NEW)
- `artifacts` — Artifact registry (NEW)
- `artifact_deps` — Dependency graph (NEW)
- `artifact_traces` — Requirements traceability (NEW)
- `change_events` — Change event log (NEW)
- `propagation_jobs` — Propagation job queue (NEW)
- `agent_issues` — Q&A and issue channel (NEW)
- `artifact_assumptions` — Inline assumption markers (NEW)
- `artifact_requests` — Artifact extension requests (NEW)

## SQLAlchemy Configuration

### Engine-Agnostic Models
- Use SQLAlchemy declarative base
- Type annotations for cross-dialect compatibility
- Avoid dialect-specific types where possible

### Dialect-Specific Handling
- SQLite: Use `JSON` type (stored as TEXT)
- MySQL: Use native `JSON` type
- SQLite: `DATETIME` stored as ISO string
- MySQL: Native `DATETIME` type

## Connection Strategy

### SQLite (Development/Testing)
- WAL mode enabled
- Busy timeout: 5000ms
- Foreign keys: ON
- Connection pool: `StaticPool` (single-threaded)

### MySQL (Production)
- Connection pool: `QueuePool` (size=10, max_overflow=20)
- Pool recycle: 3600s
- SSL: Required for remote connections
- Charset: utf8mb4

## Migration Safety

- Alembic for schema migrations
- Version tracking via `alembic_version` table
- No destructive changes allowed without explicit approval
- Rollback scripts required for all migrations