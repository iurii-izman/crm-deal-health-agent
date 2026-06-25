import json
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from schemas import (
    DealContext, AgentResult, BitrixRequest, BitrixDealRequest, BitrixAnalyzeRequest,
    BitrixCommentRequest, BitrixTaskRequest, BitrixApiResult, ActionExecutionRequest,
    BitrixOutgoingEventResult,
)
from deterministic_agent import analyze_deterministic
from ollama_client import analyze_with_ollama, CURRENT_MODEL, FAST_MODE, OLLAMA_BASE_URL, OLLAMA_THINK, OLLAMA_NUM_CTX, OLLAMA_NUM_PREDICT
from bitrix_client import BitrixClient
from bitrix_mapper import deal_to_context, format_agent_comment
from bitrix_event_parser import (
    parse_bitrix_request, extract_deal_id, extract_event_type,
    extract_stage_id, extract_secret, extract_bitrix_token, sanitize_payload,
)
from storage import (
    init_db, log_run, get_recent_runs, get_run_details,
    build_event_key, is_recent_event_processed, mark_event_processed,
)

load_dotenv()

app = FastAPI(title="BOS CRM Deal Health Agent", version="0.4.0")
init_db()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEMO_DEAL_PATH = BASE_DIR / "demo_deal.json"

APP_MODE = os.getenv("APP_MODE", "demo").lower()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_agent(deal: DealContext) -> AgentResult:
    use_ollama = os.getenv("USE_OLLAMA", "true").lower() == "true"
    if use_ollama:
        try:
            return analyze_with_ollama(deal)
        except Exception as exc:
            fallback = analyze_deterministic(deal)
            fallback.risk_explanation = (
                fallback.risk_explanation
                + f" Fallback activated because Ollama call failed: {type(exc).__name__}."
            )
            return fallback
    return analyze_deterministic(deal)


def is_bitrix_write_allowed() -> bool:
    """Re-reads env each call so toggling ALLOW_BITRIX_WRITE in .env + reload takes effect."""
    return os.getenv("ALLOW_BITRIX_WRITE", "false").lower() == "true"


def validate_event_secret(secret: Optional[str]) -> None:
    """
    Validate the handler secret against BITRIX_EVENT_SECRET.

    - In demo mode: allow empty/change-me secret (not production-safe).
    - In live mode: empty/change-me secret → HTTP 500 (misconfiguration).
    - Wrong secret → HTTP 403.
    """
    configured = os.getenv("BITRIX_EVENT_SECRET", "change-me").strip()
    app_mode = os.getenv("APP_MODE", "demo").lower()

    is_placeholder = not configured or configured == "change-me"

    if app_mode == "live" and is_placeholder:
        raise HTTPException(
            status_code=500,
            detail="BITRIX_EVENT_SECRET is not configured. Set a strong secret before enabling live mode."
        )

    if is_placeholder:
        # demo mode + placeholder secret → allow (log warning implicitly via debug flag)
        return

    if secret != configured:
        raise HTTPException(status_code=403, detail="Invalid event secret.")


def validate_optional_outgoing_token(token: Optional[str]) -> None:
    """
    If BITRIX_OUTGOING_TOKEN is configured, verify the incoming token matches.
    If BITRIX_OUTGOING_TOKEN is empty/unset, skip validation entirely.
    """
    configured = os.getenv("BITRIX_OUTGOING_TOKEN", "").strip()
    if not configured:
        return
    if token != configured:
        raise HTTPException(status_code=403, detail="Invalid outgoing webhook token.")


# ---------------------------------------------------------------------------
# Static routes
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    configured_secret = os.getenv("BITRIX_EVENT_SECRET", "").strip()
    configured_token = os.getenv("BITRIX_OUTGOING_TOKEN", "").strip()
    return {
        "status": "ok",
        "use_ollama": os.getenv("USE_OLLAMA", "true").lower() == "true",
        "model": os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        "think": os.getenv("OLLAMA_THINK", "false").lower() == "true",
        "bitrix_configured": bool(os.getenv("BITRIX_WEBHOOK_URL", "").strip()),
        "app_mode": APP_MODE,
        "allow_bitrix_write": is_bitrix_write_allowed(),
        # Block D additions
        "event_secret_configured": bool(configured_secret and configured_secret != "change-me"),
        "outgoing_token_configured": bool(configured_token),
        "event_idempotency_ttl_seconds": int(os.getenv("EVENT_IDEMPOTENCY_TTL_SECONDS", "600")),
        "public_handler_path": "/bitrix/outgoing-webhook",
    }


# ---------------------------------------------------------------------------
# Demo endpoints
# ---------------------------------------------------------------------------


@app.get("/demo-deal", response_model=DealContext)
def demo_deal():
    with open(DEMO_DEAL_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return DealContext.model_validate(data)


@app.post("/analyze", response_model=AgentResult)
def analyze(deal: DealContext):
    try:
        result = run_agent(deal)
        log_run("demo", "success", deal.model_dump(), result.model_dump())
        return result
    except Exception as exc:
        log_run("demo", "error", deal.model_dump(), None)
        raise exc


@app.get("/analyze-demo", response_model=AgentResult)
def analyze_demo():
    with open(DEMO_DEAL_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    deal = DealContext.model_validate(data)
    try:
        result = run_agent(deal)
        log_run("demo", "success", deal.model_dump(), result.model_dump())
        return result
    except Exception as exc:
        log_run("demo", "error", deal.model_dump(), None)
        raise exc


# ---------------------------------------------------------------------------
# Bitrix24 manual endpoints
# ---------------------------------------------------------------------------


@app.post("/bitrix/test", response_model=BitrixApiResult)
def bitrix_test(req: BitrixRequest):
    try:
        client = BitrixClient(req.webhook_url)
        result = client.test()
        return BitrixApiResult(ok=True, method="profile", result=result)
    except Exception as exc:
        return BitrixApiResult(ok=False, method="profile", error=str(exc))


@app.post("/bitrix/read-deal", response_model=DealContext)
def bitrix_read_deal(req: BitrixDealRequest):
    client = BitrixClient(req.webhook_url)
    return deal_to_context(client.get_deal(req.deal_id))


@app.post("/bitrix/analyze-deal", response_model=AgentResult)
def bitrix_analyze_deal(req: BitrixDealRequest):
    deal = None
    try:
        client = BitrixClient(req.webhook_url)
        deal = deal_to_context(client.get_deal(req.deal_id))
        result = run_agent(deal)
        log_run("bitrix", "success", deal.model_dump(), result.model_dump())
        return result
    except Exception as exc:
        log_run("bitrix", "error", deal.model_dump() if deal else None, None)
        raise exc


@app.post("/bitrix/write-comment", response_model=BitrixApiResult)
def bitrix_write_comment(req: BitrixCommentRequest):
    if not is_bitrix_write_allowed():
        return BitrixApiResult(ok=True, method="crm.timeline.comment.add", dry_run=True, message="Write operation skipped because ALLOW_BITRIX_WRITE=false", planned_action={"comment": req.comment})
    try:
        client = BitrixClient(req.webhook_url)
        result = client.add_deal_comment(req.deal_id, req.comment)
        return BitrixApiResult(ok=True, method="crm.timeline.comment.add", result=result)
    except Exception as exc:
        return BitrixApiResult(ok=False, method="crm.timeline.comment.add", error=str(exc))


@app.post("/bitrix/analyze-and-comment", response_model=BitrixApiResult)
def bitrix_analyze_and_comment(req: BitrixAnalyzeRequest):
    deal = None
    try:
        client = BitrixClient(req.webhook_url)
        deal = deal_to_context(client.get_deal(req.deal_id))
        result = run_agent(deal)
        comment = format_agent_comment(result)

        if not is_bitrix_write_allowed():
            log_run("bitrix", "dry-run", deal.model_dump(), result.model_dump())
            return BitrixApiResult(
                ok=True, method="crm.deal.get + analyze + crm.timeline.comment.add", dry_run=True,
                message="Write operation skipped because ALLOW_BITRIX_WRITE=false",
                result={"deal_context": deal.model_dump(), "agent_result": result.model_dump()},
                planned_action={"comment": comment}
            )

        bitrix_result = client.add_deal_comment(req.deal_id, comment)
        log_run("bitrix", "success", deal.model_dump(), result.model_dump())
        return BitrixApiResult(
            ok=True,
            method="crm.deal.get + analyze + crm.timeline.comment.add",
            result={
                "deal_context": deal.model_dump(),
                "agent_result": result.model_dump(),
                "bitrix_comment_result": bitrix_result,
            },
        )
    except Exception as exc:
        log_run("bitrix", "error", deal.model_dump() if deal else None, None)
        return BitrixApiResult(ok=False, method="crm.deal.get + analyze + crm.timeline.comment.add", error=str(exc))


@app.post("/bitrix/create-task", response_model=BitrixApiResult)
def bitrix_create_task(req: BitrixTaskRequest):
    if not is_bitrix_write_allowed():
        return BitrixApiResult(ok=True, method="tasks.task.add", dry_run=True, message="Write operation skipped because ALLOW_BITRIX_WRITE=false", planned_action={"title": req.title, "description": req.description})
    try:
        client = BitrixClient(req.webhook_url)
        result = client.create_task_for_deal(
            deal_id=req.deal_id,
            title=req.title,
            description=req.description or "",
            responsible_id=req.responsible_id,
        )
        return BitrixApiResult(ok=True, method="tasks.task.add", result=result)
    except Exception as exc:
        return BitrixApiResult(ok=False, method="tasks.task.add", error=str(exc))


@app.post("/bitrix/execute-action", response_model=BitrixApiResult)
def bitrix_execute_action(req: ActionExecutionRequest):
    if not is_bitrix_write_allowed():
        return BitrixApiResult(ok=True, method="execute-action", dry_run=True, message="Write operation skipped because ALLOW_BITRIX_WRITE=false", planned_action=req.action.model_dump())
    try:
        client = BitrixClient(req.webhook_url)
        action = req.action

        if action.type in ("create_task", "set_followup"):
            result = client.create_task_for_deal(
                deal_id=req.deal_id,
                title=action.title,
                description=action.rationale or "",
            )
            return BitrixApiResult(ok=True, method="tasks.task.add", result=result)

        elif action.type in ("add_comment", "prepare_reply"):
            comment_text = f"[{action.type}] {action.title}\n{action.rationale or ''}"
            result = client.add_deal_comment(req.deal_id, comment_text)
            return BitrixApiResult(ok=True, method="crm.timeline.comment.add", result=result)

        else:
            return BitrixApiResult(ok=False, method="execute-action", error=f"Action type '{action.type}' is not supported yet.")

    except Exception as exc:
        return BitrixApiResult(ok=False, method="execute-action", error=str(exc))


# ---------------------------------------------------------------------------
# Block D — Bitrix24 Outgoing Webhook Handler (event-driven)
# ---------------------------------------------------------------------------


@app.post("/bitrix/outgoing-webhook", response_model=BitrixOutgoingEventResult)
async def bitrix_outgoing_webhook(request: Request):
    """
    Receives events from a Bitrix24 Robot "Outgoing Webhook".

    Setup in Bitrix24:
      Robot → "Outgoing Webhook" → Handler URL:
      https://<public-tunnel>/bitrix/outgoing-webhook?secret=<BITRIX_EVENT_SECRET>

    Safety guarantees:
      - Secret validated before any processing.
      - Idempotency: duplicate events within TTL are skipped.
      - Dry-run: no Bitrix24 writes unless ALLOW_BITRIX_WRITE=true.
      - No secret/token/webhook URL stored in SQLite or logs.
      - draft_reply is NEVER sent to the client automatically.
      - CRM fields are NEVER changed automatically.
    """
    debug = os.getenv("DEBUG_INCOMING_EVENTS", "false").lower() == "true"

    # 1. Parse incoming request
    payload = await parse_bitrix_request(request)
    sanitized = sanitize_payload(payload)

    # 2. Extract fields
    query_params = dict(request.query_params)
    secret = extract_secret(payload, query_params)
    outgoing_token = extract_bitrix_token(payload)
    deal_id = extract_deal_id(payload)
    event_type = extract_event_type(payload)
    stage_id = extract_stage_id(payload)

    if debug:
        # Only log sanitized payload — never the raw one
        import logging
        logging.getLogger("uvicorn").info(
            "[DEBUG_INCOMING_EVENTS] sanitized=%s event_type=%s deal_id=%s stage_id=%s",
            sanitized, event_type, deal_id, stage_id
        )

    # 3. Validate secret
    validate_event_secret(secret)

    # 4. Validate optional outgoing token
    validate_optional_outgoing_token(outgoing_token)

    # 5. deal_id must be present
    if deal_id is None:
        return BitrixOutgoingEventResult(
            ok=False,
            event_type=event_type,
            stage_id=stage_id,
            extracted_payload=sanitized,
            error="Could not extract deal_id from the incoming payload.",
        )

    # 6. Idempotency check
    ttl = int(os.getenv("EVENT_IDEMPOTENCY_TTL_SECONDS", "600"))
    event_key = build_event_key(deal_id, event_type, stage_id)

    if is_recent_event_processed(event_key, ttl):
        return BitrixOutgoingEventResult(
            ok=True,
            skipped=True,
            reason="recently_processed",
            event_key=event_key,
            deal_id=deal_id,
            event_type=event_type,
            stage_id=stage_id,
        )

    # 7. Read deal from Bitrix24
    deal = None
    try:
        client = BitrixClient()  # uses BITRIX_WEBHOOK_URL from env
        raw_deal = client.get_deal(deal_id)
        deal = deal_to_context(raw_deal)
    except Exception as exc:
        log_run("bitrix-outgoing-webhook", "error", None, None)
        return BitrixOutgoingEventResult(
            ok=False,
            event_key=event_key,
            deal_id=deal_id,
            event_type=event_type,
            stage_id=stage_id,
            error=f"Failed to read deal from Bitrix24: {type(exc).__name__}: {exc}",
        )

    # 8. Run agent
    try:
        result = run_agent(deal)
        log_run("bitrix-outgoing-webhook", "success", deal.model_dump(), result.model_dump())
    except Exception as exc:
        log_run("bitrix-outgoing-webhook", "error", deal.model_dump(), None)
        return BitrixOutgoingEventResult(
            ok=False,
            event_key=event_key,
            deal_id=deal_id,
            event_type=event_type,
            stage_id=stage_id,
            error=f"Agent analysis failed: {type(exc).__name__}: {exc}",
        )

    # 9. Format comment
    comment = format_agent_comment(result)

    # 10. Dry-run or live write
    if not is_bitrix_write_allowed():
        mark_event_processed(event_key, deal_id, event_type, stage_id, "dry-run")
        return BitrixOutgoingEventResult(
            ok=True,
            dry_run=True,
            event_key=event_key,
            deal_id=deal_id,
            event_type=event_type,
            stage_id=stage_id,
            agent_result=result.model_dump(),
            planned_action={"comment": comment},
        )

    # Live mode: write comment to Bitrix24 timeline
    try:
        bitrix_result = client.add_deal_comment(deal_id, comment)
        mark_event_processed(event_key, deal_id, event_type, stage_id, "success")
        return BitrixOutgoingEventResult(
            ok=True,
            dry_run=False,
            event_key=event_key,
            deal_id=deal_id,
            event_type=event_type,
            stage_id=stage_id,
            agent_result=result.model_dump(),
            bitrix_result=bitrix_result,
        )
    except Exception as exc:
        mark_event_processed(event_key, deal_id, event_type, stage_id, "error")
        return BitrixOutgoingEventResult(
            ok=False,
            event_key=event_key,
            deal_id=deal_id,
            event_type=event_type,
            stage_id=stage_id,
            agent_result=result.model_dump(),
            error=f"Failed to write comment to Bitrix24: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Audit log endpoints
# ---------------------------------------------------------------------------


@app.get("/runs")
def get_runs():
    return get_recent_runs(20)


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    run = get_run_details(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ---------------------------------------------------------------------------
# Model endpoints
# ---------------------------------------------------------------------------


@app.get("/model/status")
def model_status():
    return {
        "current_model": CURRENT_MODEL,
        "fast_mode": FAST_MODE,
        "base_url": OLLAMA_BASE_URL,
        "use_ollama": os.getenv("USE_OLLAMA", "true").lower() == "true",
        "think": OLLAMA_THINK,
        "num_ctx": OLLAMA_NUM_CTX,
        "num_predict": OLLAMA_NUM_PREDICT,
    }


@app.get("/model/benchmark")
def model_benchmark():
    with open(DEMO_DEAL_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    deal = DealContext.model_validate(data)

    start_time = time.time()
    try:
        result = run_agent(deal)
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "model_used": result.model_used,
            "duration_ms": duration_ms,
            "risk_level": result.risk_level
        }
    except Exception as exc:
        return {"error": str(exc)}
