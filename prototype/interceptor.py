"""
interceptor.py
Agent Governance -- Interceptor + State Store Pattern

The sole enforcement layer. Every agent action passes through here.
No tool is reachable without passing all five checks.
Stateless -- all state is read from the state store on every call.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

# Global lock for budget read-validate-write atomicity
# Ensures linearizable enforcement under concurrent requests
_budget_lock = threading.Lock()

from state_store import (
    get_session,
    get_constraint,
    set_constraint,
    log_execution,
)


# Actions that always require reauth_verified = true regardless of session state
SENSITIVE_ACTIONS = {
    "access_sensitive",
}

# Actions blocked after PII taint is introduced (pii_accessed = true)
# These are exfiltration paths -- blocked with no reauth bypass
PII_TAINT_BLOCKED = {
    "post_message",   # slack_api
    "send_email",     # email_api
}

# Trigger map -- action on tool writes a constraint transition on ALLOW
# Format: (tool, action) -> function(session_id, metadata)
def _trigger_pii_taint(session_id: str, metadata: Dict) -> None:
    set_constraint(session_id, "pii_accessed", True)

def _trigger_budget_spent(session_id: str, metadata: Dict) -> None:
    current = get_constraint(session_id, "budget_spent") or 0.0
    amount = metadata.get("amount", 0.0)
    set_constraint(session_id, "budget_spent", current + amount)

def _trigger_reauth(session_id: str, metadata: Dict) -> None:
    set_constraint(session_id, "reauth_verified", True)

TRIGGER_MAP = {
    ("database",      "query_pii_table"):   _trigger_pii_taint,
    ("budget_spend",  "process_payment"):   _trigger_budget_spent,
    ("reauth_check",  "valid_credentials"): _trigger_reauth,
}


@dataclass
class InterceptorDecision:
    """Result of every validate() call."""
    allowed:   bool
    reason:    str
    tool:      str
    action:    str
    timestamp: str

    def __str__(self) -> str:
        status = "ALLOWED" if self.allowed else "BLOCKED"
        return f"[{status}] {self.tool}/{self.action} -- {self.reason}"


def validate(
    session_id:        str,
    claimed_principal: str,
    tool:              str,
    action:            str,
    metadata:          Dict[str, Any] = None,
) -> InterceptorDecision:
    """
    The single enforcement function.
    Runs all five checks in order against fresh state store reads.
    First failing check returns a BLOCK decision immediately.
    On ALLOW: writes triggered state transitions and audit log entry.
    On BLOCK: writes audit log entry only. Tool is never contacted.

    The agent's assertions about identity or constraints are irrelevant.
    This function reads the state store directly and decides independently.
    """
    if metadata is None:
        metadata = {}

    timestamp = datetime.utcnow().isoformat()

    def block(reason: str, skip_log: bool = False) -> InterceptorDecision:
        if not skip_log:
            try:
                log_execution(session_id, tool, action, "BLOCKED", reason)
            except Exception:
                pass  # session may not exist -- cannot log
        return InterceptorDecision(
            allowed=False,
            reason=reason,
            tool=tool,
            action=action,
            timestamp=timestamp,
        )

    def allow(reason: str) -> InterceptorDecision:
        log_execution(session_id, tool, action, "ALLOWED", reason)
        return InterceptorDecision(
            allowed=True,
            reason=reason,
            tool=tool,
            action=action,
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Check 1 -- Session existence
    # ------------------------------------------------------------------
    session = get_session(session_id)
    if session is None:
        return block("no active session found", skip_log=True)

    # ------------------------------------------------------------------
    # Check 2 -- Session validity (active and not expired)
    # ------------------------------------------------------------------
    if not session["active"]:
        return block("session has been revoked")

    now = datetime.utcnow()
    expires_at = datetime.fromisoformat(session["expires_at"])
    if now > expires_at:
        return block(f"session expired at {session['expires_at']}")

    # ------------------------------------------------------------------
    # Check 3 -- Identity continuity
    # ------------------------------------------------------------------
    if claimed_principal != session["principal_id"]:
        return block(
            f"identity mismatch: claimed {claimed_principal}, "
            f"session bound to {session['principal_id']}"
        )

    # ------------------------------------------------------------------
    # Check 4 -- Re-authentication gate
    # Applies to: always-sensitive actions
    # Also applies to: PII-gated actions when pii_accessed is true
    # ------------------------------------------------------------------
    reauth_verified = get_constraint(session_id, "reauth_verified")
    pii_accessed = get_constraint(session_id, "pii_accessed")

    requires_reauth = action in SENSITIVE_ACTIONS
    if pii_accessed and action == "query_pii_table":
        requires_reauth = True

    if requires_reauth and not reauth_verified:
        return block("re-authentication required for sensitive action")

    # ------------------------------------------------------------------
    # Check 5 -- Dynamic constraint evaluation
    # ------------------------------------------------------------------

    # PII taint -- exfiltration path blocking
    if pii_accessed and action in PII_TAINT_BLOCKED:
        return block(
            f"pii_accessed constraint: {tool} blocked as potential "
            f"exfiltration path after PII access this session"
        )

    # Budget enforcement -- wrapped in lock for linearizable read-validate-write
    if action == "process_payment":
        with _budget_lock:
            amount = metadata.get("amount", 0.0)
            if amount <= 0:
                return block("invalid spend amount: must be greater than zero")
            budget_limit = get_constraint(session_id, "budget_limit")
            budget_spent = get_constraint(session_id, "budget_spent")

            if budget_limit is None:
                return block("no budget_limit constraint set for this session")

            if budget_spent + amount > budget_limit:
                return block(
                    f"budget exceeded: limit ${budget_limit:.2f}, "
                    f"spent ${budget_spent:.2f}, "
                    f"requested ${amount:.2f}"
                )

            # Write new budget_spent inside the lock before releasing
            set_constraint(session_id, "budget_spent", budget_spent + amount)

    # ------------------------------------------------------------------
    # All checks passed -- execute trigger map and allow
    # ------------------------------------------------------------------
    # Budget trigger already handled inside lock above
    if action != "process_payment":
        trigger = TRIGGER_MAP.get((tool, action))
        if trigger:
            trigger(session_id, metadata)

    return allow("identity verified, no constraints violated")
