import os
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

load_dotenv()


class BitrixClient:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = (webhook_url or os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
        if not self.webhook_url:
            raise ValueError("Bitrix webhook URL is empty. Pass webhook_url or set BITRIX_WEBHOOK_URL in .env")
        self.webhook_url = self.webhook_url.rstrip("/") + "/"

    def endpoint(self, method: str) -> str:
        method = method.strip().strip("/")
        if not method.endswith(".json"):
            method = method + ".json"
        return urljoin(self.webhook_url, method)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = requests.post(
            self.endpoint(method),
            json=params or {},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            description = data.get("error_description") or data.get("error")
            raise RuntimeError(f"Bitrix API error in {method}: {description}")
        return data.get("result")

    def test(self) -> Any:
        return self.call("profile", {})

    def get_deal(self, deal_id: int) -> Dict[str, Any]:
        result = self.call("crm.deal.get", {"id": deal_id})
        if not isinstance(result, dict):
            raise RuntimeError(f"crm.deal.get returned unexpected result: {result}")
        return result

    def add_deal_comment(self, deal_id: int, comment: str) -> Any:
        return self.call(
            "crm.timeline.comment.add",
            {"fields": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal", "COMMENT": comment}},
        )

    def create_task_for_deal(
        self,
        deal_id: int,
        title: str,
        description: str,
        responsible_id: Optional[int] = None,
    ) -> Any:
        responsible_id = responsible_id or int(os.getenv("BITRIX_RESPONSIBLE_ID", "1"))
        return self.call(
            "tasks.task.add",
            {
                "fields": {
                    "TITLE": title,
                    "DESCRIPTION": description,
                    "RESPONSIBLE_ID": responsible_id,
                    "UF_CRM_TASK": [f"D_{deal_id}"],
                }
            },
        )
