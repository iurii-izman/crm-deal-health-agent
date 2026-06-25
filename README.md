# BOS CRM Deal Health Agent

## 1. О проекте
**BOS CRM Deal Health Agent** — это локальный AI-агент, созданный для контроля качества сделок в CRM. Агент автоматически читает контекст сделок, выявляет бизнес-риски, подсвечивает недостающие данные и предлагает конкретные дальнейшие шаги. 
Главная особенность проекта — парадигма **"Human-in-the-loop"**: агент не выполняет критических действий с клиентской базой самостоятельно, он лишь формирует рекомендации, которые требуют подтверждения человеком (Human Approval). Продукт разработан с упором на 100% приватность данных (Local LLM).

## 2. Архитектура
Пайплайн взаимодействия агента и CRM выглядит следующим образом:
`Bitrix24 (CRM) → FastAPI (Backend) → Ollama / Deterministic Fallback (AI/Logic) → AgentResult (JSON) → Human Approval (UI) → Bitrix24 (Timeline Comment / Tasks)`

## 3. Быстрый запуск на Windows
Проект написан на Python и не требует сложных зависимостей (используется FastAPI, Pydantic, requests). Никакие внешние платные API или npm-пакеты не нужны.
1. Установите Python 3.10+
2. Склонируйте репозиторий и перейдите в папку:
   ```cmd
   cd crm-deal-health-agent
   ```
3. Создайте и активируйте виртуальное окружение:
   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   ```
4. Установите зависимости:
   ```cmd
   pip install -r requirements.txt
   ```
5. Запустите локальный сервер:
   ```cmd
   uvicorn app:app --reload
   ```
6. Откройте UI в браузере: `http://127.0.0.1:8000`

## 4. Настройка `.env`
Скопируйте файл конфигурации:
```cmd
copy .env.example .env
```
Затем отредактируйте `.env` в зависимости от нужного вам режима работы.

## 5. Как включить/выключить Ollama
В `.env` файле есть флаг `USE_OLLAMA`.
- `USE_OLLAMA=true`: бэкенд будет обращаться к вашей локальной нейросети Ollama (на `http://localhost:11434`).
- `USE_OLLAMA=false`: бэкенд пропустит шаг с Ollama и мгновенно вернет результат с помощью детерминированного Fallback-алгоритма (`deterministic_agent.py`). Это полезно для тестов, если Ollama зависла или нет ресурсов GPU.

## 6. Как включить dry-run / live mode
В `.env` присутствуют настройки безопасного режима:
- `APP_MODE=demo`
- `ALLOW_BITRIX_WRITE=false` (По умолчанию отключено)

Когда `ALLOW_BITRIX_WRITE=false`, приложение находится в безопасном **Dry-Run** режиме. Кнопки в UI выполняют симуляцию действий (например, возвращают "сухой" ответ) и не отправляют POST-запросы записи в API Битрикса.
Чтобы включить боевой режим, установите `ALLOW_BITRIX_WRITE=true` и перезапустите сервер. Интерфейс переключится в красный **LIVE MODE**.

## 7. Как подключить Bitrix24 Webhook
1. В вашем портале Bitrix24 создайте **Входящий вебхук** с правами на `crm` и `tasks`.
2. В файле `.env` пропишите базовый URL вебхука:
   `BITRIX_WEBHOOK_URL=https://your-domain.bitrix24.ru/rest/1/your_webhook_code/`
3. Убедитесь, что URL оканчивается слэшем и не содержит названия конкретного метода.

## 8. Доступные Endpoints
- `GET /` — главный интерфейс (UI).
- `GET /health` — статус бэкенда и режима (Live/Demo).
- `GET /model/status` — параметры и текущая модель Ollama, включая Fast Mode.
- `GET /model/benchmark` — мгновенный замер скорости работы модели.
- `GET /demo-deal` — отдает демо-сделку из `demo_deal.json`.
- `GET /analyze-demo` — быстрый анализ демо-сделки без тела запроса.
- `POST /analyze` — анализ произвольной сделки через LLM.
- `GET /runs` — список последних 20 запусков агента.
- `GET /runs/{id}` — детальный лог конкретного запуска.
- `POST /bitrix/test` — проверка соединения с Bitrix24 webhook.
- `POST /bitrix/read-deal` — чтение реальной сделки по ID.
- `POST /bitrix/analyze-deal` — сквозной пайплайн: чтение и анализ сделки из CRM.
- `POST /bitrix/write-comment` — запись комментария в таймлайн сделки.
- `POST /bitrix/analyze-and-comment` — анализ + автозапись комментария в CRM.
- `POST /bitrix/create-task` — создание задачи в Bitrix24.
- `POST /bitrix/execute-action` — исполнение действия агента (задача/комментарий) в CRM (только при `ALLOW_BITRIX_WRITE=true`).
- `POST /bitrix/outgoing-webhook` — handler для робота Битрикс24 «Исходящий Вебхук».

## 9. Автоматический запуск из робота Битрикс24

Поток обработки события:

```text
Сделка создана / переведена на стадию
→ робот "Исходящий Вебхук"
→ Handler URL
→ FastAPI endpoint /bitrix/outgoing-webhook
→ извлечение deal_id
→ чтение сделки через Bitrix REST
→ AI-анализ
→ dry-run или комментарий в таймлайн
→ SQLite audit log
```

В поле **«Хендлер»** робота Битрикс24 вставьте:

```text
https://<public-domain>/bitrix/outgoing-webhook?secret=<BITRIX_EVENT_SECRET>
```

**Локальный туннель (cloudflared):**

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Пример итогового handler URL (только placeholders):

```text
https://<generated>.trycloudflare.com/bitrix/outgoing-webhook?secret=dev-local-123
```

Пример `.env` для локальной отладки (без реальных секретов):

```env
APP_MODE=demo
ALLOW_BITRIX_WRITE=false
USE_OLLAMA=false
BITRIX_WEBHOOK_URL=https://your-domain.bitrix24.ru/rest/USER_ID/WEBHOOK_CODE/
BITRIX_EVENT_SECRET=dev-local-123
EVENT_IDEMPOTENCY_TTL_SECONDS=600
DEBUG_INCOMING_EVENTS=false
```

## 10. Safety Notes (Меры предосторожности)
- **Webhook URL**: Никогда не показывайте код вебхука на публичных демо или интервью. Вводите его заранее или держите в `.env`. UI использует `type="password"`.
- **Write Disabled**: По умолчанию запись в Bitrix24 всегда выключена, чтобы случайно не затронуть боевые данные.
- **Draft Reply**: Блок "Черновик ответа клиенту" является *только черновиком*. Он не отправляется клиенту автоматически, а создан исключительно для помощи менеджеру.
- **Не коммитьте `.env`**: файл с реальными секретами должен оставаться только локально.
- **Handler URL с secret**: не показывайте URL с `?secret=` на записи экрана, скриншотах или в публичных чатах.
- **После публичного теста**: перегенерируйте входящий webhook Bitrix24, если URL или код могли попасть в чужие руки.
- **cloudflared / ngrok**: быстрый tunnel URL временный и меняется при каждом перезапуске — не используйте его как постоянный handler.

## 11. Roadmap
- [x] **Block A**: Локальный LLM-агент (FastAPI + Ollama), базовая логика и детерминированный fallback.
- [x] **Block B**: Интеграция с Bitrix24 (чтение сделок, запись комментариев в таймлайн).
- [x] **Block C**: Архитектура Action Execution & Human Approval (создание действий из JSON-рекомендаций LLM).
- [x] **SQLite logging**: Локальное логирование всех запусков агента для аудита и прозрачности AI.
- [x] **Bitrix tasks**: Исполнение `create_task` экшенов (создание задач) в Битриксе.
- [x] **Block D**: Bitrix24 outgoing webhook handler.
- [ ] **Future**: Полноценная оркестрация через n8n или LangGraph, маппинг пользовательских (UF_) полей в реальном времени.
