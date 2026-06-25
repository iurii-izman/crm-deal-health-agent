from typing import List, Optional, Literal, Any, Dict
from pydantic import BaseModel, Field


class DealContext(BaseModel):
    deal_id: int
    title: str
    stage: str
    source: Optional[str] = None
    client_name: Optional[str] = None
    client_message: str
    budget: Optional[str] = None
    decision_maker: Optional[str] = None
    deadline: Optional[str] = None
    next_action: Optional[str] = None
    responsible: Optional[str] = None
    tasks: List[str] = Field(default_factory=list)
    comments: List[str] = Field(default_factory=list)

    # Expanded context
    raw_stage_id: Optional[str] = None
    raw_category_id: Optional[str] = None
    assigned_by_id: Optional[str] = None
    contact_id: Optional[str] = None
    company_id: Optional[str] = None
    opportunity: Optional[str] = None
    currency: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closedate: Optional[str] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class RecommendedAction(BaseModel):
    type: Literal["create_task", "prepare_reply", "update_crm_field", "add_comment", "set_followup", "request_human_approval"]
    title: str
    owner: Optional[str] = None
    priority: Literal["low", "medium", "high"] = "medium"
    rationale: Optional[str] = None


class AgentResult(BaseModel):
    case_type: str
    priority: Literal["low", "medium", "high"]
    risk_level: Literal["low", "medium", "high"]
    risk_explanation: str
    missing_fields: List[str]
    recommended_actions: List[RecommendedAction]
    draft_reply: str
    human_approval_required: bool = True
    model_used: str


class BitrixRequest(BaseModel):
    webhook_url: Optional[str] = None


class BitrixDealRequest(BitrixRequest):
    deal_id: int


class BitrixAnalyzeRequest(BitrixDealRequest):
    write_comment: bool = False


class BitrixCommentRequest(BitrixDealRequest):
    comment: str


class BitrixTaskRequest(BitrixDealRequest):
    title: str
    description: Optional[str] = None
    responsible_id: Optional[int] = None


class ActionExecutionRequest(BitrixDealRequest):
    action: RecommendedAction


class BitrixApiResult(BaseModel):
    ok: bool
    method: str
    result: Any = None
    error: Optional[str] = None
    dry_run: bool = False
    message: Optional[str] = None
    planned_action: Any = None
