import json
import os
import re
import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from prompt import SYSTEM_PROMPT
from schemas import DealContext, AgentResult

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
FAST_MODE = os.getenv("FAST_MODE", "true").lower() == "true"
OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen3:1.7b")
OLLAMA_QUALITY_MODEL = os.getenv("OLLAMA_QUALITY_MODEL", OLLAMA_MODEL)

CURRENT_MODEL = OLLAMA_FAST_MODEL if FAST_MODE else OLLAMA_QUALITY_MODEL

OLLAMA_THINK = os.getenv("OLLAMA_THINK", "false").lower() == "true"
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "300" if FAST_MODE else "700"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))


def analyze_with_ollama(deal: DealContext) -> AgentResult:
    system_prompt = SYSTEM_PROMPT
    if FAST_MODE:
        system_prompt = "Ты быстрый CRM анализатор. " + SYSTEM_PROMPT.replace("Проанализировать", "Быстро проанализировать")

    user_content = (
        "Проанализируй CRM-сделку и верни только JSON по схеме.\n"
        "Не используй длинные рассуждения. /nothink\n\n"
        + deal.model_dump_json(indent=2)
    )

    payload = {
        "model": CURRENT_MODEL,
        "stream": False,
        "format": "json",
        "think": OLLAMA_THINK,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_NUM_PREDICT
        }
    }

    response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    content = data.get("message", {}).get("content", "")
    parsed = _extract_json(content)
    parsed["model_used"] = f"ollama:{CURRENT_MODEL}"

    try:
        return AgentResult.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"Ollama response does not match AgentResult schema: {exc}") from exc
