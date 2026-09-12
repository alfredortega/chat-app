---
artifact_id: SEC-RISK
role: Security Analyst
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
# Security Risk Register

## Risks

### RSK-001: SQLite File Permissions
- **Likelihood**: Medium
- **Impact**: High
- **Mitigation**: Set restrictive file permissions (600) on database file

### RSK-002: MySQL Credentials Management
- **Likelihood**: High
- **Impact**: Critical
- **Mitigation**: 
  - Encrypted credential storage (Fernet)
  - Environment variable override
  - Rotation policy documented

### RSK-003: MySQL Network Exposure
- **Likelihood**: Medium
- **Impact**: High
- **Mitigation**: 
  - SSL/TLS required for all connections
  - Firewall rules restricting access
  - Non-default port configuration

### RSK-004: Connection Pool Exhaustion
- **Likelihood**: Medium
- **Impact**: Medium
- **Mitigation**: 
  - Pool size limits (10 base, 20 overflow)
  - Pool recycle: 3600s
  - Monitoring and alerting

### RSK-005: SQL Injection via ORM
- **Likelihood**: Low
- **Impact**: Critical
- **Mitigation**: 
  - SQLAlchemy parameterized queries
  - No raw SQL in application code
  - Code review requirement

### RSK-006: Dual Engine Attack Surface
- **Likelihood**: Medium
- **Impact**: Medium
- **Mitigation**: 
  - Same security controls for both engines
  - Regular vulnerability scanning
  - Patch management for MySQL server

## Compliance

- GDPR: Data stored locally (SQLite) or on controlled MySQL; user controls data
- SOC2: MySQL access logging, encryption in transit, credential management
- PCI-DSS: Not applicable (no payment data)

## New Controls for MySQL

1. Credential encryption at rest (Fernet, per existing implementation)
2. SSL/TLS enforcement for all MySQL connections
3. Connection pool monitoring and alerting
4. Automated credential rotation (90-day cycle)
5. Audit logging for all schema changes