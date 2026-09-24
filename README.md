# 2KAD Chatbot

Виртуальный ассистент сайта [2kad.ru](https://2kad.ru) — кадастровые, проектные и изыскательские работы в Твери.

## Что это делает

Чат-бот отвечает на вопросы пользователей сайта:

- **Вопросы про цены** → подсказывает калькулятор, всегда говорит «точная цена после изучения документов + звонок менеджера», НЕ выдумывает цифры.
- **Любые другие вопросы** → короткий ответ на основе контента сайта + ссылка на нужную страницу.
- **Вопросы вне тематики** → вежливо перенаправляет на основной сайт.

## Архитектура

```
Браузер (JS widget)
  ↓ POST /api/chat (SSE streaming)
FastAPI backend (uvicorn, порт 8765)
  ├─ SiteIndex (BM25 по 19 страницам сайта)
  ├─ QueryRouter
  │   ├─ is_pricing → PRICING_PROMPT + калькулятор + контекст услуги
  │   ├─ info       → INFO_PROMPT + релевантная страница
  │   └─ no_answer  → NO_ANSWER_PROMPT + ссылка на главную
  └─ AnyModel API (gpt-oss-20b с fallback nemotron)
```

## Структура репозитория

```
chatbot-2kad/
├── backend/
│   ├── app.py              # FastAPI
│   ├── llm.py              # AnyModel client (stream + fallback + обработка 504)
│   ├── router.py           # Цена/инфо/no_answer роутинг
│   ├── search.py           # BM25-поиск по индексу
│   ├── prompts.py          # 3 system-промпта
│   ├── requirements.txt
│   └── data/
│       └── site_index.json   # Индекс 19 страниц сайта
├── frontend/
│   └── widget.js           # Плавающий чат-виджет для сайта
├── deploy/
│   └── 2kad-chatbot.php    # WordPress mu-plugin
├── Dockerfile              # Multi-stage python:3.13-slim
├── docker-compose.yml      # Для локального запуска
├── .env.example
├── .dockerignore
└── README.md
```

## Запуск локально

```bash
# 1. Заполни .env
cp .env.example .env
# Впиши ANYMODEL_API_KEY

# 2. Запусти
docker compose up --build

# 3. Проверь
curl http://localhost:8765/api/health
# {"status":"ok","time":1790269272,"index_loaded":true}
```

## Деплой в production

Backend развёрнут на **dev.ii4ki.ru** через Dokploy:
- Git: `github.com/evgeninho69/dev.ii4ki.ru-chatbot2Kad_site`
- Auto-build + deploy по push в `main`

### Что нужно Dokploy

1. Создать application в Dokploy (type=`application`)
2. Repository: `https://github.com/evgeninho69/dev.ii4ki.ru-chatbot2Kad_site`
3. Branch: `main`
4. Build: `Dockerfile` (auto-detected)
5. Env-переменные: `ANYMODEL_API_KEY`, `ANYMODEL_MODEL`, `PORT`
6. Domain: `dev.ii4ki.ru/chatbot` (через reverse-proxy или отдельный домен)

### Интеграция с сайтом 2kad.ru

`deploy/2kad-chatbot.php` — WordPress mu-plugin, загружает `widget.js` в footer.

В `wp-config.php`:
```php
define('KAD_CHATBOT_API_BASE', 'https://dev.ii4ki.ru/chatbot');
define('KAD_CHATBOT_WIDGET_URL', 'https://dev.ii4ki.ru/chatbot/widget/widget.js');
```

## Переиндексация сайта 2kad.ru

Если на сайте появились новые страницы или обновились цены:

```bash
# Зайди на dev.ii4ki.ru (или на свой сервер) и пересобери индекс:
cd /app/backend
python -c "from scripts.build_index import main; main()"
# или попроси AI-агента обновить индекс через WP REST API
```

## Переменные окружения

| Переменная | Описание | Где взять |
|---|---|---|
| `ANYMODEL_API_KEY` | API-ключ AnyModel | https://anymodel.org → Settings → API |
| `ANYMODEL_MODEL` | ID модели | По умолчанию `am/gpt-oss-20b` |
| `PORT` | Порт FastAPI | По умолчанию 8765 |
| `LOG_LEVEL` | Уровень логов | INFO / DEBUG / WARNING |

## Логи и наблюдение

- **Healthcheck**: `GET /api/health`
- **Статистика индекса**: `GET /api/index-stats`
- **Логи диалогов**: `GET /api/logs` (последние 60)

## Ограничения

- AnyModel free tier периодически возвращает 504 (upstream timeout). Бот это обрабатывает и пробует fallback-модель.
- Rate limit AnyModel free ≈ 5-10 req/min.
- История диалогов — в памяти (in-memory), теряется при рестарте. Для production нужна Redis.

## Roadmap

- [ ] Redis для истории диалогов
- [ ] Embeddings вместо BM25
- [ ] Интеграция с Bitrix24 (лид из чата → сделка)
- [ ] Telegram-зеркало чат-бота
- [ ] Голосовой ввод (Whisper)
- [ ] Self-host Qwen2.5-32B как альтернатива AnyModel

## Лицензия

Internal — ООО «Центр Недвижимости 2кад».