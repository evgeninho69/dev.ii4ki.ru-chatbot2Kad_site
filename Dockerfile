# Dockerfile for 2KAD Chatbot Backend
# Single-stage для простоты деплоя

FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

# Системные зависимости (gcc для некоторых wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Ставим Python-зависимости
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY deploy/ ./deploy/
COPY README.md ./
COPY .env.example ./.env.example

# Непривилегированный пользователь (без gcc, без root-доступа)
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8765/api/health', timeout=5).raise_for_status()" \
    || exit 1

WORKDIR /app/backend
CMD ["python", "app.py"]
