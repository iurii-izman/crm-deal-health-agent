"""
bitrix_event_parser.py — Robust parser for incoming Bitrix24 outgoing webhook events.

Handles all known payload formats:
  - JSON body
  - application/x-www-form-urlencoded
  - Query params
  - Raw body fallback

Never logs or stores secrets, tokens, or webhook URLs.
"""

import re
from typing import Any, Dict, Optional

from fastapi import Request

# ---------------------------------------------------------------------------
# Sensitive field names to sanitize
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS = re.compile(
    r"(token|secret|auth|webhook)",
    re.IGNORECASE,
)

_SENSITIVE_EXACT = {
    "secret",
    "BITRIX_EVENT_SECRET",
    "auth",
    "application_token",
    "access_token",
    "refresh_token",
    "webhook_url",
}


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------


async def parse_bitrix_request(request: Request) -> dict:
    """
    Parse an incoming Bitrix24 outgoing-webhook request into a flat dict.

    Priority:
      1. JSON body
      2. application/x-www-form-urlencoded
      3. Query params (merged last, lower priority)
      4. Raw body stored under '_raw_body' as fallback hint
    """
    payload: dict = {}

    # Always collect query params
    query_params = dict(request.query_params)

    content_type = request.headers.get("content-type", "")

    try:
        if "application/json" in content_type:
            body = await request.body()
            if body:
                import json
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    payload = {"_raw_json": payload}
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            payload = dict(form)
        else:
            # Try JSON first, then form
            body = await request.body()
            if body:
                try:
                    import json
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        payload = parsed
                    else:
                        payload = {"_raw_json": parsed}
                except Exception:
                    # Try form-encoded
                    try:
                        from urllib.parse import parse_qs
                        parsed_form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
                        payload = {k: v[0] if len(v) == 1 else v for k, v in parsed_form.items()}
                    except Exception:
                        payload["_raw_body"] = body.decode("utf-8", errors="replace")[:500]

    except Exception as exc:
        payload["_parse_error"] = str(exc)

    # Merge query params (payload values take precedence for overlapping keys)
    merged = {**query_params, **payload}
    return merged


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------


def _get_nested(payload: dict, *keys: str) -> Optional[Any]:
    """Navigate nested dict by a sequence of keys."""
    node: Any = payload
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def extract_deal_id(payload: dict) -> Optional[int]:
    """
    Extract deal ID from a Bitrix24 payload supporting all known formats:

    - deal_id / DEAL_ID
    - id / ID
    - data[FIELDS][ID] (flat key from form-encoded)
    - FIELDS[ID] (flat key)
    - data.FIELDS.ID (nested JSON: {"data": {"FIELDS": {"ID": 123}}})
    - document_id / DOCUMENT_ID
    - String patterns: "DEAL_123", "D_123", "CCrmDocumentDeal:123"
    """
    # Direct scalar keys (case variations)
    for key in ("deal_id", "DEAL_ID", "id", "ID", "document_id", "DOCUMENT_ID"):
        val = payload.get(key)
        if val is not None:
            result = _parse_id_value(val)
            if result is not None:
                return result

    # Nested JSON: {"data": {"FIELDS": {"ID": ...}}}
    nested = _get_nested(payload, "data", "FIELDS", "ID")
    if nested is not None:
        result = _parse_id_value(nested)
        if result is not None:
            return result

    # Nested JSON: {"FIELDS": {"ID": ...}}
    nested = _get_nested(payload, "FIELDS", "ID")
    if nested is not None:
        result = _parse_id_value(nested)
        if result is not None:
            return result

    # Flat form-encoded keys: "data[FIELDS][ID]" or "FIELDS[ID]"
    for key in ("data[FIELDS][ID]", "FIELDS[ID]"):
        val = payload.get(key)
        if val is not None:
            result = _parse_id_value(val)
            if result is not None:
                return result

    # Scan all values for string patterns like "DEAL_123", "D_123", "CCrmDocumentDeal:123"
    for key, val in payload.items():
        if isinstance(val, str):
            result = _parse_id_from_string(val)
            if result is not None:
                return result

    return None


def _parse_id_value(val: Any) -> Optional[int]:
    """Convert a raw value to int deal ID."""
    if val is None:
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    if isinstance(val, float):
        return int(val) if val > 0 else None
    if isinstance(val, str):
        val = val.strip()
        if val.isdigit():
            n = int(val)
            return n if n > 0 else None
        return _parse_id_from_string(val)
    return None


def _parse_id_from_string(s: str) -> Optional[int]:
    """
    Extract numeric ID from patterns like:
      - "DEAL_123"
      - "D_123"
      - "CCrmDocumentDeal:123"
    """
    s = s.strip()
    # CCrmDocumentDeal:123
    m = re.match(r"CCrmDocument(?:Deal)?[:\-](\d+)$", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # DEAL_123 or D_123
    m = re.match(r"(?:DEAL|D)[_\-](\d+)$", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def extract_event_type(payload: dict) -> str:
    """Extract event type from payload, default to 'bitrix_outgoing_webhook'."""
    for key in ("event", "EVENT", "event_type", "EVENT_TYPE"):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val.strip()
    return "bitrix_outgoing_webhook"


def extract_stage_id(payload: dict) -> Optional[str]:
    """
    Extract stage/pipeline ID supporting:
    - stage_id / STAGE_ID
    - data[FIELDS][STAGE_ID] (flat form-encoded)
    - FIELDS[STAGE_ID] (flat form-encoded)
    - {"data": {"FIELDS": {"STAGE_ID": "NEW"}}} (nested JSON)
    """
    for key in ("stage_id", "STAGE_ID"):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val.strip()

    nested = _get_nested(payload, "data", "FIELDS", "STAGE_ID")
    if nested and isinstance(nested, str):
        return nested.strip()

    nested = _get_nested(payload, "FIELDS", "STAGE_ID")
    if nested and isinstance(nested, str):
        return nested.strip()

    for key in ("data[FIELDS][STAGE_ID]", "FIELDS[STAGE_ID]"):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val.strip()

    return None


def extract_secret(payload: dict, query_params: dict) -> Optional[str]:
    """
    Extract the handler secret. Query params take priority over body
    (Bitrix24 puts ?secret=... in the handler URL).
    """
    # Query param first (highest priority)
    val = query_params.get("secret") or payload.get("secret")
    if val and isinstance(val, str):
        return val.strip()
    return None


def extract_bitrix_token(payload: dict) -> Optional[str]:
    """Extract optional Bitrix24 outgoing token from payload."""
    for key in ("auth[application_token]", "application_token", "outgoing_token"):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val.strip()

    # Nested: {"auth": {"application_token": "..."}}
    nested = _get_nested(payload, "auth", "application_token")
    if nested and isinstance(nested, str):
        return nested.strip()

    return None


# ---------------------------------------------------------------------------
# Sanitization — never log secrets
# ---------------------------------------------------------------------------


def sanitize_payload(payload: dict) -> dict:
    """
    Return a copy of payload with all sensitive fields removed or masked.
    Sensitive = keys matching token/secret/auth/webhook patterns,
    or the explicitly listed field names.
    """
    result: dict = {}
    for key, value in payload.items():
        if _is_sensitive_key(key):
            result[key] = "***"
        elif isinstance(value, dict):
            result[key] = sanitize_payload(value)
        else:
            result[key] = value
    return result


def _is_sensitive_key(key: str) -> bool:
    if key in _SENSITIVE_EXACT:
        return True
    if _SENSITIVE_PATTERNS.search(key):
        return True
    return False
