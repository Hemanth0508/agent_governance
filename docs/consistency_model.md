# Consistency Model
### Agent Governance - Interceptor + State Store Pattern
**Author:** Hemanth Porapu
**Date:** March 2026
**Status:** Locked

## Overview

This document explains why the Agent Governance system requires linearizable read-modify-write semantics, why eventual consistency is architecturally insufficient, and how the prototype implements the required consistency guarantees.

This is not a performance optimization decision. It is a correctness decision. A system that enforces governance constraints with eventual consistency is not enforcing governance constraints.

## What is a Consistency Model

When multiple processes read and write the same data concurrently, a consistency model defines what each reader is guaranteed to see.

**Eventual Consistency**
Writes will eventually reach all readers, but there is a window where different readers see different values. Replicas may be temporarily out of sync. Reads may return stale data. Appropriate for use cases where stale reads are tolerable. Not appropriate for enforcement decisions where a stale read silently permits a violation.

**Linearizability**
Every read sees the most recently committed write. Every operation appears to execute instantaneously at some single point in real time. Once a write is committed, every subsequent read anywhere in the system returns that value. Required when correctness depends on reading current state before making a decision that modifies shared state.

## Why Eventual Consistency Breaks This System

Two agents. Shared budget. Concurrent spend requests.

Setup:
  Budget limit:  $500
  Current spend: $300
  Agent 1 requests $200 spend
  Agent 2 requests $200 spend at the same time

With eventual consistency:

  Agent 1 reads budget_spent = $300 (from replica A)
  Agent 2 reads budget_spent = $300 (from replica B, stale)
  Agent 1 validates: $300 + $200 = $500. Within limit. ALLOW.
  Agent 2 validates: $300 + $200 = $500. Within limit. ALLOW.
  Agent 1 writes budget_spent = $500
  Agent 2 writes budget_spent = $500
  Actual total spent: $700. Limit: $500. Violation: $200 over.

No error was thrown. No exception was raised. The system silently permitted a governance violation because two reads returned stale data from out-of-sync replicas. This is not a theoretical edge case. This is the expected behavior of any eventually consistent store under concurrent write load.

With linearizable read-modify-write:

  Agent 1 begins transaction. Acquires lock on budget_spent row.
  Agent 2 begins transaction. Tries to acquire same lock. Waits.
  Agent 1 reads budget_spent = $300
  Agent 1 validates: $300 + $200 = $500. Within limit. ALLOW.
  Agent 1 writes budget_spent = $500. Commits. Releases lock.
  Agent 2 lock acquired. Reads budget_spent = $500.
  Agent 2 validates: $500 + $200 = $700. Exceeds limit. BLOCK.
  Actual total spent: $500. Invariant preserved.

## The Five Consistency Requirements

Each of the following requires a linearizable guarantee. A stale read in any of these cases produces a security or governance failure.

**Requirement 1 - Budget Enforcement**
The read of budget_spent, the validation, and the write of the new value must execute as a single atomic transaction. No other process may read or write budget_spent between the read and the write.

Failure mode: Two concurrent spend requests both read stale budget_spent, both pass validation, both write. Total spend exceeds limit. Invariant violated silently.

**Requirement 2 - Identity Validation**
The read of principal_id from the sessions table must return the value written at session creation. In practice this is lower risk because principal_id is immutable. Stated for completeness and production correctness.

Failure mode: Stale replica returns wrong principal_id. Identity mismatch not detected. Impersonation succeeds.

**Requirement 3 - Session Expiry**
The read of expires_at must return the current value. A stale read that shows a session as active when it has expired is a direct security vulnerability.

Failure mode: Session expires. Stale replica still shows active. Request on expired session passes Check 2. Enforcement bypassed.

**Requirement 4 - PII Constraint Propagation**
Once pii_accessed = true is written, every subsequent read in every interceptor instance must see it. Under eventual consistency, a Slack request that should be blocked will be allowed if the interceptor reads from a stale replica.

Failure mode: Interceptor instance A writes pii_accessed = true. Interceptor instance B reads from stale replica. pii_accessed = false. Slack request allowed. Cross-step constraint violated. This failure mode becomes more likely as interceptor instances increase.

**Requirement 5 - Concurrent Session Creation**
Two session creation requests must not produce sessions with the same session_id. Mitigated by UUID generation. Formally guaranteed by the database uniqueness constraint.

## Transaction Boundary

The Interceptor's entire validation and write sequence executes inside a single transaction with serializable isolation.

  BEGIN TRANSACTION (SERIALIZABLE ISOLATION)

    Lock session row FOR UPDATE
    Run Check 1: session exists
    Run Check 2: session active and not expired
    Run Check 3: identity match

    Lock relevant constraint rows FOR UPDATE
    Run Check 4: re-authentication gate
    Run Check 5: dynamic constraint evaluation

    IF any check fails:
      INSERT into execution_log (BLOCKED)
      ROLLBACK
      RETURN block decision

    INSERT triggered constraint writes
    INSERT execution_log (ALLOWED)

  COMMIT
  RETURN allow decision

The FOR UPDATE locks prevent any other transaction from reading or writing the locked rows until this transaction commits. This is what makes the read-validate-write sequence atomic.

## Prototype Implementation

The prototype uses SQLite with WAL mode and serializable transaction isolation. SQLite on a single node provides full linearizability because there is only one writer at a time.

SQLite serializes writes automatically. Two concurrent transactions that attempt to write will have one succeed and one wait, then retry. This is exactly the linearizable behavior needed to demonstrate the invariant holds under concurrent requests.

The concurrent budget scenario (Scenario 3) demonstrates this directly by spawning two threads that attempt simultaneous spend requests against a $500 budget.

## Production Requirements

In production, the State Store must be a distributed database that provides linearizable reads and serializable transactions across nodes.

| Property | Requirement | SQLite | Cloud Spanner | CockroachDB |
|---|---|---|---|---|
| Serializable transactions | Required | Yes (single node) | Yes | Yes |
| Linearizable reads | Required | Yes (single node) | Yes (TrueTime) | Yes |
| Multi-node consistency | Required in prod | No | Yes | Yes |
| Horizontal scale | Required in prod | No | Yes | Yes |

Cloud Spanner is the natural production target given the Google Cloud context of this work. TrueTime provides external consistency guarantees that reflect real-world ordering across all transactions.

## What Happens if Consistency is Relaxed

This section documents exact failure modes if the consistency requirement is relaxed in production.

Budget violations become probabilistic rather than impossible. Under low concurrency they will be rare. Under high concurrency they will be frequent. The system will appear to work in testing and fail silently in production under load.

PII constraint propagation failures become load-dependent. With one interceptor instance the constraint is always read from the same store. With multiple instances behind a load balancer, failure rate scales with replication lag and instance count.

Session expiry enforcement becomes unreliable under replica lag. The window of vulnerability equals the replication lag of the storage backend.

None of these failure modes produce errors or exceptions. They produce silent governance violations. The audit log will show ALLOWED for requests that should have been BLOCKED. The violation will only be discovered after the fact, if at all.

This is the fundamental reason eventual consistency is architecturally incompatible with deterministic governance enforcement.
