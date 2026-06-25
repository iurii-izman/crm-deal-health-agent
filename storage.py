import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

DB_PATH = str(Path(__file__).resolve().parent / "agent_runs.db")

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                deal_id INTEGER,
                deal_title TEXT,
                model_used TEXT,
                risk_level TEXT,
                priority TEXT,
                case_type TEXT,
                missing_fields_json TEXT,
                recommended_actions_json TEXT,
                draft_reply TEXT,
                raw_deal_json TEXT,
                raw_result_json TEXT,
                status TEXT NOT NULL
            )
        """)
        conn.commit()

def log_run(
    source: str,
    status: str,
    deal_context: Optional[Dict[str, Any]] = None,
    agent_result: Optional[Dict[str, Any]] = None
):
    created_at = datetime.utcnow().isoformat() + "Z"
    
    deal_id = deal_context.get("deal_id") if deal_context else None
    deal_title = deal_context.get("title") if deal_context else None
    raw_deal_json = json.dumps(deal_context, ensure_ascii=False) if deal_context else None
    
    model_used = agent_result.get("model_used") if agent_result else None
    risk_level = agent_result.get("risk_level") if agent_result else None
    priority = agent_result.get("priority") if agent_result else None
    case_type = agent_result.get("case_type") if agent_result else None
    missing_fields_json = json.dumps(agent_result.get("missing_fields", []), ensure_ascii=False) if agent_result else None
    recommended_actions_json = json.dumps(agent_result.get("recommended_actions", []), ensure_ascii=False) if agent_result else None
    draft_reply = agent_result.get("draft_reply") if agent_result else None
    raw_result_json = json.dumps(agent_result, ensure_ascii=False) if agent_result else None

    # If status is "success" but model_used has "fallback", switch status to "fallback"
    if status == "success" and model_used and "fallback" in model_used.lower():
        status = "fallback"

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agent_runs (
                created_at, source, deal_id, deal_title, model_used,
                risk_level, priority, case_type, missing_fields_json,
                recommended_actions_json, draft_reply, raw_deal_json,
                raw_result_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            created_at, source, deal_id, deal_title, model_used,
            risk_level, priority, case_type, missing_fields_json,
            recommended_actions_json, draft_reply, raw_deal_json,
            raw_result_json, status
        ))
        conn.commit()

def get_recent_runs(limit: int = 20) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, created_at, source, deal_id, deal_title, risk_level, priority, model_used, status
            FROM agent_runs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_run_details(run_id: int) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
