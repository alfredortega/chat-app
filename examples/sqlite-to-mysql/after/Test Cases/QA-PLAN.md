---
artifact_id: QA-PLAN
role: QA/Tester
version: 2
origin: propagation
derives_from:
  - artifact: BA-REQ
    version: 3
    requirements: [REQ-001, REQ-002]
  - artifact: DB-MODEL
    version: 2
    requirements: [REQ-001, REQ-002]
---
# QA Test Plan

## Test Scenarios

### SQLite Database Tests (REQ-001)
- TC-001: Create database file
- TC-002: CRUD operations on all tables
- TC-003: WAL mode verification
- TC-004: Busy timeout behavior
- TC-005: Foreign key constraint enforcement
- TC-006: JSON field serialization (TEXT storage)

### MySQL Database Tests (REQ-002)
- TC-007: Create database/schema
- TC-008: CRUD operations on all tables
- TC-009: Connection pool behavior
- TC-010: SSL connection verification
- TC-011: JSON field native type support
- TC-012: Charset utf8mb4 handling
- TC-013: Connection recycle/retry logic

### SQLAlchemy ORM Tests (Both Engines)
- TC-014: Model CRUD via ORM
- TC-015: Relationship loading (lazy/eager)
- TC-016: Query compilation for both dialects
- TC-017: Migration up/down (Alembic)
- TC-018: Schema comparison (SQLite vs MySQL)

### Engine Switching Tests
- TC-019: Switch SQLite → MySQL
- TC-020: Switch MySQL → SQLite
- TC-021: Data migration integrity
- TC-022: Configuration persistence

## Acceptance Criteria

- All tests pass on both SQLite and MySQL
- Zero dialect-specific failures
- Migration round-trip: SQLite → MySQL → SQLite preserves data