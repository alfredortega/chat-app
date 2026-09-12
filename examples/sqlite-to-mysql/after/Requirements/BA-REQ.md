---
artifact_id: BA-REQ
role: Business Analyst
version: 3
origin: human
derives_from: []
---
# Business Requirements

## REQ-001 — Local SQLite Database

The application shall use a local SQLite database for data persistence.

SQLite is chosen for its simplicity, zero-configuration deployment, and suitability for single-user or small-team applications.

## REQ-002 — SQLite and MySQL via SQLAlchemy ORM

The application shall support both SQLite and MySQL database engines, accessed via the SQLAlchemy ORM.

This enables deployment flexibility:
- **SQLite**: Development, testing, single-user deployments
- **MySQL**: Production, multi-user, high-concurrency environments

SQLAlchemy provides database-agnostic query interface and connection pooling.