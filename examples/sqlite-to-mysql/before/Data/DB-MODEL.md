---
artifact_id: DB-MODEL
role: Database Developer
version: 1
origin: propagation
derives_from:
  - artifact: BA-REQ
    version: 2
    requirements: [REQ-001, REQ-002]
---
# Database Model

## Schema Overview

Single SQLite database with standard tables:
- `users`
- `conversations`
- `messages`
- `settings`

## Connection Strategy

- SQLite with WAL mode enabled
- Busy timeout: 5000ms
- Foreign keys: ON

## Migration Safety

- No destructive changes allowed
- Version tracking via `schema_version` table