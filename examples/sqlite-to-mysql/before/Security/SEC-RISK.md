---
artifact_id: SEC-RISK
role: Security Analyst
version: 1
origin: propagation
derives_from:
  - artifact: BA-REQ
    version: 2
    requirements: [REQ-001, REQ-002]
  - artifact: DB-MODEL
    version: 1
    requirements: [REQ-001, REQ-002]
---
# Security Risk Register

## Risks

### RSK-001: SQLite File Permissions
- **Likelihood**: Medium
- **Impact**: High
- **Mitigation**: Set restrictive file permissions (600) on database file

### RSK-002: No Encryption at Rest
- **Likelihood**: Low
- **Impact**: Medium
- **Mitigation**: Document SQLite limitation; recommend full-disk encryption

### RSK-003: Single Database Engine
- **Likelihood**: N/A (by design per REQ-002)
- **Impact**: N/A
- **Note**: No multi-engine attack surface

## Compliance

- GDPR: Data stored locally, user controls file
- SOC2: Not applicable for single-user SQLite