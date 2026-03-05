# System Architecture
### Agent Governance — Interceptor + State Store Pattern
**Author:** Hemanth Porapu
**Date:** March 2026
**Status:** Locked

---

## Overview

The system enforces deterministic, session-scoped governance invariants
across multi-step agentic workflows. It does this by placing a
stateless enforcement layer — the Interceptor — between the agent and
every tool it can reach. The Interceptor reads all constraint state
from a durable State Store independently on every request. The agent
is fully untrusted.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         OUTSIDE SYSTEM                          │
│                                                                 │
│   User  ──────►  Authentication Provider                       │
│                        │                                        │
│                        │  verified principal_id                 │
│                        ▼                                        │
└───────────────────────────────────────────────────────────────-─┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                        OUR SYSTEM                               │
│                                                                 │
│   ┌─────────────────┐                                          │
│   │ Session Manager │── creates session ──────────────────┐   │
│   └─────────────────┘                                      │   │
│                                                             ▼   │
│   ┌─────────────────┐    reads constraints (planning)  ┌──────────────┐
│   │                 │ ──────────────────────────────►  │              │
│   │  Agent (LLM)    │                                  │  State Store │
│   │  (untrusted)    │                                  │  (durable)   │
│   │                 │                                  │              │
│   └────────┬────────┘                                  └──────┬───────┘
│            │                                                   │
│            │  proposes action                                  │
│            ▼                                                   │
│   ┌─────────────────┐    reads constraints (enforcement)       │
│   │                 │ ◄─────────────────────────────────────── │
│   │  Interceptor    │                                          │
│   │  (stateless)    │── writes state transitions + audit ─────►│
│   │                 │                                          │
│   └────────┬────────┘                                  └───────┘
│            │
│            │  ALLOW or BLOCK
│            ▼
│   ┌─────────────────────────────────────────────┐
│   │              Tool Layer                     │
│   │                                             │
│   │  database  │  slack_api  │  aws_provision   │
│   │  email_api │  payment_api│  (any tool)      │
│   └─────────────────────────────────────────────┘
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**The Interceptor is the only path to any tool.**
There is no direct connection from the Agent to the Tool Layer.

---

## The Two Reads — The Most Important Distinction

Both the Agent and the Interceptor read from the State Store.
They read for completely different reasons.

```
Agent reads State Store
  → Purpose: planning efficiency
  → Effect:  advisory only
  → Trust:   none — agent may ignore or misread constraints
  → Result:  agent proposes better actions, wastes fewer round trips

Interceptor reads State Store
  → Purpose: enforcement
  → Effect:  authoritative and final
  → Trust:   complete — state store is the single source of truth
  → Result:  allow or block, no exceptions
```

Even if the agent reads constraints correctly and plans accordingly,
the Interceptor still reads independently before every execution.
The agent's read does not substitute for the Interceptor's read.
Ever.

---

## Components

### 1. Authentication Provider (Outside System)

The authentication provider is outside the scope of this system.
It may be Google, OAuth2, SSO, or any identity provider.

Our system receives exactly one thing from it: a verified
`principal_id` — a string that uniquely identifies the authenticated
human. We do not care how they authenticated. We only care that
the identity has been verified by a trusted external system before
it reaches us.

**Responsibility:** Verify human identity. Produce a trusted
principal_id. Nothing else.

---

### 2. Session Manager

The Session Manager receives the verified `principal_id` from the
authentication provider and creates a session record in the State
Store.

It writes:
- A unique `session_id`
- The `principal_id` — immutable after this point
- `created_at` timestamp
- `expires_at` timestamp — session lifetime enforced here
- `active` flag — set to true on creation
- Any initial constraints for this session — budget limits,
  allowed tools, regional restrictions, etc.

The principal binding happens exactly once — at session creation.
No subsequent operation may change the principal bound to a session.
This is the architectural guarantee that makes FR-3 (identity
continuity) possible.

**Responsibility:** Create sessions. Bind principals. Write initial
constraints. Nothing else.

---

### 3. Agent (Untrusted Proposal Engine)

The Agent is the LLM runtime — it may be a LangChain agent, a
Vertex AI agent, a custom orchestration layer, or a simulated agent
as in this prototype. It does not matter what the agent is.

The Agent receives:
- The user's request
- The `session_id`

The Agent may:
- Read constraints from the State Store for planning efficiency
- Propose actions to the Interceptor

The Agent may NOT:
- Write to the State Store
- Modify session state
- Communicate directly with any tool
- Influence Interceptor decisions
- Assert that constraints have been satisfied

The Agent is treated as fully untrusted at all times. It may
hallucinate. It may be manipulated through prompt injection. It may
be outdated. It may have been given contradictory instructions.
None of this affects enforcement — because the Agent has no role
in enforcement.

Reading constraints is permitted for planning efficiency only.
If the Agent knows the budget is $500 and $350 has been spent, it
can avoid proposing a $200 action it knows will fail. This reduces
wasted round trips. But even if the Agent ignores what it reads,
the Interceptor will still block the violation.

**Responsibility:** Propose actions. Nothing else.

---

### 4. Interceptor (Enforcement Layer)

The Interceptor is the core of this system. It is a stateless
validation service that sits between the Agent and every tool.

Every action request from the Agent passes through the Interceptor
before reaching any tool. There are no exceptions. There is no
alternative path.

The Interceptor is stateless — it holds no local state between
requests. All state is read from the State Store on every call.
This means multiple Interceptor instances can run in parallel
without coordination. It scales horizontally.

**Validation sequence — executed in this exact order:**

```
1. Does this session_id exist in the State Store?
   NO  → BLOCK "no active session"

2. Is the session active and not expired?
   NO  → BLOCK "session expired" or "session revoked"

3. Does claimed_principal match session.principal_id?
   NO  → BLOCK "identity mismatch — impersonation attempt"

4. Does this action require re-authentication?
   YES and reauth_verified != true → BLOCK "re-authentication required"

5. Do current dynamic constraints permit this action?
   NO  → BLOCK with specific constraint violation reason

ALL PASS → ALLOW
```

On ALLOW, the Interceptor also:
- Executes any state transitions triggered by this action
  (e.g., writes `pii_accessed = true` after a PII query)
- Decrements budget counters
- Writes an ALLOWED record to the execution log

On BLOCK, the Interceptor:
- Writes a BLOCKED record to the execution log with reason
- Returns the block decision to the Agent
- Does nothing else — the tool is never contacted

**Responsibility:** Enforce all constraints on every request.
Write state transitions. Write audit log. Nothing else.

---

### 5. State Store (Durable Constraint Ledger)

The State Store is the single source of truth for everything
enforcement depends on. It is a strongly consistent database with
three tables.

**sessions table**
One row per session. Written once at session creation. The
`principal_id` field is never updated after creation.

**constraints table**
Append-only. Each row is a key-value pair scoped to a session with
a timestamp. To update a constraint, a new row is inserted — the
old row is never modified or deleted. The Interceptor always reads
the most recently inserted row for a given key.

This append-only design means:
- The full history of every constraint change is preserved
- Nothing can be deleted to bypass enforcement
- The audit trail is complete and tamper-evident

**execution_log table**
Append-only. Every Interceptor decision is recorded here — both
ALLOWED and BLOCKED. Rows are never deleted.

**Responsibility:** Store all session and constraint state durably.
Provide strongly consistent reads and writes. Serve as the single
source of truth for the Interceptor. Nothing else.

---

### 6. Tool Layer (Simulated in Prototype)

The Tool Layer represents every external system the Agent might
call — databases, APIs, cloud infrastructure, SaaS tools. In
this prototype these are simulated. In production each would be
a real service endpoint.

Tools are only reachable through the Interceptor. A tool that
accepts direct calls from the Agent — bypassing the Interceptor
— breaks the architectural guarantee. In production, tools must
be configured to only accept Interceptor-signed requests.

**Responsibility:** Execute actions. Nothing else.

---

## Execution Flow

```
Step 1 — User authenticates
         Authentication provider verifies identity
         Returns verified principal_id to Session Manager

Step 2 — Session Manager creates session
         Writes session record to State Store
         Binds principal_id permanently
         Writes initial constraints

Step 3 — Agent reads constraints (planning)
         Reads current constraint state from State Store
         Uses this to plan which actions to propose
         This step is optional and advisory

Step 4 — Agent proposes action
         Sends to Interceptor:
         { session_id, claimed_principal, tool, action }

Step 5 — Interceptor validates
         Reads session from State Store
         Reads current constraints from State Store
         Runs validation sequence (6 checks)
         Decision: ALLOW or BLOCK

Step 6a — BLOCK
          Interceptor writes BLOCKED record to execution log
          Returns block decision and reason to Agent
          Tool is never contacted

Step 6b — ALLOW
          Interceptor executes tool call
          Writes any triggered state transitions to State Store
          Writes ALLOWED record to execution log
          Returns result to Agent
```

---

## Design Decisions

Every design decision here will be questioned by a senior engineering
team. Each one is documented with the reason it was made and what
alternative was considered and rejected.

---

### Decision 1 — Interceptor is Stateless

**Decision:** The Interceptor holds no local state. All state is
read from the State Store on every request.

**Reason:** Stateless compute scales horizontally without
coordination. Multiple Interceptor instances can run in parallel
and all make correct decisions because they all read from the same
State Store. If the Interceptor held local state, instances would
diverge and a race condition could allow a violation to pass through
one instance while being blocked by another.

**Alternative considered:** Cache constraint state locally in the
Interceptor with an epoch number. Acceptable as a performance
optimisation in production, but the cache must be invalidated on
every constraint write. Not implemented in the prototype to keep
the enforcement logic clear.

---

### Decision 2 — Constraints are Append-Only

**Decision:** Constraint updates are implemented as new row inserts,
never as updates to existing rows. The current value is the most
recently inserted row for a given key.

**Reason:** Append-only design preserves the full history of every
constraint change. It eliminates an attack surface — an adversary
cannot bypass enforcement by deleting or overwriting a constraint
record. It also makes the audit trail complete and tamper-evident.

**Alternative considered:** Update-in-place with a separate audit
log. Rejected because it creates two sources of truth that can
diverge. Append-only collapses state and audit into one structure.

---

### Decision 3 — Agent is Fully Untrusted

**Decision:** The Agent has no role in enforcement. It may not write
constraints, modify sessions, or influence Interceptor decisions.

**Reason:** Any trust placed in the Agent creates a surface for
manipulation. If the Interceptor could be told by the Agent that
constraints have been checked, a compromised or prompt-injected
Agent could bypass enforcement entirely by asserting satisfaction.
Zero trust toward the Agent is the only architecturally sound
position.

**Alternative considered:** Trusted agent with signed execution
context. Would require cryptographic token infrastructure and still
relies on the agent runtime not being compromised. Deferred to
production roadmap.

---

### Decision 4 — Enforcement Not Pushed to Database Layer

**Decision:** Enforcement lives in the Interceptor + State Store
layer, not inside individual database schemas.

**Reason:** Agents do not only call databases. They call Slack,
payment APIs, cloud infrastructure, SaaS tools. Database-native
controls — row-level security, check constraints — only protect
the database boundary. They cannot enforce that a Slack call is
forbidden after PII was accessed in a database query. The
Interceptor sits above all tools and enforces invariants across
all of them uniformly.

Additionally, session-scoped governance constraints are ephemeral
and dynamic. Encoding them in core database schema would couple
runtime policy to domain data design — a clean architecture
violation.

**Alternative considered:** Push enforcement into each tool's
native access control. Rejected because it creates N enforcement
points instead of one, each with its own semantics, each blind
to what happened in the other N-1 tools.

---

### Decision 5 — Not OPA, Not Policy Engine

**Decision:** This system is not built on OPA, Cedar, or any
existing policy engine.

**Reason:** Policy engines evaluate stateless policy against a
request context. They have no concept of session-scoped mutable
state. A rule like "Slack is forbidden after PII was accessed this
session" requires the enforcement layer to know what happened in
a prior step. That is state, not policy. OPA does not maintain
a session state ledger. Our State Store does.

OPA could be used as part of the rule evaluation logic inside the
Interceptor — evaluating a policy against the constraint state
read from the State Store. That is a valid future direction. But
OPA alone, without the State Store backing it, cannot enforce
cross-step session invariants.

**Alternative considered:** OPA with an external data source
providing session state. Architecturally equivalent to what we
are building, with added complexity of OPA's policy language.
Not necessary for the prototype.

---

## Production Path

This prototype uses SQLite and in-process function calls.
The architectural pattern holds at production scale.
The components swap out. The invariant does not.

| Prototype | Production |
|---|---|
| SQLite | Cloud Spanner / CockroachDB (linearizable) |
| In-process Interceptor | Sidecar proxy / service mesh plugin |
| Simulated Agent | Real LLM agent runtime |
| Hardcoded constraint rules | Configurable rule engine |
| No token signing | Signed capability tokens |
| Single node | Horizontally scaled Interceptor fleet |

---

## What This Architecture Does Not Solve

Physical session hijacking where an authenticated user leaves their
terminal unlocked. This is addressed by session expiry (FR-9) and
re-authentication for sensitive actions (FR-10) — both implemented
in the prototype — but these are mitigations, not complete solutions.
Physical security is outside the scope of any software system.

Cryptographic identity binding. The prototype does not sign tokens.
In production, the session_id and principal binding should be
cryptographically signed so the Interceptor can verify the session
record has not been tampered with.

Agent runtime compromise at the infrastructure level. If the
machine running the Agent is compromised at the OS level, an
attacker could forge requests to the Interceptor. This is a
physical infrastructure problem, not an application architecture
problem.
