# Smoke Tests (QA)

This file contains manual commands to quickly verify that the application and its endpoints are working correctly.

## Compile Check

```powershell
python -m py_compile app.py schemas.py storage.py bitrix_client.py bitrix_mapper.py bitrix_event_parser.py ollama_client.py deterministic_agent.py
```

## Prerequisites
1. Start the backend:
   ```powershell
   uvicorn app:app --reload --host 127.0.0.1 --port 8000
   ```
2. Make sure you have `.env` properly configured.

---

## Bitrix24 Outgoing Webhook Handler

**Health:**
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

**Dry-run outgoing webhook:**
```powershell
Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8000/bitrix/outgoing-webhook?secret=dev-local-123&force=true" `
  -ContentType "application/json" `
  -Body '{"event":"ONCRMDEALUPDATE","data":{"FIELDS":{"ID":123,"STAGE_ID":"NEW"}},"write_comment":true}'
```

**Idempotency:**
- Run the same request without `force=true`.
- Run it again without `force=true`.
- Expect `skipped=true` on the second call.

**Wrong secret:**
```powershell
Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8000/bitrix/outgoing-webhook?secret=wrong" `
  -ContentType "application/json" `
  -Body '{"event":"ONCRMDEALUPDATE","data":{"FIELDS":{"ID":123}}}'
```
Expect HTTP 403.

**Form-urlencoded payload:**
```powershell
Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8000/bitrix/outgoing-webhook?secret=dev-local-123&force=true" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body "event=ONCRMDEALUPDATE&data%5BFIELDS%5D%5BID%5D=123&data%5BFIELDS%5D%5BSTAGE_ID%5D=NEW"
```

**Cloudflared tunnel:**
```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Then (replace placeholder with generated tunnel domain):
```powershell
Invoke-RestMethod `
  -Method POST `
  -Uri "https://<generated>.trycloudflare.com/bitrix/outgoing-webhook?secret=dev-local-123&force=true" `
  -ContentType "application/json" `
  -Body '{"event":"ONCRMDEALUPDATE","data":{"FIELDS":{"ID":123,"STAGE_ID":"NEW"}}}'
```

Do not use real webhook URLs in this file.

---

## 1. Basic Endpoints

**Check UI loading:**
```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing | Select-Object StatusCode, StatusDescription
```

**Check Health:**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

**Get Demo Deal:**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo-deal"
```

## 2. Agent & Ollama Endpoints

**Model Status:**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/model/status"
```

**Run Benchmark:**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/model/benchmark"
```

**Analyze Demo Deal (GET alias):**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/analyze-demo"
```

**Analyze Custom Deal (POST):**
```powershell
$body = @{
    deal_id = 999
    title = "Test Deal"
    stage = "NEW"
    client_message = "Test message"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/analyze" -Method Post -Body $body -ContentType "application/json"
```

## 3. Bitrix24 Endpoints (Dry-Run Mode recommended)

*(Assuming `ALLOW_BITRIX_WRITE=false` in `.env`)*

**Test Webhook Connection (with fake webhook):**
```powershell
$bxBody = @{ webhook_url = "https://example.com/rest/1/fake/" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/bitrix/test" -Method Post -Body $bxBody -ContentType "application/json"
```
*(Should return ok=False due to fake webhook)*

**Execute Action (Dry-Run Simulation):**
```powershell
$actionBody = @{
    webhook_url = "https://example.com/rest/1/fake/"
    deal_id = 1
    action = @{
        type = "create_task"
        title = "Call client"
        priority = "high"
        rationale = "Important"
    }
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/bitrix/execute-action" -Method Post -Body $actionBody -ContentType "application/json"
```
*(Should return ok=True, dry_run=True without calling the fake webhook)*

## 4. History / DB Endpoints

**Get recent runs:**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/runs"
```

**Get specific run (replace 1 with actual ID):**
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/runs/1"
```
