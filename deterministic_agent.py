from schemas import DealContext, AgentResult, RecommendedAction


def analyze_deterministic(deal: DealContext) -> AgentResult:
    missing = []
    if not deal.budget:
        missing.append("budget")
    if not deal.decision_maker:
        missing.append("decision_maker")
    if not deal.next_action:
        missing.append("next_action")
    if not deal.deadline:
        missing.append("deadline")

    text = f"{deal.title} {deal.client_message}".lower()
    has_1c = "1с" in text or "1c" in text
    has_crm = "crm" in text or "битрикс" in text or "битрикс24" in text

    case_parts = []
    if has_crm:
        case_parts.append("CRM implementation")
    if has_1c:
        case_parts.append("1C integration")
    if "задач" in text or "контроль" in text:
        case_parts.append("task control")
    case_type = " + ".join(case_parts) if case_parts else "business automation"

    risk_level = "high" if len(missing) >= 3 else "medium" if missing else "low"
    priority = "high" if has_1c or has_crm else "medium"

    actions = [
        RecommendedAction(
            type="create_task",
            title="Уточнить ЛПР, бюджет, сроки и критерии успеха проекта",
            owner=deal.responsible or "Менеджер продаж",
            priority="high",
            rationale="Без квалификации сделки высок риск зависания и потери клиента."
        ),
        RecommendedAction(
            type="set_followup",
            title="Поставить follow-up на завтра",
            owner=deal.responsible or "Менеджер продаж",
            priority="high",
            rationale="В сделке отсутствует следующий шаг."
        ),
        RecommendedAction(
            type="prepare_reply",
            title="Подготовить клиенту квалифицирующий ответ",
            owner=deal.responsible or "Менеджер продаж",
            priority="medium",
            rationale="Нужно быстро показать клиенту, что запрос понят и структурирован."
        ),
    ]

    if has_1c:
        actions.append(
            RecommendedAction(
                type="create_task",
                title="Провести мини-аудит текущей 1С и сценариев обмена с CRM",
                owner="Аналитик / интегратор",
                priority="high",
                rationale="Интеграция с 1С влияет на сроки, риски и архитектуру внедрения."
            )
        )

    draft = (
        "Добрый день! Спасибо за обращение. Мы видим задачу как внедрение CRM с настройкой "
        "воронок, контроля задач и интеграции с 1С. Чтобы предложить корректную схему, "
        "уточните, пожалуйста: кто принимает решение по проекту, какая конфигурация 1С используется, "
        "какие процессы сейчас ведутся в Excel/чатах, какие сроки для вас критичны и есть ли ориентир по бюджету?"
    )

    return AgentResult(
        case_type=case_type,
        priority=priority,
        risk_level=risk_level,
        risk_explanation=(
            "Высокий риск зависания сделки: не зафиксированы ключевые параметры квалификации "
            "и отсутствует следующий шаг." if risk_level == "high"
            else "Риск умеренный: часть данных требует уточнения."
        ),
        missing_fields=missing,
        recommended_actions=actions,
        draft_reply=draft,
        human_approval_required=True,
        model_used="deterministic-fallback"
    )
