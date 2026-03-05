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

The prototype must pass all 24 test cases to be considered complete.

---

## Sensitive Actions List

The following actions require reauth_verified = true before the
interceptor permits them. This list is defined at the system level.
The agent cannot modify it.

  access_sensitive

Note: query_pii_table is NOT in the sensitive actions list. First PII
access is allowed without reauth. The PII taint model governs subsequent
behavior -- repeat PII access requires reauth only after the taint is
armed. See PII Taint Rules in api_spec.md for the full rule set.

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
            this scenario. The taint flag is written atomically.
            Repeat access will be gated by Check 4.

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


---

**TC-07 - Re-authentication is allowed and state is recorded**

  Setup:    Alice session. pii_accessed = true. TC-06 just blocked.
  Action:   Alice sends reauth_check valid_credentials with credential = "token-alice"
  Expected: ALLOWED
  Reason:   All checks pass. Trigger fires. reauth_verified = true written to state store.
  Invariant: FR-5 -- constraint written by reauth_check governs future database access.

---

**TC-08 - PII access after reauth is allowed**

  Setup:    Alice session. pii_accessed = true. reauth_verified = true after TC-07.
  Action:   Alice sends database query_pii_table request
  Expected: ALLOWED
  Reason:   Check 4 runs. pii_accessed = true AND action = query_pii_table triggers
            requires_reauth = true. reauth_verified = true. Check 4 passes.

---

**TC-09 - Slack blocked after PII access (exfiltration path)**

  Setup:    Alice session. pii_accessed = true.
  Action:   Alice sends slack_api post_message request
  Expected: BLOCKED - pii_accessed constraint: slack_api blocked as potential
            exfiltration path after PII access this session
  Reason:   Check 5 runs. pii_accessed = true AND action = post_message is in
            PII_TAINT_BLOCKED. Blocked entirely. No reauth bypass.
  Invariant: FR-5 cross-tool enforcement. Slack never contacted.
  Note:     Compare with TC-20. Same request. Same session. Blocked here because
            PII was accessed in between. Slack has no idea PII was accessed.

## Scenario 3 - Concurrent Budget Race

**Principal:** Bob (bob@company.com)
**Session:** Bound to bob@company.com at creation
**Budget:** $500 limit, starting from $0 spent

This scenario proves that two concurrent agents cannot jointly
violate a shared constraint. Linearizable enforcement holds under
concurrency. The invariant is preserved regardless of timing.

---

**TC-10 - Budget spend within limit is allowed**

  Action:   Bob sends budget_spend process_payment with amount $200
  Expected: ALLOWED
            State transition: budget_spent = 200.0 written
  Reason:   $0 + $200 = $200. Within $500 limit.

  Architectural invariant proved:
  Budget enforcement allows valid spend requests and records the
  cumulative spend atomically.

---

**TC-11 - Cumulative budget exceeded is blocked**

  Action:   Bob sends budget_spend process_payment with amount $350
            after TC-10 (total would be $550)
  Expected: BLOCKED - budget exceeded: limit $500.00, spent $200.00,
            requested $350.00
  Reason:   $200 + $350 = $550. Exceeds $500 limit.

  Architectural invariant proved:
  Cumulative spend across multiple steps is tracked and enforced.
  A single request that would push total spend over the limit is
  blocked. FR-8 partial -- single agent case.

---

**TC-12 - Concurrent budget race allows only one request**

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

**TC-13 - Immediate request on fresh session is allowed**

  Action:   Carol sends database query_records immediately after
            session creation
  Expected: ALLOWED
  Reason:   Session exists, active, not yet expired.

  Architectural invariant proved:
  Session expiry does not affect valid requests within the session
  lifetime. The system does not over-block.

---

**TC-14 - Request on expired session is blocked**

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

**TC-15 - Sensitive access without re-authentication is blocked**

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

**TC-16 - Re-authentication step is allowed and state is recorded**

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

**TC-17 - Sensitive access after re-authentication is allowed**

  Action:   Dave sends sensitive_data access_sensitive request
            after TC-16
  Expected: ALLOWED
  Reason:   Check 4 runs. access_sensitive is in sensitive actions
            list. reauth_verified = true. Check 4 passes.
            No other constraints violated.

  Architectural invariant proved:
  Re-authentication unlocks sensitive actions for the remainder
  of the session. The constraint persists across subsequent
  requests without requiring re-authentication on every call.

---

## TC-18 - Audit Log Completeness

  Action:   After all scenarios have run, retrieve the execution
            log for each session using get_session_log()
  Expected: Every decision from TC-01 through TC-24 appears in
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
| 04 | 2 | PII first access | ALLOWED + taint armed | FR-4 dynamic mutation |
| 05 | 2 | Unrelated after PII | ALLOWED | No over-blocking |
| 06 | 2 | PII repeat (taint active) | BLOCKED | FR-5 reauth gate on PII |
| 07 | 2 | Re-authenticate | ALLOWED + state written | FR-4 + FR-10 |
| 08 | 2 | PII after reauth | ALLOWED | Reauth satisfies gate |
| 09 | 2 | Slack after PII | BLOCKED | FR-5 cross-tool exfiltration block |
| 10 | 3 | Spend within limit | ALLOWED + state written | Budget tracking |
| 11 | 3 | Cumulative exceeded | BLOCKED | FR-8 cumulative enforcement |
| 12 | 3 | Concurrent race | One ALLOWED one BLOCKED | NFR-3 linearizability |
| 13 | 4 | Fresh session request | ALLOWED | No over-blocking |
| 14 | 4 | Expired session | BLOCKED | FR-9 session expiry |
| 15 | 5 | Sensitive without reauth | BLOCKED | FR-10 reauth gate |
| 16 | 5 | Re-authentication step | ALLOWED + state written | FR-10 |
| 17 | 5 | Sensitive after reauth | ALLOWED | FR-10 unlocked |
| 18 | All | Audit log completeness | All decisions recorded | FR-7 audit trail |
| 19 | 1 | Fabricated session_id | BLOCKED | Check 1 session existence |
| 20 | 2 | Slack before PII | ALLOWED | No over-blocking |
| 21 | 3 | Negative spend | BLOCKED | Attack vector closed |
| 22 | 4 | Manually revoked session | BLOCKED | FR-9 revocation |
| 23 | 5 | Agent lies in metadata | BLOCKED | NFR-5 agent untrusted |
| 24 | 5 | Replay of valid_credentials | ALLOWED | Replay tolerance noted |

---

## How to Run

All test cases execute in a single run of agent_simulator.py.

  python3 prototype/agent_simulator.py

Expected output: all 24 test cases produce the stated result.
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

TC-12 fails with both ALLOWED: The concurrent race condition is
not being caught. Linearizable read-modify-write is not working.
NFR-3 is not met. This is the most serious failure mode.

TC-14 fails: Session expiry is not being enforced. An expired
session is being treated as active. FR-9 is not met.

TC-15 fails: The re-authentication gate is not working. Sensitive
actions are accessible without re-authentication. FR-10 is not met.

TC-18 fails: The audit log is incomplete. Enforcement decisions
are not being recorded. FR-7 is not met.


---

## Edge Cases -- TC-19 through TC-24

Added after prototype run to cover gaps identified during review.

---

### TC-19 -- Fabricated Session ID

  Scenario: Scenario 1 extension
  Action:   Request with completely fake session_id not in state store
  Expected: BLOCKED - no active session found
  Proves:   Check 1 fires before identity or constraint checks. FR-1.

---

### TC-20 -- Slack Allowed Before PII Taint

  Scenario: Scenario 2 extension
  Action:   Alice posts to Slack BEFORE accessing PII
  Expected: ALLOWED - pii_accessed is false, taint not armed
  Proves:   Taint model does not over-block. FR-5 precision.

---

### TC-21 -- Negative Spend Amount

  Scenario: Scenario 3 extension
  Action:   Bob attempts process_payment with amount = -100.0
  Expected: BLOCKED - invalid spend amount: must be greater than zero
  Proves:   Budget cannot be artificially reset via negative spend.

---

### TC-22 -- Manual Session Revocation

  Scenario: Scenario 4 extension
  Action:   Carol uses a session with active = 0 set by admin
  Expected: BLOCKED - session has been revoked
  Proves:   Revocation is immediate. Does not wait for expiry. FR-9.

---

### TC-23 -- Agent Lies About Re-authentication

  Scenario: Scenario 5 extension
  Action:   Dave sends access_sensitive with {reauth_verified: true}
            in metadata but reauth_verified is false in state store
  Expected: BLOCKED - re-authentication required for sensitive action
  Proves:   Interceptor ignores agent metadata. Reads state store
            directly. Agent assertions are irrelevant. NFR-5.

---

### TC-24 -- Replay of Valid Credentials

  Scenario: Scenario 5 extension
  Action:   Dave replays valid_credentials after reauth already set
  Expected: ALLOWED - idempotent, does not expand permissions
  Note:     Replay window is bounded by session expiry. Production
            fix is signed tokens with single-use nonces.
  Proves:   Replay of an allowed action does not expand session
            permissions beyond what current state permits.

---

## Updated Failure Mode Reference

| TC | Failure meaning |
|---|---|
| TC-02 | Identity enforcement broken |
| TC-06 | Repeat PII reauth gate broken |
| TC-09 | Exfiltration path not blocked after taint |
| TC-12 | Race condition not caught -- linearizability broken (most critical) |
| TC-14 | Session expiry not enforced |
| TC-18 | Silent paths exist in audit trail |
| TC-19 | Check 1 not firing on fabricated session |
| TC-21 | Negative spend attack vector open |
| TC-22 | Manual revocation not enforced |
| TC-23 | Agent metadata can bypass enforcement |
