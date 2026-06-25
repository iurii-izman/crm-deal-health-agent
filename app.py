import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from schemas import (
    DealContext, AgentResult, BitrixRequest, BitrixDealRequest, BitrixAnalyzeRequest,
    BitrixCommentRequest, BitrixTaskRequest, BitrixApiResult, ActionExecutionRequest,
)
from deterministic_agent import analyze_deterministic
from ollama_client import analyze_with_ollama, CURRENT_MODEL, FAST_MODE, OLLAMA_BASE_URL, OLLAMA_THINK, OLLAMA_NUM_CTX, OLLAMA_NUM_PREDICT
from bitrix_client import BitrixClient
from bitrix_mapper import deal_to_context, format_agent_comment
from storage import init_db, log_run, get_recent_runs, get_run_details

load_dotenv()

app = FastAPI(title="BOS CRM Deal Health Agent", version="0.3.0")
init_db()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEMO_DEAL_PATH = BASE_DIR / "demo_deal.json"

APP_MODE = os.getenv("APP_MODE", "demo").lower()
ALLOW_BITRIX_WRITE = os.getenv("ALLOW_BITRIX_WRITE", "false").lower() == "true"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "use_ollama": os.getenv("USE_OLLAMA", "true").lower() == "true",
        "model": os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        "think": os.getenv("OLLAMA_THINK", "false").lower() == "true",
        "bitrix_configured": bool(os.getenv("BITRIX_WEBHOOK_URL", "").strip()),
        "app_mode": APP_MODE,
        "allow_bitrix_write": ALLOW_BITRIX_WRITE,
    }


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
    if not ALLOW_BITRIX_WRITE:
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
        
        if not ALLOW_BITRIX_WRITE:
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
    if not ALLOW_BITRIX_WRITE:
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
    if not ALLOW_BITRIX_WRITE:
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


@app.get("/runs")
def get_runs():
    return get_recent_runs(20)


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    run = get_run_details(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


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


