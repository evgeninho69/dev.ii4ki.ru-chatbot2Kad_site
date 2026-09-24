# Dockerfile for 2KAD Chatbot Backend
# Multi-stage: build deps in builder, run slim runtime

FROM python:3.13-slim AS builder

WORKDIR /build

# Системные зависимости для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и ставим зависимости
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --user -r requirements.txt


# ─── Runtime ────────────────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Копируем установленные пакеты из builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Копируем код бэкенда
COPY backend/ ./backend/

# Копируем фронтенд (виджет)
COPY frontend/ ./frontend/

# Копируем mu-plugin для деплоя на 2kad.ru
COPY deploy/ ./deploy/

# Копируем README и .env.example для справки
COPY README.md ./
COPY .env.example ./.env.example

# Создаём непривилегированного пользователя
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

EXPOSE 8765

# Healthcheck для Dokploy / мониторинга
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8765/api/health', timeout=5); r.raise_for_status()" \
    || exit 1

# Запуск из /app/backend (где лежит app.py)
WORKDIR /app/backend
CMD ["python", "app.py"]