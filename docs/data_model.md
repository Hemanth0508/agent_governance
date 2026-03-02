# Data Model
### Agent Governance — Interceptor + State Store Pattern
**Author:** Hemanth Porapu
**Date:** March 2026
**Status:** Locked

---

## Overview

The State Store is the single source of truth for everything the
Interceptor depends on. It has three tables. All enforcement decisions
are made by reading from these tables. All state transitions are written
to these tables. The agent has no write access to any of them.

---

## Schema Diagram

```
┌─────────────────────────────────────────────────────┐
│                      sessions                       │
├─────────────────┬───────────────────────────────────┤
│ session_id      │ TEXT PRIMARY KEY                  │
│ principal_id    │ TEXT NOT NULL                     │  ← immutable
│ created_at      │ TEXT NOT NULL                     │
│ expires_at      │ TEXT NOT NULL                     │  ← FR-9
│ active          │ INTEGER DEFAULT 1                 │
└────────┬────────┴───────────────────────────────────┘
         │ 1
         │
         │ many
┌────────▼────────────────────────────────────────────┐
│                    constraints                      │
├─────────────────┬───────────────────────────────────┤
│ id              │ INTEGER PRIMARY KEY AUTOINCREMENT │
│ session_id      │ TEXT NOT NULL (FK → sessions)     │
│ constraint_key  │ TEXT NOT NULL                     │
│ constraint_value│ TEXT NOT NULL (JSON encoded)      │
│ set_at          │ TEXT NOT NULL                     │
└─────────────────┴───────────────────────────────────┘
         │ 1
         │
         │ many
┌────────▼────────────────────────────────────────────┐
│                   execution_log                     │
├─────────────────┬───────────────────────────────────┤
│ id              │ INTEGER PRIMARY KEY AUTOINCREMENT │
│ session_id      │ TEXT NOT NULL (FK → sessions)     │
│ tool            │ TEXT NOT NULL                     │
│ action          │ TEXT NOT NULL                     │
│ result          │ TEXT NOT NULL (ALLOWED / BLOCKED) │
│ reason          │ TEXT NOT NULL                     │
│ timestamp       │ TEXT NOT NULL                     │
└─────────────────┴───────────────────────────────────┘
```

---

## Table 1 — sessions

One row per session. Written once at session creation by the Session
Manager. The `principal_id` field is never updated after creation.
This immutability is the architectural guarantee behind FR-1 and FR-3.

```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    active       INTEGER DEFAULT 1
);
```

### Field Definitions

**session_id**
A unique identifier for the session. Generated at creation time.
Passed with every agent request so the Interceptor knows which
session to validate against.

**principal_id**
The authenticated identity of the user. Written once at session
creation. Never updated. This is the value the Interceptor checks
against the claimed principal in every request. Immutability here
is what makes identity continuity possible.

**created_at**
ISO 8601 timestamp of when the session was created.

**expires_at**
ISO 8601 timestamp of when the session expires. The Interceptor
compares the current time against this field on every request.
If current time is past expires_at, the session is rejected
regardless of identity match. This enforces FR-9.

**active**
1 = session is active. 0 = session has been explicitly revoked.
A session can be revoked before it expires. Both expiry and
revocation result in a block at the Interceptor.

### What Cannot Happen

The `principal_id` field has no UPDATE path in the application code.
There is no function that modifies it after creation. This is enforced
at the application layer in the prototype and would be enforced by
column-level access control in production.

---

## Table 2 — constraints

Append-only. Every constraint write is a new row insert. Existing
rows are never modified or deleted. The current value of any
constraint key is the most recently inserted row for that session
and key combination.

```sql
CREATE TABLE constraints (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    constraint_key   TEXT NOT NULL,
    constraint_value TEXT NOT NULL,
    set_at           TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX idx_constraints_session_key
    ON constraints(session_id, constraint_key, set_at DESC);
```

### Field Definitions

**id**
Auto-incrementing primary key. Provides an immutable insertion order.

**session_id**
The session this constraint belongs to. Every constraint is scoped
to exactly one session. Constraints from one session never affect
another.

**constraint_key**
The name of the constraint. See constraint key definitions below.

**constraint_value**
JSON encoded value. Using JSON allows any value type to be stored
uniformly — booleans, floats, strings, lists.

**set_at**
ISO 8601 timestamp of when this constraint was written. Used to
determine the current value — the row with the latest set_at for
a given session_id and constraint_key is the current value.

### Why Append-Only

Three reasons.

First, full history. Every constraint change is preserved. You can
reconstruct exactly what the constraint state was at any point in
time during a session.

Second, tamper resistance. A constraint cannot be deleted to bypass
enforcement. There is no DELETE path. An adversary who gains write
access to the database cannot remove a constraint that would block
their action.

Third, audit completeness. The constraints table and the execution
log together form a complete, consistent record of the session.
They never diverge because neither can be modified after writing.

### Reading the Current Value

To get the current value of a constraint:

```sql
SELECT constraint_value
FROM constraints
WHERE session_id = ?
  AND constraint_key = ?
ORDER BY set_at DESC
LIMIT 1;
```

### Mid-Session Constraint Changes

When a constraint changes mid-session a new row is inserted.
The Interceptor always reads using the query above which returns
the most recently inserted value. The change takes effect on the
very next request. No session restart is needed. No notification
to the agent is needed.

Example: Eve has a $500 budget. A manager reduces it to $200
mid-session. A new row is inserted with `budget_limit = 200.0`.
Eve's next request is validated against $200, not $500. The agent
does not need to know this happened. The Interceptor finds it
on its next read.

---

## Table 3 — execution_log

Append-only. Every Interceptor decision for every session is
recorded here. Both ALLOWED and BLOCKED decisions. Rows are
never deleted.

```sql
CREATE TABLE execution_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tool       TEXT NOT NULL,
    action     TEXT NOT NULL,
    result     TEXT NOT NULL,
    reason     TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX idx_log_session
    ON execution_log(session_id, timestamp ASC);
```

### Field Definitions

**id**
Auto-incrementing primary key. Provides immutable insertion order.

**session_id**
The session this log entry belongs to. Allows retrieval of the
complete ordered history of any session.

**tool**
The tool that was targeted. Examples: `database`, `slack_api`,
`aws_provision`, `payment_api`.

**action**
The specific action attempted. Examples: `query_pii_table`,
`post_message`, `provision_instance`, `process_payment`.

**result**
Either `ALLOWED` or `BLOCKED`. No other values are valid.

**reason**
Human readable explanation of the decision. On ALLOW this
describes what happened. On BLOCK this describes which constraint
was violated and why.

Examples:
- `"ALLOWED — identity verified, no constraints violated"`
- `"BLOCKED — identity mismatch: claimed sasha@company.com, session bound to eve@company.com"`
- `"BLOCKED — pii_accessed constraint: slack_api forbidden after PII access this session"`
- `"BLOCKED — budget exceeded: limit $500.00, spent $350.00, requested $200.00"`
- `"BLOCKED — session expired at 2026-03-01T14:30:00"`
- `"BLOCKED — re-authentication required for sensitive action"`

**timestamp**
ISO 8601 timestamp of when the decision was made.

### Reading a Session Log

To retrieve the complete ordered history of a session:

```sql
SELECT tool, action, result, reason, timestamp
FROM execution_log
WHERE session_id = ?
ORDER BY timestamp ASC;
```

This answers the enterprise audit question completely: what did
the agent do, in what order, what was allowed, what was blocked,
and why.

---

## Constraint Keys

These are the specific keys used in the constraints table across
all five demonstration scenarios.

| Key | Value Type | Written By | Read For |
|---|---|---|---|
| `budget_limit` | float | Session Manager at start | Budget ceiling check |
| `budget_spent` | float | Interceptor after each spend | Cumulative budget check |
| `pii_accessed` | bool | Interceptor after PII query | Slack and external API block |
| `reauth_verified` | bool | Interceptor after re-auth step | Sensitive action gate |

### How Budget Enforcement Works Across Multiple Steps

Budget enforcement requires reading two constraint keys together.
The Interceptor reads both on every spend request:

```
current_limit = latest value of budget_limit
current_spent = latest value of budget_spent
requested     = amount in this request

if current_spent + requested > current_limit:
    BLOCK
else:
    ALLOW
    INSERT new row: budget_spent = current_spent + requested
```

The INSERT of the new budget_spent value happens inside the same
transaction as the validation. This is what prevents the concurrent
race condition described in FR-8.

---

## Transaction Boundary

This is the most critical aspect of the data model. The Interceptor's
entire validation and write sequence must execute inside a single
atomic transaction with serializable isolation.

```
BEGIN TRANSACTION (SERIALIZABLE)

  -- Lock the session row
  SELECT * FROM sessions
  WHERE session_id = ?
  FOR UPDATE

  -- Check session exists and is active
  -- Check session has not expired
  -- Check principal matches

  -- Lock and read all relevant constraints
  SELECT constraint_value FROM constraints
  WHERE session_id = ? AND constraint_key = ?
  ORDER BY set_at DESC LIMIT 1
  FOR UPDATE

  -- Validate all constraints

  IF any check fails:
    ROLLBACK
    INSERT INTO execution_log (result = BLOCKED, ...)
    RETURN block decision

  -- Write state transitions triggered by this action
  INSERT INTO constraints (session_id, constraint_key, ...)

  -- Write audit log entry
  INSERT INTO execution_log (result = ALLOWED, ...)

COMMIT
RETURN allow decision
```

### Why This Transaction Boundary Prevents the Budget Race

Two agents. Both read `budget_spent = $300`. Both validate that
their $200 request fits within the $500 limit. Without the
transaction, both write `budget_spent = $500`. Total actual spend
is $600. Invariant violated.

With the transaction and `FOR UPDATE` locking, the second agent's
read of `budget_spent` blocks until the first agent's transaction
commits. The second agent then reads `budget_spent = $500`, sees
that $500 + $200 = $700 exceeds $500, and is blocked. Invariant
preserved.

This is why NFR-3 (linearizable read-modify-write) is a hard
requirement. Eventual consistency is not sufficient here. A
database that returns stale reads will silently allow budget
violations.

---

## Production Considerations

The prototype uses SQLite with WAL mode. SQLite provides
serializable transactions on a single node which is sufficient
for the prototype.

In production the State Store must be a distributed database
with strongly consistent reads and serializable transaction
support across nodes.

| Requirement | SQLite (Prototype) | Production |
|---|---|---|
| Serializable transactions | Yes (single node) | Cloud Spanner / CockroachDB |
| Linearizable reads | Yes (single node) | Requires TrueTime or equivalent |
| Horizontal scale | No | Yes |
| Multi-region | No | Yes |
| Managed service | No | Yes |

The schema does not change between prototype and production.
Only the database engine changes.
