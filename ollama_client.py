import json
import os
import re
from typing import Any

import requests
from dotenv import load_dotenv

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
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "700"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0"))


def _extract_json(text: str) -> dict:
    """
    Try to extract a JSON object from a model response.

    Some local models return markdown, thinking traces, or explanatory text
    around JSON. This helper keeps the strict path working when possible.
    """
    text = (text or "").strip()

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    # Remove common markdown fences if present.
    text = text.replace("```json", "").replace("```", "").strip()

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("No JSON object found in model response")

    return json.loads(match.group(0))


def _shorten(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _result_from_ollama_text(deal: DealContext, content: str) -> AgentResult:
    """
    MVP safety fallback for local LLM responses.

    If Ollama is reachable but returns non-strict JSON, we still want the demo
    to show that the local LLM was called. We normalize its text response into
    the AgentResult schema instead of falling back to deterministic_agent.
    """
    text = (content or "").strip()
    if not text:
        text = "Ollama returned an empty response, but the local LLM call was executed."

    missing_fields = []
    if not deal.budget:
        missing_fields.append("budget")
    if not deal.decision_maker:
        missing_fields.append("decision_maker")
    if not deal.deadline:
        missing_fields.append("deadline")
    if not deal.next_action:
        missing_fields.append("next_action")

    deal_text = f"{deal.title} {deal.client_message}".lower()

    risk_level = "medium"
    priority = "medium"

    if any(word in deal_text for word in ["срочно", "горит", "urgent", "asap", "жалоба", "проблема", "критично"]):
        risk_level = "high"
        priority = "high"
    elif missing_fields:
        risk_level = "medium"
        priority = "high"

    case_type = "CRM deal analysis"
    if "1с" in deal_text or "1c" in deal_text:
        case_type = "CRM implementation + 1C integration"
    elif "crm" in deal_text or "битрикс" in deal_text or "bitrix" in deal_text:
        case_type = "CRM implementation"

    ollama_summary = _shorten(text, 1200)

    return AgentResult(
        case_type=case_type,
        priority=priority,
        risk_level=risk_level,
        risk_explanation=(
            "Ollama local LLM was called successfully, but returned non-strict JSON. "
            "For MVP demo, the answer was normalized into AgentResult. "
            "Ollama summary: " + ollama_summary
        ),
        missing_fields=missing_fields,
        recommended_actions=[
            {
                "type": "set_followup",
                "title": "Уточнить недостающие данные по сделке",
                "owner": deal.responsible or "Менеджер",
                "priority": "high" if missing_fields else "medium",
                "rationale": (
                    "Агент обнаружил неполный CRM-контекст и предлагает следующий шаг "
                    "для квалификации сделки."
                ),
            },
            {
                "type": "add_comment",
                "title": "Зафиксировать AI-анализ в таймлайне сделки",
                "owner": deal.responsible or "Менеджер",
                "priority": "medium",
                "rationale": (
                    "Результат анализа должен быть виден в CRM для контроля и аудита."
                ),
            },
        ],
        draft_reply=(
            "Добрый день! Спасибо за обращение. Чтобы корректно оценить задачу и предложить следующий шаг, "
            "уточните, пожалуйста, недостающие детали по проекту: бюджет, сроки, ответственное лицо, "
            "текущую систему и критерии успеха."
        ),
        human_approval_required=True,
        model_used=f"ollama:{CURRENT_MODEL}:text-fallback",
    )


def _normalize_parsed_json(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Make model JSON more tolerant:
    - fill missing fields;
    - normalize enum-like values;
    - make sure required arrays exist.
    """
    parsed = dict(parsed or {})

    parsed.setdefault("case_type", "CRM deal analysis")
    parsed.setdefault("priority", "medium")
    parsed.setdefault("risk_level", "medium")
    parsed.setdefault("risk_explanation", "Local Ollama analysis completed.")
    parsed.setdefault("missing_fields", [])
    parsed.setdefault("recommended_actions", [])
    parsed.setdefault("draft_reply", "Добрый день! Спасибо за обращение. Уточните, пожалуйста, детали проекта.")
    parsed.setdefault("human_approval_required", True)

    if parsed["priority"] not in ("low", "medium", "high"):
        parsed["priority"] = "medium"

    if parsed["risk_level"] not in ("low", "medium", "high"):
        parsed["risk_level"] = "medium"

    if not isinstance(parsed["missing_fields"], list):
        parsed["missing_fields"] = []

    if not isinstance(parsed["recommended_actions"], list):
        parsed["recommended_actions"] = []

    # Filter/repair actions so Pydantic does not reject the whole response.
    allowed_types = {
        "create_task",
        "prepare_reply",
        "update_crm_field",
        "add_comment",
        "set_followup",
        "request_human_approval",
    }

    repaired_actions = []
    for action in parsed["recommended_actions"]:
        if not isinstance(action, dict):
            continue

        action_type = action.get("type") or "request_human_approval"
        if action_type not in allowed_types:
            action_type = "request_human_approval"

        priority = action.get("priority") or "medium"
        if priority not in ("low", "medium", "high"):
            priority = "medium"

        repaired_actions.append(
            {
                "type": action_type,
                "title": action.get("title") or "Проверить сделку менеджером",
                "owner": action.get("owner"),
                "priority": priority,
                "rationale": action.get("rationale") or "Требуется human approval.",
            }
        )

    if not repaired_actions:
        repaired_actions = [
            {
                "type": "request_human_approval",
                "title": "Проверить AI-анализ менеджером",
                "owner": None,
                "priority": "medium",
                "rationale": "Модель вернула неполный список действий, поэтому требуется ручная проверка.",
            }
        ]

    parsed["recommended_actions"] = repaired_actions
    return parsed


def analyze_with_ollama(deal: DealContext) -> AgentResult:
    system_prompt = SYSTEM_PROMPT

    if FAST_MODE:
        system_prompt = (
            "Ты быстрый CRM-анализатор. Верни только валидный JSON без markdown и без рассуждений.\n\n"
            + SYSTEM_PROMPT
        )

    user_content = (
        "Проанализируй CRM-сделку и верни только JSON по схеме AgentResult.\n"
        "Без markdown. Без ```json. Без пояснений вне JSON. /nothink\n\n"
        "CRM deal:\n"
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
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }

    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()
    content = data.get("message", {}).get("content", "")

    try:
        parsed = _extract_json(content)
        parsed = _normalize_parsed_json(parsed)
        parsed["model_used"] = f"ollama:{CURRENT_MODEL}"
        return AgentResult.model_validate(parsed)
    except Exception:
        return _result_from_ollama_text(deal, content)