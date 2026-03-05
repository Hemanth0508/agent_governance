# Director Brief
## Agent Governance — Interceptor + State Store Pattern

---

## The Problem in One Sentence

LLM agents cannot verify identity across reasoning steps, which means
any multi-step agentic workflow is vulnerable to identity substitution
by design — not by bug.

---

## Why This Is Not a Known Problem Yet

Current agentic systems treat the agent as a trusted orchestrator.
The agent authenticates once, receives credentials or session tokens,
and then calls tools on behalf of whoever it believes is asking.

The vulnerability is at the reasoning boundary, not the authentication
boundary. A second principal — human, script, or another agent — can
inject a claim mid-workflow in natural language. The agent processes
it as instruction. It has no mechanism to verify. It forwards the
claim to the tool with full session authority.

The tool sees a valid session. It complies.
No authentication failure occurs. No log entry looks wrong.
The agent became an identity proxy without knowing it.

---

## What This Prototype Proves

Enforcement moved out of the agent and into infrastructure.

Every agent action passes through a stateless interceptor before
reaching any tool. The interceptor reads a state store directly.
It does not trust anything the agent says about identity or
constraint state. It checks five things independently on every call:

  1. Does this session exist
  2. Is this session active and unexpired
  3. Does the claimed principal match the session record
  4. Does this action require re-authentication — and has it happened
  5. Do dynamic constraints (PII taint, budget) allow this action

If any check fails, the tool is never contacted. The decision is
logged. The agent cannot retry its way through a failed check.

---

## Five Scenarios — What Each One Proves

**Scenario 1 — Identity Impersonation (Eve and Sasha)**
Eve authenticates. Leaves desk. Sasha tells the agent she is Eve.
Agent forwards the claim. Interceptor reads session record.
Catches the mismatch. Tool never contacted.
Proves FR-3: identity continuity at every execution boundary.

**Scenario 2 — Data Taint Propagation (Alice)**
Alice accesses PII. This arms a session-level taint flag.
From that point Slack is blocked as an exfiltration path —
even though Slack was allowed before PII access.
Slack has no idea PII was accessed. Only the state store knows.
Proves FR-4, FR-5: cross-tool constraint enforcement.

**Scenario 3 — Concurrent Budget Race (Bob)**
Two agents simultaneously attempt spends that together exceed the
budget. Only one passes. SQLite serializable writes plus a threading
lock enforce linearizable read-modify-write.
The invariant holds under real concurrency.
Proves FR-8, NFR-3: concurrent safety.

**Scenario 4 — Session Expiry (Carol)**
Session expires after 2 seconds. Any request after expiry is blocked
regardless of identity correctness.
Proves FR-9: session lifetime enforcement.

**Scenario 5 — Re-authentication Gate + Agent Lies (Dave)**
Sensitive action requires re-authentication. Agent passes
reauth_verified: true in metadata. Interceptor ignores metadata.
Reads state store directly. Still blocked.
Proves FR-10, NFR-5: enforcement independent of agent assertions.

---

## Q&A

**Q: Where is replay protection?**

The prototype proves state enforcement, not cryptographic request
freshness. Session expiry and budget accumulation provide indirect
replay mitigation — replaying a request against an expired session
fails, and replaying a spend still accumulates against the total.

In production every interceptor call carries a short-lived signed
capability token with a single-use nonce. Replaying a captured token
fails because the nonce is consumed and the TTL is seconds.

State enforcement and cryptographic freshness are independent layers.
This prototype proves the state enforcement layer.

**Q: What prevents the agent from calling tools directly?**

In production tools are isolated behind a service account accessible
only to the interceptor. The agent has no credentials for any tool.
It can only propose actions to the interceptor. VPC, mTLS, and service
mesh policy enforce this at the network layer.

In the prototype this is enforced architecturally. There is no direct
code path from agent to tool.

**Q: What if the agent machine is compromised?**

The interceptor authenticates every caller in production. Requests
carry signed session tokens. An attacker with network access to the
interceptor endpoint cannot forge a valid signed token without
compromising the signing infrastructure — a different threat model.

Prototype and production trust models are documented separately in
docs/threat_model.md.

**Q: What about multi-node scale?**

SQLite serializes writes on a single node, which is sufficient to
prove the enforcement invariant. The invariant requires serializable
isolation and linearizable reads.

At scale that means Cloud Spanner or CockroachDB. Eventual consistency
breaks FR-8 deterministically — two concurrent reads against an
eventually consistent store both see the old value, both validate,
both write. The invariant is silently violated.

The consistency model document explains this in full. Spanner TrueTime
provides external consistency that goes beyond standard linearizability.
Every other managed database in this category either breaks FR-8 under
concurrency or sacrifices the consistency guarantees that make
enforcement deterministic.

---

## Production Path

**State store:** SQLite → Cloud Spanner
Schema unchanged. Only the database engine changes.
Spanner is the only managed offering where the enforcement invariant
holds at distributed scale without application-level workarounds.

**Token layer:** Plain UUID sessions → Signed capability tokens
Short TTL. Single-use nonces. Interceptor authenticates every caller.

**Network layer:** In-process calls → VPC + mTLS + service mesh
Agent has zero tool credentials. Tools accept only interceptor-signed
requests.

---

## What This System Does Not Solve

Physical coercion of the authenticated principal.
Infrastructure OS-level compromise.
Cryptographic token forgery without signed tokens in place.
Insider threats with database administrator access.

These are outside the scope of any application-layer system.
Knowing precisely what a system does not solve is as important
as knowing what it does. The threat model document is explicit
about these boundaries.

---

## Documentation

  docs/requirements.md         FR-1 to FR-10, NFR-1 to NFR-5, scope
  docs/architecture.md         Component design, data flow, decisions
  docs/data_model.md           Three-table schema, append-only design
  docs/api_spec.md             Interceptor API, PII taint rules
  docs/consistency_model.md    SQLite vs Spanner, why eventual consistency fails
  docs/threat_model.md         Attack surface, trust boundaries, out of scope
  docs/test_plan.md            All 24 test cases, invariant mapping
  docs/risks.md                Technical, operational, strategic risks
  docs/MANUAL.md               Step-by-step walkthrough of all five scenarios
