# Requirements
### Agent Governance — Interceptor + State Store Pattern
**Author:** Hemanth Porapu
**Date:** March 2026
**Status:** Locked

---

## Phase 1 — Problem Definition

### System Name
Agent Governance — Interceptor + State Store Pattern

### Core Problem Statement

Multi-step agentic workflows execute heterogeneous tool calls without a
durable mechanism to enforce session-scoped governance invariants across
execution boundaries.

Current systems place governance constraints inside the LLM's context
window — as system prompts, instructions, or conversational context.
This is architecturally insufficient. Language models are probabilistic.
They hallucinate. They lose context across long sessions. They can be
manipulated through prompt reframing. They cannot verify identity.

This means governance enforcement is also probabilistic — which is the
same as saying it is not enforced at all.

This is not an alignment problem. It is a distributed systems problem.
The failure mode is identical to a distributed transaction without a
write-ahead log: state transitions happen, but the invariants that
should govern them are not durably enforced. The result is silent
governance decay.

### The Navik Analogy

If a client walks into a company and says "build me the Navik app" —
the company cannot build it. They don't know what it does, what it
shouldn't do, who gets access, who doesn't, what data it touches, what
the boundaries are. Without those constraints written down and enforced,
no engineer can build correctly regardless of their skill.

Agents face the same problem. They are given constraints verbally — in
a system prompt — and expected to remember and enforce them across
every step of a complex workflow. That is not engineering. That is hope.

This system is the written contract. Durable. Outside the agent.
Enforced at every execution boundary.

### The Core Invariant

> No external side effect may execute unless it satisfies the current
> session's immutable and dynamically evolving constraint set.

This is the single statement everything in this system exists to enforce.

### What This System Is NOT

This must be stated explicitly to avoid misalignment.

This system is not a replacement for IAM or identity providers. IAM
handles authentication and static permission scoping. This system
handles dynamic, session-scoped governance invariants that evolve
during execution.

This system is not a prompt alignment solution. It does not make the
LLM safer or better at following instructions. It removes the LLM from
the enforcement path entirely.

This system is not a model fine-tuning project. No model weights are
changed. No training is involved.

This system is not a policy language. It does not define a new way to
write rules. It enforces rules that already exist — durably and
deterministically.

This system is not a jailbreak solution. It does not prevent the LLM
from generating unsafe text. It prevents unsafe external actions from
being executed regardless of what the LLM generates.

---

## Phase 2 — Stakeholders and Scope

### Primary Readers

This documentation and the prototype it describes are written for two
audiences simultaneously.

**Sailesh Krishnamurthy — VP Engineering, Google Cloud Databases**
Strategic reader. Concerned with where this fits in the stack, what
problem it solves at enterprise scale, why Google Cloud Databases is
the natural home for this work, and what the product opportunity is.
Does not need to read code. Should understand the full picture from
the README and this document alone.

**Director — Google Cloud Databases Engineering**
Deep technical reader. Will stress test every design decision. Concerned
with consistency models, threat surface, concurrency semantics, and why
existing solutions like IAM, OPA, JWT, or Temporal do not solve this.
Will read the data model, API spec, and consistency model in detail.

Every document in this repository must satisfy both readers.

### In Scope — MVP

The following are in scope for this prototype:

- Session creation with principal binding
- Durable session-level constraint storage
- Identity continuity enforcement across all requests
- Dynamic constraint mutation based on agent actions
- Cross-tool invariant enforcement
- Session expiry enforcement
- Re-authentication enforcement for sensitive actions
- Deterministic violation blocking
- Append-only audit logging
- Concurrent request safety demonstration

### Out of Scope

The following are explicitly out of scope for this prototype. They are
acknowledged as real engineering problems but are not what this
prototype is proving.

- Cryptographic token signing and verification
- Distributed locking across multiple nodes
- Real LLM integration (agent is simulated)
- Production database — Postgres, Spanner, or CockroachDB
- Horizontal scaling or failover
- REST API or service mesh integration
- Policy language or rule configuration interface
- UI dashboards or monitoring interfaces
- Multi-region replication strategy
- Full IAM replacement

### Success Criteria

The prototype is successful if it demonstrates, in a single runnable
script, all of the following:

1. A legitimate request from an authenticated principal is allowed.
2. An agent-mediated impersonation attempt is blocked deterministically.
3. A cross-step constraint violation is blocked based on prior session
   state — specifically, a tool call blocked because of what happened
   in an earlier step.
4. A cumulative budget constraint is enforced across multiple steps.
5. An expired session is rejected regardless of identity match.
6. A sensitive action is blocked when re-authentication has not occurred.
7. All decisions appear in an append-only audit log with reasons.

---

## Phase 3 — Requirements

### Functional Requirements

Functional requirements define what the system must do.

---

**FR-1 — Session creation with immutable principal binding**

When a user authenticates, the system must create a session record
that binds a unique session identifier to the authenticated principal.
The principal field must be immutable after session creation. No
subsequent operation — by the agent, by a request, or by any other
component — may change the principal bound to a session.

This is the foundation of identity continuity. Eve's session belongs
to Eve. That cannot change.

*Demonstrated by: Scenario 1 — Eve authenticates, session bound to
eve@company.com, principal never changes.*

---

**FR-2 — Durable session-level constraint storage**

The system must store session constraints in a durable state store
outside the LLM context window. Constraints must survive context
evictions, model restarts, and session length. If the model loses
context entirely, the constraints must still exist and still be
enforced.

Constraints are stored as key-value pairs scoped to a session. They
are append-only — new values are inserted as new rows, never by
updating existing rows. The current value for any key is the most
recently inserted row for that key.

*Demonstrated by: All scenarios — constraints are read from the state
store, not from the agent's memory.*

---

**FR-3 — Identity continuity enforcement on each request**

The interceptor must verify identity on every single request — not
once at session creation, but at every execution boundary. The claimed
principal in each request must match the authenticated principal bound
to the session at creation time.

This specifically addresses agent-mediated impersonation: a scenario
where a malicious user tells the agent they are someone else, and the
agent — having no identity verification mechanism — forwards that
claim. The interceptor catches the mismatch before any tool is reached.

Note: This requirement addresses agent-mediated impersonation across
sessions. Physical session hijacking — where someone uses an
authenticated terminal left unattended — is addressed by FR-9
(session expiry) and FR-10 (re-authentication for sensitive actions).

*Demonstrated by: Scenario 1 — Sasha claims to be Eve, interceptor
detects mismatch, database is never reached.*

---

**FR-4 — Dynamic constraint mutation during session**

The system must support constraints that are written during a session
based on what the agent does — not only constraints set at session
start. When the agent performs an action that triggers a governance
rule, the interceptor must write the resulting state transition to the
constraint store.

Example: When PII data is accessed, the constraint `pii_accessed = true`
is written. This constraint did not exist at session start. It was
created by an action. Future requests in the same session are now
evaluated against this new constraint.

This is what distinguishes the state store from a signed JWT. A JWT
is issued at session start and encodes static claims. It cannot encode
state transitions that happen mid-session.

*Demonstrated by: Scenario 2 — PII access in Step 1 creates a new
constraint that governs Step 3.*

---

**FR-5 — Cross-tool invariant enforcement**

A constraint triggered by one tool must govern what is allowed on a
completely different tool later in the same session. The interceptor
sits in front of all tools and reads the same state store regardless
of which tool is being called.

Example: PII accessed via the database tool in Step 1. Slack API
called in Step 3. The Slack connector has no knowledge of what happened
in Step 1. The interceptor does — because it reads the state store
which recorded the Step 1 transition. The Slack request is blocked.

This is cross-step causality. The block in Step 3 was caused by Step 1.
No per-request auth check can enforce this. No connector-level
enforcement can enforce this. Only a session-scoped state store with
cross-step memory can enforce this.

*Demonstrated by: Scenario 2 — database tool in Step 1 causes Slack
API to be blocked in Step 3.*

---

**FR-6 — Deterministic violation blocking**

When a constraint is violated, the request must be blocked. Not warned.
Not logged and allowed through. Blocked completely. The tool must never
be reached. This must happen every time, without exception.

Deterministic means the same input always produces the same output.
If a constraint is violated, the result is always a block. There is
no probability, no reasoning, no judgment call. Infrastructure enforces
it.

*Demonstrated by: All blocked scenarios — tool is never reached on
any violation.*

---

**FR-7 — Append-only audit logging of all decisions**

Every interceptor decision — both allowed and blocked — must be written
to an append-only execution log. Log entries are never deleted or
modified. Each entry must record: session ID, tool, action, result
(ALLOWED or BLOCKED), reason, and timestamp.

This provides a complete, tamper-evident record of everything that
happened in a session. It is the audit trail for compliance,
investigation, and debugging.

*Demonstrated by: All scenarios — full log printed at end of demo
run.*

---

**FR-8 — Concurrent request safety**

When two agent actions execute concurrently against a shared mutable
constraint — such as a budget — the system must guarantee that the
combined result does not violate the constraint. Only one of the two
actions may succeed if both together would exceed the limit.

This requires transactional semantics on the constraint store. The
read, validate, and write sequence must be atomic. This is covered in
detail in the Consistency Model document.

*Demonstrated by: Scenario 3 — two concurrent spend attempts against
a $500 budget, only one succeeds.*

---

**FR-9 — Session expiry enforcement**

Sessions must have a defined lifetime. The interceptor must reject any
request on an expired session, regardless of whether the identity
matches. This mitigates the physical session hijacking scenario where
an authenticated terminal is left unattended.

If Eve leaves her session open and walks away, the session will expire
after the defined timeout. When Sasha sits down and attempts to use
the session, the interceptor rejects it — not because of identity
mismatch, but because the session itself is no longer valid.

*Demonstrated by: Scenario 4 — session created with short expiry,
request after expiry is blocked.*

---

**FR-10 — Re-authentication enforcement for sensitive actions**

Certain actions — specifically those involving PII or other sensitive
data — must require explicit re-authentication within the session
before proceeding. An active, valid, non-expired session with a
matching identity is not sufficient for sensitive tool access.

The session must carry a `reauth_verified` constraint that is only
written after the user has explicitly re-authenticated. If this
constraint is absent, sensitive tool requests are blocked.

This provides a second layer of protection beyond session expiry:
even if Sasha were somehow using an active Eve session, she could not
access sensitive data without re-authenticating as Eve.

*Demonstrated by: Scenario 5 — PII access attempted without
re-authentication flag, blocked at interceptor.*

---

### Non-Functional Requirements

Non-functional requirements define how the system must behave — its
qualities and constraints, not its features.

---

**NFR-1 — Validation latency under 10ms**

The interceptor validation path — read session, read constraints,
validate, write log — must complete in under 10 milliseconds in the
prototype environment. In production, this would be a P99 latency
target against a strongly consistent distributed database.

Rationale: Governance enforcement must not meaningfully slow down
agent workflows. If enforcement adds unacceptable latency, it will
be bypassed or disabled in production systems.

---

**NFR-2 — State updates must be atomic**

Every constraint write must be atomic. A constraint either fully exists
in the store or does not exist at all. There must be no intermediate
state where a constraint is partially written.

Rationale: A partially written constraint could cause the interceptor
to make an incorrect decision — either blocking a valid request or
allowing a violation — based on incomplete state.

---

**NFR-3 — Read-modify-write must be linearizable**

The sequence of read constraint state, validate against it, and write
updated state must execute as a single linearizable operation. No other
agent or process may observe or modify the constraint state between
the read and the write.

Rationale: This is what prevents the concurrent budget race condition.
If two agents both read $300 remaining, both validate $200 spend as
valid, and both write $100 remaining — the budget has been violated.
Linearizable read-modify-write prevents this by making the entire
sequence appear instantaneous to all observers.

In production this requires a database with serializable transaction
isolation — Cloud Spanner, CockroachDB, or equivalent.

---

**NFR-4 — System must tolerate agent retries without violation**

If a blocked request is retried by the agent, it must be blocked again.
The system must not accidentally allow a request on a second or
subsequent attempt that was correctly blocked on the first.

Rationale: Agents often retry on failure. A governance system that
can be bypassed by retrying is not a governance system.

---

**NFR-5 — Enforcement must be independent of agent logic**

The agent must never be able to influence enforcement decisions. It
may read constraints from the state store for planning efficiency.
It may not write constraints, modify constraints, or communicate
with the interceptor about what the decision should be.

The interceptor reads the state store directly and makes its own
decision. The agent's reasoning, context, instructions, or assertions
are irrelevant to the enforcement outcome.

Rationale: Any trust placed in the agent creates a surface for
manipulation. A compromised or manipulated agent must not be able
to bypass governance by asserting that constraints have been satisfied.

---

## Summary Table

| ID | Type | Description | Demonstrated By |
|---|---|---|---|
| FR-1 | Functional | Immutable principal binding at session creation | All scenarios |
| FR-2 | Functional | Durable constraint storage outside LLM | All scenarios |
| FR-3 | Functional | Identity verification on every request | Scenario 1 |
| FR-4 | Functional | Dynamic constraint mutation during session | Scenario 2 |
| FR-5 | Functional | Cross-tool invariant enforcement | Scenario 2 |
| FR-6 | Functional | Deterministic violation blocking | All blocked cases |
| FR-7 | Functional | Append-only audit logging | All scenarios |
| FR-8 | Functional | Concurrent request safety | Scenario 3 |
| FR-9 | Functional | Session expiry enforcement | Scenario 4 |
| FR-10 | Functional | Re-authentication for sensitive actions | Scenario 5 |
| NFR-1 | Non-functional | Validation latency < 10ms | — |
| NFR-2 | Non-functional | Atomic state updates | — |
| NFR-3 | Non-functional | Linearizable read-modify-write | — |
| NFR-4 | Non-functional | Retry tolerance | — |
| NFR-5 | Non-functional | Enforcement independent of agent | — |
