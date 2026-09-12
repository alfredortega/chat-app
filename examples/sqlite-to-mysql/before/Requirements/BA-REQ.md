---
artifact_id: BA-REQ
role: Business Analyst
version: 2
origin: human
derives_from: []
---
# Business Requirements

## REQ-001 — Local SQLite Database

The application shall use a local SQLite database for data persistence.

SQLite is chosen for its simplicity, zero-configuration deployment, and suitability for single-user or small-team applications.

## REQ-002 — Single Database Engine

The system shall support only SQLite as the database engine.

No other database engines (MySQL, PostgreSQL, etc.) are required for the initial release.