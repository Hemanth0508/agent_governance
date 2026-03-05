# Agent Governance
## Interceptor + State Store Pattern

**Author:** Hemanth Porapu
**Date:** March 2026

---

## What This Is

A prototype that demonstrates deterministic, session-scoped governance
enforcement across multi-step agentic workflows.

The core invariant:

> No external side effect may execute unless it satisfies the current
> session's immutable and dynamically evolving constraint set.

The enforcement is guaranteed at execution time by infrastructure.
Not by the agent. Not by the LLM. By the interceptor reading the
state store directly on every single request.

The agent proposes actions.
The interceptor decides whether those actions are allowed.

```
Agent (LLM reasoning)
        ↓
Interceptor (enforcement boundary)
        ↓
State Store (session constraints)
        ↓
External Tools (DB / APIs / Slack)
```

---

## Why It Exists

LLM agents are stateless reasoners. They have no reliable memory of
what happened in previous steps. They cannot verify identity. They
cannot enforce constraints across tool boundaries. They can be
manipulated through prompt injection or by a user simply saying
they are someone else.

This creates a specific vulnerability: the agent becomes an
unintentional identity proxy. A user tells the agent they are someone
else. The agent believes them and forwards the claim to the database.
The database trusts the agent. The wrong person gets the wrong data.

The interceptor closes this gap. It sits between the agent and every
tool. It reads the session record directly. It enforces constraints
independently of what the agent was told or what the agent believes.

The agent can reason however it wants. The enforcement is deterministic
regardless.

---

## Directory Structure

```
agent-governance/
├── README.md
├── DIRECTOR_BRIEF.md          Technical Q&A, production path, positioning
├── requirements.txt
├── docs/
│   ├── requirements.md        Problem, stakeholders, FR-1 to FR-10, NFR-1 to NFR-5
│   ├── architecture.md        Five components, execution flow, design decisions
│   ├── data_model.md          Three tables, append-only design, transaction boundary
│   ├── api_spec.md            Every function, input, output, side effect
│   ├── consistency_model.md   Why eventual consistency breaks this system
│   ├── threat_model.md        Seven threats and their mitigations
│   ├── test_plan.md           24 test cases each linked to an architectural invariant
│   ├── risks.md               Eight risks across technical, operational, strategic
│   └── MANUAL.md              Step-by-step walkthrough of all five scenarios
└── prototype/
    ├── state_store.py          SQLite state store -- sessions, constraints, execution_log
    ├── interceptor.py          Five-check enforcement engine
    ├── agent_simulator.py      Five scenarios, 24 test cases
    ├── api.py                  FastAPI HTTP wrapper, five endpoints
    └── ui.html                 Browser UI for interactive demo
```

---

## How to Run

### Terminal -- all scenarios

```bash
cd agent-governance
python3 prototype/agent_simulator.py
```

### Single scenario

```bash
python3 prototype/agent_simulator.py 1    # Identity impersonation
python3 prototype/agent_simulator.py 2    # Data taint propagation
python3 prototype/agent_simulator.py 3    # Concurrent budget race
python3 prototype/agent_simulator.py 4    # Session expiry
python3 prototype/agent_simulator.py 5    # Re-authentication gate
```

### Audit log from last run

```bash
python3 prototype/agent_simulator.py log
```

### Reset database and run fresh

```bash
python3 prototype/agent_simulator.py --reset
```

The database persists between normal runs. History accumulates.
Pass --reset only when you want a completely clean slate.

### Browser UI

```bash
pip install fastapi uvicorn
cd agent-governance/prototype
python3 -m uvicorn api:app --reload --port 8000 or
python -m uvicorn api:app --reload
```

Open http://localhost:8000

Click any scenario to run it step by step. Each decision appears as
a green ALLOWED or red BLOCKED card with a plain English explanation
underneath. The Manual tab lets you fire custom raw requests with any
session, principal, tool, action, and metadata. The Production tab
shows the exact prototype vs production trust boundary.

### View database visually

```bash
pip install datasette
cd agent-governance/prototype
datasette governance.db or
python -m datasette governance.db
```

Open http://localhost:8001

Browse the sessions, constraints, and execution_log tables directly.
See the exact data the interceptor is reading and writing in real time
as scenarios run. Useful for showing the director the raw state.

### Interactive API explorer

With the FastAPI server running, open http://localhost:8000/docs

Full interactive API documentation. Try every endpoint directly in
the browser. No Postman required.

**Important when setting constraints via the API explorer:**
Always send numeric values as numbers, not strings.

    Correct:  { "key": "budget_limit", "value": 500 }
    Wrong:    { "key": "budget_limit", "value": "500" }

The wrong form stores the value as a string. The math comparison
in the interceptor fails silently. Budget enforcement breaks.
Same rule applies to booleans: use true not "true".

---

## The Five Scenarios

### Scenario 1 -- Identity Impersonation (Eve and Sasha)

Eve authenticates. Her session is bound to eve@company.com.
She leaves her desk without logging out. Sasha sits down and tells
the agent she is Eve. The agent forwards the claim. The interceptor
reads the session record directly and catches the mismatch.
The database is never contacted.

Proves FR-3: the agent cannot be used as an identity proxy.

### Scenario 2 -- Data Taint Propagation (Alice)

Alice accesses PII data. This arms a session-level taint flag.
After the taint is armed, Slack is blocked as a potential exfiltration
path. Repeat PII access requires re-authentication. Slack has no idea
PII was accessed. Only the state store knows.

Proves FR-4, FR-5: a constraint written by one tool in step 1 governs
what is allowed on a completely different tool in step 3.

### Scenario 3 -- Concurrent Budget Race (Bob)

Two agents simultaneously attempt spends that individually fit the
budget but together exceed it. Only one passes. The budget invariant
holds under real concurrency.

Proves FR-8, NFR-3: linearizable read-modify-write prevents race
conditions that eventually consistent storage cannot catch.

### Scenario 4 -- Session Expiry (Carol)

A session is created with a 2-second lifetime. An immediate request
passes. After 3 seconds the same request is blocked. The identity is
valid but the session is dead.

Proves FR-9: session lifetime is enforced at the infrastructure layer.
Identity match is not sufficient on an expired session.

### Scenario 5 -- Re-authentication Gate (Dave)

Dave tries to access sensitive data without re-authenticating. The agent
then passes a fake reauth token in metadata claiming reauth is complete.
The interceptor ignores the metadata claim, reads the state store
directly, and blocks the request.

Proves FR-10, NFR-5: agent assertions about constraint state are
irrelevant. The interceptor never trusts the agent.

---

## Positioning

This prototype is two things simultaneously.

**A Google Cloud Databases product opportunity.**
The state store requires linearizable reads and serializable
transactions. That is Spanner. Agentic governance at scale needs a
database that guarantees enforcement correctness across distributed
nodes. Spanner is the only managed offering with TrueTime-backed
external consistency. This prototype demonstrates exactly why the
database choice is an architectural requirement, not an implementation
detail. Every other database in this category either breaks FR-8 under
concurrency or sacrifices the consistency guarantees that make the
enforcement deterministic.

---

## What This System Does Not Solve

Physical coercion of the authenticated principal.
Infrastructure OS-level compromise.
Cryptographic token forgery without signed tokens in place.
Insider threats with database administrator access.

These are outside the scope of any application-layer system.
The threat model document is explicit about these boundaries.
Knowing precisely what you do not solve is as important as knowing
what you do.
