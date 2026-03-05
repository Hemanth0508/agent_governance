# Manual Walkthrough
## Agent Governance -- Interceptor + State Store Pattern

This document gives exact step-by-step instructions for reproducing
all five scenarios manually through the browser UI and API.

Use this when you want full control over each request -- choosing
which step to fire next, what values to send, and seeing the raw
decision before moving on.

---

## Before You Start

Make sure the server is running:

```bash
cd agent-governance/prototype
python3 -m uvicorn api:app --reload --port 8000
```

Open http://localhost:8000 in your browser.
Click the Manual tab on the left panel.

---

## Important: Setting Constraints via API Docs

When you use http://localhost:8000/docs to set a budget_limit or
other numeric constraint, always send the value as a number, not
a string.

Correct:
  { "key": "budget_limit", "value": 500 }

Wrong -- stored as string, math comparison fails silently:
  { "key": "budget_limit", "value": "500" }

Same rule applies to booleans. Use true not "true".

---

## Scenario 1 -- Identity Impersonation (Eve and Sasha)

What this proves: FR-3 -- the agent cannot be used as an identity proxy.

### Create Session

In the Manual tab, scroll to Create Session.

  Principal ID:  eve@company.com
  Duration:      3600

Click Create Session.
The session_id auto-fills in the Session ID field above.

---

### TC-01 -- Eve queries records (should be ALLOWED)

  Session ID:        (auto-filled)
  Claimed Principal: eve@company.com
  Tool:              database
  Action:            query_records
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: ALLOWED -- identity verified, no constraints violated.
This is the baseline. Legitimate request passes.

---

### TC-02 -- Sasha claims to be Eve (should be BLOCKED)

Change only Claimed Principal:

  Claimed Principal: sasha@company.com

Everything else stays the same.
Click Send to Interceptor.
Expected: BLOCKED -- identity mismatch: claimed sasha@company.com,
session bound to eve@company.com.
The database was never contacted. Tool never reached.

---

### TC-03 -- Sasha retries (should be BLOCKED again)

Do not change anything. Click Send to Interceptor again.
Expected: BLOCKED -- same reason.
Retrying a blocked request never produces a different result.

---

### TC-19 -- Fabricated session_id (should be BLOCKED)

  Session ID:        fake-session-xyz-123
  Claimed Principal: eve@company.com
  Tool:              database
  Action:            query_records

Click Send to Interceptor.
Expected: BLOCKED -- no active session found.
Check 1 fires before identity check even runs.

---

## Scenario 2 -- Data Taint Propagation (Alice)

What this proves: FR-4, FR-5 -- a constraint written by one tool
governs what is allowed on a completely different tool later.

### Create Session

  Principal ID:  alice@company.com
  Duration:      3600

Click Create Session.

---

### TC-20 -- Slack BEFORE PII (should be ALLOWED)

  Claimed Principal: alice@company.com
  Tool:              slack_api
  Action:            post_message
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: ALLOWED.
Slack is fine before PII is accessed. No taint armed yet.

---

### TC-04 -- First PII access (should be ALLOWED, arms taint)

  Claimed Principal: alice@company.com
  Tool:              database
  Action:            query_pii_table
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: ALLOWED.
pii_accessed = true is now written to the state store.
The taint is armed from this point forward.

---

### TC-05 -- Unrelated query (should be ALLOWED, no over-blocking)

  Claimed Principal: alice@company.com
  Tool:              database
  Action:            query_records
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: ALLOWED.
Taint only blocks exfiltration paths. Normal queries still work.

---

### TC-06 -- PII again after taint (should be BLOCKED)

  Claimed Principal: alice@company.com
  Tool:              database
  Action:            query_pii_table
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: BLOCKED -- re-authentication required for sensitive action.

---

### TC-07 -- Re-authenticate (should be ALLOWED)

  Claimed Principal: alice@company.com
  Tool:              reauth_check
  Action:            valid_credentials
  Metadata JSON:     {"credential": "token-alice"}

Click Send to Interceptor.
Expected: ALLOWED.
reauth_verified = true is now written to the state store.

---

### TC-08 -- PII again after reauth (should be ALLOWED)

  Claimed Principal: alice@company.com
  Tool:              database
  Action:            query_pii_table
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: ALLOWED. Reauth satisfied.

---

### TC-09 -- Slack AFTER PII (should be BLOCKED)

  Claimed Principal: alice@company.com
  Tool:              slack_api
  Action:            post_message
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: BLOCKED -- pii_accessed constraint: slack_api blocked
as potential exfiltration path after PII access this session.

This is the key result. Same tool, same action, same principal,
same session as TC-20. But TC-20 was allowed and TC-09 is blocked.
The only thing that changed is what happened in between.
Slack has no idea PII was accessed. Only the state store knows.

---

## Scenario 3 -- Concurrent Budget Race (Bob)

What this proves: FR-8, NFR-3 -- linearizable read-modify-write
prevents race conditions that eventual consistency cannot catch.

### Create Session

  Principal ID:  bob@company.com
  Duration:      3600

Click Create Session. Copy the session_id.

### Set Budget Limit

Open http://localhost:8000/docs in a new tab.
Find POST /session/{session_id}/constraint.
Enter the session_id in the path field.
In the request body:

  { "key": "budget_limit", "value": 500 }

Click Execute.
You should see: { "ok": true, "key": "budget_limit", "value": 500 }

Note: value must be 500 not "500". See warning at top of this doc.

---

### TC-10 -- Spend $100 (should be ALLOWED)

  Claimed Principal: bob@company.com
  Tool:              budget_spend
  Action:            process_payment
  Metadata JSON:     {"amount": 100.0}

Click Send to Interceptor.
Expected: ALLOWED. budget_spent = $100.00 written to state store.

---

### TC-11 -- Spend $450 (should be BLOCKED, cumulative $550)

  Claimed Principal: bob@company.com
  Tool:              budget_spend
  Action:            process_payment
  Metadata JSON:     {"amount": 450.0}

Click Send to Interceptor.
Expected: BLOCKED -- budget exceeded: limit $500.00, spent $100.00,
requested $450.00.

---

### TC-21 -- Negative spend (should be BLOCKED)

  Claimed Principal: bob@company.com
  Tool:              budget_spend
  Action:            process_payment
  Metadata JSON:     {"amount": -100.0}

Click Send to Interceptor.
Expected: BLOCKED -- invalid spend amount: must be greater than zero.
This attack vector is closed.

---

### TC-12 -- Concurrent race

For a true concurrent race you need two browser tabs or Postman.

Tab 1 and Tab 2 both have the same session_id and:
  Tool:    budget_spend
  Action:  process_payment
  Metadata: {"amount": 300.0}

Click Send in both tabs as simultaneously as possible.

Expected: exactly one ALLOWED and one BLOCKED.
The one that acquired the SQLite write lock first passes.
The other sees the updated total ($300 + $300 = $600 > $500) and is blocked.
Final budget_spent: $300. Invariant preserved.

---

## Scenario 4 -- Session Expiry (Carol)

What this proves: FR-9 -- session lifetime enforced at infrastructure layer.

### Create Session with 2 second expiry

  Principal ID:  carol@company.com
  Duration:      2

Click Create Session.

---

### TC-13 -- Immediate query (should be ALLOWED)

  Claimed Principal: carol@company.com
  Tool:              database
  Action:            query_records
  Metadata JSON:     {}

Click Send immediately.
Expected: ALLOWED. Session is still active.

---

### TC-14 -- Query after expiry (should be BLOCKED)

Wait 3 seconds. Then click Send again with the same values.
Expected: BLOCKED -- session expired.
Identity is valid but the session is dead. Identity match is
not sufficient on an expired session.

---

## Scenario 5 -- Re-authentication Gate (Dave)

What this proves: FR-10, NFR-5 -- the interceptor never trusts
the agent. Enforcement is independent of agent assertions.

### Create Session

  Principal ID:  dave@company.com
  Duration:      3600

Click Create Session.

---

### TC-15 -- Sensitive access without reauth (should be BLOCKED)

  Claimed Principal: dave@company.com
  Tool:              sensitive_data
  Action:            access_sensitive
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: BLOCKED -- re-authentication required for sensitive action.

---

### TC-23 -- Agent lies about reauth (should still be BLOCKED)

  Claimed Principal: dave@company.com
  Tool:              sensitive_data
  Action:            access_sensitive
  Metadata JSON:     {"reauth_verified": true, "credential": "fake-token"}

Click Send to Interceptor.
Expected: BLOCKED -- same reason.
The interceptor ignores metadata claims entirely.
It reads reauth_verified from the state store directly.
The state store says false. The metadata says true. State store wins.

---

### TC-16 -- Re-authenticate properly (should be ALLOWED)

  Claimed Principal: dave@company.com
  Tool:              reauth_check
  Action:            valid_credentials
  Metadata JSON:     {"credential": "token-dave"}

Click Send to Interceptor.
Expected: ALLOWED.
reauth_verified = true written to state store.

---

### TC-17 -- Sensitive access after reauth (should be ALLOWED)

  Claimed Principal: dave@company.com
  Tool:              sensitive_data
  Action:            access_sensitive
  Metadata JSON:     {}

Click Send to Interceptor.
Expected: ALLOWED.

---

## Viewing the Session Log

After running any steps manually, click View This Session Log
in the Manual tab. It reads whatever session_id is currently in
the Session ID field and shows the complete decision timeline for
that session.

Every decision is recorded -- ALLOWED and BLOCKED both.
No silent paths.

---

## Quick Reference -- Tools and Actions

| Tool           | Action              | What it does                              |
|----------------|---------------------|-------------------------------------------|
| database       | query_records       | General database query                    |
| database       | query_pii_table     | PII data access -- arms taint on first use|
| slack_api      | post_message        | Post to Slack -- blocked after PII access |
| budget_spend   | process_payment     | Spend against budget -- needs amount in metadata |
| reauth_check   | valid_credentials   | Re-authenticate -- needs credential in metadata |
| sensitive_data | access_sensitive    | Sensitive access -- needs reauth_verified |

---

## Quick Reference -- Metadata

| Scenario       | Metadata needed                          |
|----------------|------------------------------------------|
| Budget spend   | {"amount": 100.0}                        |
| Re-auth        | {"credential": "token-alice"}            |
| Agent lie test | {"reauth_verified": true, "credential": "fake"} |
| Everything else| {}                                       |
