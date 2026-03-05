# API Specification
### Agent Governance — Interceptor + State Store Pattern
**Author:** Hemanth Porapu  
**Date:** March 2026  
**Status:** Locked

---

## Overview

This document defines the exact interface contracts between every component in the system. Every function, every input, every output, every side effect, and every default value is specified here.

There are two interface boundaries:

- State Store API: functions the Session Manager and Interceptor use to read and write durable state
- Interceptor API: the single function the Agent calls to propose an action

---

## State Store API

### init_db()

Initializes the database. Creates all three tables if they do not already exist. Safe to call multiple times.

    Input:   none
    Output:  none
    Side effects: creates sessions, constraints, execution_log tables and indexes

---

### create_session(principal_id, duration_seconds)

Creates a new session and binds it to the authenticated principal.

    Input:
      principal_id      string   required
      duration_seconds  int      optional   default: 3600

    Output:
      session_id        string   unique session identifier

    Side effects:
      INSERT into sessions:
        session_id    = generated UUID
        principal_id  = input principal_id  (immutable after this point)
        created_at    = current timestamp
        expires_at    = created_at + duration_seconds
        active        = 1

Note: Default is 3600 seconds. For the session expiry demonstration scenario, pass 2 seconds so expiry can be observed within the demo run.

---

### get_session(session_id)

Retrieves the session record for a given session_id.

    Input:
      session_id   string   required

    Output:
      session_id    string
      principal_id  string
      created_at    string   ISO 8601
      expires_at    string   ISO 8601
      active        int      1 = active, 0 = revoked

      Returns None if session_id does not exist.

---

### set_constraint(session_id, constraint_key, constraint_value)

Appends a new constraint row to the constraints table. Never updates existing rows. The new row becomes the current value for this key.

    Input:
      session_id        string   required
      constraint_key    string   required
      constraint_value  any      required   stored as JSON encoded string

    Output:
      none

    Side effects:
      INSERT into constraints with current timestamp

---

### get_constraint(session_id, constraint_key)

Returns the current value of a constraint. Current value is the most recently inserted row for the given session and key.

    Input:
      session_id      string   required
      constraint_key  string   required

    Output:
      constraint_value parsed from JSON

      If no rows exist for this key, returns defined default:

        Key               Default
        budget_spent      0.0
        pii_accessed      false
        reauth_verified   false
        budget_limit      None (must be set explicitly at session start)

---

### log_execution(session_id, tool, action, result, reason)

Appends a decision record to the execution log. Called on every decision, both ALLOWED and BLOCKED.

    Input:
      session_id   string   required
      tool         string   required
      action       string   required
      result       string   required   must be "ALLOWED" or "BLOCKED"
      reason       string   required

    Output:
      none

    Side effects:
      INSERT into execution_log with current timestamp

---

### get_session_log(session_id)

Returns the complete ordered execution history for a session. Ordered by timestamp ascending.

    Input:
      session_id   string   required

    Output:
      list of log entries, each containing:
        tool        string
        action      string
        result      string
        reason      string
        timestamp   string   ISO 8601

      Returns empty list if no entries exist.

---

## Interceptor API

### validate(session_id, claimed_principal, tool, action, metadata)

The single enforcement function. Called by the Agent for every action it wants to execute. This is the only path to any tool.

    Input:
      session_id        string   required
      claimed_principal string   required
      tool              string   required
      action            string   required
      metadata          dict     optional   default: empty dict

    Output:
      InterceptorDecision object

    Side effects on ALLOW:
      Writes triggered state transitions to constraints table
      Writes ALLOWED record to execution_log
      Tool is contacted

    Side effects on BLOCK:
      Writes BLOCKED record to execution_log
      Tool is never contacted

---

## InterceptorDecision Object

Returned by every call to validate().

    allowed     bool     true = action permitted, false = blocked
    reason      string   explanation of the decision
    tool        string   which tool was targeted
    action      string   which action was attempted
    timestamp   string   ISO 8601 timestamp of the decision

Examples:

    ALLOWED:
      allowed: true
      reason:  "ALLOWED - identity verified, no constraints violated"

    BLOCKED - identity mismatch:
      allowed: false
      reason:  "BLOCKED - identity mismatch: claimed sasha@company.com, session bound to eve@company.com"

    BLOCKED - cross-step constraint:
      allowed: false
      reason:  "BLOCKED - pii_accessed constraint: slack_api forbidden after PII access this session"

    BLOCKED - budget exceeded:
      allowed: false
      reason:  "BLOCKED - budget exceeded: limit $500.00, spent $350.00, requested $200.00"

    BLOCKED - session expired:
      allowed: false
      reason:  "BLOCKED - session expired at 2026-03-01T14:20:00"

    BLOCKED - re-authentication required:
      allowed: false
      reason:  "BLOCKED - re-authentication required for sensitive action"

---

## Tool Name Definitions

| Tool Name      | Represents                       | Used In Scenario |
|----------------|----------------------------------|------------------|
| database       | Any database query               | Scenario 1, 2    |
| slack_api      | Any external messaging service   | Scenario 2       |
| budget_spend   | Any financial transaction        | Scenario 3       |
| reauth_check   | Re-authentication verification   | Scenario 5       |
| sensitive_data | Any sensitive or PII data access | Scenario 5       |

---

## Action Definitions

| Action            | Tool           | Triggers Constraint Write       |
|-------------------|----------------|---------------------------------|
| query_records     | database       | none                            |
| query_pii_table   | database       | writes pii_accessed = true       | First access allowed. Arms taint. Repeat requires reauth. |
| post_message      | slack_api      | none                             | Blocked after pii_accessed = true (exfiltration path) |
| process_payment   | budget_spend   | writes budget_spent = new total  | Amount must be greater than zero |
| valid_credentials | reauth_check   | writes reauth_verified = true   |
| access_sensitive  | sensitive_data | none (blocked if not reauthed)  |

---


## PII Taint Rules

When query_pii_table executes and is allowed, pii_accessed = true is written
to the constraints table for that session. This arms the session-level PII taint.

Three rules apply on every subsequent request:

Rule 1 -- Exfiltration path blocking:
  If pii_accessed = true and action is post_message or send_email,
  the request is blocked regardless of reauth status.
  These are data-leaving-the-system paths. Blocked entirely after PII access.
  No reauth bypass.

Rule 2 -- Repeat PII access requires reauth:
  If pii_accessed = true and action = query_pii_table,
  the request requires reauth_verified = true.
  First access is allowed to support legitimate analyst workflows.
  Subsequent access within the same session requires confirmation.

Rule 3 -- Session scoping:
  The pii_accessed taint is scoped to session_id. It cannot bleed
  across sessions. Eve accessing PII in her session does not affect
  Sasha's session. Each session is an isolated taint context.

## Metadata Schema

The metadata field in validate() carries action specific parameters the Interceptor needs to evaluate constraints.

| Action            | Required Key | Type   | Description            |
|-------------------|--------------|--------|------------------------|
| process_payment   | amount       | float  | spend amount requested |
| valid_credentials | credential   | string | re-auth token          |
| all others        | none         |        | empty dict is valid    |

---

## Validation Sequence

Executed in this exact order on every call to validate(). First failing check returns a BLOCK decision immediately.

    Check 1 - Session existence
      Does session_id exist in sessions table?
      Fail: BLOCK "no active session found"

    Check 2 - Session validity
      Is sessions.active == 1?
      Is current time before sessions.expires_at?
      Fail: BLOCK "session expired" or "session revoked"

    Check 3 - Identity continuity
      Does claimed_principal == sessions.principal_id?
      Fail: BLOCK "identity mismatch: claimed X, session bound to Y"

    Check 4 - Re-authentication gate
      Two conditions each independently trigger this gate:

      Condition A: action is in SENSITIVE_ACTIONS (access_sensitive)
        If reauth_verified != true: BLOCK

      Condition B: pii_accessed == true AND action == query_pii_table
        First PII access is allowed and arms the taint.
        Repeat PII access requires reauth_verified == true.

      If either condition is met and reauth_verified != true:
      Fail: BLOCK "re-authentication required for sensitive action"

      Note: The agent cannot satisfy this check by passing
      reauth_verified in metadata. The interceptor reads the
      state store directly and ignores agent assertions.

    Check 5 - Dynamic constraint evaluation
      Read all relevant constraints for this session and action.
      Evaluate each constraint rule.
      Fail: BLOCK with specific constraint violation reason

    All checks pass: ALLOW
      Write triggered state transitions.
      Write ALLOWED to execution log.
      Return InterceptorDecision(allowed=True).

---

## Constraint Key Reference

| Key             | Type  | Default | Set By          | Read At Check |
|-----------------|-------|---------|-----------------|---------------|
| budget_limit    | float | None    | Session Manager | Check 5       |
| budget_spent    | float | 0.0     | Interceptor     | Check 5       |
| pii_accessed    | bool  | false   | Interceptor     | Check 5       |
| reauth_verified | bool  | false   | Interceptor     | Check 4       |
