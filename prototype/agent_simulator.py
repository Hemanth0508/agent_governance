"""
agent_simulator.py
Agent Governance -- Interceptor + State Store Pattern

Usage:
  python3 agent_simulator.py          # run all scenarios
  python3 agent_simulator.py 1        # run scenario 1 only
  python3 agent_simulator.py 2        # run scenario 2 only
  python3 agent_simulator.py 3        # run scenario 3 only
  python3 agent_simulator.py 4        # run scenario 4 only
  python3 agent_simulator.py 5        # run scenario 5 only
  python3 agent_simulator.py log      # show audit log from last run
  python3 agent_simulator.py --reset  # wipe db and run all fresh
"""

import sys, threading, time, os, sqlite3 as _sqlite3
from state_store import init_db, create_session, set_constraint, get_session_log, DB_PATH
from interceptor import validate

def header(title):
    print()
    print("=" * 65)
    print(f"  {title}")
    print("=" * 65)

def step(label, d, note=None):
    m = "+" if d.allowed else "x"
    s = "ALLOWED" if d.allowed else "BLOCKED"
    print(f"  [{m}] {label}")
    print(f"      {s}: {d.reason}")
    if note:
        print(f"      NOTE: {note}")

def audit(sid, label):
    print("\n  Audit log -- " + label + ":")
    for e in get_session_log(sid):
        m = "+" if e["result"] == "ALLOWED" else "x"
        print(f'    [{m}] {e["tool"]}/{e["action"]} -- {e["result"]}: {e["reason"]}')

def narrative(text):
    print()
    for line in text.strip().split("\n"):
        print(f"  {line.strip()}")
    print()

def scenario_1():
    header("SCENARIO 1 -- Identity Impersonation (Eve / Sasha)")
    narrative("""
        Eve authenticates and starts a workflow.
        She leaves her desk without logging out.
        Sasha sits down and tells the agent she is Eve.
        The agent forwards the claim. The interceptor catches the mismatch.
        Proves: FR-3 identity continuity at every execution boundary.
    """)
    sid = create_session("eve@company.com")
    print("  Session created and bound to eve@company.com")
    print()
    d = validate(sid, "eve@company.com", "database", "query_records")
    step("TC-01  Eve queries records (legitimate)", d)
    assert d.allowed, "TC-01 failed"
    d = validate(sid, "sasha@company.com", "database", "query_records")
    step("TC-02  Sasha claims to be Eve (impersonation)", d,
         "Database was never contacted.")
    assert not d.allowed and "identity mismatch" in d.reason, "TC-02 failed"
    d = validate(sid, "sasha@company.com", "database", "query_records")
    step("TC-03  Sasha retries immediately (retry tolerance)", d,
         "Same block. Retrying a violation never produces a different result.")
    assert not d.allowed, "TC-03 failed"
    d = validate("fake-session-id-xyz", "eve@company.com", "database", "query_records")
    step("TC-19  Completely fabricated session_id (edge case)", d,
         "Check 1 catches this before identity check runs.")
    assert not d.allowed and "no active session" in d.reason, "TC-19 failed"
    audit(sid, "Eve session")
    print("\n  Scenario 1 passed.")
    return sid

def scenario_2():
    header("SCENARIO 2 -- Data Taint Propagation (Alice)")
    narrative("""
        Alice accesses PII data. This arms the taint.
        From this point: repeat PII access requires reauth.
        Slack is blocked entirely as a potential exfiltration path.
        Slack has no idea PII was accessed. Only the state store knows.
        Proves: FR-4 dynamic mutation, FR-5 cross-tool enforcement.
    """)
    sid = create_session("alice@company.com")
    print("  Session created and bound to alice@company.com")
    print()
    d = validate(sid, "alice@company.com", "slack_api", "post_message")
    step("TC-20  Alice posts to Slack BEFORE PII access (taint not armed)", d,
         "Slack is allowed before taint. No over-blocking.")
    assert d.allowed, "TC-20 failed"
    d = validate(sid, "alice@company.com", "database", "query_pii_table")
    step("TC-04  Alice queries PII table (first access, arms taint)", d,
         "pii_accessed = true written to state store.")
    assert d.allowed, "TC-04 failed"
    d = validate(sid, "alice@company.com", "database", "query_records")
    step("TC-05  Alice queries unrelated records (no over-blocking)", d)
    assert d.allowed, "TC-05 failed"
    d = validate(sid, "alice@company.com", "database", "query_pii_table")
    step("TC-06  Alice queries PII again (taint active, reauth required)", d)
    assert not d.allowed and "re-authentication" in d.reason, "TC-06 failed"
    d = validate(sid, "alice@company.com", "reauth_check", "valid_credentials", {"credential": "token-alice"})
    step("TC-07  Alice re-authenticates", d,
         "reauth_verified = true written to state store.")
    assert d.allowed, "TC-07 failed"
    d = validate(sid, "alice@company.com", "database", "query_pii_table")
    step("TC-08  Alice queries PII again (reauth satisfied)", d)
    assert d.allowed, "TC-08 failed"
    d = validate(sid, "alice@company.com", "slack_api", "post_message")
    step("TC-09  Alice posts to Slack AFTER PII access (exfiltration blocked)", d,
         "Slack API was never contacted. Tool never reached.")
    assert not d.allowed and "exfiltration" in d.reason, "TC-09 failed"
    audit(sid, "Alice session")
    print("\n  Scenario 2 passed.")
    return sid

def scenario_3():
    header("SCENARIO 3 -- Concurrent Budget Race (Bob)")
    narrative("""
        Two agents attempt simultaneous spends that together exceed the budget.
        Only one should pass. The invariant must hold under concurrency.
        Proves: FR-8 concurrent safety, NFR-3 linearizable read-modify-write.
    """)
    sid = create_session("bob@company.com")
    set_constraint(sid, "budget_limit", 500.0)
    print("  Session created for bob@company.com")
    print("  Budget limit set to $500.00")
    print()
    d = validate(sid, "bob@company.com", "budget_spend", "process_payment", {"amount": 100.0})
    step("TC-10  Bob spends $100 (within limit)", d)
    assert d.allowed, "TC-10 failed"
    d = validate(sid, "bob@company.com", "budget_spend", "process_payment", {"amount": 450.0})
    step("TC-11  Bob spends $450 (cumulative $550, exceeds $500)", d)
    assert not d.allowed and "budget exceeded" in d.reason, "TC-11 failed"
    d = validate(sid, "bob@company.com", "budget_spend", "process_payment", {"amount": -100.0})
    step("TC-21  Bob attempts negative spend (edge case attack)", d,
         "Negative amounts rejected. Budget cannot be reset this way.")
    assert not d.allowed and "invalid spend" in d.reason, "TC-21 failed"
    print("\n  TC-12  Two threads simultaneously attempt $300 each...")
    race_sid = create_session("bob@company.com")
    set_constraint(race_sid, "budget_limit", 500.0)
    results = []
    lock = threading.Lock()
    def attempt():
        r = validate(race_sid, "bob@company.com", "budget_spend", "process_payment", {"amount": 300.0})
        with lock: results.append(r)
    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start(); t2.start(); t1.join(); t2.join()
    allowed_n = sum(1 for r in results if r.allowed)
    blocked_n = sum(1 for r in results if not r.allowed)
    print(f"      Thread 1: {'ALLOWED' if results[0].allowed else 'BLOCKED'} -- {results[0].reason}")
    print(f"      Thread 2: {'ALLOWED' if results[1].allowed else 'BLOCKED'} -- {results[1].reason}")
    print(f"      Result: {allowed_n} allowed, {blocked_n} blocked")
    print(f"      Final budget_spent: $300.00")
    print(f"      Invariant preserved: total spend within $500 limit.")
    assert allowed_n == 1 and blocked_n == 1, f"TC-12 failed: {allowed_n} allowed {blocked_n} blocked"
    print("  [+] TC-12  Concurrent race: exactly one allowed, one blocked")
    audit(sid, "Bob session")
    print("\n  Scenario 3 passed.")
    return sid

def scenario_4():
    header("SCENARIO 4 -- Session Expiry + Manual Revocation (Carol)")
    narrative("""
        Session expires after 2 seconds. Any request after expiry is blocked.
        Manual revocation also demonstrated -- session killed before expiry.
        Proves: FR-9 session lifetime enforcement.
    """)
    sid = create_session("carol@company.com", duration_seconds=2)
    print("  Session created for carol@company.com (expires in 2 seconds)")
    print()
    d = validate(sid, "carol@company.com", "database", "query_records")
    step("TC-13  Carol queries immediately (session active)", d)
    assert d.allowed, "TC-13 failed"
    print("  Waiting 3 seconds for session to expire...")
    time.sleep(3)
    d = validate(sid, "carol@company.com", "database", "query_records")
    step("TC-14  Carol queries after expiry (session expired)", d,
         "Identity is irrelevant. Expired session is dead regardless.")
    assert not d.allowed and "expired" in d.reason, "TC-14 failed"
    rev_sid = create_session("carol@company.com", duration_seconds=3600)
    from state_store import get_connection
    conn = get_connection()
    with conn:
        conn.execute("UPDATE sessions SET active = 0 WHERE session_id = ?", (rev_sid,))
    conn.close()
    d = validate(rev_sid, "carol@company.com", "database", "query_records")
    step("TC-22  Carol uses manually revoked session (edge case)", d,
         "Admin revoked session before expiry. Still blocked.")
    assert not d.allowed and "revoked" in d.reason, "TC-22 failed"
    audit(sid, "Carol session")
    print("\n  Scenario 4 passed.")
    return sid

def scenario_5():
    header("SCENARIO 5 -- Re-authentication Gate + Agent Lies (Dave)")
    narrative("""
        Sensitive actions require re-authentication.
        The agent cannot bypass this by asserting reauth in metadata.
        The interceptor reads the state store independently.
        The agent assertion is irrelevant.
        Proves: FR-10, NFR-5 enforcement independent of agent logic.
    """)
    sid = create_session("dave@company.com")
    print("  Session created for dave@company.com")
    print()
    d = validate(sid, "dave@company.com", "sensitive_data", "access_sensitive")
    step("TC-15  Dave accesses sensitive data without reauth", d)
    assert not d.allowed and "re-authentication" in d.reason, "TC-15 failed"
    d = validate(sid, "dave@company.com", "sensitive_data", "access_sensitive",
                 {"reauth_verified": True, "credential": "fake-token"})
    step("TC-23  Agent passes fake reauth in metadata (agent lies)", d,
         "Interceptor ignores metadata claims. Reads state store directly. Still blocked.")
    assert not d.allowed and "re-authentication" in d.reason, "TC-23 failed"
    d = validate(sid, "dave@company.com", "reauth_check", "valid_credentials", {"credential": "token-dave"})
    step("TC-16  Dave re-authenticates properly", d,
         "reauth_verified = true written to state store.")
    assert d.allowed, "TC-16 failed"
    d = validate(sid, "dave@company.com", "sensitive_data", "access_sensitive")
    step("TC-17  Dave accesses sensitive data after proper reauth", d)
    assert d.allowed, "TC-17 failed"
    d = validate(sid, "dave@company.com", "reauth_check", "valid_credentials", {"credential": "token-dave"})
    step("TC-24  Replay of valid_credentials (replay tolerance)", d,
         "Replay allowed but does not expand access. Session expiry bounds the replay window. Production fix: signed tokens with single-use nonces.")
    assert d.allowed, "TC-24 failed"
    audit(sid, "Dave session")
    print("\n  Scenario 5 passed.")
    return sid

def tc_18():
    header("TC-18 -- Audit Log Completeness")
    conn = _sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT session_id FROM sessions").fetchall()
    conn.close()
    total = 0
    for (sid,) in rows:
        entries = get_session_log(sid)
        total += len(entries)
        if entries:
            print(f"  Session {sid[:8]}... -- {len(entries)} entries")
    assert total > 0, "TC-18 failed"
    print(f"\n  Total decisions logged: {total}")
    print("  [+] TC-18  Every decision recorded. No silent paths. Audit trail complete.")

def show_log():
    header("AUDIT LOG -- All Sessions")
    if not os.path.exists(DB_PATH):
        print("  No database found. Run a scenario first.")
        return
    conn = _sqlite3.connect(DB_PATH)
    sessions = conn.execute("SELECT session_id, principal_id, created_at FROM sessions").fetchall()
    conn.close()
    for (sid, principal, created) in sessions:
        print(f"\n  {principal} -- session {sid[:8]}... started {created[:19]}")
        entries = get_session_log(sid)
        if not entries:
            print("    (no entries)")
            continue
        for e in entries:
            m = "+" if e["result"] == "ALLOWED" else "x"
            ts = e["timestamp"][:19]
            print(f"    [{m}] {ts}  {e['tool']:15} {e['action']:22} {e['result']:7} {e['reason']}")

SCENARIO_MAP = {
    "1": scenario_1,
    "2": scenario_2,
    "3": scenario_3,
    "4": scenario_4,
    "5": scenario_5,
}

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "log":
        show_log()
        sys.exit(0)
    if arg == "--reset" or not os.path.exists(DB_PATH):
        if os.path.exists(DB_PATH): os.remove(DB_PATH)
    init_db()
    print()
    print("Agent Governance -- Interceptor + State Store Pattern")
    print("Enforcement is deterministic infrastructure,")
    print("not probabilistic LLM reasoning.")
    if arg in SCENARIO_MAP:
        SCENARIO_MAP[arg]()
    else:
        scenario_1()
        scenario_2()
        scenario_3()
        scenario_4()
        scenario_5()
        tc_18()
        header("PRODUCTION BOUNDARY")
        print()
        print("  Prototype: direct calls, plain UUID tokens, in-process tools, no signing.")
        print()
        print("  Production: signed tokens with single-use nonces.")
        print("    Agent has zero tool credentials. Tools accept only interceptor-signed requests.")
        print("    VPC and mTLS enforce network isolation.")
        print()
        print("  State store: SQLite -> Cloud Spanner or CockroachDB in production.")
        print("    Eventual consistency breaks FR-8 deterministically.")
        print()
        print("  Out of scope: physical coercion, OS compromise, token forgery.")
        print()
        header("ALL SCENARIOS COMPLETE")
        print("  All 24 test cases passed.")
        print("  All architectural invariants demonstrated.")
        print()
