# Risks
## Agent Governance -- Interceptor + State Store Pattern

**Author:** Hemanth Porapu
**Date:** March 2026
**Status:** Locked

---

## Overview

This document identifies risks to the Agent Governance system across
three categories: technical, operational, and strategic.

This is distinct from the threat model. The threat model covers security
attacks and their mitigations. This document covers what could go wrong
in implementation, operation, and adoption regardless of attacker intent.

---

## Technical Risks

---

### Risk 1 -- SQLite Concurrency Under High Load

**Description:**
SQLite serializes all writes through a single writer lock. Under high
concurrent request rates, lock contention causes requests to queue.
Each request waits for the previous transaction to commit before it
can acquire the lock.

**Impact:**
Latency increases with concurrency. At low request rates this is
invisible. At production scale with hundreds of concurrent agents it
becomes a bottleneck.

**What this does not affect:**
Correctness. The serialization that causes latency is the same
serialization that prevents the budget race condition. SQLite does
not allow violations -- it slows down under load but never produces
incorrect results.

**Mitigation:**
The prototype demonstrates enforcement correctness, not throughput.
In production the state store is replaced with Cloud Spanner or
CockroachDB. Both provide serializable transactions across distributed
nodes with horizontal scaling. Latency stays bounded regardless of
concurrent request count.

**Prototype impact:** Low. Demo workloads are low concurrency.
**Production impact:** High if not addressed. Addressed by database choice.

---

### Risk 2 -- Clock Skew Affecting Session Expiry

**Description:**
Session expiry compares the current UTC time from the local machine
clock against the expires_at timestamp stored at session creation.
If the machine clock drifts -- due to virtualization, NTP failure,
or deliberate manipulation -- sessions may appear expired when they
are not, or appear active when they have already expired.

**Impact:**
A clock running behind could allow an expired session to continue
operating past its intended lifetime. A clock running ahead could
prematurely expire valid sessions and disrupt legitimate workflows.

**Mitigation:**
Use NTP-synchronized clocks in all production deployments. Cloud
environments (GCP, AWS, Azure) provide NTP synchronization by default.
Cloud Spanner eliminates this risk entirely through TrueTime, which
provides bounded uncertainty on wall clock time across all nodes.

**Prototype impact:** Negligible. Single machine, clock drift minimal.
**Production impact:** Medium. Addressed by NTP and Spanner TrueTime.

---

### Risk 3 -- Constraint Key Collisions in Multi-Tenant Deployments

**Description:**
Constraint keys are plain strings -- budget_limit, pii_accessed,
reauth_verified. In a multi-tenant production system where multiple
teams use the same interceptor infrastructure, two different policies
could define constraints with the same key name but different semantics.

**Example:**
Team A defines budget_limit as a daily spend cap in USD.
Team B defines budget_limit as a monthly API call quota.
If sessions from both teams share the same state store namespace,
one team reading the other team's constraint produces incorrect
enforcement decisions silently.

**Mitigation:**
Namespace constraint keys by policy or tenant in production.
Example: finance_policy.budget_limit rather than budget_limit.
The schema supports this with no structural changes -- it is a
naming convention enforced at session creation time.

**Prototype impact:** None. Single tenant, single policy.
**Production impact:** High if not addressed. Addressed by naming convention.

---

### Risk 4 -- State Store as Enforcement Hot Path

**Description:**
Every agent action requires at minimum two state store reads (session
lookup and constraint reads) and one write (execution log) inside a
serializable transaction. At scale this is a high-frequency hot path.
If the state store is slow, every agent action is slow.

**Impact:**
Enforcement latency adds directly to agent workflow latency. NFR-1
requires validation latency under 10ms. This is achievable with a
well-tuned database and connection pooling but requires deliberate
engineering.

**Mitigation:**
Connection pooling to avoid per-request connection overhead. Read
replicas for non-enforcement reads such as audit log queries. Spanner
scales horizontally -- adding nodes reduces per-node load without
schema changes. Session records can be cached in the interceptor
process with a short TTL to reduce read pressure while maintaining
freshness for expiry checks.

**Prototype impact:** None. Low request rate.
**Production impact:** High at scale. Addressed by database choice and caching.

---

## Operational Risks

---

### Risk 5 -- Audit Log Unbounded Growth

**Description:**
The execution_log table is append-only with no deletion path. In a
production system with many agents and high request throughput, the
table grows continuously without bound.

**Example:**
100 agents, 10 requests per second each, 8 hours per day.
That is 28.8 million rows per day. 864 million rows in 30 days.
Query performance degrades without partitioning.

**Mitigation:**
Partition execution_log by time in production. Rows older than a
defined retention window (for example 90 days) are archived to cold
storage (Cloud Storage, S3, or equivalent) and removed from the hot
table. The archive is never deleted -- it is the permanent audit record.
Only the hot table is pruned.

This satisfies both FR-7 (complete audit trail) and operational
performance requirements simultaneously.

**Prototype impact:** None. Demo volumes are small.
**Production impact:** High at scale. Addressed by partitioning and archival.

---

### Risk 6 -- Constraints Table Growth From Append-Only Design

**Description:**
The constraints table never updates or deletes existing rows. Every
constraint change appends a new row. A long-running session with
frequent budget updates accumulates one row per spend. The current
value query must find the most recent row for each key.

**Impact:**
Query performance for get_constraint degrades as rows per session
accumulate. The index on (session_id, constraint_key, set_at DESC)
mitigates this significantly but does not eliminate it for very active
long-running sessions.

**Mitigation:**
After a session ends, compact its constraint history. Keep the final
value of each key as a single archived row. Remove intermediate rows.
Full history is preserved in cold storage. This is a post-session
cleanup job and does not affect live enforcement correctness.

**Prototype impact:** None. Short-lived demo sessions.
**Production impact:** Medium at scale. Addressed by post-session compaction.

---

## Strategic Risks

---

### Risk 7 -- Adoption Friction From Mandatory Interceptor Routing

**Description:**
Every agent action must route through the interceptor. There is no
optional path. Any team that wants this governance pattern must change
how their agent calls tools. This is a real integration cost for
existing systems.

**Impact:**
Teams with existing agentic systems must refactor their tool-calling
code. Teams building new systems must design around the interceptor
from the start. Both have non-trivial integration effort.

**Mitigation:**
Provide the interceptor as a thin sidecar or language-specific SDK.
The agent_simulator.py shows how simple the integration is -- one
validate() call before each tool invocation.

In a service mesh deployment (Envoy, Istio) the interceptor runs as
a sidecar and intercepts traffic transparently with no agent code
change required. This is the production path for maximum adoption.

**Prototype impact:** None. Controlled demo environment.
**Production impact:** High for adoption. Addressed by SDK and sidecar patterns.

---

### Risk 8 -- False Sense of Security

**Description:**
A team that deploys this system might believe all governance problems
are solved. They are not. The threat model is explicit about what is
out of scope: physical coercion, infrastructure-level compromise,
cryptographic token forgery, insider threats with database admin access.

**Impact:**
A team with false confidence stops thinking about governance after
deploying the interceptor. This is more dangerous than never deploying
it because the risk is invisible. It is a documentation and
communication risk, not a technical one.

**Mitigation:**
The threat model document is explicit about out-of-scope items.
The README repeats them. The production boundary section in the
simulator output repeats them. Every demo of this system ends with
a clear statement of what it does not solve.

**Prototype impact:** Communication risk only.
**Production impact:** Organizational. Addressed by documentation and communication.

---

## Risk Summary

| Risk | Category | Prototype | Production | Mitigation |
|---|---|---|---|---|
| 1 SQLite concurrency | Technical | Low | High | Spanner or CockroachDB |
| 2 Clock skew | Technical | Negligible | Medium | NTP, Spanner TrueTime |
| 3 Constraint key collisions | Technical | None | High | Namespace by policy |
| 4 State store hot path | Technical | None | High | Pooling, caching, Spanner |
| 5 Audit log growth | Operational | None | High | Time partitioning, archival |
| 6 Constraints table growth | Operational | None | Medium | Post-session compaction |
| 7 Adoption friction | Strategic | None | High | SDK and sidecar patterns |
| 8 False sense of security | Strategic | Communication | Organizational | Documentation |
