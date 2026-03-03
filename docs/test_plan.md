# Test Plan
### Agent Governance - Interceptor + State Store Pattern
**Author:** Hemanth Porapu
**Date:** March 2026
**Status:** Locked

---

## Overview

This document defines every test case for the Agent Governance
prototype. Each test case states the expected output and the
architectural invariant it proves. A test that passes but cannot
be linked to an architectural claim proves nothing useful.

The prototype must pass all 15 test cases to be considered complete.

---

## Sensitive Actions List

Before the test cases, one definition must be established. The
following actions require reauth_verified = true before the
interceptor permits them. This list is defined at the system level.
The agent cannot modify it.

  query_pii_table
  access_sensitive

Any other action is not subject to the re-authentication gate.
Check 4 is skipped for all other actions.

---

## Scenario 1 - Identity Impersonation

**Principal:** Eve (eve@company.com) and Sasha (sasha@company.com)
**Session:** Bound to eve@company.com at creation
**Budget:** Not applicable

This scenario proves that the interceptor enforces identity at
every execution boundary, not just at login. Agent-mediated
impersonation is blocked deterministically regardless of what
the agent was told.

---

**TC-01 - Legitimate request is allowed**

  Action:   Eve sends database query_records request
  Expected: ALLOWED
  Reason:   Session exists, active, not expired.
            claimed_principal matches session.principal_id.
            No constraints violated.

  Architectural invariant proved:
  A correctly authenticated principal with no constraint violations
  is permitted to execute actions. The system does not over-block.
  This is the baseline correctness check.

---

**TC-02 - Identity mismatch is blocked**

  Action:   Sasha claims to be Eve. Sends same database query_records
            request with claimed_principal = eve@company.com on
            Eve's session_id.
  Expected: BLOCKED - identity mismatch: claimed eve@company.com,
            session bound to eve@company.com but request originates
            from sasha@company.com context
  Reason:   Check 3 fails. claimed_principal does not match the
            principal bound at session creation.

  Architectural invariant proved:
  Identity is verified at the execution boundary by infrastructure,
  not assumed from the agent's claims. The agent forwarding an
  identity assertion does not make that assertion true.

---

**TC-03 - Retry of blocked request is blocked again**

  Action:   Sasha retries the same blocked request immediately.
  Expected: BLOCKED - same reason as TC-02
  Reason:   State has not changed. The constraint that caused the
            block still holds. Retrying does not change the outcome.

  Architectural invariant proved:
  The system is idempotent on blocked requests. Retrying a violation
  does not produce a different result. NFR-4 is satisfied.

---

## Scenario 2 - Cross-Step Constraint Decay

**Principal:** Alice (alice@company.com)
**Session:** Bound to alice@company.com at creation
**Budget:** Not applicable

This scenario proves that a constraint written in one step governs
what is allowed in a later step, across completely different tools.
Cross-step causality is enforced by the state store, not by the agent.

---

**TC-04 - PII access is allowed and state is recorded**

  Action:   Alice sends database query_pii_table request
  Expected: ALLOWED
            State transition: pii_accessed = true written to
            constraints table
  Reason:   Session valid. Identity matches. No prior constraints
            violated. Action is in sensitive actions list but
            reauth is not required for database PII queries in
            this scenario -- reauth gate applies to access_sensitive
            tool only.

  Architectural invariant proved:
  Actions that trigger state transitions are allowed and the
  transition is recorded atomically in the same transaction.
  FR-4 is satisfied.

---

**TC-05 - Unrelated action after PII access is allowed**

  Action:   Alice sends database query_records request after TC-04
  Expected: ALLOWED
  Reason:   pii_accessed constraint does not block all tools.
            It only blocks slack_api. A regular database query
            is not subject to this constraint.

  Architectural invariant proved:
  Constraints are scoped to specific tool and action combinations.
  A constraint triggered by one action does not blanket-block
  all subsequent actions. The system does not over-block.

---

**TC-06 - Slack post after PII access is blocked**

  Action:   Alice sends slack_api post_message request after TC-04
  Expected: BLOCKED - pii_accessed constraint: slack_api forbidden
            after PII access this session
  Reason:   Check 5 reads pii_accessed = true from constraints table.
            slack_api post_message violates the pii propagation rule.

  Architectural invariant proved:
  A constraint written by a database tool in step 1 governs what
  is allowed on a messaging tool in step 3. Cross-tool, cross-step
  enforcement works. FR-5 is satisfied.

---

## Scenario 3 - Concurrent Budget Race

**Principal:** Bob (bob@company.com)
**Session:** Bound to bob@company.com at creation
**Budget:** $500 limit, starting from $0 spent

This scenario proves that two concurrent agents cannot jointly
violate a shared constraint. Linearizable enforcement holds under
concurrency. The invariant is preserved regardless of timing.

---

**TC-07 - Budget spend within limit is allowed**

  Action:   Bob sends budget_spend process_payment with amount $200
  Expected: ALLOWED
            State transition: budget_spent = 200.0 written
  Reason:   $0 + $200 = $200. Within $500 limit.

  Architectural invariant proved:
  Budget enforcement allows valid spend requests and records the
  cumulative spend atomically.

---

**TC-08 - Cumulative budget exceeded is blocked**

  Action:   Bob sends budget_spend process_payment with amount $350
            after TC-07 (total would be $550)
  Expected: BLOCKED - budget exceeded: limit $500.00, spent $200.00,
            requested $350.00
  Reason:   $200 + $350 = $550. Exceeds $500 limit.

  Architectural invariant proved:
  Cumulative spend across multiple steps is tracked and enforced.
  A single request that would push total spend over the limit is
  blocked. FR-8 partial -- single agent case.

---

**TC-09 - Concurrent budget race allows only one request**

  Setup:    Fresh session. Budget $500. Spent $0.
  Action:   Two threads simultaneously send budget_spend
            process_payment with amount $300 each.
            Total if both pass: $600. Exceeds $500 limit.
  Expected: Exactly one ALLOWED. Exactly one BLOCKED.
            Final budget_spent = $300. Not $600.
  Reason:   Serializable transaction with FOR UPDATE locking.
            First thread acquires lock, validates $0 + $300 = $300,
            within limit, writes budget_spent = $300, commits.
            Second thread acquires lock, reads budget_spent = $300,
            validates $300 + $300 = $600, exceeds limit, blocked.

  Architectural invariant proved:
  Concurrent requests cannot jointly violate a shared constraint.
  The linearizable read-modify-write guarantee holds under real
  concurrency. NFR-3 is satisfied. FR-8 is fully satisfied.

---

## Scenario 4 - Session Expiry

**Principal:** Carol (carol@company.com)
**Session:** Created with 2 second duration
**Budget:** Not applicable

This scenario proves that an expired session is rejected regardless
of identity match. Session lifetime is enforced at the infrastructure
layer. FR-9 is satisfied.

---

**TC-10 - Immediate request on fresh session is allowed**

  Action:   Carol sends database query_records immediately after
            session creation
  Expected: ALLOWED
  Reason:   Session exists, active, not yet expired.

  Architectural invariant proved:
  Session expiry does not affect valid requests within the session
  lifetime. The system does not over-block.

---

**TC-11 - Request on expired session is blocked**

  Action:   Carol sends database query_records 3 seconds after
            session creation (session expired after 2 seconds)
  Expected: BLOCKED - session expired at [expiry timestamp]
  Reason:   Check 2 fails. Current time is past expires_at.
            Request is rejected before identity check runs.

  Architectural invariant proved:
  Session lifetime is enforced deterministically. An expired session
  cannot be used regardless of whether the identity is valid. FR-9
  is satisfied. Physical session hijacking window is bounded.

---

## Scenario 5 - Re-authentication for Sensitive Actions

**Principal:** Dave (dave@company.com)
**Session:** Bound to dave@company.com at creation
**Budget:** Not applicable

This scenario proves that a valid active session is not sufficient
for sensitive data access. Explicit re-authentication is required.
FR-10 is satisfied.

---

**TC-12 - Sensitive access without re-authentication is blocked**

  Action:   Dave sends sensitive_data access_sensitive request
            without prior re-authentication
  Expected: BLOCKED - re-authentication required for sensitive action
  Reason:   Check 4 runs. access_sensitive is in the sensitive
            actions list. reauth_verified = false (default).
            Request blocked before constraint checks run.

  Architectural invariant proved:
  A valid active session with correct identity is insufficient for
  sensitive actions. An additional explicit verification step is
  required. FR-10 is satisfied.

---

**TC-13 - Re-authentication step is allowed and state is recorded**

  Action:   Dave sends reauth_check valid_credentials with
            credential = "reauth-token-dave"
  Expected: ALLOWED
            State transition: reauth_verified = true written
  Reason:   Session valid. Identity matches. reauth_check is not
            in the sensitive actions list. valid_credentials action
            triggers reauth_verified = true write.

  Architectural invariant proved:
  The re-authentication mechanism writes a durable constraint
  that persists for the session. FR-4 applied to re-auth context.

---

**TC-14 - Sensitive access after re-authentication is allowed**

  Action:   Dave sends sensitive_data access_sensitive request
            after TC-13
  Expected: ALLOWED
  Reason:   Check 4 runs. access_sensitive is in sensitive actions
            list. reauth_verified = true. Check 4 passes.
            No other constraints violated.

  Architectural invariant proved:
  Re-authentication unlocks sensitive actions for the remainder
  of the session. The constraint persists across subsequent
  requests without requiring re-authentication on every call.

---

## TC-15 - Audit Log Completeness

  Action:   After all scenarios have run, retrieve the execution
            log for each session using get_session_log()
  Expected: Every decision from TC-01 through TC-14 appears in
            the log with correct result, reason, tool, action,
            and timestamp. No gaps. No missing entries.
            ALLOWED and BLOCKED decisions both present.
  Reason:   log_execution() is called on every interceptor
            decision without exception.

  Architectural invariant proved:
  The audit trail is complete and tamper-evident. Every decision
  is recorded regardless of outcome. FR-7 is satisfied. Enterprise
  compliance and forensic investigation requirements are met.

---

## Test Case Summary

| TC | Scenario | Action | Expected | Invariant Proved |
|---|---|---|---|---|
| 01 | 1 | Legitimate request | ALLOWED | Baseline correctness |
| 02 | 1 | Identity mismatch | BLOCKED | FR-3 identity continuity |
| 03 | 1 | Retry of blocked | BLOCKED | NFR-4 retry tolerance |
| 04 | 2 | PII access | ALLOWED + state written | FR-4 dynamic mutation |
| 05 | 2 | Unrelated after PII | ALLOWED | No over-blocking |
| 06 | 2 | Slack after PII | BLOCKED | FR-5 cross-tool enforcement |
| 07 | 3 | Spend within limit | ALLOWED + state written | Budget tracking |
| 08 | 3 | Cumulative exceeded | BLOCKED | FR-8 cumulative enforcement |
| 09 | 3 | Concurrent race | One ALLOWED one BLOCKED | NFR-3 linearizability |
| 10 | 4 | Fresh session request | ALLOWED | No over-blocking |
| 11 | 4 | Expired session | BLOCKED | FR-9 session expiry |
| 12 | 5 | Sensitive without reauth | BLOCKED | FR-10 reauth gate |
| 13 | 5 | Re-authentication step | ALLOWED + state written | FR-4 + FR-10 |
| 14 | 5 | Sensitive after reauth | ALLOWED | FR-10 unlocked |
| 15 | All | Audit log completeness | All decisions recorded | FR-7 audit trail |

---

## How to Run

All test cases execute in a single run of agent_simulator.py.

  python3 prototype/agent_simulator.py

Expected output: all 15 test cases produce the stated result.
The audit log for each session is printed at the end of the run.
No external dependencies. No network required. SQLite only.

---

## What a Failure Means

If any test case produces an unexpected result, the following
architectural claims are invalidated:

TC-02 fails: Identity enforcement is not working. The interceptor
is trusting agent assertions about identity. FR-3 is not met.

TC-03 fails: The system is not idempotent on blocked requests.
Retrying a violation produces a different result. NFR-4 is not met.

TC-06 fails: Cross-step constraint enforcement is not working.
The state written in step 1 is not being read in step 3. FR-5
is not met.

TC-09 fails with both ALLOWED: The concurrent race condition is
not being caught. Linearizable read-modify-write is not working.
NFR-3 is not met. This is the most serious failure mode.

TC-11 fails: Session expiry is not being enforced. An expired
session is being treated as active. FR-9 is not met.

TC-12 fails: The re-authentication gate is not working. Sensitive
actions are accessible without re-authentication. FR-10 is not met.

TC-15 fails: The audit log is incomplete. Enforcement decisions
are not being recorded. FR-7 is not met.
