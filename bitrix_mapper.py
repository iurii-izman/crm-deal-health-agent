from typing import Any, Dict, Optional
from schemas import DealContext, AgentResult


def _non_empty(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    if not value or value in {"0", "0.00", "None", "null"}:
        return None
    return value


def summarize_custom_fields(cf: Dict[str, Any]) -> str:
    if not cf:
        return ""
    parts = []
    for k, v in cf.items():
        val = _non_empty(v)
        if val:
            parts.append(f"{k}: {val}")
    return "Custom Fields: " + "; ".join(parts) if parts else ""


def deal_to_context(deal: Dict[str, Any]) -> DealContext:
    deal_id = int(deal.get("ID") or deal.get("id"))
    title = _non_empty(deal.get("TITLE")) or f"Deal {deal_id}"

    opportunity = _non_empty(deal.get("OPPORTUNITY"))
    currency = _non_empty(deal.get("CURRENCY_ID"))
    budget = f"{opportunity} {currency}".strip() if opportunity else None

    contact_id = _non_empty(deal.get("CONTACT_ID"))
    company_id = _non_empty(deal.get("COMPANY_ID"))
    decision_maker = f"Contact ID {contact_id}" if contact_id else (f"Company ID {company_id}" if company_id else None)

    raw_comments = _non_empty(deal.get("COMMENTS"))
    comments = [raw_comments] if raw_comments else []

    custom_fields = {k: v for k, v in deal.items() if k.startswith("UF_")}
    cf_summary = summarize_custom_fields(custom_fields)

    client_message_parts = [
        title,
        raw_comments,
        _non_empty(deal.get("SOURCE_DESCRIPTION")),
        _non_empty(deal.get("ADDITIONAL_INFO")),
        budget,
        _non_empty(deal.get("STAGE_ID")),
        cf_summary
    ]
    client_message = ". ".join([p for p in client_message_parts if p]) or title

    responsible = _non_empty(deal.get("ASSIGNED_BY_ID"))
    if responsible:
        responsible = f"User ID {responsible}"

    return DealContext(
        deal_id=deal_id,
        title=title,
        stage=_non_empty(deal.get("STAGE_ID")) or "unknown",
        source=_non_empty(deal.get("SOURCE_ID")),
        client_name=_non_empty(deal.get("COMPANY_TITLE")) or _non_empty(deal.get("CONTACT_FULL_NAME")),
        client_message=client_message,
        budget=budget,
        decision_maker=decision_maker,
        deadline=_non_empty(deal.get("CLOSEDATE")),
        next_action=None,
        responsible=responsible,
        tasks=[],
        comments=comments,
        raw_stage_id=_non_empty(deal.get("STAGE_ID")),
        raw_category_id=_non_empty(deal.get("CATEGORY_ID")),
        assigned_by_id=_non_empty(deal.get("ASSIGNED_BY_ID")),
        contact_id=contact_id,
        company_id=company_id,
        opportunity=opportunity,
        currency=currency,
        created_at=_non_empty(deal.get("DATE_CREATE")),
        updated_at=_non_empty(deal.get("DATE_MODIFY")),
        closedate=_non_empty(deal.get("CLOSEDATE")),
        custom_fields=custom_fields,
    )


def format_agent_comment(result: AgentResult) -> str:
    actions = "\n".join([
        f"- [{action.priority}] {action.title}" + (f" — {action.rationale}" if action.rationale else "")
        for action in result.recommended_actions
    ])

    missing = ", ".join(result.missing_fields) if result.missing_fields else "нет критичных пробелов"

    return (
        "🤖 CRM Deal Health Agent\n\n"
        f"Тип кейса: {result.case_type}\n"
        f"Приоритет: {result.priority}\n"
        f"Риск: {result.risk_level}\n"
        f"Пояснение риска: {result.risk_explanation}\n\n"
        f"Недостающие поля: {missing}\n\n"
        "Рекомендованные действия:\n"
        f"{actions}\n\n"
        "Черновик ответа клиенту:\n"
        f"{result.draft_reply}\n\n"
        f"Human approval required: {result.human_approval_required}\n"
        f"Model: {result.model_used}"
    )
