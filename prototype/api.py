from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import Any, Dict, Optional
import os, sqlite3
from state_store import init_db, create_session, set_constraint, get_session, get_session_log, DB_PATH
from interceptor import validate

app = FastAPI(title="Agent Governance API", version="1.0")
init_db()

class CreateSessionRequest(BaseModel):
    principal_id: str
    duration_seconds: int = 3600

class SetConstraintRequest(BaseModel):
    key: str
    value: Any

class ValidateRequest(BaseModel):
    session_id: str
    claimed_principal: str
    tool: str
    action: str
    metadata: Optional[Dict[str, Any]] = {}

@app.get("/", response_class=HTMLResponse)
def root():
    ui = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
    if os.path.exists(ui):
        return FileResponse(ui)
    return HTMLResponse("<h2>Agent Governance API</h2><a href=/docs>API Docs</a>")

@app.post("/session/create")
def api_create_session(req: CreateSessionRequest):
    sid = create_session(req.principal_id, req.duration_seconds)
    return {"session_id": sid, "principal_id": req.principal_id, "expires_in_seconds": req.duration_seconds}

@app.post("/session/{session_id}/constraint")
def api_set_constraint(session_id: str, req: SetConstraintRequest):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    set_constraint(session_id, req.key, req.value)
    return {"ok": True, "session_id": session_id, "key": req.key, "value": req.value}

@app.post("/validate")
def api_validate(req: ValidateRequest):
    decision = validate(session_id=req.session_id, claimed_principal=req.claimed_principal, tool=req.tool, action=req.action, metadata=req.metadata or {})
    return {"allowed": decision.allowed, "reason": decision.reason, "tool": decision.tool, "action": decision.action, "timestamp": decision.timestamp}

@app.get("/session/{session_id}/log")
def api_get_log(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    entries = get_session_log(session_id)
    return {"session_id": session_id, "principal_id": session["principal_id"], "total": len(entries), "log": entries}

@app.get("/sessions")
def api_list_sessions():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"sessions": [dict(r) for r in rows]}
